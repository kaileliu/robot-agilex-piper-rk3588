from __future__ import annotations

import json

import grpc

import chassis_pb2
import robonix_contracts_pb2_grpc


class ChassisClient:
    def __init__(
        self,
        endpoint: str,
        timeout: float,
        *,
        service_name: str,
        method: str,
    ) -> None:
        self.timeout = timeout
        self.channel = grpc.insecure_channel(endpoint)

        stub_name = f"{service_name}Stub"
        stub_cls = getattr(robonix_contracts_pb2_grpc, stub_name, None)
        if stub_cls is None:
            available = sorted(
                name
                for name in vars(robonix_contracts_pb2_grpc)
                if name.endswith("Stub")
            )
            self.channel.close()
            raise RuntimeError(
                f"generated gRPC stub {stub_name!r} is missing; "
                f"available stubs: {available}"
            )

        self.stub = stub_cls(self.channel)
        self.rpc = getattr(self.stub, method, None)
        if self.rpc is None:
            self.channel.close()
            raise RuntimeError(
                f"generated gRPC stub {stub_name!r} has no RPC {method!r}"
            )

    def move(
        self,
        *,
        forward_m: float = 0.0,
        rotate_deg: float = 0.0,
        linear_x: float = 0.0,
        linear_y: float = 0.0,
        angular_z: float = 0.0,
        duration_sec: float = 0.0,
    ) -> None:
        command = chassis_pb2.MoveCommand(
            forward_m=forward_m,
            rotate_deg=rotate_deg,
            linear_x=linear_x,
            linear_y=linear_y,
            angular_z=angular_z,
            duration_sec=duration_sec,
        )
        request = chassis_pb2.ExecuteMoveCommand_Request(command=command)
        response = self.rpc(request, timeout=self.timeout)

        raw = getattr(getattr(response, "status", None), "data", "").strip()
        if not raw:
            return
        try:
            status = json.loads(raw)
        except json.JSONDecodeError:
            if raw.lower() not in {"ok", "done", "success"}:
                raise RuntimeError(raw)
            return
        if status.get("status") not in {"ok", "done", "success"}:
            raise RuntimeError(status.get("error") or status.get("message") or raw)

    def close(self) -> None:
        self.channel.close()