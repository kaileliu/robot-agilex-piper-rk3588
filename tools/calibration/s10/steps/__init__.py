# Description: 步骤清单——app.py 按这个顺序生成导航与首页卡片（也就是建议的执行顺序）。
from steps.check_catch import CheckCatchStep
from steps.handeye import HandeyeStep
from steps.intrinsics import IntrinsicsStep
from steps.measure_z import MeasureZStep
from steps.park import ParkStep
from steps.teach import TeachStep

STEP_CLASSES = [
    MeasureZStep,     # 1. 桌面高度（后面的抓取高度靠它）
    IntrinsicsStep,   # 2. 内参 K/D（手眼要用去畸变后的画面）
    TeachStep,        # 3. 示教，定观察位姿
    HandeyeStep,      # 4. 手眼单应
    CheckCatchStep,   # 5. 真抓一次验证
    ParkStep,         # 6. 回零失能收尾
]
