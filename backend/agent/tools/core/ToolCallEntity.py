"""Harness-neutral entities for parsing and executing tool calls."""

from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

from ..Tool import Tool


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One parsed tool call carrying its provider-assigned ID."""

    name: str
    arguments: dict[str, Any]
    call_id: str


@dataclass(frozen=True, slots=True)
class ToolCallBatch:
    """One non-empty, atomically parsed batch of ordered tool calls."""

    calls: tuple[ToolCall, ...]


@dataclass(frozen=True, slots=True)
class FinalResponse:
    """A completed assistant response selected by finish_reason."""

    content: str


ToolProtocolResponse: TypeAlias = ToolCallBatch | FinalResponse


@dataclass(frozen=True, slots=True)
class ResolvedToolCall:
    """A parsed tool call mapped to an authorization snapshot."""

    tool: Tool
    arguments: dict[str, Any]
    call_id: str
    mcp_meta: dict[str, Any] = field(default_factory=dict)


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
    call_id: str
    content: list[dict[str, Any]]
    structured_content: dict[str, Any] | None
    is_error: bool
    error_code: ToolExecutionErrorCode | None = None


__all__ = [
    "FinalResponse",
    "ResolvedToolCall",
    "ToolCall",
    "ToolCallBatch",
    "ToolExecutionErrorCode",
    "ToolExecutionResult",
    "ToolProtocolResponse",
]
