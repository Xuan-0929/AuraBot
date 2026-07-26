"""Native Plan ToolGroup factory."""

from copy import deepcopy

from ..tool import Tool
from ..tool_group import ToolGroup
from .plan_schema import (
    COMPLETE_PLAN_STEP_INPUT_SCHEMA,
    COMPLETE_PLAN_STEP_OUTPUT_SCHEMA,
    CREATE_PLAN_INPUT_SCHEMA,
    CREATE_PLAN_OUTPUT_SCHEMA,
    PLAN_CONTEXT_ADAPTOR,
    PLAN_SNAPSHOT_SCHEMA,
    PLAN_TOOL_GROUP_DESCRIPTION,
    PLAN_TOOL_GROUP_NAME,
    READ_PLAN_INPUT_SCHEMA,
    STEP_ID_PATTERN,
    UPDATE_PLAN_INPUT_SCHEMA,
    UPDATE_PLAN_OUTPUT_SCHEMA,
)
from .plan_protocol import PLAN_TOOL_DEFINITIONS, PLAN_TOOL_NAMES


def create_plan_tool_group(server_url: str | None = None) -> ToolGroup:
    """Build one canonical Plan group without sharing mutable Tool instances."""

    if server_url is None:
        from backend.config import get_config

        configured_url = get_config()["local_mcp_server"]
    else:
        configured_url = server_url

    tools = [
        Tool(
            name=definition["name"],
            description=definition["description"],
            server_url=configured_url,
            input_schema=deepcopy(definition["inputSchema"]),
            output_schema=deepcopy(definition["outputSchema"]),
            adaptor_names=(PLAN_CONTEXT_ADAPTOR,),
        )
        for definition in PLAN_TOOL_DEFINITIONS
    ]
    return ToolGroup(
        PLAN_TOOL_GROUP_NAME,
        tools,
        PLAN_TOOL_GROUP_DESCRIPTION,
    )


__all__ = [
    "COMPLETE_PLAN_STEP_INPUT_SCHEMA",
    "COMPLETE_PLAN_STEP_OUTPUT_SCHEMA",
    "CREATE_PLAN_INPUT_SCHEMA",
    "CREATE_PLAN_OUTPUT_SCHEMA",
    "PLAN_CONTEXT_ADAPTOR",
    "PLAN_SNAPSHOT_SCHEMA",
    "PLAN_TOOL_DEFINITIONS",
    "PLAN_TOOL_GROUP_DESCRIPTION",
    "PLAN_TOOL_GROUP_NAME",
    "PLAN_TOOL_NAMES",
    "READ_PLAN_INPUT_SCHEMA",
    "STEP_ID_PATTERN",
    "UPDATE_PLAN_INPUT_SCHEMA",
    "UPDATE_PLAN_OUTPUT_SCHEMA",
    "create_plan_tool_group",
]
