# Description: 实测桌面高度（臂基座系）——示教拖夹爪尖贴桌面，读末端 z。
#
# 产物：artifacts/desktop_z.json（config.yaml 的 default_desktop_height 就是它 + 2cm 余量）
import json
import time

from flask import jsonify

import paths
from common.robot.direct import TEACH_STATUS_TEXT
from common.ui import Step, stream_response


class MeasureZStep(Step):
    name = "measure_z"
    title = "测桌面 z"
    desc = ("按臂上示教按钮进示教 → 手拖夹爪尖贴到桌面 → 点「记录当前 z」→ "
            "按示教按钮退出示教 → 点「接管 + 回零失能」。页面会自动认出示教状态。")
    needs = ("camera", "arm")

    def __init__(self):
        super().__init__()
        self.z = None
        self.recorded = None

    # ---------- 页面 ----------

    def html(self) -> str:
        return f"""
<div class="row">
  <div class="col"><img class="stream" src="/{self.name}/stream" alt="camera"></div>
  <div class="col">
    <div id="hint" class="status">正在连臂…</div>
    <div class="kv">当前末端 z：<b id="z">-</b> m　·　关节：<b id="joints">-</b></div>
    <p>
      <button class="primary" onclick="act('/{self.name}/record')">记录当前 z</button>
      <button class="danger" onclick="act('/{self.name}/exit')">接管 + 回零失能</button>
    </p>
    <h2>记录结果</h2>
    <div class="mono" id="rec">-</div>
  </div>
</div>
<script>
async function act(url) {{
  const j = await (await fetch(url, {{method: "POST"}})).json();
  tick();
  return j;
}}
async function tick() {{
  try {{
    const s = await (await fetch("/{self.name}/state")).json();
    const el = document.getElementById("hint");
    el.textContent = s.hint;
    el.className = "status " + (s.ok ? "ok" : "err");
    document.getElementById("z").textContent = s.z === null ? "-" : s.z.toFixed(4);
    document.getElementById("joints").textContent = s.joints || "-";
    document.getElementById("rec").textContent = s.recorded ? JSON.stringify(s.recorded, null, 2) : "-";
  }} catch (e) {{}}
}}
setInterval(tick, 800); tick();
</script>"""

    # ---------- 动作 ----------

    def register(self) -> None:
        app = self.app

        @app.get(f"/{self.name}/stream")
        def _stream():
            return stream_response(lambda: self.rig.camera().get_frames()["color"])

        @app.get(f"/{self.name}/state")
        def _state():
            err = self.acquire()
            if err:
                return jsonify({"ok": False, "hint": err, "z": self.z, "joints": None,
                                "recorded": self.recorded})
            try:
                arm = self.rig.arm()
                st = arm.status()
                joints = ", ".join("%.2f" % v for v in arm.joints())
                z = float(arm.end_pos()[2])
                self.z = z
                if st.teach_status == 0x01:
                    hint = "示教中：把夹爪尖贴到桌面，然后点「记录当前 z」。"
                    ok = True
                else:
                    hint = (f"当前 {TEACH_STATUS_TEXT.get(st.teach_status, hex(st.teach_status))}。"
                            "按臂上示教按钮进示教，拖夹爪尖贴桌面；记录后退出示教再点「接管 + 回零失能」。")
                    ok = False
                return jsonify({"ok": ok, "hint": hint, "z": z, "joints": joints,
                                "recorded": self.recorded})
            except Exception as e:  # noqa: BLE001
                return jsonify({"ok": False, "hint": f"读臂失败：{e}", "z": self.z,
                                "joints": None, "recorded": self.recorded})

        @app.post(f"/{self.name}/record")
        def _record():
            err = self.acquire()
            if err:
                return jsonify({"ok": False, "error": err})
            try:
                pos = self.rig.arm().end_pos()
            except Exception as e:  # noqa: BLE001
                return jsonify({"ok": False, "error": f"读末端位姿失败：{e}"})
            record = {"desktop_z": float(pos[2]), "pos": [float(v) for v in pos],
                      "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
            paths.DESKTOP_Z.parent.mkdir(parents=True, exist_ok=True)
            with open(paths.DESKTOP_Z, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=2)
            self.z = float(pos[2])
            self.recorded = record
            print("已记录桌面 z = %.4f m -> %s" % (self.z, paths.DESKTOP_Z), flush=True)
            return jsonify({"ok": True, "z": self.z, "recorded": record})

        @app.post(f"/{self.name}/exit")
        def _exit():
            err = self.acquire()
            if err:
                return jsonify({"ok": False, "error": err})
            try:
                arm = self.rig.arm()
                if arm.status().teach_status == 0x01:
                    return jsonify({"ok": False, "error": "仍在示教中——先按臂上示教按钮退出示教"})
                if not arm.take_over():
                    return jsonify({"ok": False, "error": "接管失败"})
                arm.home_and_disable()
                return jsonify({"ok": True, "hint": "已回零失能。"})
            except Exception as e:  # noqa: BLE001
                return jsonify({"ok": False, "error": str(e)})

    # ---------- 首页摘要 ----------

    def summary(self) -> dict:
        if paths.DESKTOP_Z.exists():
            with open(paths.DESKTOP_Z, encoding="utf-8") as f:
                rec = json.load(f)
            return {"ok": True, "lines": [
                f"桌面 z = {rec['desktop_z']:.5f} m（{rec.get('ts', '')}）",
                f"config.yaml 的 default_desktop_height 应为 {rec['desktop_z'] + 0.02:.5f}",
            ]}
        return {"ok": False, "lines": ["还没实测过桌面高度。"]}
