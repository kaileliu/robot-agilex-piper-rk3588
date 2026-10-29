# Piper Package Catalog 接入与提交

本项目按 [官方 catalog](https://github.com/syswonder/robonix-package-catalog)
的当前规则接入：整机清单必须在仓库根目录，普通包可通过 catalog `path:` 收录同一
仓库的子目录。Catalog PR 只修改其根目录 `catalog.yaml`；清单、README、契约与资源
都放在本整机仓库，由官方 CI 读取默认分支。图片是可选项，本次不放占位图。

根入口为 `robonix_manifest.yaml`，发布名 `robonix.robot.agilex.piper_rk3588`。
它与 `deploy/piper/manifest.local.yaml` 的现场入口分开；不替换正在使用的本地清单。
新入口的相对包路径全在本仓库内，Soma 使用 `deploy/piper/soma.yaml`、
`deploy/piper/soma_config.yaml` 和 `deploy/piper/urdf/piper.urdf`。

## 收录范围

为了保留本项目修改、不覆盖已收录的上游包，10 个 Robonix 依赖的 `package.name`
使用 `.piper_rk3588` 后缀。provider ID、能力 contract ID 和调用路径不变。
维护者信息沿用已有包的署名，发布前可由实际维护者统一更新。

| 目录（相对仓库根） | 用途 | 接口 |
| --- | --- | --- |
| packages/primitives/primitive-orbbec-dabai_dcw-camera-rbnx | Gemini 336L，部署覆盖 launch 文件 | ROS 2 图像/内参，gRPC 生命周期 |
| packages/primitives/primitive-agilex-piper-arm-rbnx | Piper CAN 控制及反馈 | ROS 2，gRPC 生命周期 |
| packages/primitives/primitive-agilex-piper-description-rbnx | URDF、TF 发布 | 内部 ROS 2 TF，不声明路由能力 |
| packages/primitives/primitive-audio-mic-rbnx | 本机 ALSA 麦克风 | gRPC |
| packages/primitives/primitive-audio-client-bridge-rbnx | 操作员端麦克风/扬声器桥 | gRPC，内部 WebSocket |
| packages/services/service-object-detect-rbnx | HTTP YOLO / VLM 检测适配 | MCP，gRPC 生命周期 |
| packages/services/service-grasp-pose-rbnx | 像素到抓取位姿 | gRPC |
| packages/services/service-roboarm-ik-rbnx | Python IK 及动作执行 | gRPC |
| packages/services/service-speech | ASR/TTS/对话 | gRPC，MCP 的 speak/list_speakers |
| packages/skills/skill-pick-vertical-grasp-piper-rbnx | pick/demo/grab/put_in_box/put_down | MCP，gRPC 生命周期 |
| yolo_npu | RK3588 NPU HTTP 推理后端 | 宿主 HTTP 支持进程，无 Atlas 能力 |

完整机器可通过文字命令使用抓取链路；语音需另外启动宿主 FunASR。音频桥与本机
麦克风同时存在时，客户端必须明确选定输入 provider。NPU 包在宿主启动，不加入
容器内 `rbnx service:` 列表：HTTP 不是 catalog 接受的 Robonix contract transport，
因此它准确声明 `capabilities: []`，Robonix-facing 接口由检测服务提供。

旧清单中的 openpi VLA 是另一条尚未迁移完成的可选路径，依赖额外腕部相机、策略
服务和模型，不是 NPU 抓取的依赖。根入口不启用它，避免缺少 `action_min` 等配置
导致初始化失败。原代码与旧清单保留供后续独立接入。

## 从源码安装

1. 在 RK3588 Linux ARM64 主机安装 Docker、Python 3.10、uv、Git，确认 RKNPU 驱动
   和设备可用。获取本仓库的完整源码，模型与 SDK 二进制随源码保留。
2. 按 `vendor/PINS.md` 获取固定版本 Robonix 及子模块，再用
   `bash containers/piper/run.sh -b -e 'rbnx --version'` 构建容器；已有对应镜像可复用。
   包内 build 脚本负责 ROS、Python 依赖和 codegen。
3. 在宿主执行 `bash yolo_npu/setup_runtime.sh`，然后在独立终端运行
   `bash yolo_npu/run_yolo_npu.sh`。用 `curl -fsS http://127.0.0.1:8770/health`
   核对设备为 `rk3588-npu`。不要同时运行另一个占用 8770 的检测服务。
4. `cp robonix_manifest.yaml robonix_manifest.local.yaml`，
   `chmod 600 robonix_manifest.local.yaml`。编辑这个完整清单副本中的 LLM 占位值、
   USB-CAN 接口、相机 launch/分辨率、标定矩阵及放置/观察位参数。
   `PIPER_WORKSPACE=/root/workspace` 是容器挂载路径；若直接在宿主运行需改为实际绝对
   根路径。模板的矩阵与姿态来自历史部署，不能用于替代新安装的标定。
5. 使用标定工具完成并通过独立验证后，在现场确认 CAN 的 1 Mbps 比特率、使能策略与
   电机反馈。根清单默认 `can0`、`auto_can_setup: false`、`auto_enable: false`。
   允许驱动自动使能时才把 `auto_enable` 改为 true。IK 初始化可回观察位，boot 不是
   纯读取操作。确认工作区清空后再运行：

   ```bash
   bash containers/piper/run.sh -e 'cd /root/workspace && rbnx build -f robonix_manifest.local.yaml'
   bash containers/piper/run.sh -e 'cd /root/workspace && rbnx boot -f robonix_manifest.local.yaml --no-update-check --verbose'
   ```

语音的宿主安装、模型获取及启动见 `host-services/README.md` 和
`host-services/funasr/`。默认 TTS 使用静音 Mock 后端；配置真实 TTS 后才能获得语音
回复。LLM 密钥、网络代理与机器专用参数不进入公开源码。

## 提交前校验与源码导出

预检直接调用官方 builder 的严格新条目校验；只把 GitHub 文件读取适配为本地发布
文件清单。校验 metadata、名称唯一性、transport、包类型、契约、IDL、图片和整机
依赖路径。Robonix 全局契约使用本部署固定的 `vendor/robonix`；因此本地通过与远端
默认分支实际可访问是两项验证，不能据此声称远端 CI 已通过。

```bash
python3 -m venv /tmp/piper-catalog-venv
/tmp/piper-catalog-venv/bin/pip install -r catalog/requirements.txt
git clone https://github.com/syswonder/robonix-package-catalog /tmp/robonix-package-catalog
git -C /tmp/robonix-package-catalog checkout 65cf91c32acb52e0ec72ff7ef4a003c02c78f00f

# 替换为实际要发布的 GitHub 仓库。
/tmp/piper-catalog-venv/bin/python scripts/check_package_submission.py \
  --catalog-source /tmp/robonix-package-catalog \
  --repo https://github.com/OWNER/robot-agilex-piper-rk3588
/tmp/piper-catalog-venv/bin/python scripts/export_package_submission.py
```

生成 `dist/catalog/catalog.additions.yaml`、`dist/catalog/validation.json`、
`dist/piper-rk3588-source.tar.gz` 及逐文件 SHA256 清单。导出使用 Git 的 ignore 规则，
不依赖当前目录本身是 Git 仓库，排除私密覆盖文件、日志、构建产物、Python 依赖缓存。
扫描常见密钥和私钥格式、拒绝越界符号链接及超过 GitHub 100 MiB 的单文件。
`dist/` 自身不会递归进入导出。导出不会更改源项目的 Git 状态。

发布后，本仓库 Actions 会以同一官方校验器执行预检并上传报告。
将 additions 中的 `packages`/`robots` 条目合并进 catalog 现有同名列表，不能覆盖已有
列表或增加第二个顶层同名键。Catalog PR 只提交其 `catalog.yaml` 的这些新增条目。

目标 GitHub 仓库尚未确认时，预检默认 URL 含 `OWNER`，它只用于本地路径校验，
绝不能直接提交。实际步骤是：先发布源码到公开可读的默认分支；以真实 `--repo`
重新预检；再提交 catalog PR。官方 CI 将使用 GitHub API 重新读取实际内容，包括
当前 Robonix 全局契约、文件树和图片。只有那个 CI 成功才能确认远端收录可行。

## 验证边界

目录接入通过不等于实机抓取通过。当前现场手眼标定独立验证未通过，物体抓取尚未
验证；本次只做源码、目录规范和既有 NPU 后处理回归验证，不启动机械臂或改变设备
状态。现场记录保留在本机，不打包到公开发布文件。
