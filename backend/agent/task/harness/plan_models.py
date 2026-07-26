"""Immutable values exposed by the Plan aggregate."""

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
