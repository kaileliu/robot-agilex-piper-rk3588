# S10 标定台

一个 Flask 服务（`:8080`），把 S10 的标定流程分成六页：**测桌面 z → 内参 → 示教 → 手眼 → 抓取自检 → 回零失能**。

整套跑在**宿主机上**，直接连接硬件：相机用 pyrealsense2（与相机原语同一套驱动，1280×720 + 手动 AE），机械臂用 piper_sdk 直连 USB-CAN（CAN 口按 sysfs 自动探测，与机械臂原语同一套判断）。标定在启动 robonix 之前做，不需要起一套栈。

## 跑起来

```bash
conda create -y -n s10-calib python=3.10
conda activate s10-calib
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r tools/calibration/s10/requirements.txt
cd tools/calibration/s10 && python app.py --port 8080
# 浏览器打开 http://<本机IP>:8080
```

`抓取自检` 那页需要 YOLO，要再装一个可选依赖（会连带下载 torch，几百 MB）：

```bash
conda activate s10-calib
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple "ultralytics>=8.4"
```

## 六步

| 页 | 干什么 | 产物 |
|---|---|---|
| `/measure_z` | 示教拖动夹爪尖贴桌面，记末端 z | `artifacts/desktop_z.json` |
| `/intrinsics` | 棋盘格采 10~20 张，解内参 K/D（画面是**原始**的，不能去畸变） | `artifacts/intrinsics/{camera_matrix,dist_coeffs}.npy` |
| `/teach` | 拖动示教记录关节角（观察位姿、安全位都靠它） | `artifacts/poses/taught_pose.json` |
| `/handeye` | 点像素 → 拖臂对准 → 记录；采够后拟合单应（画面**已按内参去畸变**） | `artifacts/handeye/{2d_image_points,2d_robot_points,2d_homography}.npy` |
| `/check_catch` | YOLO 找一个目标，单应映射成臂坐标，真抓一次（会动臂） | 无 |
| `/park` | 收尾：回零并失能 | 无 |

前四页有先后顺序：**手眼标定必须先有内参**（没内参时这一步在首页就是「不可用」，直接开 URL 也进不去、接口同样被拒——免得标出一份与运行时不符的矩阵）；**抓取自检需要内参 + 单应 + 桌面高度 + 观察位姿**（缺哪个，页面会直接告诉你）。相机装在机械臂上，所以内参标定期间机械臂要一直停在观察位姿，手眼每记录一个点要回一次拍照姿态。

页面上还能看到：当前谁占着相机/机械臂（可一键接管）、每一步产物的摘要（首页）。

## 产物怎么进部署清单

| 产物 | 清单里的键 |
|---|---|
| `artifacts/intrinsics/camera_matrix.npy` | `skill.pick.config.camera_matrix` |
| `artifacts/intrinsics/dist_coeffs.npy` | `skill.pick.config.dist_coeffs` |
| `artifacts/handeye/2d_homography.npy` | `skill.pick.config.homography_matrix` 和 `service.grasp_pose.config.homography_matrix`（同一份矩阵，两处都要改） |
| `artifacts/desktop_z.json` 的 `desktop_z` | `skill.pick.config.desktop_height`（就是这个值，别加余量）；`service.grasp_pose.config.default_desktop_height` 则是 `desktop_z + 0.02`（抓取余量 2cm） |
| `artifacts/poses/s10_calib_pose.json` 的 `joints_deg` | `service.roboarm_ik.config.init_joints_deg`（观察/拍照位姿） |
| `artifacts/poses/taught_pose.json` | 不用直接抄——它是示教原料，挑一条写进 `s10_calib_pose.json` |

⚠️ **`artifacts/` 里现有的内参与单应是 2026-08-27 用旧的 UVC 路径标定的**。取图统一到 pyrealsense2 之后，应当在真机上重新标定一次（`/intrinsics` → `/handeye`），再把新值抄进清单。

## 目录：按角色分层

```text
s10/
├── app.py        唯一入口：Flask 服务，挂页面与共享资源
├── paths.py      产物路径（唯一来源）
├── config.py / config.yaml   本部署的配置（每个键都注明谁在读）
├── steps/        本部署的步骤：每步一个模块（页面 + 动作 + 产物摘要）
│                 —— intrinsics / handeye 只是把 common/steps/ 的公共实现绑到本部署路径
├── vision/       S10 的取图（pyrealsense2）与检测（YOLO）
├── models/acm.pt 抓取自检用的 YOLO 权重
└── artifacts/    标定产物（唯一输出目录）
```

机械臂相关的代码（CAN 探测、直连会话、IK、抓取后端、URDF）在 `../common/robot/`，与 piper 标定台共用同一份，因为两个部署用的是同一个臂、同一套指令。

