# Client and Tencent ASR

Client URL: http://192.168.137.20:7860/

The client connects to Atlas at `127.0.0.1:50051`. Start the client with
`bash run_robonix_client.sh`; its systemd unit is `robonix-yolo-client.service`.
This is a transient service, not a boot-enabled service.

Tencent credentials are stored in `deploy/piper/tencent-asr.local.env` (mode
600, excluded by `*.local.env`). The container entrypoint loads this file.
AppID: `1367440399`. ASR engine: `16k_zh_en`.

This network requires the existing HTTP proxy. `TENCENT_ASR_USE_PROXY=1`
explicitly enables proxy use for ASR; the backend defaults to direct access.

The deployment manifest includes `speech` and `audio_client_bridge` for future
stack starts. They were started and initialized independently for this setup,
without restarting the running robot. `scripts/init_voice.py` is a one-time
initializer for newly registered providers, not a health check.

Client Audio routes use `audio_client_bridge` for input/output and `speech`
for ASR/TTS. The reverse bridge listens on port 60002. The installed client
captures audio devices on the RK3588, not the remote browser's microphone.
No sound card was detected during setup. Attach a microphone to the RK3588,
or run robonix-client on the computer with the desired microphone and connect
to this robot using its LAN address.

Validation: Tencent WebSocket authentication returned code 0; a 5.55-second
public FunASR sample passed through Robonix's gRPC streaming ASR and returned
Chinese transcript segments. The client discovered 13 active providers and
connected to the reverse audio bridge. Proxy configuration regression tests
passed. No test audio was sent to Pilot or the arm.

Physical microphone capture and TTS playback have not been tested. Wake-word
model downloads were skipped; hands-free wake detection is not configured.
The existing test_asr_stream_grpc.py prints only final-event text in its final
summary; Tencent also emits incremental text in preceding events.
