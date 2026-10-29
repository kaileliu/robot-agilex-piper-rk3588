# Description: S10 的内参步骤——把公共实现（common/steps/intrinsics.py）绑到本部署的产物路径。
#
# S10 的相机装在臂上：标定期间臂必须一直停在观察位姿，否则相机根本没对着桌面
# （piper 那侧相机固定在顶上，没这回事）。
import paths
from common.steps.intrinsics import IntrinsicsStep as _IntrinsicsStep


class IntrinsicsStep(_IntrinsicsStep):
    paths = paths
    desc = ("先点「摆到起始位姿」让臂上的相机对着桌面，再手持棋盘格在相机前变换位姿"
            "（倾斜、旋转、平移，覆盖画面各个区域）。检测到完整棋盘格时点「采集」，"
            "建议采 10~20 张，然后点「解算」。相机在臂上，整段过程别动臂。")
