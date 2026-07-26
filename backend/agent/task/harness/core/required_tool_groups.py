"""
负责把 Harness 强制依赖的 canonical ToolGroup 安装到 Task 授权快照。

factory 构造策略时调用本模块校验保留名称、去重并原子更新 Task resolver；它不执行
工具调用、不创建 ToolGroup，也不管理 Task runtime。
"""

from collections.abc import Sequence

from ....tools.tool import Tool
from ....tools.tool_group import ToolGroup
from ...domain import Task


def inject_required_tool_groups(
    task: Task,
    required_tool_groups: Sequence[ToolGroup],
) -> None:
    """Atomically prepend canonical Harness-required ToolGroups."""

    required = tuple(required_tool_groups)
    if any(not isinstance(group, ToolGroup) for group in required):
        raise TypeError("required_tool_groups must contain ToolGroup values")

    required_names = tuple(group.name for group in required)
    if len(set(required_names)) != len(required_names):
        raise ValueError("required ToolGroups must have unique group names")

    installed_required: list[ToolGroup] = []
    matched_group_ids: set[int] = set()
    for canonical_group in required:
        matching_groups = tuple(
            group
            for group in task.tool_groups
            if group.name == canonical_group.name
        )
        if len(matching_groups) > 1:
            raise ValueError(
                "multiple ToolGroups use reserved name "
                f"{canonical_group.name!r}"
            )
        if matching_groups:
            existing = matching_groups[0]
            if not _tool_groups_equal(existing, canonical_group):
                raise ValueError(
                    f"ToolGroup {canonical_group.name!r} conflicts with "
                    "the canonical required ToolGroup"
                )
            installed_required.append(existing)
            matched_group_ids.add(id(existing))
        else:
            installed_required.append(canonical_group)

    ordinary_groups = tuple(
        group
        for group in task.tool_groups
        if id(group) not in matched_group_ids
    )
    candidate = (*installed_required, *ordinary_groups)
    if len(candidate) == len(task.tool_groups) and all(
        candidate_group is existing_group
        for candidate_group, existing_group in zip(
            candidate,
            task.tool_groups,
        )
    ):
        return
    task.update_tool_groups(candidate)


def _tool_groups_equal(left: ToolGroup, right: ToolGroup) -> bool:
    return (
        left.name == right.name
        and left.description == right.description
        and len(left.tools) == len(right.tools)
        and all(
            _tools_equal(left_tool, right_tool)
            for left_tool, right_tool in zip(left.tools, right.tools)
        )
    )


def _tools_equal(left: Tool, right: Tool) -> bool:
    return (
        left.name == right.name
        and left.description == right.description
        and left.server_url == right.server_url
        and left.input_schema == right.input_schema
        and left.output_schema == right.output_schema
        and left.adaptor_names == right.adaptor_names
    )


__all__ = ["inject_required_tool_groups"]
