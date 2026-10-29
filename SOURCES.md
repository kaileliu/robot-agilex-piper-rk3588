# 本次展开的资源来源

- Robonix 部署：`/root/robot-agilex-piper-ga`，提交 `6190fc996f52fd833c44d110f9357347f7650b48`，原仓库工作区保持干净。
- 本地实机清单：由原 `deploy/piper/manifest.local.yaml` 复制，权限 600；后按用户提供的接口更新 pilot 与抓取名称匹配器的地址、密钥，并在原模型超时后切换到已验证可用的 `deepseek-flash`。USB-CAN 地址适配重启后的总线编号。相机、标定和抓取动作配置未改，修改前清单另存为本地私密备份。
- 已部署镜像：`piper-robonix:std-humble-arm64`，`sha256:82f10c41e5e51f38ffc720b0527faad5e6d5c376d9036859696362d1675588a9`；框架 `v0.1.0 (fb68bc5), built 2026-09-17`。复用本机镜像，未重建或修改原镜像。
- YOLO NPU：`/root/yolo_npu`，除下载/运行缓存 `.cache/` 外复制；包含 RKNN FP16、ONNX、原 best.pt、RKNN 2.3.2 运行库、本地依赖、样例、测试及历史报告。
- 复制时不带旧 PID/boot 状态、Python 虚拟环境和下载缓存。ROS 包在独立容器内首次构建完成，保留新的产物供后续直接使用。
- 新增：NPU/Robonix 启停入口、宿主 CAN 准备、原抓取链路只读检查、控制命令转发及本次实测报告。
- 实时预览：`scripts/live_preview.py`、`scripts/live_preview.html` 与 `preview_robonix_yolo.sh`；订阅现有相机并共享 NPU 检测，提供浏览器视频流。
- 副本调整：宿主 YOLO 入口指向 NPU；容器默认名改为 `robonix_yolo_dev` 并检查挂载目录；容器入口读取本地网络代理配置以访问云端模型；Docker/Git 忽略新增运行资源及私密网络配置。

测试与限制见 [integration.json](reports/integration.json) 和 [使用说明](README_ROBONIX_YOLO.md)。
