# SPDX-License-Identifier: MulanPSL-2.0
"""pick_skill_rbnx atlas bridge — 能力声明 + 三个 MCP 工具的业务编排。

S10 版本：本包只保留 S10 需要的能力（grab / put_in_box / put_down / driver），
本文件只做编排。纯臂那套（pick / 通用检测抓取 / 侧摆 / demo）已存档在纯臂副本；
整理决策见 docs/PICK_SKILL_S10_REORG.md。**能力面与生命周期不在这里重复**，
见 package_manifest.yaml 与 capabilities/*.toml。

模块分工（只调它们的公开函数，不越过它们直接碰 gRPC / ROS 话题）：

    camera.py        取图：帧缓存、新鲜度判断、去畸变（含图像订阅）
    geometry.py      像素 → 臂基座 XY（手眼 homography）+ 落盒点排除判定
    detect.py        检测：yolo / vlm 两条路径的统一入口
    poses.py         位姿：构造、平移、dict / protobuf / MCP 三种表示的转换
    grpc_clients.py  Clients：上游通道与桩 + 四个契约调用（失败抛 GrpcCallError）
    motion.py        Arm：move_pose / go_observe / go_teach_safe /
                     open_gripper_in_place（失败抛 MotionError）

三个工具的流程：

    grab        arm.go_observe() 回观察位 → detect.py 找目标 → 问 grasp_pose 要
                抓取位姿 → arm.move_pose() ×3（悬停 / 下探合爪 / 抬起）→
                arm.verify_holding() 判定是否真夹住 → arm.go_teach_safe(True)
                保持握持回安全位；失败路径张爪回位，下一次 grab 从已知状态开始
    put_in_box  搬到盒子上方 → 释放（下放或在悬停位丢下）→ 回安全位
    put_down    arm.open_gripper_in_place：原样回放当前关节、只开夹爪（零臂运动）

为什么走 robonix 契约而不是裸 ROS 服务（上游 pick.py 的老路）：pilot 看到的系统
就是一组 robonix/* 契约，混进 ROS 服务会让"语义等价"的调用形状不一；而且部署里
谁拥有哪个服务可以变，atlas 解析能跟着变，写死的话题名不能。
"""
from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from robonix_api import ATLAS, Skill, Ok, Err  # noqa: E402

# 各管一摊的兄弟模块（分工见文件头 docstring）：
from . import poses
from .camera import CameraConfig, CameraFrames
from .config import flag, matrix, num, pick_key, text, warn_deprecated, xy, xy_map
from .detect import DetectConfig, Detector
from .geometry import DropZone, Homography
from .grpc_clients import Clients, GrpcCallError
from .motion import Arm, GripperConfig, MotionError, segment_budget

# MCP 工具的类型（由 rbnx codegen 生成）
from pick_mcp import (  # noqa: E402  pylint: disable=wrong-import-position
    Pick_Request, Pick_Response, PutDown_Request, PutDown_Response,
)

logging.basicConfig(
    level=os.environ.get("PICK_SKILL_LOG_LEVEL", "INFO"),
    format="[pick_skill] %(message)s",
)
log = logging.getLogger("pick_skill")

# Provider id MUST match the deploy manifest's `skill: - name: ...`.
pick_skill = Skill(
    id=os.environ.get("ROBONIX_CAPABILITY_ID", "pick"),
    namespace="robonix/skill/pick",
)


# ── shared state (between on_activate + handler) ────────────────────────────
_state_lock = threading.Lock()
# Resolved upstream endpoints. None until on_activate completes.
# Two keys, two contract ids — see REQUIRED_INPUTS below.
_endpoints: Optional[dict[str, str]] = None
# 传输层与臂：init 里构造，activate 里接线并启动，deactivate 里收摊。
# 两者都只在生命周期内有效——技能未激活时它们是 None。
_clients: Optional[Clients] = None
_arm: Optional[Arm] = None
_frames: Optional[CameraFrames] = None    # init 里 CameraFrames.from_cfg 建
_detector: Optional[Detector] = None      # init 里建
_DEFAULT_TIMEOUT_S = 60.0

# 技能的全部配置：init 里 from_cfg 一次，之后只读。**模块级的配置状态只有这一个**
# ——不再有一堆 global 需要维护（漏一条 global 就是静默失效）。
_config: Optional["SkillConfig"] = None


# ── 原语契约（相机帧 / 关节反馈 / 关节命令）────────────────────────────────
# 技能不再写死话题名：启动时经 atlas 解析这三个原语契约，拿到对端声明的话题名与
# QoS（connect_capability → Channel.endpoint / .params）。解析不到就回落配置里的
# 兜底值（多为单机调试场景）。
_PRIMITIVE_TOPICS = {
    "camera_rgb":     "robonix/primitive/camera/rgb",
    "arm_joint_states": "robonix/primitive/arm/joint_states",
    "arm_joint_command": "robonix/primitive/arm/joint_command",
}


