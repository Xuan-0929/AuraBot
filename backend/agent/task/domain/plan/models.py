"""
负责定义 Plan 聚合跨领域与应用边界流动的不可变 value object 和 operation result。

Plan aggregate 构造 frozen、slots dataclass 快照，Harness 与应用服务只读取这些值；
本模块不执行 Plan 状态转换、不绑定 Task 生命周期，也不定义传输 schema。
"""

from dataclasses import dataclass, field
from typing import Literal


PlanStepStatus = Literal["pending", "completed"]


@dataclass(frozen=True, slots=True)
class PlanStepDefinition:
    id: str
    title: str
    description: str
    expected_output: str


@dataclass(frozen=True, slots=True)
class PlanStep:
    id: str
    title: str
    description: str
    expected_output: str
    status: PlanStepStatus
    completion_summary: str | None

    @property
    def definition(self) -> PlanStepDefinition:
        return PlanStepDefinition(
            id=self.id,
            title=self.title,
            description=self.description,
            expected_output=self.expected_output,
        )


@dataclass(frozen=True, slots=True)
class PlanSnapshot:
    goal: str
    steps: tuple[PlanStep, ...]
    current_step_id: str | None
    completed_step_count: int
    total_step_count: int
    is_complete: bool


@dataclass(frozen=True, slots=True)
class CreatePlanResult:
    current_step_id: str
    total_step_count: int
    created: bool = field(default=True, init=False)


@dataclass(frozen=True, slots=True)
class UpdatePlanResult:
    current_step_id: str
    total_step_count: int
    updated: bool = field(default=True, init=False)


@dataclass(frozen=True, slots=True)
class CompletePlanStepResult:
    completed_step_id: str
    next_step_id: str | None
    is_complete: bool
    completed: bool = field(default=True, init=False)


__all__ = [
    "CompletePlanStepResult",
    "CreatePlanResult",
    "PlanSnapshot",
    "PlanStep",
    "PlanStepDefinition",
    "PlanStepStatus",
    "UpdatePlanResult",
]
