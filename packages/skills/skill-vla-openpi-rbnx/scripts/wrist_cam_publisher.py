#!/usr/bin/env python3
# SPDX-License-Identifier: MulanPSL-2.0
"""Minimal wrist-camera publisher for the VLA path.

The robonix RealSense primitive (realsense2_camera) isn't built in this
container, but the D435i color stream is a plain UVC device. This node opens it
via cv2/V4L2 (Intel by-id index0 = color) and republishes as sensor_msgs/Image
on /camera_435i/color/image_raw — the same topic the openpi VLA skill subscribes
to for observation/wrist_image. Matches the roboarm reference client, which also
grabs the Intel color stream through OpenCV.
"""
import glob
import sys
import time

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image

TOPIC = "/camera_435i/color/image_raw"
BY_ID_GLOB = "/dev/v4l/by-id/*RealSense*index0"
W, H, FPS = 640, 480, 30


def _find_device() -> str:
    hits = sorted(glob.glob(BY_ID_GLOB))
    if not hits:
        raise RuntimeError(f"no RealSense color device matched {BY_ID_GLOB}")
    return hits[0]


def main() -> int:
    dev = _find_device()
    cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"YUYV"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, H)
    cap.set(cv2.CAP_PROP_FPS, FPS)
    if not cap.isOpened():
        print(f"[wrist_cam] ERR: cannot open {dev}", file=sys.stderr)
        return 2

    rclpy.init()
    node = rclpy.create_node("wrist_cam_publisher")
    pub = node.create_publisher(Image, TOPIC, 10)
    print(f"[wrist_cam] publishing {dev} -> {TOPIC} at {W}x{H}", flush=True)

    n = 0
    try:
        while rclpy.ok():
            ok, frame = cap.read()
            if not ok or frame is None:
                time.sleep(0.01)
                continue
            if frame.shape[1] != W or frame.shape[0] != H:
                frame = cv2.resize(frame, (W, H))
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            msg = Image()
            msg.header.stamp = node.get_clock().now().to_msg()
            msg.header.frame_id = "camera_435i_color"
            msg.height, msg.width = H, W
            msg.encoding = "rgb8"
            msg.is_bigendian = 0
            msg.step = W * 3
            msg.data = np.ascontiguousarray(rgb, dtype=np.uint8).tobytes()
            pub.publish(msg)
            n += 1
            if n % 150 == 0:
                print(f"[wrist_cam] published {n} frames", flush=True)
    finally:
        cap.release()
        node.destroy_node()
        rclpy.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
