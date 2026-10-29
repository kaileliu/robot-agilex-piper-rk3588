"""Initialize newly started voice providers without restarting the robot."""
import json
import time

import grpc
import lifecycle_pb2
import robonix_contracts_pb2_grpc as contracts
from robonix_api import ATLAS


def initialize(provider, contract, stub_name, config):
    for attempt in range(30):
        if any(p.id == provider for p in ATLAS.query()):
            break
        time.sleep(1)
    else:
        raise RuntimeError(f"Provider did not register: {provider}")
    with ATLAS.connect_capability(
        consumer_id="voice-setup", provider_id=provider,
        contract_id=contract, transport="grpc"
    ) as connection, grpc.insecure_channel(connection.endpoint) as channel:
        stub = getattr(contracts, stub_name)(channel)
        for command in (0, 1):
            result = stub.Driver(lifecycle_pb2.Driver_Request(
                command=command, config_json=json.dumps(config)), timeout=60)
            if not result.ok:
                raise RuntimeError(f"{provider}: {result.error}")
            print(f"{provider}: {result.state}", flush=True)


initialize("audio_client_bridge", "robonix/primitive/audio/driver",
           "RobonixPrimitiveAudioDriverStub",
           {"transport": "reverse", "listen_port": 60002})
initialize("speech", "robonix/service/speech/driver",
           "RobonixServiceSpeechDriverStub",
           {"speech_backend": "tencent", "tencent_asr_appid": "1367440399",
            "tencent_asr_engine": "16k_zh_en",
            "default_speaker_provider_id": "audio_client_bridge"})
