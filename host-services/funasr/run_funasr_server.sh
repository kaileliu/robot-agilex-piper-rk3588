#!/usr/bin/env bash
# SPDX-License-Identifier: MulanPSL-2.0
# Fast-start the host-side FunASR streaming-ASR server (speech seam, :8771).
#
# Mirrors host-services/yolo/yolo_detect_server.py (:8770): the robonix
# container is DL-free, so the torch+CUDA FunASR model runs HERE on the host and
# the container's speech service reaches it over ws://127.0.0.1:8771 (--net host).
#
# The venv is this service's own host-services/funasr/.venv, built by
# funasr/setup_venv.sh. 那个脚本按板子挑 requirements（Orin / Thor 的 torch
# 轮子不同）。venv 不在时这里会自动建一次，然后 exec 服务器。
#
# Override FUNASR_VENV to point at a different venv if you already have one.
set -euo pipefail

HS="$(cd "$(dirname "$0")" && pwd)"          # host-services/funasr
VENV="${FUNASR_VENV:-$HS/.venv}"             # 本服务自己的 venv（setup_venv.sh 建）
PY="$VENV/bin/python"

# The paraformer-large zh online-streaming model DIRECTORY (not model.pt).
# AutoModel(model=<dir>, hub_kwargs={"local_files_only": True}) loads it offline.
# 模型随仓库放在 host-services/funasr/model/ 下（849 MB，不入 git）；
# 手工删掉后用 fetch_funasr_model.sh 重新拉。
if [[ -z "${FUNASR_MODEL:-}" ]]; then
    FUNASR_MODEL="$HS/model/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online"
fi
: "${FUNASR_SERVER_HOST:=127.0.0.1}"
: "${FUNASR_SERVER_PORT:=8771}"
export FUNASR_MODEL FUNASR_SERVER_HOST FUNASR_SERVER_PORT

[[ -d "$FUNASR_MODEL" ]] || { echo "error: FUNASR_MODEL dir not found: $FUNASR_MODEL" >&2
    echo "       模型未随仓库带（或已被删除）→ 先跑 bash $HS/fetch_funasr_model.sh" >&2; exit 1; }

# Auto-build the shared GPU venv on first run (idempotent; rebuilds from scratch).
if [[ ! -x "$PY" ]]; then
    echo "[run] venv missing → building it once via funasr/setup_venv.sh"
    bash "$HS/setup_venv.sh"
fi

# 设备选择：显式设了 FUNASR_DEVICE 就听它的；没设就问 torch 认不认本机 GPU。
# 为什么非问不可：2026-09-10 在本机 Orin(sm_87) 上，PyPI 的 cu126 轮子只编了
# sm_80/sm_90 —— 模型能加载到 cuda、却到第一次识别才报 "no kernel image is
# available"，很迷惑。认不出就自动退 CPU（实测每 600ms 音频推理 ~0.95s，
# 约 1.6× 慢于实时）。装了含 sm_87 的 NVIDIA Jetson 轮子后（见
# torch-orin.txt），这里会自动选回 cuda。
if [[ -z "${FUNASR_DEVICE:-}" ]]; then
    FUNASR_DEVICE="$("$PY" -c '
import torch
ok = False
try:
    ok = torch.cuda.is_available() and ("sm_%d%d" % torch.cuda.get_device_capability(0)) in torch.cuda.get_arch_list()
except Exception:
    ok = False
print("cuda" if ok else "cpu")' 2>/dev/null || echo cpu)"
    echo "[run] FUNASR_DEVICE 未指定 → 自动判定: $FUNASR_DEVICE"
fi
export FUNASR_DEVICE

echo "[run] FunASR ASR server → ws://$FUNASR_SERVER_HOST:$FUNASR_SERVER_PORT"
echo "[run]   model=$FUNASR_MODEL"
echo "[run]   device=$FUNASR_DEVICE  (CPU streaming lags real time; want cuda)"
exec "$PY" "$HS/funasr_asr_server.py" \
    --host "$FUNASR_SERVER_HOST" --port "$FUNASR_SERVER_PORT"
