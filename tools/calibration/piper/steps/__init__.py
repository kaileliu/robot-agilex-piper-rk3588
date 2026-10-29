# Description: 步骤清单——app.py 按这个顺序生成导航与首页卡片（也就是建议的执行顺序）。
#
# 内参 → 手眼 → 验证：先标内参（手眼要用去畸变后的画面），再标手眼，最后让臂真走一遍。
# 两个入口都支持：刚标完内参直接接着标手眼；或者已有内参（本子树 artifacts、或清单里
# 那份现成的）时，直接标手眼。
from steps.handeye import HandeyeStep
from steps.intrinsics import IntrinsicsStep
from steps.verify import VerifyStep

STEP_CLASSES = [
    IntrinsicsStep,   # 1. 内参 K/D
    HandeyeStep,      # 2. 手眼单应（画面已去畸变）
    VerifyStep,       # 3. 验证：点一个点，臂尖去那儿
]