def _connect_primitive(contract_id: str):
    """解析一个原语的自述契约并开通道；解析不到返回 None（调用方回落配置值）。"""
    try:
        cap = ATLAS.find_unique_capability(contract_id=contract_id, transport="ros2")
        return pick_skill.connect_capability(cap, contract_id, "ros2")
    except Exception as e:  # noqa: BLE001
        log.warning("primitive %s not resolvable via atlas (%s) — "
                    "falling back to configured topic", contract_id, e)
        return None


def _topic_of(channel, fallback_topic: str, fallback_qos: str) -> tuple:
    """从通道取出 (话题名, QoS)；没通道就用兜底值。"""
    if channel is None:
        return fallback_topic, fallback_qos
    qos = getattr(getattr(channel, "params", None), "qos_profile", "") or fallback_qos
    return str(channel.endpoint or fallback_topic), str(qos)


def _require_active() -> "SkillConfig":
    """三个工具的入口共用：技能没激活就抛，顺便把当前配置交出去。"""
    if _endpoints is None or _arm is None or _config is None:
        raise RuntimeError(
            "pick skill not active (atlas hasn't resolved upstream services yet)")
    return _config


# ── 落盒点与放置行为（对齐 roboarm 的 catch_and_place）──────────────────────
# roboarm 从不把物体留在手里：catch() 之后 place()，再空手 move_to_home()。
# 这里同构。
#
# 与 roboarm 的一处刻意差异：roboarm 按类名查落盒点（place_pos.{carrot,...}，
# 查不到就退到 class_place_pos=[0.1,0.0]）。这里只有一个盒子，永远放同一个点
# （_resolve_box_xy），不按名字查——按类查不仅多余，对类名表之外的物体还会把
# 东西丢到臂前方的默认点上，而不是盒子里。
@dataclass
class PlaceConfig:
    """放置相关：落盒点、两种模式、随机落点、盒内排除、实时配置文件。"""

    positions: dict[str, list[float]] = field(default_factory=dict)  # 只作为落盒点来源
    fallback_xy: list[float] = field(default_factory=lambda: [0.1, 0.0])  # 兜底，不是盒子
    box_xy: Optional[list[float]] = None            # 唯一的落盒点；None 时从 positions 推
    go_down_before_open: bool = True                # 两种放置模式见 put_in_box
    runtime_config_file: str = ""                   # 实时配置文件，格式见 _runtime_switches
    jitter_enabled: bool = False                    # 随机落点开关（默认关 = 每次落盒心）
    jitter_m: float = 0.03                          # 抖动幅度 ±
    exclusion_radius_m: float = 0.20                # 落盒点这个半径内视为"已在盒里"

    @classmethod
    def from_cfg(cls, cfg: dict) -> "PlaceConfig":
        # 实时配置文件的新旧键名（旧名供旧清单沿用）。
        config_file_key = pick_key(cfg, "runtime_config_file", "place_mode_override_file")
        if config_file_key == "place_mode_override_file":
            warn_deprecated("place_mode_override_file", "runtime_config_file")
        return cls(
            positions=xy_map(cfg, "place_positions"),
            fallback_xy=xy(cfg, "place_fallback_xy", [0.1, 0.0]),
            box_xy=xy(cfg, "box_xy"),
            go_down_before_open=flag(cfg, "place_go_down_before_open", True),
            runtime_config_file=text(cfg, config_file_key or "runtime_config_file"),
            jitter_enabled=flag(cfg, "place_jitter_enabled", False),
            jitter_m=num(cfg, "place_jitter_m", 0.03, lo=0.0),
            exclusion_radius_m=num(cfg, "place_exclusion_radius_m", 0.20, lo=0.0),
        )


