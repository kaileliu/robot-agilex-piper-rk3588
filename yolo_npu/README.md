# Piper YOLO 的 RK3588 NPU 实现

已经在这台 **Rockchip RK3588 XIUOS Board** 上完成模型转换、真实 NPU 推理、CPU 数值对照和 HTTP 联调。实现、模型、依赖、运行库、测试数据与报告均放在 `/root/yolo_npu`。原项目代码和虚拟环境不需要修改。

直接启动：

```bash
bash /root/yolo_npu/run_yolo_npu.sh
```

服务与原项目一样监听 `127.0.0.1:8770`。启动 NPU 服务前先退出原 CPU YOLO 服务，避免占用同一个端口；今后使用上面的启动命令即可。原来的抓取技能和检测服务继续访问同一个 URL，无需修改 ROS、抓取逻辑或相机配置。前台运行时用 Ctrl+C 停止。当前交付已完成测试，测试进程已退出，未安装开机自启。

```bash
curl -s http://127.0.0.1:8770/health | python3 -m json.tool

curl -s --data-binary @/root/yolo_npu/samples/camera_calibration/calib_006.jpg \
  'http://127.0.0.1:8770/detect?conf=0.5&iou=0.45&object_name=tomato' \
  | python3 -m json.tool
```

`/health` 应显示 `device: rk3588-npu`、`precision: fp16`、`input_size: [736, 1280]` 和三个原有类别。日常运行使用系统 Python 3.10、本目录的 RKNN Lite2 和 NumPy/OpenCV，**不加载 PyTorch**。NPU 初始化失败会报错退出，不会悄悄切回 CPU。

**对原项目的理解与兼容点**

原 `host-services/yolo/yolo_detect_server.py` 加载 `weights/best.pt`，通过 Ultralytics 推理；当前设备没有可用 CUDA。模型实际是自定义 **YOLO11n-OBB**，类别为 `0: carrot`、`1: potato`、`2: tomato`。

抓取链路为：相机图像 → `pick_skill/detect.py` 去畸变/JPEG 编码 → 宿主机 `/detect` → OBB 像素坐标 → grasp-pose 服务计算位置和夹爪角度。检测服务 `service-object-detect-rbnx` 也使用相同 HTTP 接口。NPU 实现保留以下语义：

- `POST /detect?conf=...&iou=...&object_name=...`，请求体为原始 JPEG/PNG 字节。
- 默认 `conf=0.8`、`iou=0.45`；抓取技能实际会传自己的阈值，例如 `conf=0.5`。
- `image_size` 顺序为 `[高度, 宽度]`。
- `obb = [cx, cy, w, h, angle_radians]`，坐标已映射回请求图像。
- `bbox_2d = [cx-w/2, cy-h/2, cx+w/2, cy+h/2, angle_degrees]`。这里的宽高和角度用于重建旋转框，不能改成旋转后的轴对齐外接框。
- 沿用原 Ultralytics 8.4.37 的角度范围、宽高顺序、ProbIoU 与 fast NMS 规则，不擅自交换宽高或裁剪边缘框。
- `object_name` 在去重后进行忽略大小写的子串匹配。
- 保留 `success/message/detections/inference_ms`，额外提供 `timings_ms` 方便分析预处理、等待 NPU、推理和后处理耗时。

原权重的默认推理尺寸是 `1280`，配置中的相机输出为宽 `1280`、高 `720`。原 Ultralytics 的矩形 letterbox 实际得到高 `736`、宽 `1280`，默认 NPU 模型保持这一输入尺寸。其他比例图像也可发送，服务会进行 letterbox 和坐标还原；固定输入下的特征图尺寸可能与原 CPU 的自动矩形尺寸不同。

实现路径：复制的 `best.pt` → 静态 ONNX 原始检测头 → RKNN FP16 → NPU 运行卷积网络 → CPU 执行 float32 DFL/角度解码、旋转 NMS 和坐标映射。每个尺度输出框回归、类别和角度，共 9 个张量。导出只改内存中的模型实例，没有改 Ultralytics 源文件。

