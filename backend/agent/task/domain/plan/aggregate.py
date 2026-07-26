"""
负责 Plan 聚合的不变量校验、不可变快照构造与有序步骤生命周期。

领域 service 和 Harness 通过 create、update、complete_step 推进唯一当前步骤，结果以
models 中的 frozen value object 返回；本模块不绑定 Task 状态、不调度 Harness 或处理传输。
"""

from __future__ import annotations

import re
from typing import Sequence

from .errors import PLAN_ERROR_MESSAGES, PlanOperationError
from .models import (
    CompletePlanStepResult,
    CreatePlanResult,
    PlanSnapshot,
    PlanStep,
    PlanStepDefinition,
    PlanStepStatus,
    UpdatePlanResult,
)

STEP_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MIN_PLAN_STEPS = 1
MAX_PLAN_STEPS = 30


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


class Plan:
    """Behavior-rich aggregate for one ordered, immutable Plan snapshot."""

    __slots__ = ("_snapshot",)

    def __init__(self, snapshot: PlanSnapshot):
        if not isinstance(snapshot, PlanSnapshot):
            raise TypeError("snapshot must be a PlanSnapshot")
        self._snapshot = snapshot

    @classmethod
    def create(
        cls,
        goal: str,
        steps: Sequence[PlanStepDefinition],
    ) -> Plan:
        definitions = _normalize_definitions(steps)
        validate_plan_definition(goal, definitions)
        return cls(
            build_snapshot(
                goal,
                tuple(
                    PlanStep(
                        id=step.id,
                        title=step.title,
                        description=step.description,
                        expected_output=step.expected_output,
                        status="pending",
                        completion_summary=None,
                    )
                    for step in definitions
                ),
            )
        )

    @property
    def snapshot(self) -> PlanSnapshot:
        return self._snapshot

    def update(
        self,
        goal: str,
        steps: Sequence[PlanStepDefinition],
    ) -> UpdatePlanResult:
        definitions = _normalize_definitions(steps)
        validate_plan_definition(goal, definitions)
        current = self._snapshot
        if current.is_complete:
            raise PlanOperationError("invalid_task_state")

        completed_count = current.completed_step_count
        completed_definitions = tuple(
            step.definition for step in current.steps[:completed_count]
        )
        if (
            len(definitions) < completed_count
            or definitions[:completed_count] != completed_definitions
        ):
            raise PlanOperationError("completed_step_immutable")
        if len(definitions) == completed_count:
            raise PlanOperationError("invalid_plan")

        completed_steps = current.steps[:completed_count]
        pending_steps = tuple(
            PlanStep(
                id=step.id,
                title=step.title,
                description=step.description,
                expected_output=step.expected_output,
                status="pending",
                completion_summary=None,
            )
            for step in definitions[completed_count:]
        )
        self._snapshot = build_snapshot(
            goal,
            (*completed_steps, *pending_steps),
        )
        return UpdatePlanResult(
            current_step_id=_require_current_step_id(self._snapshot),
            total_step_count=self._snapshot.total_step_count,
        )

    def complete_step(
        self,
        step_id: str,
        completion_summary: str,
    ) -> CompletePlanStepResult:
        if (
            not isinstance(step_id, str)
            or not isinstance(completion_summary, str)
            or not completion_summary
        ):
            raise PlanOperationError("invalid_plan")

        current = self._snapshot
        matching_index = next(
            (
                index
                for index, step in enumerate(current.steps)
                if step.id == step_id
            ),
            None,
        )
        if matching_index is None:
            raise PlanOperationError("step_not_found")
        matching_step = current.steps[matching_index]
        if matching_step.status == "completed":
            if matching_step.completion_summary != completion_summary:
                raise PlanOperationError("step_already_completed")
            return CompletePlanStepResult(
                completed_step_id=step_id,
                next_step_id=current.current_step_id,
                is_complete=current.is_complete,
            )
        if step_id != current.current_step_id:
            raise PlanOperationError("step_not_current")

        completed_step = PlanStep(
            id=matching_step.id,
            title=matching_step.title,
            description=matching_step.description,
            expected_output=matching_step.expected_output,
            status="completed",
            completion_summary=completion_summary,
        )
        self._snapshot = build_snapshot(
            current.goal,
            (
                *current.steps[:matching_index],
                completed_step,
                *current.steps[matching_index + 1 :],
            ),
        )
        return CompletePlanStepResult(
            completed_step_id=step_id,
            next_step_id=self._snapshot.current_step_id,
            is_complete=self._snapshot.is_complete,
        )


def _normalize_definitions(
    steps: Sequence[PlanStepDefinition],
) -> tuple[PlanStepDefinition, ...]:
    try:
        return tuple(steps)
    except TypeError as exc:
        raise PlanOperationError("invalid_plan") from exc


def _require_current_step_id(plan: PlanSnapshot) -> str:
    if plan.current_step_id is None:
        raise PlanOperationError("invalid_task_state")
    return plan.current_step_id


__all__ = [
    "CompletePlanStepResult",
    "CreatePlanResult",
    "MAX_PLAN_STEPS",
    "MIN_PLAN_STEPS",
    "PLAN_ERROR_MESSAGES",
    "Plan",
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
