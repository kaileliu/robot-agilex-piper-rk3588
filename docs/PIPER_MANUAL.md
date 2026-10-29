# 纯臂 Piper 部署手册：从裸机 Jetson 到抓取

本文从一台刚装好系统、还没装过其他软件的 Jetson 开始，到能用一句话让 Piper 机械臂抓起桌上的东西收尾。只用臂和顶置相机，没有移动底盘。

和 S10 那套的区别：这里没有底盘、没有狗、没有 Realsense，相机是装在桌面上方的 Orbbec Gemini 336L。标定工具、宿主模型服务、清单都是各自的一份，别混用。

## 系统构成

| 位置 | 内容 |
|---|---|
| 宿主机（Jetson） | 模型服务（YOLO :8770、FunASR :8771）与标定台 :8090 |
| 容器 `piper_robonix_dev` | robonix 框架与 packages：原语（相机、机械臂、麦克风）、服务（检测、抓取位姿、逆运动学、语音）、技能（抓取） |
| 桌面 | Piper 机械臂、装在它上方的 Gemini 336L、被抓的物体 |

容器与宿主机共享网络（`--net host`）。

robonix 按清单启动各个包，能力分三类：**原语**（直接操作硬件）、**服务**（做计算）、**技能**（一段可调用的任务流程）。

---

## 0. 前置准备

硬件：

- Jetson（aarch64，Orin 或 Thor），系统能联网
- Piper 机械臂、USB-CAN 适配器、24V 电源
- Orbbec Gemini 336L（俯视安装，看得到整张桌面）
- 一张棋盘格标定板：`tools/calibration/boards/` 里有现成可打印的 A4/A3 版（打印选「实际大小」，打完量一下页脚那条 100 mm 的线）
- 一个 USB 麦克风（**可选**，只影响语音；没有就按第 6 节把音频那几个包注释掉）

账号与密钥（第 6 节填进清单）：

- 一个能看图的大模型（给 `llm_detect` 的 VLM 后端用），以及一个纯文本大模型：pilot 规划用它（把一句话拆成能力调用），抓取时也用它把物品名匹配到 YOLO 的类名。两处可以用同一个模型
- 腾讯云 TTS 的 SecretId/Key（可选）

获取代码（后面各节都在这个目录里操作）：

```bash
git clone https://github.com/<你的仓库>.git piper-standard
cd piper-standard
```

根分区小的话先想好放哪：Jetson 的 eMMC 常只剩几 G，仓库、Python 环境、模型都放另一块盘（下面示例都用 `<仓库>` 指代那个位置）。uv/pip 的缓存默认写在 `~/.cache`（也在根分区），`host-services` 的安装脚本已经把 uv 缓存改到仓库旁边。

---

## 1. 系统环境：驱动、CUDA、Python

```bash
uname -m                                  # 期望 aarch64
head -3 /etc/os-release                   # 期望 Ubuntu 22.04
cat /etc/nv_tegra_release 2>/dev/null || nvidia-smi | head -5
df -h /                                   # 根分区
ping baidu.com
```

**GPU 驱动**：Jetson 的驱动随 L4T 系统一起提供，不用单独装。要确认的是 CUDA 组件——**看目录，不要只看 `nvcc`**：有时候 CUDA 装着、只是 `nvcc` 不在 PATH，照报错去装 nvidia-jetpack 会白装一堆。

```bash
ls -d /usr/local/cuda*                     # 期望有 /usr/local/cuda-<JetPack 对应的版本>
/usr/local/cuda/bin/nvcc --version | tail -1
```

**Python 用 miniforge**（conda 发行版）：

```bash
curl -fL --progress-bar -o /tmp/miniforge.sh \
    https://mirrors.tuna.tsinghua.edu.cn/github-release/conda-forge/miniforge/LatestRelease/Miniforge3-Linux-aarch64.sh
bash /tmp/miniforge.sh -b -p /mnt/ssd/wfw/miniforge3     # 根分区小就装到别的盘
/mnt/ssd/wfw/miniforge3/bin/conda init bash && exec bash
```

---

## 2. 安装 Docker

```bash
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://mirrors.tuna.tsinghua.edu.cn/docker-ce/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=arm64 signed-by=/etc/apt/keyrings/docker.gpg] https://mirrors.tuna.tsinghua.edu.cn/docker-ce/linux/ubuntu jammy stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt-get update && sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin
sudo usermod -aG docker $USER            # 重新登录后生效
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{ "registry-mirrors": ["https://docker.m.daocloud.io"] }
EOF
sudo systemctl restart docker
docker run --rm hello-world | head -3
```

容器里不跑模型（模型都在宿主机上），用不到 GPU，不用装 nvidia-container-toolkit。

---

## 3. USB-CAN：让系统认出机械臂

Piper 通过 USB-CAN 适配器连接，系统要把它认成一个 `can` 接口。接口名（`can0`、`can4`…）由枚举顺序决定，每台机器不一样，先找出机械臂用的是哪一个——USB-CAN（gs_usb）的 sysfs 设备路径带 `usb`，板载 mttcan 走 `platform/...`：

