"""Canonical backend copy of AuraBot's native Plan Tool protocol."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..Tool import Tool
from ..ToolGroup import ToolGroup


PLAN_TOOL_GROUP_NAME = "Plan"
PLAN_TOOL_GROUP_DESCRIPTION = (
    "Maintain the immutable execution plan owned by a Plan-and-Solve Harness."
)
PLAN_CONTEXT_ADAPTOR = "PlanContextAdaptor"
STEP_ID_PATTERN = "^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"

CREATE_PLAN_DESCRIPTION = (
    "为当前 Plan-and-Solve Task 创建唯一的初始执行计划。计划包含一个目标和 1 至 "
    "30 个按执行顺序排列的步骤；每个 Step ID 在计划内必须唯一。所有步骤由系统初始化"
    "为 pending。当前 Task 已有 Plan 时不得覆盖，应先 read_plan，并仅在新证据要求"
    "修改剩余计划时使用 update_plan。"
)
READ_PLAN_DESCRIPTION = (
    "读取当前 Plan-and-Solve Task 的完整最新计划，包括每个步骤的状态、完成摘要、"
    "当前步骤和总体完成情况。本 Tool 不修改计划。"
)
UPDATE_PLAN_DESCRIPTION = (
    "在新证据要求调整执行方案时，提交当前 Plan 的完整目标和完整有序 Step 定义。"
    "已完成 Step 构成不可变前缀，必须保持原 ID、顺序、标题、描述和预期产物；本 Tool "
    "只能修改、删除、重排未完成后缀或加入新的 pending Step，不能直接修改 Step 状态"
    "或完成摘要。调用前应使用已有 Tool result 或 read_plan 获取最新 Plan。"
)
COMPLETE_PLAN_STEP_DESCRIPTION = (
    "在已经观察到当前步骤的真实完成结果后，将该 Step 标记为 completed，并保存简短"
    "的完成摘要。只能完成 read_plan、create_plan 或 update_plan 返回的 "
    "current_step_id，或者最近一次 plan_step_complete 返回的 next_step_id，不能跳过 "
    "pending Step。完成摘要必须描述真实结果、产物或验证，不得只复述计划。"
)


def _object_schema(
    properties: dict[str, Any],
    required: list[str],
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


STEP_DEFINITION_SCHEMA = _object_schema(
    {
        "id": {
            "type": "string",
            "pattern": STEP_ID_PATTERN,
            "description": "当前 Plan 内唯一且生命周期内稳定的 Step ID。",
        },
        "title": {
            "type": "string",
            "minLength": 1,
            "description": "简短、可执行的步骤标题。",
        },
        "description": {
            "type": "string",
            "minLength": 1,
            "description": "该步骤要完成的工作范围和动作。",
        },
        "expected_output": {
            "type": "string",
            "minLength": 1,
            "description": "判断该步骤完成所需的可观察结果或产物。",
        },
    },
    ["id", "title", "description", "expected_output"],
)

CREATE_PLAN_INPUT_SCHEMA = _object_schema(
    {
        "goal": {
            "type": "string",
            "minLength": 1,
            "description": "Plan 要实现的任务目标。",
        },
        "steps": {
            "type": "array",
            "minItems": 1,
            "maxItems": 30,
            "uniqueItems": True,
            "items": STEP_DEFINITION_SCHEMA,
            "description": "按执行顺序排列的初始 Step 列表。",
        },
    },
    ["goal", "steps"],
)
UPDATE_PLAN_INPUT_SCHEMA = _object_schema(
    {
        "goal": {
            "type": "string",
            "minLength": 1,
            "description": "更新后 Plan 的完整任务目标。",
        },
        "steps": {
            "type": "array",
            "minItems": 1,
            "maxItems": 30,
            "uniqueItems": True,
            "items": _object_schema(
                {
                    "id": {
                        "type": "string",
                        "pattern": STEP_ID_PATTERN,
                        "description": (
                            "更新后 Plan 内唯一的 Step ID；已有 Step 必须沿用原 ID。"
                        ),
                    },
                    "title": {
                        "type": "string",
                        "minLength": 1,
                        "description": "更新后的步骤标题。",
                    },
                    "description": {
                        "type": "string",
                        "minLength": 1,
                        "description": "更新后的步骤工作范围和动作。",
                    },
                    "expected_output": {
                        "type": "string",
                        "minLength": 1,
                        "description": "更新后的可观察完成结果或产物。",
                    },
                },
                ["id", "title", "description", "expected_output"],
            ),
            "description": "更新后 Plan 的完整有序 Step 列表，不是局部 patch。",
        },
    },
    ["goal", "steps"],
)
READ_PLAN_INPUT_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

PLAN_STEP_SCHEMA = _object_schema(
    {
        **deepcopy(STEP_DEFINITION_SCHEMA["properties"]),
        "status": {
            "type": "string",
            "enum": ["pending", "completed"],
            "description": (
                "步骤当前状态；只有 plan_step_complete 可以写入 completed。"
            ),
        },
        "completion_summary": {
            "type": ["string", "null"],
            "description": "步骤完成时记录的真实结果摘要；pending 时必须为 null。",
        },
    },
    [
        "id",
        "title",
        "description",
        "expected_output",
        "status",
        "completion_summary",
    ],
)
PLAN_SNAPSHOT_SCHEMA = _object_schema(
    {
        "goal": {
            "type": "string",
            "minLength": 1,
            "description": "当前 Plan 要实现的任务目标。",
        },
        "steps": {
            "type": "array",
            "minItems": 1,
            "maxItems": 30,
            "items": PLAN_STEP_SCHEMA,
            "description": (
                "按执行顺序排列的完整 Step 列表；已完成 Step 必须构成前缀。"
            ),
        },
        "current_step_id": {
            "type": ["string", "null"],
            "pattern": STEP_ID_PATTERN,
            "description": "第一个 pending Step 的 ID；全部完成时为 null。",
        },
        "completed_step_count": {
            "type": "integer",
            "minimum": 0,
            "maximum": 30,
            "description": "steps 中状态为 completed 的 Step 数量。",
        },
        "total_step_count": {
            "type": "integer",
            "minimum": 1,
            "maximum": 30,
            "description": "steps 的总数量。",
        },
        "is_complete": {
            "type": "boolean",
            "description": "是否所有 Step 都已完成。",
        },
    },
    [
        "goal",
        "steps",
        "current_step_id",
        "completed_step_count",
        "total_step_count",
        "is_complete",
    ],
)

CREATE_PLAN_OUTPUT_SCHEMA = _object_schema(
    {
        "created": {
            "type": "boolean",
            "const": True,
            "description": "请求要求的 Plan 已经存在且内容与请求一致。",
        },
        "current_step_id": {
            "type": "string",
            "pattern": STEP_ID_PATTERN,
            "description": "创建后第一个 pending Step 的 ID。",
        },
        "total_step_count": {
            "type": "integer",
            "minimum": 1,
            "maximum": 30,
            "description": "创建后 Plan 的 Step 总数。",
        },
    },
    ["created", "current_step_id", "total_step_count"],
)
UPDATE_PLAN_OUTPUT_SCHEMA = _object_schema(
    {
        "updated": {
            "type": "boolean",
            "const": True,
            "description": "请求提交的完整 Plan 定义已经成为当前状态。",
        },
        "current_step_id": {
            "type": "string",
            "pattern": STEP_ID_PATTERN,
            "description": "更新后第一个 pending Step 的 ID。",
        },
        "total_step_count": {
            "type": "integer",
            "minimum": 1,
            "maximum": 30,
            "description": "更新后 Plan 的 Step 总数。",
        },
    },
    ["updated", "current_step_id", "total_step_count"],
)
COMPLETE_PLAN_STEP_INPUT_SCHEMA = _object_schema(
    {
        "step_id": {
            "type": "string",
            "pattern": STEP_ID_PATTERN,
            "description": (
                "要完成的 Step ID；必须等于最新 PlanSnapshot.current_step_id。"
            ),
        },
        "completion_summary": {
            "type": "string",
            "minLength": 1,
            "description": "基于已观察事实的完成结果、产物或验证摘要。",
        },
    },
    ["step_id", "completion_summary"],
)
COMPLETE_PLAN_STEP_OUTPUT_SCHEMA = _object_schema(
    {
        "completed": {
            "type": "boolean",
            "const": True,
            "description": (
                "指定 Step 已经以请求中的完成摘要标记为 completed。"
            ),
        },
        "completed_step_id": {
            "type": "string",
            "pattern": STEP_ID_PATTERN,
            "description": "本次确认已经完成的 Step ID。",
        },
        "next_step_id": {
            "type": ["string", "null"],
            "pattern": STEP_ID_PATTERN,
            "description": "下一个 pending Step 的 ID；Plan 全部完成时为 null。",
        },
        "is_complete": {
            "type": "boolean",
            "description": "完成该 Step 后，Plan 是否已经全部完成。",
        },
    },
    ["completed", "completed_step_id", "next_step_id", "is_complete"],
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
