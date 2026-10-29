"""NumPy preprocessing and YOLO11 OBB decoding, matching Ultralytics 8.4.37."""
from __future__ import annotations

import math
import cv2
import numpy as np


def letterbox_rgb(frame, shape):
    """BGR uint8 -> RGB NHWC batch, gain and integer (left, top) padding."""
    h, w = frame.shape[:2]
    target_h, target_w = shape
    gain = min(target_h / h, target_w / w)
    size = (round(w * gain), round(h * gain))
    dw, dh = (target_w - size[0]) / 2, (target_h - size[1]) / 2
    left, right = round(dw - .1), round(dw + .1)
    top, bottom = round(dh - .1), round(dh + .1)
    if (w, h) != size:
        frame = cv2.resize(frame, size, interpolation=cv2.INTER_LINEAR)
    padded = cv2.copyMakeBorder(frame, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114, 114, 114))
    return np.ascontiguousarray(padded[..., ::-1][None]), gain, (left, top)


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -80, 80)))


def decode_heads(outputs, metadata, conf):
    """Return [cx,cy,w,h,confidence,class_id,angle] before NMS, network pixels."""
    nc, bins = len(metadata['names']), metadata['reg_max']
    strides = metadata['strides']
    ih, iw = metadata['input_shape']
    if len(outputs) != 3 * len(strides):
        raise ValueError(f"Expected {3 * len(strides)} output heads, got {len(outputs)}")
    predictions = []
    for i, stride in enumerate(strides):
        box, classes, angle = outputs[i*3:i*3+3]
        h, w = ih // stride, iw // stride
        for value, channels in ((box, 4*bins), (classes, nc), (angle, 1)):
            if value.shape != (1, channels, h, w) or not np.isfinite(value).all():
                raise ValueError(f"Invalid NPU head: {value.shape}, expected {(1, channels, h, w)}")
        probabilities = sigmoid(classes.reshape(nc, -1))
        ids = probabilities.argmax(axis=0)
        scores = probabilities[ids, np.arange(h*w)]
        selected = np.flatnonzero(scores > conf)
        if not len(selected):
            continue
        logits = box.reshape(4, bins, -1)[:, :, selected]
        weights = np.exp(logits - logits.max(axis=1, keepdims=True))
        weights /= weights.sum(axis=1, keepdims=True)
        distance = (weights * np.arange(bins, dtype=np.float32)[None, :, None]).sum(axis=1)
        theta = (sigmoid(angle.reshape(-1)[selected]) - .25) * np.pi
        dx, dy = (distance[2:] - distance[:2]) / 2
        cx = ((selected % w).astype(np.float32) + .5 + dx*np.cos(theta) - dy*np.sin(theta)) * stride
        cy = ((selected // w).astype(np.float32) + .5 + dx*np.sin(theta) + dy*np.cos(theta)) * stride
        bw, bh = (distance[:2] + distance[2:]) * stride
        predictions.append(np.stack((cx, cy, bw, bh, scores[selected], ids[selected], theta), axis=1))
    return np.concatenate(predictions).astype(np.float32) if predictions else np.empty((0, 7), np.float32)


def prob_iou(obb1, obb2, eps=1e-7):
    """Gaussian probabilistic IoU used by Ultralytics rotated fast NMS."""
    def covariance(boxes):
        w2, h2, angle = boxes[:, 2:3]**2/12, boxes[:, 3:4]**2/12, boxes[:, 4:5]
        cos, sin = np.cos(angle), np.sin(angle)
        return w2*cos**2+h2*sin**2, w2*sin**2+h2*cos**2, (w2-h2)*cos*sin
    x1, y1 = obb1[:, 0:1], obb1[:, 1:2]
    x2, y2 = obb2[:, 0][None], obb2[:, 1][None]
    a1, b1, c1 = covariance(obb1)
    a2, b2, c2 = (v.T for v in covariance(obb2))
    determinant = (a1+a2)*(b1+b2)-(c1+c2)**2
    t1 = ((a1+a2)*(y1-y2)**2 + (b1+b2)*(x1-x2)**2) / (determinant+eps) * .25
    t2 = (c1+c2)*(x2-x1)*(y1-y2) / (determinant+eps) * .5
    t3 = np.log(determinant / (4*np.sqrt(np.maximum(a1*b1-c1**2, 0)*np.maximum(a2*b2-c2**2, 0))+eps)+eps)*.5
    bd = np.clip(t1+t2+t3, eps, 100)
    return 1-np.sqrt(1-np.exp(-bd)+eps)


def fast_nms(predictions, iou, max_det=300, block_size=128):
    """Exact fast-NMS rule; chunked to avoid allocating an NxN matrix.

    All higher-scoring candidates suppress a box, including already suppressed
    candidates. That detail intentionally matches Ultralytics, unlike greedy NMS.
    """
    if len(predictions) == 0 or iou == 0:
        return np.empty(0, dtype=np.int64)
    order = np.argsort(-predictions[:, 4], kind='stable')[:30000]
    boxes = predictions[order][:, [0, 1, 2, 3, 6]].copy()
    boxes[:, :2] += predictions[order, 5:6] * 7680.0
    kept = []
    for start in range(0, len(order), block_size):
        end = min(start+block_size, len(order))
        overlap = prob_iou(boxes[:end], boxes[start:end])
        higher = np.arange(end)[:, None] < np.arange(start, end)[None]
        selected = np.flatnonzero(~((overlap >= iou) & higher).any(axis=0)) + start
        kept.extend(order[selected].tolist())
        if len(kept) >= max_det:
            break
    return np.asarray(kept[:max_det], dtype=np.int64)


def to_detections(predictions, names, gain, pad, iou, max_det=300):
    result = []
    for idx in fast_nms(predictions, iou, max_det):
        cx, cy, w, h, score, cid, angle = map(float, predictions[idx])
        cx, cy, w, h = (cx-pad[0])/gain, (cy-pad[1])/gain, w/gain, h/gain
        cid = int(cid)
        result.append({
            'object_name': names[cid], 'class_id': cid, 'confidence': score,
            'obb': [cx, cy, w, h, angle],
            'bbox_2d': [cx-w/2, cy-h/2, cx+w/2, cy+h/2, math.degrees(angle)],
        })
    return result
