"""
定义与具体 Harness 算法无关的工具调用值对象和结果变体。

OpenAI 协议层产生 ToolCall，resolver 转为 ResolvedToolCall，gateway 再返回不可变
执行结果供策略追加消息；本模块只承载数据，不负责解析、授权或网络执行。
"""

from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

from ..tool import Tool


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
