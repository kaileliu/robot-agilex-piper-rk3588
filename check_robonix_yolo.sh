#!/usr/bin/env bash
set -euo pipefail
CONTAINER_NAME="${ROBONIX_YOLO_CONTAINER_NAME:-robonix_yolo_dev}"
exec docker exec "$CONTAINER_NAME" bash /docker-entrypoint.sh bash -c '
    source /root/workspace/packages/primitives/primitive-agilex-piper-arm-rbnx/rbnx-build/ws/install/setup.bash
    exec python3 /root/workspace/scripts/check_pipeline.py
'
