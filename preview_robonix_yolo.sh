#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
CONTAINER_NAME="${ROBONIX_YOLO_CONTAINER_NAME:-robonix_yolo_dev}"
HOST="${ROBONIX_PREVIEW_HOST:-0.0.0.0}"
PORT="${ROBONIX_PREVIEW_PORT:-8088}"
FPS="${ROBONIX_PREVIEW_FPS:-3}"
CHECK_HOST="$HOST"
[[ "$CHECK_HOST" == "0.0.0.0" ]] && CHECK_HOST=127.0.0.1

if [[ "${1:-}" != "" && "${1:-}" != "--stop" ]]; then
    echo "Usage: $0 [--stop]" >&2
    exit 2
fi
MOUNT_ROOT="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/root/workspace"}}{{.Source}}{{end}}{{end}}' "$CONTAINER_NAME")"
if [[ "$MOUNT_ROOT" != "$ROOT" ]]; then
    echo "ERROR: $CONTAINER_NAME mounts $MOUNT_ROOT; expected $ROOT" >&2
    exit 1
fi
if [[ "${1:-}" == "--stop" ]]; then
    docker exec "$CONTAINER_NAME" pkill -TERM -f '^python3 /root/workspace/scripts/live_preview.py( |$)' 2>/dev/null || true
    echo "[preview] stopped"
    exit 0
fi
mkdir -p "$ROOT/run-logs"
bash "$ROOT/run_robonix_yolo.sh" --check
bash "$ROOT/resources_robonix_yolo.sh"
if ! docker exec "$CONTAINER_NAME" pgrep -f '^python3 /root/workspace/scripts/live_preview.py( |$)' >/dev/null; then
    docker exec -d "$CONTAINER_NAME" bash /docker-entrypoint.sh bash -c \
        'exec python3 "$@" >> /root/workspace/run-logs/preview.log 2>&1' preview \
        /root/workspace/scripts/live_preview.py --host "$HOST" --port "$PORT" --fps "$FPS"
fi
for _ in $(seq 1 25); do
    if curl --noproxy '*' -fsS --max-time 2 "http://$CHECK_HOST:$PORT/health" 2>/dev/null \
        | python3 -c 'import json,sys; s=json.load(sys.stdin); sys.exit(not(s.get("service")=="robonix-yolo-preview" and s.get("ok")))' 2>/dev/null; then
        echo "[preview] ready: http://$CHECK_HOST:$PORT"
        PREVIEW_PORT="$PORT" python3 - <<'PY'
import json, os, subprocess
interfaces = json.loads(subprocess.check_output(['ip', '-j', '-4', 'address', 'show', 'scope', 'global']))
for interface in interfaces:
    if interface['ifname'].startswith(('eth', 'en', 'wl')):
        for address in interface['addr_info']:
            print('[preview] LAN: http://' + address['local'] + ':' + os.environ['PREVIEW_PORT'])
PY
        exit 0
    fi
    sleep 1
done
echo "[preview] not ready; see $ROOT/run-logs/preview.log" >&2
exit 1
