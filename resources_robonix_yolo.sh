#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
UNIT=robonix-yolo-resources.service
if [[ "${1:-}" == "--stop" ]]; then
    systemctl stop "$UNIT" 2>/dev/null || true
    exit 0
elif [[ -n "${1:-}" ]]; then
    echo "Usage: $0 [--stop]" >&2
    exit 2
fi
if ! systemctl is-active --quiet "$UNIT"; then
    mkdir -p "$ROOT/run-logs/resources"
    systemctl reset-failed "$UNIT" 2>/dev/null || true
    systemd-run --quiet --collect --unit="$UNIT" \
        --working-directory="$ROOT" \
        --property="StandardOutput=append:$ROOT/run-logs/resources-service.log" \
        --property="StandardError=append:$ROOT/run-logs/resources-service.log" \
        /usr/bin/python3 "$ROOT/scripts/resource_monitor.py"
fi
for _ in $(seq 1 20); do
    if curl --noproxy '*' -fsS --max-time 2 http://127.0.0.1:8771/health 2>/dev/null \
        | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(not(d.get("service")=="robonix-host-resources" and d.get("ok")))' 2>/dev/null; then
        echo "[resources] 每秒采样；记录：$ROOT/run-logs/resources/metrics.jsonl"
        exit 0
    fi
    sleep .5
done
echo "[resources] 启动失败；详见 run-logs/resources-service.log" >&2
exit 1
