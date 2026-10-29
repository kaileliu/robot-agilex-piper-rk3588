"""RKNN Lite2 adapter using a runtime library stored in this project only."""
from __future__ import annotations

from pathlib import Path
import threading

import numpy as np


_INIT_LOCK = threading.Lock()


class RknnBackend:
    def __init__(self, model_path, runtime_path, core_mask=7, verbose=False):
        self.model_path = str(Path(model_path).resolve(strict=True))
        self.runtime_path = str(Path(runtime_path).resolve(strict=True))
        self.core_mask = int(core_mask)
        self._rknn = None
        from rknnlite.api import rknn_lite
        from rknnlite.api.rknn_runtime import RKNNRuntime

        library = self.runtime_path

        class LocalRuntime(RKNNRuntime):
            def _get_rknn_api_lib_path(self):
                # Lite2 2.3.2 otherwise hardcodes /usr/lib/librknnrt.so.
                # Override only RKNN's resolver; never patch os or ctypes.
                return library

        try:
            self._rknn = rknn_lite.RKNNLite(verbose=verbose)
            if self._rknn.load_rknn(self.model_path) != 0:
                raise RuntimeError("RKNN model loading failed")
            with _INIT_LOCK:
                original = rknn_lite.RKNNRuntime
                try:
                    rknn_lite.RKNNRuntime = LocalRuntime
                    ret = self._rknn.init_runtime(core_mask=self.core_mask)
                finally:
                    rknn_lite.RKNNRuntime = original
            if ret != 0:
                raise RuntimeError(f"NPU initialization failed ({ret}); inspect the RKNN driver log above")
            runtime = self._rknn.rknn_runtime
            inputs, self.output_count = runtime.get_in_out_num()
            attr = runtime.get_tensor_attr(0)
            dims = tuple(int(d) for d in attr.dims[:attr.n_dims])
            if inputs != 1 or len(dims) != 4 or dims[0] != 1:
                raise ValueError(f"Expected one batch=1 image input, got {inputs}, {dims}")
            if attr.fmt == 0 and dims[1] == 3:  # RKNN_TENSOR_NCHW
                self.input_shape = dims[2:4]
            elif attr.fmt == 1 and dims[3] == 3:  # RKNN_TENSOR_NHWC
                self.input_shape = dims[1:3]
            else:
                raise ValueError(f"Unsupported image layout: dims={dims}, fmt={attr.fmt}")
            self.sdk_version = self._rknn.get_sdk_version()
        except Exception:
            self.release()
            raise

    def infer(self, image_nhwc: np.ndarray) -> list[np.ndarray]:
        if self._rknn is None:
            raise RuntimeError("NPU backend is closed")
        expected = (1, *self.input_shape, 3)
        if image_nhwc.dtype != np.uint8 or image_nhwc.shape != expected:
            raise ValueError(f"Expected RGB uint8 {expected}, got {image_nhwc.dtype} {image_nhwc.shape}")
        outputs = self._rknn.inference(
            inputs=[np.ascontiguousarray(image_nhwc)], data_format=["nhwc"]
        )
        if outputs is None or len(outputs) != self.output_count:
            raise RuntimeError("NPU inference failed; no valid outputs (no CPU fallback)")
        return [np.asarray(output, dtype=np.float32) for output in outputs]

    def release(self):
        if self._rknn is not None:
            self._rknn.release()
            self._rknn = None
