# SPDX-License-Identifier: MulanPSL-2.0
"""Host-side FunASR streaming-ASR server for the robonix speech service.

Runs on the HOST (not in the robonix container), same pattern as
host-services/yolo/yolo_detect_server.py (:8770). The container's speech
service (packages/service-speech, SPEECH_BACKEND=custom) reaches this over
WebSocket ws://127.0.0.1:8771 via speech_service.remote_asr.

Why host-side: FunASR needs torch + CUDA, which the container image
deliberately does NOT have. GPU torch lives in a py3.10 venv that
read-only-symlinks the roboarm venv's CUDA torch (see run_funasr_server.sh).

FunASRStreamingBackend below is COPIED VERBATIM from robonix
services/speech/speech_service/service.py. Keep it byte-identical so
recognition matches the in-process backend -- only a localhost hop is added.
If robonix upstream changes that class, re-sync this copy.

Wire protocol (one WS connection per utterance):
  client -> server: binary frames = 16 kHz mono pcm_s16le
  client -> server: text '{"type":"eos"}'  = end of audio, flush final
  server -> client: text frames = JSON {event_type,text,confidence,is_final}
  server -> client: text '{"type":"done"}' = finished
Security: binds 127.0.0.1 only; container reaches it via --net host. No auth.
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import os
import queue
import threading
import wave
from typing import Iterable, Iterator

logging.basicConfig(
    level=os.environ.get("FUNASR_SERVER_LOG", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("funasr_asr_server")


class FunASRStreamingBackend:
    """Streaming ASR backend using FunASR Paraformer-zh-streaming (VERBATIM
    copy from robonix service.py; see module docstring)."""

    def __init__(self):
        from funasr import AutoModel

        model_name = os.environ.get("FUNASR_MODEL", "paraformer-zh-streaming")
        chunk_size_str = os.environ.get("FUNASR_CHUNK_SIZE", "[0,10,5]")
        device_pref = os.environ.get("FUNASR_DEVICE", "auto")
        try:
            self.chunk_size = json.loads(chunk_size_str)
        except json.JSONDecodeError:
            self.chunk_size = [0, 10, 5]

        self.chunk_stride = self.chunk_size[1] * 960

        if device_pref == "auto":
            try:
                import torch
                cuda_available = torch.cuda.is_available()
            except Exception:
                cuda_available = False
            candidates = ["cuda", "cpu"] if cuda_available else ["cpu"]
        else:
            candidates = [device_pref]

        last_err = None
        for device in candidates:
            log.info(
                "Loading FunASR model %s on %s (chunk_size=%s, stride=%d samples)...",
                model_name, device, self.chunk_size, self.chunk_stride,
            )
            try:
                self.model = AutoModel(
                    model=model_name,
                    device=device,
                    hub_kwargs={"local_files_only": True},
                )
                self.device = device
                log.info("FunASR model loaded on %s.", device)
                return
            except Exception as e:
                last_err = e
                log.warning("FunASR load on %s failed: %s", device, e)
        raise RuntimeError(
            f"FunASR failed to load on any of {candidates}: {last_err}"
        )

    def recognize_chunk(
        self,
        audio_chunk: bytes,
        cache: dict,
        is_final: bool = False,
        encoding: str = "pcm_s16le",
        sample_rate: int = 16000,
    ) -> list[dict]:
        import numpy as np

        if encoding == "pcm_s16le":
            audio = np.frombuffer(audio_chunk, dtype=np.int16)
        elif encoding == "pcm_f32le":
            audio = (np.frombuffer(audio_chunk, dtype=np.float32) * 32768).astype(np.int16)
        else:
            buf = io.BytesIO(audio_chunk)
            with wave.open(buf, "rb") as wf:
                frames = wf.readframes(wf.getnframes())
                audio = np.frombuffer(frames, dtype=np.int16)

        import time as _time

        _t0 = _time.perf_counter()
        results = self.model.generate(
            input=audio,
            cache=cache,
            is_final=is_final,
            chunk_size=self.chunk_size,
            encoder_chunk_look_back=4,
            decoder_chunk_look_back=1,
            disable_pbar=True,
            disable_log=True,
        )
        log.info(
            "[profile-asr] chunk infer %.1f ms (%d samples, is_final=%s)",
            (_time.perf_counter() - _t0) * 1000.0, int(audio.shape[0]), is_final,
        )

        outputs = []
        for res in results:
            if isinstance(res, dict):
                text = res.get("text", "")
            else:
                text = str(res)
            outputs.append({"text": text, "confidence": 0.9})
        return outputs
    def recognize_stream(self, pcm_chunks: Iterable[bytes]) -> Iterator[dict]:
        cache: dict = {}
        frame_buf = bytearray()
        stride_bytes = self.chunk_stride * 2

        for chunk in pcm_chunks:
            if not chunk:
                continue
            frame_buf.extend(chunk)
            while len(frame_buf) >= stride_bytes:
                frame = bytes(frame_buf[:stride_bytes])
                del frame_buf[:stride_bytes]
                for result in self.recognize_chunk(frame, cache, is_final=False):
                    text = result.get("text", "")
                    if text:
                        yield {
                            "event_type": 0,
                            "text": text,
                            "confidence": result.get("confidence", 0.0),
                            "is_final": False,
                        }

        for result in self.recognize_chunk(bytes(frame_buf), cache, is_final=True):
            text = result.get("text", "")
            if text:
                yield {
                    "event_type": 1,
                    "text": text,
                    "confidence": result.get("confidence", 0.0),
                    "is_final": True,
                }


# ── WebSocket wrapper ───────────────────────────────────────────────────────
# One shared model (loaded once). A global lock serialises sessions: FunASR's
# streaming cache is per-session but the model's internal state is not designed
# for concurrent generate() calls. This deploy is single-user, so serialising
# is fine and keeps behaviour identical to the in-process backend.
_BACKEND: FunASRStreamingBackend | None = None
_MODEL_LOCK = threading.Lock()


def _handle(ws) -> None:
    """Serve one utterance: recv PCM binary frames + 'eos', stream back events."""
    peer = getattr(ws, "remote_address", None)
    log.info("client connected: %s", peer)
    pcm_q: "queue.Queue[bytes | None]" = queue.Queue(maxsize=256)

    def _reader() -> None:
        try:
            for msg in ws:
                if isinstance(msg, (bytes, bytearray)):
                    pcm_q.put(bytes(msg))
                else:
                    try:
                        obj = json.loads(msg)
                    except Exception:
                        continue
                    if obj.get("type") == "eos":
                        break
        except Exception as exc:  # noqa: BLE001
            log.info("reader ended: %s", exc)
        finally:
            pcm_q.put(None)  # sentinel: end of audio

    reader = threading.Thread(target=_reader, name="ws-reader", daemon=True)
    reader.start()

    def _chunks() -> Iterator[bytes]:
        while True:
            item = pcm_q.get()
            if item is None:
                return
            yield item

    try:
        with _MODEL_LOCK:
            for ev in _BACKEND.recognize_stream(_chunks()):
                ws.send(json.dumps(ev, ensure_ascii=False))
        ws.send(json.dumps({"type": "done"}))
        log.info("stream done: %s", peer)
    except Exception as exc:  # noqa: BLE001
        log.exception("stream failed")
        try:
            ws.send(json.dumps({"type": "error", "message": str(exc)}))
        except Exception:
            pass
    finally:
        reader.join(timeout=2.0)


def main() -> int:
    global _BACKEND
    ap = argparse.ArgumentParser(description="Host-side FunASR streaming ASR WS server")
    ap.add_argument("--host", default=os.environ.get("FUNASR_SERVER_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("FUNASR_SERVER_PORT", "8771")))
    args = ap.parse_args()

    # Diagnostics mirror the service's check_torch_cuda so a CPU fallback is
    # obvious in the log (CPU streaming lags far behind real time).
    try:
        import torch
        log.info("Torch %s | CUDA available: %s", torch.__version__, torch.cuda.is_available())
        if torch.cuda.is_available():
            log.info("CUDA device: %s", torch.cuda.get_device_name(0))
    except Exception as exc:  # noqa: BLE001
        log.warning("torch diagnostics skipped: %s", exc)

    log.info("loading FunASR model (FUNASR_MODEL=%s)...", os.environ.get("FUNASR_MODEL", "<default>"))
    _BACKEND = FunASRStreamingBackend()
    log.info("model ready on %s", getattr(_BACKEND, "device", "?"))

    from websockets.sync.server import serve

    with serve(_handle, args.host, args.port, max_size=None) as server:
        log.info("FunASR ASR server listening on ws://%s:%d", args.host, args.port)
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
