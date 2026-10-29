# S10 部署手册：从裸机 Jetson 到运行交互

> 从一台刚装好系统、还没装任何额外软件的 Jetson 开始，到能和 robonix 对话、让它抓取物体。

## 系统构成

| 位置 | 内容 |
|---|---|
| 宿主机（Jetson） | 模型服务（YOLO :8770、FunASR :8771）与标定台 :8080 |
| 容器 `s10_robonix_dev` | robonix 框架与 packages：原语（相机、机械臂、麦克风）、服务（检测、抓取位姿、逆运动学、语音）、技能（抓取） |
| 机器人本体 | D435i（装在机械臂上）、Piper 机械臂、麦克风等硬件 |

容器与宿主机共享网络（`--net host`）。

robonix 是这套栈的框架：它按清单启动各个包，并把能力分成三类——**原语**（primitive，直接操作硬件）、**服务**（service，做计算）、**技能**（skill，一段可调用的任务流程，内部调用原语和服务）。

---

## 0. 前置准备

硬件：

- Jetson（aarch64，Orin 或 Thor），系统能联网
- Piper 机械臂、USB-CAN 适配器、24V 电源
- Intel D435i（装在机械臂上）
- 一个 USB 麦克风（型号 UGREEN USB MIC-CM564）
- **一张棋盘格标定板**：附录 A 标定时要对着相机拍。`tools/calibration/boards/` 里有现成可打印的 A4/A3 版（打印时选「实际大小」，打完量一下页脚那条 100 mm 的线），不用去买

账号与密钥（第 6 节填进清单）：

- 一个能看图的大模型（给 `llm_detect` 的 VLM 后端用），以及一个纯文本或多模态大模型，两个用途：pilot 规划（pilot 是 robonix 的规划侧，把用户的一句话拆成能力调用）、抓取时把物品名匹配到 YOLO 的类名。这两处可以用同一个模型
- 腾讯云 TTS 的 SecretId/Key（可选。默认 TTS 使用 mock，不会有声音）

获取代码（后面各节都在这个目录里操作）：

```bash
git clone https://github.com/<你的仓库>.git piper-standard    # GitHub 慢时加 gh-proxy 前缀
cd piper-standard
```

---

## 1. 系统环境：驱动、CUDA、Python

先确认板子和系统：

```bash
uname -m                                  # 期望 aarch64
head -3 /etc/os-release                   # 期望 Ubuntu 22.04 或 24.04
cat /etc/nv_tegra_release 2>/dev/null || nvidia-smi | head -5   # 确认 JetPack/CUDA 在
df -h /                                   # 剩余空间 ≥ 40G
ping baidu.com                            # 能上网
```

**根分区小的话先想好放哪**：Jetson 的 eMMC 通常只有几十 G，而且往往已经用掉大半（`df -h /` 显示不到 40G 就别硬塞）。仓库、Python 环境、模型都放另一块盘上（测试机上放 `/mnt/ssd/wfw/`），后面所有命令在那个目录里执行。还有一处容易忽略：uv / pip 的下载缓存默认写在 `~/.cache`（也在根分区），下一个 3 GB 的 torch 就够把它撑满——`host-services/*/setup_venv.sh` 已经把 uv 缓存默认改到仓库旁的 `.uv-cache/`；手工用 pip 装东西时记得 `PIP_CACHE_DIR=/别的/盘/.pip-cache pip install …`。

**GPU 驱动**：Jetson 的驱动随 L4T 系统一起提供（L4T 是 NVIDIA 给 Jetson 的 Ubuntu 系统，驱动在 `nvidia-l4t-core` 这一组包里），不需要单独安装。这里要确认的是 CUDA 等组件是否装齐——**看目录，不要只看 `nvcc` 命令**：有时候 CUDA 装着、只是 `nvcc` 不在 PATH 里，照 `nvcc: command not found` 去装 nvidia-jetpack 会白装一堆东西。

