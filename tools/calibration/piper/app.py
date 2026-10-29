#!/usr/bin/env python3
"""piper 标定台：一个 Flask 服务，把标定流程的每一步挂成一页（宿主上跑，不用 ROS）。

    cd tools/calibration/piper
    uv venv --python 3.10 .venv && uv pip install -r requirements.txt
    .venv/bin/python app.py --port 8090
    # 浏览器打开 http://<本机IP>:8090

页面：
    /            总览：每一步的产物状态，从这里进各个步骤
    /intrinsics  相机内参（棋盘格；画面是原始的，不去畸变）
    /handeye     手眼标定（2D 单应；画面已按内参去畸变）
    /verify      验证：点一个点，让臂尖移过去，肉眼看对不对

两条入口都支持：先标内参再标手眼；或者已有内参（本子树 artifacts/，或从清单里那份
现成的抄过来）时直接标手眼——不管哪条，手眼用的画面都是去畸变后的，与运行时一致。

臂这边与 S10 共用同一套代码（common/robot/）：直连 CAN，robonix 栈不用起。
"""
import argparse
import os
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent          # tools/calibration/piper
TOOLS = ROOT.parent                             # tools/calibration（common/ 在这下面）
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(ROOT))

from flask import Flask, jsonify, redirect, request  # noqa: E402

import paths  # noqa: E402
from common.rig import Rig  # noqa: E402
from common.ui import card_html, page  # noqa: E402
from config import get_config_value  # noqa: E402
from steps import STEP_CLASSES  # noqa: E402
from vision.camera import OrbCamera  # noqa: E402

app = Flask(__name__)
app.site_name = "piper 标定台"
# 相机由本部署注入（piper = Orbbec / pyorbbecsdk）；CAN 口留空则自动探测（同臂原语）
rig = Rig(camera_factory=OrbCamera, paths=paths,
          port=get_config_value("arm_port", None, raise_if_missing=False) or None)
STEPS = [cls() for cls in STEP_CLASSES]

for _step in STEPS:
    _step.mount(app, rig, STEPS)


def _step_by_owner(owner: str):
    return next((s for s in STEPS if s.name == owner), None)


@app.get("/")
def home():
    rig_status = rig.status()
    cards = [card_html(step) for step in STEPS]
    head = (
        f'<h1>piper 标定台</h1><div class="desc">当前为标定模式，聊天抓取与检测预览已暂停。'
        f"已导入当前相机的出厂内参。请先进入「手眼」采集 9 个拟合点和 3 个独立验证点。"
        f"相机和底座保持固定；采点验证通过后，还需核对高度、实机复核并更新抓取配置。</div>"
        f'<div class="status">硬件占用：<b>{rig_status["owner"] or "空闲"}</b>　'
        f'CAN 口：<b>{rig_status["arm_port"]}</b>　'
        f'内参：<b>{"已加载" if rig_status["has_intrinsics"] else "未加载"}</b></div>'
    )
    return page("总览", f'{head}<div class="grid">{"".join(cards)}</div>', STEPS, active="")


@app.get("/api/rig")
def api_rig():
    return jsonify(rig.status())


@app.post("/rig/take")
def rig_take():
    """强制接管硬件（页面上那个按钮）：把别的步骤挤掉，自己占上。"""
    owner = (request.get_json(silent=True) or {}).get("owner", "")
    step = _step_by_owner(owner)
    if step is None:
        return jsonify({"ok": False, "error": f"未知步骤: {owner}"}), 400
    try:
        rig.acquire(owner, force=True)
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "owner": owner})


@app.post("/rig/release")
def rig_release():
    owner = (request.get_json(silent=True) or {}).get("owner", "")
    try:
        rig.release(owner)
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True})


@app.get("/favicon.ico")
def favicon():
    return redirect("/", code=302)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="piper 标定台（Flask 一体服务，宿主上跑）")
    ap.add_argument("--host", default="0.0.0.0", help="绑定地址；默认全网卡（局域网/手机可开）")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--debug", action="store_true", help="Flask 调试模式（会重启进程）")
    return ap.parse_args()


def main():
    args = parse_args()
    print("=" * 56, flush=True)
    print("piper 标定台: http://<本机IP>:%d（本机打开用 http://127.0.0.1:%d）"
          % (args.port, args.port), flush=True)
    print("产物目录: %s" % os.path.relpath(ROOT / "artifacts", os.getcwd()), flush=True)
    print("=" * 56, flush=True)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        app.run(host=args.host, port=args.port, threaded=True, debug=args.debug, use_reloader=False)
    finally:
        rig.release(rig.owner() or "")


if __name__ == "__main__":
    main()
