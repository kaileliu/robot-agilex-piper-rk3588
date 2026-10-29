#!/usr/bin/env bash
# Pass the user's prompt to the existing Robonix pilot/executor.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
CONTAINER_NAME="${ROBONIX_YOLO_CONTAINER_NAME:-robonix_yolo_dev}"
if [[ $# -eq 0 ]]; then
    echo 'Usage: bash ask_robonix.sh "你的机械臂指令"' >&2
    exit 2
fi
MOUNT_ROOT="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/root/workspace"}}{{.Source}}{{end}}{{end}}' "$CONTAINER_NAME")"
if [[ "$MOUNT_ROOT" != "$ROOT" ]]; then
    echo "ERROR: $CONTAINER_NAME mounts $MOUNT_ROOT; expected $ROOT" >&2
    exit 1
fi
bash "$ROOT/run_robonix_yolo.sh" --check
exec docker exec "$CONTAINER_NAME" bash /docker-entrypoint.sh rbnx ask "$@"
