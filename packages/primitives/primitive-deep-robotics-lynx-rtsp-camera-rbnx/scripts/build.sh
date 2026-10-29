#!/usr/bin/env bash
# Build: rbnx codegen only (MCP types + ROS2 IDL). 无第三方驱动要编译 —
# 拉流用容器自带 python3-opencv (CAP_FFMPEG RTSP 解码)。
set -euo pipefail
PKG="${RBNX_PACKAGE_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$PKG"
CLEAN="${RBNX_BUILD_CLEAN:-}"

if [[ "$CLEAN" == "1" ]]; then
    echo "[lynx_rtsp_camera/build] clean: removing rbnx-build/"
    rm -rf rbnx-build
fi
mkdir -p rbnx-build

FLAGS=(--mcp --ros2 --out-dir "$PKG/rbnx-build/codegen")
[[ "$CLEAN" == "1" ]] && FLAGS+=(--clean)
RBNX="${RBNX_BIN:-$(command -v rbnx 2>/dev/null || ls /root/workspace/.robonix-build/target/release/rbnx 2>/dev/null)}"
echo "[lynx_rtsp_camera/build] rbnx codegen ${FLAGS[*]}"
"$RBNX" codegen -p "$PKG" "${FLAGS[@]}"

ROS2_IDL="$PKG/rbnx-build/codegen/ros2_idl"
if [[ -f "$ROS2_IDL/setup.py" || -f "$ROS2_IDL/package.xml" ]]; then
    echo "[lynx_rtsp_camera/build] colcon build (Robonix ROS 2 interfaces)"
    (cd "$ROS2_IDL" && colcon build)
fi

touch "$PKG/rbnx-build/.rbnx-built"
echo "[lynx_rtsp_camera/build] done."
