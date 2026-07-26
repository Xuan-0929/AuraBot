"""Internal native Plan Tool use cases."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ...agent.task.manager import TaskManager
from ...agent.task.model import TaskType
from ...agent.task.harness.plan import PlanOperationError, PlanStepDefinition
from ...agent.task.harness.planning import PlanningHarness
from .errors import ApplicationError


class PlanToolApplicationService:
    def __init__(self, task_manager: TaskManager):
        if not callable(getattr(task_manager, "get_runtime", None)):
            raise TypeError("task_manager must provide get_runtime()")
        self.task_manager = task_manager

    def create(
        self,
        task_name: str,
        goal: str,
        steps: tuple[PlanStepDefinition, ...],
    ) -> dict[str, Any]:
        return self._invoke(
            task_name,
            lambda runtime: runtime.create(goal, steps),
        )

    def read(self, task_name: str) -> dict[str, Any]:
        return self._invoke(task_name, lambda runtime: runtime.read())

    def update(
        self,
        task_name: str,
        goal: str,
        steps: tuple[PlanStepDefinition, ...],
    ) -> dict[str, Any]:
        return self._invoke(
            task_name,
            lambda runtime: runtime.update(goal, steps),
        )

    def complete_step(
        self,
        task_name: str,
        step_id: str,
        completion_summary: str,
    ) -> dict[str, Any]:
        return self._invoke(
            task_name,
            lambda runtime: runtime.complete_step(
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
            )
        if (
            runtime.task.task_type is not TaskType.PlanAndSolve
            or not isinstance(runtime, PlanningHarness)
        ):
            raise ApplicationError(
                "invalid_task_type",
                "The requested Task runtime is not Plan-and-Solve.",
            )
        try:
            return asdict(operation(runtime.planning_runtime))
        except PlanOperationError as exc:
            raise ApplicationError(
                exc.code,
                exc.message,
            ) from exc


__all__ = ["PlanToolApplicationService"]