```bash
ls -d /usr/local/cuda*                     # 期望有 /usr/local/cuda-<JetPack 对应的版本>
/usr/local/cuda/bin/nvcc --version | tail -2   # 用绝对路径，12.x（JetPack 6）或 13.x（Thor）
# 目录不存在、或没有 nvcc，才需要装：
sudo apt-get update
sudo apt-get install -y nvidia-jetpack     # JetPack 是 NVIDIA 给 Jetson 的 SDK 包，CUDA / cuDNN / TensorRT 都在里面
```

**Python 使用 miniforge**（conda 发行版），比系统自带的 Python 干净，版本也可以自己选：

```bash
# 安装 miniforge（使用清华镜像）
curl -fL --progress-bar -o /tmp/miniforge.sh \
    https://mirrors.tuna.tsinghua.edu.cn/github-release/conda-forge/miniforge/LatestRelease/Miniforge3-Linux-aarch64.sh
bash /tmp/miniforge.sh -b -p "$HOME/miniforge3"
"$HOME/miniforge3/bin/conda" init bash && exec bash     # 重新进入 shell 后 conda 可用
conda config --add channels https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge/
conda config --set show_channel_urls yes
```

---

## 2. 安装 Docker 并配置镜像加速

```bash
# 1) 使用清华的 docker-ce 源（arm64；系统是 24.04 时把下面的 jammy 换成 noble）
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://mirrors.tuna.tsinghua.edu.cn/docker-ce/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=arm64 signed-by=/etc/apt/keyrings/docker.gpg] https://mirrors.tuna.tsinghua.edu.cn/docker-ce/linux/ubuntu jammy stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# 2) 当前用户加入 docker 组（重新登录后生效）
sudo usermod -aG docker $USER

# 3) Docker Hub 走国内加速（拉取 ros 基础镜像时要用）
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{ "registry-mirrors": ["https://docker.m.daocloud.io"] }
EOF
sudo systemctl restart docker

# 4) 验证
docker run --rm hello-world | head -3
docker pull ros:humble-ros-base          # 后面构建镜像需要，先拉下来
```

容器里不跑模型（模型都在宿主机上），用不到 GPU，因此不需要安装 nvidia-container-toolkit。

---

## 3. USB-CAN：让系统认出机械臂

Piper 机械臂通过 USB-CAN 适配器连接，系统需要把它识别成一个 `can` 网络接口。接口名（`can0`、`can4`…）由枚举顺序决定，每台机器不一样，所以第一步是找出机械臂用的是哪一个。

机械臂原语认口的办法是看 sysfs（`/sys` 下的设备信息）：接口的设备路径带 `usb` 的是 USB-CAN 适配器，走 `platform/...mttcan` 的是 Jetson 板载 CAN。手工查用同一套办法：

```bash
# 1) 列出所有 can 接口，看每个接口挂在哪种设备上
for i in $(ip -br link show type can | awk '{print $1}'); do
    echo "$i -> $(readlink -f /sys/class/net/$i/device)"
done
```

- 有一行的路径里有 `usb`（形如 `…/usb1/1-4/1-4.2/…`）→ 这个接口就是机械臂的，把它的名字记下来，下面的命令换成它。
- 每一行都是 `platform/...mttcan`，或者一条 can 接口都没有 → USB-CAN 没有出现，先看驱动：
  ```bash
  lsmod | grep gs_usb || sudo modprobe gs_usb
  ```
  如果 `modprobe` 提示找不到该模块，说明这版内核没有编译 `gs_usb`，需要从内核源码编译：
  ```bash
  sudo apt-get install -y linux-headers-$(uname -r) build-essential
  # 源码在内核树的 drivers/net/can/usb/gs_usb.c（JetPack 的源码包 nvidia-l4t-kernel-source 中包含）
  # 编译完成后 insmod gs_usb.ko，之后 modprobe gs_usb 即可加载
  ```
  驱动装载后还是没有 → 用 `lsusb` 确认适配器是否被识别（插紧、换一个口）。

