# Description: 找机械臂的 CAN 口——和臂原语一样的办法（本子树里只有这一份）。
#
# 臂原语 piper_ctl/main.py 的 _find_gs_usb_can()：USB-CAN 适配器（gs_usb）的网卡在
# sysfs 里指向 USB 设备，而 Jetson 板载的 mttcan 指向 platform/...；接口名开机后可能
# 会漂（can0/can4/...），所以按驱动拓扑认口，而不是写死一个名字。
#
# 这里只做**只读探测**：口没起来（DOWN）时打印提示，不替你去改接口状态
# （原语在 boot 时会尽力拉起，别的进程可能正在用它，工具不该动）。
import glob
import os
import subprocess


def list_can_interfaces() -> list[str]:
    """当前系统里所有 CAN 口（含板载 mttcan），已排序。"""
    return sorted(os.path.basename(p) for p in glob.glob("/sys/class/net/can*"))


def find_usb_can() -> str | None:
    """USB-CAN 适配器的接口名（如 "can4"）；找不到返回 None。"""
    for path in sorted(glob.glob("/sys/class/net/can*")):
        try:
            dev = os.path.realpath(os.path.join(path, "device"))
        except OSError:
            continue
        if "usb" in dev.lower():
            return os.path.basename(path)
    return None


def is_up(iface: str) -> bool:
    """接口是否已 UP（读 sysfs，不需要权限）。"""
    try:
        with open(f"/sys/class/net/{iface}/operstate", encoding="utf-8") as f:
            return f.read().strip() in ("up", "unknown")
    except OSError:
        return False


def bitrate_of(iface: str) -> str:
    """接口当前波特率（读不出来就返回空）——只看，不改。"""
    try:
        out = subprocess.run(["ip", "-details", "link", "show", iface],
                             capture_output=True, text=True, timeout=5.0)
    except Exception:
        return ""
    for token in out.stdout.split():
        if token.isdigit() and len(token) >= 5:   # 1000000 这类
            return token
    return ""


def resolve_arm_port(configured: str | None = None, verbose: bool = True) -> str:
    """定下用哪个 CAN 口。

    顺序是有意的：**先自动探测**（同臂原语），探测不到才退回配置值——接口名会漂，
    配置里那个名字多半是上一次开机抄的。

    Args:
        configured: 配置里的 `arm_port`（可为空/None）。
        verbose: 是否打印探测结果与提示。

    Returns:
        接口名，例如 "can4"。

    Raises:
        RuntimeError: 既没探测到 USB-CAN，配置里也没给口名。
    """
    detected = find_usb_can()
    if detected:
        if verbose:
            if configured and configured != detected:
                print(f"CAN 口自动探测：用 {detected}（配置里写的是 {configured}，以探测结果为准）", flush=True)
            else:
                print(f"CAN 口自动探测：用 {detected}", flush=True)
            if not is_up(detected):
                print(f"⚠️ {detected} 当前是 DOWN——先把它起起来再用（工具不替你改接口状态）："
                      f"\n    sudo ip link set {detected} type can bitrate 1000000 && sudo ip link set {detected} up",
                      flush=True)
        return detected

    if configured:
        if verbose:
            print(f"没探测到 USB-CAN（gs_usb），退回配置里的 arm_port={configured}；"
                  f"现有 CAN 口: {list_can_interfaces() or '无'}", flush=True)
        return configured

    raise RuntimeError(
        "没找到 USB-CAN 接口，配置里也没写 arm_port。"
        f"现有 CAN 口: {list_can_interfaces() or '无'}（板载 mttcan 不算，要的是 USB-CAN 适配器）"
    )
