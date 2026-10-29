# Robonix + RK3588 NPU YOLO

当前已用 13 个现场采点拟合，并将结果及当前相机内参写入本地配置的 pick / grasp_pose。但独立验证点误差为 30.28、12.84、44.72 mm，验证未通过，chat 自动抓取与 8088 预览保持暂停。相机、机械臂反馈及 NPU 服务可单独检查；当前标定页为 <http://192.168.137.20:8090/handeye>。详见 [拟合结果及误差图](reports/calibration_fit.md) 和 [标定说明](CALIBRATION.md)。

已从 `/root/robot-agilex-piper-ga` 复制展开到本目录，复用本机已部署的 Docker 镜像 `piper-robonix:std-humble-arm64`。`packages/` 和抓取逻辑沿用原项目；本地清单的手眼矩阵与相机内参已按上方状态更新，云端模型连接使用用户提供的接口。已验证的 NPU 模型与运行依赖位于 `yolo_npu/`；原两个目录未修改。

原抓取技能与检测服务仍访问 `http://127.0.0.1:8770/detect`。默认使用 FP16、736×1280 输入的 RKNN 模型，原相机输出保持 1280×720。运行不需要原 CPU YOLO 虚拟环境。

## 切换标定模式前的实机检查（2026-09-23）

- 容器 `robonix_yolo_dev` 挂载本目录到 `/root/workspace`，复用原镜像。首次构建已完成，产物保存在本目录的各包 `rbnx-build/`。
- Orbbec Gemini 336L 已出图；Piper、相机、检测、抓取位姿、IK 服务均 ACTIVE。抓取技能 INIT 成功，INACTIVE 表示等待首次调用。
- 宿主 `can0` 已设为 1 Mbps。设备重启后 USB 总线编号变化，核对适配器序列号后，将本地清单中的 `can_usb_address` 更新为 `8-1.2:1.0`。启动入口在宿主启用总线，解决原容器无 NET_ADMIN 权限时无法启用 CAN 的问题。
- 原 IK 启动流程已执行回安全位并确认成功；实机反馈 `arm_status=0`、`err_code=0`。
- 原抓取技能的实时取图、去畸变、NPU HTTP 检测、双帧一致性及 Robonix 抓取位姿计算已贯通。单次 HTTP 检测约 252 ms；NPU 后处理 4 项回归测试通过。
- 最新就绪检查：相机已俯视桌面，胡萝卜目标通过双帧一致性，置信度约 0.86，抓取位姿计算成功。六个电机已使能，机械臂错误码为 0，聊天链路测试通过。此次检查只执行了原启动回安全位流程，未执行物体抓取；相机调整后的标定精度仍需实物验证。见 [就绪报告](reports/readiness.json)、[检测画面](reports/camera_npu_detection.jpg) 和 [实时报告](reports/live_pipeline.json)。
- 原清单中停用备用的 `openvla_client` 因缺少 `action_min` 初始化失败；它独立于本次 YOLO 抓取路径，本次未修改。

## 使用

正常运行使用临时 systemd 服务保持 NPU 与 Robonix 在后台，没有设置开机自启。当前先完成上方标定流程、退出标定模式，再运行以下命令。不要同时启动原部署占用同一相机、CAN 和服务端口。

```bash
cd /root/robonix_yolo
# 后台启动整套：NPU → 宿主 CAN → 原 Robonix boot
bash run_robonix_yolo.sh --background

# 查看当前提供者
docker exec robonix_yolo_dev rbnx caps

# 不发机械臂指令的实时检查；保存画面及报告到 reports/
bash check_robonix_yolo.sh

# 查看启动与推理日志
tail -f run-logs/boot.log
# run-logs/yolo_npu.log 为 NPU 日志
```

相机恢复原标定视角、目标处于工作区后，通过原 Robonix pilot/executor 发控制指令：

```bash
bash ask_robonix.sh "抓起胡萝卜并保持"
```

也可以进入交互式聊天，再输入同样的抓取指令：

```bash
docker exec -it robonix_yolo_dev bash /docker-entrypoint.sh rbnx chat
```

抓取指令会使机械臂实际运动。私密值在 `deploy/piper/manifest.local.yaml`（权限 600）。实际物体抓取尚未验证。

## 云端模型连接

pilot 和抓取技能的物品名称匹配器统一使用用户提供的 `https://newapitoken.zhenghechuhai.cn/v1`，当前模型为 `deepseek-flash`。分别对应清单中的 `system.pilot.vlm` 和 `skill.pick.config.grab_match_llm_*`。本次就绪检查中原 `deepseek-v4-pro` 请求持续超时，切换到已实测可用的模型后，流式 JSON、原匹配器将“胡萝卜”匹配为 `carrot`、以及 chat 链路均已通过。

服务已重启，chat 使用的 `Liaison.SubmitTask → Pilot` 链路回复“聊天已就绪”，耗时约 7.8 秒，没有调用工具。记录见 [最新聊天检查](reports/readiness_chat.json)；早先的接口与代理修复记录见 [连接修复报告](reports/llm_connection_fix.json)。旧 chat 会话需退出后重新进入。

本机直连外网存在 DNS 解析失败，宿主机通过网络代理访问模型接口。容器入口会读取本目录的 `deploy/piper/network.local.env`，让模型请求使用相同代理；`NO_PROXY` 保持本地 Robonix、YOLO 连接直连。该文件权限为 600，并已排除出 Git 和镜像构建。更换宿主机网络代理后，同步更新此文件并重启部署。旧清单保存在 `deploy/piper/manifest.before-upstream-*.local.yaml`。

## 停止与检测

```bash
# 停止整套，保留容器和构建产物
bash stop_robonix_yolo.sh

# 只启动 NPU；相机与机械臂不启动
bash run_robonix_yolo.sh --yolo-only

# 仅检测服务健康
bash run_robonix_yolo.sh --check
curl -s http://127.0.0.1:8770/health
```

`bash run_robonix_yolo.sh`（无参数）以前台方式运行 Robonix。`host-services/yolo/run_yolo_server.sh` 也已改为 NPU 的兼容入口，前台运行检测；旧 CPU 启动脚本仅保留为 `run_yolo_server_cpu.sh`。

## 实时检测画面

在 Robonix 和 NPU 服务运行时执行：

```bash
cd /root/robonix_yolo
bash preview_robonix_yolo.sh
```

浏览器访问 `http://192.168.137.20:8088`（RK3588 当前局域网地址）或本机的 `http://127.0.0.1:8088`。页面默认显示带检测框的画面，可切换原始画面、保存当前帧，并查看刷新率、检测耗时和目标置信度。脚本也会打印当前网卡地址。

预览订阅现有 Robonix 相机话题，使用原抓取配置去畸变、调用同一个 NPU YOLO 服务；不执行名称匹配或机械臂动作。所有浏览器共享一次检测循环，默认最多 3 FPS，实测约 2.6 FPS。检测与抓取共享 NPU，开启预览会增加持续推理负载，可按需停止预览。

```bash
# 只停止预览
bash preview_robonix_yolo.sh --stop
```

预览随容器停止而退出，重启整套服务后需重新运行预览脚本。日志为 `run-logs/preview.log`，连续视频帧验证记录在 `reports/live_preview_check.json`。

原项目操作细节见 [Piper 手册](docs/PIPER_MANUAL.md)。本副本的容器名统一为 `robonix_yolo_dev`，按本页命令使用；原手册里的 CPU 安装步骤不再需要。历史 NPU 性能报告与重建脚本见 `yolo_npu/`，其中的历史来源路径保持原记录。资源版本见 [SOURCES.md](SOURCES.md)。
