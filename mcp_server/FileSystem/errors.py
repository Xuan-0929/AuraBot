"""Stable, client-safe FileSystem tool errors."""

from __future__ import annotations


class ToolFailure(Exception):
    """An expected tool failure safe to expose to MCP clients."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


__all__ = ["ToolFailure"]
