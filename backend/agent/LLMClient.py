import json
from collections.abc import Iterable
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

Message = dict[str, str]


class LLMClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        stream: bool = False,
        temperature: float = 0.2,
        timeout: float | None = 30,
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.stream = stream
        self.temperature = temperature
        self.timeout = timeout

    def invoke(self, messages: list[Message]) -> str:
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }

        if self.stream:
            body["stream"] = True

        response = self._post(json.dumps(body).encode("utf-8"))

        with response:
            if self.stream:
                return self._consume_stream(response)

            return self._extract_content(json.loads(response.read().decode("utf-8")))

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

    def _consume_stream(
        self,
        chunks: Iterable[bytes],
    ) -> str:
        buffer = ""
        content = ""

        for chunk in chunks:
            buffer += chunk.decode("utf-8")
            parsed = self._consume_sse_buffer(buffer)
            content += parsed["content"]
            buffer = parsed["remaining"]

        if buffer.strip():
            parsed = self._consume_sse_buffer(f"{buffer}\n\n")
            content += parsed["content"]

        if not content.strip():
            raise ValueError(
                "OpenAI-compatible streaming response did not include content"
            )

        return content

    def _consume_sse_buffer(
        self,
        buffer: str,
    ) -> dict[str, str]:
        parts = buffer.replace("\r\n", "\n").split("\n\n")
        remaining = parts.pop() if parts else ""
        content = ""

        for part in parts:
            delta = self._parse_sse_delta(part)

            if not delta:
                continue

            content += delta

        return {"content": content, "remaining": remaining}

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
