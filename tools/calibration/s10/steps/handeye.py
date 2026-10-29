# Description: S10 的手眼步骤——把公共实现（common/steps/handeye.py）绑到本部署的产物路径。
#
# S10 的相机装在臂上：每记录一个点都要回到拍照姿态，画面才和标定时一致
# （piper 那侧相机固定在顶上，臂回到位只是为了让操作有个固定起点）。
import paths
from common.steps.handeye import HandeyeStep as _HandeyeStep


class HandeyeStep(_HandeyeStep):
    paths = paths
    desc = ("在画面上点一个桌面上的显眼点 → 按臂上示教按钮进示教、手拖夹爪尖对准该点 → "
            "点「记录」→ 按示教按钮退出示教 → 点「接管臂 + 回起始位姿」（相机在臂上，"
            "必须回位再采下一个点）。采够 ≥4 点（建议 9~16，分散些）后点「拟合单应」。")
