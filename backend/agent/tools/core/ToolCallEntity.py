"""Entities used while parsing and resolving LLM tool calls."""

from dataclasses import dataclass
from typing import Any

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


__all__ = ["ResolvedToolCall", "ToolCall"]
