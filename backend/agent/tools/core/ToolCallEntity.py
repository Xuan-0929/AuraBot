"""Entities used while parsing and resolving LLM tool calls."""

from dataclasses import dataclass
from typing import Any, Literal

from ..Tool import Tool


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One parsed tool call in the order returned by the model."""

    name: str
    arguments: dict[str, Any]
    call_id: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedToolCall:
    """A parsed tool call mapped to an authorized runtime tool."""

    tool: Tool
    arguments: dict[str, Any]
    call_id: str | None = None


ToolExecutionErrorCode = Literal[
    "invalid_arguments",
    "invalid_tool_schema",
    "invalid_tool_route",
    "mcp_connection",
    "mcp_initialization",
    "mcp_protocol",
    "mcp_timeout",
    "mcp_tool_call",
    "tool_error",
]


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    """JSON-serializable result of one resolved tool call."""

    tool_name: str
    call_id: str | None
    content: list[dict[str, Any]]
    structured_content: dict[str, Any] | None
    is_error: bool
    error_code: ToolExecutionErrorCode | None = None


__all__ = [
    "ResolvedToolCall",
    "ToolCall",
    "ToolExecutionErrorCode",
    "ToolExecutionResult",
]
