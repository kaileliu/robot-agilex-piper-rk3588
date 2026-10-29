#!/usr/bin/env python3
# SPDX-License-Identifier: MulanPSL-2.0
"""Smoothly ramp the Piper arm back to home (all joints 0, gripper open).

VLA (pi05_piper) was trained starting from the home pose; if a rollout leaves
the arm mid-trajectory, the next execute starts out-of-distribution. Run this
between rollouts. Rate-limited (deg/step) so the arm eases home, never jumps.
"""
import time

import numpy as np
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import JointState

JOINT_CMD = "/arm/joint_command"
JOINT_STATE = "/arm/joint_states_single"
HOME = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float64)  # rad
HOME_GRIP = 0.0792                                                 # m (open)
MAX_STEP_RAD = np.deg2rad(3.0)                                     # per tick
DT = 0.033
VEL_PCT = 20.0


def main() -> int:
    rclpy.init()
    node = rclpy.create_node("home_arm")
    pub = node.create_publisher(JointState, JOINT_CMD, 10)

    cur = {"pos": None}

    def _cb(msg: JointState):
        cur["pos"] = np.array(msg.position[:6], dtype=np.float64)

    node.create_subscription(JointState, JOINT_STATE, _cb, 10)

    print("[home] waiting for joint_states ...", flush=True)
    t0 = time.time()
    while cur["pos"] is None and time.time() - t0 < 5.0:
        rclpy.spin_once(node, timeout_sec=0.1)
    if cur["pos"] is None:
        print("[home] ERR: no joint_states", flush=True)
        return 2

    start = cur["pos"].copy()
    print(f"[home] start={np.round(np.rad2deg(start),1)} deg -> home=0", flush=True)

    target = HOME.copy()
    q = start.copy()
    n = 0
    while rclpy.ok():
        delta = np.clip(target - q, -MAX_STEP_RAD, MAX_STEP_RAD)
        q = q + delta
        msg = JointState()
        msg.header.stamp = node.get_clock().now().to_msg()
        msg.name = ["joint1", "joint2", "joint3", "joint4", "joint5", "joint6", "gripper"]
        msg.position = [float(x) for x in q] + [HOME_GRIP]
        msg.velocity = [0.0] * 6 + [VEL_PCT]
        msg.effort = [0.0] * 7
        pub.publish(msg)
        n += 1
        if np.max(np.abs(target - q)) < np.deg2rad(0.5):
            break
        time.sleep(DT)

    print(f"[home] reached home in {n} ticks", flush=True)
    time.sleep(0.5)
    node.destroy_node()
    rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
