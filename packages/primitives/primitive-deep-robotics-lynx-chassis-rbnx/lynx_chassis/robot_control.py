from __future__ import annotations

import math
import threading
from numbers import Real
from typing import Any, Literal

from .udp_client import UDPClient


MotionCommand = tuple[float, float, float, float, float, float]
MotionControlType = Literal["Motion_Ctrl", "Real_Motion_Ctrl"]


class RobotControl:
    """山猫 Lynx UDP 控制器。"""

    def __init__(
        self,
        tar_ip: str,
        tar_port: int,
        timeout: float = 3.0,
        ctl_hz: float = 20.0,
    ) -> None:
        if ctl_hz <= 0.0:
            raise ValueError("ctl_hz must be positive")

        self.udp_client = UDPClient(
            tar_ip,
            tar_port,
            timeout=timeout,
        )
        self._ctl_hz = float(ctl_hz)

        # 指令和控制类型必须一起更新，避免发送线程读到不匹配的组合。
        self._mctl_lock = threading.Lock()
        self._mctl_cmd: MotionCommand | None = None
        self._mctl_type: MotionControlType | None = None

        self._mctl_stop_event = threading.Event()
        self._mctl_thread: threading.Thread | None = None

        # 同一时间只允许执行一个限时运动。
        self._exec_lock = threading.Lock()

        # close() 用它提前唤醒 move_for()。
        self._close_event = threading.Event()
        self._closed = False

    def _ensure_open(self) -> None:
        if self._closed or self._close_event.is_set():
            raise RuntimeError("RobotControl已关闭或正在关闭")

    @staticmethod
    def _make_command(
        x: float,
        y: float,
        z: float,
        roll: float,
        pitch: float,
        yaw: float,
    ) -> MotionCommand:
        values = (x, y, z, roll, pitch, yaw)

        for value in values:
            if not isinstance(value, Real):
                raise TypeError("运动控制量必须是实数")
            if not math.isfinite(float(value)):
                raise ValueError("运动控制量必须是有限数值")

        return tuple(float(value) for value in values)  # type: ignore[return-value]

    @staticmethod
    def _validate_control_type(mc_type: str) -> MotionControlType:
        if mc_type not in ("Motion_Ctrl", "Real_Motion_Ctrl"):
            raise ValueError(f"不支持的运动控制类型：{mc_type!r}")
        return mc_type

    def cur_state(self) -> None:
        """查询机器人当前状态。"""
        self._ensure_open()
        self.udp_client._send_udp("Current_State", {})

    def msc(self, mp: int) -> None:
        """切换运动状态。常用值：1站立、4趴下、16小车、17 RL。"""
        self._ensure_open()
        self.udp_client._send_udp(
            "Motion_Sta_Chge",
            {"MotionParam": int(mp)},
        )

    def mgc(self, gp: int) -> None:
        """切换步态。"""
        self._ensure_open()
        self.udp_client._send_udp(
            "Motion_Gait_Chge",
            {
                "GaitParam": int(gp),
                "ActionParam": 0,
            },
        )

    def _send_motion_once(
        self,
        command: MotionCommand,
        mc_type: MotionControlType,
    ) -> Any:
        x, y, z, roll, pitch, yaw = command
        return self.udp_client._send_udp(
            mc_type,
            {
                "X": x,
                "Y": y,
                "Z": z,
                "Roll": roll,
                "Pitch": pitch,
                "Yaw": yaw,
            },
        )

    def _motion_loop(self) -> None:
        """按 ctl_hz 周期发送当前控制指令。"""
        try:
            while not self._mctl_stop_event.is_set():
                with self._mctl_lock:
                    command = self._mctl_cmd
                    mc_type = self._mctl_type

                if command is None or mc_type is None:
                    break

                try:
                    self._send_motion_once(command, mc_type)
                except Exception as exc:
                    print(f"[motion] UDP发送失败：{exc}")

                if self._mctl_stop_event.wait(1.0 / self._ctl_hz):
                    break
        finally:
            print("[motion] 运动发送线程退出")

    def mctl(
        self,
        x: float,
        y: float,
        z: float = 0.0,
        roll: float = 0.0,
        pitch: float = 0.0,
        yaw: float = 0.0,
        mc_type: str = "Motion_Ctrl",
    ) -> None:
        """
        开始或更新周期运动指令。

        Motion_Ctrl 接收比例值；Real_Motion_Ctrl 接收真实控制值。
        具体范围由上层 main.py 按接口分别校验。
        """
        self._ensure_open()
        command = self._make_command(x, y, z, roll, pitch, yaw)
        control_type = self._validate_control_type(mc_type)

        if control_type == "Motion_Ctrl":
            self.mgc(gp=0x3002)
        elif control_type == "Real_Motion_Ctrl":
            self.mgc(gp=0x1001)

        with self._mctl_lock:
            self._mctl_cmd = command
            self._mctl_type = control_type

        if self._mctl_thread is not None and self._mctl_thread.is_alive():
            return

        self._mctl_stop_event.clear()
        self._mctl_thread = threading.Thread(
            target=self._motion_loop,
            name="lynx-motion-control",
            daemon=True,
        )
        self._mctl_thread.start()

    def _end_periodic_send(self) -> None:
        """只结束周期发送，不向机器人补发任何运动指令。"""
        self._mctl_stop_event.set()
        thread = self._mctl_thread

        if thread is not None and thread is not threading.current_thread():
            thread.join()

        self._mctl_thread = None
        with self._mctl_lock:
            self._mctl_cmd = None
            self._mctl_type = None

    def move_for(
        self,
        x: float,
        y: float,
        yaw: float,
        duration: float,
        *,
        mc_type: str,
    ) -> None:
        """在指定时间内周期发送运动指令，结束后仅停止发送。"""
        if not isinstance(duration, Real) or not math.isfinite(float(duration)):
            raise ValueError("duration必须是有限实数")
        if duration <= 0.0:
            raise ValueError("duration必须大于0")

        control_type = self._validate_control_type(mc_type)

        with self._exec_lock:
            self._ensure_open()
            self.mctl(
                x=x,
                y=y,
                yaw=yaw,
                mc_type=control_type,
            )
            try:
                self._close_event.wait(float(duration))
            finally:
                self._end_periodic_send()

    def close(self) -> None:
        """结束周期发送并关闭 UDP；不发送全零运动指令。"""
        if self._closed:
            return

        self._close_event.set()

        # 等待正在执行的 move_for() 在 finally 中结束发送。
        with self._exec_lock:
            if self._closed:
                return

            self._end_periodic_send()
            self._closed = True
            self.udp_client.close()

        print("[robot] RobotControl已关闭")