```bash
# 2) 启用 can 接口（下面的 can2 换成第 1 步查到的接口名；波特率 1 Mbps，与清单里的 can_bitrate 一致）
sudo ip link set can2 type can bitrate 1000000
sudo ip link set can2 up

# 3) 确认
ip -details link show can2 | head -4      # 期望 state UP、bitrate 1000000
```

---

## 4. 宿主机上的模型服务

AI 模型服务都装在宿主机上，避免向容器引入 GPU 依赖，容器内的包能用 localhost 访问它们。两个服务各建一个 venv（在仓库根目录执行）：

```bash
bash host-services/yolo/setup_venv.sh      # 建 yolo/.venv
bash host-services/funasr/setup_venv.sh    # 建 funasr/.venv
```

（需要 uv：`python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple uv`，装在第 1 节那个 miniforge 环境里就行。）

下载 FunASR 模型（走 modelscope，不随仓库提供）：

```bash
bash host-services/funasr/fetch_funasr_model.sh
```

启动两个服务（建议开两个终端，或用 `nohup ... &`）；两个脚本发现 venv 不在会自己建：

```bash
# 终端 1：YOLO 检测 → :8770
bash host-services/yolo/run_yolo_server.sh

# 终端 2：FunASR 流式识别 → :8771
bash host-services/funasr/run_funasr_server.sh
```

自测：

```bash
curl -s 127.0.0.1:8770/health | python3 -m json.tool | head -12
# 期望：success=true、classes 里有 carrot/potato/tomato

host-services/funasr/.venv/bin/python host-services/funasr/test_asr_ws_client.py
# 期望：把模型自带的示例音频发送给 :8771，逐句返回中文
```

如果出现 torch 拿不到 GPU 的情况，识别仍可用，只是比较慢。要强制走 CPU：`FUNASR_DEVICE=cpu bash host-services/funasr/run_funasr_server.sh`。

---

## 5. 仓库、镜像、容器

这里要构建两层镜像：基础层（系统 + ROS 2 + robonix）和 S10 层（在基础层上再放 packages 与配置），容器从 S10 层启动。

在仓库根目录构建。构建上下文（命令末尾的 `.`）就是这里——Docker 只能读这个目录里的文件，而 S10 层要拷 `packages/` 与 `deploy/`；run.sh 里也是这么写的。

```bash
# 1) 构建镜像：先基础层（ros + ROS2 + Rust + gRPC + 已编译的 robonix），再 S10 层
docker build -f containers/piper/Dockerfile -t piper-robonix:std-humble-arm64 .
docker build -f containers/s10/Dockerfile \
    --build-arg BASE_IMAGE=piper-robonix:std-humble-arm64 \
    -t s10-robonix:std-humble-arm64 .
```

基础层里 `cargo build` 那一步最慢，要编译 robonix，视板子而定，从十几分钟到几十分钟不等。`packages/` 排在 S10 层的最后几层，所以以后改 packages 只会重建末尾那几层。

```bash
# 2) 启动容器（run.sh 会构建镜像、创建容器，并把密钥清单送进去）
IMAGE_NAME=s10-robonix:std-humble-arm64 CONTAINER_NAME=s10_robonix_dev \
    containers/s10/run.sh
```

清单文件 `deploy/s10/manifest.local.yaml` 在第 6 节创建。先跑 run.sh 的话它会提示找不到，不影响容器创建；按第 6 节建好后再跑一次 run.sh，清单就会被送进去。

run.sh 做了这几件事：给容器加上 `--net host` 和 `-v /dev:/dev`（相机、机械臂、CAN 设备），挂载 docker socket（导航技能要在容器里执行 `docker compose`），把 `deploy/s10/manifest.local.yaml` 复制进容器（密钥不进入镜像），然后运行入口脚本。

