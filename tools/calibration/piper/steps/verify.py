# Description: 验证——点画面上的一个点，让臂把夹爪尖移到那个点，人看对不对。
#
# 标定本身只能验"点对得上"（重投影误差）；这一步是拿真臂走一遍：像素 → 单应 → 臂平面
# (x, y) → IK → 移过去。不经过 robonix 栈（原语那套是运行时的事，标定时不必起）。
import threading

import cv2
import numpy as np
from flask import jsonify, request

import paths
from common.homography import image_to_robot_xy
from common.ui import Step, stream_response
from config import get_config_value


class VerifyStep(Step):
    name = "verify"
    title = "验证"
    desc = ("点画面上的一个点 → 臂把夹爪尖移到该点（像素经单应映射成臂坐标，IK 求解）。"
            "人看一眼夹爪尖是不是正好落在点的那件东西上。会动臂。"
            "臂从「起始位姿」（清单 init_joints_deg 那个固定姿态）出发，和手眼那页是同一个。")
    needs = ("camera", "arm")
    paths = paths

    def __init__(self):
        super().__init__()
        self.selected = None
        self.last = None          # {"pixel": (u,v), "xy": (x,y), "z": z}
        self.hint = "先在画面里点一个点，再点「移过去」。"
        self._lock = threading.Lock()
        self._arm = None

    def guard(self) -> str | None:
        """这一步是拿真臂去验手眼那份矩阵，内参和单应缺哪个都不让进。"""
        missing = [name for name, p in (("内参", self.paths.CAMERA_MATRIX),
                                        ("单应矩阵", self.paths.HOMOGRAPHY)) if not p.exists()]
        if missing:
            return "缺" + "、".join(missing) + "，先把前两步做完。"
        return None

    # ---------- 工具 ----------

    def _frame(self):
        frame = self.rig.camera().get_frames()["color"]
        if frame is None:
            return None
        if self.selected is not None:
            cv2.circle(frame, tuple(self.selected), 8, (0, 0, 255), -1)
        return frame

    def _move_arm(self):
        """建/复用抓取后端（Arm：构造时使能 + 回零；与本子树其它步骤共用一条 CAN 连接）。"""
        if self._arm is None:
            self.rig.drop_arm()          # 同一个 CAN 口不该有两条连接
            from common.robot.arm import Arm

            self._arm = Arm()
            self._arm.hand_eye_calibration_matrix = np.load(self.paths.HOMOGRAPHY)
        return self._arm

    # ---------- 页面 ----------

    def html(self) -> str:
        default_z = get_config_value("default_desktop_height", 0.135, raise_if_missing=False)
        return f"""
<div class="row">
  <div class="col"><img class="stream" src="/{self.name}/stream" alt="camera"></div>
  <div class="col">
    <!-- 两块分开：msg=按钮动作的结果（一直留到下次动作），hint=每秒轮询的状态 -->
    <div id="msg" class="status" style="display:none"></div>
    <div id="hint" class="status">{self.hint}</div>
    <div class="kv">已选点：<b id="sel">-</b>　·　映射到的臂坐标：<b id="xy">-</b>　·
      内参：<b id="intr">-</b></div>
    <p>
      目标 z（臂基座系，米）<input id="z" type="number" step="0.005" value="{default_z}" style="width:7em">
      <button class="primary" onclick="act('move')">移过去</button>
      <button onclick="act('pose')">回起始位姿</button>
    </p>
    <div class="kv">做的是：像素 → 单应 → 臂平面 (x, y) → IK → 末端移到 (x, y, z)，"
      末端垂直朝下。臂会动，确认周围没人再点。</div>
    <h2>最近一次</h2>
    <div class="mono" id="last">-</div>
  </div>
</div>
<script>
const img = document.querySelector("img.stream");
img.addEventListener("click", (e) => {{
  const rect = img.getBoundingClientRect();
  const x = Math.round((e.clientX - rect.left) * img.naturalWidth / rect.width);
  const y = Math.round((e.clientY - rect.top) * img.naturalHeight / rect.height);
  fetch("/{self.name}/click", {{method: "POST", headers: {{"Content-Type": "application/json"}},
                                body: JSON.stringify({{x, y}})}}).then(tick);
}});
async function act(what) {{
  const body = JSON.stringify({{z: +document.getElementById("z").value}});
  const j = await (await fetch("/{self.name}/" + what,
      {{method: "POST", headers: {{"Content-Type": "application/json"}}, body}})).json();
  const el = document.getElementById("msg");
  el.style.display = "block";
  el.textContent = j.hint || j.error || "";
  el.className = "status " + (j.ok ? "ok" : "err");
  tick();
}}
async function tick() {{
  try {{
    const s = await (await fetch("/{self.name}/state")).json();
    document.getElementById("sel").textContent = s.selected ? s.selected.join(", ") : "-";
    document.getElementById("xy").textContent = s.xy ? s.xy.map(v => v.toFixed(4)).join(", ") : "-";
    document.getElementById("intr").textContent = s.has_intrinsics ? "已标" : "未标（先去内参页）";
    document.getElementById("last").textContent = s.last || "-";
    if (s.hint) {{
      const el = document.getElementById("hint");
      el.textContent = s.hint;
      el.className = "status " + (s.ok ? "ok" : "err");
    }}
  }} catch (e) {{}}
}}
setInterval(tick, 900); tick();
</script>"""

    # ---------- 动作 ----------

    def register(self) -> None:
        app = self.app

        @app.get(f"/{self.name}/stream")
        def _stream():
            return stream_response(lambda: self._frame())

        @app.get(f"/{self.name}/state")
        def _state():
            xy = None
            if self.selected is not None and self.paths.HOMOGRAPHY.exists():
                xy = [float(v) for v in image_to_robot_xy(
                    np.load(self.paths.HOMOGRAPHY), self.selected)]
            return jsonify({"selected": list(self.selected) if self.selected else None, "xy": xy,
                            "has_intrinsics": self.rig.has_intrinsics(),
                            "last": self.last, "hint": self.hint,
                            "ok": self.last is not None})

        @app.post(f"/{self.name}/click")
        def _click():
            err = self.acquire()
            if err:
                return jsonify({"ok": False, "error": err})
            data = request.get_json(silent=True) or {}
            with self._lock:
                self.selected = (int(data.get("x", 0)), int(data.get("y", 0)))
            self.hint = "已选点 %s，点「移过去」让臂尖去那儿。" % (self.selected,)
            return jsonify({"ok": True, "hint": self.hint})

        @app.post(f"/{self.name}/move")
        def _move():
            err = self.acquire()
            if err:
                return jsonify({"ok": False, "error": err})
            missing = [name for name, p in (("内参", self.paths.CAMERA_MATRIX),
                                            ("单应矩阵", self.paths.HOMOGRAPHY)) if not p.exists()]
            if missing:
                return jsonify({"ok": False, "error": "还缺：" + "、".join(missing)})
            with self._lock:
                sel = self.selected
            if sel is None:
                return jsonify({"ok": False, "error": "先在画面里点一个点"})
            z = float((request.get_json(silent=True) or {}).get(
                "z", get_config_value("default_desktop_height", 0.135, raise_if_missing=False)))
            x, y = [float(v) for v in image_to_robot_xy(np.load(self.paths.HOMOGRAPHY), sel)]
            try:
                arm = self._move_arm()
            except Exception as e:  # noqa: BLE001
                return jsonify({"ok": False, "error": f"连臂失败：{e}"})
            try:
                ok = arm.move_to([x, y, z], gripper_open_0to1=1)
            except Exception as e:  # noqa: BLE001
                return jsonify({"ok": False, "error": f"动臂失败：{e}"})
            if not ok:
                self.hint = "移过去失败（IK 不收敛或未到位），换个点或改 z 再试。"
                return jsonify({"ok": False, "error": self.hint})
            self.last = (f"像素 {tuple(sel)} → 臂 ({x:.4f}, {y:.4f}, {z:.3f})，已移过去；"
                         f"看夹爪尖对不对得上")
            self.hint = self.last
            print(self.last, flush=True)
            return jsonify({"ok": True, "hint": self.hint})

        @app.post(f"/{self.name}/pose")
        def _pose():
            err = self.acquire()
            if err:
                return jsonify({"ok": False, "error": err})
            import json

            if not self.paths.CALIB_POSE.exists():
                return jsonify({"ok": False, "error": f"没有 {self.paths.CALIB_POSE}"})
            with open(self.paths.CALIB_POSE, encoding="utf-8") as f:
                target = list(json.load(f)["joints_deg"])
            try:
                arm = self._move_arm()
                arm.set_arm_angles(target, gripper_open_0to1=1)
            except Exception as e:  # noqa: BLE001
                return jsonify({"ok": False, "error": f"回起始位姿失败：{e}"})
            self.hint = "已回起始位姿。"
            return jsonify({"ok": True, "hint": self.hint})

    # ---------- 摘要 ----------

    def summary(self) -> dict:
        ok = self.paths.HOMOGRAPHY.exists() and self.paths.CAMERA_MATRIX.exists()
        return {"ok": ok, "lines": [
            "标定完点一个桌面上的点，让臂尖去那儿，肉眼看对不对。",
            self.last or "还没验证过。",
        ]}
