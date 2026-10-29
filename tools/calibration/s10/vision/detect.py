# Description: YOLO(OBB) 目标检测——给标定台的「抓取自检」那页用。
#
# 与 roboarm 的 object_detect/detect.py 是同一套 ultralytics OBB 调用通路；
# 原文件里那个用 Camera 的 main() 与 Camera 依赖没带过来（取图归 vision/camera.py）。
# 这个模块是唯一需要 ultralytics 的地方（可选依赖，见 requirements.txt）。
import cv2
import numpy as np


def detect_objects_in_frame(model, frame, conf_thres=0.8, iou_thres=0.45):
    """跑一帧检测。

    Returns:
        [(u, v, w, h, r(rad)), score, class_id, class_name] 的列表（OBB 格式）。
    """
    results = model(frame, conf=conf_thres, iou=iou_thres)[0]
    detections = results.obb.xywhr.cpu().numpy()  # xywhr format
    scores = results.obb.conf.cpu().numpy()
    class_ids = results.obb.cls.cpu().numpy().astype(int)
    class_names = [model.names[i] for i in class_ids]
    return [
        ((u, v, w, h, r), score, class_id, class_name)
        for (u, v, w, h, r), score, class_id, class_name in zip(
            detections, scores, class_ids, class_names
        )
    ]


def draw_box(frame, u, v, w, h, angle_deg, label):
    """把 OBB 画到画面上（含标签）。"""
    box_points = cv2.boxPoints(((u, v), (w, h), angle_deg))
    box_points = np.int64(box_points)
    cv2.drawContours(frame, [box_points], 0, (0, 255, 0), 2)
    cv2.putText(
        frame,
        label,
        (int(u - w / 2), int(v - h / 2) - 10),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (0, 255, 0),
        2,
    )


def load_model(model_path, device=""):
    """加载 YOLO 模型；device 为空则用 ultralytics 自动选择。

    ultralytics 是可选依赖（见 requirements.txt），用到时才 import，
    这样没装它也能跑 --help 和其它过程。
    """
    from ultralytics import YOLO

    model = YOLO(model_path)
    if device:
        model.to(device)
    return model