# ── 高度模型：一个绝对量 + 两个偏移，全部在 arm/base_link 坐标系 ────────────
#     desktop_height   H    桌面高度。**每个部署按标定填**——坐标系跟着臂基座走，
#                           臂装在哪儿它就在哪儿（纯臂摆实验台、S10 挂狗背，两者
#                           不共享此值）。没配会告警。
#     grasp_offset          贴桌高度 = H + 它。夹爪合拢、贴桌面放置都用这个高度。
#     travel_offset         腾挪高度 = H + 它。所有"在空中移动"的位置：抓取悬停、
#                           抓起抬起、搬运到盒子上方、投放模式的松手点。
# 抓取点的 z 由 grasp_pose 服务算（它自己配的 default_desktop_height），按本模型
# 应当等于 H + grasp_offset；_grasp_and_verify 会核对并在不一致时告警（两份配置
# 漂了的话，抓取点和悬停点就会互相矛盾）。
@dataclass
class HeightConfig:
    """高度模型（米）。"""

    desktop_height: float = 0.115   # 示例值；每个部署必须自己配
    grasp_offset: float = 0.02
    travel_offset: float = 0.10

    @classmethod
    def from_cfg(cls, cfg: dict) -> "HeightConfig":
        if "desktop_height" not in cfg:
            log.warning("desktop_height not configured — assuming %.3f m. Every "
                        "deploy must set it to its own calibrated desktop height "
                        "(arm/base_link)!", cls.desktop_height)
        # 腾挪高度的旧键名（approach_dist / place_raise_height）仍接受，值一样。
        travel_key = pick_key(cfg, "travel_offset", "approach_dist", "place_raise_height")
        if travel_key and travel_key != "travel_offset":
            warn_deprecated(travel_key, "travel_offset")
        return cls(
            desktop_height=num(cfg, "desktop_height", cls.desktop_height),
            grasp_offset=num(cfg, "grasp_offset", cls.grasp_offset, lo=0.0),
            travel_offset=num(cfg, travel_key or "travel_offset",
                              cls.travel_offset, lo=0.0),
        )


@dataclass
class SkillConfig:
    """技能的全部配置：谁消费谁负责解析（各家的 from_cfg）。"""

    gripper: GripperConfig
    detect: DetectConfig
    heights: HeightConfig
    place: PlaceConfig
    camera: CameraConfig

    @classmethod
    def from_cfg(cls, cfg: dict) -> "SkillConfig":
        return cls(
            gripper=GripperConfig.from_cfg(cfg),
            detect=DetectConfig.from_cfg(cfg),
            heights=HeightConfig.from_cfg(cfg),
            place=PlaceConfig.from_cfg(cfg),
            camera=CameraConfig.from_cfg(cfg),
        )


# ── atlas-resolved upstream contracts ───────────────────────────────────────
# object_detect 服务（llm_detect_rbnx）刻意不是依赖：检测在技能内做（detect.py），
# S10 也不部署那个服务；原因见 docs/PICK_SKILL_S10_REORG.md。
REQUIRED_INPUTS = {
    "grasp_request":  ("robonix/service/perception/grasp_pose/grasp_request",    "grpc"),
    "execute_grasp":  ("robonix/service/manipulation/execute_grasp",             "grpc"),
}

# 可选依赖：缺了技能照样激活，各自行为退化——reset＝检测前的观察位与失败回位；
# teach_safe＝grab 收尾（保持握持）与每条退出路径的安全位。
# put_down 不是依赖：它靠 arm.open_gripper_in_place() 直接发 joint_command，
# 不经过 manipulation/put_down 服务。
OPTIONAL_INPUTS = {
    "reset":          ("robonix/service/manipulation/reset",                     "grpc"),
    "teach_safe":     ("robonix/service/manipulation/teach_safe",                "grpc"),
}


def _resolve_inputs(deadline_s: float = 60.0) -> dict[str, str]:
    """Block until atlas can resolve all REQUIRED_INPUTS upstream endpoints,
    or fail loudly. Then best-effort resolve OPTIONAL_INPUTS
    — missing optionals only generate a warning. Same shape as
    explore_rbnx.resolve_inputs."""
    required = REQUIRED_INPUTS

    resolved: dict[str, str] = {}
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        for key, (cid, transport) in required.items():
            if key in resolved:
                continue
            try:
                cap_view = ATLAS.find_unique_capability(
                    contract_id=cid, transport=transport)
                ch = pick_skill.connect_capability(cap_view, cid, transport)
            except Exception:  # noqa: BLE001
                continue
            ep = ch.endpoint
            try:
                ch.close()
            except Exception:  # noqa: BLE001
                pass
            if ep:
                resolved[key] = ep
                log.info("resolved %s [%s] → %s", cid, transport, ep)
        if len(resolved) == len(required):
            break
        time.sleep(2.0)

    missing = [k for k in required if k not in resolved]
    if missing:
        raise RuntimeError(
            f"pick skill cannot find dependencies on atlas: missing "
            f"{[required[k][0] for k in missing]}. The skill needs "
            f"grasp_pose_rbnx (perception/grasp_pose) + roboarm_ik_rbnx "
            f"(manipulation/execute_grasp) both ACTIVE before it can run. "
            f"There is intentionally no ROS-service fallback — "
            f"packaging-spec invariant #1.")

    # Best-effort resolve OPTIONAL_INPUTS. Single shot — if they're not
    # advertised yet, log a warning and move on. They'll be silently
    # skipped at call time.
    for key, (cid, transport) in OPTIONAL_INPUTS.items():
        try:
            cap_view = ATLAS.find_unique_capability(
                contract_id=cid, transport=transport)
            ch = pick_skill.connect_capability(cap_view, cid, transport)
            ep = ch.endpoint
            try:
                ch.close()
            except Exception:  # noqa: BLE001
                pass
            if ep:
                resolved[key] = ep
                log.info("resolved %s [%s] → %s (optional)", cid, transport, ep)
        except Exception:  # noqa: BLE001
            log.warning(
                "optional dep %s not on atlas — pick will run without it. "
                "See OPTIONAL_INPUTS docstring for degraded behaviour.", cid)

    return resolved


