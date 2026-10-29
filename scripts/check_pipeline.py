#!/usr/bin/env python3
"""Read live Robonix camera/arm data and exercise the original NPU detection path.

Run in the deployment container. This check publishes no arm commands.
"""
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "packages/skills/skill-pick-vertical-grasp-piper-rbnx"
sys.path[:0] = [str(PKG), str(PKG / "rbnx-build/codegen/proto_gen"),
                str(PKG / "rbnx-build/codegen/robonix_mcp_types"),
                "/opt/robonix/src/pylib/robonix-api"]
import cv2
import numpy as np
import yaml
import rclpy
from sensor_msgs.msg import JointState
from piper_msgs.msg import PiperStatusMsg
from robonix_api import ATLAS
from pick_skill.camera import CameraFrames
from pick_skill.detect import DetectConfig, Detector
from pick_skill.geometry import DropZone, Homography
from pick_skill.grpc_clients import Clients
from pick_skill.motion import qos_from_profile


def main():
    out = ROOT / "reports"
    out.mkdir(exist_ok=True)
    cfg = yaml.safe_load((ROOT / "deploy/piper/manifest.local.yaml").read_text())
    pick_cfg = next(x["config"] for x in cfg["skill"] if x["name"] == "pick")
    with urllib.request.urlopen("http://127.0.0.1:8770/health", timeout=5) as response:
        health = json.load(response)
    if health.get("device") != "rk3588-npu":
        raise RuntimeError("The configured detector is not the RK3588 NPU service")
    providers = {p.id: {"state": p.state.name, "detail": p.state_detail}
                 for p in ATLAS.query()}
    for name in ("orbbec_camera", "piper_ctl", "grasp_pose", "roboarm_ik", "llm_detect"):
        if providers.get(name, {}).get("state") != "ACTIVE":
            raise RuntimeError(f"Required Robonix provider is not ACTIVE: {name}")

    channels = ExitStack()
    def connect(provider, contract, transport):
        return channels.enter_context(ATLAS.connect_capability(
            consumer_id="robonix_yolo/check", provider_id=provider,
            contract_id=contract, transport=transport))

    camera = connect("orbbec_camera", "robonix/primitive/camera/rgb", "ros2")
    joints = connect("piper_ctl", "robonix/primitive/arm/joint_states", "ros2")
    status = connect("piper_ctl", "robonix/primitive/arm/arm_status", "ros2")
    frames = CameraFrames.from_cfg(pick_cfg)
    frames.set_topic(camera.endpoint, camera.params.qos_profile)
    rclpy.init()
    node = rclpy.create_node("robonix_yolo_check")
    feedback = {}
    counts = {"joints": 0, "status": 0}
    def joint_cb(msg):
        counts["joints"] += 1
        feedback["joints"] = {"name": list(msg.name), "position": list(msg.position),
                              "velocity": list(msg.velocity), "effort": list(msg.effort)}
    def status_cb(msg):
        counts["status"] += 1
        feedback["status"] = {key: getattr(msg, key) for key in msg.get_fields_and_field_types()}
    node.create_subscription(JointState, joints.endpoint, joint_cb,
                             qos_from_profile(joints.params.qos_profile))
    node.create_subscription(PiperStatusMsg, status.endpoint, status_cb,
                             qos_from_profile(status.params.qos_profile))
    if not frames.attach(node):
        raise RuntimeError("Camera subscription failed")
    stop = threading.Event()
    def spin():
        while not stop.is_set():
            rclpy.spin_once(node, timeout_sec=0.1)
    thread = threading.Thread(target=spin, daemon=True)
    thread.start()
    clients = Clients()
    try:
        started = time.monotonic()
        while time.monotonic() - started < 15:
            if frames._cb_count >= 5 and counts["joints"] >= 5 and counts["status"] >= 5:
                break
            time.sleep(0.1)
        if frames._cb_count < 5 or min(counts.values()) < 5:
            raise RuntimeError(f"Missing live data: images={frames._cb_count}, {counts}")
        raw, age = frames.latest()
        frame = frames.frame(max_age_s=3.0, wait_s=5.0)
        if frame is None or frame.shape[:2] != (720, 1280):
            raise RuntimeError("No fresh 1280x720 image matching the existing calibration")
        cv2.imwrite(str(out / "camera_raw.jpg"), raw)
        cv2.imwrite(str(out / "camera_undistorted.jpg"), frame)
        homography = Homography(pick_cfg["homography_matrix"])
        drop_zone = DropZone(pick_cfg["place_positions"]["tomato"],
                             pick_cfg["place_exclusion_radius_m"])
        detector = Detector(frames, homography, drop_zone, DetectConfig.from_cfg(pick_cfg))
        started_detect = time.monotonic()
        detections = detector._yolo_detect_all(frame)
        if detections is None:
            raise RuntimeError("Original pick skill could not call the NPU service")
        elapsed_ms = (time.monotonic() - started_detect) * 1000
        stable = detector._two_frame_consistency(frame, detections)
        grasp_results = []
        with ATLAS.connect_capability(consumer_id="robonix_yolo/check", provider_id="grasp_pose",
                contract_id="robonix/service/perception/grasp_pose/grasp_request", transport="grpc") as grasp:
            for d in stable:
                box = d["bbox_2d"]
                result = clients.grasp_request(grasp.endpoint, d["object_name"], box, [])
                grasp_results.append({"object_name": d["object_name"], "bbox_2d": box,
                    "arm_xy_m": homography.bbox_center_to_arm_xy(box),
                    "inside_drop_zone": drop_zone.bbox_inside(homography, box)[0],
                    "grasp_response": result})
        preview = frame.copy()
        for d in detections:
            u, v, w, h, angle = d["obb"]
            polygon = cv2.boxPoints(((u, v), (w, h), math.degrees(angle))).astype(np.int32)
            cv2.polylines(preview, [polygon], True, (0, 255, 0), 2)
            cv2.putText(preview, f'{d["object_name"]} {d["confidence"]:.2f}',
                        (max(0, int(u-w/2)), max(20, int(v-h/2))),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        cv2.imwrite(str(out / "camera_npu_detection.jpg"), preview)
        report = {"checked_at": datetime.now(timezone.utc).isoformat(),
                  "arm_motion_requested": False, "npu_health": health, "providers": providers,
                  "camera": {"topic": camera.endpoint, "shape": list(frame.shape),
                             "frames_received": frames._cb_count, "frame_age_s": age},
                  "arm": {"topic": joints.endpoint, "messages_received": counts, **feedback},
                  "pick_skill_http_ms": elapsed_ms, "detections": detections,
                  "two_frame_stable_detections": stable, "grasp_pose_results": grasp_results}
        (out / "live_pipeline.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        stop.set()
        thread.join(timeout=3)
        node.destroy_node()
        rclpy.shutdown()
        clients.close()
        channels.close()


if __name__ == "__main__":
    main()
