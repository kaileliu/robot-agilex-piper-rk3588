"""Compare the NPU export against original weights and benchmark matching sizes.

Run with bash validate.sh [--image path]. No robot actions or camera access.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import time
from types import MethodType

import cv2
import numpy as np
import torch
from ultralytics import YOLO
from ultralytics.data.augment import LetterBox
from ultralytics.utils.nms import non_max_suppression

from export_onnx import raw_obb_forward
from yolo_obb import letterbox_rgb, decode_heads, fast_nms, to_detections
from yolo_npu_server import Detector, DEFAULT_MODEL

ROOT = Path(__file__).resolve().parent


def summary(values):
    return {k: float(v) for k, v in zip(('median_ms', 'p95_ms', 'mean_ms'),
                                      (np.median(values), np.percentile(values, 95), np.mean(values)))}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--model', type=Path, default=DEFAULT_MODEL)
    ap.add_argument('--image', type=Path)
    ap.add_argument('--runs', type=int, default=10)
    ap.add_argument('--cpu-runs', type=int, default=5)
    ap.add_argument('--conf', type=float, default=.5)
    ap.add_argument('--output', type=Path, default=ROOT/'reports/validation.json')
    args = ap.parse_args()
    if min(args.runs, args.cpu_runs) <= 0 or not 0 <= args.conf <= 1:
        ap.error('runs/cpu-runs must be positive; conf must be in [0, 1]')
    cv2.setNumThreads(2)
    torch.set_num_threads(4)
    if args.image:
        frame = cv2.imread(str(args.image))
        if frame is None:
            ap.error(f'Cannot decode {args.image}')
        image_source = str(args.image.resolve())
    else:
        frame = np.random.default_rng(2026).integers(0, 256, (720, 1280, 3), np.uint8)
        image_source = 'seed=2026 random 720x1280; numerical/speed test, not accuracy dataset'
    report = {'image': image_source, 'model': str(args.model), 'runs': args.runs, 'cpu_runs': args.cpu_runs}
    detector = Detector(args.model)
    try:
        report['input_shape'] = list(detector.backend.input_shape)
        report['sdk_version'] = detector.backend.sdk_version
        rgb, gain, pad = letterbox_rgb(frame, detector.backend.input_shape)
        expected = LetterBox(detector.backend.input_shape, auto=False)(image=frame)[..., ::-1][None]
        np.testing.assert_array_equal(rgb, expected)
        yolo = YOLO(str(ROOT/'models/best.pt'))
        yolo.model.eval().fuse()
        tensor = torch.from_numpy(rgb.transpose(0, 3, 1, 2).copy()).float()/255
        with torch.inference_mode():
            dense = yolo.model(tensor)[0].numpy()
            head_model = copy.deepcopy(yolo.model)
            head_model.model[-1].forward = MethodType(raw_obb_forward, head_model.model[-1])
            heads = [a.numpy() for a in head_model(tensor)]
        decoded = decode_heads(heads, detector.metadata, 0)
        reconstructed = decoded[:, [0, 1, 2, 3, 6]]
        reference = dense[0, [0, 1, 2, 3, 7]].T
        np.testing.assert_allclose(reconstructed, reference, rtol=1e-4, atol=.005)
        score_diff = np.abs(decoded[:, 4] - dense[0, 4:7].max(axis=0))
        assert float(score_diff.max()) < 1e-5
        report['cpu_decode_max_abs_xywhr'] = float(np.abs(reconstructed-reference).max())

        # Independent Ultralytics fast NMS oracle, including cross-class boxes.
        candidates = decoded[decoded[:, 4] > .003]
        ours = fast_nms(candidates, .45)
        from ultralytics.utils.nms import TorchNMS
        from ultralytics.utils.metrics import batch_probiou
        boxes = candidates[:, [0, 1, 2, 3, 6]].copy()
        boxes[:, :2] += candidates[:, 5:6]*7680
        if len(boxes) > 3000:
            # Avoid an enormous torch NxN oracle matrix on a low threshold.
            candidates = candidates[np.argsort(-candidates[:, 4])[:3000]]
            boxes = candidates[:, [0, 1, 2, 3, 6]].copy()
            boxes[:, :2] += candidates[:, 5:6]*7680
            ours = fast_nms(candidates, .45)
        oracle = TorchNMS.fast_nms(torch.from_numpy(boxes), torch.from_numpy(candidates[:, 4]), .45, iou_func=batch_probiou)[:300].numpy()
        np.testing.assert_array_equal(ours, oracle)
        report['nms_oracle_candidates'] = len(candidates)

        npu_heads = detector.backend.infer(rgb)
        npu_pred = decode_heads(npu_heads, detector.metadata, 0)
        errors = np.abs(npu_pred[:, [0, 1, 2, 3, 6]] - reference)
        report['npu_vs_cpu'] = {
            'mean_abs_xywh_pixels': errors[:, :4].mean().item(),
            'max_abs_xywh_pixels': errors[:, :4].max().item(),
            'mean_abs_angle_rad': errors[:, 4].mean().item(),
            'max_abs_score': np.abs(npu_pred[:, 4]-decoded[:, 4]).max().item(),
        }
        cpu_boxes = non_max_suppression(torch.from_numpy(dense), args.conf, .45, nc=3, rotated=True)[0].numpy()
        cpu_dets = to_detections(cpu_boxes, detector.names, gain, pad, 1.0)
        npu_dets, _ = detector.detect(frame, args.conf)
        report['detections'] = {'conf': args.conf, 'cpu': cpu_dets, 'npu': npu_dets}
        same_classes = [d['class_id'] for d in cpu_dets] == [d['class_id'] for d in npu_dets]
        report['detection_comparison'] = {'same_classes_and_count_in_score_order': same_classes}
        if same_classes and cpu_dets:
            differences = np.abs(np.asarray([d['obb'] for d in cpu_dets])-np.asarray([d['obb'] for d in npu_dets]))
            report['detection_comparison'].update({
                'max_abs_xywh_pixels': float(differences[:, :4].max()),
                'max_abs_angle_degrees': float(np.degrees(differences[:, 4]).max()),
                'max_abs_confidence': max(abs(a['confidence']-b['confidence']) for a, b in zip(cpu_dets, npu_dets)),
            })
        npu_times, net_times = [], []
        for i in range(args.runs + 2):
            _, times = detector.detect(frame, args.conf)
            if i >= 2:
                npu_times.append(times['total'])
                net_times.append(times['npu'])
        report['npu_pipeline'] = summary(npu_times)
        report['npu_runtime'] = summary(net_times)

        # Full Ultralytics pipeline using identical letterbox shape on both sides.
        cpu_times = []
        for i in range(args.cpu_runs+2):
            t = time.perf_counter()
            result = yolo(frame, imgsz=list(detector.backend.input_shape), rect=False, device='cpu', conf=args.conf, iou=.45, verbose=False)[0]
            if i >= 2:
                cpu_times.append((time.perf_counter()-t)*1000)
        report['cpu_same_shape_pipeline'] = summary(cpu_times)
        report['same_shape_speedup'] = float(np.median(cpu_times)/np.median(npu_times))
        # Check the real predictor's final image-coordinate OBBs as well as its
        # network-space decode. Robot yaw must keep the original angle convention.
        np.testing.assert_allclose(np.asarray([d['obb'] for d in cpu_dets]).reshape(-1, 5),
                                   result.obb.xywhr.cpu().numpy(), rtol=1e-4, atol=.005)
        np.testing.assert_array_equal([d['class_id'] for d in cpu_dets], result.obb.cls.cpu().numpy())
        report['cpu_image_coordinate_parity'] = True
        # Confirm the untouched original weights' default inference resolution.
        original = YOLO(str(ROOT/'models/best.pt'))
        original(frame, conf=args.conf, iou=.45, device='cpu', verbose=False)
        report['original_default_imgsz'] = original.predictor.imgsz
        report['original_preprocessed_shape'] = list(original.predictor.preprocess([frame]).shape)
        report['status'] = 'passed'
        report['scope'] = 'Decode/NMS/coordinate checks and speed; not a labeled accuracy evaluation'
    finally:
        detector.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
