#!/usr/bin/env python3
"""Capture one frame from each VLA camera topic to PNG for inspection."""
import time

import numpy as np
import rclpy
from PIL import Image as PIL
from sensor_msgs.msg import Image


def decode(msg):
    h, w = msg.height, msg.width
    enc = msg.encoding.lower()
    raw = np.frombuffer(bytes(msg.data), dtype=np.uint8)
    if enc == "bgr8":
        return raw.reshape((h, w, 3))[:, :, ::-1].copy()
    return raw.reshape((h, w, 3)).copy()


def main():
    rclpy.init()
    n = rclpy.create_node("grab")
    got = {}

    def mk(k):
        def cb(m):
            if k not in got:
                got[k] = decode(m)
                print(k, "enc=", m.encoding, "shape=", got[k].shape,
                      "mean_rgb=", got[k].reshape(-1, 3).mean(0).round(1))
        return cb

    n.create_subscription(Image, "/camera/color/image_raw", mk("full"), 10)
    n.create_subscription(Image, "/camera_435i/color/image_raw", mk("wrist"), 10)
    t0 = time.time()
    while (len(got) < 2) and time.time() - t0 < 8:
        rclpy.spin_once(n, timeout_sec=0.1)
    for k, v in got.items():
        PIL.fromarray(v).save(f"/tmp/cam_{k}.png")
    print("saved:", list(got.keys()))


if __name__ == "__main__":
    main()
