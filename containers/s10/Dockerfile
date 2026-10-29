# S10 (DeepRobotics Lynx S10 dog) robonix container image.
#
# 叠在 piper 那层之上（`containers/piper/Dockerfile` 建出来的 ROS 2 + Rust +
# gRPC + robonix 工具链），不另起一套基座：同一台机器（aarch64）上那套已经验过。
#
# 基座镜像名由 BASE_IMAGE 给，默认是本仓库自己那条线（`-std-` 后缀那对标签，
# 与旧镜像 `piper-robonix:humble-arm64` / `s10-robonix:humble-arm64` 并存、互不覆盖）：
#     docker build -f containers/piper/Dockerfile -t piper-robonix:std-humble-arm64 .
#     docker build -f containers/s10/Dockerfile \
#         --build-arg BASE_IMAGE=piper-robonix:std-humble-arm64 \
#         -t s10-robonix:std-humble-arm64 containers/s10
# 想直接复用已有的旧基座也行：--build-arg BASE_IMAGE=piper-robonix:humble-arm64
ARG BASE_IMAGE=piper-robonix:std-humble-arm64

# 构建上下文 = 仓库根（不是本目录）：最后几层要把 packages/ 和 deploy/ 拷进镜像，
# 所以用 `docker build -f containers/s10/Dockerfile -t … .`（run.sh 已经这么做了）。
# .dockerignore 会挡住 *.local.yaml（密钥不进镜像）、packages/*/rbnx-build/（编译产物）、
# .git 等——所以镜像里那两份内容是干净的源码。
#
# What S10 needs beyond piper:
#   1. pyrealsense2 (aarch64, cp310) — the D435i manual-AE primitive
#      (primitive-intel-d435i-manual-ae-camera-rbnx) captures via the SDK.
#      The wheel is STATICALLY linked against librealsense (checked with ldd:
#      only libusb/udev/libstdc++), so copying the package straight into
#      dist-packages is sufficient — no librealsense2 system package needed.
#      Source: s10 roboarm venv (docker-cp'd from the old Jetson container).
#      → 仓库内路径 containers/s10/payloads/pyrealsense2/（无公开下载源，
#        见同目录 README 的退路说明）。
#   2. requests — used by skill-internvla-navigation-rbnx's scripts
#      (health check / HTTP calls); the piper image does not carry it.
#   3. docker CLI + compose 插件 — skill-internvla-navigation-rbnx/scripts/
#      start.sh 会在容器内执行 `docker compose up -d`，经 /var/run/docker.sock
#      (见 run.sh) 在宿主上拉起 InternVLA server。构建时从镜像源取官方 .deb
#      解包安装（不落仓库、版本可 pin）。
#
# No GPU/ML deps: InternVLA inference runs host-side from the
# mujue37/internvla-server image (like the host-side YOLO server), so this
# image stays CPU-only like piper's.
FROM ${BASE_IMAGE}

COPY containers/s10/payloads/pyrealsense2/ /usr/local/lib/python3.10/dist-packages/pyrealsense2/

# pillow: realsense_camera 的 snapshot/depth_snapshot JPEG 转换用（旧机容器
# 手动装过没进镜像，迁移后丢失 → 2026-09-09 补进）。
RUN pip3 install --no-cache-dir --timeout 120 --retries 5 requests pillow

# ── docker CLI + compose 插件 ───────────────────────────────────────────
# 取官方 .deb 解包安装（不往仓库里放二进制载荷）。
#   源：TUNA 的 docker-ce apt 池（= 本镜像 apt 已在用的镜像站）。
#   官方 download.docker.com 在该网络实测连不通（连接超时），故走镜像。
#   不做 apt-get install：避免往镜像里加 docker apt 源与信任链，
#   直接 curl + dpkg-deb -x 更可控，且版本可以用 build-arg pin 死。
# 静态包（static/stable/docker-*.tgz）里**不含 compose 插件**，故 composer
# 单独取（plugin 是独立 deb）。
ARG DEB_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/docker-ce/linux/ubuntu/dists/jammy/pool/stable/arm64
ARG DOCKER_CE_CLI_DEB=docker-ce-cli_29.8.0-1~ubuntu.22.04~jammy_arm64.deb
ARG DOCKER_COMPOSE_DEB=docker-compose-plugin_5.5.1-1~ubuntu.22.04~jammy_arm64.deb
RUN set -eux;     curl -fsSL --retry 3 --connect-timeout 20 "$DEB_MIRROR/$DOCKER_CE_CLI_DEB" -o /tmp/cli.deb;     curl -fsSL --retry 3 --connect-timeout 20 "$DEB_MIRROR/$DOCKER_COMPOSE_DEB" -o /tmp/compose.deb;     dpkg-deb -x /tmp/cli.deb /tmp/dcli;     dpkg-deb -x /tmp/compose.deb /tmp/dcomp;     install -m 0755 /tmp/dcli/usr/bin/docker /usr/local/bin/docker;     mkdir -p /usr/libexec/docker/cli-plugins;     install -m 0755 /tmp/dcomp/usr/libexec/docker/cli-plugins/docker-compose                     /usr/libexec/docker/cli-plugins/docker-compose;     rm -rf /tmp/cli.deb /tmp/compose.deb /tmp/dcli /tmp/dcomp;     docker --version && docker compose version

# ── 运行时那两份内容，烤在最后几层 ──────────────────────────────────────
# 为什么烤：容器不再挂载仓库——跑栈产生的 rbnx-build/（每包一个）、rbnx-boot/
# （清单目录旁）都落在容器自己身上，宿主仓库保持干净。
# 为什么放最后：packages/ 改得最勤。放最后，改包只重建末尾这两层，
# 上面装 ROS / Rust / 编译 robonix 的层都命中缓存。
COPY containers/piper/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh
COPY packages/ /root/workspace/packages/
COPY deploy/ /root/workspace/deploy/
ENTRYPOINT ["/docker-entrypoint.sh"]
