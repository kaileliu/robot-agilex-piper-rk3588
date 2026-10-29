#!/usr/bin/env bash
# SPDX-License-Identifier: MulanPSL-2.0
# 建 YOLO 检测服务自己的 venv：host-services/yolo/.venv
#
# 装两趟：
#   1) torch-orin.txt / torch-thor.txt —— 按板子选。Orin 是 sm_87，PyPI/镜像站上
#      aarch64 的轮子没编它的 kernel，得从 NVIDIA 的 Jetson 索引拿；Thor 是 sm_110，
#      镜像站的 cu130 轮子就行。这一份单独装，因为它的索引只给一个。
#   2) requirements.txt —— 其余依赖，走镜像站。
# 板子看 /proc/device-tree/model。已有 venv 就复用，不删。
set -euo pipefail

HS="$(cd "$(dirname "$0")" && pwd)"        # host-services/yolo
VENV="${YOLO_VENV:-$HS/.venv}"
# 缓存跟仓库放一起：Jetson 的根分区常很小（eMMC），而 uv 默认缓存写在 ~/.cache/uv，
# 下一个 3 GB 的 torch 就能把 / 撑满。默认落到 host-services/.uv-cache（两个服务共用）。
export UV_CACHE_DIR="${UV_CACHE_DIR:-$HS/../.uv-cache}"

command -v uv >/dev/null 2>&1 || { echo "error: 找不到 uv（装一个：python -m pip install uv）" >&2; exit 1; }

MODEL="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || true)"
case "$MODEL" in
    *Orin*) TORCH="$HS/torch-orin.txt" ;;
    *Thor*) TORCH="$HS/torch-thor.txt" ;;
    *RK3588*) TORCH="$HS/torch-cpu.txt" ;;
    *) echo "error: 认不出板子（/proc/device-tree/model = '$MODEL'）" >&2
       echo "       照 torch-orin.txt / torch-thor.txt 里的注释手工装" >&2
       exit 1 ;;
esac
echo "[setup] 板子：$MODEL → $(basename "$TORCH")"

# 环境里残留的索引设置会盖掉 requirements 文件里的 --index-url，清掉
unset UV_INDEX_URL UV_EXTRA_INDEX_URL || true
# 本机没有 py3.10 时 uv 会去下独立的 CPython（走 GitHub），默认挂 gh-proxy
: "${UV_PYTHON_INSTALL_MIRROR:=https://hk.gh-proxy.org/https://github.com/astral-sh/python-build-standalone/releases/download}"
export UV_PYTHON_INSTALL_MIRROR

if [ -x "$VENV/bin/python" ]; then
    echo "[setup] 复用已有环境 → $VENV"
else
    echo "[setup] 建 venv → $VENV（python 3.10）"
    uv venv --python 3.10 "$VENV"
fi

echo "[setup] 装 torch（约 3 GB 下载）"
uv pip install --python "$VENV/bin/python" -r "$TORCH"
echo "[setup] 装其余依赖"
uv pip install --python "$VENV/bin/python" -r "$HS/requirements.txt"

# Jetson 版 torch 要 libcudss.so.0：它装在 /usr/lib/aarch64-linux-gnu/libcudss/<版本>/ 里，
# 没登记进动态库缓存的话 import torch 直接 ImportError（这个坑踩过）
if [ -d /usr/lib/aarch64-linux-gnu/libcudss ] && ! ldconfig -p | grep -q libcudss; then
    echo "[setup] ⚠️ 系统里有 libcudss 但动态库缓存里没有，torch 会 import 失败。执行这条修一次："
    echo "[setup]    ls -d /usr/lib/aarch64-linux-gnu/libcudss/*/ | sudo tee /etc/ld.so.conf.d/cudss.conf >/dev/null && sudo ldconfig"
fi

echo "[setup] 自测"
"$VENV/bin/python" - <<'PY'
import torch, ultralytics
arch = torch.cuda.get_arch_list()
print("[setup]   torch", torch.__version__, "| cuda", torch.version.cuda, "| 编译的 arch:", arch)
print("[setup]   ultralytics", ultralytics.__version__)
if torch.cuda.is_available():
    cap = "sm_%d%d" % torch.cuda.get_device_capability(0)
    print("[setup]   GPU:", torch.cuda.get_device_name(0), cap)
    if cap not in arch:
        print("[setup]   ⚠️ 这份 torch 没有", cap, "的 kernel，跑推理会报 no kernel image is available")
PY

echo "[setup] 完成。启动：bash $HS/run_yolo_server.sh"
