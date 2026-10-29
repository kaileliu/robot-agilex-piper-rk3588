#!/usr/bin/env bash
set -euo pipefail

PKG="${RBNX_PACKAGE_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$PKG"

FLAGS=(--mcp)
[[ "${RBNX_BUILD_CLEAN:-}" == "1" ]] && FLAGS+=(--clean)

python3 -c "import grpc, mcp, requests" || {
    echo "[internvla_navigation] missing Python dependencies" >&2
    echo "Run: python3 -m pip install -r $PKG/requirements.txt" >&2
    exit 1
}

rbnx codegen -p "$PKG" "${FLAGS[@]}"
echo "[internvla_navigation] build done"