# 三段式竖直抓取（对齐 roboarm.catch()）：悬停 → 下探合爪 → 抬起，三步实现在
# _grasp_and_verify()；悬停高度用高度模型的 _travel_offset，见文件上半段。
# 注意那是"大致够用"的余量，不是竖直路径的保证：execute_grasp 在关节空间插值，
# 悬停位到下探位之间末端走的是弧线。
# （夹爪开度用 _arm.gripper.open_width / close_width —— 单一来源。）


def _apply_place_jitter(point: list[float], place: PlaceConfig) -> list[float]:
    """按开关给落点加 ±place.jitter_m 的均匀抖动（关掉就原样返回）。

    每次 place 只调一次，四个路点都用同一个结果，保证一次分拣自洽。
    """
    if not place.jitter_enabled or place.jitter_m <= 0.0:
        return [float(point[0]), float(point[1])]
    j = place.jitter_m
    return [float(point[0]) + random.uniform(-j, j),
            float(point[1]) + random.uniform(-j, j)]


def _resolve_box_xy(place: PlaceConfig) -> list[float]:
    """唯一的落盒点：显式 box_xy 优先，否则取 place_positions（几项都指同一个
    盒子），让标定值只存在一处。取不到时退到 fallback_xy——那不是盒子（是 roboarm
    的臂前默认点），所以会告警。
    """
    if place.box_xy is not None:
        return [float(place.box_xy[0]), float(place.box_xy[1])]
    for point in place.positions.values():
        return [float(point[0]), float(point[1])]
    log.warning("no box_xy and no place_positions configured — falling back to %s, "
                "which is NOT the box", place.fallback_xy)
    return [float(place.fallback_xy[0]), float(place.fallback_xy[1])]


def _runtime_switches(cfg: "SkillConfig") -> dict:
    """当前生效的运行时开关 = 配置值 + **实时配置文件**覆盖（每次调用都重读）。

    返回 {"place_go_down_before_open": bool, "detect_scheme": str}。动作开始前
    调一次，所以改文件立即生效，不用重启服务。

    文件路径见 PlaceConfig.runtime_config_file（配置键 runtime_config_file），格式
    为每行一个 key=value，只认 place_go_down_before_open=true|false 与
    detect_scheme=yolo|vlm（另有历史遗留的"首行裸 true/false"写法，只影响放置
    模式）。文件不存在、读不了、键缺失，一律回落 CMD_INIT 的配置值。
    """
    out = {
        "place_go_down_before_open": cfg.place.go_down_before_open,
        "detect_scheme": cfg.detect.default_backend,
    }
    path = cfg.place.runtime_config_file
    if not path:
        return out
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
    except OSError:
        return out
    if not lines:
        return out

    overridden: dict = {}
    first = lines[0].lower()
    if first in ("true", "1", "yes", "on"):
        overridden["place_go_down_before_open"] = True
    elif first in ("false", "0", "no", "off"):
        overridden["place_go_down_before_open"] = False
    for ln in lines:
        if "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        k, v = k.strip().lower(), v.strip().lower()
        if k in ("place_go_down_before_open", "place_mode", "go_down"):
            overridden["place_go_down_before_open"] = v in ("true", "1", "yes", "on")
        elif k in ("detect_scheme", "scheme") and v in ("yolo", "vlm"):
            overridden["detect_scheme"] = v
    if overridden:
        out.update(overridden)
        log.info("runtime switches (config file %s): %s", path, overridden)
    return out


