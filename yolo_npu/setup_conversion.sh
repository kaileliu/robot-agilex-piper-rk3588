#!/usr/bin/env bash
# Recreate local conversion dependencies; use the source YOLO venv read-only.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
SOURCE_VENV="${YOLO_SOURCE_VENV:-/root/robot-agilex-piper-ga/host-services/yolo/.venv}"
PYTHON="${YOLO_CONVERT_PYTHON:-$SOURCE_VENV/bin/python}"
export PYTHONDONTWRITEBYTECODE=1
export UV_CACHE_DIR="$ROOT/.cache/uv" TMPDIR="$ROOT/.cache/tmp"
mkdir -p "$ROOT/convert_deps" "$TMPDIR"
command -v uv >/dev/null
"$PYTHON" - "$ROOT" <<'PY'
import hashlib
from pathlib import Path
import sys
import zipfile
root = Path(sys.argv[1])
wheel = root/'vendor/rknn_toolkit2-2.3.2-cp310-cp310-manylinux_2_17_aarch64.manylinux2014_aarch64.whl'
if sys.version_info[:2] != (3, 10):
    raise SystemExit('The bundled wheel requires Python 3.10')
if hashlib.sha256(wheel.read_bytes()).hexdigest() != '73d0bb55d30ee00bbd9603331a4118da73a4e12fa9d9196200460d07d67c6a12':
    raise SystemExit('RKNN Toolkit2 wheel checksum mismatch')
with zipfile.ZipFile(wheel) as z:
    z.extractall(root/'converter_python')
PY
uv pip install --python "$PYTHON" --target "$ROOT/convert_deps" --no-deps \
    --index-url "${YOLO_PYPI_INDEX:-https://pypi.org/simple}" -r "$ROOT/requirements-convert.txt"
echo "[setup] conversion dependencies installed in $ROOT/convert_deps"