**现成 ONNX 的取舍**

已查询 [Rockchip YOLO11 示例](https://github.com/airockchip/rknn_model_zoo/tree/main/examples/yolo11) 和 [YOLOv8 OBB 示例](https://github.com/airockchip/rknn_model_zoo/tree/main/examples/yolov8_obb)。官方提供可下载的 ONNX，但这些是通用预训练模型，不是此项目的三类蔬菜 OBB 权重。因此这里保留原 `best.pt`，已实际导出两份可用 ONNX 并转换成 RKNN；正常使用无需重新导出或下载。

**本机实测**

环境：RKNN Toolkit2/Lite2/Runtime `2.3.2`，内核 RKNPU 驱动 `0.9.8`；未更换驱动、修改系统库或调整硬件频率。以下为预热后完整检测流程的中位耗时，包含预处理和后处理，不包含 JPEG 解码、HTTP 传输、相机取帧及机械臂动作。每组 CPU/NPU 使用同一张图、同一输入尺寸和同一阈值；NPU 10 次、CPU 5 次。

| 输入高×宽 / 图像 | CPU | NPU FP16 | 同尺寸加速 | 报告 |
| --- | ---: | ---: | ---: | --- |
| 736×1280 / 真实相机标定照片 | 1339 ms | 186 ms | 7.19 倍 | [validation_camera.json](reports/validation_camera.json) |
| 736×1280 / 固定种子随机图 | 1327 ms | 199 ms | 6.66 倍 | [validation.json](reports/validation.json) |
| 640×640 / 同一随机图 | 684 ms | 88 ms | 7.82 倍 | [validation_640.json](reports/validation_640.json) |

真实照片 `calib_006.png` 上，在 `conf=0.5` 时 CPU/NPU 都检测到同一个番茄：中心及宽高最大差异 `0.118` 像素、角度差 `0.412°`，置信度分别为 `0.715` 和 `0.693`。这只是该照片的对照，不代表完整数据集精度。

已通过颜色顺序/奇数 padding/DFL/角度/fast NMS 回归测试，以及与原 Ultralytics 的解码、NMS 和最终像素坐标对照。[HTTP 测试报告](reports/http_smoke.json) 记录了真实 JPEG 非空检测、类别过滤、参数错误、四个并发请求与正常停止，并记录到实际 NPU 负载。运行进程加载的是本目录的 `librknnrt.so`，未加载 `libtorch`。

目前可用照片主要是棋盘标定帧，尚无覆盖三个类别的标注检测集，也没有执行机械臂抓取或在线相机闭环测试。FP16 转换存在数值差异；靠近置信度阈值的目标可能改变是否检出。`reports` 中的随机图只用于数值检查和计时。

**可选的 640 速度档**

```bash
YOLO_NPU_MODEL=/root/yolo_npu/models/best_640x640_heads_fp16.rknn \
  bash /root/yolo_npu/run_yolo_npu.sh
```

此档约 88 ms/帧，使用相同训练权重，但输入分辨率下降，小目标和抓取角度需要用实际工作画面验证。默认服务仍使用 `736×1280`。上述速度档数字是与同尺寸 CPU 的比较。

可通过 `YOLO_PORT`、`YOLO_HOST`、`YOLO_CONF`、`YOLO_IOU`、`YOLO_NPU_MODEL`、`YOLO_NPU_CORES` 指定参数，也可直接追加 `--port 18770` 等服务参数。默认 core mask 为 `7`，允许使用三个 NPU 核；硬件实际调度由 RKNN 决定。

**文件与重建方式**

| 路径 | 用途 |
| --- | --- |
| `run_yolo_npu.sh` / `yolo_npu_server.py` | 直接启动、兼容原 HTTP 服务 |
| `rknn_backend.py` / `yolo_obb.py` | 本地运行库适配、模型推理与后处理 |
| `models/best.pt` | 从原项目复制的权重，SHA256 记录在模型 JSON 中 |
| `models/best_736x1280_heads.onnx` / `*_fp16.rknn` | 默认模型，均有同名 JSON 元数据 |
| `models/best_640x640_heads.onnx` / `*_fp16.rknn` | 可选速度档，均有同名 JSON 元数据 |
| `runtime/` / `runtime_python/` / `runtime_deps/` | 本地运行库、RKNN Lite2、运行依赖 |
| `converter_python/` / `convert_deps/` / `vendor/` | 转换工具、依赖、原始安装包与授权文件 |
| `samples/` / `tests/` / `reports/` | 已复制的照片、回归测试和实测报告 |
| [SOURCES.md](SOURCES.md) | 下载来源、版本及校验值 |

模型已准备好。需要重新导出时，下面的命令只读使用原 YOLO `.venv` 中的 PyTorch/Ultralytics；转换依赖独立放在本目录，兼容 RKNN 所需的 NumPy 1.x，不会降级原环境的 NumPy。`YOLO_SOURCE_VENV` 可以指定另一个兼容环境。

```bash
bash /root/yolo_npu/export_onnx.sh
bash /root/yolo_npu/build_rknn.sh

# 重建 640 模型
bash /root/yolo_npu/export_onnx.sh --imgsz 640 640
bash /root/yolo_npu/build_rknn.sh \
  --onnx /root/yolo_npu/models/best_640x640_heads.onnx
```

所有生成物位于本目录。ONNX 和 RKNN 旁的 JSON 包含输入尺寸、类别、输出格式和 SHA256；服务启动会校验 RKNN 与 JSON 是否配套，避免拿错模型后静默产生错误坐标。

只有本地依赖被删除时才需要重新安装；以下命令需要本机 `uv`，安装位置和下载缓存均在本目录：

```bash
bash /root/yolo_npu/setup_runtime.sh
bash /root/yolo_npu/setup_conversion.sh
```

随附 wheel 是 Linux AArch64 / CPython 3.10 版本。服务运行已不依赖原项目虚拟环境，但重新导出和 CPU 对照测试仍需要其 PyTorch/Ultralytics。默认源为 PyPI，也可用 `YOLO_PYPI_INDEX` 指定包源。

重跑验证：

```bash
cd /root/yolo_npu
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=runtime_deps:. \
  /usr/bin/python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 tests/smoke_http.py
bash validate.sh --image samples/camera_calibration/calib_006.png \
  --output reports/validation_camera.json
```

`validate.sh --image /绝对路径/frame.jpg` 可对自己的工作画面做 CPU/NPU 对照。HTTP 测试自动选择临时端口并在结束后关闭服务；性能测试建议在没有其他 NPU 推理任务时执行。

当前交付的是 **FP16**。转换脚本保留 INT8 入口，但尚未生成或验证 INT8 模型。需要先把覆盖实际工作场景的校准图片放入 `calibration/`，并在 `dataset.txt` 中逐行写图片绝对路径，再运行：

```bash
bash /root/yolo_npu/build_rknn.sh --quantized \
  --dataset /root/yolo_npu/calibration/dataset.txt \
  --output /root/yolo_npu/models/best_736x1280_heads_int8.rknn
```

随机图和当前棋盘照片不适合作为这套蔬菜模型的代表性 INT8 校准集；INT8 完成后需另做精度与角度验证。

运行时 SDK 可能打印 `Query dynamic range failed ... static shape`：这是对静态模型查询动态尺寸产生的已知提示；本目录的两个模型均为静态尺寸，已在该提示下通过实际推理。如果出现其他初始化失败，先检查 `GET /health`、设备 `/dev/dri/renderD128` 和 RKNN 报错。无需把本目录的运行库复制到 `/usr/lib`。
