# RK3588 NPU YOLO OBB backend

Host-side HTTP object detection for the Piper deployment. The default model is
`models/best_736x1280_heads_fp16.rknn`; its adjacent JSON describes input/output tensors and
class names. Keep the model and metadata together. The backend uses RKNN Lite2
2.3.2 and the bundled `runtime/librknnrt.so` on Linux AArch64 / Python 3.10.

## Install and run

Install `uv` and a working RK3588 RKNPU driver. From the repository root:

```bash
bash yolo_npu/setup_runtime.sh
bash yolo_npu/run_yolo_npu.sh
```

`setup_runtime.sh` verifies and extracts the small bundled Lite2 wheel and
installs `requirements-runtime.txt` into ignored local directories. It does not
install PyTorch or the full model converter. Use `YOLO_NPU_PYTHON` to select a
Python 3.10 interpreter. See `run_yolo_npu.sh` and the server's `--help` for
runtime options. The default endpoint is `http://127.0.0.1:8770`.

```bash
curl -fsS http://127.0.0.1:8770/health
curl -fsS -X POST --data-binary @yolo_npu/samples/camera_calibration/calib_006.jpg \
  'http://127.0.0.1:8770/detect?conf=0.5&iou=0.45'
```

The health response identifies `rk3588-npu`. `/detect` accepts encoded image
bytes and returns detections with bounding boxes, classes, confidence and OBB
geometry. Robonix's detection service and Piper skill access this HTTP process;
the support package itself declares no Atlas capabilities. Run one process on
port 8770. Stop it with `Ctrl-C`.

## Source and model conversion

`models/best.pt` is retained for reproducibility and numerical comparison.
ONNX files and alternate 640 exports are generated intermediates and excluded
from publication. The default 736 RKNN model is ready to run.

Conversion is optional and needs a separate Linux AArch64 Python 3.10
environment with PyTorch and Ultralytics. Point `YOLO_SOURCE_VENV` at that
environment; the scripts never modify its installed dependencies.

```bash
export YOLO_SOURCE_VENV=/absolute/path/to/yolo-venv
bash yolo_npu/setup_conversion.sh
bash yolo_npu/export_onnx.sh
bash yolo_npu/build_rknn.sh
bash yolo_npu/validate.sh --image "$PWD/yolo_npu/samples/camera_calibration/calib_006.jpg"
```

The conversion setup downloads the pinned RKNN Toolkit2 2.3.2 wheel into
`.cache/` if a local copy is absent and verifies its SHA256 before extraction.
Additional dependencies are listed in `requirements-convert.txt`. Inspect
script options before rebuilding; outputs and comparison reports are local
artifacts. `validate.sh` compares the original weights with RKNN inference and
requires working NPU hardware; it does not move the robot.

## Regression tests

Postprocessing can be checked without NPU access using an environment with
NumPy and OpenCV:

```bash
cd yolo_npu
python -m unittest discover -s tests -v
```

Sample images are test inputs, not a calibrated robot acceptance dataset.
Detector health and numerical regression do not establish grasp accuracy.

## Files and licenses

- `yolo_npu_server.py`, `rknn_backend.py`, `yolo_obb.py`: HTTP inference and OBB postprocessing.
- `models/`: original weights and default RKNN model/metadata.
- `runtime/`, `vendor/`: matching runtime library, Lite2 wheel and SDK license.
- `samples/`, `tests/`: image fixtures and postprocessing regression checks.
- `setup_*`, `export_*`, `convert_*`, `validate*`: reproducible setup and model tools.

Rockchip SDK files retain [RKNN-LICENSE](vendor/RKNN-LICENSE). Model files retain
their original provenance; the deployment license does not replace third-party
model or SDK terms. See the [deployment guide](../README.md) for hardware,
configuration and calibration requirements.
