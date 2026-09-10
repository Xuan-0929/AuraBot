"""Adapt public-web providers to normalized search records and extracted text.

WebTools owns ToolCall handling and bounded source references; this module owns provider HTTP,
authentication and secret-free failures. Composition selects one adapter per Profile, with one lazy
client closed through the existing Profile resource lifecycle.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol


class ObservationExecutorError(RuntimeError):
    """Stable external provider failure without upstream detail."""


@dataclass(frozen=True, slots=True)
class SearchResult:
    url: str
    title: str
    snippet: str


class SearchProvider(Protocol):
    async def search(self, query: str) -> list[SearchResult]: ...
    async def read(self, url: str) -> str: ...
    async def aclose(self) -> None: ...


def create_search_provider(provider: str, api_key: str) -> SearchProvider:
    if provider != "tavily":
        raise ValueError("Unsupported search provider")
    return TavilyProvider(api_key)


class TavilyProvider:
    """Normalize Tavily search/extract using one lazily created HTTP client."""

    def __init__(self, api_key: str, *, client: Any | None = None, timeout_seconds: float = 10.0) -> None:
        if not api_key:
            raise ValueError("Tavily API key is required")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._client = client

    async def search(self, query: str) -> list[SearchResult]:
        data = await self._post("search", {
            "query": query, "search_depth": "basic", "max_results": 8,
            "include_answer": False, "include_raw_content": False, "include_images": False,
        })
        items = data.get("results", [])
        if not isinstance(items, list):
            raise ObservationExecutorError("web_provider_failed")
        return [
            SearchResult(item["url"], str(item.get("title", "")), str(item.get("content", "")))
            for item in items[:8]
            if isinstance(item, Mapping) and isinstance(item.get("url"), str)
        ]

    async def read(self, url: str) -> str:
        data = await self._post("extract", {
            "urls": [url], "extract_depth": "basic", "format": "markdown", "include_images": False,
        })
        results = data.get("results", [])
        if not isinstance(results, list) or not results or not isinstance(results[0], Mapping):
            raise ObservationExecutorError("web_extract_failed")
        return str(results[0].get("raw_content", ""))

    async def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            if self._client is None:
                import httpx
                self._client = httpx.AsyncClient()
            response = await self._client.post(
                f"https://api.tavily.com/{endpoint}",
                json={**payload, "api_key": self.api_key},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError
            return data
        except Exception as exc:
            raise ObservationExecutorError("web_provider_failed") from exc

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
