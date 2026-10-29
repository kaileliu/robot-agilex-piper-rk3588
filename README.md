# AgileX Piper + RK3588 NPU YOLO for Robonix

基于 **AgileX Piper 六轴机械臂、平行夹爪、Orbbec Gemini 336L 相机和 Rockchip
RK3588** 的 Robonix 真机部署仓库。提供相机与机械臂原语、YOLO OBB 检测、抓取位姿
计算、Python 逆运动学和垂直抓取技能，并包含可选的语音交互链路。

模型推理运行在宿主机的 RK3588 NPU；Robonix、ROS 2 驱动和抓取服务运行在
ROS 2 Humble 容器中，通过 host 网络访问推理服务。日常检测无需 PyTorch、CUDA
或重新转换模型。源码中已包含部署所需的本地 package、URDF、模型和 RKNN 运行库。

**当前状态：软件接入和 package 校验已通过，但本机手眼标定的独立验证未通过，真实
物体抓取尚未完成验证。** 本仓库提供需现场配置的部署模板，历史标定矩阵和动作姿态
不能直接作为新安装的有效参数。封面和演示图片暂留空。

[能力](#能力) · [架构](#架构) · [安装](#安装) · [配置与标定](#配置与标定) ·
[启动与使用](#启动与使用) · [验证](#验证) · [停止](#停止) ·
[Package Catalog 接入](#package-catalog-接入) · [故障排查](#故障排查)

## 能力

| 组件 / 服务 | Provider ID | 功能与接口 |
| --- | --- | --- |
| Orbbec Gemini 336L | `orbbec_camera` | ROS 2 RGB、深度和相机内参；gRPC 生命周期 |
| Piper 机械臂及夹爪 | `piper_ctl` | ROS 2 关节状态/指令、末端位姿/指令和机械臂状态；gRPC 生命周期 |
| Piper 机体描述 | `piper_description` | URDF 和 `robot_state_publisher`，发布 TF；不声明独立路由能力 |
| 本机麦克风 | `audio_mic` | ALSA 采集、设备枚举与选择；gRPC |
| 操作员音频桥 | `audio_client_bridge` | 远端麦克风/扬声器，gRPC 能力及内部反向 WebSocket |
| 目标检测 | `llm_detect` | YOLO HTTP / VLM 后端适配；MCP 检测接口 |
| 抓取位姿 | `grasp_pose` | 基于单应矩阵，将图像坐标转换为机械臂基座系抓取位姿；gRPC |
| Python IK 执行 | `roboarm_ik` | 逆运动学、抓取执行、复位与放置；gRPC；不依赖 MoveIt |
| 语音服务 | `speech` | ASR、唤醒、TTS、对话；gRPC 和 MCP |
| 垂直抓取技能 | `pick` | `pick`、`demo`、`grab`、`put_in_box`、`put_down`；MCP |
| 宿主 NPU 检测 | 无 Atlas provider | YOLO11n-OBB 的 HTTP 推理后端，监听 `127.0.0.1:8770` |

随附检测权重的类别是 **carrot、potato、tomato**。当前路径使用俯视相机、平面手眼
标定和垂直抓取；抓取技能会去畸变、调用检测并使用双帧一致性筛选目标。

`grab` 抓起并保持，`put_in_box` 搬运到配置的盒位，`put_down` 就地松爪，`pick`
组合检测、抓取和放置，`demo` 在配置的两个位置间演示。它们都可能使实机运动。
VLM 后端可配置为另一种检测方式，但需要实际可用的多模态模型和独立验证。

## 架构

```text
操作员：rbnx chat / rbnx ask / robonix-client
                         │
           Atlas · Pilot · Executor · Liaison · Soma
                         │
                       pick 技能
                         ├── 去畸变 → JPEG → HTTP YOLO → 目标匹配
                         ├── grasp_pose：像素 → arm/base_link
                         └── roboarm_ik：逆解 → ROS 2 关节命令
                                                   │
                      ROS 2 Humble 容器             │
                 orbbec_camera                 piper_ctl
                      │                            │
                 USB RGB-D 相机              宿主 SocketCAN → Piper

宿主机：yolo_npu（RKNN Lite2 → RK3588 NPU，HTTP :8770）
        FunASR（可选，WebSocket :8771）
```

检测服务 `llm_detect` 也能调用同一个 HTTP 后端；抓取技能的默认 YOLO 路径直接访问
宿主检测服务。NPU 支持包没有 Atlas contract，不放入容器内 `service:` 启动列表。

机体层级与描述在 [soma.yaml](deploy/piper/soma.yaml)，TF/几何描述在
[Piper URDF](deploy/piper/urdf/piper.urdf)，系统和 package 配置集中在根目录
[robonix_manifest.yaml](robonix_manifest.yaml)。包间调用依赖 contract ID，provider
名称与发布名是不同的标识。

## 环境要求

| 项目 | 当前部署要求 |
| --- | --- |
| 主机 | Rockchip RK3588，Linux AArch64；配套 CPython 3.10 |
| 机械臂 | AgileX Piper、夹爪、USB-CAN 适配器；CAN 1 Mbps |
| 相机 | Orbbec Gemini 336L，推荐 USB 3，彩色 1280×720 |
| ROS | 容器内 ROS 2 Humble，默认 `rmw_zenoh_cpp` |
| 容器 | Docker Engine，能够访问 USB 设备并使用 host 网络 |
| NPU | RKNPU 驱动和 `/dev/dri/renderD128`；随附 RKNN Lite2/Runtime 2.3.2 |
| 构建工具 | Git、curl、Python venv、uv；构建期可访问 GitHub、系统和 Python 软件源 |
| 模型接口 | 用于 Pilot 和目标名称匹配的 OpenAI 兼容接口；VLM 另需支持图片输入 |
| 可选语音 | ALSA 麦克风或操作员音频客户端，另备 FunASR 模型与依赖 |

历史 NPU 测试使用 Linux 6.1.118、Python 3.10.12 和 RKNPU 驱动 0.9.8。
随附 RKNN wheel 针对 CPython 3.10 / AArch64，不能直接用于 x86_64 或其他 Python
版本。检测模型输入高×宽为 736×1280，FP16；原始相机输出宽×高为 1280×720。

## 安装

### 1. 获取仓库与固定版本 Robonix

```bash
git clone https://github.com/kaileliu/robot-agilex-piper-rk3588.git
cd robot-agilex-piper-rk3588

git clone https://github.com/syswonder/robonix.git vendor/robonix
git -C vendor/robonix checkout fb68bc52f9bb9e2bb57337332d32357340d16968
git -C vendor/robonix submodule update --init --recursive
```

Robonix 源码不会随本仓库重复提交，版本与获取说明见 [vendor/PINS.md](vendor/PINS.md)。
ROS 驱动、服务和技能以源码形式放在 `packages/`，构建时不会重新 clone 它们的上游
默认分支；本项目的修改不会在启动时被上游更新覆盖。

### 2. 构建 ROS / Robonix 容器

```bash
bash containers/piper/run.sh -b -e 'rbnx --version'
```

构建使用 [Dockerfile](containers/piper/Dockerfile)，默认镜像
`piper-robonix:std-humble-arm64`、容器 `robonix_yolo_dev`。仓库挂载到容器中的
`/root/workspace`；Robonix 核心源码编入镜像。后续无需每次 `-b` 重建：

```bash
bash containers/piper/run.sh -e 'rbnx --version'
```

容器使用 host 网络和 `/dev` 挂载以访问相机、音频和 SocketCAN。若同名容器已挂载
另一个目录，脚本会拒绝复用；请用 `CONTAINER_NAME` 指定本部署独立的容器名称。
首次构建需要下载 ROS、Rust 和 Python 依赖，耗时取决于主机与网络。

### 3. 安装宿主 NPU 运行依赖

在宿主安装 Python 3.10、venv 和 uv，再执行：

```bash
bash yolo_npu/setup_runtime.sh
```

安装结果位于 `yolo_npu/runtime_python/` 和 `runtime_deps/`，均不提交 Git。
模型、JSON 元数据、原始 wheel 和 `runtime/librknnrt.so` 已在仓库中；启动时会检查
模型与元数据的 SHA256 配对，不需要把运行库复制到系统目录。

## 配置与标定

### 1. 创建本机配置

```bash
cp robonix_manifest.yaml robonix_manifest.local.yaml
chmod 600 robonix_manifest.local.yaml
```

编辑这个**完整清单副本**。所有 `*.local.yaml` / `*.local.env` 都已忽略，实际 API key
只写入本机文件。根清单中的模型名称与 URL 是可替换的历史配置，不代表接口当前可用。

| 配置位置 | 必须核对的内容 |
| --- | --- |
| `system.pilot.vlm` | 规划模型的 upstream、api_key、model 和 api_format |
| `primitive.piper_ctl.config` | `can_port`、1 Mbps 比特率、USB 地址和使能策略 |
| `primitive.orbbec_camera.config` | Gemini 330 系列 launch，实际相机分辨率/帧率 |
| `service.roboarm_ik.config` | URDF 路径、观察位、复位和放置姿态 |
| `service.grasp_pose.config` | 本机单应矩阵、桌面高度和基座坐标系 |
| `skill.pick.config` | 同一单应矩阵、相机 K/D、桌面高度、盒位及动作偏移 |
| `skill.pick.config.grab_match_llm_*` | YOLO 类别与用户措辞匹配用的文本模型 |
| `service.llm_detect.config.llm_*` / `skill.pick.config.grab_vlm_*` | 使用 VLM 时可用的多模态接口与密钥 |
| `env.PIPER_WORKSPACE` | 容器默认 `/root/workspace`；直接宿主运行需改成实际路径 |

表中的 `primitive.piper_ctl` 等写法按条目的 `name` 指代 YAML 列表成员，不能直接
改成字典结构。根清单默认关闭 `auto_enable` 和 `auto_can_setup`。

### 2. 配置 CAN

先核对 USB-CAN 设备与接口名称。下面命令以 `can0` 为例，在宿主执行：

```bash
ip -details link show can0
# 确认接口未被其他部署占用，且处于 DOWN 时：
sudo ip link set can0 type can bitrate 1000000
sudo ip link set can0 up
ip -details link show can0
```

把实际接口写入本机清单。需要驱动自动使能电机时，现场确认后再设置
`auto_enable: true`。**使能后启动 IK 服务可能将机械臂移到观察位，boot 不是纯读检查。**

### 3. 完成相机内参与平面手眼标定

按 [Piper 标定台说明](tools/calibration/piper/README.md) 依次完成：

1. 内参：采集不同位置、距离和倾角的棋盘格照片，计算 `camera_matrix`、`dist_coeffs`。
2. 手眼：在去畸变图上采集像素点及其机械臂基座系对应点，拟合单应矩阵。
3. 独立验证：使用未参与拟合的点检查实际落点；通过后才恢复自动抓取。

相机和 CAN 不能同时被标定台与 Robonix 驱动占用。标定工具依赖单独安装，初始化
方式见其 README；验证页面会移动机械臂。把结果写入根目录的本机清单：

| 标定结果 | 写入字段 |
| --- | --- |
| 相机矩阵 K、畸变 D | `skill.pick.config.camera_matrix`、`dist_coeffs` |
| 单应矩阵 H | `service.grasp_pose.config.homography_matrix` 与 `skill.pick.config.homography_matrix` |
| 桌面高度 | `grasp_pose.default_desktop_height` 与 `pick.desktop_height`，并核对 `grasp_offset` |

H 的两处配置必须一致，运行时必须使用标定时的相机分辨率和去畸变方式。本仓库当前
现场标定尚未通过独立验证，不能据历史矩阵或软件预检结果判断实机可以抓取。

## 启动与使用

与参考部署相同，建议把推理后端、Robonix 和交互终端分开运行。

### 终端 1：宿主 NPU 推理

```bash
cd robot-agilex-piper-rk3588
bash yolo_npu/run_yolo_npu.sh
```

另一个宿主终端检查：

```bash
curl -fsS http://127.0.0.1:8770/health | python3 -m json.tool
```

`device` 应为 `rk3588-npu`。可单独对仓库样图检测，不接触机械臂：

```bash
curl -fsS --data-binary @yolo_npu/samples/camera_calibration/calib_006.jpg \
  'http://127.0.0.1:8770/detect?conf=0.5&iou=0.45&object_name=tomato' \
  | python3 -m json.tool
```

`POST /detect` 接收原始 JPEG/PNG 字节。`image_size` 为 `[高度, 宽度]`；OBB 的角度
使用弧度，`bbox_2d` 第五项使用度。输出协议、模型重建和参数见
[NPU 实现说明](yolo_npu/README.md)。

### 终端 2：构建并启动 Robonix

在完成标定、CAN 和运动参数核对后执行：

```bash
bash containers/piper/run.sh -e \
  'cd /root/workspace && rbnx build -f robonix_manifest.local.yaml'

bash containers/piper/run.sh -e \
  'cd /root/workspace && rbnx boot -f robonix_manifest.local.yaml --no-update-check --verbose'
```

启动按 primitive → service → skill 编排。`pick` 是按需激活的技能，初始为
`INACTIVE` 不一定是异常；应结合能力注册和初始化日志判断。

### 终端 3：能力检查与文字交互

```bash
bash containers/piper/run.sh -e 'rbnx caps'
bash containers/piper/run.sh -e 'rbnx tools'

# 交互式 shell 内运行 rbnx chat
bash containers/piper/run.sh
# 容器内：
rbnx chat
```

先检查能力与状态，再在已验证的工作区中发动作请求，例如：

```text
当前机械臂有哪些能力？
抓起胡萝卜并保持。
把夹持的物体放进盒子。
放下当前物体。
```

非交互请求使用：

```bash
bash containers/piper/run.sh -e 'rbnx ask "当前机械臂有哪些能力？"'
```

动作类请求会调用实际执行器。不要同时运行另一个抓取任务、直接 CAN 控制程序或
标定验证动作；机器人当前可执行的任务还取决于现场配置和标定状态。

### 可选：语音交互

宿主语音识别服务独立于 NPU 检测运行：

```bash
bash host-services/funasr/fetch_funasr_model.sh
FUNASR_DEVICE=cpu bash host-services/funasr/run_funasr_server.sh
```

FunASR 模型未入库，脚本负责下载；依赖和性能限制见
[宿主服务说明](host-services/README.md)。RK3588 的 YOLO NPU 后端不会自动加速
FunASR。默认唤醒词为“罗伯特”；默认 TTS 是返回静音的 `MockTTSBackend`，有文本
结果不等于有语音回复。需要语音输出时另配真实 TTS。

本机麦克风和远端音频桥同时存在时，必须在客户端选择明确的输入 provider，避免
相同 `audio/mic` contract 的自动解析选到另一端。远端扬声器使用音频桥。

### 可选：检测预览与本机历史入口

仓库也保留了现场使用的 `run_robonix_yolo.sh`、`preview_robonix_yolo.sh`、
`check_robonix_yolo.sh` 和 `calibrate_robonix_yolo.sh`。这些工具读取
**`deploy/piper/manifest.local.yaml`**，部分还依赖已有标定环境与 systemd；它们与
上面的根目录本机清单不是同一入口。

新机器优先使用上述分终端流程。需要沿用现场脚本时，先阅读
[现场运行说明](README_ROBONIX_YOLO.md)，按它们的路径准备独立的本机清单。
检测预览默认端口为 8088，标定台默认 8090；本文未启动或验证新的预览实例。

## 验证

### 不启动机械臂的 package 检查

```bash
# 例如验证机械臂包；其他包目录见 catalog/package-paths.json。
bash containers/piper/run.sh -e \
  'cd /root/workspace && rbnx validate packages/primitives/primitive-agilex-piper-arm-rbnx'
```

命令检查 manifest，不运行 package 的 start。容器入口本身会准备 ROS/Zenoh 环境。
仓库的 [GitHub Actions](https://github.com/kaileliu/robot-agilex-piper-rk3588/actions)
执行 catalog 严格预检；规则与报告说明见 [提交准备文档](docs/PACKAGE_SUBMISSION.md)。

### NPU 后处理回归

安装 NPU 运行依赖后，在宿主执行：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=yolo_npu/runtime_deps:yolo_npu \
  python3 -m unittest discover -s yolo_npu/tests -p 'test_*.py' -v
```

现有 4 项测试覆盖 RGB/letterbox、DFL 和旋转坐标、NMS 及空结果。
历史同尺寸 736×1280 测试中，NPU FP16 完整检测约 186–199 ms，CPU 对照约
1.33 s；数字包含预处理与后处理，不含相机、HTTP 传输或机械臂动作。
这是历史样图测试，不能视为当前完整数据集精度或闭环抓取成功率。

### 验证状态

| 项目 | 状态 / 范围 |
| --- | --- |
| 11 个 package 的 `rbnx validate` | 已通过 |
| 官方 catalog 严格规则、本地和远端源文件检查 | 已通过；包括 metadata、contract/IDL、transport、依赖路径 |
| 源码仓库 GitHub Actions | 已通过；最新结果见 Actions 页面 |
| NPU 后处理 4 项回归 | 已通过 |
| 本机手眼标定独立验证 | 未通过，需重新核对采点与安装参数 |
| 实物抓取、搬运与放置验收 | 尚未完成 |

## 停止

先停止动作请求并确认机械臂状态，再在运行 `rbnx boot` 的终端按 `Ctrl-C`，在
NPU 服务终端按 `Ctrl-C`。要停止整个默认容器及现场后台服务，可从宿主执行：

```bash
bash stop_robonix_yolo.sh
```

脚本会核对容器挂载目录，停止默认容器和相关 systemd 服务，保留源码、模型和构建
产物。若使用自定义容器名，须同步设置 `ROBONIX_YOLO_CONTAINER_NAME`。停止软件
进程不等于机械臂完成回安全位或断电，应按 Piper 现场操作流程处理硬件。

## Package Catalog 接入

按 [官方 Submit 手册](https://packages.robonix.ai/submit/) 准备本仓库：

- 公开 GitHub 仓库，默认分支 `main`，根目录有 `robonix_manifest.yaml`。
- 整机发布名为 `robonix.robot.agilex.piper_rk3588`；`catalog:` 提供 version、
  description、license、tags、maintainers。
- 依赖源码在本仓库内，通过相对路径引用。10 个 Robonix 包和独立 NPU 支持包各自
  提供 `package_manifest.yaml`、README 及实际需要的契约/IDL 和资源。
- 本地变体使用 `.piper_rk3588` 发布名后缀，避免与已有上游包重名；能力 contract
  与 provider ID 保留。每个已声明能力都有实际 transport。
- 封面与 icon 可选，本次不放占位图片；本机密钥、缓存、日志和构建目录不提交。

**当前仅维护源码仓库，暂不申请官方 catalog 收录。** 将来确认提交时再在官方
`catalog.yaml` 中加入 name/repo/path；不要把整个源码上传到 catalog 仓库。
校验命令、依赖清单和发布文件导出方式见 [PACKAGE_SUBMISSION.md](docs/PACKAGE_SUBMISSION.md)。

## 故障排查

| 现象 | 检查方向 |
| --- | --- |
| 容器提示挂载目录不一致 | 同名容器属于另一目录；为本部署指定独立 `CONTAINER_NAME` |
| NPU 初始化失败 | 主机是否为 AArch64 / Python 3.10，驱动与设备权限，RKNN 版本及模型 JSON 配对 |
| 8770 端口被占用 | 是否仍有 CPU YOLO 或另一 NPU 实例；同一检测端口只运行一个后端 |
| 相机 INIT 超时或无法出图 | 标定台/其他驱动是否占相机，USB 链路和 Gemini 330 launch、分辨率设置 |
| 机械臂无状态反馈 | 宿主实际 CAN 接口、UP 状态与 1 Mbps 比特率、适配器及供电 |
| 有图像但落点偏移 | K/D、H、图像尺寸、去畸变顺序，以及基座坐标系与桌面高度是否一致 |
| 名称匹配或 Pilot 超时 | 本机清单中的模型地址/密钥、模型实际可用性、网络代理 |
| 文字交互正常但无语音 | FunASR 是否启动、输入 provider 是否选对；默认 Mock TTS 本就静音 |
| 预览/检查脚本找不到配置 | 现场脚本读 `deploy/piper/manifest.local.yaml`；根流程读 `robonix_manifest.local.yaml` |

系统端口 50051/50061/50081 与音频桥 60002 使用可信局域网通信，当前模板没有为它们
配置公网 TLS/鉴权。具体驱动、标定和部署日志排查见
[Piper 操作手册](docs/PIPER_MANUAL.md) 与各 package README。

## 仓库结构

```text
robonix_manifest.yaml              面向新安装与 Catalog 的根部署入口
catalog/package-paths.json         11 个发布 package 的目录清单
packages/primitives/              机械臂、相机、描述与音频原语
packages/services/                检测、抓取位姿、IK 与语音服务
packages/skills/                  垂直抓取技能及保留的实验路径
yolo_npu/                         RKNN 模型、运行库、HTTP 服务与回归测试
containers/piper/                 ROS 2 Humble / Robonix 镜像与容器入口
deploy/piper/                     Soma、URDF、现场清单模板
host-services/funasr/             可选宿主语音识别服务及模型获取脚本
tools/calibration/                内参、平面手眼与验证工具
robonix-client/                   操作员客户端源码
scripts/                          校验、发布导出与现场诊断工具
.github/workflows/                源码仓库 CI
docs/                            部署、架构与提交准备说明
vendor/PINS.md                    Robonix 固定版本获取方式
```

仓库保留了一些迁移前的 S10 与 VLA 实验代码。根清单不启用 openpi 路径：它还需要
额外腕部相机、框架、checkpoint 和动作配置，不属于当前 RK3588 YOLO 抓取验收范围。

## 来源与许可

部署代码按 [MulanPSL-2.0](LICENSE) 提供；各 package、第三方驱动、模型和 SDK
自己的许可与分发条件继续适用，包括 Apache-2.0 驱动和
[RKNN SDK License](yolo_npu/vendor/RKNN-LICENSE)。

- [Robonix](https://github.com/syswonder/robonix) 与 [官方文档](https://book.robonix.ai/)：运行时、能力契约、Soma 和部署规范。
- [packages/UPSTREAMS.md](packages/UPSTREAMS.md)：各包上游版本与本地改动。
- [SOURCES.md](SOURCES.md)：本部署展开和适配来源。
- [yolo_npu/SOURCES.md](yolo_npu/SOURCES.md)：原始权重、RKNN SDK 和运行库的来源、版本与校验值。
- [参考部署 README](https://github.com/syswonder/robot-agilex-ranger_with_piper_mujoco)：本文的组织方式参考，实际功能和命令以本真机仓库为准。
