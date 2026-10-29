from __future__ import annotations

import logging
import threading
import time
from typing import Any

import requests

from robonix_api import ATLAS, Deferred, Err, Ok, Skill
from robonix_api.atlas_types import GrpcParams, Transport

from internvla_navigation_mcp import Navigate_Request, Navigate_Response

from .action_adapter import result_to_commands
from .camera_client import CameraClient
from .chassis_client import ChassisClient
from .internvla_client import InternVLAClient


log = logging.getLogger("internvla_navigation")
logging.basicConfig(
    level=logging.INFO,
    format="[internvla_navigation] %(levelname)s %(message)s",
)

skill = Skill(
    id="internvla_navigation",
    namespace="robonix/skill/internvla_navigation",
)

_cfg: dict[str, Any] = {
    "server_url": "http://127.0.0.1:5801",
    "client_id": "lynx-01",
    "request_timeout_sec": 120.0,
    "camera_timeout_sec": 10.0,
    "chassis_timeout_sec": 10.0,
    "max_steps": 100,
    "timeout_sec": 300.0,
    "lookahead_m": 0.30,
    "discrete_forward_m": 0.25,
    "turn_threshold_deg": 15.0,
    "discrete_step_deg": 15.0,
    "wait_interval_sec": 0.30,
}

_vla: InternVLAClient | None = None
_camera: CameraClient | None = None
_chassis: ChassisClient | None = None
_stop_event = threading.Event()
_run_lock = threading.Lock()


def _response(status: str, reason: str, steps: int) -> Navigate_Response:
    return Navigate_Response(status=status, reason=reason, steps=steps)


@skill.mcp("robonix/skill/internvla_navigation/navigate")
def navigate(req: Navigate_Request) -> Navigate_Response:
    """Navigate the robot to a visually described destination with InternVLA.

    Use this for language-guided local navigation. It repeatedly captures an
    RGB snapshot and asks InternVLA for the next result. A discrete result is
    executed as an ordered sequence of generic chassis moves; a trajectory
    result produces one local fallback move before the next snapshot.
    """
    instruction = req.instruction.strip()
    if not instruction:
        return _response("failed", "instruction is empty", 0)
    if _vla is None or _camera is None or _chassis is None:
        return _response("failed", "skill is not active", 0)
    if not _run_lock.acquire(blocking=False):
        return _response("busy", "another navigation task is running", 0)

    max_steps = int(req.max_steps) or int(_cfg["max_steps"])
    timeout_sec = float(req.timeout_sec) or float(_cfg["timeout_sec"])
    task_id = ""
    steps = 0
    _stop_event.clear()

    try:
        task_id = _vla.start_task(instruction)
        deadline = time.monotonic() + timeout_sec

        for frame_id in range(max_steps):
            if _stop_event.is_set():
                _vla.report(task_id, "cancelled", "skill deactivated")
                return _response("cancelled", "skill deactivated", steps)
            if time.monotonic() >= deadline:
                _vla.report(task_id, "failed", "navigation timeout")
                return _response("timeout", "navigation timeout", steps)

            image_jpeg = _camera.snapshot()
            result = _vla.infer(task_id, frame_id, image_jpeg)
            action, commands = result_to_commands(
                result,
                lookahead_m=float(_cfg["lookahead_m"]),
                discrete_forward_m=float(_cfg["discrete_forward_m"]),
                turn_threshold_deg=float(_cfg["turn_threshold_deg"]),
                discrete_step_deg=float(_cfg["discrete_step_deg"]),
            )

            if action == "stop":
                _chassis.stop()
                _vla.report(task_id, "completed", "model stop")
                return _response("completed", "model stop", steps)
            if action == "wait":
                time.sleep(float(_cfg["wait_interval_sec"]))
                continue

            for command in commands:
                if _stop_event.is_set():
                    _vla.report(task_id, "cancelled", "skill deactivated")
                    return _response("cancelled", "skill deactivated", steps)
                if time.monotonic() >= deadline:
                    _vla.report(task_id, "failed", "navigation timeout")
                    return _response("timeout", "navigation timeout", steps)

                log.info("executing model command %s", command)
                _chassis.move(**command)
                steps += 1

        _vla.report(task_id, "failed", "max steps reached")
        return _response("max_steps", "max steps reached", steps)

    except Exception as exc:
        log.exception("navigation failed")
        if task_id and _vla is not None:
            try:
                _vla.report(task_id, "failed", str(exc))
            except Exception:
                log.exception("failed to report task failure")
        return _response("failed", str(exc), steps)
    finally:
        if _chassis is not None:
            try:
                _chassis.stop()
            except Exception:
                log.exception("failed to stop chassis")
        _run_lock.release()


