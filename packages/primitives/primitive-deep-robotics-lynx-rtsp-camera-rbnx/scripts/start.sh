#!/usr/bin/env bash
# Start: launch the rtsp_camera atlas bridge (纯 python, 无需 ROS 环境变量)。
set -euo pipefail
PKG="${RBNX_PACKAGE_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$PKG"

RBNX="${RBNX_BIN:-$(command -v rbnx 2>/dev/null || ls /root/workspace/.robonix-build/target/release/rbnx 2>/dev/null)}"
if ROBONIX_API="$("$RBNX" path robonix-api 2>/dev/null)"; then
    export PYTHONPATH="$ROBONIX_API:$PKG:$PKG/rbnx-build/codegen:${PYTHONPATH:-}"
fi

exec python3 -m rtsp_camera.main