## 与 roboarm 原版的对应关系

这套工具是从 roboarm 的标定脚本里挑出需要的部分重构出来的，不是整目录拷贝：

| 老位置（s10-calib） | 现在 |
|---|---|
| `scripts/arm_home_disable.py` | `steps/park.py` |
| `scripts/measure_z.py` | `steps/measure_z.py` |
| `scripts/calibrate_intrinsics_s10.py` | `steps/intrinsics.py`（实现在 `common/steps/intrinsics.py`） |
| `scripts/calibrate_handeye_s10_web.py` | `steps/handeye.py`（实现在 `common/steps/handeye.py`） |
| `scripts/teach_tool.py` | `steps/teach.py` |
| `scripts/catch_s10.py` | `steps/check_catch.py` |
| `roboarm/arm/arm_base.py` | `../common/robot/arm.py` |
| `roboarm/arm/piper_ctrl_by_sdk.py` | `../common/robot/piper_sdk.py` |
| `roboarm/arm/calibrate_handeye_2d.py` | `../common/homography.py`（只取三个纯函数） |
| `roboarm/calibration/calibrate_intrinsics.py` | `../common/chessboard.py` |
| `scripts/s10_camera.py` | `vision/camera.py` 的 `S10Camera` |
| `roboarm/object_detect/detect.py` | `vision/detect.py` |
| `roboarm/utils/config_getter.py` | `config.py` |
| `roboarm/utils/cv2_display.py` + `.html` | 不再需要（所有画面都在网页里） |
| `roboarm/config.yaml` | `config.yaml`（只留本子树读的键） |
| `roboarm/data/` | `artifacts/`（子目录改名为 `intrinsics/` `handeye/` `poses/`） |
| `roboarm/urdf/piper/` | `../common/robot/urdf/piper/` |
| 新写的 | `app.py`、`common/ui.py`、`common/rig.py`、`common/robot/{direct,canbus,ik}.py` |

实际动过的逻辑（除 import 路径）：

1. 取图统一到 `S10Camera`（pyrealsense2 + 手动 AE，与相机原语同一套驱动和档位）：roboarm 那套 UVC 直读（`UsbCamera` / `is_color_frame`）没有带过来，标定脚本和运行时必须用同一套取图方式。
2. 交互从「每个脚本一个窗口或一个网页」合成一个 Flask 服务：`procedures/` 的 CLI 入口没有了，流程改由页面驱动（`steps/`），相机与机械臂由 `common/rig.py` 统一持有。
3. 四个脚本里各抄一遍的「使能 / 接管 / 关节插值 / 回零失能」合并进 `common/robot/direct.py`（`DirectArm`）。
4. `robot/arm.py` 的后端表只留 piper（去掉 lerobo 分支）；`pixel2pos` 改调 `common/homography.image_to_robot_xy`；URDF 解析集中到 `common/robot/ik.py`。
5. `vision/chessboard.py` 去掉 Orbbec 分支与 `main()`——整套工具不再需要 pyorbbecsdk；`vision/detect.py` 去掉 `main()`，ultralytics 改成用到才 import（装不装都能跑其它步骤）。
6. 两个网页里的 CSS `width: 100%%` 改成 `100%`（原文件里多打了一个百分号，页面一直在用无效值）。

没有搬过来的部分（这套工具用不上）：roboarm 的 `pyproject.toml` / `uv.lock`（整个 roboarm 工程的锁文件，含 lerobot、torch 路径）、`config.yaml` 里的大模型/语音/象棋/主从臂/录制等配置键、`arm/` 其它脚本、`camera/`（Orbbec 封装）、`sim/`、`llm/`、`leader_follower/`。依赖改成 `requirements.txt`。

## 上机前先看

- **取图只有一条路**：pyrealsense2 + 手动 AE、1280×720×30（与相机原语一致），见 `vision/camera.py`。
- **CAN 口自动探测**（与机械臂原语同一套判断：按 sysfs 拓扑找 USB-CAN，接口名漂了也不怕）：`config.yaml` 的 `arm_port` 只在探测不到时当退路。接口是 DOWN 时页面会打印启用它的命令，但不会替你改接口状态（可能别的进程正在用）。
- **`default_desktop_height: -0.00629`** 是本机 S10 测出来的值（桌面 z = -0.02629 加 2cm 余量），换桌面、换机器或重装相机后要重新测量并回填。
- **没有在真机上跑过**：这套代码目前只做过静态检查（逐文件编译、模块导入、页面路由冒烟、纯数学自检、URDF 解析、配置键审计、CAN 口探测），**没有连接过机械臂和相机**。第一次上机建议小步走：先看首页的 CAN 口探测对不对 → `/measure_z` 看相机和示教 → 再往后。