@skill.on_init
def init(cfg: dict[str, Any]):
    _cfg.update(cfg or {})
    positive = (
        "request_timeout_sec",
        "camera_timeout_sec",
        "chassis_timeout_sec",
        "max_steps",
        "timeout_sec",
        "lookahead_m",
        "discrete_forward_m",
        "discrete_step_deg",
    )
    non_negative = ("turn_threshold_deg", "wait_interval_sec")
    try:
        for name in positive:
            if float(_cfg[name]) <= 0:
                raise ValueError(f"{name} must be positive")
        for name in non_negative:
            if float(_cfg[name]) < 0:
                raise ValueError(f"{name} must be non-negative")
    except (TypeError, ValueError) as exc:
        return Err(str(exc))
    return Ok()


@skill.on_activate
def activate():
    global _vla, _camera, _chassis

    chassis_caps = ATLAS.find_capability(
        contract_id="robonix/primitive/chassis/move",
        transport=Transport.GRPC,
    )
    if not chassis_caps:
        return Deferred("waiting for robonix/primitive/chassis/move")

    camera_caps = ATLAS.find_capability(
        contract_id="robonix/primitive/camera/snapshot",
        transport=Transport.MCP,
    )
    if not camera_caps:
        return Deferred("waiting for robonix/primitive/camera/snapshot")

    try:
        chassis_channel = skill.connect_capability(
            chassis_caps[0],
            "robonix/primitive/chassis/move",
            Transport.GRPC,
        )
        camera_channel = skill.connect_capability(
            camera_caps[0],
            "robonix/primitive/camera/snapshot",
            Transport.MCP,
        )

        grpc_params = chassis_channel.params
        if not isinstance(grpc_params, GrpcParams):
            raise RuntimeError(
                "chassis/move returned no gRPC binding metadata from Atlas"
            )
        if not grpc_params.service_name or not grpc_params.method:
            raise RuntimeError(
                "chassis/move returned incomplete gRPC binding metadata from Atlas"
            )

        _chassis = ChassisClient(
            chassis_channel.endpoint,
            float(_cfg["chassis_timeout_sec"]),
            service_name=grpc_params.service_name,
            method=grpc_params.method,
        )
        _camera = CameraClient(
            camera_channel.endpoint,
            float(_cfg["camera_timeout_sec"]),
        )
        _vla = InternVLAClient(
            str(_cfg["server_url"]),
            str(_cfg["client_id"]),
            float(_cfg["request_timeout_sec"]),
        )

        health = _vla.health()
        if not health.get("server_ready", False):
            _close_clients()
            reason = health.get("model_error") or health.get("model_state", "not ready")
            return Deferred(f"InternVLA server is not ready: {reason}")
        log.info("activated with camera=%s chassis=%s", camera_caps[0].provider_id, chassis_caps[0].provider_id)
        return Ok()
    except requests.RequestException as exc:
        _close_clients()
        return Deferred(f"InternVLA server unavailable: {exc}")
    except Exception as exc:
        _close_clients()
        return Err(str(exc))


def _close_clients() -> None:
    global _vla, _camera, _chassis
    if _chassis is not None:
        try:
            _chassis.stop()
        finally:
            _chassis.close()
            _chassis = None
    if _camera is not None:
        _camera.close()
        _camera = None
    if _vla is not None:
        _vla.close()
        _vla = None


@skill.on_deactivate
def deactivate():
    _stop_event.set()
    if _run_lock.acquire(timeout=5.0):
        try:
            _close_clients()
        finally:
            _run_lock.release()
    return Ok()


@skill.on_shutdown
def shutdown():
    _stop_event.set()
    if _run_lock.acquire(timeout=5.0):
        try:
            _close_clients()
        finally:
            _run_lock.release()


if __name__ == "__main__":
    skill.run()