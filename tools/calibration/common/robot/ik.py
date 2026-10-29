# Description: piper URDF 的运动学——解析运动学链与关节限位、数值求解逆运动学。
#
# robot/piper_sdk.py（抓取用的臂后端）也用这里的解析结果，URDF 只在一处解析。
import re
from pathlib import Path
from xml.etree import ElementTree

import kinpy
import numpy as np
from scipy.optimize import minimize
from scipy.spatial.transform import Rotation as R

from common.robot.arm import Arm

# piper 的 URDF（随本子树放在 robot/urdf/piper/）
PIPER_URDF_PATH = Path(__file__).resolve().parent / "urdf" / "piper" / "piper_description.urdf"

# 末端连杆名（URDF 里最后一段）
END_LINK = "link6"


def build_chain_and_bounds(urdf_path=PIPER_URDF_PATH):
    """解析 URDF。

    Returns:
        (kinpy 串联链, 各 revolute 关节的 (lower, upper) 限位，弧度)。
    """
    content = Path(urdf_path).read_text(encoding="utf-8")
    # 去掉 XML 声明，否则 ElementTree 解析 unicode 字符串会报错
    content = re.sub(r"<\?xml[^?]*\?>", "", content, count=1)
    chain = kinpy.build_serial_chain_from_urdf(content, END_LINK)

    bounds: list[tuple[float, float]] = []
    for joint_elem in ElementTree.fromstring(content).findall("joint"):
        if joint_elem.get("type") != "revolute":
            continue
        limit = joint_elem.find("limit")
        if limit is None:
            print(f"警告：URDF 关节 {joint_elem.get('name')} 没有 limit，按不可动处理")
            bounds.append((0.0, 0.0))
            continue
        bounds.append((float(limit.get("lower", 0)), float(limit.get("upper", 0))))
    return chain, bounds


def solve_ik(pos, euler_zyx_deg, chain, bounds, n_starts: int = 15, seed: int = 42):
    """多起点 SLSQP 求逆运动学解。

    单起点容易落进不可用的局部解（标定工具要的是一个"能到位且姿态正"的解），
    所以从随机起点跑 n_starts 次，取代价最小的那个。

    Args:
        pos: 目标末端位置 [x, y, z]，单位为米。
        euler_zyx_deg: 目标末端欧拉角 [RZ, RY, RX]，单位为度。
        chain: build_chain_and_bounds 得到的运动学链。
        bounds: build_chain_and_bounds 得到的关节限位。
        n_starts: 随机起点个数。
        seed: 随机种子（固定后同一目标的结果可复现）。

    Returns:
        scipy 的 OptimizeResult：`x` 为关节角（弧度），`fun` 为该解的代价。
    """
    target = kinpy.Transform(
        pos=np.array(pos, dtype=float),
        rot=R.from_euler("zyx", euler_zyx_deg, degrees=True).as_euler("xyz"),
    ).matrix()
    rng = np.random.default_rng(seed)
    lo = np.array([b[0] for b in bounds])
    hi = np.array([b[1] for b in bounds])
    best = None
    for _ in range(n_starts):
        res = minimize(
            Arm._ik_cost_function, x0=rng.uniform(lo, hi), args=(target, chain),
            method="SLSQP", bounds=bounds, options={"maxiter": 300, "ftol": 1e-9},
        )
        if best is None or res.fun < best.fun:
            best = res
    return best
