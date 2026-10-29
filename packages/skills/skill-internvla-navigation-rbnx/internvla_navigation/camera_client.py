from __future__ import annotations

import asyncio
import base64
import binascii
import json
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


class CameraClient:
    def __init__(self, endpoint: str, timeout: float) -> None:
        self.endpoint = endpoint
        self.timeout = timeout
        self.executor = ThreadPoolExecutor(max_workers=1)

    @staticmethod
    def _payload(result: Any) -> dict[str, Any]:
        payload = getattr(result, "structuredContent", None)
        if payload is None:
            payload = getattr(result, "structured_content", None)
        if isinstance(payload, dict):
            return payload

        for item in getattr(result, "content", []):
            text = getattr(item, "text", None)
            if text:
                data = json.loads(text)
                if isinstance(data, dict):
                    return data
        raise RuntimeError("camera snapshot returned no structured image")

    @staticmethod
    def _as_mapping(value: Any) -> dict[str, Any] | None:
        """Convert MCP/Pydantic/codegen objects to a plain mapping."""
        if isinstance(value, Mapping):
            return dict(value)

        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            converted = to_dict()
            if isinstance(converted, Mapping):
                return dict(converted)

        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            converted = model_dump(mode="json")
            if isinstance(converted, Mapping):
                return dict(converted)
        return None

    @classmethod
    def _find_image_payload(cls, value: Any, depth: int = 0) -> dict[str, Any]:
        """Find sensor_msgs/Image inside MCP SDK result wrappers."""
        if depth > 6:
            raise RuntimeError("camera snapshot result is nested too deeply")

        payload = cls._as_mapping(value)
        if payload is None:
            raise RuntimeError(
                "camera snapshot returned an unsupported payload type: "
                f"{type(value).__name__}"
            )
        if "data" in payload and (
            "encoding" in payload or "header" in payload or "height" in payload
        ):
            return payload

        # FastMCP/MCP SDK versions use different names for a structured
        # return value.  Do not assume the sensor message is top-level.
        for key in ("result", "image", "output", "value", "root"):
            if key in payload:
                try:
                    return cls._find_image_payload(payload[key], depth + 1)
                except RuntimeError:
                    pass

        for nested in payload.values():
            if cls._as_mapping(nested) is not None:
                try:
                    return cls._find_image_payload(nested, depth + 1)
                except RuntimeError:
                    pass
        raise RuntimeError(
            "camera snapshot returned no sensor_msgs/Image fields; "
            f"keys={sorted(map(str, payload.keys()))}"
        )

    @classmethod
    def _decode_binary(cls, value: Any, depth: int = 0) -> bytes:
        """Decode uint8[] representations emitted by MCP/codegen versions."""
        if depth > 6:
            raise RuntimeError("camera snapshot byte data is nested too deeply")
        if isinstance(value, bytes):
            return value
        if isinstance(value, (bytearray, memoryview)):
            return bytes(value)
        if isinstance(value, str):
            encoded = value.strip()
            if encoded.startswith("data:"):
                marker = ";base64,"
                if marker not in encoded:
                    raise RuntimeError("camera snapshot data URL is not base64")
                encoded = encoded.split(marker, 1)[1]
            try:
                return base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise RuntimeError("camera snapshot contains invalid base64 data") from exc
        if isinstance(value, Sequence):
            try:
                return bytes(value)
            except (TypeError, ValueError) as exc:
                raise RuntimeError("camera snapshot contains an invalid byte sequence") from exc

        wrapped = cls._as_mapping(value)
        if wrapped is not None:
            # Includes Pydantic RootModel, serde wrappers and Node Buffer JSON.
            for key in ("base64", "data", "value", "root", "bytes"):
                if key in wrapped:
                    return cls._decode_binary(wrapped[key], depth + 1)

        for attr in ("root", "value", "data"):
            if hasattr(value, attr):
                return cls._decode_binary(getattr(value, attr), depth + 1)

        raise RuntimeError(
            "camera snapshot data has an unsupported type: "
            f"{type(value).__module__}.{type(value).__name__}"
        )

    @classmethod
    def _image_bytes(cls, payload: dict[str, Any]) -> bytes:
        payload = cls._find_image_payload(payload)
        image = cls._decode_binary(payload.get("data"))

        header = cls._as_mapping(payload.get("header")) or {}
        frame_id = str(header.get("frame_id", ""))
        if frame_id.startswith("error:"):
            raise RuntimeError(frame_id.removeprefix("error:"))
        encoding = str(payload.get("encoding", "")).lower()
        if encoding not in {"jpeg", "jpg"} or not image.startswith(b"\xff\xd8"):
            raise RuntimeError("camera snapshot is not a JPEG image")
        return image

    async def _snapshot(self) -> bytes:
        async with streamablehttp_client(self.endpoint) as streams:
            read_stream, write_stream = streams[:2]
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool("snapshot", arguments={})

        if getattr(result, "isError", False):
            raise RuntimeError("camera snapshot failed")
        return self._image_bytes(self._payload(result))

    def snapshot(self) -> bytes:
        future = self.executor.submit(asyncio.run, self._snapshot())
        return future.result(timeout=self.timeout)

    def close(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)