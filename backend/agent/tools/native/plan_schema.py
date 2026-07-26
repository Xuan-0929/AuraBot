"""
保存 Backend 进程使用的 native Plan tool JSON schema 副本。

plan_protocol 把这些静态 schema 绑定到工具声明，schema parity 测试再与 MCP Server
副本逐项比较；本模块不共享跨进程类，也不执行业务校验或 Plan 操作。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

PLAN_TOOL_GROUP_NAME = "Plan"
PLAN_TOOL_GROUP_DESCRIPTION = (
    "Maintain the immutable execution plan owned by a Plan-and-Solve Harness."
)
PLAN_CONTEXT_ADAPTOR = "PlanContextAdaptor"
STEP_ID_PATTERN = "^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"

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

__all__ = [
    "COMPLETE_PLAN_STEP_INPUT_SCHEMA",
    "COMPLETE_PLAN_STEP_OUTPUT_SCHEMA",
    "CREATE_PLAN_INPUT_SCHEMA",
    "CREATE_PLAN_OUTPUT_SCHEMA",
    "PLAN_CONTEXT_ADAPTOR",
    "PLAN_SNAPSHOT_SCHEMA",
    "PLAN_TOOL_GROUP_DESCRIPTION",
    "PLAN_TOOL_GROUP_NAME",
    "READ_PLAN_INPUT_SCHEMA",
    "STEP_ID_PATTERN",
    "UPDATE_PLAN_INPUT_SCHEMA",
    "UPDATE_PLAN_OUTPUT_SCHEMA",
]
