#!/usr/bin/env bash
# Use the copied calibration tool with exclusive access to camera and CAN.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
CONTAINER="${ROBONIX_YOLO_CONTAINER_NAME:-robonix_yolo_dev}"
CALIB=/root/workspace/tools/calibration/piper
PATTERN="^$CALIB/.venv/bin/python $CALIB/app.py --port 8090$"
LOG_DIR="$ROOT/run-logs"
mkdir -p "$LOG_DIR"

MOUNT_ROOT="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/root/workspace"}}{{.Source}}{{end}}{{end}}' "$CONTAINER")"
if [[ "$MOUNT_ROOT" != "$ROOT" ]]; then
    echo "ERROR: $CONTAINER mounts $MOUNT_ROOT; expected $ROOT" >&2
    exit 1
fi

if [[ "${1:-}" == "--stop" ]]; then
    docker exec "$CONTAINER" pkill -TERM -f "$PATTERN" 2>/dev/null || true
    for _ in $(seq 1 30); do
        if ! docker exec "$CONTAINER" pgrep -f "$PATTERN" >/dev/null 2>&1; then
            rm -f "$LOG_DIR/calibration.active"
            echo "标定服务已停止。恢复抓取前请确认新标定已验证并写入配置。"
            exit 0
        fi
        sleep 0.2
    done
    echo "ERROR: 标定服务尚未退出，保留硬件占用标记" >&2
    exit 1
elif [[ -n "${1:-}" ]]; then
    echo "Usage: $0 [--stop]" >&2
    exit 2
fi

if docker exec "$CONTAINER" pgrep -f "$PATTERN" >/dev/null 2>&1; then
    curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8090/api/rig
    echo "标定页面：http://192.168.137.20:8090/handeye"
    exit 0
fi
if ss -H -ltn '( sport = :8090 )' | read -r _; then
    echo "ERROR: 8090 端口已被其他进程占用" >&2
    exit 1
fi
docker start "$CONTAINER" >/dev/null
docker exec "$CONTAINER" bash /docker-entrypoint.sh "$CALIB/.venv/bin/python" -c \
    'import sys; sys.path.insert(0,"/root/workspace/tools/calibration/piper"); import app; assert app.rig.has_intrinsics(), "先准备当前相机内参"; print("标定环境检查通过")'

printf '%s\n' 'calibration owns camera and CAN; runtime startup paused' > "$LOG_DIR/calibration.active"
bash "$ROOT/stop_robonix_yolo.sh"
/usr/bin/python3 "$ROOT/scripts/prepare_can.py"
docker start "$CONTAINER" >/dev/null
docker exec -d "$CONTAINER" bash /docker-entrypoint.sh bash -c \
    'cd /root/workspace/tools/calibration/piper && exec /root/workspace/tools/calibration/piper/.venv/bin/python /root/workspace/tools/calibration/piper/app.py --port 8090 >> /root/workspace/run-logs/calibration.log 2>&1'
for _ in $(seq 1 25); do
    if curl --noproxy '*' -fsS --max-time 2 http://127.0.0.1:8090/api/rig >/dev/null 2>&1; then
        echo "标定页面：http://192.168.137.20:8090/handeye"
        echo "chat 与 NPU 预览已暂停；启动标定服务本身不发送运动指令。"
        exit 0
    fi
    sleep 1
done
echo "ERROR: 标定服务启动失败，详见 $LOG_DIR/calibration.log" >&2
exit 1
