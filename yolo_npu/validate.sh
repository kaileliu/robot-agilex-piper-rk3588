#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
mkdir -p "$ROOT/.cache/ultralytics" "$ROOT/.cache/matplotlib" "$ROOT/.cache/tmp"
export PYTHONDONTWRITEBYTECODE=1 YOLO_AUTOINSTALL=false
export YOLO_CONFIG_DIR="$ROOT/.cache/ultralytics" MPLCONFIGDIR="$ROOT/.cache/matplotlib" TMPDIR="$ROOT/.cache/tmp"
export PYTHONPATH="$ROOT:$ROOT/convert_deps:$ROOT/runtime_python${PYTHONPATH:+:$PYTHONPATH}"
SOURCE_VENV="${YOLO_SOURCE_VENV:-/root/robot-agilex-piper-ga/host-services/yolo/.venv}"
exec "$SOURCE_VENV/bin/python" "$ROOT/validate.py" "$@"
