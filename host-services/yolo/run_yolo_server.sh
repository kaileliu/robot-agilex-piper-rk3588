#!/usr/bin/env bash
# Compatibility entry point for the copied Robonix deployment.
# The detector is now served by RK3588 NPU resources under yolo_npu/.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
exec bash "$ROOT/yolo_npu/run_yolo_npu.sh" "$@"
