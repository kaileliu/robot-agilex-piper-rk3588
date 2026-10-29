# AgileX Piper + RK3588 NPU YOLO

Robonix 整机部署：AgileX Piper 六轴机械臂及夹爪、Orbbec Gemini 336L
相机、RK3588 NPU YOLO OBB 检测、Python IK 和垂直抓取技能。可选语音链路使用
ALSA/远端音频桥及宿主 FunASR。适用 Linux ARM64、Python 3.10、ROS 2 Humble；
RKNN 运行库/驱动要求见 [NPU 说明](yolo_npu/README.md)。这是真机部署，不包含仿真。

面向 Package Catalog 的入口是根目录 [robonix_manifest.yaml](robonix_manifest.yaml)。
10 个 Robonix 依赖及独立的宿主 NPU 支持包随源码提供，封面暂留空。
发布方式、完整依赖列表、安装步骤和官方 CI 预检见
[接入与提交说明](docs/PACKAGE_SUBMISSION.md)。

当前手眼标定的独立验证未通过，尚未验证真实物体抓取。发布配置是需现场配置的模板，
不包含本机密钥与标定覆盖文件；首次运行前必须完成标定验证、CAN 配置及运动参数核对。
`auto_enable` 默认关闭，启用机械臂后 IK 初始化可能移动到观察位。

本机现有运行记录和操作入口见 [现场使用说明](README_ROBONIX_YOLO.md)。
软件按 [MulanPSL-2.0](LICENSE) 提供；各依赖自己的许可优先，
包括 Apache-2.0 驱动及 [RKNN SDK 许可](yolo_npu/vendor/RKNN-LICENSE)。
源码和模型来源见 [SOURCES.md](SOURCES.md)、[包来源](packages/UPSTREAMS.md)
和 [NPU 来源](yolo_npu/SOURCES.md)。
