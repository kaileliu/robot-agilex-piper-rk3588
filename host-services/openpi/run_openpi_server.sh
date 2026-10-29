#!/usr/bin/env bash
# SPDX-License-Identifier: MulanPSL-2.0
# Fast-start the host-side openpi VLA policy server (VLA seam, :8002).
#
# Mirrors run_funasr_server.sh (:8771) and yolo_detect_server.py (:8770): the
# robonix container is DL-free, so the torch/JAX pi0.5 policy runs HERE on the
# host and the container's openvla_client skill reaches it over
# http://127.0.0.1:8002 (--net host). VLA is PARKED (pick stays mode: pipeline),
# so this is only needed for a direct robonix/skill/vla/execute call.
#
# UNLIKE the YOLO weight (vendored at host-services/yolo/weights/best.pt), the
# openpi framework (~hundreds of MB) and the pi05_piper checkpoint (GB-scale)
# are NOT vendored — they live on the host and are pointed to by the env vars
# below. Override any of them to relocate without editing this script.
set -euo pipefail

HS="$(cd "$(dirname "$0")" && pwd)"          # host-services/openpi
# openpi checkout (contains scripts/serve_policy.py + its own CUDA/JAX venv).
# 默认放在本子目录下（setup_openpi.sh 会 clone 到这里；已被 .gitignore 忽略）。
: "${OPENPI_DIR:=$HS/openpi-src}"
: "${OPENPI_PY:=$OPENPI_DIR/.venv/bin/python}"
# Trained pi0.5 Piper checkpoint directory (--policy.dir).
: "${OPENPI_POLICY_CONFIG:=pi05_piper}"
# ⚠️ 这个 checkpoint（GB 级）没有随仓库迁移：在旧机器上，本机没有 → 需要自己准备
# （或把旧机的 /home/czn/piper_full/full_pytorch 拷过来），再显式设 OPENPI_POLICY_DIR。
: "${OPENPI_POLICY_DIR:=}"
: "${OPENPI_SERVER_PORT:=8002}"

[[ -x "$OPENPI_PY" ]]            || { echo "error: openpi venv python not found: $OPENPI_PY (先跑 setup_openpi.sh，或设 OPENPI_PY / OPENPI_DIR)" >&2; exit 1; }
[[ -f "$OPENPI_DIR/scripts/serve_policy.py" ]] || { echo "error: serve_policy.py not found under $OPENPI_DIR/scripts (set OPENPI_DIR)" >&2; exit 1; }
[[ -n "$OPENPI_POLICY_DIR" && -d "$OPENPI_POLICY_DIR" ]] || { echo "error: policy checkpoint dir not found: ${OPENPI_POLICY_DIR:-<unset>} (set OPENPI_POLICY_DIR; 该 checkpoint 未随仓库带)" >&2; exit 1; }

echo "[run] openpi VLA policy server → http://127.0.0.1:$OPENPI_SERVER_PORT"
echo "[run]   config=$OPENPI_POLICY_CONFIG  dir=$OPENPI_POLICY_DIR"
echo "[run]   (VLA is parked; only needed for a direct vla/execute call)"

# serve_policy.py resolves its --policy.config against the openpi package, so run
# from the checkout root with the relative scripts/ path (matches the original
# command baked in skill-vla-openpi-rbnx/package_manifest.yaml).
cd "$OPENPI_DIR"
exec "$OPENPI_PY" scripts/serve_policy.py --port "$OPENPI_SERVER_PORT" \
    policy:checkpoint --policy.config "$OPENPI_POLICY_CONFIG" --policy.dir "$OPENPI_POLICY_DIR"
