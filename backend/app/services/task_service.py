"""
负责向 HTTP gateway 与未来 transport 提供稳定的 Task application use cases。

service 把传输层输入交给 TaskManager，并把准入、中断和缺失错误转换为 ApplicationError；
它只返回公开 snapshot/订阅 DTO，不接触内部 runtime record，也不实现 HTTP 编码。
"""

from __future__ import annotations

from ...agent.task import (
    DuplicateTaskNameError,
    InterruptTaskOutcome,
    TaskListSnapshot,
    TaskManager,
    TaskManagerClosedError,
    TaskSnapshot,
    TaskStartSpec,
)
from ...agent.task.events import TaskEventSubscription
from .errors import ApplicationError


class TaskApplicationService:
    def __init__(self, task_manager: TaskManager):
        if not isinstance(task_manager, TaskManager):
            raise TypeError("task_manager must be a TaskManager")
        self.task_manager = task_manager

    def start(self, spec: TaskStartSpec) -> TaskSnapshot:
        try:
            return self.task_manager.start_task(spec)
        except DuplicateTaskNameError as exc:
            raise ApplicationError(
                "task_name_conflict",
                "A running or retained Task already uses that name.",
            ) from exc
        except TaskManagerClosedError as exc:
            raise ApplicationError(
                "runtime_closing",
                "The backend runtime is shutting down.",
            ) from exc

    def list(self) -> TaskListSnapshot:
        return self.task_manager.list_tasks()

    def get(self, task_name: str) -> TaskSnapshot:
        return self._require_snapshot(task_name)

    def interrupt(self, task_name: str) -> TaskSnapshot:
        outcome = self.task_manager.interrupt_task(task_name)
        if outcome.outcome is InterruptTaskOutcome.NotFound:
            raise _task_not_found()
        return self.get(task_name)

    def clear(self, task_name: str) -> None:
        if self.task_manager.get_task(task_name) is None:
            raise _task_not_found()
        if not self.task_manager.clear_task(task_name):
            raise ApplicationError(
                "task_not_terminal",
                "Only a settled Task can be removed.",
            )

    def subscribe(self) -> TaskEventSubscription:
        return self.task_manager.subscribe_events()

    def _require_snapshot(self, task_name: str) -> TaskSnapshot:
        snapshot = self.task_manager.get_task(task_name)
        if snapshot is None:
            raise _task_not_found()
        return snapshot


def _task_not_found() -> ApplicationError:
    return ApplicationError(
        "task_not_found",
        "No running, queued or retained Task has that name.",
    )


__all__ = ["TaskApplicationService"]
