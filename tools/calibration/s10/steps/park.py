# Description: 回零并失能（收尾）——把臂收回到可安全断电的位置。
#
# 标定/示教跑完，臂通常还带电保持在上一个位姿（内参标定、示教都是这样退出的），
# 这里走 s10/robot/arm.py 的 Arm（构造时使能 + 回零，disconnect 时回安全位 + 失能）。
import time

from flask import jsonify

from common.ui import Step


class ParkStep(Step):
    name = "park"
    title = "回零失能"
    desc = "收尾：使能 → 回安全位（J5=25°）→ 失能断开。标定/示教结束后点一下。"
    needs = ("arm",)

    def __init__(self):
        super().__init__()
        self.hint = "还没执行。"
        self.done = False

    def html(self) -> str:
        return f"""
<h2>回零并失能</h2>
<div id="hint" class="status">点下面的按钮开始。</div>
<p><button class="primary" onclick="run()">回零并失能</button></p>
<div class="kv">做的是：连臂 → 使能 → 回零 → 回到可安全失能的位置（J5=25°）→ 失能断开。<br>
臂会动，确认周围没人再点。</div>
<script>
async function run() {{
  const el = document.getElementById("hint");
  el.textContent = "执行中…（臂会先回零，再回到安全位，大约十几秒）";
  el.className = "status";
  const j = await (await fetch("/{self.name}/run", {{method: "POST"}})).json();
  el.textContent = j.hint || j.error || "";
  el.className = "status " + (j.ok ? "ok" : "err");
}}
</script>"""

    def register(self) -> None:
        app = self.app

        @app.post(f"/{self.name}/run")
        def _run():
            err = self.acquire()
            if err:
                return jsonify({"ok": False, "error": err})
            try:
                # 先把直连会话断开：同一个 CAN 口不该有两条连接
                self.rig.drop_arm()
                from common.robot.arm import Arm

                print("回零失能：连接并使能…", flush=True)
                arm = Arm()
                time.sleep(1)
                print("回零失能：回安全位并失能…", flush=True)
                arm.disconnect_arm()
                self.done = True
                self.hint = "已回零并失能。"
                return jsonify({"ok": True, "hint": self.hint})
            except Exception as e:  # noqa: BLE001
                self.hint = f"失败：{e}"
                return jsonify({"ok": False, "error": self.hint})

        @app.get(f"/{self.name}/state")
        def _state():
            return jsonify({"ok": self.done, "hint": self.hint})

    def summary(self) -> dict:
        return {"ok": self.done, "lines": [self.hint]}