容器里没有挂载仓库：`packages/` 与 `deploy/` 都是镜像里的副本，运行时产生的 `rbnx-build/`、`rbnx-boot/` 也写在容器内部，宿主机上的仓库只保留源码。修改源码后重新构建镜像，再执行一次上面的启动命令即可（run.sh 发现容器用的镜像已经过期时，会用新镜像重建容器）。

入口脚本负责三件事：加载 ROS 环境、启动 Zenoh 路由器（ROS 节点靠它互相发现，端口 :7447）、执行 `rbnx setup` 注册框架源码路径。绕过它直接进容器（`docker exec -it <容器> bash`）就会缺这三项。

---

## 6. 清单与密钥

清单（manifest）声明这次启动哪些包、各自的参数是什么。配置位于 `deploy/s10/`：`manifest.yaml` 是模板（密钥位置都是 `<YOUR_...>` 占位符），真实值放在 `manifest.local.yaml`（已被 .gitignore 忽略）。

```bash
cp deploy/s10/manifest.yaml deploy/s10/manifest.local.yaml
chmod 600 deploy/s10/manifest.local.yaml
# 编辑 manifest.local.yaml，替换以下几处的占位符：
#   system.pilot.vlm.api_key              —— pilot 规划使用（纯文本模型即可）
#   service.llm_detect.config.llm_api_key  —— 检测服务的 VLM 后端，**必须能看图**
#   skill.pick.config.grab_vlm_api_key     —— 抓取技能的 VLM 兜底（不使用时可保留占位符）
#   skill.pick.config.grab_match_llm_api_key —— 抓取时把用户说的话匹配到类名的模型
# 顶层 env: 中的 TTS 凭据（可选，见第 0 节）
```

换机器时需要重新核对的几项：

| 键 | 说明 |
|---|---|
| `primitive.audio_mic.config.mic_device` | 麦克风所在的声卡。用 `arecord -l` 查你的麦克风是第几个声卡，按 `card N` 改（模板里的 `plughw:2,0` 只是示例）；名字要写 `plughw:` 而不是 `hw:`，原因见清单里的注释 |
| `primitive.piper_ctl.config.can_usb_address` | 适配器插在哪个 USB 口，形如 `1-4.1:1.0`；`readlink -f /sys/class/net/<接口名>/device` 就能看到（第 3 节那套 sysfs 办法）。也可以用 `bash packages/primitives/primitive-agilex-piper-arm-rbnx/scripts/find_all_can_port.sh`，它顺带打印 bus-info；但那个脚本有个已知毛病：`set -o pipefail` 遇到 `dpkg -l \| grep -q` 提前退出会误报 "ethtool not detected"，看到这句先自己 `command -v ethtool` 确认一下 |
| `primitive.realsense_camera.config.*` | 分辨率必须为 1280×720×30，标定值按该档位生成，不要修改 |
| `service.roboarm_ik.config.urdf_path` / `init_joints_deg` / `teach_safe_joints_deg` | URDF 路径与位姿；更换安装方式后需要重新标定（见附录 A） |
| `skill.pick.config.*`（height/位姿/homography/camera_matrix/dist_coeffs） | 都来自标定产物，见附录 A |

---

## 7. 启动整套系统

```bash
# 在容器内执行（run.sh 会带你进入容器；已经在容器里时用：containers/s10/run.sh -e "命令"）
cd /root/workspace
rbnx boot -f deploy/s10/manifest.local.yaml
```

启动顺序与清单中的声明顺序一致（提供者在前）。原语（primitive）初始化失败会让整个启动过程失败，技能（skill）失败只影响它自己。

另开一个终端查看日志（日志在容器内，需要先进入容器）：

```bash
docker exec -it s10_robonix_dev bash -lc \
    "tail -f /root/workspace/deploy/s10/rbnx-boot/logs/*.log"
```

**启动成功的判据**：

