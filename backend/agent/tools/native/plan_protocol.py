"""
把 Plan 工具名称、说明和 JSON schema 组合为稳定的 native Tool 声明。

plan ToolGroup factory 消费这里的不可变协议数据，Harness 将其编码到模型请求；
本模块不调用 PlanningRuntime，也不负责 Backend/MCP schema 一致性判断。
"""

from .plan_descriptions import (
    COMPLETE_PLAN_STEP_DESCRIPTION,
    CREATE_PLAN_DESCRIPTION,
    READ_PLAN_DESCRIPTION,
    UPDATE_PLAN_DESCRIPTION,
)
from .plan_schema import (
    COMPLETE_PLAN_STEP_INPUT_SCHEMA,
    COMPLETE_PLAN_STEP_OUTPUT_SCHEMA,
    CREATE_PLAN_INPUT_SCHEMA,
    CREATE_PLAN_OUTPUT_SCHEMA,
    PLAN_SNAPSHOT_SCHEMA,
    READ_PLAN_INPUT_SCHEMA,
    UPDATE_PLAN_INPUT_SCHEMA,
    UPDATE_PLAN_OUTPUT_SCHEMA,
)

PLAN_TOOL_DEFINITIONS = (
    {
        "name": "create_plan",
        "description": CREATE_PLAN_DESCRIPTION,
        "inputSchema": CREATE_PLAN_INPUT_SCHEMA,
        "outputSchema": CREATE_PLAN_OUTPUT_SCHEMA,
    },
    {
        "name": "read_plan",
        "description": READ_PLAN_DESCRIPTION,
        "inputSchema": READ_PLAN_INPUT_SCHEMA,
        "outputSchema": PLAN_SNAPSHOT_SCHEMA,
    },
    {
        "name": "update_plan",
        "description": UPDATE_PLAN_DESCRIPTION,
        "inputSchema": UPDATE_PLAN_INPUT_SCHEMA,
        "outputSchema": UPDATE_PLAN_OUTPUT_SCHEMA,
    },
    {
        "name": "plan_step_complete",
        "description": COMPLETE_PLAN_STEP_DESCRIPTION,
        "inputSchema": COMPLETE_PLAN_STEP_INPUT_SCHEMA,
        "outputSchema": COMPLETE_PLAN_STEP_OUTPUT_SCHEMA,
    },
)
PLAN_TOOL_NAMES = tuple(
    definition["name"] for definition in PLAN_TOOL_DEFINITIONS
)


__all__ = ["PLAN_TOOL_DEFINITIONS", "PLAN_TOOL_NAMES"]
