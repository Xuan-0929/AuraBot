"""Task-local planning runtime and the narrow Harness protocol it exposes."""

from __future__ import annotations

from collections.abc import Sequence
from threading import RLock
from typing import Protocol, runtime_checkable

from ..Task import Task, TaskInactiveError
from .Plan import (
    CompletePlanStepResult,
    CreatePlanResult,
    Plan,
    PlanOperationError,
    PlanSnapshot,
    PlanStepDefinition,
    UpdatePlanResult,
)


class PlanningRuntime:
    """Coordinate a Plan aggregate with its owning Task lifecycle."""

    def __init__(self, task: Task):
        if not isinstance(task, Task):
            raise TypeError("task must be a Task")
        self._task = task
        self._plan: Plan | None = None
        self._lock = RLock()

    @property
    def plan(self) -> PlanSnapshot | None:
        with self._lock:
            return None if self._plan is None else self._plan.snapshot

    def create(
        self,
        goal: str,
        steps: Sequence[PlanStepDefinition],
    ) -> CreatePlanResult:
        definitions = _normalize_definitions(steps)
        try:
            with self._task.active_state_guard(), self._lock:
                if self._plan is not None:
                    snapshot = self._plan.snapshot
                    if snapshot.is_complete:
                        raise PlanOperationError("invalid_task_state")
                    if (
                        snapshot.goal != goal
                        or tuple(
                            step.definition for step in snapshot.steps
                        ) != definitions
                    ):
                        raise PlanOperationError("plan_already_exists")
                    return CreatePlanResult(
                        current_step_id=_require_current_step_id(snapshot),
                        total_step_count=snapshot.total_step_count,
                    )

                self._plan = Plan.create(goal, definitions)
                snapshot = self._plan.snapshot
                return CreatePlanResult(
                    current_step_id=_require_current_step_id(snapshot),
                    total_step_count=snapshot.total_step_count,
                )
        except TaskInactiveError as exc:
            raise PlanOperationError("invalid_task_state") from exc

    def read(self) -> PlanSnapshot:
        try:
            with self._task.active_state_guard(), self._lock:
                if self._plan is None:
                    raise PlanOperationError("plan_not_found")
                return self._plan.snapshot
        except TaskInactiveError as exc:
            raise PlanOperationError("invalid_task_state") from exc

    def update(
        self,
        goal: str,
        steps: Sequence[PlanStepDefinition],
    ) -> UpdatePlanResult:
        definitions = _normalize_definitions(steps)
        try:
            with self._task.active_state_guard(), self._lock:
                if self._plan is None:
                    raise PlanOperationError("plan_not_found")
                return self._plan.update(goal, definitions)
        except TaskInactiveError as exc:
            raise PlanOperationError("invalid_task_state") from exc

    def complete_step(
        self,
        step_id: str,
        completion_summary: str,
    ) -> CompletePlanStepResult:
        try:
            with self._task.active_state_guard(), self._lock:
                if self._plan is None:
                    raise PlanOperationError("plan_not_found")
                return self._plan.complete_step(step_id, completion_summary)
        except TaskInactiveError as exc:
            raise PlanOperationError("invalid_task_state") from exc


@runtime_checkable
class PlanningHarness(Protocol):
    """Specialized capability contract implemented by planning Harnesses."""

    @property
    def task(self) -> Task:
        ...

    @property
    def planning_runtime(self) -> PlanningRuntime:
        ...


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


__all__ = ["PlanningHarness", "PlanningRuntime"]
