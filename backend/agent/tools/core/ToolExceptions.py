
"""Exceptions raised while parsing and resolving tool calls."""


class ToolCallParseError(ValueError):
    """Raised when an LLM response declares malformed tool calls."""


class ToolCallResolveError(LookupError):
    """Raised when parsed tool calls cannot be mapped to authorized tools."""


__all__ = ["ToolCallParseError", "ToolCallResolveError"]
