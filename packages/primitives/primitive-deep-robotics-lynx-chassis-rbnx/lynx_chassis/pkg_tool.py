import struct
import json
from datetime import datetime
from typing import Any

HEADER_STRUCT = struct.Struct("<4sHHBBB5s")
HEADER_SIZE = HEADER_STRUCT.size
SYNC = b"\xEB\x91\xEB\x90"
VERSION = 1
JSON_FMT = 0x01
XML_FMT = 0x00
RESERVED = b"\x00" * 5
TWO_BYTES_MAX = 65535

INST_TYPE_CMD_MAP = {
    "Heart_Beat": (0x00100064 ,0x00000005),
    "Motion_Sta_Chge": (0x00100001,0x00200002),
    "Motion_Gait_Chge": (0x00100001, 0x00300002),
    "Motion_Ctrl": (0x00100001,0x00100002),
    "Real_Motion_Ctrl": (0x00100001, 0x00110002),
    "Current_State": (0x00100064,0x00f00000),
}

MOTION_STATE_NAMES = {
    -2: "软急停",
    0: "默认状态",
    1: "站立状态",
    2: "关节阻尼状态",
    3: "开机阻尼状态",
    4: "趴下状态",
    5: "标零状态",
    16: "小车移动状态",
    17: "RL控制模式",
    0x1001: "阻尼趴下状态",
}


def parse_int(value: Any) -> int:
    """
    支持：
        16
        "16"
        "0x10"
        "0x0010 0064"
    """
    if isinstance(value, bool):
        return int(value)

    if isinstance(value, int):
        return value

    if isinstance(value, str):
        text = value.strip().replace(" ", "")

        if not text:
            raise ValueError("整数值为空")

        return int(text, 0)

    raise TypeError(
        f"无法把 {type(value).__name__} 转换成整数"
    )


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        return value != 0

    if isinstance(value, str):
        text = value.strip().lower()

        if text in {"true", "1", "yes"}:
            return True

        if text in {"false", "0", "no"}:
            return False

    raise ValueError(f"无法解析布尔值：{value!r}")

def build_packet(payload: bytes, fmt_type: int, packet_id: int) -> bytes:
    payload_len = len(payload)
    header = HEADER_STRUCT.pack(
        SYNC,
        payload_len,
        packet_id,
        fmt_type,
        0, # 当前包编号，0-255
        VERSION,
        RESERVED,
    )
    return header + payload

def build_json_pl(items: dict[str, Any], inst: str) -> bytes:
    inst_type, inst_cmd = INST_TYPE_CMD_MAP[inst]
    """ JSON体格式：
    {
        "PatrolDevice": {
            "Type": ...,
            "Command": ...,
            "Time": ...,
            "Items": {...}
        }
    }"""
    body = {
        "PatrolDevice": {
            "Type": inst_type,
            "Command": inst_cmd,
            "Time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Items": items,
        }
    }
    json_text = json.dumps(
        body,
        ensure_ascii=False,
        separators=(",",":"),
    )
    return json_text.encode("utf-8")

