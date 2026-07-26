"""Internal native Plan Tool use cases."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ...agent.task.TaskManager import TaskManager
from ...agent.task.harness.Plan import PlanOperationError, PlanStepDefinition
from ...agent.task.harness.PlanAndSolveHarness import PlanAndSolveHarness
from .errors import ApplicationError

PLAN_ERROR_STATUS = {
    "invalid_plan": 400,
    "task_not_found": 404,
    "plan_not_found": 404,
    "step_not_found": 404,
    "invalid_task_type": 409,
    "invalid_task_state": 409,
    "plan_already_exists": 409,
    "completed_step_immutable": 409,
    "step_not_current": 409,
    "step_already_completed": 409,
}


class PlanToolApplicationService:
    def __init__(self, task_manager: TaskManager):
        if not isinstance(task_manager, TaskManager):
            raise TypeError("task_manager must be a TaskManager")
        self.task_manager = task_manager

    def create(
        self,
        task_name: str,
        goal: str,
        steps: tuple[PlanStepDefinition, ...],
    ) -> dict[str, Any]:
        return self._invoke(
            task_name,
            lambda harness: harness.create_plan(goal, steps),
        )

    def read(self, task_name: str) -> dict[str, Any]:
        return self._invoke(task_name, lambda harness: harness.read_plan())

    def update(
        self,
        task_name: str,
        goal: str,
        steps: tuple[PlanStepDefinition, ...],
    ) -> dict[str, Any]:
        return self._invoke(
            task_name,
            lambda harness: harness.update_plan(goal, steps),
        )

    def complete_step(
        self,
        task_name: str,
        step_id: str,
        completion_summary: str,
    ) -> dict[str, Any]:
        return self._invoke(
            task_name,
            lambda harness: harness.complete_plan_step(
                step_id,
                completion_summary,
            ),
        )

    def _invoke(self, task_name: str, operation) -> dict[str, Any]:
        runtime = self.task_manager.get_runtime(task_name)
        if runtime is None:
            raise ApplicationError(
                "task_not_found",
                "No active or retained Task runtime has that name.",
                404,
            )
        if not isinstance(runtime, PlanAndSolveHarness):
            raise ApplicationError(
                "invalid_task_type",
                "The requested Task runtime is not Plan-and-Solve.",
                409,
            )
        try:
            return asdict(operation(runtime))
        except PlanOperationError as exc:
            raise ApplicationError(
                exc.code,
                exc.message,
                PLAN_ERROR_STATUS[exc.code],
            ) from exc


__all__ = ["PLAN_ERROR_STATUS", "PlanToolApplicationService"]
