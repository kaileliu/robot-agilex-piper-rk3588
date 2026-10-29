#!/usr/bin/env bash
# Build & run the S10 (DeepRobotics Lynx S10 dog) robonix container.
#
# 与 piper 那份（../piper/run.sh）同一套路，区别只在镜像/容器名和"什么烤进镜像"：
#   - 镜像 s10-robonix / 容器 s10_robonix_dev，与 piper 那套互不覆盖
#   - **不挂载仓库**：packages/ 与 deploy/ 由 Dockerfile 末尾几层烤进 /root/workspace。
#     跑栈产生的 rbnx-build/（每包一个）、rbnx-boot/（清单目录旁）都落在容器自己身上，
#     宿主仓库保持干净。改了源码 → 重建镜像（只重建末尾那几层）→ 重建容器。
#   - 密钥不进镜像：deploy 下的 *.local.yaml 被 .dockerignore 挡住，容器建好后由本
#     脚本 docker cp 送进去；每次跑本脚本都覆盖一次，改了密钥重跑一下即可。
#   - 入口脚本（zenoh 路由器 / rbnx setup）也烤在镜像里，不再挂载。
#
# The container is created DETACHED (runs `sleep infinity`); attach via
# `docker exec`. Host CAN + USB camera device passthrough (rules identical
# to the piper container: Orbbec 189/81 majors + ALSA 116; the D435i uses
# libusb (189) and UVC (81)).
#
# Usage:
#   ./run.sh                 # build image if missing, ensure container, attach
#   ./run.sh -b              # force rebuild image, ensure container, attach
#   ./run.sh -e "CMD …"      # ensure container, run CMD non-interactively
#   ./run.sh -b -e "CMD …"   # rebuild, then run CMD non-interactively
#   ./run.sh -d              # delete the container
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"     # <repo>/containers/s10
DEPLOY_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"  # <repo>
IMAGE_NAME="${IMAGE_NAME:-s10-robonix:humble-arm64}"
CONTAINER_NAME="${CONTAINER_NAME:-s10_robonix_dev}"
RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_zenoh_cpp}"
LOCAL_MANIFEST="${LOCAL_MANIFEST:-$DEPLOY_ROOT/deploy/s10/manifest.local.yaml}"

FORCE_BUILD=false
EXEC_MODE=false
EXEC_CMD=""
while [ $# -gt 0 ]; do
    case "$1" in
        -b|--build)  FORCE_BUILD=true; shift ;;
        -d|--delete) docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
                     echo "[*] removed container $CONTAINER_NAME"; exit 0 ;;
        -e|--exec)   EXEC_MODE=true; shift; EXEC_CMD="$*"; break ;;
        -h|--help)   echo "Usage: $0 [-b|--build] [-e|--exec \"CMD …\"] | -d|--delete"; exit 0 ;;
        *)           echo "Unknown option: $1"; exit 1 ;;
    esac
done

# ── Build image if missing (or forced) ─────────────────────────────────
# 上下文是**仓库根**：Dockerfile 末尾要 COPY packages/ 和 deploy/
if $FORCE_BUILD || ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
    echo "[*] Building $IMAGE_NAME (base=piper-robonix:humble-arm64, context=$DEPLOY_ROOT) …"
    docker build -f "$SCRIPT_DIR/Dockerfile" -t "$IMAGE_NAME" "$DEPLOY_ROOT"
fi

# ── Ensure a detached, persistent container exists and is running ──────
# 容器是照某个镜像的快照建的：源码改了、镜像重建之后，旧容器里还是老内容。
# 这里自动把它换成新镜像的容器（先尽力停掉里面的栈，再重建）。
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    if [ "$(docker inspect -f '{{.Image}}' "$CONTAINER_NAME")" != \
         "$(docker inspect -f '{{.Id}}' "$IMAGE_NAME")" ]; then
        echo "[*] $CONTAINER_NAME 用的镜像已过期 → 换成新镜像的容器"
        docker exec "$CONTAINER_NAME" bash /docker-entrypoint.sh bash -c \
            "cd /root/workspace && rbnx shutdown" >/dev/null 2>&1 || true
        docker rm -f "$CONTAINER_NAME" >/dev/null
    fi
fi
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    docker start "$CONTAINER_NAME" >/dev/null 2>&1 || true
else
    echo "[*] Creating detached container $CONTAINER_NAME …"
    docker run -d \
        --name "$CONTAINER_NAME" \
        --hostname s10-robonix \
        --net host \
        --device-cgroup-rule='c 81:* rmw' \
        --device-cgroup-rule='c 189:* rmw' \
        --device-cgroup-rule='c 116:* rmw' \
        -v /dev:/dev \
        -v /var/run/docker.sock:/var/run/docker.sock \
        -e RMW_IMPLEMENTATION="$RMW_IMPLEMENTATION" \
        -e ROBONIX_META_GRPC_ADDR="0.0.0.0:50051" \
        -e ROBONIX_META_GRPC_ENDPOINT="127.0.0.1:50051" \
        "$IMAGE_NAME" \
        sleep infinity >/dev/null
fi

# ── 把本地密钥清单送进容器（密钥从不进镜像）─────────────────────────────
if [ -f "$LOCAL_MANIFEST" ]; then
    docker cp "$LOCAL_MANIFEST" "$CONTAINER_NAME:/root/workspace/deploy/s10/manifest.local.yaml"
else
    echo "[!] 找不到 $LOCAL_MANIFEST —— 容器里只有模板清单（密钥是占位符），boot 前先补上"
fi

# ── Dispatch: one-off command (non-interactive) or interactive shell ───
if $EXEC_MODE; then
    exec docker exec "$CONTAINER_NAME" bash /docker-entrypoint.sh bash -c "$EXEC_CMD"
fi

echo "[*] Attaching to $CONTAINER_NAME"
exec docker exec -it "$CONTAINER_NAME" bash /docker-entrypoint.sh
