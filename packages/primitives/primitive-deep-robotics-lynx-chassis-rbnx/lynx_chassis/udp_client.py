import socket
import threading
import json
from pathlib import Path
from typing import Any
from .pkg_tool import *

LOG_FILE_MAP = {
    (0x00100064, 0x00F00000): "basic_status.jsonl",
    (0x00100001, 0x00F00000): "motion_status.jsonl",
    (0x00100002, 0x00F00000): "device_status.jsonl",
    (0x0010007F, 0x00F00000): "error_status.jsonl",
}

"""
和机器人通信的UDP客户端。由于状态信息的存在，UDPClient在发送UDP时并不会
处理其响应。由于每个响应的Type+Command组合是唯一确定的，所以会专门开一个线程
去接收UDP的响应。
"""
class UDPClient:
    def __init__(
        self, 
        ip: str, 
        port: int, 
        local_ip: str = "0.0.0.0", 
        local_port: int = 0, 
        timeout: float = 0.5, 
        log_dir: str = "./udp_logs/"
        ):
        self.ip = ip
        self.port = port
        self.timeout = timeout
        self.packet_id = 0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((local_ip, local_port))
        self.sock.settimeout(timeout)
        self._send_lock = threading.Lock()

        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True,exist_ok=True)

        self._recv_stop_ent = threading.Event()
        self._recv_thd = threading.Thread(target=self._recv_loop, daemon=True)

        self._recv_thd.start()
        print(
            "[UDP] 客户端启动："
            f"local={self.sock.getsockname()}, "
            f"target={self.ip}:{self.port}"
        )

    def _send_udp_packet(
        self,
        packet: bytes,
    ) -> None:
        with self._send_lock:
            sent_size = self.sock.sendto(
                packet,
                (self.ip, self.port),
            )

    def _send_udp(
        self,
        inst_type: str,
        items: dict[str, Any],
    ) -> None:
        payload = build_json_pl(items, inst_type)
        packet = build_packet(payload, JSON_FMT, self.packet_id)
        # print(packet)
        self.packet_id = (self.packet_id + 1) % TWO_BYTES_MAX
        self._send_udp_packet(packet)

    def decode_udp_response(
    
        self,
        response: Any,
    ) -> dict[str, Any]:
        """
        将 UDPClient 返回值转换成已经解析的 JSON 字典。

        支持：
            bytes
            bytearray
            (bytes, address)
            已解析的 dict
        """
        
        # socket.recvfrom() 可能返回：
        #     (packet, address)
        if isinstance(response, tuple):
            if not response:
                raise ValueError("UDP 响应元组为空")
            response = response[0]
        if response is None:
            return None
        if isinstance(response, dict):
            return response
        if isinstance(response, bytearray):
            response = bytes(response)
        if isinstance(response, bytes):
            return parse_apdu(response)
        # 仅兼容 UDPClient 已经剥离协议头并返回纯 JSON 字符串的情况。
        if isinstance(response, str):
            try:
                data = json.loads(response)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"响应字符串不是合法 JSON：{response[:200]!r}"
                ) from exc

            if not isinstance(data, dict):
                raise ValueError(
                    "响应 JSON 顶层必须是对象"
                )
            return data
        raise TypeError(
            "不支持的 UDP 响应类型："
            f"{type(response).__name__}"
        )

    def _recv_loop(self) -> None:
        print("[UDP] 接收线程启动")

        while not self._recv_stop_ent.is_set():
            try:
                packet, address = (
                    self.sock.recvfrom(65535)
                )

            except socket.timeout:
                continue

            except OSError:
                # close() 关闭 socket 后可能进入这里。
                if self._recv_stop_ent.is_set():
                    break

                raise

            try:
                data = self.decode_udp_response(
                    packet
                )

                if data is None:
                    continue

                self._write_response_log(
                    data=data,
                    address=address,
                )

            except Exception as exc:
                print(
                    f"[UDP] 接收报文解析失败：{exc}"
                )

                self._write_decode_error(
                    packet=packet,
                    address=address,
                    error=exc,
                )

        print("[UDP] 接收线程退出")

    def _get_msg_key(self, data: dict[str, Any])->tuple[int,int]:
        patrol_dev = data.get("PatrolDevice")
        if not isinstance(patrol_dev, dict):
            raise ValueError("UDP 响应中缺少 PatrolDevice 字段或其不是对象")

        msg_type = parse_int(patrol_dev.get("Type"))
        cmd = parse_int(patrol_dev.get("Command"))
        return msg_type, cmd

    def _get_log_path(self, msg_type:int, cmd:int)->Path:
        filename = LOG_FILE_MAP.get((msg_type, cmd))
        if filename is None:
            filename = f"type_{msg_type:08X}_cmd_{cmd:08X}.log"
        return self.log_dir / filename

    def _write_response_log(
        self,
        data: dict[str, Any],
        address: tuple[str, int],
    ) -> None:
        msg_type, command = (self._get_msg_key(data))
        log_path = self._get_log_path(msg_type,command)

        record = {
            "receive_time": (
                datetime.now().isoformat(
                    timespec="milliseconds"
                )
            ),
            "source": (
                f"{address[0]}:{address[1]}"
            ),
            "type": (
                f"0x{msg_type:08x}"
            ),
            "command": (
                f"0x{command:08x}"
            ),
            "data": data,
        }

        # JSONL：每个 UDP 报文占一行。
        #
        # 这样即使运控状态达到 10 Hz，
        # 日志仍然容易追加和后续解析。
        with log_path.open(
            mode="a",
            encoding="utf-8",
        ) as file:
            file.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                )
            )
            file.write("\n")

    def _write_decode_error(
        self,
        packet: bytes,
        address: tuple[str, int],
        error: Exception,
    ) -> None:
        """
        无法解析的报文单独保存，方便检查协议头或加密问题。
        """
        log_path = (
            self.log_dir
            / "decode_error.jsonl"
        )

        record = {
            "receive_time": (
                datetime.now().isoformat(
                    timespec="milliseconds"
                )
            ),
            "source": (
                f"{address[0]}:{address[1]}"
            ),
            "error": str(error),
            "packet_length": len(packet),
            "packet_hex": packet.hex(" "),
        }

        with log_path.open(
            mode="a",
            encoding="utf-8",
        ) as file:
            file.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                )
            )
            file.write("\n")

    # ========================================================
    # 关闭
    # ========================================================

    def close(self) -> None:
        self._recv_stop_ent.set()

        try:
            self.sock.close()
        except OSError:
            pass

        if (
            self._recv_thd.is_alive()
            and self._recv_thd
            is not threading.current_thread()
        ):
            self._recv_thd.join(
                timeout=1.5
            )

        print("[UDP] 客户端已关闭")