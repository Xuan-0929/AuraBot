"""
提供 Plan 子领域的稳定公开入口，聚合不可变值对象、聚合行为和 task-local service。

Harness 与应用服务只从本包消费 Plan 契约；具体实现留在相邻模块，本包不负责
Task 调度、事件传输、FastAPI 路由或 MCP schema。
"""

from .aggregate import (
    MAX_PLAN_STEPS,
    MIN_PLAN_STEPS,
    STEP_ID_PATTERN,
    Plan,
    build_snapshot,
    validate_plan_definition,
)
from .errors import PLAN_ERROR_MESSAGES, PlanOperationError
from .harness_protocol import PlanningHarness
from .models import (
    CompletePlanStepResult,
    CreatePlanResult,
    PlanSnapshot,
    PlanStep,
    PlanStepDefinition,
    PlanStepStatus,
    UpdatePlanResult,
)
from .response import (
    PlanAndSolveFinalResponseError,
    PlanFinalStatus,
    parse_plan_final_status,
)
from .service import PlanningRuntime

__all__ = [
    "CompletePlanStepResult",
    "CreatePlanResult",
    "MAX_PLAN_STEPS",
    "MIN_PLAN_STEPS",
    "PLAN_ERROR_MESSAGES",
    "Plan",
    "PlanAndSolveFinalResponseError",
    "PlanFinalStatus",
    "PlanOperationError",
    "PlanSnapshot",
    "PlanStep",
    "PlanStepDefinition",
    "PlanStepStatus",
    "PlanningHarness",
    "PlanningRuntime",
    "STEP_ID_PATTERN",
    "UpdatePlanResult",
    "build_snapshot",
    "parse_plan_final_status",
    "validate_plan_definition",
]
