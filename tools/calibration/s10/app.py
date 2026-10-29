#!/usr/bin/env python3
"""S10 标定台：一个 Flask 服务，把标定流程的每一步挂成一页。

在宿主上跑（venv 见 requirements.txt）：
    cd tools/calibration/s10
    uv venv --python 3.10 .venv && uv pip install -r requirements.txt
    .venv/bin/python app.py --port 8080
    # 浏览器打开 http://<本机IP>:8080

页面：
    /            总览：每一步的产物状态，从这里进各个步骤
    /measure_z   实测桌面高度（夹爪尖碰桌面，记末端 z）
    /intrinsics  相机内参（棋盘格；画面是原始的，不去畸变）
    /teach       拖动示教，记录关节角（观察位姿、安全位都靠它）
    /handeye     手眼标定（2D 单应；画面已按内参去畸变）
    /check_catch 抓取自检：真抓一次（动臂）
    /park        回零并失能（收尾）

结构：app.py 只管挂页面与共享资源；rig.py 管相机/臂（同时只让一个步骤用）；
steps/ 每个步骤一个模块（页面 + 动作 + 产物摘要）。标定的算法都不在这里，
在 common/（棋盘格、单应）与 robot/、vision/（臂、相机）里。
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent          # tools/calibration/s10
TOOLS = ROOT.parent                             # tools/calibration（common/ 在这下面）
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(ROOT))

from flask import Flask, jsonify, redirect, request  # noqa: E402

import paths  # noqa: E402
from common.rig import Rig  # noqa: E402
from common.ui import card_html, page  # noqa: E402
from config import get_config_value  # noqa: E402
from steps import STEP_CLASSES  # noqa: E402
from vision.camera import S10Camera  # noqa: E402

app = Flask(__name__)
app.site_name = "S10 标定台"
# 相机由本部署注入（S10 = pyrealsense2）；CAN 口留空则自动探测（同臂原语）
rig = Rig(camera_factory=S10Camera, paths=paths,
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
        f'<h1>S10 标定台</h1><div class="desc">六个步骤，顺序建议：'
        f"测桌面 → 内参 → 示教（定观察位姿）→ 手眼 → 抓取自检 → 回零并失能。"
        f"标定完把数值抄进 deploy/s10/manifest.yaml（对照表见 README）。</div>"
        f'<div class="status">硬件占用：<b>{rig_status["owner"] or "空闲"}</b>　'
        f'CAN 口：<b>{rig_status["arm_port"]}</b>　'
        f'内参：<b>{"已标" if rig_status["has_intrinsics"] else "未标"}</b></div>'
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
    ap = argparse.ArgumentParser(description="S10 标定台（Flask 一体服务）")
    ap.add_argument("--host", default="0.0.0.0", help="绑定地址；默认全网卡（局域网/手机可开）")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--debug", action="store_true", help="Flask 调试模式（会重启进程）")
    return ap.parse_args()


def main():
    args = parse_args()
    print("=" * 56, flush=True)
    print("S10 标定台: http://<本机IP>:%d（本机打开用 http://127.0.0.1:%d）"
          % (args.port, args.port), flush=True)
    print("产物目录: %s" % os.path.relpath(ROOT / "artifacts", os.getcwd()), flush=True)
    print("=" * 56, flush=True)
    app.run(host=args.host, port=args.port, threaded=True, debug=args.debug, use_reloader=False)


if __name__ == "__main__":
    main()
