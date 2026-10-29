#!/usr/bin/env python3

import json
import logging
import math
import sys
from pathlib import Path
from typing import Any

from robonix_api import Err, Ok, Primitive

# codegen 生成的 MCP types（stand/sit 的 Empty 输入消息）
sys.path.insert(
    0,
    str(Path(__file__).resolve().parent.parent / "rbnx-build" / "codegen" / "robonix_mcp_types"),
)
from std_msgs_mcp import Empty  # noqa: E402

import chassis_pb2
import std_msgs_pb2

from .robot_control import RobotControl


logging.basicConfig(
    level=logging.INFO,
    format="[lynx_chassis] %(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("lynx_chassis")

app = Primitive(
    id="lynx_chassis",
    namespace="robonix/primitive/chassis",
)

cfg: dict[str, Any] = {
    "dry_run": False,
    "robot_ip": "10.21.33.103",
    "robot_port": 30004,
    "timeout": 3.0,
    "ctl_hz": 20.0,
    # ratio: 使用 Motion_Ctrl 比例控制。
    # direct: 使用 Real_Motion_Ctrl 真实速度控制。
    "control_mode": "ratio",

    # ratio 模式下的开环参数。
    "forward_ratio": 0.3,
    "turn_ratio": 0.4,
    "forward_sec_per_meter": 4.0,
    "turn_sec_per_degree": 1.0 / 15.0,

    # direct 模式下的开环速度。
    "move_v": 0.2,
    "turn_w": 0.5,

    # 直接收到 linear_x / linear_y / angular_z 且未指定时间时使用。
    "default_dur": 0.5,
    "yaw_sign": 1.0,
}

ctrl: RobotControl | None = None


def reply(
    data: dict[str, Any],
    response_type,
):
    return response_type(
        status=std_msgs_pb2.String(
            data=json.dumps(data, ensure_ascii=False)
        )
    )


def require_finite(name: str, value: float) -> None:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value!r}")


def validate_ratio(name: str, value: float) -> None:
    """Motion_Ctrl：比例值必须位于[-1, 1]。"""
    require_finite(name, value)
    if value < -1.0 or value > 1.0:
        raise ValueError(
            f"{name} must be between -1 and 1, got {value:.3f}"
        )


def make_action(cmd) -> dict[str, Any]:
    forward_m = float(getattr(cmd, "forward_m", 0.0))
    rotate_deg = float(getattr(cmd, "rotate_deg", 0.0))
    linear_x = float(getattr(cmd, "linear_x", 0.0))
    linear_y = float(getattr(cmd, "linear_y", 0.0))
    angular_z = float(getattr(cmd, "angular_z", 0.0))
    duration_sec = float(getattr(cmd, "duration_sec", 0.0))

    for name, value in (
        ("forward_m", forward_m),
        ("rotate_deg", rotate_deg),
        ("linear_x", linear_x),
        ("linear_y", linear_y),
        ("angular_z", angular_z),
        ("duration_sec", duration_sec),
    ):
        require_finite(name, value)

    if forward_m != 0.0 and rotate_deg != 0.0:
        raise ValueError("forward_m and rotate_deg cannot both be non-zero")

    control_mode = str(cfg["control_mode"])
    use_ratio = control_mode == "ratio"

    if forward_m != 0.0:
        mode = "forward"
        linear_y = 0.0
        angular_z = 0.0
        if use_ratio:
            mc_type = "Motion_Ctrl"
            linear_x = math.copysign(
                float(cfg["forward_ratio"]), forward_m
            )
            duration_sec = (
                abs(forward_m) * float(cfg["forward_sec_per_meter"])
            )
        else:
            mc_type = "Real_Motion_Ctrl"
            linear_x = math.copysign(float(cfg["move_v"]), forward_m)
            duration_sec = abs(forward_m) / float(cfg["move_v"])
    elif rotate_deg != 0.0:
        mode = "rotate"
        linear_x = 0.0
        linear_y = 0.0
        if use_ratio:
            mc_type = "Motion_Ctrl"
            angular_z = math.copysign(
                float(cfg["turn_ratio"]), rotate_deg
            )
            duration_sec = (
                abs(rotate_deg) * float(cfg["turn_sec_per_degree"])
            )
        else:
            mc_type = "Real_Motion_Ctrl"
            angular_z = math.copysign(float(cfg["turn_w"]), rotate_deg)
            duration_sec = (
                abs(math.radians(rotate_deg)) / float(cfg["turn_w"])
            )
    else:
        if linear_x == 0.0 and linear_y == 0.0 and angular_z == 0.0:
            return {
                "mode": "noop",
                "mc_type": None,
                "x": 0.0,
                "y": 0.0,
                "yaw": 0.0,
                "dur": 0.0,
            }

        if use_ratio:
            validate_ratio("linear_x_ratio", linear_x)
            validate_ratio("linear_y_ratio", linear_y)
            validate_ratio("angular_z_ratio", angular_z)
            mode = "velocity_ratio"
            mc_type = "Motion_Ctrl"
        else:
            mode = "velocity_direct"
            mc_type = "Real_Motion_Ctrl"

        if duration_sec <= 0.0:
            duration_sec = float(cfg["default_dur"])

    if duration_sec <= 0.0:
        raise ValueError("duration must be positive")

    angular_z *= float(cfg["yaw_sign"])
    return {
        "mode": mode,
        "mc_type": mc_type,
        "x": linear_x,
        "y": linear_y,
        "yaw": angular_z,
        "dur": duration_sec,
    }


