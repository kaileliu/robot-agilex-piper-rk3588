#!/usr/bin/env python3
"""Compile an ONNX model to an RK3588 RKNN model on this ARM64 board."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "convert_deps"))
sys.path.insert(0, str(ROOT / "converter_python"))

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--onnx", default=str(ROOT / "models" / "best_736x1280_heads.onnx"))
    parser.add_argument("--output", default=None)
    parser.add_argument(
        "--quantized",
        action="store_true",
        help="build INT8; also pass --dataset with one image path per line",
    )
    parser.add_argument("--dataset", default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    onnx_path = Path(args.onnx).resolve()
    suffix = '_int8.rknn' if args.quantized else '_fp16.rknn'
    output_path = Path(args.output).resolve() if args.output else onnx_path.with_name(onnx_path.stem + suffix)
    if not onnx_path.is_file():
        raise FileNotFoundError(onnx_path)
    if args.quantized and not args.dataset:
        parser.error("--quantized requires --dataset")

    metadata = json.loads(onnx_path.with_suffix('.json').read_text())
    if metadata['onnx_sha256'] != hashlib.sha256(onnx_path.read_bytes()).hexdigest():
        raise ValueError('ONNX does not match its metadata; rerun export_onnx.sh')
    from rknn.api import RKNN
    rknn = RKNN(verbose=args.verbose)
    try:
        ret = rknn.config(
            target_platform='rk3588',
            mean_values=[[0.0, 0.0, 0.0]],
            std_values=[[255.0, 255.0, 255.0]],
            optimization_level=3,
        )
        if ret != 0:
            raise RuntimeError(f"RKNN.config failed with code {ret}")
        ret = rknn.load_onnx(model=str(onnx_path))
        if ret != 0:
            raise RuntimeError(f"RKNN.load_onnx failed with code {ret}")
        kwargs = {"do_quantization": bool(args.quantized)}
        if args.quantized:
            kwargs["dataset"] = str(Path(args.dataset).resolve())
        ret = rknn.build(**kwargs)
        if ret != 0:
            raise RuntimeError(f"RKNN.build failed with code {ret}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        ret = rknn.export_rknn(str(output_path))
        if ret != 0:
            raise RuntimeError(f"RKNN.export_rknn failed with code {ret}")
    finally:
        rknn.release()
    metadata.update({
        'target': 'rk3588', 'precision': 'int8' if args.quantized else 'fp16',
        'rknn_sha256': hashlib.sha256(output_path.read_bytes()).hexdigest(),
        'toolkit_version': '2.3.2',
    })
    output_path.with_suffix('.json').write_text(json.dumps(metadata, indent=2) + '\n')
    print(output_path)


if __name__ == "__main__":
    main()
