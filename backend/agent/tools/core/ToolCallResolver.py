"""Resolve parsed LLM tool calls against a Task's authorized tool groups."""

from collections.abc import Sequence

from ..Tool import Tool
from ..ToolGroup import ToolGroup
from .ToolCallEntity import ResolvedToolCall, ToolCall
from .ToolExceptions import ToolCallResolveError
from .parser import ParserInput, parse_tool_calls


class ToolCallResolver:
    """Map tool calls to the unique tools exposed by authorized groups."""

    def __init__(self, tool_groups: Sequence[ToolGroup]):
        self._tools_by_name = _build_tool_index(tool_groups)

    def resolve(self, tool_calls: Sequence[ToolCall]) -> list[ResolvedToolCall]:
        """Resolve a complete batch while preserving model-returned order."""

        resolved: list[ResolvedToolCall] = []
        for index, call in enumerate(tool_calls):
            tool = self._tools_by_name.get(call.name)
            if tool is None:
                raise ToolCallResolveError(
                    f"tool call at index {index} references an unauthorized "
                    f"or unknown tool: {call.name!r}"
                )
            resolved.append(
                ResolvedToolCall(
                    tool=tool,
                    arguments=call.arguments,
                    call_id=call.call_id,
                )
            )
        return resolved

    def resolve_message(self, response: ParserInput) -> list[ResolvedToolCall]:
        """Parse and resolve all tool calls in one LLM response."""

        tool_calls = parse_tool_calls(response)
        return self.resolve(tool_calls)


def _build_tool_index(tool_groups: Sequence[ToolGroup]) -> dict[str, Tool]:
    tools_by_name: dict[str, Tool] = {}
    for group in tool_groups:
        for tool in group.tools:
            existing = tools_by_name.get(tool.name)
            if existing is not None:
                raise ToolCallResolveError(
                    f"authorized tool name is ambiguous: {tool.name!r} "
                    f"is provided by {existing.server_url!r} and {tool.server_url!r}"
                )
            tools_by_name[tool.name] = tool
    return tools_by_name


__all__ = ["ToolCallResolver"]
