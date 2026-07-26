"""Terminal Task retention and resource cleanup."""

import asyncio
from collections.abc import Callable

from .runtime import _TaskRuntimeRecord
from .runtime_registry import TaskRuntimeRegistry
from .scheduler import TERMINAL_STATES, TaskScheduler
from .task_event_publisher import TaskEventPublisher


class TaskRetentionController:
    def __init__(
        self,
        *,
        seconds: float,
        registry: TaskRuntimeRegistry,
        scheduler: TaskScheduler,
        events: TaskEventPublisher,
        event_loop: Callable[[], asyncio.AbstractEventLoop | None],
        is_closing: Callable[[], bool],
    ):
        self.seconds = seconds
        self._registry = registry
        self._scheduler = scheduler
        self._events = events
        self._event_loop = event_loop
        self._is_closing = is_closing
        self._handles: dict[str, asyncio.TimerHandle] = {}

    def schedule(self, record: _TaskRuntimeRecord) -> None:
        if record.task.state not in TERMINAL_STATES:
            return
        loop = self._event_loop()
        if loop is None or loop.is_closed():
            return
        name = record.task.task_title
        self._cancel_timer(name)
        self._handles[name] = loop.call_later(
            self.seconds,
            self._expire,
            name,
            record,
        )

    def remove(
        self,
        record: _TaskRuntimeRecord,
        *,
        publish: bool,
    ) -> None:
        name = record.task.task_title
        if not self._registry.remove(record):
            return
        self._scheduler.remove(record)
        self._cancel_timer(name)
        if record.unsubscribe_state is not None:
            record.unsubscribe_state()
            record.unsubscribe_state = None
        if record.capabilities is not None:
            record.capabilities.close()
            record.capabilities = None
        if publish and not self._is_closing():
            self._events.publish(
                "task.removed",
                extra={"task_name": name, "task_id": record.task.task_id},
            )
            self._events.publish_queue()

    def _expire(
        self,
        task_name: str,
        expected: _TaskRuntimeRecord,
    ) -> None:
        record = self._registry.get(task_name)
        if (
            record is expected
            and record.task.state in TERMINAL_STATES
            and record.runner is None
        ):
            self.remove(record, publish=True)

    def _cancel_timer(self, task_name: str) -> None:
        handle = self._handles.pop(task_name, None)
        if handle is not None:
            handle.cancel()


__all__ = ["TaskRetentionController"]
