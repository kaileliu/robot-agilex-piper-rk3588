#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""realsense_camera_rbnx — Intel RealSense D435i RGBD primitive
(capability_id=realsense_camera).

Owns `robonix/primitive/camera/*`. The D435i has an internal IMU but
we deliberately do NOT atlas-route it under `primitive/imu/imu`
(MID-360 IMU is canonical for the ranger). Subscribers needing the
camera IMU directly can read /<camera_name>/imu.

Capability surface:
  primitive/camera/driver         rpc gRPC (lifecycle)
  primitive/camera/rgb            topic_out ROS2 (continuous, raw)
  primitive/camera/depth          topic_out ROS2 (continuous, raw)
  primitive/camera/snapshot       rpc MCP (one-shot RGB JPEG — VLM-facing)
  primitive/camera/depth_snapshot rpc MCP (one-shot depth as 8-bit JPEG)
  primitive/camera/extrinsics     topic_out ROS2 (TODO — latched TF)

Lifecycle:
    on_init      — spawn rs_launch.py with camera_name + profiles → wait
                   for first RGB frame → subscribe rgb+depth → declare
                   rgb + depth topic_out + snapshot + depth_snapshot.
    on_shutdown  — kill realsense subprocess.

Config (from manifest):
    camera_name        default "camera_435i"
    rgb_topic          default "/<camera_name>/color/image_raw"
    depth_topic        default "/<camera_name>/aligned_depth_to_color/image_raw"
    rgb_profile        default "640x480x30"
    depth_profile      default "848x480x30"
    align_depth        default true
    enable_imu         default true   (published, NOT atlas-routed)
    enable_sync        default true
    sentinel_timeout_s default 30.0
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from io import BytesIO
from pathlib import Path

import numpy as np

from robonix_api import Primitive, Ok, Err

logging.basicConfig(
    level=os.environ.get("REALSENSE_LOG_LEVEL", "INFO"),
    format="[realsense] %(message)s",
)
log = logging.getLogger("realsense")

cap = Primitive(id="realsense_camera", namespace="robonix/primitive/camera")


# ── snapshot state ───────────────────────────────────────────────────────────
_state_lock = threading.Lock()
_latest_rgb_jpeg: bytes | None = None
_latest_depth_jpeg: bytes | None = None
_rgb_frame_id: str = "camera_435i_color_optical_frame"
_depth_frame_id: str = "camera_435i_depth_optical_frame"


# ── pyrealsense2 采集 + EMA 自动曝光（手动 AE：关闭 hardware AE，用 EMA 平滑
#    中位数亮度 + 高光占比惩罚来调 exposure；环境光照缓慢变化时持续跟踪）──
_rs_pipeline = None
_rs_color_sensor = None
_rs_stop = threading.Event()
_rs_exposure = 1000.0
_rs_pub = None            # robonix_api publisher (robonix/primitive/camera/rgb)
_rs_align = None          # rs.align(stream.color)：深度对齐到彩色像素


def _rs_open(cfg: dict):
    """打开 D435i 彩色+深度流（深度对齐到彩色），关闭 hardware auto-exposure，
    返回 (pipeline, color sensor, rs 模块, align 对象)"""
    w = int(cfg.get("width", 1280))
    h = int(cfg.get("height", 720))
    fps = int(cfg.get("fps", 30))
    # 深度流 profile（z16，毫米）。帧率与彩色一致，rs.align 才能逐帧配对。
    dw, dh, dfps = (int(x) for x in str(
        cfg.get("depth_profile", "848x480x30")).split("x"))
    import pyrealsense2 as rs
    pipeline = rs.pipeline()
    conf = rs.config()
    conf.enable_stream(rs.stream.color, w, h, rs.format.bgr8, fps)
    conf.enable_stream(rs.stream.depth, dw, dh, rs.format.z16, dfps)
    profile = pipeline.start(conf)
    align = rs.align(rs.stream.color)
    sensor = None
    for s in profile.get_device().query_sensors():
        if "RGB" in s.name:
            sensor = s
    if sensor is None:
        raise RuntimeError("未找到 RGB sensor")
    # 关掉失效的 hardware AE
    try:
        sensor.set_option(rs.option.enable_auto_exposure, 0)
    except Exception as e:
        log.warning("disable auto exposure failed: %s", e)
    log.info("realsense opened color %dx%d@%d + depth %dx%d@%d (manual AE)",
             w, h, fps, dw, dh, dfps)
    return pipeline, sensor, rs, align


