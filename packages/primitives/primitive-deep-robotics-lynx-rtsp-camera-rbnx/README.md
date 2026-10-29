# primitive-deep-robotics-lynx-rtsp-camera-rbnx

Robonix 原语：把 **DeepRobotics Lynx S10 狗自带相机**（官方 `robot_drivers` 采集后推到狗上 mediamtx 的 RTSP 流）接进 robonix，占用 `primitive/camera/*` 命名空间。

接口与其他相机原语（D435i 等）**完全一致**，上层技能通过 atlas 发现 `snapshot`/`rgb` 即可，对相机品牌无感知。

## 为什么存在

InternVLA 导航需要"随时取一帧 RGB"（DualVLN 是 RGB-only，已验证）。狗相机的硬件（SPCA2650 ×4）由狗端官方 `usb_cam_multi_node` 采集并推流到狗上 mediamtx（RTSP :8554，路径 `video0/2/4/6`，其中两路活跃）。本原语把这条 RTSP 流拉进容器：

- 连续发布 RGB 话题（`rgb8`，按 `fps` 节流）；
- 提供一对一 JPEG `snapshot` 的 MCP 能力（导航技能真正用的那个）。

拉流用容器自带的 `python3-opencv`（`cv2.VideoCapture(url, cv2.CAP_FFMPEG)`）解码，**不需要**编译任何第三方驱动，也不需要 RealSense/Orbbec SDK。

## 能力面

| 契约 | 模式 | 传输 | 说明 |
|---|---|---|---|
| `robonix/primitive/camera/driver` | rpc | gRPC | `Driver(CMD_INIT, config_json)` — 生命周期门 |
| `robonix/primitive/camera/rgb` | topic_out | ROS 2 | `/<camera_name>/color/image_raw`（sensor_msgs/Image, rgb8） |
| `robonix/primitive/camera/snapshot` | rpc | MCP | 一次性 JPEG 抓帧 |

## 配置（经 `Driver(CMD_INIT, config_json)` 下发）

| 键 | 默认值 | 含义 |
|---|---|---|
| `rtsp_url` | `rtsp://admin:drcam@123@10.21.33.103:8554/video4` | mediamtx 流地址（凭据来自狗上 `mediamtx.yml`；`video0/2/4/6` 四路，两路活跃） |
| `camera_name` | `camera_lynx` | 发布话题的 ROS 命名空间（部署清单可覆盖） |
| `rgb_topic` | `/<camera_name>/color/image_raw` | 消费者通过 atlas 解析到的话题 |
| `fps` | `30` | **仅发布节流**，源帧率由相机决定 |
| `sentinel_timeout_s` | `30.0` | Init 心跳超时 |

> ⚠️ 默认 `rtsp_url` 里内嵌了凭据。这是狗出厂设置、不是我们管的密钥；部署时应在清单里显式覆盖（真值放本地/密钥文件），不要依赖这个默认值。

## 目录

```
package_manifest.yaml   契约声明 + build/start 钩子
rtsp_camera/main.py     原语实现：RTSP 拉流 → rgb 话题 + snapshot RPC
scripts/build.sh        rbnx codegen（MCP + ROS2 IDL）+ 生成 IDL 的 colcon build
scripts/start.sh        exec python3 -m rtsp_camera.main
```

## 构建 / 单独运行

```bash
bash scripts/build.sh
# 需要 atlas 在跑；config 由 rbnx boot 通过 CMD_INIT 传入
bash scripts/start.sh
```

## 许可

Apache-2.0。
