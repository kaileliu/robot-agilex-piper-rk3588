# Host services

The RK3588 detector runs on the host using RKNN Lite2. See the
[NPU backend guide](../yolo_npu/README.md). Robonix runs inside a container
with host networking and accesses the detector at `http://127.0.0.1:8770`.

## Optional speech recognition

FunASR runs separately from NPU inference and uses CPU on RK3588. Its Python
environment and downloaded model are created locally and excluded from Git.
Install `uv`, then run from the repository root:

```bash
bash host-services/funasr/setup_venv.sh
bash host-services/funasr/fetch_funasr_model.sh
FUNASR_DEVICE=cpu bash host-services/funasr/run_funasr_server.sh
```

The streaming ASR endpoint is `ws://127.0.0.1:8771`. Configure the speech
service's custom/remote ASR backend to use this endpoint. The fetch script
provides the model download location; allow sufficient storage for the model
and PyTorch environment. Existing Orin/Thor dependency profiles are also
retained because the setup script selects them on those boards.

To test the running service without opening the robot camera or moving the arm:

```bash
host-services/funasr/.venv/bin/python host-services/funasr/test_asr_ws_client.py
```

Text commands do not require FunASR. Audible replies require a real TTS backend;
the default mock TTS produces silence. Keep credentials in ignored local
configuration files. Stop the host server with `Ctrl-C` in its terminal.