def parse_apdu(packet: bytes) -> dict[str, Any]:
    """
    解析山猫 UDP APDU：

        16 字节协议头 + JSON/XML ASDU

    当前仅处理 JSON ASDU。
    """
    if not isinstance(packet, bytes):
        raise TypeError(
            f"packet 必须是 bytes，实际为 {type(packet).__name__}"
        )

    if len(packet) < HEADER_SIZE:
        raise ValueError(
            f"UDP 报文过短：{len(packet)} 字节，"
            f"至少需要 {HEADER_SIZE} 字节"
        )

    (
        sync,
        asdu_length,
        message_id,
        asdu_format,
        packet_index,
        version,
        reserved,
    ) = HEADER_STRUCT.unpack_from(packet)

    if sync != SYNC:
        raise ValueError(
            "同步字符错误："
            f"expected={SYNC.hex(' ')}, "
            f"actual={sync.hex(' ')}"
        )

    asdu_start = HEADER_SIZE
    asdu_end = asdu_start + asdu_length

    if len(packet) < asdu_end:
        raise ValueError(
            "UDP 报文长度不足："
            f"头部声明 ASDU 长度={asdu_length}，"
            f"实际剩余={len(packet) - HEADER_SIZE}"
        )

    asdu_bytes = packet[
        asdu_start:asdu_end
    ]

    print(
        "[UDP] "
        f"id={message_id} "
        f"format={asdu_format} "
        f"packet={packet_index} "
        f"version={version} "
        f"asdu_len={asdu_length}"
    )

    if asdu_format == JSON_FMT:
        try:
            # utf-8-sig 同时兼容可能存在的 UTF-8 BOM。
            asdu_text = asdu_bytes.decode(
                "utf-8-sig"
            )
        except UnicodeDecodeError as exc:
            raise ValueError(
                "协议头解析成功，但 ASDU 不是合法 UTF-8。"
                f"ASDU 前32字节："
                f"{asdu_bytes[:32].hex(' ')}"
            ) from exc

        try:
            data = json.loads(asdu_text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "ASDU 不是合法 JSON："
                f"{asdu_text[:200]!r}"
            ) from exc

        if not isinstance(data, dict):
            raise ValueError(
                "JSON ASDU 顶层必须是对象"
            )

        return data

    if asdu_format == XML_FMT:
        raise NotImplementedError(
            "当前收到 XML ASDU，暂未实现 XML 解析"
        )

    raise ValueError(
        f"未知 ASDU 格式：0x{asdu_format:02x}"
    )


def hex_dump(data: bytes, width: int = 16) -> str:
    """
    以类似抓包工具的格式输出：
    偏移量 + 十六进制 + ASCII。
    """
    lines: list[str] = []

    for offset in range(0, len(data), width):
        chunk = data[offset:offset + width]

        hex_part = " ".join(f"{byte:02x}" for byte in chunk)
        hex_part = hex_part.ljust(width * 3 - 1)

        ascii_part = "".join(
            chr(byte) if 32 <= byte <= 126 else "."
            for byte in chunk
        )

        lines.append(
            f"{offset:04x}  {hex_part}  |{ascii_part}|"
        )

    return "\n".join(lines)


def print_field(
    name: str,
    offset: int,
    raw: bytes,
    value: Any,
    description: str = "",
) -> None:
    """输出一个包头字段。"""
    end_offset = offset + len(raw) - 1
    offset_text = (
        f"{offset}"
        if offset == end_offset
        else f"{offset}-{end_offset}"
    )

    raw_text = raw.hex(" ")

    print(
        f"{offset_text:<7} "
        f"{name:<14} "
        f"{len(raw):<4} "
        f"{raw_text:<18} "
        f"{str(value):<20} "
        f"{description}"
    )


