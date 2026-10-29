# 标定工具（tools/calibration）

两个部署各有一套**网页标定台**（Flask 一体服务），底层是两边共用的代码。

```text
tools/calibration/
├── common/             两个部署共用（与具体部署无关）
│   ├── chessboard.py     棋盘格检测 + 内参求解
│   ├── homography.py     像素 ↔ 臂平面单应（求解 / 映射 / 重投影误差）
│   ├── camera.py         取图接口、去畸变包装、MJPEG 流
│   ├── ui.py             页面外壳 + 步骤基类（Flask）
│   ├── rig.py            相机与机械臂的持有者（同一时刻只给一个步骤用）
│   ├── steps/            两步共用的流程：intrinsics、handeye
│   └── robot/            臂的直连实现：CAN 口自动探测、会话、IK、抓取后端、URDF
├── s10/                S10 标定台（宿主：D435i + 直连 CAN）
├── piper/              纯臂 piper 标定台（宿主：Orbbec 336L + 直连 CAN）
└── boards/             可打印的棋盘格标定板（A4/A3 的 PDF）+ 生成脚本
```

两边形态相同：宿主机上一个 Flask 服务，直接连接硬件。

| | s10 | piper |
|---|---|---|
| 启动 | `s10/app.py`，默认 :8080 | `piper/app.py`，默认 :8090 |
| 相机 | D435i（pyrealsense2，与相机原语同一套驱动） | Gemini 336L（pyorbbecsdk，与相机原语同一个 SDK） |
| 臂 | 直连 CAN（robonix 的臂原语是同一个臂、同一套指令） | 同 s10 |
| 步骤 | 测桌面 z → 内参 → 示教 → 手眼 → 抓取自检 → 回零失能 | 内参 → 手眼 → 验证 |

三个共同的约定：

1. **标定输入就是运行时输入**。手眼标定用的画面必须与运行时一致（先按内参去畸变、同一分辨率）；两套走的是同一段手眼流程（`common/steps/handeye.py`），没有内参就不能开始。
2. **产物是接口**。标定唯一的输出是各自 `artifacts/` 里的数值，由人**手工**抄进对应的部署清单（`deploy/s10/manifest.yaml` / `deploy/piper/manifest.yaml`）；两边 README 里各有一张"产物 → 清单键"的对照表，没有自动写回。
3. **硬件独占**。相机和机械臂同时只给一个步骤用；页面上会显示谁占着，可以一键「强制接管」。

piper 那套 2026-09-11 在实机上（Jetson + Gemini 336L + Piper）完整走过一遍：内参 11 张 RMS 0.47 px、手眼 7 点平均 3.5 mm，踩到的坑记在 `piper/README.md` 的「实机情况」里。S10 那套仍只做过静态检查（编译、模块导入、页面路由冒烟、纯数学自检、CAN 口探测），没有连过真机。
