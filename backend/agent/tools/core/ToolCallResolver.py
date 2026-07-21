"""Resolve parsed LLM tool calls against a Task authorization snapshot."""

from collections.abc import Mapping, Sequence

from ..Tool import Tool
from ..utils.parser import ParserInput, parse_tool_calls
from .ToolCallEntity import ResolvedToolCall, ToolCall
from .ToolExceptions import ToolNotAuthorizedError


class ToolCallResolver:
    """Map tool calls using a prevalidated Task authorization index."""

    def __init__(self, tools_by_name: Mapping[str, Tool]):
        self._tools_by_name = dict(tools_by_name)

    def resolve(self, tool_calls: Sequence[ToolCall]) -> list[ResolvedToolCall]:
        """Resolve a complete batch while preserving model-returned order."""

        resolved: list[ResolvedToolCall] = []
        for index, call in enumerate(tool_calls):
            tool = self._tools_by_name.get(call.name)
            if tool is None:
                raise ToolNotAuthorizedError(
                    f"tool call at index {index} references a tool that is "
                    f"not authorized for this Task: {call.name!r}"
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


__all__ = ["ToolCallResolver"]
