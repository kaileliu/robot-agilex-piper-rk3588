# 各包的上游对照

这份文件记**基线**（本地副本是从上游哪个 commit 复制进来的）和**本地改动**（相对基线我们改了什么）。

基线就是复制那一刻上游所在的 commit：本仓库的副本 = 基线 + 本地改动。

Package Catalog 接入补充：根入口使用的 10 个包另有发布元数据改动——
`package.name` 增加 `.piper_rk3588` 后缀、能力补齐 `transport`，麦克风与语音包
补齐 tags/maintainers，麦克风包增加 README。下表“无”指原运行源码基线，
不包含这次元数据补充。运行时 provider ID、contract ID 未改。

上游仓库都在 `https://github.com/syswonder/` 下，下面只写仓库名。

| 包（`packages/` 下） | 上游 | 基线 | 本地改动 |
|---|---|---|---|
| `primitives/primitive-agilex-piper-description-rbnx` | `primitive-agilex-piper-description-rbnx` | `b009854` | 无 |
| `primitives/primitive-agilex-piper-arm-rbnx` | `primitive-agilex-piper-arm-rbnx` | `0052f2b` | 新增 CAN 接口的动态发现与拉起，找不到 USB-CAN 时回退基线原逻辑 |
| `primitives/primitive-audio-mic-rbnx` | 官方版是 `primitive-audio-driver-rbnx`（无同名上游） | `6f7b46a` | 从官方版裁出纯麦克风变体（去掉 speaker、改命名空间与契约集）；TODO 换回官方版试跑，注意它的 speaker 会和 `audio_client_bridge` 撞契约 |
| `primitives/primitive-audio-client-bridge-rbnx` | `primitive-audio-client-bridge-rbnx` | `759e3b1` | 无 |
| `primitives/primitive-deep-robotics-lynx-chassis-rbnx` | `primitive-deep-robotics-lynx-chassis-rbnx` | `e97a9d8` | 新增站立、趴下两个 MCP 能力 |
| `primitives/primitive-deep-robotics-lynx-rtsp-camera-rbnx` | 无上游，本项目原创 | — | lynx s10搭载相机的原语 |
| `primitives/primitive-intel-d435i-manual-ae-camera-rbnx` | 派生自 `primitive-intel-realsense_d435i-camera-rbnx` | `489e9ee` | 走 pyrealsense2 直连 + 手动 AE，以解决官方驱动自动曝光失效的问题 |
| `primitives/primitive-orbbec-dabai_dcw-camera-rbnx` | `primitive-orbbec-dabai_dcw-camera-rbnx` | `089bf54` | 把 launch 文件名从写死改成配置项，以支持 Gemini 336L |
| `services/service-grasp-pose-rbnx` | `service-grasp-pose-rbnx` | `78a54ac` | 只改注释和文档（说明 `approach_dist` 不是本服务读的参数） |
| `services/service-object-detect-rbnx` | `service-object-detect-rbnx` | `04e5806` | 增加双检测后端（`backend` 字段选 yolo/vlm）；另修红蓝通道互换——收到的图是 RGB 顺序、代码按 BGR 读，改成先转成 BGR |
| `services/service-roboarm-ik-rbnx` | `service-roboarm-ik-rbnx` | `919fdfc` | 无 |
| `services/service-speech` | 无独立仓库；上游是 robonix 主仓的 `services/speech/` | `fb68bc52` | 集成 FunASR（本地模型比腾讯 ASR 服务的可用性更好），另加 ASR 转写日志；依赖里把 mcp 封在 <2（robonix-api 用的是 mcp 1.x 的 API 却没封顶，装到 2.x 服务起不来） |
| `skills/skill-internvla-navigation-rbnx` | `skill-internvla-navigation-rbnx` | `e6c369b` | 无；端到端验证还没做完 |
| `skills/skill-vla-openpi-rbnx` | 无上游，本项目自研 | — | 自研的 openpi VLA 客户端；当前跑不起来——openpi 框架与 pi05_piper checkpoint 没随机器迁移 |
| `skills/skill-pick-vertical-grasp-rbnx` | `skill-pick-vertical-grasp-rbnx` | — | 本项目重写，不按上游结构走 |
| `skills/skill-pick-vertical-grasp-piper-rbnx` | 同上 | — | 同上 |
