# Description: 拖动示教 + 记录关节角——观察位姿、安全位这类"要人去摆"的位姿都靠它。
#
# 产物：artifacts/poses/taught_pose.json（一条一条追加，带时间戳；挑合适的写进
# artifacts/poses/s10_calib_pose.json，再抄进清单的 init_joints_deg）
import json
import threading
import time

from flask import jsonify

import paths
from common.robot.direct import ARM_STATUS_TEXT, CTRL_MODE_TEXT, TEACH_STATUS_TEXT
from common.ui import Step, stream_response


class TeachStep(Step):
    name = "teach"
    title = "示教"
    desc = ("进拖动示教（臂松弛、可手拖）→ 手拖到想要的位姿 → 点「记录当前关节角」。"
            "「走到观察位姿」用于先把臂摆回上次标定好的位置。")
    needs = ("camera", "arm")

    def __init__(self):
        super().__init__()
        self.hint = "点「进拖动示教」开始（臂会松开，托住它）。"
        self.in_teach = False
        self._lock = threading.Lock()

    # ---------- 页面 ----------

    def html(self) -> str:
        return f"""
<div class="row">
  <div class="col"><img class="stream" src="/{self.name}/stream" alt="camera"></div>
  <div class="col">
    <div id="hint" class="status">{self.hint}</div>
    <div class="kv">
      状态：<b id="stat">-</b><br>
      关节角（度）：<b id="joints">-</b><br>
      末端 pos：<b id="pos">-</b>　euler zyx：<b id="euler">-</b><br>
      夹爪开度：<b id="grip">-</b>
    </div>
    <p>
      <button onclick="act('pose')">走到观察位姿</button>
      <button class="primary" onclick="act('enter')">进拖动示教</button>
      <button onclick="act('exit')">退出示教（保持不动）</button>
      <button class="danger" onclick="if(confirm('回零并失能？臂会缓慢回安全位。')) act('home')">回零并失能</button>
    </p>
    <p><button class="primary" onclick="act('record')">记录当前关节角</button></p>
    <h2>已记录</h2>
    <div class="mono" id="records">-</div>
  </div>
</div>
<script>
async function act(what) {{
  const j = await (await fetch("/{self.name}/" + what, {{method: "POST"}})).json();
  const el = document.getElementById("hint");
  el.textContent = j.hint || j.error || "";
  el.className = "status " + (j.ok ? "ok" : "err");
  tick();
}}
async function tick() {{
  try {{
    const s = await (await fetch("/{self.name}/state")).json();
    document.getElementById("stat").textContent = s.stat || "-";
    document.getElementById("joints").textContent = s.joints || "-";
    document.getElementById("pos").textContent = s.pos || "-";
    document.getElementById("euler").textContent = s.euler || "-";
    document.getElementById("grip").textContent = s.gripper === null ? "-" : s.gripper;
    document.getElementById("records").textContent = s.records || "-";
    if (s.hint) {{
      const el = document.getElementById("hint");
      el.textContent = s.hint;
      el.className = "status " + (s.ok ? "ok" : "err");
    }}
  }} catch (e) {{}}
}}
setInterval(tick, 800); tick();
</script>"""

    # ---------- 记录文件 ----------

    def _records(self) -> list:
        try:
            with open(paths.TAUGHT_POSE, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _records_text(self) -> str:
        records = self._records()
        if not records:
            return "（还没有记录）"
        return "\n".join(
            f"#{i} {r['ts']}  [{', '.join('%.2f' % v for v in r['joints_deg'])}]"
            for i, r in enumerate(records)
        )

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
                return jsonify({"ok": False, "hint": err, "stat": None, "joints": None,
                                "pos": None, "euler": None, "gripper": None,
                                "records": self._records_text()})
            try:
                arm = self.rig.arm()
                st = arm.status()
                pos = arm.end_pos()
                return jsonify({
                    "ok": self.in_teach,
                    "hint": self.hint,
                    "stat": "%s / %s / %s" % (
                        ARM_STATUS_TEXT.get(st.arm_status, hex(st.arm_status)),
                        TEACH_STATUS_TEXT.get(st.teach_status, hex(st.teach_status)),
                        CTRL_MODE_TEXT.get(st.ctrl_mode, hex(st.ctrl_mode))),
                    "joints": ", ".join("%.2f" % v for v in arm.joints()),
                    "pos": "[%.4f, %.4f, %.4f]" % tuple(pos),
                    "euler": ", ".join("%.2f" % v for v in arm.end_euler_zyx_deg()),
                    "gripper": round(arm.gripper_open_0to1(), 2),
                    "records": self._records_text(),
                })
            except Exception as e:  # noqa: BLE001
                return jsonify({"ok": False, "hint": f"读臂失败：{e}", "stat": None, "joints": None,
                                "pos": None, "euler": None, "gripper": None,
                                "records": self._records_text()})

        @app.post(f"/{self.name}/pose")
        def _pose():
            err = self.acquire()
            if err:
                return jsonify({"ok": False, "error": err})
            if not paths.CALIB_POSE.exists():
                return jsonify({"ok": False, "error": f"没有 {paths.CALIB_POSE}（还没标定过观察位姿）"})
            with open(paths.CALIB_POSE, encoding="utf-8") as f:
                target = list(json.load(f)["joints_deg"])
            try:
                arm = self.rig.arm()
                arm.enable_and_take_control()
                arm.move_joints(target)
            except Exception as e:  # noqa: BLE001
                return jsonify({"ok": False, "error": f"动臂失败：{e}"})
            self.hint = "已到观察位姿。"
            return jsonify({"ok": True, "hint": self.hint})

        @app.post(f"/{self.name}/enter")
        def _enter():
            err = self.acquire()
            if err:
                return jsonify({"ok": False, "error": err})
            try:
                arm = self.rig.arm()
                arm.enable_and_take_control()
                arm.open_gripper()
                arm.enter_teach_mode()
            except Exception as e:  # noqa: BLE001
                return jsonify({"ok": False, "error": f"进示教失败：{e}"})
            self.in_teach = True
            self.hint = "示教中：托住臂拖到想要的位姿，然后点「记录当前关节角」。"
            return jsonify({"ok": True, "hint": self.hint})

        @app.post(f"/{self.name}/record")
        def _record():
            err = self.acquire()
            if err:
                return jsonify({"ok": False, "error": err})
            try:
                arm = self.rig.arm()
                joints = [round(v, 2) for v in arm.joints()]
                pos = [round(float(v), 4) for v in arm.end_pos()]
                euler = [round(v, 2) for v in arm.end_euler_zyx_deg()]
                record = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "joints_deg": joints,
                          "gripper": round(arm.gripper_open_0to1(), 2), "pos": pos,
                          "euler_zyx": euler}
            except Exception as e:  # noqa: BLE001
                return jsonify({"ok": False, "error": f"读臂失败：{e}"})
            records = self._records()
            records.append(record)
            paths.POSES_DIR.mkdir(parents=True, exist_ok=True)
            with open(paths.TAUGHT_POSE, "w", encoding="utf-8") as f:
                json.dump(records, f, ensure_ascii=False, indent=2)
            print("RECORDED #%d: %s" % (len(records) - 1, json.dumps(record, ensure_ascii=False)),
                  flush=True)
            self.hint = "已记录 #%d。要选观察位姿就把它抄进 s10_calib_pose.json。" % (len(records) - 1)
            return jsonify({"ok": True, "hint": self.hint, "index": len(records) - 1})

        @app.post(f"/{self.name}/exit")
        def _exit():
            err = self.acquire()
            if err:
                return jsonify({"ok": False, "error": err})
            try:
                self.rig.arm().exit_teach_mode()
            except Exception as e:  # noqa: BLE001
                return jsonify({"ok": False, "error": str(e)})
            self.in_teach = False
            self.hint = "已退出示教，臂在当前位置保持（带电）。"
            return jsonify({"ok": True, "hint": self.hint})

        @app.post(f"/{self.name}/home")
        def _home():
            err = self.acquire()
            if err:
                return jsonify({"ok": False, "error": err})
            try:
                arm = self.rig.arm()
                if self.in_teach:
                    arm.exit_teach_mode()
                    self.in_teach = False
                arm.home_and_disable()
            except Exception as e:  # noqa: BLE001
                return jsonify({"ok": False, "error": str(e)})
            self.hint = "已回零并失能。"
            return jsonify({"ok": True, "hint": self.hint})

    # ---------- 摘要 ----------

    def summary(self) -> dict:
        records = self._records()
        if not records:
            return {"ok": False, "lines": ["还没记录过位姿。"]}
        last = records[-1]
        return {"ok": True, "lines": [
            f"记录了 {len(records)} 条，最后一条 {last['ts']}",
            "最后一条关节角: [" + ", ".join("%.2f" % v for v in last["joints_deg"]) + "]",
        ]}
