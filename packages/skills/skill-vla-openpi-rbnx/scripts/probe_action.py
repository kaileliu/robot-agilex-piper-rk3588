#!/usr/bin/env python3
"""Read-only: capture current obs (like the skill), run ONE openpi inference,
dump the raw 50x7 action chunk. Does NOT command the arm. Distinguishes
'policy never commands grasp' (obs problem) vs 'policy commands grasp but
pipeline/scale drops it' (action problem)."""
import time
import numpy as np
import rclpy
from sensor_msgs.msg import Image, JointState
import websockets.sync.client as wsc
from openpi_client import msgpack_numpy

DEG = 57.29577951308232
FULL = "/camera/color/image_raw"
WRIST = "/camera_435i/color/image_raw"
JS = "/arm/joint_states_single"
GRIP_M_TO_0TO1 = 1.0 / 0.0995  # corrected: divide readback by real full-open (0.0995 m)


def resize(img):
    from PIL import Image as P
    return np.asarray(P.fromarray(img).resize((640, 480)), np.uint8)


def decode(m):
    a = np.frombuffer(bytes(m.data), np.uint8).reshape((m.height, m.width, 3))
    return a[:, :, ::-1].copy() if m.encoding.lower() == "bgr8" else a.copy()


def main():
    rclpy.init()
    n = rclpy.create_node("probe")
    g = {}
    n.create_subscription(Image, FULL, lambda m: g.__setitem__("f", decode(m)), 10)
    n.create_subscription(Image, WRIST, lambda m: g.__setitem__("w", decode(m)), 10)
    n.create_subscription(JointState, JS, lambda m: g.__setitem__("j", list(m.position)), 10)
    t0 = time.time()
    while len(g) < 3 and time.time() - t0 < 8:
        rclpy.spin_once(n, timeout_sec=0.1)
    if len(g) < 3:
        print("missing obs:", list(g.keys())); return
    pos = g["j"]
    grip_m = float(pos[6]) if len(pos) >= 7 else 0.0
    grip_0to1 = float(np.clip(grip_m * GRIP_M_TO_0TO1, 0.0, 1.0))
    state = np.array([p * DEG for p in pos[:6]] + [grip_0to1 * 100.0], np.float32)
    print("STATE fed to policy: joints_deg=", np.round(state[:6], 1),
          " gripper_raw_m=", round(grip_m, 4), " -> state[6]=", round(float(state[6]), 1))
    obs = {"observation/image": resize(g["f"]),
           "observation/wrist_image": resize(g["w"]),
           "observation/state": state,
           "prompt": "pick the potato toy and place into box"}
    packer = msgpack_numpy.Packer()
    with wsc.connect("ws://127.0.0.1:8002", ping_interval=None, open_timeout=30, max_size=None) as ws:
        ws.recv()
        ws.send(packer.pack(obs)); resp = ws.recv()
    a = np.asarray(msgpack_numpy.unpackb(resp)["actions"], np.float32)
    print("action chunk shape:", a.shape)
    print("--- gripper column (action[:,6]) across 50 steps ---")
    print(np.round(a[:, 6], 1))
    print("gripper: first=%.1f last=%.1f min=%.1f max=%.1f" %
          (a[0, 6], a[-1, 6], a[:, 6].min(), a[:, 6].max()))
    print("--- joint deltas step0 -> step49 (deg) ---")
    print("j1..j6 first:", np.round(a[0, :6], 1))
    print("j1..j6 last :", np.round(a[-1, :6], 1))
    print("delta       :", np.round(a[-1, :6] - a[0, :6], 1))
    n.destroy_node(); rclpy.shutdown()


if __name__ == "__main__":
    main()