def _rs_capture_loop(rgb_topic: str, depth_topic: str):
    """采集 + EMA 自动曝光 + 发布 RGB + 对齐深度(16UC1)"""
    global _latest_rgb_jpeg, _latest_depth_jpeg, _rgb_frame_id, _rs_exposure, _rs_pub
    import cv2
    import numpy as np
    pub = cap.create_publisher(
        "robonix/primitive/camera/rgb", topic=rgb_topic,
        msg_type="Image", qos="best_effort", declare=False)
    _rs_pub = pub
    depth_pub = cap.create_publisher(
        "robonix/primitive/camera/depth", topic=depth_topic,
        msg_type="Image", qos="best_effort", declare=False)
    import pyrealsense2 as _rsmod

    # EMA 自动曝光参数（已在真机验证收敛）
    ALPHA = 0.02
    MED_TARGET = 150.0
    PH_STOP1 = 0.02
    PH_STOP2 = 0.01
    med_e = None
    ph_e = None
    last_adj = -10
    frame_i = 0

    while not _rs_stop.is_set():
        frames = _rs_pipeline.wait_for_frames()
        if _rs_align is not None:
            frames = _rs_align.process(frames)
        cf = frames.get_color_frame()
        if not cf:
            time.sleep(0.01)
            continue
        frame = np.asanyarray(cf.get_data())
        frame_i += 1
        gray = frame.mean(axis=2)
        med = float(np.median(gray))
        ph = float((gray > 250).mean())
        if med_e is None:
            med_e, ph_e = med, ph
        else:
            med_e = ALPHA * med + (1 - ALPHA) * med_e
            ph_e = ALPHA * ph + (1 - ALPHA) * ph_e
        # 控制曝光（5 帧间隔，高光优先）
        if frame_i - last_adj >= 5:
            ne = _rs_exposure
            if ph_e > PH_STOP1:
                ne = _rs_exposure * 0.90
            elif ph_e > PH_STOP2:
                ne = _rs_exposure * 0.95
            else:
                if abs(med_e - MED_TARGET) > 15:
                    err = (med_e - MED_TARGET) / MED_TARGET
                    ne = _rs_exposure * (1.0 - err * 0.08)
            ne = max(100, min(8000, ne))
            if abs(ne - _rs_exposure) > 5:
                _rs_exposure = ne
                last_adj = frame_i
                try:
                    _rs_color_sensor.set_option(_rsmod.option.exposure,
                                                int(_rs_exposure))
                except Exception as e:
                    log.warning("set exposure failed: %s", e)

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        from sensor_msgs.msg import Image as RsImage
        from builtin_interfaces.msg import Time as RsTime
        from std_msgs.msg import Header as RsHeader
        im = RsImage()
        im.header = RsHeader()
        now = time.time()
        im.header.stamp = RsTime(sec=int(now), nanosec=int((now % 1) * 1e9) % 10**9)
        im.header.frame_id = _rgb_frame_id
        im.height, im.width, im.encoding = h, w, "rgb8"
        im.is_bigendian = 0
        im.step = w * 3
        im.data = rgb.tobytes()
        pub.publish(im)
        ok2, jpg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if ok2:
            with _state_lock:
                _latest_rgb_jpeg = jpg.tobytes()
        # ── 对齐深度（aligned_depth_to_color，16UC1 毫米，与彩色同尺寸）──
        df = frames.get_depth_frame()
        if df is not None:
            dnp = np.asanyarray(df.get_data())
            dh_, dw_ = dnp.shape
            dim = RsImage()
            dim.header = RsHeader()
            dim.header.stamp = im.header.stamp
            dim.header.frame_id = _depth_frame_id
            dim.height, dim.width, dim.encoding = dh_, dw_, "16uc1"
            dim.is_bigendian = 0
            dim.step = dw_ * 2
            dim.data = dnp.tobytes()
            depth_pub.publish(dim)
            # 快照缓存：8-bit 归一化 JPEG（与 _ros_image_to_jpeg 的 16uc1 路径一致）
            mx = int(dnp.max())
            if mx > 0:
                vis = (dnp / mx * 255).astype(np.uint8)
                okd, djpg = cv2.imencode(".jpg", vis,
                                         [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                if okd:
                    with _state_lock:
                        _latest_depth_jpeg = djpg.tobytes()
        time.sleep(0.005)


def _spawn_realsense(cfg: dict) -> None:
    """pyrealsense2 backend: open + start capture/auto-exposure/publish thread."""
    global _rs_pipeline, _rs_color_sensor, _rs_align
    print("[TIMING] _spawn_realsense start", flush=True)
    _rs_pipeline, _rs_color_sensor, _, _rs_align = _rs_open(cfg)
    print("[TIMING] realsense opened", flush=True)
    cam = cfg.get("camera_name", "camera_435i")
    rgb_topic = cfg.get("rgb_topic", f"/{cam}/color/image_raw")
    depth_topic = cfg.get(
        "depth_topic", f"/{cam}/aligned_depth_to_color/image_raw")
    _rs_stop.clear()
    threading.Thread(target=_rs_capture_loop, args=(rgb_topic, depth_topic),
                     daemon=True).start()
    print("[TIMING] capture thread started", flush=True)
    log.info("realsense capture thread started (rgb=%s, depth=%s, manual AE)",
             rgb_topic, depth_topic)


def _kill_realsense() -> None:
    _rs_stop.set()
    if _rs_pipeline is not None:
        try:
            _rs_pipeline.stop()
        except Exception as e:
            log.warning("stop pipeline failed: %s", e)


# ── image conversion ─────────────────────────────────────────────────────────
def _ros_image_to_jpeg(msg) -> bytes:
    """Encode a sensor_msgs/Image into JPEG bytes.
    Supports: rgb8, bgr8, rgba8, bgra8, mono8, 16uc1, 32fc1."""
    h, w = msg.height, msg.width
    enc = msg.encoding.lower()
    if enc == "rgb8":
        arr = np.frombuffer(msg.data, dtype=np.uint8).reshape(h, w, 3)
    elif enc == "bgr8":
        arr = np.frombuffer(msg.data, dtype=np.uint8).reshape(h, w, 3)[:, :, ::-1]
    elif enc == "rgba8":
        arr = np.frombuffer(msg.data, dtype=np.uint8).reshape(h, w, 4)[:, :, :3]
    elif enc == "bgra8":
        arr = np.frombuffer(msg.data, dtype=np.uint8).reshape(h, w, 4)[:, :, :3][:, :, ::-1]
    elif enc == "mono8":
        arr = np.frombuffer(msg.data, dtype=np.uint8).reshape(h, w)
        arr = np.stack([arr, arr, arr], axis=-1)
    elif enc == "16uc1":
        # realsense depth: 16-bit mm. Normalize to 8-bit for visualization.
        raw = np.frombuffer(msg.data, dtype=np.uint16).reshape(h, w)
        arr = (raw / raw.max() * 255).astype(np.uint8) if raw.max() > 0 else np.zeros((h, w), np.uint8)
        arr = np.stack([arr, arr, arr], axis=-1)
    elif enc == "32fc1":
        raw = np.frombuffer(msg.data, dtype=np.float32).reshape(h, w)
        valid = np.isfinite(raw)
        if valid.any():
            mn, mx = raw[valid].min(), raw[valid].max()
            norm = np.where(valid, (raw - mn) / max(mx - mn, 1e-6) * 255, 0).astype(np.uint8)
        else:
            norm = np.zeros((h, w), np.uint8)
        arr = np.stack([norm, norm, norm], axis=-1)
    else:
        raise ValueError(f"unsupported image encoding: {enc}")
    from PIL import Image as PILImage
    buf = BytesIO()
    PILImage.fromarray(np.ascontiguousarray(arr)).save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _on_rgb(msg) -> None:
    global _latest_rgb_jpeg, _rgb_frame_id
    try:
        jpg = _ros_image_to_jpeg(msg)
        with _state_lock:
            _latest_rgb_jpeg = jpg
            if msg.header.frame_id:
                _rgb_frame_id = msg.header.frame_id
    except Exception as e:  # noqa: BLE001
        log.warning("RGB conversion error: %s", e)


def _on_depth(msg) -> None:
    global _latest_depth_jpeg, _depth_frame_id
    try:
        jpg = _ros_image_to_jpeg(msg)
        with _state_lock:
            _latest_depth_jpeg = jpg
            if msg.header.frame_id:
                _depth_frame_id = msg.header.frame_id
    except Exception as e:  # noqa: BLE001
        log.warning("depth conversion error: %s", e)


# ── MCP snapshot tools (typed against codegen MCP dataclasses) ──────────────
import builtin_interfaces_mcp  # noqa: E402
import std_msgs_mcp  # noqa: E402
from sensor_msgs_mcp import Image  # noqa: E402
from std_msgs_mcp import Empty  # noqa: E402


def _now_header(frame_id: str) -> std_msgs_mcp.Header:
    now = time.time()
    sec = int(now)
    ns = int((now % 1) * 1e9) % 1_000_000_000
    return std_msgs_mcp.Header(
        stamp=builtin_interfaces_mcp.Time(sec=sec, nanosec=ns),
        frame_id=frame_id,
    )


def _jpeg_to_image_mcp(jpg: bytes, frame_id: str) -> Image:
    from PIL import Image as PILImage
    im = PILImage.open(BytesIO(jpg))
    w, h = im.size
    return Image(
        header=_now_header(frame_id),
        height=h, width=w,
        encoding="jpeg",
        is_bigendian=0,
        step=len(jpg),
        data=jpg,
    )


def _empty_image_error(reason: str) -> Image:
    """Return a tiny black 1x1 JPEG when we can't deliver a frame.
    Reason is encoded in frame_id so the agent can read it."""
    from PIL import Image as PILImage
    buf = BytesIO()
    PILImage.new("RGB", (1, 1), (0, 0, 0)).save(buf, format="JPEG")
    return _jpeg_to_image_mcp(buf.getvalue(), f"error:{reason}")


@cap.mcp("robonix/primitive/camera/snapshot")
def snapshot(msg: Empty) -> Image:
    """PRIMARY perception tool. Use freely — between every chassis/cmd
    burst — to see what's in front of the robot and decide what to do
    next. Returns the current RGB frame as a JPEG-encoded
    sensor_msgs/Image (encoding='jpeg', data=JPEG bytes)."""
    with _state_lock:
        data = _latest_rgb_jpeg
        frame_id = _rgb_frame_id
    if data is None:
        return _empty_image_error("no RGB frame received yet")
    return _jpeg_to_image_mcp(data, frame_id)


@cap.mcp("robonix/primitive/camera/depth_snapshot")
def depth_snapshot(msg: Empty) -> Image:
    """Depth snapshot as 8-bit JPEG (normalized for visualization).
    Returns sensor_msgs/Image with encoding='jpeg'. For actual metric
    depth, subscribe to robonix/primitive/camera/depth (16UC1)."""
    with _state_lock:
        data = _latest_depth_jpeg
        frame_id = _depth_frame_id
    if data is None:
        return _empty_image_error("no depth frame received yet")
    return _jpeg_to_image_mcp(data, frame_id)


# ── lifecycle ────────────────────────────────────────────────────────────────
@cap.on_init
def init(cfg: dict):
    """REGISTERED → INACTIVE: spawn realsense, subscribe RGB+depth, declare."""
    cam = cfg.get("camera_name", "camera_435i")
    rgb_topic = cfg.get("rgb_topic", f"/{cam}/color/image_raw")
    depth_topic = cfg.get(
        "depth_topic", f"/{cam}/aligned_depth_to_color/image_raw"
    )
    intrinsics_topic = cfg.get("intrinsics_topic", f"/{cam}/color/camera_info")
    sentinel_timeout = float(cfg.get("sentinel_timeout_s", 30.0))

    try:
        _spawn_realsense(cfg)
    except Exception as e:  # noqa: BLE001
        return Err(f"spawn realsense failed: {e}")

    # Subscribe RGB + depth via robonix_api (declare=False — we declare
    # the ros2 topic_out interfaces explicitly below, after sentinel passes).
    cap.create_subscription(
        "robonix/primitive/camera/rgb",
        topic=rgb_topic, msg_type="Image",
        callback=_on_rgb, qos="best_effort", declare=False,
    )
    cap.create_subscription(
        "robonix/primitive/camera/depth",
        topic=depth_topic, msg_type="Image",
        callback=_on_depth, qos="best_effort", declare=False,
    )

    # Gate INIT on first RGB arriving — webots/jetson cold-boot can lag.
    if not cap.wait_for_topic(rgb_topic, "Image", sentinel_timeout):
        _kill_realsense()
        return Err(f"no Image on {rgb_topic} within {sentinel_timeout:.1f}s")
    # 深度流软校验：失败不挡 boot（臂栈不该被深度问题拖死），日志报出来即可。
    if not cap.wait_for_topic(depth_topic, "Image", 5.0):
        log.warning("no depth Image on %s within 5s — depth_snapshot will "
                    "error until depth frames arrive", depth_topic)

    cap.declare_ros2_topic(
        "robonix/primitive/camera/rgb",
        topic=rgb_topic, qos="best_effort",
    )
    cap.declare_ros2_topic(
        "robonix/primitive/camera/depth",
        topic=depth_topic, qos="best_effort",
    )
    # Pinhole intrinsics (sensor_msgs/CameraInfo) for the color stream. Depth is
    # aligned_depth_to_color, so consumers reuse the color K to back-project.
    # Without this, scene's ConceptGraphs detector blocks forever on
    # "waiting for camera intrinsics" and never produces 3D objects.
    cap.declare_ros2_topic(
        "robonix/primitive/camera/intrinsics",
        topic=intrinsics_topic,
        qos="reliable",
    )
    log.info("init complete: rgb=%s depth=%s intrinsics=%s + snapshot/depth_snapshot MCP exposed",
             rgb_topic, depth_topic, intrinsics_topic)
    return Ok()


@cap.on_shutdown
def shutdown():
    _kill_realsense()
    return Ok()


# ── 插桩：原语启动/init 各阶段时间戳（定位 soma 1s settle 未等到 driver 的环节）──
import time as _t

_t_start = _t.monotonic()


def _stamp(tag: str):
    print("[TIMING] %-30s t=%.3fs" % (tag, _t.monotonic() - _t_start),
          flush=True)


if __name__ == "__main__":
    _stamp("main start (before cap.run)")
    cap.run()
