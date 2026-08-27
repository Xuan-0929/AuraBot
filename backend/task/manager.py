"""提供由单一 owner 管理的本地 Task runtime 与公开命令门面。

TaskManager 自己拥有同步 Store、SQLite FIFO dispatcher、最多五个 TaskRun、Pi handle、
active clock、recovery、fail-stop 和 SSE 唤醒。Gateway 只调用本类；process.py 负责进程
边界，rpc.py 负责严格 JSONL，其他模块都不保存 Task 运行状态。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TypeVar
from uuid import UUID

from .models import (
    CreateTaskCommand,
    TERMINAL_TASK_STATUSES,
    TaskCommandError,
    TaskErrorCode,
    TaskEvent,
    TaskPage,
    TaskRecord,
    TaskRuntimeUnavailable,
    TaskStateConflict,
    TaskStatus,
    TaskVersionConflict,
)
from .process import PiProcess, PiProcessStartError, remove_session, start_pi, stop_pi
from .rpc import PiProtocolError, agent_end_has_model_error, dialog_response_id, parse_question_request
from .settings import TaskSettings
from .store import TaskPersistenceError, TaskStore

logger = logging.getLogger(__name__)
MAX_ACTIVE_TASKS = 5
# ponytail: five idle Pi 0.84.2 processes measured ~656 MiB RSS. Keep this fixed
# ceiling until observed resource pressure proves adaptive admission is necessary.
RESUME_PROMPT = (
    "Resume the existing AuraBot Task session. Inspect the persisted session context, "
    "completed Tool results, and the original goal before continuing safely."
)
_Result = TypeVar("_Result")

@dataclass(slots=True)
class TaskRun:
    task_id: UUID
    worker: asyncio.Task[None] | None
    pi: PiProcess | None
    active_runtime_ms: int
    active_started_at: float | None


class TaskManager:
    def __init__(self, settings: TaskSettings) -> None:
        self._settings = settings
        self._store = TaskStore(settings.database_path)
        self._dispatcher: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        # ponytail: one lock intentionally serializes at most five teardowns. Add
        # run-local locks only if measured command latency becomes unacceptable.
        self._state_lock = asyncio.Lock()
        self._runs: dict[UUID, TaskRun] = {}
        self._event_condition = asyncio.Condition()
        self._event_revision = 0
        self._started = False
        self._closing = False
        self._fatal_error: BaseException | None = None
        self._fail_stop_task: asyncio.Task[None] | None = None

    async def startup(self) -> None:
        if self._started:
            return
        self._store.initialize()
        try:
            for task_id in self._store.deleted_task_ids():
                remove_session(self._settings, task_id)
            for task in self._store.recoverable_tasks():
                if task.status != TaskStatus.RUNNING:
                    continue
                session = self._settings.session_directory(task.id)
                if session.is_dir() and not session.is_symlink():
                    await self._commit(lambda task=task: self._store.transition_task(
                        task.id, TaskStatus.PAUSED, expected_version=task.version,
                        active_runtime_ms=task.active_runtime_ms,
                        pause_reason="backend_restarted",
                    ))
                else:
                    await self._commit(lambda task=task: self._store.transition_task(
                        task.id, TaskStatus.FAILED, expected_version=task.version,
                        active_runtime_ms=task.active_runtime_ms,
                        error_code=TaskErrorCode.SESSION_MISSING,
                        error_message="Task session is missing after Backend restart.",
                    ))
        except BaseException:
            self._store.close()
            raise
        self._started = True
        self._dispatcher = asyncio.create_task(self._dispatch_loop(), name="task-dispatcher")
        self._wake.set()

    async def shutdown(self) -> None:
        if not self._started or self._closing:
            return
        self._closing = True
        self._wake.set()
        if self._dispatcher is not None:
            self._dispatcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._dispatcher
        if self._fail_stop_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._fail_stop_task
        async with self._state_lock:
            runs = tuple(self._runs.values())
            for run in runs:
                if run.worker is not None:
                    run.worker.cancel()
        async with asyncio.timeout(self._settings.shutdown_timeout_seconds):
            await asyncio.gather(
                *(run.worker for run in runs if run.worker is not None),
                return_exceptions=True,
            )
        async with self._state_lock:
            for run in runs:
                task = self._store.get_task(run.task_id)
                if task.status == TaskStatus.RUNNING:
                    await self._commit(lambda task=task, run=run: self._store.transition_task(
                        task.id, TaskStatus.PAUSED, expected_version=task.version,
                        active_runtime_ms=self._active_ms(run),
                        pause_reason="backend_shutdown",
                    ))
        self._store.close()
        self._started = False
        async with self._event_condition:
            self._event_condition.notify_all()

    async def create_task(self, command: CreateTaskCommand) -> TaskRecord:
        command, working_directory = self._validate_create_command(command)
        timeout = command.timeout_seconds
        if timeout is None:
            timeout = self._settings.default_timeout_seconds
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 86_400:
            raise TaskCommandError("timeout_seconds must be an integer from 1 to 86400.")
        async with self._state_lock:
            self._require_available()
            task = await self._commit(lambda: self._store.create_task(
                replace(command, timeout_seconds=timeout), working_directory
            ))
            self._wake.set()
            return task

    def _validate_create_command(self, command: CreateTaskCommand) -> tuple[CreateTaskCommand, Path]:
        """Normalize all Task admission fields before durable storage or process ownership."""

        def text(name: str, value: object, maximum: int) -> str:
            if not isinstance(value, str) or not (normalized := value.strip()):
                raise TaskCommandError(f"{name} must be a non-empty string.")
            if len(normalized) > maximum:
                raise TaskCommandError(f"{name} must be at most {maximum} characters.")
            return normalized

        title = text("title", command.title, 200)
        prompt = text("prompt", command.prompt, 200_000)
        owner_profile_id = text("owner_profile_id", command.owner_profile_id, 128)
        idempotency_key = text("idempotency_key", command.idempotency_key, 200)
        try:
            working_directory = self._settings.workspace_registry.require(command.workspace_id)
        except ValueError as exc:
            raise TaskCommandError(str(exc)) from exc
        return (
            replace(
                command,
                title=title,
                prompt=prompt,
                workspace_id=command.workspace_id.strip(),
                owner_profile_id=owner_profile_id,
                idempotency_key=idempotency_key,
            ),
            Path(working_directory),
        )

    async def list_tasks(
        self, *, after_row_id: int | None = None, limit: int = 50,
        statuses: Iterable[TaskStatus] | None = None,
    ) -> TaskPage:
        return self._store.list_tasks(
            after_row_id=after_row_id, limit=limit, statuses=statuses
        )

    async def get_task(
        self, task_id: UUID, include_deleted: bool = False
    ) -> TaskRecord:
        return self._store.get_task(task_id, include_deleted)

    async def pause_task(self, task_id: UUID, expected_version: int) -> TaskRecord:
        async with self._state_lock:
            self._require_available()
            current = self._store.get_task(task_id)
            if current.status == TaskStatus.PAUSED:
                return current
            self._require_version(current, expected_version)
            if current.status in {TaskStatus.QUEUED, TaskStatus.WAITING}:
                paused, _ = await self._commit(lambda: self._store.transition_task(
                    task_id, TaskStatus.PAUSED, expected_version=current.version,
                    active_runtime_ms=current.active_runtime_ms,
                    pause_reason="user_paused",
                ))
                return paused
            if current.status != TaskStatus.RUNNING:
                raise TaskStateConflict(current.status, TaskStatus.PAUSED)
            run = self._runs.get(task_id)
            if run is None:
                raise TaskRuntimeUnavailable("Running Task has no owned process.")
            await self._stop_run(run)
            current = self._store.get_task(task_id)
            paused, _ = await self._commit(lambda: self._store.transition_task(
                task_id, TaskStatus.PAUSED, expected_version=current.version,
                active_runtime_ms=self._active_ms(run), pause_reason="user_paused",
            ))
            return paused

    async def resume_task(self, task_id: UUID, expected_version: int) -> TaskRecord:
        async with self._state_lock:
            self._require_available()
            current = self._store.get_task(task_id)
            if current.status == TaskStatus.QUEUED:
                return current
            self._require_version(current, expected_version)
            if current.status != TaskStatus.PAUSED:
                raise TaskStateConflict(current.status, TaskStatus.QUEUED)
            target = (
                TaskStatus.WAITING
                if current.question_id is not None and current.question_answer is None
                else TaskStatus.QUEUED
            )
            resumed, _ = await self._commit(lambda: self._store.transition_task(
                task_id, target, expected_version=current.version,
                active_runtime_ms=current.active_runtime_ms,
            ))
            if target == TaskStatus.QUEUED:
                self._wake.set()
            return resumed

    async def cancel_task(self, task_id: UUID, expected_version: int) -> TaskRecord:
        async with self._state_lock:
            self._require_available()
            current = self._store.get_task(task_id)
            if current.status == TaskStatus.CANCELLED:
                return current
            self._require_version(current, expected_version)
            if current.status in TERMINAL_TASK_STATUSES:
                raise TaskStateConflict(current.status, TaskStatus.CANCELLED)
            runtime = current.active_runtime_ms
            if current.status == TaskStatus.RUNNING:
                run = self._runs.get(task_id)
                if run is None:
                    raise TaskRuntimeUnavailable("Running Task has no owned process.")
                await self._stop_run(run)
                current, runtime = self._store.get_task(task_id), self._active_ms(run)
            cancelled, _ = await self._commit(lambda: self._store.transition_task(
                task_id, TaskStatus.CANCELLED, expected_version=current.version,
                active_runtime_ms=runtime,
            ))
            return cancelled

    async def delete_task(self, task_id: UUID) -> None:
        async with self._state_lock:
            self._require_available()
            await self._commit(lambda: self._store.tombstone_task(task_id))
            remove_session(self._settings, task_id)

    async def answer_question(
        self, task_id: UUID, question_id: UUID, answer: str,
        expected_version: int,
    ) -> TaskRecord:
        async with self._state_lock:
            self._require_available()
            queued, _ = await self._commit(lambda: self._store.answer_question(
                task_id, question_id, answer, expected_version=expected_version
            ))
            self._wake.set()
            return queued

    async def iter_events(
        self, after_event_id: int, task_id: UUID | None = None,
    ) -> AsyncIterator[TaskEvent]:
        cursor = after_event_id
        while True:
            revision = self._event_revision
            events = self._store.events_after(cursor, task_id, 200)
            if events:
                for event in events:
                    cursor = event.event_id
                    yield event
                continue
            if self._closing:
                return
            async with self._event_condition:
                if revision == self._event_revision and not self._closing:
                    await self._event_condition.wait()

    async def _dispatch_loop(self) -> None:
        """
        持续监听是否有任务队列变动。

        _wake表示一个钟。别的协程进行了可能改变任务队列状态的动作就敲钟(_wake.set())。
        可能改变任务队列状态的事件举例：新任务入队、任务被暂停、任务被取消、任务被删除、任务回答了问题等。

        这里持续等着敲钟(await _wake.wait())，发现敲钟就进后面的loop。

        发布任务时，调用create_task()。create_task的任务就是下面的_run_task()。
        """
        
        try:
            while True:
                await self._wake.wait()
                self._wake.clear()
                while True:
                    async with self._state_lock:  # 整个TaskManager共用同一把大锁。
                        if self._closing or self._fatal_error is not None:
                            return
                        free = MAX_ACTIVE_TASKS - len(self._runs)
                        if free <= 0:
                            break
                        claimed = await self._commit(
                            lambda: self._store.claim_queued(free)
                        )
                        for task in claimed:
                            run = TaskRun(
                                task.id, None, None, task.active_runtime_ms, time.monotonic()
                            )
                            self._runs[task.id] = run
                            run.worker = asyncio.create_task(
                                self._run_task(run, task), name=f"task-worker-{task.id}"
                            )
                    if not claimed:
                        break
        except asyncio.CancelledError:
            raise
        except TaskRuntimeUnavailable:
            return
        except Exception as error:
            logger.exception("Task dispatcher failed")
            self._begin_fail_stop(error)

    async def _run_task(self, run: TaskRun, task: TaskRecord) -> None:
        """
        _dispatch_loop()里创建的协程。每个任务一个协程。

        这里负责任务环境的铺垫。执行Loop在下面的_execute()里。两个函数就是协程中的生命周期。
        """

        try:
            remaining = task.timeout_seconds - task.active_runtime_ms / 1000
            if remaining <= 0:
                raise TimeoutError
            async with asyncio.timeout(remaining):
                await self._execute(run, task)
        except asyncio.CancelledError:
            raise
        except TaskRuntimeUnavailable:
            return
        except TimeoutError:
            await self._fail_run(run, TaskErrorCode.TIMEOUT,
                                 "Task exceeded its active runtime timeout.")
        except PiProcessStartError as error:
            await self._fail_run(run, error.code, str(error))
        except PiProtocolError:
            await self._fail_run(run, TaskErrorCode.PROTOCOL_ERROR,
                                 "Pi violated the RPC protocol.")
        except Exception:
            logger.exception("Task worker failed", extra={"task_id": str(run.task_id)})
            await self._fail_run(run, TaskErrorCode.INTERNAL_ERROR,
                                 "Task execution failed internally.")
        finally:
            if run.pi is not None:
                pi, run.pi = run.pi, None
                with contextlib.suppress(BaseException):
                    await stop_pi(pi)
            if self._runs.get(run.task_id) is run:
                self._runs.pop(run.task_id, None)
            self._wake.set()

    async def _execute(self, run: TaskRun, task: TaskRecord) -> None:
        prompt = self._execution_prompt(task)
        run.pi = await start_pi(self._settings, task, prompt)
        if task.question_answer is not None:
            async with self._state_lock:
                current = self._store.get_task(task.id)
                await self._commit(lambda: self._store.clear_question(
                    task.id, expected_version=current.version
                ))
        model_failed = False
        async for event in run.pi.rpc.events():
            if event.type == "text_output":
                text = event.payload.get("text")
                if isinstance(text, str) and text:
                    async with self._state_lock:
                        await self._commit(lambda text=text: self._store.append_output(
                            task.id, text
                        ))
            elif event.type == "extension_ui_request":
                question = parse_question_request(event.payload)
                if question is None:
                    request_id = dialog_response_id(event.payload)
                    if request_id is not None:
                        await run.pi.rpc.send_extension_ui_response(request_id, None)
                    continue
                text, options = question
                async with self._state_lock:
                    current = self._store.get_task(task.id)
                    active = self._active_ms(run)
                    await self._commit(lambda: self._store.raise_question(
                        task.id, text, options, expected_version=current.version,
                        active_runtime_ms=active,
                    ))
                    run.active_runtime_ms, run.active_started_at = active, None
                    pi, run.pi = run.pi, None
                    await stop_pi(pi)
                return
            elif event.type == "extension_error":
                raise PiProcessStartError(
                    TaskErrorCode.RESOURCE_LOAD_FAILED,
                    "A Pi extension failed while running the Task.",
                )
            elif event.type == "agent_end":
                model_failed = agent_end_has_model_error(event.payload)
            elif event.type == "agent_settled":
                if model_failed:
                    await self._fail_run(run, TaskErrorCode.MODEL_ERROR,
                                         "Pi model execution failed.")
                    return
                result = await run.pi.last_assistant_text()
                pi, run.pi = run.pi, None
                await stop_pi(pi, abort=False)
                if result is None:
                    await self._fail_run(run, TaskErrorCode.RESULT_UNAVAILABLE,
                                         "Pi completed without a final assistant result.")
                    return
                async with self._state_lock:
                    current = self._store.get_task(task.id)
                    await self._commit(lambda: self._store.transition_task(
                        task.id, TaskStatus.COMPLETED, expected_version=current.version,
                        active_runtime_ms=self._active_ms(run), result_text=result,
                    ))
                return
        raise PiProcessStartError(
            TaskErrorCode.UNEXPECTED_EXIT,
            "Pi exited before the Task reached agent_settled.",
        )

    def _execution_prompt(self, task: TaskRecord) -> str:
        session = self._settings.session_directory(task.id)
        first_claim = task.active_runtime_ms == 0 and task.started_at == task.updated_at
        if task.question_answer is not None:
            if not session.is_dir() or session.is_symlink():
                raise PiProcessStartError(TaskErrorCode.SESSION_MISSING,
                                          "Task session is missing before continuation.")
            return f"{RESUME_PROMPT}\nThe Main Agent answered the pending question: {task.question_answer}"
        if session.is_dir() and not session.is_symlink():
            return RESUME_PROMPT
        if first_claim:
            return task.prompt
        raise PiProcessStartError(TaskErrorCode.SESSION_MISSING,
                                  "Task session is missing before continuation.")

    async def _fail_run(
        self, run: TaskRun, code: TaskErrorCode, message: str,
    ) -> None:
        if run.pi is not None:
            pi, run.pi = run.pi, None
            await stop_pi(pi)
        async with self._state_lock:
            current = self._store.get_task(run.task_id)
            if current.status != TaskStatus.RUNNING:
                return
            await self._commit(lambda: self._store.transition_task(
                run.task_id, TaskStatus.FAILED, expected_version=current.version,
                active_runtime_ms=self._active_ms(run), error_code=code,
                error_message=message,
            ))

    async def _stop_run(self, run: TaskRun) -> None:
        if run.worker is not None and run.worker is not asyncio.current_task():
            run.worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await run.worker
        if run.pi is not None:
            pi, run.pi = run.pi, None
            await stop_pi(pi)
        if self._runs.get(run.task_id) is run:
            self._runs.pop(run.task_id, None)
        self._wake.set()

    def _active_ms(self, run: TaskRun) -> int:
        if run.active_started_at is None:
            return run.active_runtime_ms
        return run.active_runtime_ms + max(1, int((time.monotonic() - run.active_started_at) * 1000))

    async def _commit(self, operation: Callable[[], _Result]) -> _Result:
        try:
            result = operation()
        except TaskPersistenceError as error:
            self._begin_fail_stop(error)
            raise TaskRuntimeUnavailable(
                "Task persistence failed; the runtime stopped admission."
            ) from error
        async with self._event_condition:
            self._event_revision += 1
            self._event_condition.notify_all()
        return result

    def _begin_fail_stop(self, error: BaseException) -> None:
        if self._fatal_error is not None:
            return
        self._fatal_error = error
        self._wake.set()
        current = asyncio.current_task()
        if self._dispatcher is not None and self._dispatcher is not current:
            self._dispatcher.cancel()
        self._fail_stop_task = asyncio.create_task(self._reap_fail_stop(), name="task-fail-stop")

    async def _reap_fail_stop(self) -> None:
        async with self._state_lock:
            runs = tuple(self._runs.values())
            current = asyncio.current_task()
            for run in runs:
                if run.worker is not None and run.worker is not current:
                    run.worker.cancel()
        await asyncio.gather(
            *(run.worker for run in runs if run.worker is not None),
            return_exceptions=True,
        )

    def _require_available(self) -> None:
        if not self._started or self._closing or self._fatal_error is not None:
            raise TaskRuntimeUnavailable("Task runtime is unavailable or shutting down.")

    @staticmethod
    def _require_version(task: TaskRecord, expected: int) -> None:
        if task.version != expected:
            raise TaskVersionConflict(expected, task.version)

__all__ = ["MAX_ACTIVE_TASKS", "TaskManager", "TaskRun"]
