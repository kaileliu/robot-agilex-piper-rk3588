"""Validate a planar calibration with the final three points held out."""
import cv2
import numpy as np

from common.homography import calibrate_2d_from_correspondences, compute_reprojection_errors


def fit_checked(image_points, robot_points, samples):
    image = np.asarray(image_points, dtype=np.float32)
    robot = np.asarray(robot_points, dtype=np.float32)
    n = len(image)
    if n < 12 or image.shape != (n, 2) or robot.shape != (n, 2):
        raise ValueError("需要至少 12 对点：前 9 个拟合，最后 3 个独立验证")
    if not np.isfinite(image).all() or not np.isfinite(robot).all():
        raise ValueError("坐标包含无效值")
    if len(samples) != n or any("xyz_m" not in s for s in samples):
        raise ValueError("缺少实测高度记录，请重新采点")
    xyz = np.array([s["xyz_m"] for s in samples], dtype=float)
    if xyz.shape != (n, 3) or not np.isfinite(xyz).all():
        raise ValueError("末端坐标格式无效")
    if not np.allclose(xyz[:, :2], robot, atol=1e-6, rtol=0):
        raise ValueError("采点记录与拟合坐标不一致")
    if np.ptp(xyz[:, 2]) > 0.005:
        raise ValueError("各点高度相差超过 5 mm，请在同一平面对点")
    for s in samples:
        if s.get("tilt_from_down_deg", float("inf")) >= 8.0:
            raise ValueError("存在夹爪偏斜 ≥8° 或缺失姿态的采点")
        if s.get("sdk_fk_difference_mm", float("inf")) > 5:
            raise ValueError("存在 SDK 与 IK 参考点不一致的采点")
    distances = np.linalg.norm(image[:, None] - image[None, :], axis=2)
    distances[np.eye(n, dtype=bool)] = np.inf
    if distances.min() < 15:
        raise ValueError("采点过近或重复，请选择不同位置（间距至少 15 像素）")
    train_image, train_robot = image[:-3], robot[:-3]
    hull = cv2.convexHull(train_image)
    if cv2.contourArea(hull) < 10000 or np.ptp(train_robot, axis=0).min() < 0.1:
        raise ValueError("采点覆盖不足：请铺满抓取区，臂坐标 X/Y 各覆盖至少 10 cm")
    if any(cv2.pointPolygonTest(hull, tuple(map(float, p)), False) < 0 for p in image[-3:]):
        raise ValueError("最后 3 个验证点需要位于前面采点包围的区域内")
    H, mask = calibrate_2d_from_correspondences(
        train_image, train_robot, ransac_threshold=0.005, max_iters=10000, confidence=0.999)
    errors = compute_reprojection_errors(H, image, robot)
    if not np.isfinite(errors).all():
        raise ValueError("拟合产生无效坐标")
    train_rms = float(np.sqrt(np.mean(errors[:-3] ** 2)))
    if not mask.all() or train_rms > 0.003 or errors[:-3].max() > 0.005:
        bad = (np.where(~mask | (errors[:-3] > 0.005))[0] + 1).tolist()
        raise ValueError(f"拟合误差过大（RMS {train_rms * 1000:.1f} mm；异常点 {bad}），请检查对点")
    if errors[-3:].max() > 0.005:
        raise ValueError("独立验证未通过：最后 3 点误差为 " +
                         ", ".join(f"{v * 1000:.1f} mm" for v in errors[-3:]))
    denominator = np.c_[image, np.ones(n)] @ H[2]
    if not np.isfinite(H).all() or np.any(denominator == 0) or np.any(denominator * denominator[0] <= 0):
        raise ValueError("矩阵在工作区域存在无效映射")
    quality = {
        "status": "correspondences_validated_pending_physical_check",
        "training_points": n - 3, "held_out_points": 3,
        "ransac_threshold_m": 0.005, "training_rms_mm": train_rms * 1000,
        "held_out_errors_mm": (errors[-3:] * 1000).tolist(),
        "all_errors_mm": (errors * 1000).tolist(),
        "measured_link6_z_m": float(np.median(xyz[:, 2])),
        "z_spread_mm": float(np.ptp(xyz[:, 2]) * 1000),
        "image_hull": hull.reshape(-1, 2).tolist(), "homography": H.tolist(),
        "deployment_updated": False, "physical_grasp_verified": False,
    }
    report = (f"拟合点 {n - 3}；独立验证点 3（未用于拟合）\n"
              f"拟合 RMS：{train_rms * 1000:.2f} mm\n独立验证误差：" +
              ", ".join(f"{v * 1000:.2f} mm" for v in errors[-3:]) +
              f"\n末端参考点高度中位数：{quality['measured_link6_z_m']:.4f} m\n"
              "这是夹爪对点时的末端高度；抓取物体的合爪高度仍需核对。\n"
              "仅在采点覆盖的同一平面内有效，尚未实机抓取验证或写入部署配置。\n" + str(H))
    return H, np.r_[mask, np.ones(3, dtype=bool)], report, quality