@pick_skill.mcp("robonix/skill/pick/put_down")
def put_down(_req: PutDown_Request) -> PutDown_Response:
    """Put down / release the currently held object by opening the gripper
    WHERE THE ARM ALREADY IS — no arm motion.

    Use this when the user says put it down / release it / let go / drop it /
    open the gripper. It reads the live arm joint feedback and republishes it
    unchanged with only the fingers opened, so nothing but the gripper moves.
    It does NOT carry the object anywhere or change the arm pose.

    If the user names a destination — the box, putting it away, tidying up —
    use put_in_box instead, which carries the object there before releasing.
    """
    _require_active()
    t0 = time.monotonic()
    try:
        message = _arm.open_gripper_in_place()
    except MotionError as e:
        raise RuntimeError(f"put_down failed: {e}") from e
    return PutDown_Response(
        success=True,
        message=str(message),
        elapsed_s=time.monotonic() - t0,
    )


@pick_skill.mcp("robonix/skill/pick/put_in_box")
def put_in_box(_req: PutDown_Request) -> PutDown_Response:
    """Put the object the arm is ALREADY HOLDING into the box: carry it over
    the box, lower it, release it, and return home empty-handed.

    Call this with {} after a successful grab, when the user says put it in the
    box / 把东西放到盒子里 / 放进盒子里 / 收起来. It does NOT detect or grasp
    anything, so it works no matter which grab variant picked the object up.
    After grab the arm hovers above the grasp point holding the object;
    put_in_box carries it from that hover pose straight over the box.

    For "put it down / let go / 放下 / 松手" — releasing on the spot without
    carrying it anywhere — use put_down instead.

    内部流程（对齐 roboarm 的 arm_base.place() + catch_and_place 的收尾
    move_to_home）——落点与两个高度都来自高度模型（见文件上半段）：
      P1 合爪搬到盒子上方 → [下放模式] P2 合爪下放到落点 → P3 张爪释放
      → [下放模式] P4 张爪抬回上方 → P5 空手回安全位
    投放模式（place_go_down_before_open=false）跳过 P2/P4：臂停在悬停位就地张爪，
    物体自己掉进盒子（落差 = 腾挪高度 − 贴桌高度）。
    失败一律抛 RuntimeError；P4 抬高失败只告警（物体已经放开了）。
    """
    cfg = _require_active()
    t0 = time.monotonic()
    width = _arm.gripper_width_now()
    # Logged for diagnosis only. Gripper feedback saturates on this arm, so a
    # width check would reject legitimate places; carrying an empty gripper to
    # the box is harmless.
    log.info("put_in_box: gripper width feedback=%s",
             f"{width:.4f}m" if width is not None else "none")

    # 落点与高度：只有这一个盒子，落点与搬的是什么无关；贴桌高度放、飞到盒子上方
    # 用腾挪高度。
    heights = cfg.heights
    drop_z = heights.desktop_height + heights.grasp_offset
    hover_z = heights.desktop_height + heights.travel_offset
    px, py = _apply_place_jitter(_resolve_box_xy(cfg.place), cfg.place)
    place_pose = poses.vertical_pose(px, py, drop_z)
    hover_pose = poses.with_z(place_pose, hover_z)
    stage_to = segment_budget(_DEFAULT_TIMEOUT_S, 3)
    close_w = _arm.gripper.close_width
    open_w = _arm.gripper.open_width

    # 运行时模式：配置值 + 开关文件覆盖（见 _runtime_switches），演示时不用重启
    # 就能切换"下放"与"投放"。一次调用只读一次，保证整串动作用同一个模式。
    go_down = _runtime_switches(cfg)["place_go_down_before_open"]

    # P1..P5（见 docstring）。每步把"失败短语"交给动作层，它负责记日志、拼错误
    # 文案；这里只在最外层包一次，加上本工具的名字。P4 例外：失败只告警。
    log.info("put_in_box: drop point=(%.3f,%.3f) mode=%s released %.2fm above desk",
             px, py, "descend" if go_down else "drop", hover_z - drop_z)
    try:
        # P1: 合爪搬到盒子上方（仍握着）。
        _arm.move_pose(hover_pose, close_w, stage_to, stage="place carry")

        # 两种放置模式从这里分叉：下放 P2→P3→P4，投放 P3'（就地张爪，物体自己
        # 掉进盒子）。
        if go_down:
            _arm.move_pose(place_pose, close_w, stage_to, stage="place descend")   # P2
            _arm.move_pose(place_pose, open_w, stage_to, stage="place release")    # P3
            try:                                                                   # P4
                _arm.move_pose(hover_pose, open_w, stage_to, stage="place lift")
            except MotionError as e:
                # 物体已经放开了，不值得把一次成功的放置报成失败。
                log.warning("%s — object released, continuing home", e)
        else:
            _arm.open_gripper_in_place(stage="place release")                      # P3'

        # P5: 空手回安全位（服务端配置的 teach_safe_joints_deg + 张爪）。
        _arm.go_teach_safe(stage="home after place")
    except MotionError as e:
        # 失败就不硬撑——东西还在爪里，调用方可以重试或改用 put_down（所以这里
        # 不做任何复位动作），也可以把原因转述给用户。
        raise RuntimeError(f"put_in_box failed: {e}") from e

    drop_note = " (dropped from hover)" if not go_down else ""
    return PutDown_Response(
        success=True,
        message=f"placed at ({px:.3f},{py:.3f}){drop_note} and returned home",
        elapsed_s=time.monotonic() - t0,
    )


