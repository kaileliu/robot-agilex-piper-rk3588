# 资源来源与可复现信息

本机核验日期：2026-09-22。只复制/生成本任务所需资源到 `/root/yolo_npu`。

| 资源 | 来源 / 版本 | SHA256 |
| --- | --- | --- |
| `models/best.pt` | `/root/robot-agilex-piper-ga/host-services/yolo/weights/best.pt`；YOLO11n OBB；carrot/potato/tomato | `536ba21b64377a89c9bccf08c5898f54f5130aff7bafbdf9d5ed49d05a1aeb3b` |
| `vendor/rknn_toolkit2-2.3.2-cp310-cp310-manylinux_2_17_aarch64.manylinux2014_aarch64.whl` | [PyPI 下载](https://files.pythonhosted.org/packages/c2/79/d5f886bd0bbf455c3aa62073191fde1cb9a98a253cee885d57123ffa76f1/rknn_toolkit2-2.3.2-cp310-cp310-manylinux_2_17_aarch64.manylinux2014_aarch64.whl) | `73d0bb55d30ee00bbd9603331a4118da73a4e12fa9d9196200460d07d67c6a12` |
| `vendor/rknn_toolkit_lite2-2.3.2-cp310-cp310-manylinux_2_17_aarch64.manylinux2014_aarch64.whl` | [PyPI 下载](https://files.pythonhosted.org/packages/ab/db/76b40afe343f8a8c5222300da425e0dace30ce639a94776468b1d157311b/rknn_toolkit_lite2-2.3.2-cp310-cp310-manylinux_2_17_aarch64.manylinux2014_aarch64.whl) | `821e80c95e6838308c133915660b1a6ae78bb8d079b2cbbd46a02dae61192d33` |
| `runtime/librknnrt.so` | [Rockchip 官方仓库 ARM64 运行库](https://raw.githubusercontent.com/airockchip/rknn-toolkit2/master/rknpu2/runtime/Linux/librknn_api/aarch64/librknnrt.so)；实测 API 2.3.2，commit `429f97ae6b` | `d31fc19c85b85f6091b2bd0f6af9d962d5264a4e410bfb536402ec92bac738e8` |

运行库下载链接指向上游分支，未来可能更新；本次下载的二进制以本目录文件及校验值为准。RKNN SDK 许可见 [vendor/RKNN-LICENSE](vendor/RKNN-LICENSE)，来源为 [官方 LICENSE](https://github.com/airockchip/rknn-toolkit2/blob/master/LICENSE)。Python 依赖版本分别固定于 `requirements-runtime.txt` 和 `requirements-convert.txt`，安装包自身附带各依赖的元数据及许可信息。

`models` 中的 ONNX/RKNN 由本目录脚本从复制的原权重生成，不是下载的通用模型。每个模型旁的 JSON 记录 ONNX/RKNN 和来源权重的 SHA256、Ultralytics 版本、类别和尺寸。

照片 `samples/camera_calibration/calib_000.png`、`calib_006.png`、`calib_009.png` 原样复制自原项目的 `tools/calibration/s10/artifacts/intrinsics/images/`；`calib_006.jpg` 是同一 PNG 以 OpenCV JPEG quality=90 编码生成，用于验证原项目实际使用的 JPEG 请求。其余测试随机图使用 NumPy seed=2026，无训练用途。

参考过的上游实现/说明：

- [RKNN Toolkit2 / Lite2 / Runtime](https://github.com/airockchip/rknn-toolkit2)
- [Rockchip YOLO11 预导出 ONNX 与转换示例](https://github.com/airockchip/rknn_model_zoo/tree/main/examples/yolo11)：通用 80 类检测头，不能直接替代本项目的三类 OBB 权重。
- [Rockchip YOLOv8 OBB 预导出 ONNX 与转换示例](https://github.com/airockchip/rknn_model_zoo/tree/main/examples/yolov8_obb)：通用旋转检测模型，同样不是当前训练权重。
- 原项目虚拟环境中的 Ultralytics `8.4.37`，读取 OBB head、LetterBox、ProbIoU、fast NMS 和坐标还原实现，并以其实际输出做对照。

实测系统：Linux AArch64 6.1.118，Python 3.10.12，RK3588，RKNPU 驱动 0.9.8。RKNN 服务使用 `/dev/dri/renderD128`；初始化日志和 HTTP 集成测试记录在 `.cache/` 及 `reports/http_smoke.json`。
