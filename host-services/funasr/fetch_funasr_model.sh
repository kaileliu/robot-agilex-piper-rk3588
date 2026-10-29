#!/usr/bin/env bash
# SPDX-License-Identifier: MulanPSL-2.0
# Fetch the FunASR paraformer-large zh online-streaming model into the local
# modelscope cache, so the host FunASR server (:8771) can load it OFFLINE
# (AutoModel(..., hub_kwargs={"local_files_only": True})).
#
# WHY (self-contained)
# --------------------
# The model weights (~849 MB) are too large to vendor in git, but they ARE
# reconstructible: this script pulls them from modelscope on demand. That keeps
# the repo self-contained-by-script (clone + run this) without bloating history.
#
# Idempotent: if the model dir already exists and is non-empty, we skip. Uses
# this service's own venv (funasr/.venv, built by setup_venv.sh — modelscope
# 是它装的依赖之一).
set -euo pipefail

HS="$(cd "$(dirname "$0")" && pwd)"          # host-services/funasr
VENV="${FUNASR_VENV:-$HS/.venv}"             # 本服务自己的 venv（setup_venv.sh 建）
PY="$VENV/bin/python"

# 目标目录必须与 run_funasr_server.sh 的默认一致：host-services/funasr/model/<模型名>
: "${FUNASR_MODEL_ID:=iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online}"
: "${FUNASR_MODEL:=$HS/model/${FUNASR_MODEL_ID##*/}}"

if [[ -d "$FUNASR_MODEL" ]] && [[ -n "$(ls -A "$FUNASR_MODEL" 2>/dev/null)" ]]; then
    echo "[fetch] model already present, skipping: $FUNASR_MODEL"
    exit 0
fi

if [[ ! -x "$PY" ]]; then
    echo "[fetch] venv missing → building it once via funasr/setup_venv.sh"
    bash "$HS/setup_venv.sh"
fi

echo "[fetch] downloading FunASR model via modelscope: $FUNASR_MODEL_ID"
"$PY" - "$FUNASR_MODEL_ID" "$FUNASR_MODEL" <<'PY'
import shutil, sys
from pathlib import Path
from modelscope import snapshot_download
src = Path(snapshot_download(sys.argv[1]))
dst = Path(sys.argv[2])
print("[fetch]   downloaded to:", src)
if src.resolve() != dst.resolve():
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, dirs_exist_ok=True)
    print("[fetch]   copied into:", dst)
PY

echo "[fetch] done. Model dir: $FUNASR_MODEL"
echo "[fetch]   start server: bash $HS/run_funasr_server.sh"
