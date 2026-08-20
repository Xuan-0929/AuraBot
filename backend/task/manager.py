"""
编排 AuraBot Task 产品状态、FIFO 准入、Pi Worker 与业务事件投影。

TaskManager 是 Gateway 和未来 Main Agent 的唯一门面：它组合 concrete Store、Scheduler
和 Supervisor，负责 pause/resume/cancel、问题等待、active timeout、重启与 shutdown。
它不解析 Tool Call、不持有 subprocess handle，也不向外暴露 Pi raw 协议。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Iterable
from pathlib import Path
from uuid import UUID

from .models import (
    CreateTaskCommand,
    TERMINAL_TASK_STATUSES,
    TaskErrorCode,
    TaskEvent,
    TaskPage,
    TaskRecord,
    TaskStateConflict,
    TaskStatus,
    TaskVersionConflict,
)
from .process import (
    PiProcessStartError,
    PiProcessSupervisor,
    StopIntent,
    TaskLaunchSpec,
)
from .rpc import PiProtocolError
from .scheduler import FifoScheduler, SchedulerClosed
from .settings import TaskSettings
from .store import TaskPersistenceError, TaskStore

QUESTION_MARKER = "AURABOT_TASK_QUESTION_V1\n"
RESUME_PROMPT = (
    "Resume the existing AuraBot Task session. Inspect the persisted session context, "
    "completed Tool results, and the original goal before continuing safely."
)
logger = logging.getLogger(__name__)


class TaskRuntimeUnavailable(RuntimeError):
    """Raised when startup/shutdown/fail-stop prevents new Task admission."""


class TaskManager:
    """Concrete orchestration facade for Task business operations."""

    def __init__(
        self,
        settings: TaskSettings,
        store: TaskStore,
        scheduler: FifoScheduler,
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
        self._continuations: dict[UUID, str] = {}
        self._active_base_ms: dict[UUID, int] = {}
        self._active_started: dict[UUID, float] = {}
        self._event_condition = asyncio.Condition()
        self._event_revision = 0
        self._started = False
        self._closing = False
        self._fatal_error: TaskPersistenceError | None = None

    async def startup(self) -> None:
        """Initialize persistence, reconcile orphaned Tasks and start FIFO dispatch."""

        if self._started:
            return
        await self._store.initialize()
        await self._reconcile_startup()
        self._started = True
        self._dispatcher = asyncio.create_task(
            self._dispatch_loop(), name="task-dispatcher"
        )

    async def shutdown(self) -> None:
        """Stop admission and persist deterministic states only after workers are reaped."""

        if self._closing:
            return
        self._closing = True
        await self._scheduler.shutdown()
        if self._dispatcher is not None:
            self._dispatcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._dispatcher

        async def close_all() -> None:
            task_ids = tuple(self._executions)
            await asyncio.gather(
                *(self._shutdown_task(task_id) for task_id in task_ids),
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
            await self._store.close()
            async with self._event_condition:
                self._event_condition.notify_all()

    async def create_task(self, command: CreateTaskCommand) -> TaskRecord:
        """Persist queued before notifying the in-memory scheduler."""

        self._require_available()
        task = await self._persist(self._store.create_task(command))
        await self._notify_event()
        await self._scheduler.enqueue(task.id)
        return task

    async def list_tasks(
        self,
        *,
        after_row_id: int | None = None,
        limit: int = 50,
        statuses: Iterable[TaskStatus] | None = None,
    ) -> TaskPage:
        return await self._store.list_tasks(
            after_row_id=after_row_id, limit=limit, statuses=statuses
        )

    async def get_task(
        self, task_id: UUID, include_deleted: bool = False
    ) -> TaskRecord:
        return await self._store.get_task(task_id, include_deleted)

    async def pause_task(self, task_id: UUID, expected_version: int) -> TaskRecord:
        """Pause queued/active/waiting work and guarantee no process remains."""

        lock = self._lock_for(task_id)
        async with lock:
            current = await self._store.get_task(task_id)
            if current.status == TaskStatus.PAUSED:
                return current
            self._require_version(current, expected_version)
            if current.status == TaskStatus.QUEUED:
                await self._scheduler.remove(task_id)
                paused, _ = await self._persist(
                    self._store.transition_task(
                        task_id,
                        TaskStatus.PAUSED,
                        expected_version=current.version,
                        pause_reason="user_paused",
                    )
                )
                await self._notify_event()
                return paused
            if current.status not in {
                TaskStatus.STARTING,
                TaskStatus.RUNNING,
                TaskStatus.WAITING,
                TaskStatus.PAUSING,
            }:
                raise TaskStateConflict(current.status, TaskStatus.PAUSED)
            intermediate = current
            if current.status != TaskStatus.PAUSING:
                intermediate, _ = await self._persist(
                    self._store.transition_task(
                        task_id,
                        TaskStatus.PAUSING,
                        expected_version=current.version,
                        active_runtime_ms=self._stop_active_clock(task_id, current),
                    )
                )
                await self._notify_event()
            await self._supervisor.stop(task_id, StopIntent.PAUSE)
            await self._cancel_execution(task_id)
            paused, _ = await self._persist(
                self._store.transition_task(
                    task_id,
                    TaskStatus.PAUSED,
                    expected_version=intermediate.version,
                    active_runtime_ms=intermediate.active_runtime_ms,
                    pause_reason="user_paused",
                )
            )
            await self._notify_event()
            return paused

    async def resume_task(self, task_id: UUID, expected_version: int) -> TaskRecord:
        """Return paused work to the queue, or waiting when its question remains pending."""

        self._require_available()
        async with self._lock_for(task_id):
            current = await self._store.get_task(task_id)
            if current.status == TaskStatus.QUEUED:
                return current
            self._require_version(current, expected_version)
            if current.status != TaskStatus.PAUSED:
                raise TaskStateConflict(current.status, TaskStatus.QUEUED)
            pending = await self._store.pending_question(task_id)
            if pending is not None:
                waiting, _ = await self._persist(
                    self._store.transition_task(
                        task_id,
                        TaskStatus.WAITING,
                        expected_version=current.version,
                        pause_reason=None,
                    )
                )
                await self._notify_event()
                return waiting
            queued, _ = await self._persist(
                self._store.transition_task(
                    task_id,
                    TaskStatus.QUEUED,
                    expected_version=current.version,
                    pause_reason=None,
                )
            )
            self._continuations[task_id] = RESUME_PROMPT
            await self._notify_event()
            await self._scheduler.enqueue(task_id)
            return queued

    async def cancel_task(self, task_id: UUID, expected_version: int) -> TaskRecord:
        """Permanently cancel work, reaping an active process before terminal commit."""

        async with self._lock_for(task_id):
            current = await self._store.get_task(task_id)
            if current.status == TaskStatus.CANCELLED:
                return current
            self._require_version(current, expected_version)
            if current.status in TERMINAL_TASK_STATUSES:
                raise TaskStateConflict(current.status, TaskStatus.CANCELLED)
            if current.status in {TaskStatus.QUEUED, TaskStatus.PAUSED} or (
                current.status == TaskStatus.WAITING
                and await self._supervisor.get(task_id) is None
            ):
                await self._scheduler.remove(task_id)
                cancelled, _ = await self._persist(
                    self._store.transition_task(
                        task_id,
                        TaskStatus.CANCELLED,
                        expected_version=current.version,
                        active_runtime_ms=self._stop_active_clock(task_id, current),
                    )
                )
                await self._notify_event()
                return cancelled
            intermediate = current
            if current.status != TaskStatus.CANCELLING:
                intermediate, _ = await self._persist(
                    self._store.transition_task(
                        task_id,
                        TaskStatus.CANCELLING,
                        expected_version=current.version,
                        active_runtime_ms=self._stop_active_clock(task_id, current),
                    )
                )
                await self._notify_event()
            await self._supervisor.stop(task_id, StopIntent.CANCEL)
            await self._cancel_execution(task_id)
            cancelled, _ = await self._persist(
                self._store.transition_task(
                    task_id,
                    TaskStatus.CANCELLED,
                    expected_version=intermediate.version,
                    active_runtime_ms=intermediate.active_runtime_ms,
                )
            )
            await self._notify_event()
            return cancelled

    async def delete_task(self, task_id: UUID) -> None:
        """Soft-delete terminal work after Store performs controlled session cleanup."""

        await self._persist(self._store.soft_delete(task_id))
        await self._notify_event()

    async def answer_question(
        self,
        task_id: UUID,
        question_id: UUID,
        answer: str,
        expected_version: int,
    ) -> TaskRecord:
        """Answer live RPC immediately or queue a hibernated session continuation."""

        async with self._lock_for(task_id):
            waiting, question, _ = await self._persist(
                self._store.answer_question(
                    task_id,
                    question_id,
                    answer,
                    expected_version=expected_version,
                )
            )
            await self._notify_event()
            hibernate = self._hibernate_tasks.pop(task_id, None)
            if hibernate is not None:
                hibernate.cancel()
            running = await self._supervisor.get(task_id)
            if running is not None and question.rpc_request_id is not None:
                await running.rpc.send_extension_ui_response(question.rpc_request_id, answer)
                resumed, _ = await self._persist(
                    self._store.transition_task(
                        task_id,
                        TaskStatus.RUNNING,
                        expected_version=waiting.version,
                    )
                )
                self._start_active_clock(task_id, resumed)
                await self._notify_event()
                return resumed
            queued, _ = await self._persist(
                self._store.transition_task(
                    task_id,
                    TaskStatus.QUEUED,
                    expected_version=waiting.version,
                )
            )
            self._continuations[task_id] = (
                f"{RESUME_PROMPT}\nThe Main Agent answered the pending question: {answer}"
            )
            await self._notify_event()
            await self._scheduler.enqueue(task_id)
            return queued

    async def iter_events(
        self,
        after_event_id: int,
        task_id: UUID | None = None,
    ) -> AsyncIterator[TaskEvent]:
        """Replay DB events and use memory only to wait for newly committed rows."""

        cursor = after_event_id
        while True:
            seen_revision = self._event_revision
            events = await self._store.events_after(cursor, task_id, 200)
            if events:
                for event in events:
                    cursor = event.event_id
                    yield event
                continue
            if self._closing:
                return
            async with self._event_condition:
                await self._event_condition.wait_for(
                    lambda: self._event_revision != seen_revision or self._closing
                )

    async def _dispatch_loop(self) -> None:
        try:
            while True:
                task_id = await self._scheduler.admit()
                execution = asyncio.create_task(
                    self._execute_task(task_id), name=f"task-worker-{task_id}"
                )
                self._executions[task_id] = execution
        except SchedulerClosed:
            return
        except asyncio.CancelledError:
            raise

    async def _execute_task(self, task_id: UUID) -> None:
        try:
            async with self._lock_for(task_id):
                queued = await self._store.get_task(task_id)
                if queued.status != TaskStatus.QUEUED:
                    return
                self._start_active_clock(task_id, queued)
                starting, _ = await self._persist(
                    self._store.transition_task(
                        task_id,
                        TaskStatus.STARTING,
                        expected_version=queued.version,
                        pi_session_id=str(task_id),
                        pi_session_path=self._settings.session_directory(task_id),
                    )
                )
                await self._notify_event()
            spec = TaskLaunchSpec(
                task_id=task_id,
                title=starting.title,
                prompt=starting.prompt,
                working_directory=starting.working_directory,
                continuation_prompt=self._continuations.pop(task_id, None),
            )
            remaining = self._remaining_seconds(starting)
            running_process = await asyncio.wait_for(
                self._supervisor.start_task(spec), timeout=remaining
            )
            async with self._lock_for(task_id):
                current = await self._store.get_task(task_id)
                if current.status != TaskStatus.STARTING:
                    return
                running, _ = await self._persist(
                    self._store.transition_task(
                        task_id,
                        TaskStatus.RUNNING,
                        expected_version=current.version,
                    )
                )
                await self._notify_event()

            iterator = running_process.rpc.events()
            last_run_model_failed = False
            while True:
                current = await self._store.get_task(task_id)
                try:
                    if current.status == TaskStatus.WAITING:
                        event = await anext(iterator)
                    else:
                        event = await asyncio.wait_for(
                            anext(iterator), timeout=self._remaining_seconds(current)
                        )
                except StopAsyncIteration:
                    current = await self._store.get_task(task_id)
                    if current.status == TaskStatus.WAITING:
                        hibernate = self._hibernate_tasks.get(task_id)
                        if hibernate is not None and hibernate is not asyncio.current_task():
                            with contextlib.suppress(asyncio.CancelledError):
                                await asyncio.shield(hibernate)
                        return
                    raise PiProcessStartError(
                        TaskErrorCode.UNEXPECTED_EXIT,
                        "Pi exited before the Task reached agent_settled.",
                    )

                if event.type == "text_output":
                    text = event.payload.get("text")
                    if isinstance(text, str) and text:
                        await self._persist(self._store.append_output(task_id, text))
                        await self._notify_event()
                elif event.type == "extension_ui_request":
                    await self._handle_extension_ui(task_id, event.payload)
                elif event.type == "extension_error":
                    raise PiProcessStartError(
                        TaskErrorCode.RESOURCE_LOAD_FAILED,
                        "A Pi extension failed while running the Task.",
                    )
                elif event.type == "agent_end":
                    last_run_model_failed = self._agent_end_has_model_error(
                        event.payload
                    )
                elif event.type == "agent_settled":
                    latest = await self._store.get_task(task_id)
                    if latest.status == TaskStatus.WAITING:
                        continue
                    if last_run_model_failed:
                        await self._fail_task(
                            task_id,
                            TaskErrorCode.MODEL_ERROR,
                            "Pi model execution failed.",
                        )
                        return
                    result = await running_process.last_assistant_text()
                    await self._supervisor.stop(task_id, StopIntent.COMPLETE)
                    if result is None:
                        await self._fail_task(
                            task_id,
                            TaskErrorCode.RESULT_UNAVAILABLE,
                            "Pi completed without a final assistant result.",
                        )
                        return
                    async with self._lock_for(task_id):
                        current = await self._store.get_task(task_id)
                        completed, _ = await self._persist(
                            self._store.transition_task(
                                task_id,
                                TaskStatus.COMPLETED,
                                expected_version=current.version,
                                active_runtime_ms=self._stop_active_clock(task_id, current),
                                result_text=result,
                            )
                        )
                        del completed
                        await self._notify_event()
                    return
        except asyncio.CancelledError:
            raise
        except TaskRuntimeUnavailable:
            # _persist() has already stopped admission and reaped every process;
            # no further durable transition is safe after persistence loss.
            return
        except TimeoutError:
            await self._fail_task(
                task_id, TaskErrorCode.TIMEOUT, "Task exceeded its active runtime timeout."
            )
        except PiProcessStartError as exc:
            await self._fail_task(task_id, exc.code, str(exc))
        except PiProtocolError:
            await self._fail_task(
                task_id, TaskErrorCode.PROTOCOL_ERROR, "Pi violated the RPC protocol."
            )
        except BaseException:
            await self._fail_task(
                task_id, TaskErrorCode.INTERNAL_ERROR, "Task execution failed internally."
            )
        finally:
            await self._scheduler.release(task_id)
            current = asyncio.current_task()
            if self._executions.get(task_id) is current:
                self._executions.pop(task_id, None)

    async def _handle_extension_ui(
        self, task_id: UUID, payload: object
    ) -> None:
        if not isinstance(payload, dict):
            return
        running = await self._supervisor.get(task_id)
        if running is None:
            return
        request_id = payload.get("id")
        method = payload.get("method")
        title = payload.get("title")
        if not (
            isinstance(request_id, str)
            and method in {"input", "select"}
            and isinstance(title, str)
            and title.startswith(QUESTION_MARKER)
        ):
            if isinstance(request_id, str) and method in {
                "input",
                "select",
                "confirm",
                "editor",
            }:
                await running.rpc.send_extension_ui_response(request_id, None)
            return
        question_text = title[len(QUESTION_MARKER) :]
        raw_options = payload.get("options")
        options = (
            tuple(value for value in raw_options if isinstance(value, str))
            if isinstance(raw_options, list)
            else ()
        )
        async with self._lock_for(task_id):
            current = await self._store.get_task(task_id)
            active_runtime_ms = self._stop_active_clock(task_id, current)
            waiting, _, _ = await self._persist(
                self._store.raise_question(
                    task_id,
                    question_text,
                    options,
                    request_id,
                    expected_version=current.version,
                    active_runtime_ms=active_runtime_ms,
                )
            )
            await self._notify_event()
            previous = self._hibernate_tasks.pop(task_id, None)
            if previous is not None:
                previous.cancel()
            self._hibernate_tasks[task_id] = asyncio.create_task(
                self._hibernate_waiting(task_id),
                name=f"task-wait-hibernate-{task_id}",
            )

    async def _hibernate_waiting(self, task_id: UUID) -> None:
        try:
            await asyncio.sleep(self._settings.waiting_process_grace_seconds)
            async with self._lock_for(task_id):
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

    async def _fail_task(
        self, task_id: UUID, code: TaskErrorCode, message: str
    ) -> None:
        await self._supervisor.stop(task_id, StopIntent.CANCEL)
        async with self._lock_for(task_id):
            current = await self._store.get_task(task_id)
            if current.status in TERMINAL_TASK_STATUSES or current.status == TaskStatus.PAUSED:
                return
            if current.status == TaskStatus.WAITING:
                current, _ = await self._persist(
                    self._store.transition_task(
                        task_id,
                        TaskStatus.CANCELLING,
                        expected_version=current.version,
                        active_runtime_ms=self._stop_active_clock(task_id, current),
                    )
                )
                await self._notify_event()
            failed, _ = await self._persist(
                self._store.transition_task(
                    task_id,
                    TaskStatus.FAILED,
                    expected_version=current.version,
                    active_runtime_ms=self._stop_active_clock(task_id, current),
                    error_code=code,
                    error_message=message,
                )
            )
            del failed
            await self._notify_event()

    async def _cancel_execution(self, task_id: UUID) -> None:
        execution = self._executions.get(task_id)
        if execution is None or execution is asyncio.current_task():
            return
        execution.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await execution

    async def _shutdown_task(self, task_id: UUID) -> None:
        async with self._lock_for(task_id):
            current = await self._store.get_task(task_id)
            if current.status == TaskStatus.WAITING:
                await self._supervisor.stop(task_id, StopIntent.WAITING)
                await self._cancel_execution(task_id)
                return
            if current.status == TaskStatus.CANCELLING:
                await self._supervisor.stop(task_id, StopIntent.SHUTDOWN)
                await self._cancel_execution(task_id)
                cancelled, _ = await self._persist(
                    self._store.transition_task(
                        task_id,
                        TaskStatus.CANCELLED,
                        expected_version=current.version,
                    )
                )
                del cancelled
                await self._notify_event()
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
                        active_runtime_ms=self._stop_active_clock(task_id, current),
                    )
                )
                await self._notify_event()
            await self._supervisor.stop(task_id, StopIntent.SHUTDOWN)
            await self._cancel_execution(task_id)
            paused, _ = await self._persist(
                self._store.transition_task(
                    task_id,
                    TaskStatus.PAUSED,
                    expected_version=intermediate.version,
                    active_runtime_ms=intermediate.active_runtime_ms,
                    pause_reason="backend_shutdown",
                )
            )
            del paused
            await self._notify_event()

    async def _reconcile_startup(self) -> None:
        for task in await self._store.recoverable_tasks():
            if task.status == TaskStatus.QUEUED:
                await self._scheduler.enqueue(task.id)
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
                await self._notify_event()
            elif task.status == TaskStatus.CANCELLING:
                await self._persist(
                    self._store.transition_task(
                        task.id,
                        TaskStatus.CANCELLED,
                        expected_version=task.version,
                    )
                )
                await self._notify_event()

    def _start_active_clock(self, task_id: UUID, task: TaskRecord) -> None:
        self._active_base_ms[task_id] = task.active_runtime_ms
        self._active_started[task_id] = time.monotonic()

    def _current_active_ms(self, task_id: UUID, task: TaskRecord) -> int:
        started = self._active_started.get(task_id)
        base = self._active_base_ms.get(task_id, task.active_runtime_ms)
        if started is None:
            return base
        elapsed = max(1, int((time.monotonic() - started) * 1000))
        return base + elapsed

    def _stop_active_clock(self, task_id: UUID, task: TaskRecord) -> int:
        total = self._current_active_ms(task_id, task)
        self._active_started.pop(task_id, None)
        self._active_base_ms[task_id] = total
        return total

    def _remaining_seconds(self, task: TaskRecord) -> float:
        remaining_ms = task.timeout_seconds * 1000 - self._current_active_ms(task.id, task)
        if remaining_ms <= 0:
            raise TimeoutError
        return remaining_ms / 1000

    @staticmethod
    def _agent_end_has_model_error(payload: object) -> bool:
        """Read only the final assistant stop reason from one Pi agent_end event."""

        if not isinstance(payload, dict):
            return False
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return False
        return any(
            isinstance(message, dict)
            and message.get("role") == "assistant"
            and message.get("stopReason") == "error"
            for message in reversed(messages)
        )

    def _lock_for(self, task_id: UUID) -> asyncio.Lock:
        return self._task_locks.setdefault(task_id, asyncio.Lock())

    @staticmethod
    def _require_version(task: TaskRecord, expected_version: int) -> None:
        if task.version != expected_version:
            raise TaskVersionConflict(expected_version, task.version)

    def _require_available(self) -> None:
        if not self._started or self._closing or self._fatal_error is not None:
            raise TaskRuntimeUnavailable("Task runtime is unavailable or shutting down.")

    async def _persist(self, operation):
        """Run one Store write and enter fail-stop on durability loss."""

        try:
            return await operation
        except TaskPersistenceError as error:
            await self._enter_fail_stop(error)
            raise TaskRuntimeUnavailable(
                "Task persistence failed; the runtime stopped admission."
            ) from error

    async def _enter_fail_stop(self, error: TaskPersistenceError) -> None:
        """Stop admission and reap every worker after a durable Store failure."""

        if self._fatal_error is not None:
            return
        self._fatal_error = error
        logger.critical("Task runtime entered persistence fail-stop", exc_info=error)
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

    async def _notify_event(self) -> None:
        async with self._event_condition:
            self._event_revision += 1
            self._event_condition.notify_all()


__all__ = ["QUESTION_MARKER", "TaskManager", "TaskRuntimeUnavailable"]