1. `rbnx boot` 结束时没有 FATAL（日志里最严重的一级）；`/root/workspace/deploy/s10/rbnx-boot/logs/<每个包>.log` 都有内容
2. 清单中列出的原语、服务、技能都已注册：

   ```bash
   rbnx caps            # 列出已注册的能力（在容器内执行）
   ```

3. ROS 层有数据（在容器内执行）：

   ```bash
   ros2 topic hz /camera_435i/color/image_raw     # 期望 ~30 Hz（话题 = ROS 2 节点之间传数据的通道）
   ros2 topic hz /arm/joint_states_single         # 期望有稳定频率
   ```

   这两项没有数据时，后续抓取一定失败。

停止整套系统：

```bash
rbnx shutdown
```

---

## 8. 交互流程

### 浏览器客户端（推荐）

```bash
# 在仓库内获取客户端（vendor/ 已被 .gitignore 忽略，不进入版本库）
# 下载慢时加 gh-proxy 前缀：https://hk.gh-proxy.org/<原 GitHub 地址>
git clone https://github.com/syswonder/robonix-client.git vendor/robonix-client
cd vendor/robonix-client
git checkout fc54bb9            # 本机验证过的版本；不切换就用最新
conda create -y -n robonix-client python=3.12
conda activate robonix-client
python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -e ".[audio]"
# 需要 PortAudio：sudo apt-get install -y libportaudio2 portaudio19-dev

robonix-client --robot-host 127.0.0.1        # 在本机运行
```

浏览器打开 `http://127.0.0.1:7860/`，确认 Robot Host 是 `127.0.0.1`、Atlas Port 是 `50051`（Atlas 是 robonix 的调度服务，容器里监听这个端口），点 **Connect**，状态变成 online 就连上了。

- **文字输入**：输入"抓西红柿并放进盒子"，点击 Send
- **语音输入**：在 Audio 面板中把 Input Primitive 指定为 `audio_mic`、Output 指定为 `audio_client_bridge`（清单中这两个原语都提供麦克风契约，不指定会随机选中其中一个）。先说唤醒词"罗伯特"，再说要抓什么

### 命令行（`rbnx chat`）

```bash
rbnx chat
```

同样需要指定麦克风与喇叭，否则可能选到错误的提供者：

```bash
export ROBONIX_CHAT_MIC_NODE=audio_mic
export ROBONIX_CHAT_SPEAKER_NODE=audio_client_bridge
```

### 抓取流程

`grab`（抓起并保持）、`pick`（抓起放进盒子）、`put_in_box`、`put_down` 是抓取技能的四个能力。以 `/pick` 为例，一次抓取分为四段：

1. 机械臂回到观察位姿（相机在机械臂上，需要对准桌面）
2. 宿主机 YOLO 全量检测 → 两帧一致性校验 → 排除盒内物体 → 用大模型把用户说的话匹配到 YOLO 的类名
3. 计算抓取位姿（单应变换把画面像素映射到桌面平面，再解逆运动学）→ 移动到悬停位 → 下探合爪 → 抬起 → 校验夹爪反馈
4. 搬运到落盒点 → 释放 → 空手回到安全位

需要临时改变行为时不用重启：往 `deploy/s10/rbnx-boot/runtime_config.txt` 里写 `key=value`（例如 `place_go_down_before_open=true`、`detect_scheme=vlm`），每次抓取和检测都会重新读这个文件。

---

## 9. 验收清单

按顺序确认：

| 检查 | 命令 | 期望 |
|---|---|---|
| CAN 接口 | `ip -details link show <接口名> \| head -3` | UP、bitrate 1000000 |
| YOLO 服务 | `curl -s 127.0.0.1:8770/health` | success=true、三类水果 |
| FunASR 服务 | `host-services/funasr/.venv/bin/python host-services/funasr/test_asr_ws_client.py` | 逐句返回中文 |
| 容器运行中 | `docker ps \| grep s10_robonix_dev` | Up |
| Zenoh 路由器 | `docker exec s10_robonix_dev bash -c "ss -ltn \| grep 7447"` | 有监听 |
| 栈已启动 | 容器内 `rbnx caps` | 清单中的原语、服务、技能都在 |
| 相机有画面 | 容器内 `ros2 topic hz /camera_435i/color/image_raw` | ~30 Hz |
| 机械臂有反馈 | 容器内 `ros2 topic hz /arm/joint_states_single` | 稳定频率 |
| 抓取可用 | 在客户端说"抓西红柿"，然后查看容器内 `/root/workspace/deploy/s10/rbnx-boot/logs/pick.log` | 出现 `grab ... ok` |

