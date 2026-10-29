# primitive-intel-d435i-manual-ae-camera-rbnx

Robonix 原语：**Intel RealSense D435i** RGBD 相机，占用 `primitive/camera/*` 命名空间。RGB + 对齐深度（+ 内参）都挂成通用契约，消费者通过 atlas 解析话题，不必硬编码 `/camera_435i/...`。

**本包的取流方式与"官方 realsense 驱动版"不同**：它用容器自带的 **pyrealsense2（Python SDK）直连相机（UVC）**，并且**关掉硬件自动曝光、改用自己实现的手动 AE**。因此：

- 不需要 Intel 的 `librealsense2` **系统包**；
- 不需要 colcon 编译 `realsense2_camera` **ROS 驱动**（包里也没有 vendored 驱动源码）；
- 构建只做 `rbnx codegen`（见 `scripts/build.sh`）。

## 为什么是手动 AE

官方驱动的自动曝光在这台机器上表现不可用（画面忽明忽暗/过曝）。手动 AE 的做法：每帧统计彩色图的亮度中位数，并对高光像素占比施加惩罚 → 得到一个目标曝光误差 → 按比例微调 `rs.option.exposure`（带死区，避免抖动）。环境光缓慢变化时能持续跟随，实测收敛。

实现位置：`realsense_camera/main.py` —— 打开流时 `set_option(rs.option.enable_auto_exposure, 0)` 关掉硬件 AE，采集循环里按上述规则更新 `_rs_exposure`。

## 能力面

| 契约 | 模式 | 传输 | 说明 |
|---|---|---|---|
| `robonix/primitive/camera/driver` | rpc | gRPC | `Driver(CMD_INIT, config_json)` — 生命周期门 |
| `robonix/primitive/camera/rgb` | topic_out | ROS 2 | `<camera_name>/color/image_raw`（best_effort） |
| `robonix/primitive/camera/depth` | topic_out | ROS 2 | 对齐到彩色的深度流 |
| `robonix/primitive/camera/intrinsics` | topic_out | ROS 2 | 彩色流的 `sensor_msgs/CameraInfo`（reliable）；深度是对齐到彩色的，消费者复用彩色 K 做反投影 |
| `robonix/primitive/camera/snapshot` | rpc | MCP | 一次性 RGB（JPEG） |
| `robonix/primitive/camera/depth_snapshot` | rpc | MCP | 一次性深度（JPEG 可视化） |

## 配置（经 `Driver(CMD_INIT, config_json)` 下发）

| 键 | 默认 | 含义 |
|---|---|---|
| `camera_name` | `camera_435i` | ROS 命名空间（话题前缀） |
| `width` / `height` / `fps` | `1280` / `720` / `30` | 彩色流档位；**必须与手眼标定所用分辨率一致**（本部署为 1280×720） |
| `rgb_topic` / `depth_profile` / `intrinsics_topic` | 见 `package_manifest.yaml` | 话题与深度档位覆盖 |
| `sentinel_timeout_s` | `30.0` | Init 心跳超时 |

## 生命周期

`start.sh` 只拉起 atlas 桥（gRPC server + 注册 `camera/driver`），然后阻塞等待 `Driver(CMD_INIT)`。Init 时按 config 打开彩色+深度流（深度对齐到彩色）、等首帧、再声明 `rgb`/`depth`/`intrinsics` 三个话题，最后返回 ok —— atlas 只广告已确认在发布的话题。

## 运行依赖

- **相机设备直通**：容器需要 `/dev` 里的 UVC/USB 节点（见 `containers/*/run.sh` 的 `--device-cgroup-rule` 与 `-v /dev:/dev`）。
- **pyrealsense2**：由 S10 镜像自带（`containers/s10/payloads/pyrealsense2/`，其 README 记录了版本与"重新获取"的退路）。

## 布局

```
package_manifest.yaml        契约声明 + build/start 钩子（build 只做 codegen）
realsense_camera/main.py     原语实现：pyrealsense2 打开流 + 手动 AE + 发布/声明 + snapshot MCP
scripts/build.sh             rbnx codegen（MCP + ROS2 IDL）+ 生成 IDL 的 colcon build
scripts/start.sh             source ROS/IDL 环境 → exec python3 -m realsense_camera.main
config.spec                  运行时配置说明
```

## 单独运行

```bash
bash scripts/build.sh
# 需要 atlas 在跑；config 由 rbnx boot 通过 CMD_INIT 传入
bash scripts/start.sh
```

## 许可

Apache-2.0。
