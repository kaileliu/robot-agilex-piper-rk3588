#!/usr/bin/env python3
"""Serve the Robonix camera and existing NPU detector as a browser preview."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import math
from pathlib import Path
import signal
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "packages/skills/skill-pick-vertical-grasp-piper-rbnx"
sys.path[:0] = [str(PKG), str(PKG / "rbnx-build/codegen/proto_gen"),
                str(PKG / "rbnx-build/codegen/robonix_mcp_types"),
                "/opt/robonix/src/pylib/robonix-api"]
import cv2
import numpy as np
import rclpy
import yaml
from robonix_api import ATLAS
from pick_skill.camera import CameraFrames
from pick_skill.detect import DetectConfig, Detector

log = logging.getLogger("live_preview")


class Preview:
    def __init__(self, frames, cfg, fps):
        self.frames = frames
        self.detector = Detector(frames, None, None, DetectConfig.from_cfg(cfg))
        self.detector.cfg.yolo_timeout_s = 3.0
        self.period = 1.0 / fps
        self.stop = threading.Event()
        self.changed = threading.Condition()
        self.jpeg = {}
        self.sequence = 0
        self.updated = 0.0
        self.error = "正在等待相机画面"
        self.metrics = {}

    def status(self):
        with self.changed:
            age = time.monotonic() - self.updated if self.updated else None
            fresh = age is not None and age < 3.0
            return {"service": "robonix-yolo-preview", "ok": fresh and not self.error,
                    "error": self.error or ("画面已中断" if not fresh else ""),
                    "frame_age_s": round(age, 3) if age is not None else None,
                    "sequence": self.sequence, "device": "rk3588-npu", **self.metrics}

    def run(self):
        previous = None
        while not self.stop.is_set():
            started = time.monotonic()
            try:
                raw, age = self.frames.latest()
                if raw is None or age > 2.0:
                    raise RuntimeError("未收到新相机画面")
                frame = self.frames.undistort(raw)
                t0 = time.monotonic()
                detections = self.detector._yolo_detect_all(frame)
                elapsed = (time.monotonic() - t0) * 1000
                error = "" if detections is not None else "NPU 检测暂时不可用"
                detections = detections or []
                annotated = frame.copy()
                colors = {"carrot": (60, 160, 255), "potato": (40, 220, 250),
                          "tomato": (130, 100, 255)}
                for d in detections:
                    u, v, w, h, angle = d["obb"]
                    polygon = cv2.boxPoints(((u, v), (w, h), math.degrees(angle)))
                    polygon = polygon.astype(np.int32)
                    color = colors.get(d["object_name"], (100, 240, 130))
                    cv2.polylines(annotated, [polygon], True, color, 2, cv2.LINE_AA)
                    label = f'{d["object_name"]} {d["confidence"]:.0%}'
                    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, .65, 2)
                    x = max(0, min(int(polygon[:, 0].min()), frame.shape[1] - tw - 10))
                    y = max(th + 12, min(int(polygon[:, 1].min()), frame.shape[0] - 5))
                    cv2.rectangle(annotated, (x, y - th - 10), (x + tw + 10, y + 3), (24, 24, 24), -1)
                    cv2.putText(annotated, label, (x + 5, y - 3),
                                cv2.FONT_HERSHEY_SIMPLEX, .65, color, 2, cv2.LINE_AA)
                encoded = {}
                for view, picture in (("raw", raw), ("detect", annotated)):
                    ok, jpeg = cv2.imencode(".jpg", picture, [cv2.IMWRITE_JPEG_QUALITY, 85])
                    if not ok:
                        raise RuntimeError("画面编码失败")
                    encoded[view] = jpeg.tobytes()
                now = time.monotonic()
                metrics = {"fps": round(1.0 / (now - previous), 1) if previous else 0.0,
                           "inference_ms": round(elapsed, 1), "width": frame.shape[1],
                           "height": frame.shape[0], "detections": detections,
                           "confidence_threshold": self.detector.cfg.yolo_conf,
                           "updated_at": time.time()}
                previous = now
                with self.changed:
                    self.jpeg = encoded
                    self.updated, self.error, self.metrics = now, error, metrics
                    self.sequence += 1
                    self.changed.notify_all()
            except Exception as exc:
                with self.changed:
                    self.error = str(exc)
                log.warning("preview: %s", exc)
            self.stop.wait(max(0.05, self.period - (time.monotonic() - started)))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_body(self, body, content_type, code=200):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            self.handle_preview()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass

    def handle_preview(self):
        parsed = urlparse(self.path)
        state = self.server.preview
        if parsed.path == "/":
            self.send_body((ROOT / "scripts/live_preview.html").read_bytes(), "text/html; charset=utf-8")
        elif parsed.path == "/resources.js":
            self.send_body((ROOT / "scripts/live_resources.js").read_bytes(), "text/javascript; charset=utf-8")
        elif parsed.path in ("/resources", "/resources/history", "/resources/export.csv"):
            route = {"/resources": "/status", "/resources/history": "/history",
                     "/resources/export.csv": "/export.csv"}[parsed.path]
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                url = "http://127.0.0.1:8771" + route + ("?" + parsed.query if parsed.query else "")
                with opener.open(url, timeout=3) as response:
                    self.send_body(response.read(), response.headers.get("Content-Type", "application/json"))
            except Exception:
                self.send_body(json.dumps({"ok": False, "error": "资源监控连接暂时中断"}, ensure_ascii=False).encode(),
                               "application/json; charset=utf-8", 503)
        elif parsed.path in ("/status", "/health"):
            self.send_body(json.dumps(state.status(), ensure_ascii=False).encode(), "application/json; charset=utf-8")
        elif parsed.path in ("/stream.mjpg", "/snapshot.jpg"):
            view = parse_qs(parsed.query).get("view", ["detect"])[0]
            if view not in ("raw", "detect"):
                self.send_error(400)
                return
            with state.changed:
                if not state.jpeg:
                    state.changed.wait_for(lambda: state.jpeg or state.stop.is_set(), timeout=5)
                jpeg = state.jpeg.get(view)
            if jpeg is None:
                self.send_error(503, "Waiting for camera")
                return
            if parsed.path == "/snapshot.jpg":
                if not state.status()["ok"]:
                    self.send_error(503, "Preview is not current")
                    return
                self.send_body(jpeg, "image/jpeg")
                return
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.connection.settimeout(10)
            last = -1
            while not state.stop.is_set():
                with state.changed:
                    state.changed.wait_for(lambda: state.sequence != last or state.stop.is_set(), timeout=2)
                    if state.sequence == last or state.stop.is_set():
                        continue
                    last, jpeg = state.sequence, state.jpeg[view]
                header = f"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n"
                self.wfile.write(header.encode() + jpeg + b"\r\n")
                self.wfile.flush()
        else:
            self.send_error(404)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--fps", type=float, default=3.0)
    args = parser.parse_args()
    if not 0 < args.fps <= 10:
        parser.error("fps must be in (0, 10]")
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")
    cv2.setNumThreads(2)
    manifest = yaml.safe_load((ROOT / "deploy/piper/manifest.local.yaml").read_text())
    cfg = next(s["config"] for s in manifest["skill"] if s["name"] == "pick")
    with urllib.request.urlopen("http://127.0.0.1:8770/health", timeout=5) as response:
        if json.load(response).get("device") != "rk3588-npu":
            raise RuntimeError("RK3588 NPU detector is unavailable")
    with ATLAS.connect_capability(consumer_id="robonix_yolo/preview", provider_id="orbbec_camera",
            contract_id="robonix/primitive/camera/rgb", transport="ros2") as camera:
        frames = CameraFrames.from_cfg(cfg)
        frames.set_topic(camera.endpoint, camera.params.qos_profile)
        rclpy.init()
        node = rclpy.create_node("robonix_yolo_live_preview")
        state = Preview(frames, cfg, args.fps)
        threads = []
        def spin():
            while not state.stop.is_set():
                rclpy.spin_once(node, timeout_sec=.1)
        def interrupt(*_):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, interrupt)
        signal.signal(signal.SIGINT, interrupt)
        try:
            if not frames.attach(node):
                raise RuntimeError("Could not subscribe to Robonix camera")
            with ThreadingHTTPServer((args.host, args.port), Handler) as server:
                server.preview = state
                for target in (spin, state.run):
                    thread = threading.Thread(target=target, daemon=True)
                    thread.start()
                    threads.append(thread)
                print(f"Live NPU preview: http://{args.host}:{args.port}", flush=True)
                server.serve_forever(poll_interval=.25)
        except KeyboardInterrupt:
            pass
        finally:
            state.stop.set()
            with state.changed:
                state.changed.notify_all()
            for thread in threads:
                thread.join(timeout=4)
            node.destroy_node()
            rclpy.shutdown()


if __name__ == "__main__":
    main()
