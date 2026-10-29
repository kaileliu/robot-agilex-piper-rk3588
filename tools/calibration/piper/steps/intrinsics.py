# Description: piper 的内参步骤——把公共实现（common/steps/intrinsics.py）绑到本部署的产物路径。
#
# 相机固定在顶部：标定期间不用动臂（「摆到起始位姿」只是把臂转到侧面，别挡住桌面）。
import paths
from common.steps.intrinsics import IntrinsicsStep as _IntrinsicsStep


class IntrinsicsStep(_IntrinsicsStep):
    paths = paths
    desc = ("相机固定在顶部：把棋盘格放在桌面上，变换它的位姿（倾斜、旋转、平移，"
            "覆盖画面各个区域），检测到完整棋盘格时点「采集」。建议采 10~20 张后点「解算」。"
            "整段过程臂不用动（可先点「摆到起始位姿」让它让开桌面）。")
