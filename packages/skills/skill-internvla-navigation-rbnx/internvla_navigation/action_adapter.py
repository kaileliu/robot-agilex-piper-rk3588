from __future__ import annotations

import math
from typing import Any


Command = dict[str, float]


def _to_list(value: Any) -> list[Any]:
    """Convert tensor/ndarray/list-like model output to a Python list."""
    if value is None:
        return []
    if hasattr(value, "detach"):
        value = value.detach().cpu()
    if hasattr(value, "tolist"):
        value = value.tolist()

    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _normalize_actions(actions: Any) -> list[int]:
    values = _to_list(actions)
    while (
        len(values) == 1
        and isinstance(values[0], (list, tuple))
    ):
        values = list(values[0])
    return [int(value) for value in values]


def _normalize_trajectory(trajectory: Any) -> list[Any]:
    points = _to_list(trajectory)
    # Remove an optional batch dimension: [[[x, y], ...]] -> [[x, y], ...].
    # Keep a one-point trajectory [[x, y]] intact.
    while (
        len(points) == 1
        and isinstance(points[0], (list, tuple))
        and points[0]
        and isinstance(points[0][0], (list, tuple))
    ):
        points = list(points[0])
    return points


def trajectory_to_command(
    trajectory: Any,
    *,
    lookahead_m: float,
    turn_threshold_deg: float,
) -> Command | None:
    """Convert one local trajectory point to a chassis command.

    InternNav trajectory coordinates are interpreted as x-forward and y-left.
    This is deliberately a one-step closed-loop fallback, not official MPC
    trajectory tracking: after this command, the Skill captures a new image.
    """
    points: list[tuple[float, float]] = []
    for point in _normalize_trajectory(trajectory):
        if point is None or not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        x, y = float(point[0]), float(point[1])
        if math.isfinite(x) and math.isfinite(y):
            points.append((x, y))

    if not points:
        return None

    target_x, target_y = points[-1]
    for x, y in points:
        if math.hypot(x, y) >= lookahead_m:
            target_x, target_y = x, y
            break

    target_distance = math.hypot(target_x, target_y)
    if target_distance < 1e-4:
        return None

    angle_deg = math.degrees(math.atan2(target_y, target_x))
    if abs(angle_deg) > turn_threshold_deg:
        return {"rotate_deg": angle_deg}

    # Use the selected look-ahead point rather than the trajectory endpoint;
    # the latter may be much farther away than this closed-loop step.
    return {"forward_m": min(lookahead_m, target_distance)}


def discrete_to_commands(
    actions: Any,
    *,
    forward_m: float,
    step_deg: float,
) -> tuple[str, list[Command]]:
    """Convert each InternNav action to one generic chassis command.

    0 is a no-op in an action sequence and means stop when it is the only
    effective action. 1 moves forward, 2 turns left, 3 turns right. Camera-only
    actions 5 and 9 cannot be executed by this chassis and are ignored. The
    remaining actions keep their original order and are not merged or split.
    """
    action_list = _normalize_actions(actions)
    if not action_list:
        return "wait", []

    commands: list[Command] = []
    for action in action_list:
        if action in (0, 5, 9):
            continue
        if action == 1:
            commands.append({"forward_m": forward_m})
        elif action == 2:
            commands.append({"rotate_deg": step_deg})
        elif action == 3:
            commands.append({"rotate_deg": -step_deg})
        else:
            raise ValueError(f"unsupported InternNav discrete action: {action}")

    if commands:
        return "move", commands
    if all(action == 0 for action in action_list):
        return "stop", []
    return "wait", []


def result_to_commands(
    result: dict[str, Any],
    *,
    lookahead_m: float,
    discrete_forward_m: float,
    turn_threshold_deg: float,
    discrete_step_deg: float,
) -> tuple[str, list[Command]]:
    discrete_action = result.get("discrete_action")
    if discrete_action is not None:
        return discrete_to_commands(
            discrete_action,
            forward_m=discrete_forward_m,
            step_deg=discrete_step_deg,
        )

    trajectory = result.get("trajectory")
    if trajectory is not None:
        command = trajectory_to_command(
            trajectory,
            lookahead_m=lookahead_m,
            turn_threshold_deg=turn_threshold_deg,
        )
        return ("move", [command]) if command is not None else ("wait", [])

    raise RuntimeError("InternVLA result contains neither discrete_action nor trajectory")