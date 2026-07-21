
"""Exceptions raised while parsing and resolving tool calls."""


class ToolCallParseError(ValueError):
    """Raised when an LLM response declares malformed tool calls."""


class ToolCallResolveError(LookupError):
    """Raised when parsed tool calls cannot be mapped to authorized tools."""


class ToolNotAuthorizedError(ToolCallResolveError):
    """Raised when a tool is absent from the current Task authorization."""


class DuplicateToolNameError(ToolCallResolveError):
    """Raised when Task ToolGroups expose the same tool name more than once."""


__all__ = [
    "DuplicateToolNameError",
    "ToolCallParseError",
    "ToolCallResolveError",
    "ToolNotAuthorizedError",
]
