"""Immutable Plan domain protocol owned by PlanAndSolveHarness."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal


PlanStepStatus = Literal["pending", "completed"]
STEP_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MIN_PLAN_STEPS = 1
MAX_PLAN_STEPS = 30


PLAN_ERROR_MESSAGES = MappingProxyType({
    "invalid_task_state": "The Task state does not allow this Plan operation.",
    "plan_already_exists": (
        "A different Plan already exists for this Task. Read it before updating."
    ),
    "plan_not_found": "No Plan exists for this Task. Create one first.",
    "invalid_plan": "The submitted Plan violates the Plan invariants.",
    "completed_step_immutable": (
        "Completed Plan steps are immutable and must remain an unchanged prefix."
    ),
    "step_not_found": "The requested Plan step does not exist.",
    "step_not_current": "Only the current pending Plan step can be completed.",
    "step_already_completed": (
        "The Plan step is already completed with a different summary."
    ),
})


class PlanOperationError(RuntimeError):
    """A stable, safe business error returned by Plan operations."""

    def __init__(self, code: str):
        if code not in PLAN_ERROR_MESSAGES:
            raise ValueError(f"unknown Plan error code: {code}")
        self.code = code
        self.message = PLAN_ERROR_MESSAGES[code]
        super().__init__(self.message)


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


def validate_plan_definition(
    goal: str,
    steps: tuple[PlanStepDefinition, ...],
) -> None:
    """Validate constraints that are shared by create and update."""

    if not isinstance(goal, str) or not goal:
        raise PlanOperationError("invalid_plan")
    if not MIN_PLAN_STEPS <= len(steps) <= MAX_PLAN_STEPS:
        raise PlanOperationError("invalid_plan")

    step_ids: set[str] = set()
    for step in steps:
        if not isinstance(step, PlanStepDefinition):
            raise PlanOperationError("invalid_plan")
        if (
            not isinstance(step.id, str)
            or STEP_ID_PATTERN.fullmatch(step.id) is None
            or step.id in step_ids
            or not isinstance(step.title, str)
            or not step.title
            or not isinstance(step.description, str)
            or not step.description
            or not isinstance(step.expected_output, str)
            or not step.expected_output
        ):
            raise PlanOperationError("invalid_plan")
        step_ids.add(step.id)


def build_snapshot(
    goal: str,
    steps: tuple[PlanStep, ...],
) -> PlanSnapshot:
    """Derive all aggregate fields from one immutable ordered step tuple."""

    completed_count = 0
    current_step_id: str | None = None
    pending_seen = False
    for step in steps:
        if step.status == "completed":
            if pending_seen or not step.completion_summary:
                raise PlanOperationError("invalid_plan")
            completed_count += 1
        elif step.status == "pending":
            if step.completion_summary is not None:
                raise PlanOperationError("invalid_plan")
            pending_seen = True
            if current_step_id is None:
                current_step_id = step.id
        else:
            raise PlanOperationError("invalid_plan")

    return PlanSnapshot(
        goal=goal,
        steps=steps,
        current_step_id=current_step_id,
        completed_step_count=completed_count,
        total_step_count=len(steps),
        is_complete=current_step_id is None,
    )


__all__ = [
    "CompletePlanStepResult",
    "CreatePlanResult",
    "MAX_PLAN_STEPS",
    "MIN_PLAN_STEPS",
    "PLAN_ERROR_MESSAGES",
    "PlanOperationError",
    "PlanSnapshot",
    "PlanStep",
    "PlanStepDefinition",
    "PlanStepStatus",
    "STEP_ID_PATTERN",
    "UpdatePlanResult",
    "build_snapshot",
    "validate_plan_definition",
]
