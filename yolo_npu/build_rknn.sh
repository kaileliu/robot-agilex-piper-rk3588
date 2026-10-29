#!/usr/bin/env bash
# Rebuild the local RKNN model from the exported raw OBB heads.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
mkdir -p "$ROOT/.cache/ultralytics" "$ROOT/.cache/matplotlib" "$ROOT/.cache/tmp"
export PYTHONDONTWRITEBYTECODE=1 YOLO_AUTOINSTALL=false
export YOLO_CONFIG_DIR="$ROOT/.cache/ultralytics" MPLCONFIGDIR="$ROOT/.cache/matplotlib" TMPDIR="$ROOT/.cache/tmp"
SOURCE_VENV="${YOLO_SOURCE_VENV:-/root/robot-agilex-piper-ga/host-services/yolo/.venv}"
PYTHON="${YOLO_CONVERT_PYTHON:-$SOURCE_VENV/bin/python}"

if [[ ! -x "$PYTHON" ]]; then
    PYTHON="/usr/bin/python3"
    SOURCE_SITE="$SOURCE_VENV/lib/python3.10/site-packages"
    if [[ ! -d "$SOURCE_SITE" ]]; then
        echo "error: RKNN conversion needs a Python environment containing torch" >&2
        echo "       set YOLO_CONVERT_PYTHON or YOLO_SOURCE_VENV" >&2
        exit 1
    fi
    export PYTHONPATH="$SOURCE_SITE${PYTHONPATH:+:$PYTHONPATH}"
fi

export PYTHONPATH="$ROOT/convert_deps:$ROOT/converter_python${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON" "$ROOT/convert_rknn.py" "$@"
