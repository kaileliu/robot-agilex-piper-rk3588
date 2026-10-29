#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""lynx_rtsp_camera — DeepRobotics Lynx S10 狗自带相机 RTSP primitive
(package=robonix.primitive.lynx_rtsp_camera, namespace=robonix/primitive/camera).

狗相机骨架: 相机硬件(SPCA2650×4)由狗官方 robot_drivers 采集并推流到狗上
mediamtx (RTSP :8554, 路径 video0/2/4/6, 凭据 admin/drcam@123 来自狗上
mediamtx.yml)。本原语把 RTSP 流接入 robonix:

  primitive/camera/driver    rpc gRPC  (lifecycle)
  primitive/camera/rgb       topic_out ROS2 (continuous, rgb8)
  primitive/camera/snapshot  rpc MCP    (one-shot JPEG — InternVLA 导航用)

与 D435i 原语接口完全一致(同一套 contracts), 上层 skill 通过 atlas 发现
snapshot 即可, 对其无品牌感知。

Config (from manifest):
    rtsp_url   default "rtsp://admin:drcam@123@10.21.33.103:8554/video4"
    camera_name default "camera_lynx"
    rgb_topic  default "/<camera_name>/color/image_raw"
    fps        default 30         — 发布节流，非编码帧率
    sentinel_timeout_s default 30.0
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np

from robonix_api import Primitive, Ok, Err

# codegen 生成的 MCP types（rob onx start 不一定注入，这里显式兜底）
sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "rbnx-build" / "codegen" / "robonix_mcp_types")
)

logging.basicConfig(
    level=os.environ.get("LYNX_RTSP_LOG_LEVEL", "INFO"),
    format="[lynx_rtsp] %(message)s",
)
log = logging.getLogger("lynx_rtsp")

cap = Primitive(id="lynx_rtsp_camera", namespace="robonix/primitive/camera")

# ── snapshot state ───────────────────────────────────────────────────────────
_state_lock = threading.Lock()
_latest_rgb_jpeg: bytes | None = None
_rgb_frame_id: str = "camera_lynx_color_optical_frame"


# ── RTSP 采集 + 发布 ─────────────────────────────────────────────────────────
_capture: "cv2.VideoCapture | None" = None
_capture_url: str = ""
_stop = threading.Event()
_pub = None            # robonix_api publisher


def _rtsp_url(cfg: dict) -> str:
    return str(
        cfg.get(
            "rtsp_url",
            "rtsp://admin:drcam@123@10.21.33.103:8554/video4",
        )
    )


def _open_capture(url: str):
    """Open RTSP stream; raise RuntimeError if the first frame can't be reached."""
    import cv2
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        cap.release()
        raise RuntimeError(f"RTSP open failed: {url}")
    # 首帧校验（否则 wait_for_topic sentinel 会超时）
    for _ in range(5):
        ok, _ = cap.read()
        if ok:
            break
    else:
        raise RuntimeError("RTSP opened but no frame arrived")
    return cap


def _capture_loop(rgb_topic: str):
    """拉 RTSP → 发布 rgb8 → 缓存最新 JPEG。断线自动重连（1s 退避）。"""
    global _capture, _pub, _latest_rgb_jpeg, _rgb_frame_id
    import cv2
    pub = cap.create_publisher(
        "robonix/primitive/camera/rgb", topic=rgb_topic,
        msg_type="Image", qos="best_effort", declare=False)
    _pub = pub

    from sensor_msgs.msg import Image as RsImage
    from builtin_interfaces.msg import Time as RsTime
    from std_msgs.msg import Header as RsHeader

    while not _stop.is_set():
        if _capture is None:
            try:
                _capture = _open_capture(_capture_url)
                log.info("RTSP connected: %s", _capture_url)
            except Exception as e:  # noqa: BLE001
                log.warning("reconnect failed (%.1fs backoff): %s", 1.0, e)
                _stop.wait(1.0)
                continue
        try:
            ok, frame = _capture.read()
        except Exception as e:  # noqa: BLE001
            log.warning("read error: %s", e)
            ok = False
            frame = None
        if not ok or frame is None:
            log.warning("RTSP stream dropped; reopening")
            try:
                _capture.release()
            except Exception:  # noqa: BLE001
                pass
            _capture = None
            _stop.wait(1.0)
            continue

        # 发布 rgb8
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        im = RsImage()
        im.header = RsHeader()
        now = time.time()
        im.header.stamp = RsTime(sec=int(now), nanosec=int((now % 1) * 1e9) % 10**9)
        im.header.frame_id = _rgb_frame_id
        im.height, im.width, im.encoding = h, w, "rgb8"
        im.is_bigendian = 0
        im.step = w * 3
        im.data = rgb.tobytes()
        try:
            pub.publish(im)
        except Exception as e:  # noqa: BLE001
            log.warning("publish failed: %s", e)

        # 最新 JPEG 缓存（snapshot 用）
        ok2, jpg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if ok2:
            with _state_lock:
                _latest_rgb_jpeg = jpg.tobytes()

        _stop.wait(1.0 / 30.0)