def _grasp_and_verify(grasp_pose_dict: dict, stage_budget_s: float,
                      heights: HeightConfig) -> tuple[str, float]:
    """三段式竖直抓取 + 夹爪校验：

    a. 悬停 —— pre_pose（x/y/朝向沿用抓取位姿，z = H + travel_offset），张爪
    b. 下探 —— grasp_pose（服务算的抓取位姿），合爪
    c. 抬起 —— pre_pose，保持合爪
    d. 读关节反馈，判定夹爪是否真的夹住物体

    a/b 段失败抛 RuntimeError；c 段失败只告警——物体已在爪中，仍按成功继续。
    d 段没夹住抛 RuntimeError。抛出的文案**不含**"grasp attempt failed; " 前缀，
    那是调用方（grab）在最外层统一加的。

    返回 (message, measured_width)；没夹住就抛异常了，所以能返回就是夹住了。

    stage_budget_s 是每段的执行预算，由调用方按剩余时间算好传入。
    """
    # 悬停位：水平位置与朝向沿用抓取位姿，高度换成模型里的"腾挪高度"。
    hover_z = heights.desktop_height + heights.travel_offset
    pre_pose_dict = poses.with_z(grasp_pose_dict, hover_z)
    open_w = _arm.gripper.open_width
    close_w = _arm.gripper.close_width

    # Y 方案：抓取点的 z 由 grasp_pose 服务按它自己的配置算，与模型里的
    # H + _grasp_offset 应当一致。不一致说明两份配置漂了（S10 曾因此把投放点
    # 抬高 14cm），这里核对一次、告警一次，不改写服务给的值。
    grasp_z = float(grasp_pose_dict.get("pose", {})
                    .get("position", {}).get("z", 0.0))
    expected_z = heights.desktop_height + heights.grasp_offset
    if abs(grasp_z - expected_z) > 0.01:
        log.warning("grasp_pose returned z=%.4f but this deploy's height model "
                    "expects %.4f (desktop_height %.3f + grasp_offset %.3f) — "
                    "check the grasp_pose service's default_desktop_height",
                    grasp_z, expected_z, heights.desktop_height, heights.grasp_offset)

    # 3a: 悬停（pre_pose + 张爪）。每步的失败短语交给动作层；"grasp attempt
    # failed; " 前缀由调用方加。
    _arm.move_pose(pre_pose_dict, open_w, stage_budget_s, stage="execute (approach)")

    # 3b: 下探 + 合爪（抓取位姿 + 合爪）。合爪时刻从这里算起，后面校验夹爪用。
    close_started_mono = time.monotonic()
    _arm.move_pose(grasp_pose_dict, close_w, stage_budget_s, stage="execute (descend)")

    # 3c: 抬起（pre_pose + 保持合爪）。失败只告警——物体已在爪中，仍按成功继续。
    try:
        _arm.move_pose(pre_pose_dict, close_w, stage_budget_s, stage="execute (lift)")
    except MotionError as e:
        log.warning("%s — reporting success anyway (object may be in gripper)", e)

    holding_ok, holding_msg, grip_width = _arm.verify_holding(close_started_mono)
    if not holding_ok:
        raise RuntimeError(f"gripper verification failed: {holding_msg}")
    log.info("grab gripper verification ok: %s", holding_msg)
    return holding_msg, grip_width


