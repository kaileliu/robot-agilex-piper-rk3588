#!/usr/bin/env python3
"""RK3588 NPU YOLO11 OBB service compatible with the Piper HTTP detector."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
from pathlib import Path
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np

from rknn_backend import RknnBackend
from yolo_obb import letterbox_rgb, decode_heads, to_detections

ROOT = Path(__file__).resolve().parent
DEFAULT_MODEL = ROOT / 'models/best_736x1280_heads_fp16.rknn'
log = logging.getLogger('yolo_npu')


class Detector:
    def __init__(self, model=DEFAULT_MODEL, runtime=ROOT/'runtime/librknnrt.so', core_mask=7, metadata=None, max_det=300):
        self.model = Path(model).resolve()
        self.metadata = json.loads(Path(metadata or self.model.with_suffix('.json')).read_text())
        if hashlib.sha256(self.model.read_bytes()).hexdigest() != self.metadata['rknn_sha256']:
            raise ValueError('RKNN model does not match its JSON metadata; rebuild or restore the matching pair')
        if self.metadata['output_format'] != 'raw_obb_heads_v1' or self.metadata['color'] != 'RGB':
            raise ValueError('Unsupported model format; use export_onnx.sh and build_rknn.sh')
        self.names = {int(k): str(v) for k, v in self.metadata['names'].items()}
        if sorted(self.names) != list(range(len(self.names))):
            raise ValueError('Class IDs must be contiguous starting at zero')
        self.max_det = max_det
        self.lock = threading.Lock()
        self.backend = RknnBackend(self.model, runtime, core_mask)
        if tuple(self.metadata['input_shape']) != self.backend.input_shape:
            self.close()
            raise ValueError('Model dimensions do not match metadata')

    def detect(self, frame, conf=.8, iou=.45):
        if not 0 <= conf <= 1 or not 0 <= iou <= 1:
            raise ValueError('conf/iou must be finite numbers in [0, 1]')
        started = time.perf_counter()
        rgb, gain, pad = letterbox_rgb(frame, self.backend.input_shape)
        prepared = time.perf_counter()
        with self.lock:
            acquired = time.perf_counter()
            outputs = self.backend.infer(rgb)
        inferred = time.perf_counter()
        predictions = decode_heads(outputs, self.metadata, conf)
        detections = to_detections(predictions, self.names, gain, pad, iou, self.max_det)
        ended = time.perf_counter()
        return detections, {
            'preprocess': (prepared-started)*1000, 'queue': (acquired-prepared)*1000,
            'npu': (inferred-acquired)*1000, 'postprocess': (ended-inferred)*1000,
            'total': (ended-started)*1000,
        }

    def close(self):
        self.backend.release()


class DetectionHTTPServer(ThreadingHTTPServer):
    # Finish in-flight requests before releasing the shared NPU context.
    daemon_threads = False


class Handler(BaseHTTPRequestHandler):
    server_version = 'yolo_npu_server/2.0'

    def _send_json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        log.debug('%s - %s', self.address_string(), fmt % args)

    def do_GET(self):
        if urlparse(self.path).path != '/health':
            self._send_json(404, {'success': False, 'message': 'not found'})
            return
        d = self.server.detector
        self._send_json(200, {
            'success': True, 'status': 'ok', 'device': 'rk3588-npu',
            'model_path': str(d.model), 'runtime_path': d.backend.runtime_path,
            'classes': d.names, 'default_conf': self.server.conf, 'default_iou': self.server.iou,
            'input_size': list(d.backend.input_shape), 'core_mask': d.backend.core_mask,
            'precision': d.metadata['precision'], 'sdk_version': d.backend.sdk_version,
        })

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != '/detect':
            self._send_json(404, {'success': False, 'message': 'not found'})
            return
        qs = parse_qs(parsed.query)
        try:
            conf = float(qs.get('conf', [self.server.conf])[0])
            iou = float(qs.get('iou', [self.server.iou])[0])
            if not 0 <= conf <= 1 or not 0 <= iou <= 1:
                raise ValueError()
        except (ValueError, TypeError):
            self._send_json(400, {'success': False, 'message': 'conf/iou must be finite numbers in [0, 1]'})
            return
        try:
            length = int(self.headers.get('Content-Length', 0))
        except ValueError:
            length = 0
        if length <= 0:
            self._send_json(400, {'success': False, 'message': 'empty body; POST raw image bytes'})
            return
        raw = self.rfile.read(length)
        frame = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR) if raw else None
        if frame is None:
            self._send_json(400, {'success': False, 'message': 'could not decode image bytes'})
            return
        try:
            detections, timings = self.server.detector.detect(frame, conf, iou)
        except Exception as exc:
            log.exception('NPU inference failed')
            self._send_json(500, {'success': False, 'message': f'inference error: {exc}'})
            return
        key = qs.get('object_name', [''])[0].strip().lower()
        if key:
            detections = [d for d in detections if key in d['object_name'].lower()]
        self._send_json(200, {
            'success': True, 'message': f'{len(detections)} detection(s)',
            'image_size': list(frame.shape[:2]), 'inference_ms': round(timings['total'], 1),
            'timings_ms': {k: round(v, 2) for k, v in timings.items()}, 'detections': detections,
        })


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--model', type=Path, default=DEFAULT_MODEL)
    ap.add_argument('--runtime', type=Path, default=ROOT/'runtime/librknnrt.so')
    ap.add_argument('--metadata', type=Path)
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8770)
    ap.add_argument('--conf', type=float, default=.8)
    ap.add_argument('--iou', type=float, default=.45)
    ap.add_argument('--max-det', type=int, default=300)
    ap.add_argument('--core-mask', type=int, choices=[0, 1, 2, 3, 4, 7], default=7)
    ap.add_argument('--log', choices=['debug', 'info', 'warning', 'error'], default='info')
    args = ap.parse_args()
    if not 0 <= args.conf <= 1 or not 0 <= args.iou <= 1 or args.max_det <= 0:
        ap.error('conf/iou must be in [0, 1] and max-det must be positive')
    logging.basicConfig(level=args.log.upper(), format='%(asctime)s %(levelname)s %(message)s')
    cv2.setNumThreads(2)
    detector = Detector(args.model, args.runtime, args.core_mask, args.metadata, args.max_det)
    def stop(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        detector.detect(np.zeros((720, 1280, 3), np.uint8), args.conf, args.iou)
        with DetectionHTTPServer((args.host, args.port), Handler) as httpd:
            httpd.detector, httpd.conf, httpd.iou = detector, args.conf, args.iou
            log.info('NPU ready: %s, input HxW=%s, classes=%s', detector.model, detector.backend.input_shape, detector.names)
            log.info('Serving http://%s:%s (GET /health, POST /detect)', args.host, args.port)
            httpd.serve_forever()
    except KeyboardInterrupt:
        log.info('Shutting down')
    finally:
        detector.close()


if __name__ == '__main__':
    main()
