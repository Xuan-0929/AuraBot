import json
from collections.abc import AsyncIterator, Iterable
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

Message = dict[str, Any]


class LLMClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.2,
        timeout: float | None = 30,
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.timeout = timeout

    def chunk_invoke(self, messages: list[Message]) -> str:
        response = self._post(self._build_body(messages))

        with response:
            return self._extract_content(json.loads(response.read().decode("utf-8")))

    async def invoke(self, messages: list[Message]) -> str:
        return self.chunk_invoke(messages)

    async def aysnc_invoke(self, messages: list[Message]) -> str:
        return self.chunk_invoke(messages)

    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        response = self._post(self._build_body(messages, stream=True))

        with response:
            for delta in self._iter_stream(response):
                yield delta

    def _build_body(self, messages: list[Message], stream: bool = False) -> bytes:
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }

        if stream:
            body["stream"] = True

        return json.dumps(body).encode("utf-8")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "content-type": "application/json",
        }

    def _chat_completions_url(self) -> str:
        return urljoin(self.base_url.rstrip("/") + "/", "chat/completions")

    def _post(self, body: bytes) -> Any:
        request = Request(
            self._chat_completions_url(),
            data=body,
            headers=self._headers(),
            method="POST",
        )

        try:
            return urlopen(request, timeout=self.timeout)
        except HTTPError as exc:
            raise RuntimeError(
                f"OpenAI-compatible request failed with status {exc.code}"
            ) from exc

    def _extract_content(self, payload: dict[str, Any]) -> str:
        choice = (payload.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content") or choice.get("text") or ""

        if not content.strip():
            raise ValueError("OpenAI-compatible response did not include content")

        return content

    def _iter_stream(
        self,
        chunks: Iterable[bytes],
    ) -> Iterable[str]:
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

        return content

    def _consume_sse_buffer(
        self,
        buffer: str,
    ) -> dict[str, Any]:
        parts = buffer.replace("\r\n", "\n").split("\n\n")
        remaining = parts.pop() if parts else ""
        deltas = []

        for part in parts:
            delta = self._parse_sse_delta(part)

            if not delta:
                continue

            deltas.append(delta)

        return {"deltas": deltas, "remaining": remaining}

    def _parse_sse_delta(self, part: str) -> str | None:
        content = ""

        for line in part.splitlines():
            line = line.strip()

            if not line.startswith("data:"):
                continue

            data = line[len("data:") :].strip()

            if not data or data == "[DONE]":
                continue

            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                continue

            choice = (payload.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            message = choice.get("message") or {}
            content += (
                delta.get("content")
                or message.get("content")
                or choice.get("text")
                or ""
            )

        return content or None
