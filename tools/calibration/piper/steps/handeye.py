# Description: piper 的手眼步骤——把公共实现（common/steps/handeye.py）绑到本部署的产物路径。
#
# 相机固定在顶部：臂回到起始位姿只是为了让操作有个固定起点，画面不影响
# （S10 那侧相机在臂上，回位是必须的）。
import paths
import json
from flask import jsonify
from common.steps.handeye import HandeyeStep as _HandeyeStep
from feedback import read_feedback, read_stationary_sample
from quality import fit_checked


class HandeyeStep(_HandeyeStep):
    paths = paths
    min_points = 12
    record_hint = "已记录 #%d。手动移开夹爪后选下一个标记；第 10～12 点留作独立验证。"
    desc = ("固定相机和机械臂底座，在桌面放 12 个分散的十字标记。前 9 个覆盖抓取区，"
            "最后 3 个在区域内独立验证。点画面上的标记 → 按臂上示教按钮进入拖动示教 → "
            "把夹爪朝下（偏斜小于 8°），两指尖之间的中心对准标记、保持同一高度 → 停稳后记录。"
            "相机固定，采点间可以继续手动移开夹爪；「回起始位姿」按钮会自动动臂，须先退出示教。")

    def _read_sample(self):
        return read_stationary_sample(self.rig.arm())

    def _fit_and_check(self, image_points, robot_points):
        H, mask, report, quality = fit_checked(image_points, robot_points, self.samples)
        (self.paths.HANDEYE_DIR / "quality.json").write_text(
            json.dumps(quality, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return H, mask, report

    def html(self):
        return self.page_head() + super().html().replace(
            '<h2>拟合结果</h2>',
            '<h2>当前机械臂读数</h2><div class="mono" id="feedback">等待读数</div>'
            '<h2>拟合结果</h2>') + """
<script>
async function feedbackTick() {
  try {
    const s = await (await fetch('/handeye/feedback')).json();
    document.getElementById('feedback').textContent = s.ok ?
      '末端 XYZ (mm): ' + s.xyz_m.map(v => (v*1000).toFixed(1)).join(', ') +
      '\\n偏离垂直: ' + s.tilt_from_down_deg.toFixed(1) + '°（记录要求小于 8°）' +
      '\\n示教状态: ' + s.teach_status + ' / 错误码: ' + s.err_code : s.error;
  } catch(e) { document.getElementById('feedback').textContent = '读数连接中断'; }
  setTimeout(feedbackTick, 1000);
}
feedbackTick();
</script>"""

    def register(self):
        super().register()

        @self.app.get('/handeye/feedback')
        def feedback():
            err = self.acquire()
            if err:
                return jsonify(ok=False, error=err), 409
            try:
                return jsonify(ok=True, **read_feedback(self.rig.arm()))
            except Exception as exc:
                return jsonify(ok=False, error=str(exc)), 503
