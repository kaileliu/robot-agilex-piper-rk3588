#!/usr/bin/env bash
# Export models/best.pt without changing the original project's virtualenv.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
mkdir -p "$ROOT/.cache/ultralytics" "$ROOT/.cache/matplotlib" "$ROOT/.cache/tmp"
export PYTHONDONTWRITEBYTECODE=1 YOLO_AUTOINSTALL=false
export YOLO_CONFIG_DIR="$ROOT/.cache/ultralytics" MPLCONFIGDIR="$ROOT/.cache/matplotlib" TMPDIR="$ROOT/.cache/tmp"
SOURCE_VENV="${YOLO_SOURCE_VENV:-/root/robot-agilex-piper-ga/host-services/yolo/.venv}"
PYTHON="${YOLO_EXPORT_PYTHON:-$SOURCE_VENV/bin/python}"

if [[ ! -x "$PYTHON" ]]; then
    echo "error: source YOLO Python was not found: $PYTHON" >&2
    echo "       set YOLO_SOURCE_VENV to an environment containing torch and ultralytics" >&2
    exit 1
fi

export PYTHONPATH="$ROOT/convert_deps:$ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON" "$ROOT/export_onnx.py" "$@"