def _spawn(cfg: dict) -> None:
    """open RTSP + start capture/publish thread."""
    global _capture_url
    _capture_url = _rtsp_url(cfg)
    cam = cfg.get("camera_name", "camera_lynx")
    rgb_topic = cfg.get("rgb_topic", f"/{cam}/color/image_raw")
    _stop.clear()
    threading.Thread(target=_capture_loop, args=(rgb_topic,), daemon=True).start()
    log.info("capture thread started (rgb=%s)", rgb_topic)


def _kill() -> None:
    global _capture
    _stop.set()
    if _capture is not None:
        try:
            _capture.release()
        except Exception:  # noqa: BLE001
            pass
    _capture = None


# ── MCP snapshot tool (typed against codegen MCP dataclasses) ──────────────
import builtin_interfaces_mcp  # noqa: E402, F401
import std_msgs_mcp  # noqa: E402, F401
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
    """JPEG bytes → codegen sensor_msgs/Image（无 Pillow 依赖；用 cv2 解尺寸）"""
    import cv2
    arr = cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)
    h, w = arr.shape[:2]
    return Image(
        header=_now_header(frame_id),
        height=h, width=w,
        encoding="jpeg",
        is_bigendian=0,
        step=len(jpg),
        data=jpg,
    )


def _empty_image_error(reason: str) -> Image:
    import cv2
    buf = cv2.imencode(
        ".jpg", np.zeros((1, 1, 3), np.uint8),
        [int(cv2.IMWRITE_JPEG_QUALITY), 85],
    )[1].tobytes()
    return _jpeg_to_image_mcp(buf, f"error:{reason}")


@cap.mcp("robonix/primitive/camera/snapshot")
def snapshot(msg: Empty) -> Image:
    """PRIMARY perception tool. Returns the CURRENT dog-camera RGB frame as a
    JPEG-encoded sensor_msgs/Image (encoding='jpeg', data=JPEG bytes). Use it
    freely between navigation steps to see what the dog sees."""
    with _state_lock:
        data = _latest_rgb_jpeg
        frame_id = _rgb_frame_id
    if data is None:
        return _empty_image_error("no RGB frame received yet")
    return _jpeg_to_image_mcp(data, frame_id)


# ── lifecycle ────────────────────────────────────────────────────────────────
@cap.on_init
def init(cfg: dict):
    """REGISTERED → INACTIVE: spawn capture, subscribe RGB, declare topics."""
    cam = cfg.get("camera_name", "camera_lynx")
    rgb_topic = cfg.get("rgb_topic", f"/{cam}/color/image_raw")
    sentinel_timeout = float(cfg.get("sentinel_timeout_s", 30.0))

    try:
        _spawn(cfg)
    except Exception as e:  # noqa: BLE001
        return Err(f"spawn rtsp camera failed: {e}")

    if not cap.wait_for_topic(rgb_topic, "Image", sentinel_timeout):
        _kill()
        return Err(f"no Image on {rgb_topic} within {sentinel_timeout:.1f}s")

    cap.declare_ros2_topic(
        "robonix/primitive/camera/rgb",
        topic=rgb_topic, qos="best_effort",
    )
    log.info("init complete: rgb=%s + snapshot MCP exposed", rgb_topic)
    return Ok()


@cap.on_shutdown
def shutdown():
    _kill()
    return Ok()


if __name__ == "__main__":
    cap.run()
