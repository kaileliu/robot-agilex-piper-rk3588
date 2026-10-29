#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
UNIT=robonix-yolo-client.service
if systemctl is-active --quiet "$UNIT"; then
    echo "Client already running on port 7860"
    exit 0
fi
mkdir -p "$ROOT/run-logs"
systemd-run --quiet --collect --unit="$UNIT" \
    --working-directory="$ROOT/robonix-client" \
    --property="Restart=on-failure" \
    --property="StandardOutput=append:$ROOT/run-logs/client.log" \
    --property="StandardError=append:$ROOT/run-logs/client.log" \
    "$ROOT/robonix-client/.venv/bin/robonix-client" \
    --host 0.0.0.0 --port 7860 --robot-host 127.0.0.1 --atlas-port 50051
