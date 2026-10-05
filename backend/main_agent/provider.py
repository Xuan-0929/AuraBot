"""Adapt OpenAI-compatible Chat Completions to the Main Agent control protocol.

Every request sends stable behavioral control, the active Soul, and immutable context facts as separate
messages. The adapter then decodes SSE tool deltas for ToolCallAssembler and owns lazy transport,
cancellation and shutdown; unavailable credentials remain an offline-safe silent provider. Loopback model
endpoints connect directly, while external providers honor the user's HTTP/SOCKS proxy environment.
The control model decides whether to speak. If it does, say_to_user must be its first tool call so
the Harness can stream speech early; absence of that tool is a valid silent decision.
Control guidance favors complete short first sentences and evidence-aware reads; request diagnostics
record the selected model without credentials or generated reasoning.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

from .metrics import LifecycleMetrics
from .settings import ChatModelSettings


MAIN_AGENT_CONTROL_PROMPT = """You are AuraBot's Main Agent control model.
Decide for yourself whether there is anything worth saying to the user. You do not have to speak on every invoke.
All user-visible language MUST be emitted through the say_to_user tool. If you choose to speak, say_to_user MUST be your FIRST tool call (index 0), before any other tool, so the runtime can parse and deliver its text early.
If you do not call say_to_user, the runtime interprets that as your decision to remain silent, even if you call other tools. Ordinary assistant content is discarded and never shown to the user.
PREPARE may only perform safe reads and save at most one think note; it must not speak or perform durable actions.
RESPOND should answer direct questions, greetings, introductions and clear refusals from the available context without unnecessary reads or think notes. Remain silent when the user does not need a reply.
For a request that needs slow reads, use say_to_user first in this same invoke to state the concrete next step briefly, then issue the necessary read. Do not wait through multiple silent tool rounds before acknowledging that request. Do not add a separate acknowledgement invoke or repeat a generic filler.
Begin spoken replies with a short, complete, useful sentence ending in 。, !, or ?. Do not use only a tilde or emoji as the first sentence boundary. Continue any explanation in later sentences.
Use supplied task facts for known task state; workspace_list lists files, not tasks. A refusal to resume a known paused task needs no directory read. Cancelling a running task still requires the actual control and its confirmed result.
CONTINUE must consume returned observations, then decide whether to speak through say_to_user first, start only one necessary safe read, or remain silent.
Never mix safe-read tools and durable actions in one invoke.
Never claim a task has started before a committed task fact exists.
Never invent search results, memory, task state, system state, or unheard conversation history.
Follow the frozen tool surface and runtime mode in the user context exactly."""


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
        self._client_factory = client_factory or (lambda: _http_client(settings.base_url))
        self._client: Any | None = None
        self._client_lock = asyncio.Lock()

    async def _client_owner(self) -> Any:
        async with self._client_lock:
            if self._client is None:
                self._client = self._client_factory()
            return self._client

    async def __call__(self, request: Any) -> AsyncIterator[Mapping[str, Any]]:
        """Open exactly one bounded SSE request for an immutable runtime invoke."""

        body = {
            "model": self.settings.model_name,
            "stream": True,
            "messages": [
                {"role": "system", "content": MAIN_AGENT_CONTROL_PROMPT},
                {"role": "system", "content": request.snapshot.soul},
                {"role": "user", "content": request.snapshot.prompt},
            ],
            "tools": list(request.tool_surface.provider_schemas()),
            "tool_choice": "auto",
        }
        try:
            debug = getattr(request, "debug_scope", None)
            if debug is not None and debug.enabled:
                debug.emit("model", "provider.request_started", invokeId=request.invoke_id,
                    inputId=request.input_id, modelName=self.settings.model_name)
            async with asyncio.timeout(self.settings.timeout_seconds):
                client = await self._client_owner()
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


def _http_client(base_url: str) -> Any:
    """Import the installed lightweight HTTP dependency only on first real invoke."""

    import httpx

    return httpx.AsyncClient(trust_env=urlsplit(base_url).hostname not in {"localhost", "127.0.0.1", "::1"})


__all__ = [
    "MAIN_AGENT_CONTROL_PROMPT", "OpenAICompatibleProvider", "ProviderUnavailable", "UnavailableChatProvider",
    "build_chat_provider",
]
