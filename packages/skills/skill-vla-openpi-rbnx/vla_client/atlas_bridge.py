# SPDX-License-Identifier: MulanPSL-2.0
"""skill-vla-openpi-rbnx atlas bridge — openpi VLA inference + JOINT-SPACE execution.

Forked from lhw2002426/vla_client_rbnx. The robonix contract surface is
UNCHANGED (robonix/skill/vla/{driver,execute}); only the inference seam and the
action/state unit convention are swapped so this talks to an **openpi policy
server** (scripts/serve_policy.py, pi05_piper) over the openpi websocket client,
instead of the old HTTP openvla server with client-side [-1,1] de-normalization.

Pipeline:
    openpi policy server ─(50,7) absolute action chunk─► this skill ─►
        sensor_msgs/JointState on /arm/joint_command ─► piper_ctl ─► CAN ─► arm

Action / state convention (matches /home/czn/roboarm/classification/main.py,
the verified openpi client for pi05_piper):
    observation/state        = [joint_1..joint_6 (deg), gripper_0to1 * 100]  (7,)
    observation/image        = "above" Orbbec frame, RGB HxWx3 (resized 640x480)
    observation/wrist_image  = "wrist" RealSense frame, RGB 640x480
    policy.infer(obs)["actions"] = (50, 7), same 7-dim absolute space
    action                   = [joint_1..6 (deg), gripper_0to1 * 100]

Unit bridge to piper_ctl.joint_callback (piper_ctrl_single_node.py):
    joints : deg → rad  (piper_ctl factor = 57324.840764 = 1000*180/π expects rad)
    gripper: position[6] in METERS; piper_ctl does round(m*1e6)*gripper_val_mutiple.
             roboarm's low-level CAN uses gripper_0to1 * 100 * 1000 = 0to1*1e5 SDK.
             With gripper_val_mutiple=2, meters = gripper_0to1 / 20 reproduces it.
             MUST be re-verified on real hardware before any arm-moving run.

Lifecycle (Skill — lazy activate):
    on_init      — parse config
    on_activate  — connect openpi websocket policy, start ROS subscriber thread
    on_deactivate — stop subscriber thread, drop policy client
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from typing import Optional

import numpy as np

from robonix_api import ATLAS, Skill, Ok, Err  # noqa: E402

logging.basicConfig(
    level=os.environ.get("VLA_CLIENT_LOG_LEVEL", "INFO"),
    format="[vla_openpi] %(message)s",
)
log = logging.getLogger("vla_openpi")

vla_skill = Skill(
    id=os.environ.get("ROBONIX_CAPABILITY_ID", "openvla_client"),
    namespace="robonix/skill/vla",
)

# ── unit-conversion constants (see module docstring) ────────────────────────
DEG_PER_RAD = 180.0 / math.pi
# roboarm gripper: gripper_0to1 * 100 (state/action units). piper_ctl computes
# CAN as round(position[6]_m * 1e6) * gripper_val_mutiple(=2). To reproduce
# roboarm's gripper_0to1 * MAX_GRIPPER_ANGLE_DEG(100) * FACTOR(1000) CAN value:
#   meters = (action[6] / 100) / 20      (with gripper_val_mutiple == 2)
GRIPPER_UNITS_TO_0TO1 = 100.0    # action[6] = gripper_0to1 * 100
# Two DIFFERENT domains, off by gripper_val_mutiple(=2) — measured on-arm:
#   COMMAND: piper_ctl does CAN = position[6]_m * 1e6 * 2, so publishing
#            P meters opens the jaw to 2*P. Full open (0to1=1.0) => P = 0.0498.
#   READBACK: piper_ctl publishes position[6] = CAN/1e6 = physical opening.
#            Full open reads ~0.0995 m (calib: cmd 0.05 -> 0.0995, linear;
#            0.8*0.0995=0.0796 == home_arm HOME_GRIP, == roboarm home 0.8).
# Using one constant for both saturated state[6] at half range and blinded the
# policy's closed loop. Keep them separate.
GRIPPER_0TO1_TO_METERS = 0.0498       # SEND: 0to1 -> published meters (P domain)
GRIPPER_STATE_FULL_OPEN_M = 0.0995    # READ: full-open readback (state domain)

# ── shared state ────────────────────────────────────────────────────────────
_state_lock = threading.Lock()
_cfg: dict = {}
_active = False
_endpoints: Optional[dict[str, str]] = None

# openpi websocket policy client (created in on_activate)
_policy = None

# ROS subscriber state (written by subscriber thread, read by handler)
_latest_full_image: Optional[np.ndarray] = None
_latest_wrist_image: Optional[np.ndarray] = None
_latest_joint_state_deg: Optional[np.ndarray] = None  # [j1..6 deg, gripper_0to1*100]
_obs_lock = threading.Lock()

# ROS publisher/node handles (set by subscriber thread)
_g_joint_cmd_pub = None
_g_node = None

# Safety filter state — last commanded action in ACTION units [deg*6, grip*100]
_last_action: Optional[np.ndarray] = None

# ROS thread handle
_ros_thread: Optional[threading.Thread] = None
_ros_stop_event = threading.Event()


# ── atlas-resolved upstream contracts ───────────────────────────────────────
# The openpi policy server is reached directly over the websocket (host:port
# from config), NOT through atlas. Only an optional emergency reset is looked up.
REQUIRED_INPUTS: dict = {}
OPTIONAL_INPUTS = {
    "reset": ("robonix/service/manipulation/reset", "mcp"),
}


def _resolve_inputs(deadline_s: float = 60.0) -> dict[str, str]:
    """Best-effort resolve OPTIONAL_INPUTS on atlas. The policy server itself
    is accessed directly via websocket from config, not through atlas."""
    resolved: dict[str, str] = {}
    for key, (cid, transport) in OPTIONAL_INPUTS.items():
        try:
            cap_view = ATLAS.find_unique_capability(
                contract_id=cid, transport=transport)
            ch = vla_skill.connect_capability(cap_view, cid, transport)
            ep = ch.endpoint
            try:
                ch.close()
            except Exception:
                pass
            if ep:
                resolved[key] = ep
                log.info("resolved %s [%s] → %s (optional)", cid, transport, ep)
        except Exception:
            log.warning("optional dep %s not on atlas — degraded (no emergency reset)", cid)
    return resolved


# ── ROS subscriber thread ───────────────────────────────────────────────────

def _start_ros_subscriber_thread():
    global _ros_thread
    _ros_stop_event.clear()
    _ros_thread = threading.Thread(target=_ros_spin_loop, daemon=True,
                                   name="vla-openpi-ros")
    _ros_thread.start()
    log.info("ROS subscriber thread started")


def _decode_ros_image(msg) -> np.ndarray:
    """Decode sensor_msgs/Image → numpy RGB (no cv_bridge). Copies out of DDS buf."""
    h, w = msg.height, msg.width
    enc = msg.encoding.lower()
    raw = np.frombuffer(bytes(msg.data), dtype=np.uint8)
    if enc == "rgb8":
        return raw.reshape((h, w, 3)).copy()
    if enc == "bgr8":
        return raw.reshape((h, w, 3))[:, :, ::-1].copy()
    if enc == "rgba8":
        return raw.reshape((h, w, 4))[:, :, :3].copy()
    if enc == "bgra8":
        return raw.reshape((h, w, 4))[:, :, 2::-1].copy()
    if enc == "mono8":
        grey = raw.reshape((h, w))
        return np.stack([grey, grey, grey], axis=-1)
    return raw.reshape((h, w, 3)).copy()


def _resize_640x480(img: np.ndarray) -> np.ndarray:
    """Resize to 640x480 RGB — matches roboarm openpi client preprocessing."""
    from PIL import Image as PILImage
    if img.shape[1] == 640 and img.shape[0] == 480:
        return img.astype(np.uint8)
    pil = PILImage.fromarray(img).resize((640, 480), PILImage.BILINEAR)
    return np.array(pil, dtype=np.uint8)


def _ros_spin_loop():
    """ROS2 subscriber loop (dedicated thread).

    Subscribes: full_image, wrist_image (camera RGB), joint_states (proprio).
    Publishes:  sensor_msgs/JointState on joint_cmd_topic (piper_ctl command).
    """
    global _g_joint_cmd_pub, _g_node
    import rclpy
    from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
    from sensor_msgs.msg import Image, JointState

    rclpy.init()
    node = rclpy.create_node("vla_openpi_subscriber")

    qos_be = QoSProfile(reliability=ReliabilityPolicy.BEST_EFFORT,
                        history=HistoryPolicy.KEEP_LAST, depth=1)

    full_topic = _cfg.get("full_image_topic", "/camera/color/image_raw")
    wrist_topic = _cfg.get("wrist_image_topic", "/camera_435i/color/image_raw")
    js_topic = _cfg.get("joint_states_topic", "/arm/joint_states_single")
    joint_cmd_topic = _cfg.get("joint_cmd_topic", "/arm/joint_command")

    def _on_full(msg: Image):
        global _latest_full_image
        try:
            with _obs_lock:
                _latest_full_image = _resize_640x480(_decode_ros_image(msg))
        except Exception as e:
            log.warning("full_image cb error: %s", e)

    def _on_wrist(msg: Image):
        global _latest_wrist_image
        try:
            with _obs_lock:
                _latest_wrist_image = _resize_640x480(_decode_ros_image(msg))
        except Exception as e:
            log.warning("wrist_image cb error: %s", e)

    def _on_joints(msg: JointState):
        # piper_ctl publishes joint_states_single: position [j1..6 rad, gripper m].
        global _latest_joint_state_deg
        try:
            pos = list(msg.position)
            if len(pos) < 6:
                return
            deg = [p * DEG_PER_RAD for p in pos[:6]]
            grip_m = float(pos[6]) if len(pos) >= 7 else 0.0
            grip_0to1 = float(np.clip(grip_m / GRIPPER_STATE_FULL_OPEN_M, 0.0, 1.0))
            grip_action = grip_0to1 * GRIPPER_UNITS_TO_0TO1  # → gripper_0to1 * 100
            with _obs_lock:
                _latest_joint_state_deg = np.array(deg + [grip_action], dtype=np.float32)
        except Exception as e:
            log.warning("joint_states cb error: %s", e)

    node.create_subscription(Image, full_topic, _on_full, qos_be)
    node.create_subscription(Image, wrist_topic, _on_wrist, qos_be)
    node.create_subscription(JointState, js_topic, _on_joints, qos_be)
    _g_joint_cmd_pub = node.create_publisher(JointState, joint_cmd_topic, 10)
    _g_node = node

    log.info("subscribed: full=%s wrist=%s joints=%s | publish: %s",
             full_topic, wrist_topic, js_topic, joint_cmd_topic)

    while not _ros_stop_event.is_set():
        try:
            rclpy.spin_once(node, timeout_sec=0.05)
        except Exception as e:
            log.warning("rclpy.spin_once raised: %s", e)

    try:
        node.destroy_node()
    except Exception:
        pass
    try:
        rclpy.shutdown()
    except Exception:
        pass
    log.info("ROS subscriber thread stopped")


# ── observation + inference + action ────────────────────────────────────────

def _get_current_observation():
    """Return (full_image, wrist_image, state_deg) from latest ROS callbacks.

    state_deg = [j1..6 deg, gripper_0to1*100]. Read from /arm/joint_states_single
    (piper_ctl proprio); no direct SDK read (piper_ctl owns the CAN bus)."""
    with _obs_lock:
        full = _latest_full_image.copy() if _latest_full_image is not None else None
        wrist = _latest_wrist_image.copy() if _latest_wrist_image is not None else None
        state = _latest_joint_state_deg.copy() if _latest_joint_state_deg is not None else None
    return full, wrist, state


def _infer_openpi(instruction, full_image, wrist_image, state_deg):
    """Query the openpi policy server. Returns (N,7) action chunk or None.

    obs keys + dtypes match /home/czn/roboarm/classification/main.py exactly."""
    if _policy is None:
        log.error("openpi policy client not connected")
        return None
    obs = {
        "observation/image": np.asarray(full_image, dtype=np.uint8),
        "observation/wrist_image": np.asarray(wrist_image, dtype=np.uint8),
        "observation/state": np.asarray(state_deg, dtype=np.float32),
        "prompt": instruction,
    }
    try:
        result = _policy.infer(obs)
        actions = np.asarray(result["actions"], dtype=np.float32)
        if actions.ndim == 1:
            actions = actions[None, :]
        return actions
    except Exception as e:
        log.error("openpi infer failed: %s", e)
        return None


def _apply_safety(action: np.ndarray) -> np.ndarray:
    """Clip to action limits + bound per-step delta. Units: [deg*6, grip*100]."""
    global _last_action
    target = np.asarray(action, dtype=np.float32).flatten().copy()
    if not _cfg.get("enable_safety_filter", True):
        return target
    lo = np.array(_cfg["action_min"], dtype=np.float32)
    hi = np.array(_cfg["action_max"], dtype=np.float32)
    target = np.clip(target, lo, hi)
    if _last_action is not None:
        max_delta = np.array(_cfg["max_delta_per_step"], dtype=np.float32)
        delta = np.clip(target - _last_action, -max_delta, max_delta)
        target = _last_action + delta
    return target


def _send_action_to_arm(action: np.ndarray) -> None:
    """Publish one openpi action [j1..6 deg, grip*100] to piper_ctl joint_command."""
    global _last_action
    target = _apply_safety(action)
    positions = np.zeros(7, dtype=np.float64)
    positions[:6] = target[:6] / DEG_PER_RAD                       # deg → rad
    grip_0to1 = float(np.clip(target[6] / GRIPPER_UNITS_TO_0TO1, 0.0, 1.0))
    positions[6] = grip_0to1 * GRIPPER_0TO1_TO_METERS             # → meters
    _publish_joint_state(positions)
    _last_action = target


def _publish_joint_state(positions_ros: np.ndarray) -> None:
    """Publish JointState to joint_cmd_topic — layout matches piper_ctl.joint_callback."""
    if _g_joint_cmd_pub is None:
        log.warning("joint_command publisher not ready")
        return
    try:
        from sensor_msgs.msg import JointState
        msg = JointState()
        if _g_node is not None:
            msg.header.stamp = _g_node.get_clock().now().to_msg()
        msg.name = ['joint1', 'joint2', 'joint3', 'joint4', 'joint5', 'joint6', 'gripper']
        msg.position = [float(x) for x in positions_ros]
        vel_pct = float(_cfg.get("joint_velocity_pct", 30.0))
        msg.velocity = [0.0] * 6 + [vel_pct]
        grip_eff = float(_cfg.get("gripper_effort", 1.0))
        msg.effort = [0.0] * 6 + [grip_eff]
        _g_joint_cmd_pub.publish(msg)
    except Exception as e:
        log.error("publish joint_command failed: %s", e)


def _safe_reset(context: str) -> None:
    """Best-effort emergency stop hook. reset MCP is optional; log if absent."""
    if _endpoints is None or "reset" not in _endpoints:
        log.warning("reset not available — skipping (%s)", context)
        return
    log.info("reset endpoint present (%s) — manual reset left to operator", context)


def _build_prompt(instruction: str) -> str:
    """Map a pick instruction to the training prompt style.

    pick skill sends 'Pick up the {object_name}'. pi05_piper was trained on
    prompts like 'pick the potato toy and place into box'. If a prompt_template
    is configured, substitute the extracted object; otherwise pass through."""
    tmpl = _cfg.get("prompt_template", "").strip()
    if not tmpl:
        return instruction
    # Idempotent: if the caller already passed a fully-formed prompt (ends with
    # the template's literal suffix after {object}), don't re-wrap it.
    suffix = tmpl.split("{object}", 1)[-1].strip()
    if suffix and instruction.rstrip().endswith(suffix):
        return instruction
    obj = instruction
    low = instruction.lower()
    for prefix in ("pick up the ", "pick up ", "pick the ", "pick "):
        if low.startswith(prefix):
            obj = instruction[len(prefix):].strip()
            break
    try:
        return tmpl.format(object=obj, instruction=instruction)
    except Exception:
        return tmpl


# ── MCP handler ─────────────────────────────────────────────────────────────

from vla_client_mcp import VlaExecute_Request, VlaExecute_Response  # noqa: E402


@vla_skill.mcp("robonix/skill/vla/execute")
def execute(req: VlaExecute_Request) -> VlaExecute_Response:
    """Run an end-to-end VLA (vision-language-action) policy for an instruction.

    Synchronous closed loop: observe → infer (openpi) → act → repeat, until
    timeout or max_steps. Actions stream to piper_ctl at ~control_hz.
    """
    global _last_action
    if not _active or _policy is None:
        return VlaExecute_Response(
            success=False,
            message="vla skill not active (openpi policy not connected yet)",
            steps_executed=0, elapsed_s=0.0,
        )

    instruction = (req.instruction or "").strip()
    if not instruction:
        return VlaExecute_Response(
            success=False, message="instruction is empty",
            steps_executed=0, elapsed_s=0.0)

    prompt = _build_prompt(instruction)
    timeout_s = float(req.timeout_s) if req.timeout_s > 0 else float(_cfg.get("timeout_s", 60.0))
    max_steps = int(req.max_steps) if req.max_steps > 0 else 0
    control_dt = float(_cfg.get("control_dt", 0.033))
    chunk_execute = int(_cfg.get("actions_per_chunk", 50))

    t0 = time.monotonic()
    deadline = t0 + timeout_s
    steps_executed = 0
    _last_action = None

    log.info("execute(instruction=%r prompt=%r) timeout=%.1fs max_steps=%d dt=%.3f chunk=%d",
             instruction, prompt, timeout_s, max_steps, control_dt, chunk_execute)

    try:
        while time.monotonic() < deadline:
            if max_steps > 0 and steps_executed >= max_steps:
                break
            full_img, wrist_img, state = _get_current_observation()
            if full_img is None or state is None:
                time.sleep(0.1)
                continue
            if wrist_img is None:
                wrist_img = full_img.copy()
                log.debug("wrist_image unavailable — using full_image as fallback")

            actions = _infer_openpi(prompt, full_img, wrist_img, state)
            if actions is None:
                return VlaExecute_Response(
                    success=False, message="openpi inference failed",
                    steps_executed=steps_executed, elapsed_s=time.monotonic() - t0)

            log.info("openpi returned %s actions (executing %d), step_so_far=%d",
                     actions.shape, min(chunk_execute, actions.shape[0]), steps_executed)

            for action in actions[:chunk_execute]:
                if time.monotonic() >= deadline:
                    break
                if max_steps > 0 and steps_executed >= max_steps:
                    break
                _send_action_to_arm(action)
                steps_executed += 1
                time.sleep(control_dt)

        elapsed = time.monotonic() - t0
        log.info("execute done: %d steps in %.2fs", steps_executed, elapsed)
        return VlaExecute_Response(
            success=True, message=f"Executed {steps_executed} steps",
            steps_executed=steps_executed, elapsed_s=elapsed)
    except Exception as e:
        log.error("execute error: %s", e)
        _safe_reset("after execute error")
        return VlaExecute_Response(
            success=False, message=f"execution error: {e}",
            steps_executed=steps_executed, elapsed_s=time.monotonic() - t0)


# ── lifecycle ───────────────────────────────────────────────────────────────

@vla_skill.on_init
def init(cfg):
    """CMD_INIT: light. Parse + validate config (safety limits are length-7)."""
    global _cfg
    cfg = cfg or {}
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg) if cfg else {}
        except json.JSONDecodeError as e:
            return Err(f"bad config_json: {e}")
    _cfg = cfg

    if bool(_cfg.get("enable_safety_filter", True)):
        for key in ("action_min", "action_max", "max_delta_per_step"):
            v = _cfg.get(key)
            if v is None or len(v) != 7:
                return Err(f"config.{key} must be a length-7 list (got {v!r})")

    log.info("CMD_INIT ok (vla_host=%s vla_port=%s control_dt=%.3f safety=%s)",
             _cfg.get("vla_host", "127.0.0.1"), _cfg.get("vla_port", 8002),
             float(_cfg.get("control_dt", 0.033)),
             "ON" if _cfg.get("enable_safety_filter", True) else "OFF")
    return Ok()


@vla_skill.on_activate
def activate():
    """CMD_ACTIVATE: connect openpi websocket policy, start ROS subscriber."""
    global _endpoints, _policy, _active
    with _state_lock:
        if _active:
            log.info("CMD_ACTIVATE — already active, no-op")
            return Ok()
        try:
            _endpoints = _resolve_inputs()
        except RuntimeError as e:
            return Err(str(e))

        host = str(_cfg.get("vla_host", "127.0.0.1"))
        port = int(_cfg.get("vla_port", 8002))
        api_key = _cfg.get("vla_api_key") or None
        try:
            from openpi_client import websocket_client_policy as _wcp
            _policy = _wcp.WebsocketClientPolicy(host=host, port=port, api_key=api_key)
            meta = _policy.get_server_metadata()
            log.info("connected to openpi policy server %s:%d — metadata: %s",
                     host, port, meta)
        except Exception as e:
            _policy = None
            return Err(f"failed to connect openpi policy server {host}:{port}: {e}")

        _start_ros_subscriber_thread()
        _active = True
    log.info("CMD_ACTIVATE ok — endpoints: %s", list((_endpoints or {}).keys()))
    return Ok()


@vla_skill.on_deactivate
def deactivate():
    """CMD_DEACTIVATE: stop ROS thread, drop policy client + state."""
    global _endpoints, _policy, _last_action, _active
    with _state_lock:
        _endpoints = None
        _policy = None
        _last_action = None
        _active = False
    _ros_stop_event.set()
    if _ros_thread is not None:
        _ros_thread.join(timeout=5.0)
    log.info("CMD_DEACTIVATE ok")
    return Ok()


# ── entrypoint ──────────────────────────────────────────────────────────────

def main() -> int:
    vla_skill.run()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
