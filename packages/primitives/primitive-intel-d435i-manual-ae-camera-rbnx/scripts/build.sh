#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Build phase: rbnx codegen only（MCP types + ROS 2 IDL），无第三方驱动要编译。
#
# 为什么没有驱动构建：本原语不通过 ROS 驱动取流——它用容器自带的
# pyrealsense2（Python SDK，见 docker/s10/payloads/pyrealsense2）直接打开
# USB 相机（UVC），曝光由 main.py 里的手动 AE 控制。因此这里既不需要 Intel 的
# librealsense2 系统包，也不需要 colcon 编译 realsense2_camera。
#
# 产物：rbnx-build/{codegen,data}/；start.sh 会在存在时 source
# rbnx-build/codegen/ros2_idl/install/setup.bash。
set -euo pipefail
PKG="${RBNX_PACKAGE_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$PKG"
CLEAN="${RBNX_BUILD_CLEAN:-}"

if [[ "$CLEAN" == "1" ]]; then
    echo "[realsense_camera/build] clean: removing rbnx-build/"
    rm -rf rbnx-build
fi
mkdir -p rbnx-build/data

ROS_DISTRO="${ROS_DISTRO:-humble}"
# shellcheck disable=SC1091
set +u; source "/opt/ros/${ROS_DISTRO}/setup.bash"; set -u

FLAGS=(--mcp --ros2 --out-dir "$PKG/rbnx-build/codegen")
[[ "$CLEAN" == "1" ]] && FLAGS+=(--clean)
echo "[realsense_camera/build] rbnx codegen ${FLAGS[*]}"
rbnx codegen -p "$PKG" "${FLAGS[@]}"

ROS2_IDL="$PKG/rbnx-build/codegen/ros2_idl"
echo "[realsense_camera/build] colcon build (Robonix ROS 2 interfaces)"
(cd "$ROS2_IDL" && colcon build)

touch "$PKG/rbnx-build/.rbnx-built"
echo "[realsense_camera/build] done."