```bash
for i in $(ip -br link show type can | awk '{print $1}'); do
    echo "$i -> $(readlink -f /sys/class/net/$i/device)"
done
```

找到那条带 `usb` 的（下面假设是 `can2`），启用它：

```bash
sudo ip link set can2 type can bitrate 1000000
sudo ip link set can2 up
ip -details link show can2 | head -4      # 期望 state UP、bitrate 1000000
```

记录这条路径（形如 `/sys/.../1-4.1/1-4.1:1.0` → USB 地址 `1-4.1:1.0`），第 6 节要填进清单。清单里 `piper_ctl` 的 `auto_can_setup: true` 会在启动时用同一套 sysfs 判断自动认口并启用；上面只是先手工确认一遍。

认不出接口时：`lsmod | grep gs_usb || sudo modprobe gs_usb`；提示找不到模块就是这版内核没编 `gs_usb`，要从内核源码编译（源码在内核树的 `drivers/net/can/usb/gs_usb.c`）。

---

## 4. 宿主机上的两个模型服务

两个服务各建一个 venv（它们的 torch 需求不同）。setup 脚本按板子挑 torch：Orin 是 sm_87，得从 NVIDIA 的 Jetson 索引拿带 sm_87 的轮子；Thor 是 sm_110，走镜像站就行。

```bash
bash host-services/yolo/setup_venv.sh      # 建 yolo/.venv，约 3 GB 下载
bash host-services/funasr/setup_venv.sh    # 建 funasr/.venv（只做语音才需要）
```

⚠️ **装完如果 `import torch` 报 `libcudss.so.0` 找不到**（脚本会提示）：这是 cuDSS 装在版本子目录、没登记进动态库缓存。修一次：

```bash
ls -d /usr/lib/aarch64-linux-gnu/libcudss/*/ | sudo tee /etc/ld.so.conf.d/cudss.conf >/dev/null
sudo ldconfig
```

启动（开着就行，抓取时用）：

```bash
bash host-services/yolo/run_yolo_server.sh       # :8770
bash host-services/funasr/run_funasr_server.sh   # :8771（语音才需要）
```

自测：

```bash
curl -s 127.0.0.1:8770/health | python3 -m json.tool | head -12   # success=true、三类水果
```

---

## 5. 镜像与容器

```bash
# 在仓库根目录构建（上下文是这里，Docker 只能读这个目录里的文件）
docker build -f containers/piper/Dockerfile -t piper-robonix:std-humble-arm64 .

# 启动容器：--net host、透传 /dev、把密钥清单送进去
IMAGE_NAME=piper-robonix:std-humble-arm64 CONTAINER_NAME=piper_robonix_dev \
    containers/piper/run.sh -e "echo ok"
```

基础层里 `cargo build` 那一步最慢（编译 robonix），视板子而定十几分钟到几十分钟。

和 S10 那套不同：**piper 容器挂载仓库**（`<仓库> → /root/workspace`），`packages/`、`deploy/` 用的是宿主上的这一份，改完源码 `rbnx boot` 就能生效，不用重建镜像。

入口脚本负责三件事：加载 ROS 环境、起 Zenoh 路由器（ROS 节点靠它互相发现，:7447）、`rbnx setup` 注册框架源码路径。所以别绕过入口脚本进容器。

---

## 6. 清单与密钥

```bash
cp deploy/piper/manifest.yaml deploy/piper/manifest.local.yaml
chmod 600 deploy/piper/manifest.local.yaml
# 编辑 manifest.local.yaml，把这几处的占位符换成真实密钥：
#   system.pilot.vlm.api_key                —— pilot 规划使用
#   service.llm_detect.config.llm_api_key   —— 检测服务的 VLM 后端，必须能看图
#   skill.pick.config.grab_vlm_api_key      —— 抓取技能的 VLM 兜底
#   skill.pick.config.grab_match_llm_api_key —— 抓取时把物品名匹配到类名的模型
```

换机器时要重新核对的键：

| 键 | 说明 |
|---|---|
| `primitive.piper_ctl.config.can_usb_address` / `can_port` | 第 3 节查到的 USB 地址（如 `1-4.1:1.0`）与接口名 |
| `service.roboarm_ik.config.urdf_path` | 本仓库的 `deploy/piper/urdf/piper.urdf` |
| `service.roboarm_ik.config.init_joints_deg` | 观察位姿（臂转到侧面、给俯视相机让开桌面）。换安装方式要重标 |
| `primitive.audio_mic.config.mic_device` | 麦克风声卡，用 `arecord -l` 查（写 `plughw:` 而不是 `hw:`） |
| `skill.pick.config.*` 里的标定值 | 来自第 7 节 |

**没有麦克风**：把 `primitive:` 下的 `audio_mic`、`audio_client_bridge` 和 `service:` 下的 `speech` 三处整段注释掉。原语初始化失败会让整个启动过程中止，缺硬件就必须去掉。

