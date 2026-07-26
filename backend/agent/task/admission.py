"""
负责 Task 名称预留、启动参数归一化与 runtime 准入。

TaskManager 提交 TaskStartSpec 后，本模块创建领域 Task 与 runtime record，再交给
registry、scheduler 和事件发布器；本模块不运行 Harness，也不负责保留期清理。
"""

from collections.abc import Callable
from datetime import UTC, datetime

from .errors import DuplicateTaskNameError, TaskManagerClosedError
from .domain import Task
from .domain.task import TaskStateObserver
from .runtime import (
    TaskSnapshot,
    TaskStartSpec,
    _TaskRuntimeRecord,
    snapshot_task,
)
from .runtime_registry import TaskRuntimeRegistry
from .scheduler import TaskScheduler
from .task_event_publisher import TaskEventPublisher
from .validation import validate_start_spec


class TaskAdmissionController:
    def __init__(
        self,
        *,
        registry: TaskRuntimeRegistry,
        scheduler: TaskScheduler,
        events: TaskEventPublisher,
        state_observer: TaskStateObserver,
        is_closing: Callable[[], bool],
        default_timeout_ms: int,
        max_timeout_ms: int,
    ):
        self._registry = registry
        self._scheduler = scheduler
        self._events = events
        self._state_observer = state_observer
        self._is_closing = is_closing
        self._default_timeout_ms = default_timeout_ms
        self._max_timeout_ms = max_timeout_ms

    def start(self, spec: TaskStartSpec) -> TaskSnapshot:
        validate_start_spec(spec)
        timeout_ms = (
            self._default_timeout_ms
            if spec.timeout_ms is None
            else spec.timeout_ms
        )
        if (
            not isinstance(timeout_ms, int)
            or isinstance(timeout_ms, bool)
            or not 1 <= timeout_ms <= self._max_timeout_ms
        ):
            raise ValueError(
                f"timeout_ms must be between 1 and {self._max_timeout_ms}"
            )
        if self._is_closing():
            raise TaskManagerClosedError("TaskManager is closing")
        if self._registry.get(spec.task_name) is not None:
            raise DuplicateTaskNameError(
                f"Task name is already reserved: {spec.task_name!r}"
            )

        task = Task(
            task_id=self._registry.allocate_id(),
            task_title=spec.task_name,
            parent_session_id=spec.parent_session_id,
            task_type=spec.task_type,
            task_prompt=spec.task_prompt,
            workdir=spec.workdir,
            constraints=spec.constraints,
            timeout_ms=timeout_ms,
        )
        record = _TaskRuntimeRecord(
            task=task,
            start_spec=spec,
            created_at=datetime.now(UTC),
        )
        record.unsubscribe_state = task.add_state_observer(
            self._state_observer
        )
        self._registry.add(record)
        self._scheduler.enqueue(record)
        self._events.publish("task.created", record)
        self._events.publish_queue()
        self._scheduler.drain()
        return snapshot_task(record)


__all__ = ["TaskAdmissionController"]
