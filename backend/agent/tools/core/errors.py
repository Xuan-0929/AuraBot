"""
定义工具协议解析、授权解析与执行边界使用的稳定错误类型。

OpenAIChatToolProtocol、ToolCallResolver 与 gateway 在各自层抛出这些语义错误，
上层 Harness 再归一化为工具结果；本模块不解析请求，也不执行任何工具。
"""

"""Exceptions raised while parsing and resolving tool calls."""

from typing import Literal


ToolCallParseErrorCode = Literal[
    "invalid_finish_reason",
    "invalid_message",
    "invalid_tool_calls",
    "invalid_call",
    "invalid_arguments",
    "incomplete_completion",
    "content_filtered",
]


class ToolCallParseError(ValueError):
    """Raised when an OpenAI Chat completion violates the tool protocol."""

    def __init__(self, code: ToolCallParseErrorCode, message: str):
        super().__init__(message)
        self.code = code


class ToolCallResolveError(LookupError):
    """Raised when parsed calls cannot be mapped to an authorization snapshot."""


class ToolNotAuthorizedError(ToolCallResolveError):
    """Raised when a tool is absent from the current authorization snapshot."""


class DuplicateToolNameError(ToolCallResolveError):
    """Raised when ToolGroups expose the same tool name more than once."""


__all__ = [
    "DuplicateToolNameError",
    "ToolCallParseError",
    "ToolCallParseErrorCode",
    "ToolCallResolveError",
    "ToolNotAuthorizedError",
]
