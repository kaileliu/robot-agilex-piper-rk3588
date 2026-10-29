from __future__ import annotations

import json
from typing import Any

import requests


class InternVLAClient:
    def __init__(self, server_url: str, client_id: str, timeout: float) -> None:
        self.server_url = server_url.rstrip("/")
        self.client_id = client_id
        self.timeout = timeout
        self.session = requests.Session()

    @staticmethod
    def _json(response: requests.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"InternVLA returned non-JSON HTTP {response.status_code}"
            ) from exc

        if not response.ok or not data.get("ok", False):
            message = data.get("message") or data.get("error") or response.reason
            raise RuntimeError(f"InternVLA HTTP {response.status_code}: {message}")
        return data

    def health(self) -> dict[str, Any]:
        response = self.session.get(f"{self.server_url}/health", timeout=self.timeout)
        return self._json(response)

    def start_task(self, instruction: str) -> str:
        response = self.session.post(
            f"{self.server_url}/task/start",
            json={"client_id": self.client_id, "instruction": instruction},
            timeout=self.timeout,
        )
        return str(self._json(response)["task_id"])

    def infer(self, task_id: str, frame_id: int, image_jpeg: bytes) -> dict[str, Any]:
        meta = {
            "client_id": self.client_id,
            "task_id": task_id,
            "frame_id": frame_id,
        }
        response = self.session.post(
            f"{self.server_url}/eval_dual",
            data={"json": json.dumps(meta)},
            files={"image": ("rgb.jpg", image_jpeg, "image/jpeg")},
            timeout=self.timeout,
        )
        return self._json(response)

    def report(self, task_id: str, status: str, message: str = "") -> None:
        response = self.session.post(
            f"{self.server_url}/task/report",
            json={
                "client_id": self.client_id,
                "task_id": task_id,
                "status": status,
                "message": message,
            },
            timeout=self.timeout,
        )
        self._json(response)

    def close(self) -> None:
        self.session.close()

