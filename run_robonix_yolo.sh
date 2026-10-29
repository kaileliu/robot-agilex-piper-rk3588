#!/usr/bin/env bash
# Start the RK3588 NPU detector and the copied Robonix Piper deployment.
# The source deployment under /root/robot-agilex-piper-ga is never touched.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
YOLO_ROOT="$ROOT/yolo_npu"
LOG_DIR="${ROBONIX_YOLO_LOG_DIR:-$ROOT/run-logs}"
UNIT="robonix-yolo-npu.service"
HOST="${YOLO_HOST:-127.0.0.1}"
PORT="${YOLO_PORT:-8770}"

mkdir -p "$LOG_DIR"

if [[ "${1:-}" == "" || "${1:-}" == "--background" ]]; then
    if [[ -f "$ROOT/run-logs/calibration.requires-validation" ]]; then
        echo "[robonix_yolo] 当前标定的独立验证未通过，请先核对采点并通过验证；详见 reports/calibration_fit.md" >&2
        exit 1
    fi
    if [[ -f "$ROOT/run-logs/calibration.active" ]]; then
        echo "[robonix_yolo] 当前为标定模式，请先完成标定并执行 bash calibrate_robonix_yolo.sh --stop" >&2
        exit 1
    fi
fi

health_device() {
    curl -fsS --max-time 2 "http://${HOST}:${PORT}/health" 2>/dev/null \
        | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin).get("device", ""))' \
        2>/dev/null || true
}

start_npu() {
    local device
    device="$(health_device)"
    if [[ "$device" == "rk3588-npu" ]] && systemctl is-active --quiet "$UNIT"; then
        echo "[robonix_yolo] NPU YOLO already healthy at http://${HOST}:${PORT}"
        return 0
    fi
    if [[ -n "$device" ]]; then
        echo "[robonix_yolo] ERROR: ${HOST}:${PORT} is occupied by device=${device}" >&2
        echo "[robonix_yolo] Stop the old detector first, then rerun this script." >&2
        return 1
    fi

    echo "[robonix_yolo] starting RK3588 NPU YOLO ..."
    systemctl reset-failed "$UNIT" 2>/dev/null || true
    systemd-run --quiet --collect --unit="$UNIT" \
        --working-directory="$ROOT" \
        --property="StandardOutput=append:$LOG_DIR/yolo_npu.log" \
        --property="StandardError=append:$LOG_DIR/yolo_npu.log" \
        --setenv="YOLO_HOST=$HOST" --setenv="YOLO_PORT=$PORT" \
        --setenv="YOLO_CONF=${YOLO_CONF:-0.8}" --setenv="YOLO_IOU=${YOLO_IOU:-0.45}" \
        --setenv="YOLO_NPU_MODEL=${YOLO_NPU_MODEL:-$YOLO_ROOT/models/best_736x1280_heads_fp16.rknn}" \
        --setenv="YOLO_NPU_CORES=${YOLO_NPU_CORES:-7}" \
        /bin/bash "$YOLO_ROOT/run_yolo_npu.sh"

    for _ in $(seq 1 40); do
        if [[ "$(health_device)" == "rk3588-npu" ]]; then
            echo "[robonix_yolo] NPU YOLO ready: http://${HOST}:${PORT}"
            return 0
        fi
        if ! systemctl is-active --quiet "$UNIT"; then
            echo "[robonix_yolo] ERROR: NPU YOLO exited during startup" >&2
            tail -80 "$LOG_DIR/yolo_npu.log" >&2 || true
            return 1
        fi
        sleep 1
    done
    echo "[robonix_yolo] ERROR: NPU YOLO did not become healthy within 40s" >&2
    tail -80 "$LOG_DIR/yolo_npu.log" >&2 || true
    return 1
}

case "${1:-}" in
    --background)
        if systemctl is-active --quiet robonix-yolo-stack.service; then
            echo "[robonix_yolo] Robonix startup service is already active; see $LOG_DIR/boot.log"
            exit 0
        fi
        systemctl reset-failed robonix-yolo-stack.service 2>/dev/null || true
        systemd-run --quiet --collect --unit=robonix-yolo-stack.service \
            --working-directory="$ROOT" \
            --property="StandardOutput=append:$LOG_DIR/launcher.log" \
            --property="StandardError=append:$LOG_DIR/launcher.log" \
            /bin/bash "$ROOT/run_robonix_yolo.sh"
        echo "[robonix_yolo] background startup requested; see $LOG_DIR/boot.log"
        exit 0
        ;;
    --yolo-only)
        start_npu
        exit 0
        ;;
    --check)
        device="$(health_device)"
        printf 'yolo_device=%s\n' "$device"
        [[ "$device" == "rk3588-npu" ]] || exit 1
        exit 0
        ;;
    "")
        ;;
    *)
        echo "Usage: $0 [--background|--yolo-only|--check]" >&2
        exit 2
        ;;
esac

if [[ "$PORT" != "8770" || "$HOST" != "127.0.0.1" ]]; then
    echo "[robonix_yolo] ERROR: the existing Robonix manifest requires 127.0.0.1:8770" >&2
    exit 1
fi

# Use a separate container name so an old container created from the original
# repository cannot accidentally mount the original tree.
export CONTAINER_NAME="${ROBONIX_YOLO_CONTAINER_NAME:-robonix_yolo_dev}"
export IMAGE_NAME="${ROBONIX_YOLO_IMAGE_NAME:-piper-robonix:std-humble-arm64}"

if docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
    MOUNT_ROOT="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/root/workspace"}}{{.Source}}{{end}}{{end}}' "$CONTAINER_NAME")"
    if [[ "$MOUNT_ROOT" != "$ROOT" ]]; then
        echo "ERROR: $CONTAINER_NAME mounts $MOUNT_ROOT; expected $ROOT" >&2
        exit 1
    fi
fi
start_npu
if docker exec "$CONTAINER_NAME" pgrep -f '^rbnx boot ' >/dev/null 2>&1; then
    echo "[robonix_yolo] Robonix is already running in $CONTAINER_NAME"
    exec docker exec "$CONTAINER_NAME" rbnx caps
fi
/usr/bin/python3 "$ROOT/scripts/prepare_can.py"

echo "[robonix_yolo] booting Robonix from $ROOT"
"$ROOT/containers/piper/run.sh" -e \
    "cd /root/workspace && exec rbnx boot -f deploy/piper/manifest.local.yaml --no-update-check --verbose" \
    2>&1 | tee "$LOG_DIR/boot.log"
