"""OpenAI-compatible chat response values and parsers."""

import json
from dataclasses import dataclass
from typing import Any

Message = dict[str, Any]
OpenAITool = dict[str, Any]
ToolChoice = str | dict[str, Any]


@dataclass(frozen=True, slots=True)
class ChatCompletionChoice:
    """One non-streaming OpenAI Chat Completions choice."""

    message: Message
    finish_reason: str


def extract_choice(payload: dict[str, Any]) -> ChatCompletionChoice:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError(
            "OpenAI-compatible response did not include a completion choice"
        )
    choice = choices[0]
    if not isinstance(choice, dict):
        raise ValueError(
            "OpenAI-compatible completion choice must be an object"
        )
    finish_reason = choice.get("finish_reason")
    if not isinstance(finish_reason, str) or not finish_reason:
        raise ValueError(
            "OpenAI-compatible response did not include finish_reason"
        )
    raw_message = choice.get("message")
    if raw_message is None and isinstance(choice.get("text"), str):
        raw_message = {
            "role": "assistant",
            "content": choice["text"],
        }
    if not isinstance(raw_message, dict):
        raise ValueError(
            "OpenAI-compatible response did not include an assistant message"
        )
    message = dict(raw_message)
    message.setdefault("role", "assistant")
    if message["role"] != "assistant":
        raise ValueError(
            "OpenAI-compatible response message must have assistant role"
        )
    return ChatCompletionChoice(
        message=message,
        finish_reason=finish_reason,
    )


def consume_sse_buffer(buffer: str) -> dict[str, Any]:
    parts = buffer.replace("\r\n", "\n").split("\n\n")
    remaining = parts.pop() if parts else ""
    deltas = [
        delta
        for part in parts
        if (delta := parse_sse_delta(part))
    ]
    return {"deltas": deltas, "remaining": remaining}


def parse_sse_delta(part: str) -> str | None:
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


__all__ = [
    "ChatCompletionChoice",
    "Message",
    "OpenAITool",
    "ToolChoice",
    "consume_sse_buffer",
    "extract_choice",
    "parse_sse_delta",
]
