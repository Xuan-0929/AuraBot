"""Provide synchronous and cancellable async model boundaries for Memory extraction.

Legacy callers keep the synchronous request. Main Agent conversation ingestion uses the async request so
new user activity can cancel the HTTP operation before Memory creates an episode; both paths validate the
same Chat Completions envelope and leave candidate validation to ``StructuredMemoryExtractor``. Local model
endpoints bypass environment proxies; external extraction endpoints retain configured HTTP/SOCKS routing.
"""

from __future__ import annotations

import json
from typing import Any, Protocol
from urllib.parse import urlsplit


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
        async_client_factory: Any | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        trust_env = urlsplit(self.base_url).hostname not in {"localhost", "127.0.0.1", "::1"}
        self._client_factory = client_factory or (lambda **kwargs: _http_client(trust_env=trust_env, **kwargs))
        self._async_client_factory = async_client_factory or (lambda **kwargs: _async_http_client(trust_env=trust_env, **kwargs))

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
        facts = _facts(content, metadata, memory_type, importance)
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
                                "Extract durable user memories, entities, and a concise conversation summary. "
                                "Return one JSON object with memories and entities arrays plus summary text. "
                                "The summary preserves topics, temporary intent, decisions, and unresolved "
                                "references. Output JSON only, without markdown."
                            ),
                        },
                        {"role": "user", "content": facts},
                    ],
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            envelope = response.json()
        return _payload(envelope)

    async def extract_memory_payload_async(
        self,
        *,
        content: str,
        metadata: dict[str, Any],
        memory_type: str | None = None,
        importance: float = 0.5,
    ) -> dict[str, Any]:
        """Run one cancellation-aware extraction request without opening durable Memory state."""

        if not self.base_url or not self.model_name or not self.api_key:
            raise MemoryExtractionUnavailable("memory extraction model is unavailable")
        async with self._async_client_factory(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model_name,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Extract durable user memories, entities, and a concise conversation summary. "
                                "Return one JSON object with memories and entities arrays plus summary text. "
                                "The summary preserves topics, temporary intent, decisions, and unresolved "
                                "references. Output JSON only, without markdown."
                            ),
                        },
                        {"role": "user", "content": _facts(content, metadata, memory_type, importance)},
                    ],
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            envelope = response.json()
        return _payload(envelope)


def _facts(
    content: str,
    metadata: dict[str, Any],
    memory_type: str | None,
    importance: float,
) -> str:
    return json.dumps(
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


def _payload(envelope: Any) -> dict[str, Any]:
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


def _async_http_client(**kwargs: Any) -> Any:
    import httpx

    return httpx.AsyncClient(**kwargs)


__all__ = [
    "MemoryExtractionModel",
    "MemoryExtractionUnavailable",
    "OpenAICompatibleMemoryExtractionModel",
]
