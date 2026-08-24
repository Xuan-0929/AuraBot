"""拥有 Task Control Plane 的进程内调度、并发协调与故障状态。

TaskRuntimeEngine 管理 dispatcher、execution/hibernate 协程、单 Task 锁、active clock、
事件提交唤醒和 fail-stop。TaskManager 负责产品命令顺序，单 Task 执行流程位于
execution.py；Engine 不依赖 Manager 或 Gateway。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from contextlib import AbstractAsyncContextManager
from enum import StrEnum
from uuid import UUID

from ..models import (
    TaskErrorCode,
    TaskRecord,
    TaskRuntimeUnavailable,
    TaskStatus,
)
from ..persistence.store import TaskPersistenceError, TaskStore
from ..settings import TaskSettings
from .execution import run_task
from .process import PiProcessSupervisor, StopIntent, StopResult
from .rpc import PiProtocolError
from .scheduler import FIFOScheduler, SchedulerClosed

logger = logging.getLogger(__name__)


class LiveAnswerDelivery(StrEnum):
    """Outcome of attempting to write an accepted answer to the live Pi process."""

    DELIVERED = "delivered"
    RESTART_REQUIRED = "restart_required"


class TaskRuntimeEngine:
    """Coordinate all ephemeral state for the Python Task runtime."""

    def __init__(
        self,
        settings: TaskSettings,
        store: TaskStore,
        scheduler: FIFOScheduler,
        supervisor: PiProcessSupervisor,
    ) -> None:
        self._settings = settings
        self._store = store
        self._scheduler = scheduler
        self._supervisor = supervisor
        self._dispatcher: asyncio.Task[None] | None = None
        self._executions: dict[UUID, asyncio.Task[None]] = {}
        self._task_locks: dict[UUID, asyncio.Lock] = {}
        self._hibernate_tasks: dict[UUID, asyncio.Task[None]] = {}
        self._active_base_ms: dict[UUID, int] = {}
        self._active_started: dict[UUID, float] = {}
        self._event_condition = asyncio.Condition()
        self._event_revision = 0
        self._started = False
        self._closing = False
        self._fatal_error: RuntimeError | None = None

    async def startup(self) -> None:
        """Reconcile durable non-terminal work and start FIFO dispatch."""

        if self._started:
            return
        await self._reconcile_startup()
        self._started = True
        self._dispatcher = asyncio.create_task(
            self._dispatch_loop(), name="task-dispatcher"
        )

    async def shutdown(self) -> None:
        """Stop admission and persist deterministic states after workers are reaped."""

        if self._closing:
            return
        self._closing = True
        await self._scheduler.shutdown()
        if self._dispatcher is not None:
            self._dispatcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._dispatcher

        async def close_all() -> None:
            await asyncio.gather(
                *(self._shutdown_task(task_id) for task_id in tuple(self._executions)),
                return_exceptions=False,
            )
            await self._supervisor.shutdown(self._settings.shutdown_timeout_seconds)

        try:
            async with asyncio.timeout(self._settings.shutdown_timeout_seconds):
                await close_all()
        finally:
            for hibernate in tuple(self._hibernate_tasks.values()):
                hibernate.cancel()
            for hibernate in tuple(self._hibernate_tasks.values()):
                with contextlib.suppress(asyncio.CancelledError):
                    await hibernate
            self._hibernate_tasks.clear()
            for execution in tuple(self._executions.values()):
                execution.cancel()
            for execution in tuple(self._executions.values()):
                with contextlib.suppress(asyncio.CancelledError):
                    await execution
            async with self._event_condition:
                self._event_condition.notify_all()

    def require_available(self) -> None:
        """Reject commands before startup, during shutdown or after fail-stop."""

        if not self._started or self._closing or self._fatal_error is not None:
            raise TaskRuntimeUnavailable("Task runtime is unavailable or shutting down.")

    def task_guard(self, task_id: UUID) -> AbstractAsyncContextManager[None]:
        """Return the one lock shared by commands and execution for a Task."""

        return self._task_locks.setdefault(task_id, asyncio.Lock())

    async def enqueue(self, task_id: UUID) -> None:
        await self._scheduler.enqueue(task_id)

    async def remove_queued(self, task_id: UUID) -> bool:
        return await self._scheduler.remove(task_id)

    def start_active_clock(self, task: TaskRecord) -> None:
        self._active_base_ms[task.id] = task.active_runtime_ms
        self._active_started[task.id] = time.monotonic()

    def _current_active_ms(self, task: TaskRecord) -> int:
        started = self._active_started.get(task.id)
        base = self._active_base_ms.get(task.id, task.active_runtime_ms)
        if started is None:
            return base
        elapsed = max(1, int((time.monotonic() - started) * 1000))
        return base + elapsed

    def stop_active_clock(self, task: TaskRecord) -> int:
        total = self._current_active_ms(task)
        self._active_started.pop(task.id, None)
        self._active_base_ms[task.id] = total
        return total

    def remaining_seconds(self, task: TaskRecord) -> float:
        remaining_ms = task.timeout_seconds * 1000 - self._current_active_ms(task)
        if remaining_ms <= 0:
            raise TimeoutError
        return remaining_ms / 1000

    async def stop_active(
        self,
        task_id: UUID,
        intent: StopIntent,
    ) -> StopResult:
        """Reap one Pi process and cancel its execution coroutine."""

        result = await self._supervisor.stop(task_id, intent)
        await self._cancel_execution(task_id)
        await self._require_reaped(result)
        return result

    async def deliver_live_answer(
        self,
        task_id: UUID,
        rpc_request_id: str | None,
        answer: str,
    ) -> LiveAnswerDelivery:
        """Deliver to live Pi or safely retire it so durable continuation can run."""

        await self._cancel_hibernate(task_id)
        running = await self._supervisor.get(task_id)
        if running is None:
            return LiveAnswerDelivery.RESTART_REQUIRED
        if rpc_request_id is None:
            await self._cancel_execution(task_id)
            result = await self._supervisor.stop(task_id, StopIntent.WAITING)
            await self._require_reaped(result)
            return LiveAnswerDelivery.RESTART_REQUIRED
        try:
            await running.rpc.send_extension_ui_response(rpc_request_id, answer)
        except PiProtocolError:
            await self._cancel_execution(task_id)
            result = await self._supervisor.stop(task_id, StopIntent.WAITING)
            await self._require_reaped(result)
            return LiveAnswerDelivery.RESTART_REQUIRED
        return LiveAnswerDelivery.DELIVERED

    def schedule_hibernate(self, task_id: UUID) -> None:
        """Ensure a waiting Task has at most one delayed Pi retirement timer."""

        previous = self._hibernate_tasks.pop(task_id, None)
        if previous is not None:
            previous.cancel()
        self._hibernate_tasks[task_id] = asyncio.create_task(
            self._hibernate_waiting(task_id),
            name=f"task-wait-hibernate-{task_id}",
        )

    async def notify_committed_event(self) -> None:
        async with self._event_condition:
            self._event_revision += 1
            self._event_condition.notify_all()

    @property
    def event_revision(self) -> int:
        return self._event_revision

    async def wait_for_event_change(self, revision: int) -> None:
        async with self._event_condition:
            await self._event_condition.wait_for(
                lambda: self._event_revision != revision or self._closing
            )

    @property
    def closing(self) -> bool:
        return self._closing

    async def enter_fail_stop(self, error: RuntimeError) -> None:
        """Stop admission and reap every worker after unrecoverable runtime failure."""

        if self._fatal_error is not None:
            return
        self._fatal_error = error
        logger.critical("Task runtime entered fail-stop", exc_info=error)
        await self._scheduler.shutdown()
        dispatcher = self._dispatcher
        if dispatcher is not None and dispatcher is not asyncio.current_task():
            dispatcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await dispatcher
        current = asyncio.current_task()
        for execution in tuple(self._executions.values()):
            if execution is not current:
                execution.cancel()
        await self._supervisor.shutdown(self._settings.shutdown_timeout_seconds)

    async def _dispatch_loop(self) -> None:
        try:
            while True:
                task_id = await self._scheduler.admit()
                runner = asyncio.create_task(
                    self._run_admitted(task_id), name=f"task-worker-{task_id}"
                )
                self._executions[task_id] = runner
        except SchedulerClosed:
            return
        except asyncio.CancelledError:
            raise

    async def _run_admitted(self, task_id: UUID) -> None:
        try:
            await run_task(
                task_id,
                runtime=self,
                store=self._store,
                supervisor=self._supervisor,
                settings=self._settings,
            )
        except TaskPersistenceError as error:
            await self.enter_fail_stop(error)
        finally:
            await self._scheduler.release(task_id)
            if self._executions.get(task_id) is asyncio.current_task():
                self._executions.pop(task_id, None)

    async def _cancel_execution(self, task_id: UUID) -> None:
        execution = self._executions.get(task_id)
        if execution is None or execution is asyncio.current_task():
            return
        execution.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await execution

    async def _cancel_hibernate(self, task_id: UUID) -> None:
        hibernate = self._hibernate_tasks.pop(task_id, None)
        if hibernate is None:
            return
        hibernate.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hibernate

    async def _hibernate_waiting(self, task_id: UUID) -> None:
        try:
            await asyncio.sleep(self._settings.waiting_process_grace_seconds)
            async with self.task_guard(task_id):
                current = await self._store.get_task(task_id)
                if current.status != TaskStatus.WAITING:
                    return
                if await self._supervisor.get(task_id) is None:
                    return
                await self._supervisor.stop(task_id, StopIntent.WAITING)
        except asyncio.CancelledError:
            raise
        finally:
            if self._hibernate_tasks.get(task_id) is asyncio.current_task():
                self._hibernate_tasks.pop(task_id, None)

    async def _shutdown_task(self, task_id: UUID) -> None:
        async with self.task_guard(task_id):
            current = await self._store.get_task(task_id)
            if current.status == TaskStatus.WAITING:
                await self.stop_active(task_id, StopIntent.WAITING)
                return
            if current.status == TaskStatus.CANCELLING:
                await self.stop_active(task_id, StopIntent.SHUTDOWN)
                await self._persist(
                    self._store.transition_task(
                        task_id,
                        TaskStatus.CANCELLED,
                        expected_version=current.version,
                    )
                )
                await self.notify_committed_event()
                return
            if current.status not in {
                TaskStatus.STARTING,
                TaskStatus.RUNNING,
                TaskStatus.PAUSING,
            }:
                return
            intermediate = current
            if current.status != TaskStatus.PAUSING:
                intermediate, _ = await self._persist(
                    self._store.transition_task(
                        task_id,
                        TaskStatus.PAUSING,
                        expected_version=current.version,
                        active_runtime_ms=self.stop_active_clock(current),
                    )
                )
                await self.notify_committed_event()
            await self.stop_active(task_id, StopIntent.SHUTDOWN)
            await self._persist(
                self._store.transition_task(
                    task_id,
                    TaskStatus.PAUSED,
                    expected_version=intermediate.version,
                    active_runtime_ms=intermediate.active_runtime_ms,
                    pause_reason="backend_shutdown",
                )
            )
            await self.notify_committed_event()

    async def _reconcile_startup(self) -> None:
        for task in await self._store.recoverable_tasks():
            if task.status == TaskStatus.QUEUED:
                await self.enqueue(task.id)
            elif task.status in {
                TaskStatus.STARTING,
                TaskStatus.RUNNING,
                TaskStatus.PAUSING,
            }:
                session_exists = self._settings.session_directory(task.id).is_dir()
                target = TaskStatus.PAUSED if session_exists else TaskStatus.FAILED
                kwargs = (
                    {"pause_reason": "backend_restarted"}
                    if session_exists
                    else {
                        "error_code": TaskErrorCode.SESSION_MISSING,
                        "error_message": "Pi session is missing after Backend restart.",
                    }
                )
                await self._persist(
                    self._store.transition_task(
                        task.id,
                        target,
                        expected_version=task.version,
                        **kwargs,
                    )
                )
                await self.notify_committed_event()
            elif task.status == TaskStatus.CANCELLING:
                await self._persist(
                    self._store.transition_task(
                        task.id,
                        TaskStatus.CANCELLED,
                        expected_version=task.version,
                    )
                )
                await self.notify_committed_event()
            elif task.status == TaskStatus.WAITING:
                if await self._store.undelivered_answer(task.id) is not None:
                    queued, _ = await self._persist(
                        self._store.transition_task(
                            task.id,
                            TaskStatus.QUEUED,
                            expected_version=task.version,
                        )
                    )
                    await self.notify_committed_event()
                    await self.enqueue(queued.id)

    async def _persist(self, operation):
        try:
            return await operation
        except TaskPersistenceError as error:
            await self.enter_fail_stop(error)
            raise TaskRuntimeUnavailable(
                "Task persistence failed; the runtime stopped admission."
            ) from error

    async def _require_reaped(self, result: StopResult) -> None:
        if result.reaped:
            return
        error = TaskRuntimeUnavailable(
            "The previous Pi process could not be safely reaped."
        )
        await self.enter_fail_stop(error)
        raise error


__all__ = ["LiveAnswerDelivery", "TaskRuntimeEngine"]