def execute_move(
    req,
):
    try:
        action = make_action(req.command)

        log.info(
            "command mode=%s udp_type=%s x=%.3f y=%.3f "
            "yaw=%.3f duration=%.3fs",
            action["mode"],
            action["mc_type"],
            action["x"],
            action["y"],
            action["yaw"],
            action["dur"],
        )

        if bool(cfg["dry_run"]):
            return reply(
                {
                    "status": "done",
                    "dry_run": True,
                    "udp_sent": False,
                    "action": action,
                },
                chassis_pb2.ExecuteMoveCommand_Response
            )

        if ctrl is None:
            raise RuntimeError("RobotControl is not initialized")

        if action["mode"] != "noop":
            ctrl.move_for(
                x=action["x"],
                y=action["y"],
                yaw=action["yaw"],
                duration=action["dur"],
                mc_type=action["mc_type"],
            )

        return reply(
            {
                "status": "done",
                "dry_run": False,
                "udp_sent": action["mode"] != "noop",
                "action": action,
            },
            chassis_pb2.ExecuteMoveCommand_Response
        )

    except Exception as exc:
        log.exception("move failed")
        return reply(
            {
                "status": "error",
                "error": str(exc),
            },
            chassis_pb2.ExecuteMoveCommand_Response
        )


@app.grpc(
    "robonix/primitive/chassis/move",
    description="Execute Lynx motion using configured control mode",
)
def move(req):
    """执行距离/角度命令，或直接传入速度值。"""
    return execute_move(req)

def validate_config() -> None:
    cfg["control_mode"] = str(cfg["control_mode"]).strip().lower()
    if cfg["control_mode"] not in ("ratio", "direct"):
        raise ValueError("control_mode must be 'ratio' or 'direct'")

    positive_float_fields = (
        "timeout", "ctl_hz", "move_v", "turn_w",
        "forward_ratio", "turn_ratio",
        "forward_sec_per_meter", "turn_sec_per_degree",
        "default_dur",
    )
    for name in positive_float_fields:
        cfg[name] = float(cfg[name])
        if not math.isfinite(cfg[name]) or cfg[name] <= 0.0:
            raise ValueError(f"{name} must be a finite positive number")

    cfg["robot_port"] = int(cfg["robot_port"])
    cfg["yaw_sign"] = float(cfg["yaw_sign"])

    if not 1 <= cfg["robot_port"] <= 65535:
        raise ValueError("robot_port must be between 1 and 65535")
    if cfg["yaw_sign"] not in (-1.0, 1.0):
        raise ValueError("yaw_sign must be 1 or -1")

    validate_ratio("forward_ratio", cfg["forward_ratio"])
    validate_ratio("turn_ratio", cfg["turn_ratio"])


@app.on_init
def init(raw_cfg):
    global ctrl
    if raw_cfg:
        cfg.update(dict(raw_cfg))

    try:
        validate_config()
        if bool(cfg["dry_run"]):
            log.info("initialized in dry-run mode; no UDP packet will be sent")
            return Ok()

        ctrl = RobotControl(
            tar_ip=str(cfg["robot_ip"]),
            tar_port=int(cfg["robot_port"]),
            timeout=float(cfg["timeout"]),
            ctl_hz=float(cfg["ctl_hz"]),
        )
        ctrl.msc(mp=1)
        log.info(
            "Lynx initialized at %s:%d, control_mode=%s",
            cfg["robot_ip"],
            cfg["robot_port"],
            cfg["control_mode"],
        )
        return Ok()
    except Exception as exc:
        log.exception("initialization failed")
        if ctrl is not None:
            try:
                ctrl.close()
            except Exception:
                log.exception("failed to close RobotControl")
            ctrl = None
        return Err(str(exc))


@app.on_shutdown
def shutdown():
    global ctrl
    if ctrl is not None:
        try:
            ctrl.msc(mp=4)
        except Exception:
            log.exception("failed to make Lynx sit")
        try:
            ctrl.close()
        except Exception:
            log.exception("failed to close RobotControl")
        ctrl = None

    log.info("shutdown complete")
    return Ok()


# ── 姿态能力（站立/趴下）───────────────────────────────────────────────────
# Motion_Sta_Chge 状态机切换：mp=1 → 站立状态；mp=4 → 趴下状态。
# 供 pilot / chat 直接对话控制（原来只有 driver/move，无法从能力面操作姿态）。
# 输入类型 std_msgs_mcp.Empty（文件顶部已 import）。


def _pose(mp: int) -> None:
    global ctrl
    if ctrl is None or cfg.get("dry_run"):  # 未初始化或 dry-run 时不可动作
        raise RuntimeError("lynx not initialized (or dry_run)")
    ctrl.msc(mp=mp)


@app.mcp("robonix/primitive/chassis/stand")
def stand(msg: Empty) -> None:
    """Make the Lynx S10 dog stand up (Motion_Sta_Chge 站立状态 mp=1)."""
    return _pose(1)


@app.mcp("robonix/primitive/chassis/sit")
def sit(msg: Empty) -> None:
    """Make the Lynx S10 dog lie down (Motion_Sta_Chge 趴下状态 mp=4)."""
    return _pose(4)


if __name__ == "__main__":
    app.run()
