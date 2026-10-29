#!/usr/bin/env python3
# SPDX-License-Identifier: MulanPSL-2.0
"""Simulated-voice test client for the host FunASR WS server (:8771).

Reads a 16 kHz mono s16le WAV, streams it as binary frames (same wire protocol
as speech_service.remote_asr → funasr_asr_server), then prints the recognition
events. This exercises the ASR seam WITHOUT a microphone — "simulated voice".

Usage:
  ../.venv-host-gpu/bin/python test_asr_ws_client.py [WAV] [--url ws://127.0.0.1:8771]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import wave
from pathlib import Path

# 默认用随仓库那份模型自带的示例音频（16 kHz 单声道）。
DEFAULT_WAV = str(
    Path(__file__).resolve().parent / "model"
    / "speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online"
    / "example" / "asr_example.wav"
)


def read_pcm(path: str) -> bytes:
    with wave.open(path, "rb") as w:
        assert w.getnchannels() == 1, f"want mono, got {w.getnchannels()}ch"
        assert w.getframerate() == 16000, f"want 16kHz, got {w.getframerate()}"
        assert w.getsampwidth() == 2, f"want s16le, got {w.getsampwidth()}B"
        return w.readframes(w.getnframes())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("wav", nargs="?", default=DEFAULT_WAV)
    ap.add_argument("--url", default="ws://127.0.0.1:8771")
    ap.add_argument("--chunk-ms", type=int, default=600)
    args = ap.parse_args()

    from websockets.sync.client import connect

    pcm = read_pcm(args.wav)
    # 16 kHz * 2 bytes/sample = 32000 B/s → chunk_ms worth of bytes.
    step = int(16000 * 2 * args.chunk_ms / 1000)
    print(f"[test] wav={args.wav}")
    print(f"[test] {len(pcm)} bytes ({len(pcm)/32000:.2f}s), "
          f"{args.chunk_ms}ms chunks ({step} B), url={args.url}")

    finals: list[str] = []
    partials = 0
    with connect(args.url, max_size=None, open_timeout=10) as ws:
        # sender: stream binary frames then eos
        import threading

        def _send() -> None:
            for i in range(0, len(pcm), step):
                ws.send(pcm[i:i + step])  # binary frame
                time.sleep(0.02)  # tiny pacing; server is real-time capable
            ws.send(json.dumps({"type": "eos"}))

        t = threading.Thread(target=_send, daemon=True)
        t.start()

        while True:
            msg = ws.recv()
            if isinstance(msg, (bytes, bytearray)):
                continue
            ev = json.loads(msg)
            if ev.get("type") == "done":
                break
            if ev.get("type") == "error":
                print(f"[test] SERVER ERROR: {ev.get('message')}")
                return 1
            txt = ev.get("text", "")
            if ev.get("is_final"):
                finals.append(txt)
                print(f"[test]  FINAL: {txt!r} (conf={ev.get('confidence')})")
            else:
                partials += 1
                print(f"[test]  partial: {txt!r}")
        t.join(timeout=2)

    print(f"\n[test] done. {partials} partial event(s), {len(finals)} final(s).")
    print(f"[test] full transcript: {''.join(finals)!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
