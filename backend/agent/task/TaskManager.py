"""Process-local registry and retention lifecycle for Harness runtimes."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from threading import RLock

from .Task import Task
from .TaskEntity import (
    InterruptTaskOutcome,
    InterruptTaskResult,
    PeekTaskResult,
    TaskState,
)
from .harness.Harness import Harness


TERMINAL_STATES = {
    TaskState.Completed,
    TaskState.Failed,
    TaskState.Interrupted,
}


class TaskManager:
    """Store complete Harness runtimes under unique Task IDs and names."""

    _instance: "TaskManager | None" = None
    TERMINAL_RETENTION_SECONDS = 100.0

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, *, retention_seconds: float | None = None):
        if getattr(self, "_initialized", False):
            return
        retention = (
            self.TERMINAL_RETENTION_SECONDS
            if retention_seconds is None
            else retention_seconds
        )
        if retention < 0:
            raise ValueError("retention_seconds must be non-negative")
        self._retention_seconds = float(retention)
        self._runtimes_by_id: dict[int, Harness] = {}
        self._runtimes_by_name: dict[str, Harness] = {}
        self._cleanup_handles: dict[str, asyncio.TimerHandle] = {}
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._runtimes_lock = RLock()
        self._initialized = True

    @property
    def retention_seconds(self) -> float:
        return self._retention_seconds

    def bind_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind cleanup scheduling to the backend process's main event loop."""

        if not isinstance(loop, asyncio.AbstractEventLoop):
            raise TypeError("loop must be an asyncio event loop")
        with self._runtimes_lock:
            self._main_loop = loop

    def register_runtime(self, harness: Harness) -> Harness:
        """Register one real Harness and reject both ID and name collisions."""

        task = self._require_runtime(harness)
        with self._runtimes_lock:
            if task.task_id in self._runtimes_by_id:
                raise ValueError(f"Task ID is already registered: {task.task_id}")
            if task.task_title in self._runtimes_by_name:
                raise ValueError(
                    f"Task name is already registered: {task.task_title!r}"
                )
            self._runtimes_by_id[task.task_id] = harness
            self._runtimes_by_name[task.task_title] = harness
        return harness

    @contextmanager
    def runtime(self, harness: Harness) -> Iterator[Harness]:
        """Register for one run and apply terminal retention on exit."""

        self.register_runtime(harness)
        try:
            yield harness
        except BaseException:
            if harness.task.state in TERMINAL_STATES:
                self._request_terminal_cleanup(harness)
            else:
                self._remove_runtime(harness)
            raise
        else:
            if harness.task.state in TERMINAL_STATES:
                self._request_terminal_cleanup(harness)
            else:
                self._remove_runtime(harness)

    def get_runtime(self, task_name: str) -> Harness | None:
        with self._runtimes_lock:
            return self._runtimes_by_name.get(task_name)

    def list_runtimes(self) -> tuple[Harness, ...]:
        with self._runtimes_lock:
            return tuple(self._runtimes_by_id.values())

    def peek_task(self, task_name: str) -> PeekTaskResult:
        harness = self.get_runtime(task_name)
        if harness is None:
            return PeekTaskResult(
                task_name=task_name,
                found=False,
                task_id=None,
                task_type=None,
                state=None,
                checkpoint=None,
            )

        task = harness.task
        return PeekTaskResult(
            task_name=task_name,
            found=True,
            task_id=task.task_id,
            task_type=task.task_type,
            state=task.state,
            checkpoint=task.checkpoint,
        )

    def interrupt_task(self, task_name: str) -> InterruptTaskResult:
        harness = self.get_runtime(task_name)
        if harness is None:
            return InterruptTaskResult(
                task_name=task_name,
                outcome=InterruptTaskOutcome.NotFound,
                task_id=None,
                previous_state=None,
                state=None,
            )

        task = harness.task
        previous_state, current_state, deferred = task.interrupt()
        if deferred:
            outcome = InterruptTaskOutcome.Deferred
        elif previous_state is TaskState.Interrupted:
            outcome = InterruptTaskOutcome.AlreadyInterrupted
        elif current_state is TaskState.Interrupted:
            outcome = InterruptTaskOutcome.Interrupted
        else:
            outcome = InterruptTaskOutcome.AlreadySettled

        return InterruptTaskResult(
            task_name=task_name,
            outcome=outcome,
            task_id=task.task_id,
            previous_state=previous_state,
            state=current_state,
        )

    def clear_task(self, task_name: str) -> bool:
        """Remove one terminal runtime and cancel its retention timer."""

        with self._runtimes_lock:
            harness = self._runtimes_by_name.get(task_name)
            if harness is None or harness.task.state not in TERMINAL_STATES:
                return False
            self._delete_indexes_locked(harness)
            handle = self._cleanup_handles.pop(task_name, None)
            if handle is not None:
                handle.cancel()
            return True

    def shutdown(self) -> None:
        """Cancel every timer and drop all process-local runtime state."""

        with self._runtimes_lock:
            for handle in self._cleanup_handles.values():
                handle.cancel()
            self._cleanup_handles.clear()
            self._runtimes_by_id.clear()
            self._runtimes_by_name.clear()
            self._main_loop = None

    def _request_terminal_cleanup(self, harness: Harness) -> None:
        with self._runtimes_lock:
            loop = self._main_loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._schedule_terminal_cleanup, harness)

    def _schedule_terminal_cleanup(self, harness: Harness) -> None:
        task_name = harness.task.task_title
        with self._runtimes_lock:
            if (
                self._runtimes_by_name.get(task_name) is not harness
                or harness.task.state not in TERMINAL_STATES
            ):
                return
            previous = self._cleanup_handles.pop(task_name, None)
            if previous is not None:
                previous.cancel()
            loop = self._main_loop
            if loop is None or loop.is_closed():
                return
            self._cleanup_handles[task_name] = loop.call_later(
                self._retention_seconds,
                self._expire_runtime,
                task_name,
                harness,
            )

    def _expire_runtime(self, task_name: str, harness: Harness) -> None:
        with self._runtimes_lock:
            if self._runtimes_by_name.get(task_name) is not harness:
                return
            self._cleanup_handles.pop(task_name, None)
            if harness.task.state in TERMINAL_STATES:
                self._delete_indexes_locked(harness)

    def _remove_runtime(self, harness: Harness) -> bool:
        with self._runtimes_lock:
            task_name = harness.task.task_title
            if self._runtimes_by_name.get(task_name) is not harness:
                return False
            self._delete_indexes_locked(harness)
            handle = self._cleanup_handles.pop(task_name, None)
            if handle is not None:
                handle.cancel()
            return True

    def _delete_indexes_locked(self, harness: Harness) -> None:
        task = harness.task
        if self._runtimes_by_id.get(task.task_id) is harness:
            self._runtimes_by_id.pop(task.task_id, None)
        if self._runtimes_by_name.get(task.task_title) is harness:
            self._runtimes_by_name.pop(task.task_title, None)

    @staticmethod
    def _require_runtime(harness: Harness) -> Task:
        if not isinstance(harness, Harness):
            raise TypeError("runtime must be a Harness")
        task = harness.task
        if not isinstance(task, Task):
            raise TypeError("Harness runtime must hold a Task")
        if (
            not isinstance(task.task_id, int)
            or isinstance(task.task_id, bool)
            or not isinstance(task.task_title, str)
            or not task.task_title
            or task.task_title != task.task_title.strip()
        ):
            raise ValueError("Harness Task must have a valid ID and name")
        return task


__all__ = ["TERMINAL_STATES", "TaskManager"]
