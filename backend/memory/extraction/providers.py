"""Provide strict model boundaries for structured Memory extraction.

The OpenAI-compatible adapter performs one lazy synchronous request per sealed episode, validates the
Chat Completions envelope, and returns only a JSON object. MemoryWorker calls this blocking boundary in
its existing worker thread, while StructuredMemoryExtractor owns candidate schema validation.
"""

from __future__ import annotations

import json
from typing import Any, Protocol


class MemoryExtractionModel(Protocol):
    def extract_memory_payload(
        self,
        *,
        content: str,
        metadata: dict[str, Any],
        memory_type: str | None = None,
        importance: float = 0.5,
    ) -> dict[str, Any]:
        ...


class StaticExtractionModel:
    """Small test/offline provider that returns a fixed extraction payload."""

    def __init__(self, payload: dict[str, Any]):
        self.payload = payload

    def extract_memory_payload(
        self,
        *,
        content: str,
        metadata: dict[str, Any],
        memory_type: str | None = None,
        importance: float = 0.5,
    ) -> dict[str, Any]:
        return self.payload


class MemoryExtractionUnavailable(RuntimeError):
    """Stable failure for missing credentials or malformed model responses."""


class OpenAICompatibleMemoryExtractionModel:
    """Request a strict structured payload from an OpenAI-compatible chat endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        model_name: str,
        api_key: str,
        timeout_seconds: float,
        client_factory: Any | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._client_factory = client_factory or _http_client

    def extract_memory_payload(
        self,
        *,
        content: str,
        metadata: dict[str, Any],
        memory_type: str | None = None,
        importance: float = 0.5,
    ) -> dict[str, Any]:
        if not self.base_url or not self.model_name or not self.api_key:
            raise MemoryExtractionUnavailable("memory extraction model is unavailable")
        facts = json.dumps(
            {
                "content": content,
                "metadata": metadata,
                "memory_type": memory_type,
                "default_importance": importance,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        with self._client_factory(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/chat/completions",
                headers={"authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model_name,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Extract durable user memories and entities. Return one JSON object "
                                "with arrays named memories and entities. Output JSON only, without markdown."
                            ),
                        },
                        {"role": "user", "content": facts},
                    ],
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            envelope = response.json()
        try:
            raw = envelope["choices"][0]["message"]["content"]
            payload = json.loads(raw)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise MemoryExtractionUnavailable("memory extraction response is invalid") from exc
        if not isinstance(payload, dict):
            raise MemoryExtractionUnavailable("memory extraction response must be an object")
        return payload


def _http_client(**kwargs: Any) -> Any:
    import httpx

    return httpx.Client(**kwargs)


__all__ = [
    "MemoryExtractionModel",
    "MemoryExtractionUnavailable",
    "OpenAICompatibleMemoryExtractionModel",
    "StaticExtractionModel",
]