---

## 10. 常见故障

**相机话题没有数据**（`ros2 topic hz` 一直等待）
D435i 被其他进程占用。用 `lsusb | grep 8086` 确认设备存在；在容器内用 `ps aux | grep realsense` 确认驱动是否启动。分辨率被改成 1280×720 以外的值时，已有的标定值也会失效。

**机械臂不动 / 日志中出现 `no JointState within 30s`**
CAN 接口没有启用（回到第 3 节）。还有一个容易漏掉的原因：`piper_sdk` 在打开总线前会执行 `ip -details link show <can>` 核对波特率，容器内没有 `ip` 命令时会直接拒绝连接。镜像中已经安装 `iproute2`，自行修改镜像时不要删除。

**`rbnx boot` 中某个原语报 FATAL**
原语初始化失败会中止整个启动过程（技能失败只影响自身）。到容器内 `/root/workspace/deploy/s10/rbnx-boot/logs/<包名>.log` 查看第一条 error。

**抓取报 `matcher_llm_failed: ...`**
这是**把用户说的话匹配到类名的那次大模型调用**出错（超时、无法连接、返回内容无法解析）。重试一次通常可以成功；反复失败时检查清单中 `grab_match_llm_*` 对应的端点与额度。桌面上确实没有该物体时，报的是另一类信息：`detection_failed: object 'X' not detected (table has: ...)`。

**容器重启（或被 OOM 杀掉，即内存不足被系统强制结束）后所有话题超时**
容器内缺少 Zenoh 路由器（ROS 节点依赖它互相发现）。使用**入口脚本**进入或启动容器（`containers/s10/run.sh`），它会自行启动路由器；只用 `docker start` 加 `docker exec bash` 进来就会绕过它。

**语音没有反应**
宿主机上的 FunASR 服务没有启动（第 4 节）；或者说的内容与唤醒词不符（唤醒词在清单 `service.speech.config.wake_words` 中，默认为"罗伯特"）。

**能听到响应但没有声音**
默认 TTS 使用 mock（清单里就这么配的）。需要声音时要配置腾讯 TTS（顶层 `env:` 中的凭据），并把客户端 Output 指定为 `audio_client_bridge`。

**pilot 报 `VLM stream error`（open / decoding response body）**
pilot 调大模型时的偶发失败，与网络质量无关（同一时刻用 curl 直接打端点往往正常）。重发一次通常就过。

---

## 11. 收尾、重启、关机

```bash
# 停止（在容器内执行）
rbnx shutdown

# 完整重启：先 shutdown，再 boot。不要只重启部分（残留进程会占用端口）
rbnx shutdown && rbnx boot -f deploy/s10/manifest.local.yaml

# 关机顺序：先 shutdown 停止各组件 → 再停止容器 → 最后断开机器人电源
docker stop s10_robonix_dev
```

⚠️ **重启必须杀干净再 boot，中间要确认端口空了**（piper 那套踩过一次：杀旧栈后立刻 boot，旧 supervisor 收到 SIGTERM 收尾时，把新栈刚注册的服务一起关掉——现象是服务起来几秒就没了）。杀完先看一眼端口，空出来再 boot：

```bash
docker exec s10_robonix_dev bash -c 'ss -ltn | grep -E ":5005[0-9]|:5006[0-9]|:5007[0-9]|:5008[0-9]|:5009[0-9]"'   # 应当没有输出
```

