"""
提供 AuraBot 进程内声明的 canonical native ToolGroup 工厂。

Harness core 根据策略的 required group 名称从这里取得稳定声明并注入 Task 授权快照；
本包不执行工具、不持有 Task，也不替代 MCP Server 侧实现。
"""

from collections.abc import Callable, Sequence

from ..tool_group import ToolGroup
from .plan import (
    PLAN_TOOL_DEFINITIONS,
    PLAN_TOOL_GROUP_NAME,
    PLAN_TOOL_NAMES,
    create_plan_tool_group,
)


NATIVE_TOOL_GROUP_FACTORIES: dict[str, Callable[[], ToolGroup]] = {
    PLAN_TOOL_GROUP_NAME: create_plan_tool_group,
}


def create_native_tool_groups(
    names: Sequence[str],
) -> tuple[ToolGroup, ...]:
    """Build canonical native groups for standalone Harness construction."""

    try:
        return tuple(NATIVE_TOOL_GROUP_FACTORIES[name]() for name in names)
    except KeyError as exc:
        raise ValueError(
            f"No native ToolGroup registered with name: {exc.args[0]!r}"
        ) from exc


__all__ = [
    "NATIVE_TOOL_GROUP_FACTORIES",
    "PLAN_TOOL_DEFINITIONS",
    "PLAN_TOOL_GROUP_NAME",
    "PLAN_TOOL_NAMES",
    "create_native_tool_groups",
    "create_plan_tool_group",
]
