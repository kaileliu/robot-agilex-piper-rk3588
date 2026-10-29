#!/usr/bin/env bash
# SPDX-License-Identifier: MulanPSL-2.0
#
# Build phase for skill-vla-openpi-rbnx.
# Pure Python skill — no colcon, no vendored ROS packages.
# Codegen (MCP types for VlaExecute) + ensure openpi_client is importable.
set -euo pipefail
PKG="${RBNX_PACKAGE_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$PKG"
CLEAN="${RBNX_BUILD_CLEAN:-}"

if [[ "$CLEAN" == "1" ]]; then
    echo "[vla_openpi/build] clean: removing rbnx-build/"
    rm -rf rbnx-build
fi
mkdir -p rbnx-build

# Codegen — emits proto_gen/ + robonix_mcp_types/vla_client_mcp.py from VlaExecute.srv
if command -v rbnx &>/dev/null; then
    FLAGS=(--out-dir "$PKG/rbnx-build/codegen" --mcp)
    [[ "$CLEAN" == "1" ]] && FLAGS+=(--clean)
    echo "[vla_openpi/build] rbnx codegen ${FLAGS[*]}"
    rbnx codegen -p "$PKG" "${FLAGS[@]}" || true
fi

# Ensure openpi_client is importable (light dep: websockets + msgpack + numpy).
# Prefer an editable install from the host openpi checkout if visible in-container.
OPENPI_CLIENT_DIR="${OPENPI_CLIENT_DIR:-/home/czn/openpi/packages/openpi-client}"
if ! python3 -c "import openpi_client" 2>/dev/null; then
    if [[ -d "$OPENPI_CLIENT_DIR" ]]; then
        echo "[vla_openpi/build] installing openpi_client from $OPENPI_CLIENT_DIR"
        pip install -e "$OPENPI_CLIENT_DIR" 2>&1 | tail -3 || \
            echo "[WARN] openpi_client install failed — install manually before boot"
    else
        echo "[WARN] openpi_client not importable and $OPENPI_CLIENT_DIR missing."
        echo "       Install with: pip install -e /home/czn/openpi/packages/openpi-client"
    fi
fi

# Soft-check other runtime deps.
python3 -c "import numpy, PIL" 2>/dev/null || \
    echo "[WARN] Missing: pip install numpy pillow"

touch "$PKG/rbnx-build/.rbnx-built"
echo "[vla_openpi/build] done."