`rbnx shutdown` 偶尔会报 `No such file or directory`（state 对不上时），那就直接用 pkill 按进程名清，别跳过"确认端口空"这一步。

---

## 附录 A：标定（更换机器或需要重新标定时）

工具位于 `tools/calibration/s10/`，是一个跑在宿主机上的网页标定台，直接连接相机与机械臂，不需要启动 robonix 栈。

```bash
conda create -y -n s10-calib python=3.10
conda activate s10-calib
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r tools/calibration/s10/requirements.txt
cd tools/calibration/s10 && python app.py --port 8080      # 浏览器打开 :8080
```

共六步，顺序为：**测桌面 z → 内参 → 示教 → 手眼 → 抓取自检 → 回零失能**。其中示教是手拖机械臂摆到要记的位置，手眼标定求的是相机像素到机械臂坐标的对应关系。

内参那页要填**内角点数**而不是格子数（`tools/calibration/boards/` 那两张 A4/A3 板子是 12×8 格 → 填 11 × 7；20×14 格的板子填 19 × 13）；没有板子就先照 `tools/calibration/boards/README.md` 打一张。拍的时候板子占画面 1/4 以上、每次明显倾斜、远近也变一变，否则焦距量不出来（工具会自己检查并提示重拍）。

- 手眼标定要先完成内参标定：采点用的是按内参去畸变后的画面，没有内参就不能开始。标定时的画面必须与运行时一致。
- 产物位于 `tools/calibration/s10/artifacts/`；需要抄写到清单中的哪几个键，见 `tools/calibration/s10/README.md` 的对照表。
- 抓取自检是唯一会真实驱动机械臂、验证整条链路的步骤。

⚠️ 仓库中现有的内参与单应矩阵是用**旧的 UVC 取图路径**标定的，而运行时使用 pyrealsense2。投产前应重新标定一次，并更新清单。

---

## 附录 B：镜像源一览

| 用途 | 镜像源 | 配置位置 |
|---|---|---|
| apt（ubuntu/ros2） | `mirrors.tuna.tsinghua.edu.cn` | 构建镜像时在 Dockerfile 中替换源 |
| pip | `pypi.tuna.tsinghua.edu.cn` | Dockerfile 的 `PIP_INDEX_URL` |
| 宿主机 GPU 环境 | `mirrors.ustc.edu.cn/pypi/simple`；Orin 的 torch 走 `pypi.jetson-ai-lab.io/jp6/cu126` | 各服务的 `requirements.txt` 与 `torch-{orin,thor}.txt` 里的 `--index-url` |
| Rust 工具链 | `mirrors.tuna.tsinghua.edu.cn/rustup` | Dockerfile 的 `RUSTUP_DIST_SERVER` |
| crate 文件 | `rsproxy.cn` | Dockerfile 的 `/root/.cargo/config.toml` |
| Docker Hub | `docker.m.daocloud.io` | `/etc/docker/daemon.json` |
| HuggingFace | `hf-mirror.com` | Dockerfile 的 `HF_ENDPOINT` |
| FunASR 模型 | `modelscope` | `fetch_funasr_model.sh` |
| GitHub 克隆 | `gh-proxy`（前缀 `https://hk.gh-proxy.org/`） | 手工加前缀，例如获取 robonix-client 时 |

---

## 写在最后：哪些环节经过实机验证

本文的内容逐条对照仓库当前代码编写，但**整套流程没有在一台全新机器上完整执行过**。在本机实测过的环节：宿主机两个模型服务的自测、容器镜像的构建与容器运行、`rbnx boot` 启动成功（无 FATAL）、相机与机械臂的话题、语音链路（含 FunASR 与客户端）、抓取端到端。没有实测的环节：gs_usb 从源码编译（当时机器上已经编好）、在 Orin 上替换 NVIDIA torch 那组包的命令、以及一份全新清单从零配置的全过程。
