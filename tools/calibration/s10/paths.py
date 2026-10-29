# Description: 标定产物的路径——全子树唯一来源。
#
# 产物是这套工具与部署之间的接口，标定完要抄进部署清单（deploy/s10/manifest.yaml）：
#   intrinsics/{camera_matrix,dist_coeffs}.npy → 清单里 roboarm_ik 的内参/畸变
#   handeye/2d_homography.npy                 → 清单里抓取技能的 2d_homography
#   desktop_z.json                            → 桌面高度（抓取/放置的 z 基准）
#   poses/s10_calib_pose.json                 → init_joints_deg（观察/拍照位姿）
#   poses/taught_pose.json                    → 示教记录（选观察位姿、安全位时改的原料）
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ARTIFACTS = ROOT / "artifacts"

# 内参标定（标定台「内参」那页产出）
INTRINSICS_DIR = ARTIFACTS / "intrinsics"
CAMERA_MATRIX = INTRINSICS_DIR / "camera_matrix.npy"
DIST_COEFFS = INTRINSICS_DIR / "dist_coeffs.npy"
INTRINSICS_IMAGES = INTRINSICS_DIR / "images"

# 手眼标定（标定台「手眼」那页产出）
HANDEYE_DIR = ARTIFACTS / "handeye"
HOMOGRAPHY = HANDEYE_DIR / "2d_homography.npy"
HANDEYE_IMAGE_POINTS = HANDEYE_DIR / "2d_image_points.npy"
HANDEYE_ROBOT_POINTS = HANDEYE_DIR / "2d_robot_points.npy"

# 位姿（标定台「示教」那页产出）
POSES_DIR = ARTIFACTS / "poses"
CALIB_POSE = POSES_DIR / "s10_calib_pose.json"    # 标定观察位姿（关节角）
TAUGHT_POSE = POSES_DIR / "taught_pose.json"      # 示教记录（多条，带时间戳）

# 桌面高度实测（标定台「测桌面 z」那页产出）
DESKTOP_Z = ARTIFACTS / "desktop_z.json"
