
"""Exceptions raised while parsing and resolving tool calls."""

from typing import Literal


ToolCallParseErrorCode = Literal[
    "invalid_json",
    "invalid_envelope",
    "invalid_final",
    "invalid_calls",
    "invalid_call",
]


class ToolCallParseError(ValueError):
    """Raised when model text violates the canonical tool protocol."""

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
