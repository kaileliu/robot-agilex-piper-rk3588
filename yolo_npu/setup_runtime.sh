#!/usr/bin/env bash
# Install only the small runtime-side Python dependencies into this directory.
# The RKNN Lite2 wheel and librknnrt.so are already vendored under this tree.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${YOLO_NPU_PYTHON:-/usr/bin/python3}"
export PYTHONDONTWRITEBYTECODE=1
export UV_CACHE_DIR="$ROOT/.cache/uv" TMPDIR="$ROOT/.cache/tmp"

command -v uv >/dev/null 2>&1 || {
    echo "error: uv is required to install the local runtime dependencies" >&2
    exit 1
}

mkdir -p "$ROOT/runtime_deps" "$TMPDIR"
"$PYTHON" - "$ROOT" <<'PY'
import hashlib
from pathlib import Path
import sys
import zipfile
root = Path(sys.argv[1])
wheel = root/'vendor/rknn_toolkit_lite2-2.3.2-cp310-cp310-manylinux_2_17_aarch64.manylinux2014_aarch64.whl'
if sys.version_info[:2] != (3, 10):
    raise SystemExit('The bundled wheel requires Python 3.10')
if hashlib.sha256(wheel.read_bytes()).hexdigest() != '821e80c95e6838308c133915660b1a6ae78bb8d079b2cbbd46a02dae61192d33':
    raise SystemExit('RKNN Lite2 wheel checksum mismatch')
with zipfile.ZipFile(wheel) as z:
    z.extractall(root/'runtime_python')
PY
uv pip install \
    --python "$PYTHON" \
    --target "$ROOT/runtime_deps" \
    --index-url "${YOLO_PYPI_INDEX:-https://pypi.org/simple}" \
    -r "$ROOT/requirements-runtime.txt"

echo "[setup] runtime dependencies installed in $ROOT/runtime_deps"
