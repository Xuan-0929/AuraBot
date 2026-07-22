"""OpenAI Chat Completions function-tool protocol."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from ..ToolGroup import ToolGroup
from .ToolCallEntity import (
    FinalResponse,
    ToolCall,
    ToolCallBatch,
    ToolExecutionResult,
    ToolProtocolResponse,
)
from .ToolExceptions import DuplicateToolNameError, ToolCallParseError


_FUNCTION_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class OpenAIChatToolProtocol:
    """Stateless OpenAI Chat translation for Tool domain entities."""

    @staticmethod
    def encode_tools(
        tool_groups: Sequence[ToolGroup],
    ) -> list[dict[str, Any]]:
        """Encode authorized ToolGroups as OpenAI function-tool definitions."""

        definitions: list[dict[str, Any]] = []
        group_by_tool_name: dict[str, str] = {}

        for group in tool_groups:
            for tool in group.tools:
                if tool.name in group_by_tool_name:
                    raise DuplicateToolNameError(
                        f"tool {tool.name!r} is exposed by both ToolGroup "
                        f"{group_by_tool_name[tool.name]!r} and ToolGroup "
                        f"{group.name!r}"
                    )
                if not isinstance(tool.name, str) or not _is_function_name(tool.name):
                    raise ValueError("OpenAI function tool name is invalid")
                if (
                    not isinstance(tool.description, str)
                    or not tool.description.strip()
                ):
                    raise ValueError(
                        f"OpenAI function tool {tool.name!r} needs a description"
                    )

                group_by_tool_name[tool.name] = group.name
                definitions.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": copy.deepcopy(tool.input_schema),
                        },
                    }
                )

        return definitions

    @staticmethod
    def parse_completion(
        message: Mapping[str, Any],
        finish_reason: str,
    ) -> ToolProtocolResponse:
        """Interpret one non-streaming OpenAI Chat completion atomically."""

        if not isinstance(message, Mapping):
            raise TypeError("OpenAI assistant message must be a mapping")
        if message.get("role") != "assistant":
            raise ToolCallParseError(
                "invalid_message",
                "OpenAI completion message must have assistant role",
            )

        if finish_reason == "tool_calls":
            return OpenAIChatToolProtocol._parse_tool_calls(message)
        if finish_reason == "stop":
            return OpenAIChatToolProtocol._parse_final(message)
        if finish_reason == "length":
            raise ToolCallParseError(
                "incomplete_completion",
                "OpenAI completion stopped before it was complete",
            )
        if finish_reason == "content_filter":
            raise ToolCallParseError(
                "content_filtered",
                "OpenAI completion was blocked by content filtering",
            )
        raise ToolCallParseError(
            "invalid_finish_reason",
            f"unsupported OpenAI finish_reason: {finish_reason!r}",
        )

    @staticmethod
    def encode_tool_messages(
        results: Sequence[ToolExecutionResult],
    ) -> list[dict[str, Any]]:
        """Encode ordered execution results as individual tool messages."""

        if not results:
            raise ValueError("tool message results cannot be empty")

        call_ids = [result.call_id for result in results]
        if any(not call_id or call_id != call_id.strip() for call_id in call_ids):
            raise ValueError("tool message call IDs cannot be empty")
        if len(set(call_ids)) != len(call_ids):
            raise ValueError("tool message call IDs must be unique")

        messages: list[dict[str, Any]] = []
        for result in results:
            content = json.dumps(
                {
                    "name": result.tool_name,
                    "is_error": result.is_error,
                    "content": result.content,
                    "structured_content": result.structured_content,
                    "error_code": result.error_code,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": result.call_id,
                    "content": content,
                }
            )
        return messages

    @staticmethod
    def _parse_final(message: Mapping[str, Any]) -> FinalResponse:
        tool_calls = message.get("tool_calls")
        if tool_calls not in (None, []):
            raise ToolCallParseError(
                "invalid_message",
                "stop completion cannot contain tool_calls",
            )

        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return FinalResponse(content=content)

        refusal = message.get("refusal")
        if isinstance(refusal, str) and refusal.strip():
            return FinalResponse(content=refusal)

        raise ToolCallParseError(
            "invalid_message",
            "stop completion must contain non-empty content or refusal",
        )

    @staticmethod
    def _parse_tool_calls(message: Mapping[str, Any]) -> ToolCallBatch:
        raw_calls = message.get("tool_calls")
        if not isinstance(raw_calls, list) or not raw_calls:
            raise ToolCallParseError(
                "invalid_tool_calls",
                "tool_calls completion must contain a non-empty tool_calls array",
            )

        validated = [
            OpenAIChatToolProtocol._validate_tool_call(raw_call, index)
            for index, raw_call in enumerate(raw_calls)
        ]
        call_ids = [call.call_id for call in validated]
        if len(set(call_ids)) != len(call_ids):
            raise ToolCallParseError(
                "invalid_tool_calls",
                "tool call IDs must be unique within a completion",
            )
        return ToolCallBatch(calls=tuple(validated))

    @staticmethod
    def _validate_tool_call(raw_call: Any, index: int) -> ToolCall:
        location = f"tool call at index {index}"
        if not isinstance(raw_call, Mapping):
            raise ToolCallParseError(
                "invalid_call",
                f"{location} must be an object",
            )
        if set(raw_call) != {"id", "type", "function"}:
            raise ToolCallParseError(
                "invalid_call",
                f"{location} must contain exactly id, type, and function",
            )

        call_id = raw_call["id"]
        if (
            not isinstance(call_id, str)
            or not call_id
            or call_id != call_id.strip()
        ):
            raise ToolCallParseError(
                "invalid_call",
                f"{location} has an invalid ID",
            )
        if raw_call["type"] != "function":
            raise ToolCallParseError(
                "invalid_call",
                f"{location} must have function type",
            )

        function = raw_call["function"]
        if not isinstance(function, Mapping):
            raise ToolCallParseError(
                "invalid_call",
                f"{location} function must be an object",
            )
        if set(function) != {"name", "arguments"}:
            raise ToolCallParseError(
                "invalid_call",
                f"{location} function must contain exactly name and arguments",
            )

        name = function["name"]
        if not isinstance(name, str) or not _is_function_name(name):
            raise ToolCallParseError(
                "invalid_call",
                f"{location} has an invalid function name",
            )

        raw_arguments = function["arguments"]
        if not isinstance(raw_arguments, str):
            raise ToolCallParseError(
                "invalid_arguments",
                f"{location} arguments must be a JSON string",
            )
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            raise ToolCallParseError(
                "invalid_arguments",
                f"{location} arguments are not valid JSON",
            ) from exc
        if not isinstance(arguments, Mapping):
            raise ToolCallParseError(
                "invalid_arguments",
                f"{location} arguments must decode to an object",
            )

        return ToolCall(
            name=name,
            arguments=dict(arguments),
            call_id=call_id,
        )


def _is_function_name(name: str) -> bool:
    return _FUNCTION_NAME_PATTERN.fullmatch(name) is not None


__all__ = ["OpenAIChatToolProtocol"]
