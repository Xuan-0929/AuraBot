"""
负责对排队中或运行中的 Task 执行幂等中断。

controller 从 registry 定位 record，协调 scheduler 取消、Task 状态迁移、事件发布和
retention；它不解析 HTTP 请求，也不决定 Harness 的内部退出算法。
"""

from datetime import UTC, datetime

from ...domain import TaskState
from .retention import TaskRetentionController
from ..records import InterruptTaskOutcome, InterruptTaskResult
from ..registry import TaskRuntimeRegistry
from ..scheduler import TaskScheduler
from ..event_publisher import TaskEventPublisher


class TaskInterruptionController:
    def __init__(
        self,
        *,
        registry: TaskRuntimeRegistry,
        scheduler: TaskScheduler,
        retention: TaskRetentionController,
        events: TaskEventPublisher,
    ):
        self._registry = registry
        self._scheduler = scheduler
        self._retention = retention
        self._events = events

    def interrupt(self, task_name: str) -> InterruptTaskResult:
        record = self._registry.get(task_name)
        if record is None:
            return InterruptTaskResult(
                task_name=task_name,
                outcome=InterruptTaskOutcome.NotFound,
                task_id=None,
                previous_state=None,
                state=None,
            )
        task = record.task
        was_queued = self._scheduler.remove_queued(record)
        previous_state, current_state = task.interrupt()
        if current_state is TaskState.Interrupted:
            self._scheduler.cancel(record)
        if previous_state is TaskState.Interrupted:
            outcome = InterruptTaskOutcome.AlreadyInterrupted
        elif current_state is TaskState.Interrupted:
            outcome = InterruptTaskOutcome.Interrupted
        else:
            outcome = InterruptTaskOutcome.AlreadySettled

        if was_queued and current_state is TaskState.Interrupted:
            record.settled_at = datetime.now(UTC)
            self._events.publish("task.settled", record)
            self._events.publish_queue()
            self._retention.schedule(record)
            self._scheduler.drain()
        return InterruptTaskResult(
            task_name=task_name,
            outcome=outcome,
            task_id=task.task_id,
            previous_state=previous_state,
            state=current_state,
        )


__all__ = ["TaskInterruptionController"]
