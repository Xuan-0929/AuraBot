"""提供 AuraBot Task Control Plane 的稳定业务命令门面。

TaskManager 是 Gateway 和未来 Main Agent 的唯一入口：它校验产品状态与版本，并在
TaskStore、TaskRuntimeEngine 和 TaskSessionDeletion 之间编排命令顺序。FIFO、Pi 进程、
协程 registry、active clock、recovery 与 fail-stop 全部由 Runtime Engine 管理。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from dataclasses import replace
from uuid import UUID

from .models import (
    CreateTaskCommand,
    TERMINAL_TASK_STATUSES,
    TaskCommandError,
    TaskEvent,
    TaskPage,
    TaskRecord,
    TaskRuntimeUnavailable,
    TaskStateConflict,
    TaskStatus,
    TaskVersionConflict,
)
from .persistence.sessions import TaskSessionCleanupError, TaskSessionDeletion
from .persistence.store import TaskPersistenceError, TaskStore
from .runtime.engine import LiveAnswerDelivery, TaskRuntimeEngine
from .runtime.process import StopIntent


class TaskManager:
    """Coordinate stable Task commands across persistence and runtime boundaries."""

    def __init__(
        self,
        default_timeout_seconds: int,
        store: TaskStore,
        runtime: TaskRuntimeEngine,
        session_deletion: TaskSessionDeletion,
    ) -> None:
        self._default_timeout_seconds = default_timeout_seconds
        self._store = store
        self._runtime = runtime
        self._session_deletion = session_deletion
        self._started = False

    async def startup(self) -> None:
        """Initialize Store, recover session deletion, then start Task runtime."""

        if self._started:
            return
        await self._store.initialize()
        try:
            await self._session_deletion.initialize()
            await self._session_deletion.reconcile()
            await self._runtime.startup()
        except BaseException:
            await self._store.close()
            raise
        self._started = True

    async def shutdown(self) -> None:
        """Shut down runtime first and always close the Manager-owned Store."""

        try:
            await self._runtime.shutdown()
        finally:
            await self._store.close()

    async def create_task(self, command: CreateTaskCommand) -> TaskRecord:
        """Persist queued work before notifying the in-memory runtime."""

        self._runtime.require_available()
        timeout = (
            command.timeout_seconds
            if command.timeout_seconds is not None
            else self._default_timeout_seconds
        )
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int)
            or not 1 <= timeout <= 86_400
        ):
            raise TaskCommandError("timeout_seconds must be an integer from 1 to 86400.")
        task = await self._persist(
            self._store.create_task(replace(command, timeout_seconds=timeout))
        )
        await self._runtime.notify_committed_event()
        await self._runtime.enqueue(task.id)
        return task

    async def list_tasks(
        self,
        *,
        after_row_id: int | None = None,
        limit: int = 50,
        statuses: Iterable[TaskStatus] | None = None,
    ) -> TaskPage:
        return await self._store.list_tasks(
            after_row_id=after_row_id,
            limit=limit,
            statuses=statuses,
        )

    async def get_task(
        self, task_id: UUID, include_deleted: bool = False
    ) -> TaskRecord:
        return await self._store.get_task(task_id, include_deleted)

    async def pause_task(self, task_id: UUID, expected_version: int) -> TaskRecord:
        """Pause queued/active/waiting work and guarantee no process remains."""

        async with self._runtime.task_guard(task_id):
            current = await self._store.get_task(task_id)
            if current.status == TaskStatus.PAUSED:
                return current
            self._require_version(current, expected_version)
            if current.status == TaskStatus.QUEUED:
                await self._runtime.remove_queued(task_id)
                paused, _ = await self._persist(
                    self._store.transition_task(
                        task_id,
                        TaskStatus.PAUSED,
                        expected_version=current.version,
                        pause_reason="user_paused",
                    )
                )
                await self._runtime.notify_committed_event()
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
                        active_runtime_ms=self._runtime.stop_active_clock(current),
                    )
                )
                await self._runtime.notify_committed_event()
            await self._runtime.stop_active(task_id, StopIntent.PAUSE)
            paused, _ = await self._persist(
                self._store.transition_task(
                    task_id,
                    TaskStatus.PAUSED,
                    expected_version=intermediate.version,
                    active_runtime_ms=intermediate.active_runtime_ms,
                    pause_reason="user_paused",
                )
            )
            await self._runtime.notify_committed_event()
            return paused

    async def resume_task(self, task_id: UUID, expected_version: int) -> TaskRecord:
        """Return paused work to the queue, or waiting when a question remains."""

        self._runtime.require_available()
        async with self._runtime.task_guard(task_id):
            current = await self._store.get_task(task_id)
            if current.status == TaskStatus.QUEUED:
                return current
            self._require_version(current, expected_version)
            if current.status != TaskStatus.PAUSED:
                raise TaskStateConflict(current.status, TaskStatus.QUEUED)
            if await self._store.pending_question(task_id) is not None:
                waiting, _ = await self._persist(
                    self._store.transition_task(
                        task_id,
                        TaskStatus.WAITING,
                        expected_version=current.version,
                        pause_reason=None,
                    )
                )
                await self._runtime.notify_committed_event()
                return waiting
            queued, _ = await self._persist(
                self._store.transition_task(
                    task_id,
                    TaskStatus.QUEUED,
                    expected_version=current.version,
                    pause_reason=None,
                )
            )
            await self._runtime.notify_committed_event()
            await self._runtime.enqueue(task_id)
            return queued

    async def cancel_task(self, task_id: UUID, expected_version: int) -> TaskRecord:
        """Permanently cancel work, reaping active Pi before terminal commit."""

        async with self._runtime.task_guard(task_id):
            current = await self._store.get_task(task_id)
            if current.status == TaskStatus.CANCELLED:
                return current
            self._require_version(current, expected_version)
            if current.status in TERMINAL_TASK_STATUSES:
                raise TaskStateConflict(current.status, TaskStatus.CANCELLED)

            active_was_stopped = False
            directly_cancellable = current.status in {
                TaskStatus.QUEUED,
                TaskStatus.PAUSED,
            }
            if current.status == TaskStatus.WAITING:
                stopped = await self._runtime.stop_active(task_id, StopIntent.CANCEL)
                active_was_stopped = stopped.returncode is not None
                directly_cancellable = not active_was_stopped
            if directly_cancellable:
                await self._runtime.remove_queued(task_id)
                cancelled, _ = await self._persist(
                    self._store.transition_task(
                        task_id,
                        TaskStatus.CANCELLED,
                        expected_version=current.version,
                        active_runtime_ms=self._runtime.stop_active_clock(current),
                    )
                )
                await self._runtime.notify_committed_event()
                return cancelled

            intermediate = current
            if current.status != TaskStatus.CANCELLING:
                intermediate, _ = await self._persist(
                    self._store.transition_task(
                        task_id,
                        TaskStatus.CANCELLING,
                        expected_version=current.version,
                        active_runtime_ms=self._runtime.stop_active_clock(current),
                    )
                )
                await self._runtime.notify_committed_event()
            if not active_was_stopped:
                await self._runtime.stop_active(task_id, StopIntent.CANCEL)
            cancelled, _ = await self._persist(
                self._store.transition_task(
                    task_id,
                    TaskStatus.CANCELLED,
                    expected_version=intermediate.version,
                    active_runtime_ms=intermediate.active_runtime_ms,
                )
            )
            await self._runtime.notify_committed_event()
            return cancelled

    async def delete_task(self, task_id: UUID) -> None:
        """Delete terminal work through durable tombstone/session coordination."""

        async with self._runtime.task_guard(task_id):
            try:
                await self._persist(self._session_deletion.delete(task_id))
            except TaskSessionCleanupError as error:
                if error.tombstone_committed:
                    await self._runtime.notify_committed_event()
                raise
            await self._runtime.notify_committed_event()

    async def answer_question(
        self,
        task_id: UUID,
        question_id: UUID,
        answer: str,
        expected_version: int,
    ) -> TaskRecord:
        """Persist an answer, then deliver live or queue durable continuation."""

        async with self._runtime.task_guard(task_id):
            waiting, question, _ = await self._persist(
                self._store.answer_question(
                    task_id,
                    question_id,
                    answer,
                    expected_version=expected_version,
                )
            )
            await self._runtime.notify_committed_event()
            delivery = await self._runtime.deliver_live_answer(
                task_id,
                question.rpc_request_id,
                answer,
            )
            if delivery == LiveAnswerDelivery.DELIVERED:
                resumed, _, _ = await self._persist(
                    self._store.deliver_answer_and_transition(
                        task_id,
                        question.id,
                        TaskStatus.RUNNING,
                        expected_version=waiting.version,
                    )
                )
                self._runtime.start_active_clock(resumed)
                await self._runtime.notify_committed_event()
                return resumed
            queued, _ = await self._persist(
                self._store.transition_task(
                    task_id,
                    TaskStatus.QUEUED,
                    expected_version=waiting.version,
                )
            )
            await self._runtime.notify_committed_event()
            await self._runtime.enqueue(task_id)
            return queued

    async def iter_events(
        self,
        after_event_id: int,
        task_id: UUID | None = None,
    ) -> AsyncIterator[TaskEvent]:
        """Replay SQLite events and use Runtime only to wait for new commits."""

        cursor = after_event_id
        while True:
            seen_revision = self._runtime.event_revision
            events = await self._store.events_after(cursor, task_id, 200)
            if events:
                for event in events:
                    cursor = event.event_id
                    yield event
                continue
            if self._runtime.closing:
                return
            await self._runtime.wait_for_event_change(seen_revision)

    @staticmethod
    def _require_version(task: TaskRecord, expected_version: int) -> None:
        if task.version != expected_version:
            raise TaskVersionConflict(expected_version, task.version)

    async def _persist(self, operation):
        """Enter Runtime fail-stop when a Manager-originated write loses durability."""

        try:
            return await operation
        except TaskPersistenceError as error:
            await self._runtime.enter_fail_stop(error)
            raise TaskRuntimeUnavailable(
                "Task persistence failed; the runtime stopped admission."
            ) from error


__all__ = ["TaskManager"]
