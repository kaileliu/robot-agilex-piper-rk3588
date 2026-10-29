#!/usr/bin/env bash
# Start the RK3588 NPU backed detector.  Everything used by this wrapper lives
# below this directory; the original robot-agilex-piper-ga tree is untouched.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${YOLO_NPU_PYTHON:-/usr/bin/python3}"
export PYTHONDONTWRITEBYTECODE=1

export PYTHONPATH="$ROOT/runtime_python:$ROOT/runtime_deps${PYTHONPATH:+:$PYTHONPATH}"
export LD_LIBRARY_PATH="$ROOT/runtime${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

if ! "$PYTHON" -c 'import cv2, numpy; from rknnlite.api import RKNNLite' >/dev/null 2>&1; then
    echo "[run] runtime dependencies are missing; installing them under $ROOT/runtime_deps"
    bash "$ROOT/setup_runtime.sh"
fi

MODEL="${YOLO_NPU_MODEL:-$ROOT/models/best_736x1280_heads_fp16.rknn}"
RUNTIME="${YOLO_NPU_RUNTIME:-$ROOT/runtime/librknnrt.so}"
HOST="${YOLO_HOST:-127.0.0.1}"
PORT="${YOLO_PORT:-8770}"

echo "[run] RK3588 NPU YOLO service -> http://${HOST}:${PORT}"
echo "[run]   model=$MODEL"
exec "$PYTHON" "$ROOT/yolo_npu_server.py" \
    --host "$HOST" \
    --port "$PORT" \
    --model "$MODEL" \
    --runtime "$RUNTIME" \
    --conf "${YOLO_CONF:-0.8}" \
    --iou "${YOLO_IOU:-0.45}" \
    --core-mask "${YOLO_NPU_CORES:-7}" "$@"
