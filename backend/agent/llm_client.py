"""
定义 Harness 使用的 LLM client 契约及 OpenAI-compatible 实现。

具体 Harness 提交消息、工具 schema 与模型参数，本模块调用 chat completions endpoint
并返回稳定 choice 值；它不解释工具调用语义，也不管理 Task 或 Harness 生命周期。
"""

import asyncio
import json
from collections.abc import AsyncIterator, Iterable
from typing import Any
from urllib.parse import urljoin

import httpx

from .llm_protocol import (
    ChatCompletionChoice,
    Message,
    OpenAITool,
    ToolChoice,
    consume_sse_buffer,
    extract_choice,
    parse_sse_delta,
)


class LLMClient:
    """Shared OpenAI-compatible HTTP client for concurrent agent loops.

    Request-specific state stays in local variables, so one instance can be
    shared by multiple asyncio tasks running on the same event loop.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.2,
        timeout: float | None = 30,
        max_concurrency: int = 8,
        http_client: httpx.AsyncClient | None = None,
    ):
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")

        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self.max_concurrency = max_concurrency

        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._http_client = http_client or httpx.AsyncClient(
            headers=self._headers(),
            timeout=timeout,
        )

    def chunk_invoke(
        self,
        messages: list[Message],
        *,
        tools: list[OpenAITool] | None = None,
        tool_choice: ToolChoice | None = None,
    ) -> ChatCompletionChoice:
        """Compatibility API for callers that still require blocking I/O."""
        try:
            response = httpx.post(
                self._chat_completions_url(),
                headers=self._headers(),
                content=self._build_body(
                    messages,
                    tools=tools,
                    tool_choice=tool_choice,
                ),
                timeout=self.timeout,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"OpenAI-compatible request failed with status {exc.response.status_code}"
            ) from exc

        return self._extract_choice(response.json())

    async def invoke(
        self,
        messages: list[Message],
        *,
        tools: list[OpenAITool] | None = None,
        tool_choice: ToolChoice | None = None,
    ) -> ChatCompletionChoice:
        async with self._semaphore:
            response = await self._http_client.post(
                self._chat_completions_url(),
                content=self._build_body(
                    messages,
                    tools=tools,
                    tool_choice=tool_choice,
                ),
                headers=self._headers(),
            )
            self._raise_for_status(response)

        return self._extract_choice(response.json())

    async def aysnc_invoke(
        self,
        messages: list[Message],
        *,
        tools: list[OpenAITool] | None = None,
        tool_choice: ToolChoice | None = None,
    ) -> ChatCompletionChoice:
        """Backward-compatible alias for the historical misspelled method."""
        return await self.invoke(
            messages,
            tools=tools,
            tool_choice=tool_choice,
        )

    async def stream(
        self,
        messages: list[Message],
        *,
        tools: list[OpenAITool] | None = None,
        tool_choice: ToolChoice | None = None,
    ) -> AsyncIterator[str]:
        if tools or tool_choice is not None:
            raise ValueError(
                "streaming tool calls are not supported; use invoke()"
            )

        content = ""
        buffer = ""

        async with self._semaphore:
            async with self._http_client.stream(
                "POST",
                self._chat_completions_url(),
                content=self._build_body(messages, stream=True),
                headers=self._headers(),
            ) as response:
                self._raise_for_status(response)

                async for chunk in response.aiter_text():
                    buffer += chunk
                    parsed = self._consume_sse_buffer(buffer)
                    buffer = parsed["remaining"]

                    for delta in parsed["deltas"]:
                        content += delta
                        yield delta

        if buffer.strip():
            parsed = self._consume_sse_buffer(f"{buffer}\n\n")

            for delta in parsed["deltas"]:
                content += delta
                yield delta

        if not content.strip():
            raise ValueError(
                "OpenAI-compatible streaming response did not include content"
            )

    async def aclose(self) -> None:
        await self._http_client.aclose()

    async def __aenter__(self) -> "LLMClient":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.aclose()

    def _build_body(
        self,
        messages: list[Message],
        stream: bool = False,
        *,
        tools: list[OpenAITool] | None = None,
        tool_choice: ToolChoice | None = None,
    ) -> bytes:
        if tool_choice is not None and not tools:
            raise ValueError("tool_choice requires non-empty tools")

        body = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }

        if stream:
            body["stream"] = True
        if tools:
            body["tools"] = tools
            if tool_choice is not None:
                body["tool_choice"] = tool_choice

        return json.dumps(body).encode("utf-8")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "content-type": "application/json",
        }

    def _chat_completions_url(self) -> str:
        return urljoin(self.base_url.rstrip("/") + "/", "chat/completions")

    def _raise_for_status(self, response: httpx.Response) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"OpenAI-compatible request failed with status {response.status_code}"
            ) from exc

    def _extract_choice(self, payload: dict[str, Any]) -> ChatCompletionChoice:
        return extract_choice(payload)

    def _iter_stream(
        self,
        chunks: Iterable[bytes],
    ) -> Iterable[str]:
        """Retained parser helper for compatibility with existing callers."""
        buffer = ""
        content = ""

        for chunk in chunks:
            buffer += chunk.decode("utf-8")
            parsed = self._consume_sse_buffer(buffer)
            buffer = parsed["remaining"]

            for delta in parsed["deltas"]:
                content += delta
                yield delta

        if buffer.strip():
            parsed = self._consume_sse_buffer(f"{buffer}\n\n")

            for delta in parsed["deltas"]:
                content += delta
                yield delta

        if not content.strip():
            raise ValueError(
                "OpenAI-compatible streaming response did not include content"
            )

    def _consume_sse_buffer(
        self,
        buffer: str,
    ) -> dict[str, Any]:
        return consume_sse_buffer(buffer)

    def _parse_sse_delta(self, part: str) -> str | None:
        return parse_sse_delta(part)


__all__ = [
    "ChatCompletionChoice",
    "LLMClient",
    "Message",
    "OpenAITool",
    "ToolChoice",
]
