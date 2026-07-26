"""FIFO execution-slot scheduler for Task runtime records."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from ..capabilities.lease import CapabilitySession
from .TaskEntity import TaskState
from .harness.Harness import Harness
from .runtime import _TaskRuntimeRecord


TERMINAL_STATES = {
    TaskState.Completed,
    TaskState.Failed,
    TaskState.Interrupted,
}
ACTIVE_STATES = {
    TaskState.Initializing,
    TaskState.Thinking,
    TaskState.Pushing,
    TaskState.Planning,
    TaskState.Concluding,
}


class TaskScheduler:
    def __init__(
        self,
        *,
        max_running: int,
        harness_factory: Any | None = None,
        capability_session_factory: (
            Callable[[], CapabilitySession] | None
        ) = None,
        is_retained: Callable[[_TaskRuntimeRecord], bool] | None = None,
        on_started: Callable[[_TaskRuntimeRecord], None] | None = None,
        on_settled: Callable[[_TaskRuntimeRecord], None] | None = None,
        on_queue_changed: Callable[[], None] | None = None,
    ):
        if (
            not isinstance(max_running, int)
            or isinstance(max_running, bool)
            or max_running < 1
        ):
            raise ValueError("max_running must be a positive integer")
        self._max_running = max_running
        self._harness_factory = harness_factory
        self._capability_session_factory = capability_session_factory
        self._is_retained = is_retained or (lambda record: True)
        self._on_started = on_started or (lambda record: None)
        self._on_settled = on_settled or (lambda record: None)
        self._on_queue_changed = on_queue_changed or (lambda: None)
        self._ready: deque[_TaskRuntimeRecord] = deque()
        self._running: dict[str, _TaskRuntimeRecord] = {}

    @property
    def running_count(self) -> int:
        return len(self._running)

    @property
    def queued_count(self) -> int:
        return len(self._ready)

    @property
    def ready(self) -> tuple[_TaskRuntimeRecord, ...]:
        return tuple(self._ready)

    def enqueue(self, record: _TaskRuntimeRecord) -> None:
        self._ready.append(record)
        self._refresh_positions()

    def take_ready(self) -> _TaskRuntimeRecord | None:
        if (
            not self._ready
            or len(self._running) >= self._max_running
        ):
            return None
        record = self._ready.popleft()
        record.queue_position = None
        self._refresh_positions()
        return record

    def remove_queued(self, record: _TaskRuntimeRecord) -> bool:
        match = next(
            (candidate for candidate in self._ready if candidate is record),
            None,
        )
        if match is None:
            return False
        self._ready.remove(match)
        match.queue_position = None
        self._refresh_positions()
        return True

    def mark_running(self, record: _TaskRuntimeRecord) -> None:
        self._running[record.task.task_title] = record

    def mark_settled(self, record: _TaskRuntimeRecord) -> None:
        if self._running.get(record.task.task_title) is record:
            self._running.pop(record.task.task_title, None)

    def remove(self, record: _TaskRuntimeRecord) -> None:
        self.remove_queued(record)
        self.mark_settled(record)

    def clear(self) -> None:
        for record in self._ready:
            record.queue_position = None
        self._ready.clear()
        self._running.clear()

    def drain(self) -> None:
        if self._harness_factory is None:
            return
        changed = False
        while True:
            record = self.take_ready()
            if record is None:
                break
            changed = True
            if record.task.state is TaskState.Initializing:
                self._activate(record)
        if changed:
            self._on_queue_changed()

    def cancel(self, record: _TaskRuntimeRecord) -> None:
        runner = record.runner
        try:
            current = asyncio.current_task()
        except RuntimeError:
            current = None
        if (
            runner is not None
            and runner is not current
        ):
            runner.cancel()

    @property
    def runners(self) -> tuple[asyncio.Task[None], ...]:
        return tuple(
            record.runner
            for record in self._running_records()
            if record.runner is not None
        )

    async def wait_for_runners(self, grace_seconds: float) -> None:
        runners = self.runners
        if not runners:
            return
        _, pending = await asyncio.wait(
            runners,
            timeout=grace_seconds,
        )
        for runner in pending:
            runner.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def _activate(self, record: _TaskRuntimeRecord) -> None:
        session: CapabilitySession | None = None
        try:
            if self._capability_session_factory is None:
                raise RuntimeError(
                    "TaskScheduler requires a capability session factory"
                )
            session = self._capability_session_factory()
            if not isinstance(session, CapabilitySession):
                raise TypeError(
                    "capability session factory must return CapabilitySession"
                )
            tool_groups = session.acquire_tool_groups(
                record.start_spec.tool_group_names
            )
            skill_groups = session.acquire_skill_groups(
                record.start_spec.skill_group_names
            )
            record.task.update_tool_groups(tool_groups)
            record.task.update_skill_groups(skill_groups)
            harness = self._harness_factory.create(
                record.task,
                capability_session=session,
            )
            if not isinstance(harness, Harness):
                raise TypeError("harness factory must return Harness")
        except Exception:
            if session is not None:
                try:
                    session.close()
                except BaseException:
                    pass
            record.error = {
                "code": "task_initialization_failed",
                "message": "Task capabilities could not be initialized.",
            }
            record.task.compare_and_set_state(
                TaskState.Initializing,
                TaskState.Failed,
            )
            record.settled_at = _utc_now()
            self._on_settled(record)
            return

        if (
            not self._is_retained(record)
            or record.task.state is not TaskState.Initializing
        ):
            session.close()
            return

        record.capabilities = session
        record.harness = harness
        record.started_at = _utc_now()
        self.mark_running(record)
        record.runner = asyncio.create_task(
            self._execute(record),
            name=f"aurabot-task:{record.task.task_title}",
        )
        self._on_started(record)

    async def _execute(self, record: _TaskRuntimeRecord) -> None:
        try:
            if record.harness is None:
                raise RuntimeError("Task runtime has no Harness")
            async with asyncio.timeout(record.task.timeout_ms / 1000):
                record.result = await record.harness.run()
        except asyncio.CancelledError:
            if record.task.state not in TERMINAL_STATES:
                record.task.interrupt()
        except Exception:
            record.task.compare_and_set_state(
                ACTIVE_STATES,
                TaskState.Failed,
            )
            record.error = {
                "code": "task_execution_failed",
                "message": "Task execution failed.",
            }
        finally:
            record.runner = None
            record.settled_at = record.settled_at or _utc_now()
            self.mark_settled(record)
            self._on_settled(record)
            self.drain()

    def _running_records(self) -> tuple[_TaskRuntimeRecord, ...]:
        return tuple(self._running.values())

    def _refresh_positions(self) -> None:
        for position, record in enumerate(self._ready, start=1):
            record.queue_position = position


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = ["ACTIVE_STATES", "TERMINAL_STATES", "TaskScheduler"]
