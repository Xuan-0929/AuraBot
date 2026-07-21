"""Parse tool calls from an LLM response without executing them.

The current :class:`LLMClient` returns one assistant message. This module also
accepts text, message lists, and complete OpenAI-compatible response objects so
callers can normalize tool calls at one boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..core.ToolCallEntity import ToolCall
from ..core.ToolExceptions import ToolCallParseError
from .tool_call_response import (
    decode_tool_call_response,
    extract_raw_tool_calls,
    parse_tool_arguments,
)


ParserInput = str | Mapping[str, Any] | list[Mapping[str, Any]]


def parse_tool_calls(response: ParserInput) -> list[ToolCall]:
    """Return all tool calls declared by one LLM response.

    Supported inputs are:

    * the assistant message returned by the current ``LLMClient``;
    * ordinary text or JSON;
    * an OpenAI-compatible response object containing ``choices``;
    * an OpenAI-compatible message containing ``tool_calls``.

    Text JSON may use ``tool_calls`` (OpenAI shape) or a ``tool`` field whose
    value is one call or a list of calls. Calls are parsed atomically: if one
    item is invalid, no partial result is returned.
    """

    payload = decode_tool_call_response(response)
    raw_calls = extract_raw_tool_calls(payload)
    return [
        _parse_call(raw_call, index)
        for index, raw_call in enumerate(raw_calls)
    ]


def parse_tool_call(response: ParserInput) -> list[ToolCall]:
    """Compatibility alias for callers using the historical singular name."""

    return parse_tool_calls(response)


def _parse_call(raw_call: Any, index: int) -> ToolCall:
    location = f"tool call at index {index}"
    if not isinstance(raw_call, Mapping):
        raise ToolCallParseError(f"{location} must be an object")

    call_type = raw_call.get("type")
    if call_type is not None and call_type not in {"function", "tool_call"}:
        raise ToolCallParseError(f"{location} has unsupported type")

    function = raw_call.get("function")
    if function is not None:
        if not isinstance(function, Mapping):
            raise ToolCallParseError(f"{location}.function must be an object")
        name = function.get("name")
        arguments = function.get("arguments")
    else:
        name = raw_call.get("name", raw_call.get("tool_name"))
        if name is None and isinstance(raw_call.get("tool"), str):
            name = raw_call["tool"]
        arguments = raw_call.get("arguments")

    if not isinstance(name, str) or not name or name != name.strip():
        raise ToolCallParseError(f"{location} has an invalid tool name")

    parsed_arguments = parse_tool_arguments(arguments, location)

    call_id = raw_call.get("id")
    if call_id is not None and (
        not isinstance(call_id, str) or not call_id or call_id != call_id.strip()
    ):
        raise ToolCallParseError(f"{location} has an invalid id")

    return ToolCall(name=name, arguments=parsed_arguments, call_id=call_id)


__all__ = [
    "ParserInput",
    "ToolCall",
    "ToolCallParseError",
    "parse_tool_call",
    "parse_tool_calls",
]
