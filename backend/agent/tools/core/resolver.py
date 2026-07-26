"""
负责把模型产生的 ToolCall 解析到 Task 授权快照中的唯一 Tool。

ToolExecutionPipeline 提供当前 ToolGroup 集合，本 resolver 校验名称唯一性与授权并
生成 ResolvedToolCall；它不修改参数、不调用 MCP，也不管理 Task 状态。
"""

from collections.abc import Mapping, Sequence

from ..tool import Tool
from ..tool_group import ToolGroup
from .model import ResolvedToolCall, ToolCall
from .errors import DuplicateToolNameError, ToolNotAuthorizedError


class ToolCallResolver:
    """Map tool calls using a prevalidated authorization index."""

    def __init__(self, tools_by_name: Mapping[str, Tool]):
        self._tools_by_name = dict(tools_by_name)

    @classmethod
    def from_tool_groups(
        cls,
        tool_groups: Sequence[ToolGroup],
    ) -> "ToolCallResolver":
        """Build one resolver and reject ambiguous names atomically."""

        tools_by_name: dict[str, Tool] = {}
        group_by_tool_name: dict[str, str] = {}

        for group in tool_groups:
            for tool in group.tools:
                existing = tools_by_name.get(tool.name)
                if existing is not None:
                    raise DuplicateToolNameError(
                        f"tool {tool.name!r} is exposed by both "
                        f"ToolGroup {group_by_tool_name[tool.name]!r} "
                        f"({existing.server_url!r}) and ToolGroup "
                        f"{group.name!r} ({tool.server_url!r})"
                    )
                tools_by_name[tool.name] = tool
                group_by_tool_name[tool.name] = group.name

        return cls(tools_by_name)

    def resolve(self, tool_calls: Sequence[ToolCall]) -> list[ResolvedToolCall]:
        """Resolve a complete batch while preserving model-returned order."""

        resolved: list[ResolvedToolCall] = []
        for index, call in enumerate(tool_calls):
            tool = self._tools_by_name.get(call.name)
            if tool is None:
                raise ToolNotAuthorizedError(
                    f"tool call at index {index} references a tool that is "
                    f"not present in the authorization snapshot: {call.name!r}"
                )
            resolved.append(
                ResolvedToolCall(
                    tool=tool,
                    arguments=call.arguments,
                    call_id=call.call_id,
                )
            )
        return resolved

__all__ = ["ToolCallResolver"]
