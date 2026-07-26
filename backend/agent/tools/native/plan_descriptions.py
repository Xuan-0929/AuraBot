"""
集中保存 native Plan tools 面向模型的组、工具与字段说明文本。

plan_protocol 将这些静态文案与 schema 组合成 Tool 声明，Harness 再编码给 LLM；
本模块不定义 JSON schema、不执行 Plan 操作，也不处理错误转换。
"""

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


__all__ = [
    "COMPLETE_PLAN_STEP_DESCRIPTION",
    "CREATE_PLAN_DESCRIPTION",
    "READ_PLAN_DESCRIPTION",
    "UPDATE_PLAN_DESCRIPTION",
]
