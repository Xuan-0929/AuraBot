"""Task use cases presented to HTTP and future transports."""

from __future__ import annotations

from typing import Any

from ...agent.task.TaskEntity import InterruptTaskOutcome
from ...agent.task.TaskManager import (
    DuplicateTaskNameError,
    TaskManager,
    TaskManagerClosedError,
)
from ...agent.task.events import TaskEventSubscription
from ...agent.task.runtime import TaskRuntimeRecord, TaskStartSpec
from .errors import ApplicationError


class TaskApplicationService:
    def __init__(self, task_manager: TaskManager):
        if not isinstance(task_manager, TaskManager):
            raise TypeError("task_manager must be a TaskManager")
        self.task_manager = task_manager

    def start(self, spec: TaskStartSpec) -> dict[str, Any]:
        try:
            record = self.task_manager.start_task(spec)
        except DuplicateTaskNameError as exc:
            raise ApplicationError(
                "task_name_conflict",
                "A running or retained Task already uses that name.",
                409,
            ) from exc
        except TaskManagerClosedError as exc:
            raise ApplicationError(
                "runtime_closing",
                "The backend runtime is shutting down.",
                503,
            ) from exc
        return self.task_manager.record_view(record)

    def list(self) -> dict[str, Any]:
        return {
            "running_count": self.task_manager.running_count,
            "queued_count": self.task_manager.queued_count,
            "tasks": [
                self.task_manager.record_view(record)
                for record in self.task_manager.list_records()
            ],
        }

    def get(self, task_name: str) -> dict[str, Any]:
        return self.task_manager.record_view(self._require_record(task_name))

    def interrupt(self, task_name: str) -> dict[str, Any]:
        outcome = self.task_manager.interrupt_task(task_name)
        if outcome.outcome is InterruptTaskOutcome.NotFound:
            raise _task_not_found()
        return self.get(task_name)

    def clear(self, task_name: str) -> None:
        record = self.task_manager.get_record(task_name)
        if record is None:
            raise _task_not_found()
        if not self.task_manager.clear_task(task_name):
            raise ApplicationError(
                "task_not_terminal",
                "Only a settled Task can be removed.",
                409,
            )

    def subscribe(self) -> TaskEventSubscription:
        return self.task_manager.subscribe_events()

    def _require_record(self, task_name: str) -> TaskRuntimeRecord:
        record = self.task_manager.get_record(task_name)
        if record is None:
            raise _task_not_found()
        return record


def _task_not_found() -> ApplicationError:
    return ApplicationError(
        "task_not_found",
        "No running, queued or retained Task has that name.",
        404,
    )


__all__ = ["TaskApplicationService"]
