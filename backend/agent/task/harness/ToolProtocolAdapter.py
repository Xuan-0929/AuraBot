"""Translate harness messages to and from the generic tools protocol."""

from collections.abc import Mapping, Sequence
from typing import Any

from ...LLMClient import Message
from ...tools.core.ToolCallEntity import (
    ToolExecutionResult,
    ToolProtocolResponse,
)
from ...tools.core.ToolCallProtocol import ToolCallProtocol


class HarnessToolProtocolAdapter:
    """Keep LLM message roles outside the harness-neutral tools module."""

    def __init__(self, protocol: ToolCallProtocol | None = None):
        self._protocol = protocol or ToolCallProtocol()

    @property
    def protocol(self) -> ToolCallProtocol:
        return self._protocol

    def parse_assistant_message(
        self,
        message: Mapping[str, Any],
    ) -> ToolProtocolResponse:
        if message.get("role") != "assistant":
            raise ValueError("tool protocol input must be an assistant message")
        if message.get("tool_calls") is not None:
            raise ValueError("native tool_calls are not supported")

        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("assistant message must contain non-empty text content")
        return self._protocol.parse(content)

    def build_observation_message(
        self,
        results: Sequence[ToolExecutionResult],
    ) -> Message:
        return {
            "role": "user",
            "content": self._protocol.encode_observation(results),
        }


__all__ = ["HarnessToolProtocolAdapter"]
