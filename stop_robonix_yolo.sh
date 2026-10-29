#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
CONTAINER_NAME="${ROBONIX_YOLO_CONTAINER_NAME:-robonix_yolo_dev}"

if docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
    MOUNT_ROOT="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/root/workspace"}}{{.Source}}{{end}}{{end}}' "$CONTAINER_NAME")"
    if [[ "$MOUNT_ROOT" != "$ROOT" ]]; then
        echo "ERROR: refusing to stop $CONTAINER_NAME mounted from $MOUNT_ROOT" >&2
        exit 1
    fi
    # Stop Robonix consumers before stopping their inference backend.
    docker exec "$CONTAINER_NAME" pkill -INT -f '^rbnx boot ' 2>/dev/null || true
    docker stop --time 10 "$CONTAINER_NAME" >/dev/null
fi
if systemctl is-active --quiet robonix-yolo-stack.service; then
    systemctl stop robonix-yolo-stack.service
fi
if systemctl is-active --quiet robonix-yolo-npu.service; then
    systemctl stop robonix-yolo-npu.service
fi
if systemctl is-active --quiet robonix-yolo-resources.service; then
    systemctl stop robonix-yolo-resources.service
fi
echo "[robonix_yolo] stopped; the container and build outputs are retained"
