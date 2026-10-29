# Description: 抓取自检——YOLO 找一个目标，单应映射成臂坐标，真抓一次。
#
# 这是标定链的最后一环：内参 → 手眼 → 真抓一次，才知道整条链对不对。
# 相机用去畸变后的画面（与运行时一致），检测用本子树里的 YOLO 权重（models/acm.pt）。
# 会动臂，抓完停在放置点（要收尾去「回零失能」那页）。
import concurrent.futures
import copy
import json
import threading
import time

import numpy as np
from flask import jsonify, request

import paths
from config import get_config_value
from common.ui import Step, stream_response
from vision.detect import detect_objects_in_frame, draw_box, load_model

# 抓取/放置时的夹爪尖离桌面高度（余量）：桌面 z + 2cm
DESKTOP_CLEARANCE_M = 0.02


class CheckCatchStep(Step):
    name = "check_catch"
    title = "抓取自检"
    desc = ("YOLO 找一个目标 → 单应映射成臂坐标 → 抓起来放到 place_pos。会动臂。"
            "需要先有内参、单应矩阵、桌面高度，以及宿主 YOLO（未装 ultralytics 时本页会提示）。")
    needs = ("camera", "arm")

    def __init__(self):
        super().__init__()
        self.running = False
        self.hint = "点「开始」：臂先回零、走到拍照姿态，然后自动抓一次。"
        self.target = "tomato"
        self.detections: list = []
        self.last_action = ""
        self._frame = None
        self._fps = 0.0
        self._lock = threading.Lock()
        self._pump_thread = None
        self._model = None
        self._catch_arm = None
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self._future = None

    # ---------- 预览帧（一个抓图线程，检测与出图共用） ----------

    def _pump_loop(self) -> None:
        from vision.detect import detect_objects_in_frame as _detect

        while True:
            try:
                frame = self.rig.camera().get_frames()["color"]
                if frame is None:
                    time.sleep(0.05)
                    continue
                t0 = time.time()
                if self.running and self._model is not None:
                    self.detections = _detect(
                        self._model, frame,
                        conf_thres=get_config_value("default_conf_thres", 0.5, raise_if_missing=False))
                    self._maybe_catch()
                for (u, v, w, h, r), score, class_id, class_name in self.detections:
                    draw_box(frame, u, v, w, h, np.rad2deg(r), f"{class_name}: {score:.2f}")
                with self._lock:
                    self._frame = frame
                    self._fps = 1.0 / max(1e-6, time.time() - t0)
            except Exception as e:  # noqa: BLE001
                print("抓取自检的抓图线程出错:", e, flush=True)
                time.sleep(0.2)
            time.sleep(0.02)

    def _frame_now(self):
        with self._lock:
            frame = self._frame
        if frame is None:                       # 还没出过图，先直接取一帧
            return self.rig.camera().get_frames()["color"]
        return frame

    # ---------- 抓取 ----------

    def _ensure_catch_arm(self):
        """建抓取用的臂后端（Arm：构造时使能 + 回零；与标定用的直连会话分开）。"""
        if self._catch_arm is None:
            # 让出直连会话：同一个 CAN 口不该有两条连接
            self.rig.drop_arm()
            from common.robot.arm import Arm

            arm = Arm()
            arm.hand_eye_calibration_matrix = np.load(paths.HOMOGRAPHY)
            with open(paths.DESKTOP_Z, encoding="utf-8") as f:
                desktop_z = float(json.load(f)["desktop_z"])
            arm.desktop_height = desktop_z + DESKTOP_CLEARANCE_M
            print("抓取/放置高度 desktop_height = %.4f m（桌面 z=%.4f + 余量 %.2f m）"
                  % (arm.desktop_height, desktop_z, DESKTOP_CLEARANCE_M), flush=True)
            self._catch_arm = arm
        return self._catch_arm

    def _prepare(self) -> None:
        """回到拍照姿态（相机在臂上，不对着桌面就什么都检测不到）。"""
        try:
            arm = self._ensure_catch_arm()
            arm.move_to_home(gripper_open_0to1=1)
            with open(paths.CALIB_POSE, encoding="utf-8") as f:
                observe = list(json.load(f)["joints_deg"])
            if not arm.set_arm_angles(observe):
                self.hint = "动臂到拍照姿态失败。"
                self.running = False
                return
            self.hint = "已到拍照姿态，正在自动抓取。要停就点「停止」。"
        except Exception as e:  # noqa: BLE001
            self.hint = f"准备失败：{e}"
            self.running = False

    def _maybe_catch(self) -> None:
        """有候选目标且上一个动作做完 → 抓一个。"""
        if self._future is not None and not self._future.done():
            return
        if self._catch_arm is None:
            return
        candidates = [d for d in self.detections if self.target in ("", None) or d[3] == self.target]
        if not candidates:
            return
        (u, v, w, h, r), score, _cid, class_name = max(candidates, key=lambda d: d[1])
        try:
            target_x, target_y = self._catch_arm.pixel2pos(u, v)
        except Exception as e:  # noqa: BLE001
            self.last_action = f"映射失败：{e}"
            return
        gripper_angle_rad = self._catch_arm.gripper_angle_by_longer(u, v, w, h, np.rad2deg(r))
        offset = get_config_value("catch_offset", 0.0, raise_if_missing=False)
        place_pos = get_config_value("place_pos", default={}, raise_if_missing=False)
        place_distance_threshold = get_config_value("place_distance_threshold", 0.0,
                                                    raise_if_missing=False)
        class_place_pos = copy.deepcopy(place_pos.get(class_name))
        if class_place_pos is None or "pos" not in class_place_pos:
            class_place_pos = {"pos": [target_x, target_y]}
        else:
            for i, axis in enumerate(class_place_pos["pos"]):
                if axis == "x":
                    class_place_pos["pos"][i] = target_x
                elif axis == "-x":
                    class_place_pos["pos"][i] = -target_x
                elif axis == "y":
                    class_place_pos["pos"][i] = target_y
                elif axis == "-y":
                    class_place_pos["pos"][i] = -target_y
        if np.linalg.norm(np.array(class_place_pos["pos"], dtype=float)
                          - np.array([target_x, target_y])) < place_distance_threshold:
            self.last_action = f"{class_name} 离放置点太近，跳过"
            return
        self.last_action = (f"抓 {class_name}（置信度 {score:.2f}）：像素({u:.0f},{v:.0f}) → "
                            f"臂({target_x:.3f}, {target_y:.3f})")
        print(self.last_action, flush=True)
        self._future = self._executor.submit(
            self._catch_arm.catch_and_place,
            target_x + offset * np.cos(gripper_angle_rad),
            target_y + offset * np.sin(-gripper_angle_rad),
            gripper_angle_rad,
            class_place_pos["pos"],
        )

    # ---------- 页面 ----------

    def html(self) -> str:
        return f"""
<div class="row">
  <div class="col"><img class="stream" src="/{self.name}/stream" alt="detections"></div>
  <div class="col">
    <div id="hint" class="status">{self.hint}</div>
    <div class="kv">状态：<b id="run">停止</b>　·　FPS：<b id="fps">-</b>　·　检测到：<b id="ndet">0</b></div>
    <p>
      目标类别 <input id="target" value="{self.target}" style="width:8em">
      <button class="primary" onclick="start()">开始</button>
      <button class="danger" onclick="act('stop')">停止</button>
    </p>
    <div class="kv">最近动作：<b id="last">-</b></div>
    <h2>检测到的物体</h2>
    <div class="mono" id="dets">-</div>
  </div>
</div>
<script>
async function act(what) {{
  const j = await (await fetch("/{self.name}/" + what, {{method: "POST"}})).json();
  const el = document.getElementById("hint");
  el.textContent = j.hint || j.error || "";
  el.className = "status " + (j.ok ? "ok" : "err");
}}
async function start() {{
  const j = await (await fetch("/{self.name}/start", {{method: "POST",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify({{target: document.getElementById("target").value.trim()}})}})).json();
  const el = document.getElementById("hint");
  el.textContent = j.hint || j.error || "";
  el.className = "status " + (j.ok ? "ok" : "err");
}}
async function tick() {{
  try {{
    const s = await (await fetch("/{self.name}/state")).json();
    document.getElementById("run").textContent = s.running ? "运行中" : "停止";
    document.getElementById("fps").textContent = s.fps.toFixed(1);
    document.getElementById("ndet").textContent = s.n_detections;
    document.getElementById("last").textContent = s.last_action || "-";
    document.getElementById("dets").textContent = s.detections_text || "-";
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
            if self._pump_thread is None:
                self._pump_thread = threading.Thread(target=self._pump_loop, daemon=True)
                self._pump_thread.start()
            return stream_response(self._frame_now, fps=20)

        @app.get(f"/{self.name}/state")
        def _state():
            with self._lock:
                frame_fps = self._fps
            dets = self.detections
            dets_text = "\n".join(
                f"{name}: {score:.2f} @ 像素({u:.0f},{v:.0f}) 尺寸({w:.0f}x{h:.0f})"
                for (u, v, w, h, _r), score, _cid, name in dets) or "（没有检测到）"
            return jsonify({"running": self.running, "fps": frame_fps,
                            "n_detections": len(dets), "detections_text": dets_text,
                            "last_action": self.last_action, "hint": self.hint, "ok": self.running})

        @app.post(f"/{self.name}/start")
        def _start():
            err = self.acquire()
            if err:
                return jsonify({"ok": False, "error": err})
            missing = [name for name, path in (("内参", paths.CAMERA_MATRIX), ("单应矩阵", paths.HOMOGRAPHY),
                                               ("桌面高度", paths.DESKTOP_Z), ("拍照姿态", paths.CALIB_POSE))
                       if not path.exists()]
            if missing:
                return jsonify({"ok": False, "error": "还缺：" + "、".join(missing)})
            data = request.get_json(silent=True) or {}
            self.target = str(data.get("target", self.target)).strip()
            if self._pump_thread is None:
                self._pump_thread = threading.Thread(target=self._pump_loop, daemon=True)
                self._pump_thread.start()
            if self._model is None:
                model_path = paths.ROOT / get_config_value("yolo_model_path", "models/acm.pt",
                                                           raise_if_missing=False)
                try:
                    self._model = load_model(str(model_path))
                except ModuleNotFoundError:
                    return jsonify({"ok": False, "error":
                                    "没装 ultralytics（可选依赖）。宿主上装：uv pip install --python .venv/bin/python "
                                    "\"ultralytics>=8.4\"（会连带拉 torch），或者先用 src 里的其它检查手段。"})
                except Exception as e:  # noqa: BLE001
                    return jsonify({"ok": False, "error": f"加载 YOLO 失败：{e}"})
            self.running = True
            self.last_action = ""
            self.hint = "正在准备（回零 → 拍照姿态）…"
            self._future = self._executor.submit(self._prepare)
            return jsonify({"ok": True, "hint": self.hint})

        @app.post(f"/{self.name}/stop")
        def _stop():
            self.running = False
            self.hint = "已停止检测（臂停在原地）。要收尾去「回零失能」那页。"
            return jsonify({"ok": True, "hint": self.hint})

    # ---------- 摘要 ----------

    def summary(self) -> dict:
        lines = []
        ok = paths.HOMOGRAPHY.exists() and paths.CAMERA_MATRIX.exists() and paths.DESKTOP_Z.exists()
        if not ok:
            lines.append("还缺内参/单应/桌面高度——先去前面的步骤。")
        else:
            lines.append("前置产物齐了，可以真抓一次验证。")
        lines.append("会动臂；抓完停在放置点，用「回零失能」收尾。")
        return {"ok": ok, "lines": lines}