@pick_skill.mcp("robonix/skill/pick/grab")
def grab(req: Pick_Request) -> Pick_Response:
    """GRAB and KEEP HOLDING an object on the table. Use this for ANY grasp
    request — pass the user's description of the target as object_name
    (name, color, any wording). If the target is not on the table, the tool
    reports "object not detected" — relay that to the user and grasp
    nothing. Afterwards use put_in_box to drop the held object into the box.
    """
    # 流程见文件头 docstring。检测后端由 detect_scheme 决定（配置 + 运行时
    # 开关文件），没有按调用选的 backend 参数。
    cfg = _require_active()
    object_name = (req.object_name or "").strip()
    if not object_name:
        raise RuntimeError("object_name is empty")

    total_to = _DEFAULT_TIMEOUT_S
    t0 = time.monotonic()
    deadline = t0 + total_to
    grasp_succeeded = False

    try:
        # Stage 0: 检测前回到固定观察位（reset 是可选依赖；缺了只告警不报错，
        # 由 arm.go_observe() 内部处理）。
        _arm.go_observe(stage="pre_grab_reset")

        # S10：无顶部相机（D435i 在臂上），不旋转 aside —— 旋转反而会让臂带相机
        # 脱离观察位姿，D435i 检测不到桌面。直接在观察位姿检测。

        # Stage 1: 检测（detect.py 的统一入口：yolo=全量检测+措辞匹配，
        # vlm=一次多模态定位）。后端由 detect_scheme 决定，运行时开关文件可覆盖；
        # 认不出目标时抛 "detection_failed: ..."，pilot 据此回答"没有X"。
        det = _detector.detect(object_name,
                               backend=_runtime_switches(cfg)["detect_scheme"])

        bbox_2d   = list(det.get("bbox_2d") or [])
        center_3d = list(det.get("object_center_3d") or [])

        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"timeout: budget {total_to:.1f}s exhausted before grasp attempt")

        # Stage 2: 问 grasp_pose 服务要一个抓取位姿（感知，不动机器臂）。传输失败
        # 与"服务说算不出来"走同一条失败文案；center_3d 与 retry 本实现不读
        # （见 GraspRequest.srv），只把检测拿到的中心点原样传过去。
        endpoint = (_endpoints or {}).get("grasp_request")
        try:
            if not endpoint or _clients is None:
                raise GrpcCallError("grasp_request not resolved on atlas")
            gr = _clients.grasp_request(endpoint, object_name, bbox_2d, center_3d)
            log.info("grasp_request: object=%r -> success=%s score=%.3f gripper=%.3f",
                     object_name, gr.get("success"), float(gr.get("score", 0.0)),
                     float(gr.get("gripper_width", 0.0)))
            if not gr.get("success"):
                raise GrpcCallError(str(gr.get("message") or "unknown"))
        except GrpcCallError as e:
            last_failure_msg = f"grasp_pose failed: {e}"
            log.warning("%s", last_failure_msg)
            raise RuntimeError(
                f"grasp attempt failed; last: {last_failure_msg}") from e

        grasp_pose_dict = gr.get("grasp_pose") or poses.empty_pose()
        score           = float(gr.get("score", 0.0))

        # Stage 3: 三段式竖直抓取（悬停 → 下探合爪 → 抬起）+ 夹爪校验
        # —— 共用动作见 _grasp_and_verify()。它的失败文案不带本工具的定语，
        # 这里补上"grasp attempt failed; "。
        remaining = max(1.0, deadline - time.monotonic())
        stage_to = segment_budget(remaining, 3)
        heights = cfg.heights
        try:
            holding_msg, grip_width = _grasp_and_verify(grasp_pose_dict, stage_to, heights)
        except RuntimeError as e:
            raise RuntimeError(f"grasp attempt failed; {e}") from e

        grasp_succeeded = True

        # S10：grab 成功后保持握持、回安全位（服务端配置的 teach_safe 位姿），
        # 不再停在悬停位。hold_gripper=True 保持夹爪闭合（物体仍在手里）。
        # 这一步失败只告警——东西已经在手上，不必把已经成功的抓取改判成失败。
        try:
            _arm.go_teach_safe(hold_gripper=True, stage="grab hold")
        except MotionError as e:
            log.warning("%s — staying at grasp pose", e)

        return Pick_Response(
            success=True,
            message=f"ok (grab + hold at teach-safe pose; {holding_msg})",
            grasp_pose=poses.to_mcp(grasp_pose_dict),
            gripper_width=grip_width, score=score,
            elapsed_s=time.monotonic() - t0,
        )
    except Exception:
        # 只有失败才回位（回位会张爪），让臂干净地留给下一次；成功路径故意不复位
        # ——东西还在爪里。回位失败也吞掉，不能盖过正要返回给调用方的失败原因。
        if not grasp_succeeded:
            try:
                _arm.go_teach_safe()
            except Exception as e:  # noqa: BLE001
                log.warning("post-pick go_teach_safe failed: %s — continuing", e)
        raise


