"""
负责把 Task 的工作目录、约束、SkillGroup 与 ToolGroup 格式化为稳定提示词字段。

ReAct 和 Plan-and-Solve Harness 在渲染模板前读取这些纯函数输出；本模块只消费
Task 领域只读视图，不修改授权、不执行工具调用，也不管理 runtime。
"""

from __future__ import annotations

from collections.abc import Mapping

from ....tools.tool_group import ToolGroup
from ...domain import Task


_COMMON_PROMPT_FIELDS = frozenset(
    {"workdir", "constraints", "skill_groups", "tool_groups"}
)


def format_optional(value: object | None) -> str:
    """Format an optional scalar for a plain-text prompt field."""

    if value is None or value == "":
        return "Not specified."
    return str(value)


def format_skill_groups(task: Task) -> str:
    """Format all authorized SkillGroups for a Harness prompt."""

    descriptions = "\n".join(
        group.list_skills_descriptions() for group in task.skill_groups
    )
    return descriptions or "None provided."


def format_tool_group_summary(group: ToolGroup) -> str:
    """Format authorization context without duplicating function schemas."""

    tool_names = ", ".join(tool.name for tool in group.tools) or "none"
    return f"- {group.name}: {group.description} (tools: {tool_names})"


def format_tool_groups(task: Task) -> str:
    """Format all authorized ToolGroups for a Harness prompt."""

    summaries = "\n".join(
        format_tool_group_summary(group) for group in task.tool_groups
    )
    return summaries or "None provided."


def build_common_prompt_values(task: Task) -> dict[str, str]:
    """Build the prompt fields shared by every Task Harness."""

    return {
        "workdir": format_optional(task.workdir),
        "constraints": format_optional(task.constraints),
        "skill_groups": format_skill_groups(task),
        "tool_groups": format_tool_groups(task),
    }



__all__ = [
    "build_common_prompt_values",
    "format_optional",
    "format_skill_groups",
    "format_tool_group_summary",
    "format_tool_groups",
]
