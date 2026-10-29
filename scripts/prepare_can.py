#!/usr/bin/env python3
"""Bring up this deployment's USB-CAN interface on the host."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "yolo_npu/runtime_deps"))
from ruamel.yaml import YAML


def main():
    manifest = YAML(typ="safe").load(ROOT / "deploy/piper/manifest.local.yaml")
    cfg = next(p["config"] for p in manifest["primitive"] if p["name"] == "piper_ctl")
    port = cfg["can_port"]
    usb_address = cfg.get("can_usb_address", "")
    if usb_address:
        matches = [p.name for p in Path("/sys/class/net").iterdir()
                   if (p / "type").read_text().strip() == "280"
                   and (p / "device").resolve().name == usb_address]
        if len(matches) != 1:
            raise RuntimeError(f"USB-CAN {usb_address}: expected one interface, found {matches}")
        port = matches[0]
    bitrate = int(cfg.get("can_bitrate", 1000000))
    info = json.loads(subprocess.check_output(["ip", "-j", "-details", "link", "show", port]))[0]
    if "UP" in info["flags"]:
        actual = info.get("linkinfo", {}).get("info_data", {}).get("bittiming", {}).get("bitrate")
        if actual != bitrate:
            raise RuntimeError(f"{port} is already UP at {actual} bps; expected {bitrate}")
    else:
        subprocess.run(["ip", "link", "set", port, "type", "can", "bitrate", str(bitrate)], check=True)
        subprocess.run(["ip", "link", "set", port, "up"], check=True)
    print(f"[robonix_yolo] {port} UP, bitrate={bitrate}")


if __name__ == "__main__":
    main()