# ── lifecycle ───────────────────────────────────────────────────────────────
@pick_skill.on_init
def init(cfg):
    """CMD_INIT: light. Don't query atlas — upstream services may still be
    warming up. 这里只做两件事：解析配置（各家的 from_cfg）、构造协作者。"""
    global _config, _frames, _detector, _clients, _arm
    cfg = cfg or {}
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg) if cfg else {}
        except json.JSONDecodeError as e:
            return Err(f"bad config_json: {e}")

    # 配置解析全交给 SkillConfig.from_cfg：夹爪 / 检测 / 高度 / 放置四组，各自由
    # 消费它们的模块提供 from_cfg；坏值一律告警后回落默认值，绝不让 init 失败。
    _config = SkillConfig.from_cfg(cfg)

    # 帧缓存：activate 时在 ROS 节点上订阅图像话题（camera.attach），回调把帧喂给
    # update()。手眼矩阵给 Detector 做"落盒点排除"的像素→臂基座换算。
    _frames = CameraFrames.from_cfg(cfg)
    homography = Homography(matrix(cfg, "homography_matrix", 3, 3))

    # 落盒点用于"盒内排除"（已分拣进盒的物体不再当目标）。解不出来（配置残缺）
    # 时禁用排除，绝不让它拖垮 init。
    try:
        box_xy = _resolve_box_xy(_config.place)
        exclusion_radius_m = _config.place.exclusion_radius_m
    except Exception as e:  # noqa: BLE001
        log.warning("box drop point unresolvable (%s) — box exclusion disabled", e)
        box_xy, exclusion_radius_m = [0.0, 0.0], 0.0
    _detector = Detector(_frames, homography,
                         DropZone(box_xy, exclusion_radius_m), _config.detect)

    # 传输层与臂：只构造对象——上游端点在 activate 里接，ROS 节点也在 activate
    # 里才启动（init 必须保持"轻"，不碰 atlas / ROS）。
    _clients = Clients()
    _arm = Arm(_clients, _config.gripper, camera=_frames)

    log.info("CMD_INIT ok (timeout_s=%.1f, gripper_open=%.3f, gripper_close=%.3f, "
             "grasp_threshold=%.4f, feedback_topic=%s, heights(desktop=%.3f grasp=+%.3f "
             "travel=+%.3f), detect_backend=%s, yolo=%s, two_frame=%s, runtime_config=%s)",
             _DEFAULT_TIMEOUT_S, _config.gripper.open_width, _config.gripper.close_width,
             _config.gripper.grasp_threshold_width, _config.gripper.joint_states_topic,
             _config.heights.desktop_height, _config.heights.grasp_offset,
             _config.heights.travel_offset, _config.detect.default_backend,
             _config.detect.yolo_url,
             (_config.detect.two_frame_interval_s, _config.detect.two_frame_iou),
             _config.place.runtime_config_file or "none")
    return Ok()


@pick_skill.on_activate
def activate():
    """CMD_ACTIVATE: heavy. Resolve upstream endpoints. Idempotent."""
    global _endpoints
    with _state_lock:
        if _endpoints is not None:
            log.info("CMD_ACTIVATE — already active, no-op")
            return Ok()
        try:
            _endpoints = _resolve_inputs()
        except RuntimeError as e:
            return Err(str(e))
        # 端点交给 Arm（它要用 execute_grasp / reset / teach_safe），然后启动
        # 它的 ROS 节点：关节反馈订阅 + joint_command 发布器；相机订阅由
        # _frames.attach() 挂在同一个节点上。
        _arm.set_endpoints(_endpoints)

        # 原语话题经 atlas 解析（不写死话题名）；解析不到的回落配置兜底值。
        cam_topic, cam_qos = _topic_of(_connect_primitive(_PRIMITIVE_TOPICS["camera_rgb"]),
                                       _config.camera.topic, "best_effort")
        js_topic, js_qos = _topic_of(_connect_primitive(_PRIMITIVE_TOPICS["arm_joint_states"]),
                                     _config.gripper.joint_states_topic, "best_effort")
        jc_topic, jc_qos = _topic_of(_connect_primitive(_PRIMITIVE_TOPICS["arm_joint_command"]),
                                     _config.gripper.joint_command_topic, "best_effort")
        log.info("primitive topics: camera=%s [%s], joint_states=%s [%s], "
                 "joint_command=%s [%s]",
                 cam_topic, cam_qos, js_topic, js_qos, jc_topic, jc_qos)
        _frames.set_topic(cam_topic, cam_qos)
        _arm.set_topics((js_topic, js_qos), (jc_topic, jc_qos))

        _arm.start()
    log.info("CMD_ACTIVATE ok — endpoints resolved: %s", list(_endpoints.keys()))
    return Ok()


@pick_skill.on_deactivate
def deactivate():
    """CMD_DEACTIVATE: drop client cache + endpoints. Idle eviction."""
    global _endpoints
    with _state_lock:
        _endpoints = None
        if _arm is not None:
            _arm.set_endpoints(None)
            _arm.shutdown()      # 停 ROS 节点 + 关上游连接
    log.info("CMD_DEACTIVATE ok — endpoints cleared")
    return Ok()


def main() -> int:
    pick_skill.run()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
