"""Export this project's trained YOLO11 OBB heads for Rockchip RKNN."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from types import MethodType

ROOT = Path(__file__).resolve().parent


def raw_obb_forward(self, features):
    # Same trained heads, before DFL/box decode. Output order is part of the
    # metadata contract. Sigmoid/DFL/trigonometry run in float32 on the CPU.
    outputs = []
    for i in range(self.nl):
        outputs.extend((self.cv2[i](features[i]), self.cv3[i](features[i]), self.cv4[i](features[i])))
    return tuple(outputs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights", type=Path, default=ROOT / "models/best.pt")
    parser.add_argument("--imgsz", nargs=2, type=int, default=[736, 1280], metavar=("HEIGHT", "WIDTH"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    h, w = args.imgsz
    if min(h, w) <= 0 or h % 32 or w % 32:
        parser.error("Input dimensions must be positive multiples of 32")

    import torch
    import onnx
    from ultralytics import YOLO
    from ultralytics.nn.modules.head import OBB

    torch.set_num_threads(4)
    model = YOLO(str(args.weights.resolve())).model.float().eval().fuse()
    head = model.model[-1]
    if type(head) is not OBB or head.end2end or head.ne != 1:
        raise ValueError("Exporter supports the project's non-end2end YOLO11 OBB head only")
    names = {int(k): str(v) for k, v in model.names.items()}
    strides = [int(s) for s in head.stride.tolist()]
    head.forward = MethodType(raw_obb_forward, head)
    output = args.output or ROOT / f"models/best_{h}x{w}_heads.onnx"
    output.parent.mkdir(parents=True, exist_ok=True)
    with torch.inference_mode():
        torch.onnx.export(
            model, torch.zeros(1, 3, h, w), str(output),
            input_names=["images"],
            output_names=[f"{kind}_s{s}" for s in strides for kind in ("dfl", "classes", "angle")],
            opset_version=12, dynamo=False,
        )
    graph = onnx.load(str(output))
    onnx.checker.check_model(graph)
    metadata = {
        "task": "obb", "architecture": "yolo11n-obb", "names": names,
        "input_shape": [h, w], "color": "RGB", "input_scale": 255.0,
        "output_format": "raw_obb_heads_v1", "strides": strides, "reg_max": head.reg_max,
        "source_weights_sha256": hashlib.sha256(args.weights.read_bytes()).hexdigest(),
        "onnx_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "ultralytics_version": __import__('ultralytics').__version__,
    }
    output.with_suffix('.json').write_text(json.dumps(metadata, indent=2) + '\n')
    print(f"Exported {output} ({h}x{w}, {len(graph.graph.output)} raw heads)")


if __name__ == '__main__':
    main()
