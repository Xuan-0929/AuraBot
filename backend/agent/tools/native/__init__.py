"""AuraBot project-native ToolGroup definitions."""

from collections.abc import Callable, Sequence

from ..ToolGroup import ToolGroup
from .PlanToolGroup import (
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
