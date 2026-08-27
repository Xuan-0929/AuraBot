"""Adapt one OpenAI-compatible Chat Completions SSE stream to Main Agent provider chunks.

The adapter serializes an immutable InvokeRequest snapshot and frozen tool surface, opens HTTP only
when that invoke is actually consumed, decodes provider SSE data frames into the mapping protocol
already accepted by ToolCallAssembler, and owns cancellation/deadline/client shutdown.  Composition
uses the unavailable provider when credentials are absent, so import, startup and readiness remain
strictly local and never probe or log a model endpoint or secret.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any

from .metrics import LifecycleMetrics
from .settings import ChatModelSettings


class ProviderUnavailable(RuntimeError):
    """Stable provider configuration/runtime boundary failure without secret material."""


class UnavailableChatProvider:
    """Settle invokes silently when production credentials are intentionally unavailable."""

    def __init__(self, metrics: LifecycleMetrics | None = None) -> None:
        self.metrics = metrics

    async def __call__(self, _request: Any) -> AsyncIterator[Mapping[str, Any]]:
        if self.metrics is not None:
            self.metrics.increment("main.provider.unavailable")
        if False:
            yield {}

    async def aclose(self) -> None:
        return None


class OpenAICompatibleProvider:
    """Own a lazy shared HTTP client and stream validated Chat Completions SSE chunks."""

    def __init__(
        self,
        settings: ChatModelSettings,
        *,
        metrics: LifecycleMetrics | None = None,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.settings = settings
        self.metrics = metrics
        self._client_factory = client_factory or _http_client
        self._client: Any | None = None
        self._client_lock = asyncio.Lock()

    async def _client_owner(self) -> Any:
        async with self._client_lock:
            if self._client is None:
                self._client = self._client_factory()
            return self._client

    async def __call__(self, request: Any) -> AsyncIterator[Mapping[str, Any]]:
        """Open exactly one bounded SSE request for an immutable runtime invoke."""

        client = await self._client_owner()
        body = {
            "model": self.settings.model_name,
            "stream": True,
            "messages": [{"role": "system", "content": request.snapshot.prompt}],
            "tools": list(request.tool_surface.provider_schemas()),
            "tool_choice": "auto",
        }
        try:
            async with asyncio.timeout(self.settings.timeout_seconds):
                async with client.stream(
                    "POST",
                    f"{self.settings.base_url}/chat/completions",
                    headers={
                        "authorization": f"Bearer {self.settings.api_key}",
                        "accept": "text/event-stream",
                    },
                    json=body,
                    timeout=self.settings.timeout_seconds,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if not data or data == "[DONE]":
                            continue
                        event = json.loads(data)
                        if not isinstance(event, Mapping) or "error" in event:
                            raise ProviderUnavailable("provider stream error")
                        yield event
        except asyncio.CancelledError:
            if self.metrics is not None:
                self.metrics.increment("main.provider.cancelled")
            raise
        except TimeoutError:
            if self.metrics is not None:
                self.metrics.increment("main.provider.timeout")
            raise
        except Exception:
            if self.metrics is not None:
                self.metrics.increment("main.provider.error")
            raise

    async def aclose(self) -> None:
        """Close only a client actually constructed by a real invoke."""

        async with self._client_lock:
            client, self._client = self._client, None
        if client is not None:
            await client.aclose()


def build_chat_provider(
    settings: ChatModelSettings,
    metrics: LifecycleMetrics | None = None,
    *,
    client_factory: Callable[[], Any] | None = None,
) -> OpenAICompatibleProvider | UnavailableChatProvider:
    """Return a lazy network provider only when an API key is configured."""

    if not settings.api_key or not settings.base_url or not settings.model_name:
        return UnavailableChatProvider(metrics)
    return OpenAICompatibleProvider(
        settings, metrics=metrics, client_factory=client_factory
    )


def _http_client() -> Any:
    """Import the installed lightweight HTTP dependency only on first real invoke."""

    import httpx

    return httpx.AsyncClient()


__all__ = [
    "OpenAICompatibleProvider", "ProviderUnavailable", "UnavailableChatProvider",
    "build_chat_provider",
]