---

## 7. 标定（第一次上机必做）

标定台跑在宿主机上，直连相机和臂，不用起 robonix。

```bash
conda create -y -n piper-calib python=3.10 && conda activate piper-calib
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r tools/calibration/piper/requirements.txt
cd tools/calibration/piper && python app.py --port 8090     # 浏览器打开 :8090
```

三步：**内参 → 手眼 → 验证**。两个要点：

- 内参那页填的是**内角点数**，不是格子数（`boards/` 那两张板子填 11×7，20×14 格的板子填 19×13）。拍的时候板子占画面 1/4 以上、每次明显倾斜、远近也变一变——板子小又正对时焦距量不出来，工具会自己检查并提示重拍
- 手眼那页要臂上电、按臂上示教按钮进拖动模式。没有内参时这一步在首页就是「不可用」

标定完把产物抄进清单（对照表见 `tools/calibration/piper/README.md`）：两处 `homography_matrix`、`skill.pick.config` 的 `camera_matrix` / `dist_coeffs`。

⚠️ **标定台会一直占着相机和臂，直到你在页面上点「结束本步骤」**。启动栈之前务必让它放手，否则容器里的相机原语 30 秒拿不到帧，原语初始化失败会拖垮整个 boot。

---

## 8. 启动整套系统

```bash
# 在容器内执行（run.sh 会带你进容器；也可以 run.sh -e "命令" 非交互执行）
cd /root/workspace
rbnx boot -f deploy/piper/manifest.local.yaml
```

启动顺序与清单里的声明顺序一致（提供者在前）。**原语初始化失败会中止整个启动过程**，技能失败只影响它自己。

启动成功的判据：

1. `rbnx boot` 结束时没有 FATAL
2. `rbnx caps` 里清单列出的原语、服务、技能都在（技能显示 INACTIVE 是正常的——等第一次被调用时才激活）
3. 相机有画面：容器内 `ros2 topic hz /camera/color/image_raw` 期望 ~30 Hz

## 9. 让它抓一个东西

```bash
# 容器内
rbnx ask 抓个胡萝卜
```

pilot 会规划一次调用：技能先让臂回观察位 → 技能内检测（宿主 YOLO 全量检测 + 大模型把"胡萝卜"匹配到类名 `carrot`）→ 抓取位姿（单应 + 逆运动学）→ 悬停 → 下探合爪 → 抬起 → 校验夹爪反馈。成功时日志里是：

```
match: user '胡萝卜' -> carrot (conf=0.94)
grab gripper verification ok: gripper held at 0.0555m >= 0.0050m threshold
```

`grab` 抓起来保持握持；`pick` 会接着放进盒子；`put_down` 就地松手。

---

## 10. 常见故障

**`rbnx boot` 报某个原语 FATAL / soma stage 1 failed**
原语初始化失败会中止整个 boot。到容器内 `deploy/piper/rbnx-boot/logs/<包名>.log` 看第一条 error。最常见的两个原因：相机被标定台占着（第 7 节末尾）、清单里留了没有的硬件（麦克风）。

**技能或服务起来几秒后就没了**
多半是"杀旧栈之后立刻 boot"：旧 supervisor 收到 SIGTERM 后收尾时，会把新栈刚注册的服务一起关掉。正确顺序见第 11 节。

**pilot 报 `VLM stream error`（open / decoding response body）**
这是 pilot 调大模型时的**偶发**失败，跟网络质量无关（同一时刻用 curl 直接打端点往往正常）。再发一次通常就过。

**相机话题没有数据**
容器内 `ros2 topic hz /camera/color/image_raw` 一直等：相机被别的进程占着（标定台）。标定台页面上点「结束本步骤」。

**`import torch` 报 `libcudss.so.0`**
见第 4 节，登记一次 `/etc/ld.so.conf.d/cudss.conf` 即可。

---

## 11. 收尾、重启

重启栈要"杀干净 → 确认端口都空 → 再 boot"，少一步就会出上面那条"服务起来又没了"：

```bash
# 1) 杀干净（在容器内）
docker exec piper_robonix_dev bash -c 'pkill -f "[r]bnx"; pkill -f "[r]obonix-"; pkill -f "[p]iper_single_ctrl"'

# 2) 确认端口都空了（应当没有输出）
docker exec piper_robonix_dev bash -c 'ss -ltn | grep -E ":5005[0-9]|:5006[0-9]|:5007[0-9]|:5008[0-9]|:5009[0-9]"'

# 3) 再 boot
cd /root/workspace && rbnx boot -f deploy/piper/manifest.local.yaml
```

⚠️ `rbnx shutdown` 在这套上可能报 `No such file or directory`（state 对不上时），别指望它；按上面的 pkill 来，并**确认端口空了**再 boot。

关机顺序：先停栈 → 再 `docker stop piper_robonix_dev` → 最后断机器人电源。