def inspect_packet(packet: bytes) -> None:
    """
    解析并以便于人工检查的形式打印完整报文。
    """
    print("=" * 90)
    print("报文检查")
    print("=" * 90)

    if len(packet) < HEADER_STRUCT.size:
        print(
            f"错误：完整报文只有 {len(packet)} 字节，"
            f"小于固定包头长度 {HEADER_STRUCT.size} 字节"
        )
        return

    header = packet[:HEADER_STRUCT.size]

    (
        sync,
        payload_len,
        packet_id,
        fmt_type,
        packet_index,
        version,
        reserved,
    ) = HEADER_STRUCT.unpack(header)

    payload = packet[HEADER_STRUCT.size:]
    expected_total_len = HEADER_STRUCT.size + payload_len

    print(f"固定包头长度：{HEADER_STRUCT.size} 字节")
    print(f"包头声明的 ASDU 长度：{payload_len} 字节")
    print(f"实际 ASDU 长度：{len(payload)} 字节")
    print(f"期望完整报文长度：{expected_total_len} 字节")
    print(f"实际完整报文长度：{len(packet)} 字节")

    if len(packet) == expected_total_len:
        print("长度检查：正确")
    else:
        print("长度检查：错误")

    print()
    print("一、包头字段")
    print("-" * 90)
    print(
        f"{'偏移':<7} "
        f"{'字段':<14} "
        f"{'长度':<4} "
        f"{'原始字节':<18} "
        f"{'解析值':<20} "
        f"说明"
    )
    print("-" * 90)

    print_field(
        name="同步字符",
        offset=0,
        raw=header[0:4],
        value=sync.hex(" "),
        description=(
            "正确"
            if sync == SYNC
            else f"错误，期望 {SYNC.hex(' ')}"
        ),
    )

    print_field(
        name="ASDU长度",
        offset=4,
        raw=header[4:6],
        value=payload_len,
        description=(
            f"小端整数，0x{payload_len:04x}"
        ),
    )

    print_field(
        name="报文ID",
        offset=6,
        raw=header[6:8],
        value=packet_id,
        description=(
            f"小端整数，0x{packet_id:04x}"
        ),
    )

    fmt_description = {
        XML_FMT: "XML",
        JSON_FMT: "JSON",
    }.get(fmt_type, "未知格式")

    print_field(
        name="ASDU格式",
        offset=8,
        raw=header[8:9],
        value=f"0x{fmt_type:02x}",
        description=fmt_description,
    )

    print_field(
        name="当前包编号",
        offset=9,
        raw=header[9:10],
        value=packet_index,
        description="从0开始",
    )

    print_field(
        name="协议版本",
        offset=10,
        raw=header[10:11],
        value=f"0x{version:02x}",
        description=(
            "正确"
            if version == VERSION
            else f"与本地版本 {VERSION} 不一致"
        ),
    )

    print_field(
        name="预留字段",
        offset=11,
        raw=header[11:16],
        value=reserved.hex(" "),
        description=(
            "全部为0"
            if reserved == RESERVED
            else "存在非0字节"
        ),
    )

    print()
    print("二、16字节包头")
    print("-" * 90)
    print(header.hex(" "))

    print()
    print("按字段分组：")
    print(
        f"{header[0:4].hex(' ')}  | "
        f"{header[4:6].hex(' ')}  | "
        f"{header[6:8].hex(' ')}  | "
        f"{header[8:9].hex(' ')}  | "
        f"{header[9:10].hex(' ')}  | "
        f"{header[10:11].hex(' ')}  | "
        f"{header[11:16].hex(' ')}"
    )

    print(
        "SYNC         | LEN   | ID    | FMT | IDX | VER | RESERVED"
    )

    print()
    print("三、ASDU协议体")
    print("-" * 90)

    if fmt_type == JSON_FMT:
        try:
            json_text = payload.decode("utf-8")
            json_obj = json.loads(json_text)

            print("UTF-8 JSON：")
            print(
                json.dumps(
                    json_obj,
                    ensure_ascii=False,
                    indent=2,
                )
            )
        except UnicodeDecodeError as exc:
            print(f"JSON UTF-8 解码失败：{exc}")
            print(payload.hex(" "))
        except json.JSONDecodeError as exc:
            print(f"JSON 格式解析失败：{exc}")
            print("原始文本：")
            print(payload.decode("utf-8", errors="replace"))

    elif fmt_type == XML_FMT:
        print(payload.decode("utf-8", errors="replace"))

    else:
        print("未知 ASDU 格式，输出原始十六进制：")
        print(payload.hex(" "))

    print()
    print("四、完整报文十六进制")
    print("-" * 90)
    print(packet.hex(" "))

    print()
    print("五、完整报文 Hex Dump")
    print("-" * 90)
    print(hex_dump(packet))

    print("=" * 90)