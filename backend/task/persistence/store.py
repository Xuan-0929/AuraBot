"""
持久化 AuraBot Task 产品状态、问题与可重放业务事件。

TaskStore 独占一份 Python-owned SQLite，使用单连接写锁和显式事务保证状态与事件原子
提交；它不决定状态图、不保存 Pi raw JSON，也不访问 Electron/Memory 数据库或 Task
session 文件系统。session quarantine 与 tombstone 协调由 persistence.sessions 负责。
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path
from typing import Any, TypeVar
from uuid import UUID, uuid4

import aiosqlite

from ..models import (
    CreateTaskCommand,
    TERMINAL_TASK_STATUSES,
    TaskErrorCode,
    TaskCommandError,
    TaskEvent,
    TaskNotFound,
    TaskPage,
    TaskQuestion,
    TaskRecord,
    TaskStateConflict,
    TaskStatus,
    TaskVersionConflict,
    validate_status_transition,
)
from . import events as event_ops
from . import questions as question_ops
from .codec import dump_time, event_from_row, now, task_from_row
from .schema import TaskStoreMigrationError, initialize_schema


class TaskPersistenceError(RuntimeError):
    """Raised when a Task write cannot be durably committed to SQLite."""


WriteResult = TypeVar("WriteResult")


class TaskStore:
    """Concrete asynchronous Task SQLite store with no interface indirection."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.resolve()
        self._connection: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Open, migrate and verify the dedicated database idempotently."""

        if self._connection is not None:
            return
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = await aiosqlite.connect(self.database_path)
        connection.row_factory = aiosqlite.Row
        try:
            await connection.execute("PRAGMA journal_mode = WAL")
            await connection.execute("PRAGMA foreign_keys = ON")
            await connection.execute("PRAGMA busy_timeout = 5000")
            await initialize_schema(connection)
        except BaseException:
            await connection.close()
            raise
        self._connection = connection

    async def close(self) -> None:
        """Close the SQLite connection idempotently."""

        connection, self._connection = self._connection, None
        if connection is not None:
            await connection.close()

    def _db(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("TaskStore is not initialized.")
        return self._connection

    async def _write(
        self,
        operation: Callable[[aiosqlite.Connection], Awaitable[WriteResult]],
    ) -> WriteResult:
        """Run one Store write under the shared lock and transaction boundary."""

        async with self._write_lock:
            connection = self._db()
            try:
                await connection.execute("BEGIN IMMEDIATE")
                result = await operation(connection)
                await connection.commit()
                return result
            except BaseException as error:
                await connection.rollback()
                if isinstance(error, sqlite3.Error):
                    raise TaskPersistenceError(
                        "The Task database rejected a state or event write."
                    ) from error
                raise

    async def create_task(self, command: CreateTaskCommand) -> TaskRecord:
        """Insert one queued Task and its task.created event atomically."""

        if (
            command.timeout_seconds is None
            or isinstance(command.timeout_seconds, bool)
            or not 1 <= command.timeout_seconds <= 86_400
        ):
            raise TaskCommandError("timeout_seconds must be an integer from 1 to 86400.")

        task_id = uuid4()
        occurred_at = now()

        async def operation(connection: aiosqlite.Connection) -> TaskRecord:
            if command.rerun_of_task_id is not None:
                rerun = await self._fetch_task_row(
                    command.rerun_of_task_id, include_deleted=False
                )
                if TaskStatus(rerun["status"]) not in TERMINAL_TASK_STATUSES:
                    raise TaskStateConflict(
                        TaskStatus(rerun["status"]), TaskStatus.QUEUED
                    )
            cursor = await connection.execute(
                """
                INSERT INTO tasks(
                    id, title, prompt, working_directory, status,
                    timeout_seconds, active_runtime_ms, rerun_of_task_id,
                    version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, 1, ?, ?)
                """,
                (
                    str(task_id),
                    command.title,
                    command.prompt,
                    str(command.working_directory),
                    TaskStatus.QUEUED.value,
                    command.timeout_seconds,
                    str(command.rerun_of_task_id)
                    if command.rerun_of_task_id
                    else None,
                    dump_time(occurred_at),
                    dump_time(occurred_at),
                ),
            )
            await event_ops.insert_event(
                connection,
                task_id,
                1,
                "task.created",
                occurred_at,
                {"status": TaskStatus.QUEUED.value},
            )
            row = await self._fetch_task_row(task_id, include_deleted=True)
            assert int(row["row_id"]) == cursor.lastrowid
            return task_from_row(row)

        return await self._write(operation)

    async def get_task(
        self, task_id: UUID, include_deleted: bool = False
    ) -> TaskRecord:
        """Return a Task snapshot or hide logical deletions by default."""

        return task_from_row(
            await self._fetch_task_row(task_id, include_deleted=include_deleted)
        )

    async def _fetch_task_row(
        self, task_id: UUID, *, include_deleted: bool
    ) -> aiosqlite.Row:
        clause = "" if include_deleted else " AND deleted_at IS NULL"
        cursor = await self._db().execute(
            f"SELECT * FROM tasks WHERE id = ?{clause}", (str(task_id),)
        )
        row = await cursor.fetchone()
        if row is None:
            raise TaskNotFound(f"Task not found: {task_id}")
        return row

    async def list_tasks(
        self,
        *,
        after_row_id: int | None = None,
        limit: int = 50,
        statuses: Iterable[TaskStatus] | None = None,
    ) -> TaskPage:
        """List visible Tasks in FIFO row order with an integer cursor."""

        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        parameters: list[Any] = [after_row_id or 0]
        where = ["deleted_at IS NULL", "row_id > ?"]
        status_values = tuple(status.value for status in (statuses or ()))
        if status_values:
            where.append(f"status IN ({','.join('?' for _ in status_values)})")
            parameters.extend(status_values)
        parameters.append(limit + 1)
        cursor = await self._db().execute(
            f"SELECT * FROM tasks WHERE {' AND '.join(where)} ORDER BY row_id LIMIT ?",
            parameters,
        )
        rows = await cursor.fetchall()
        has_more = len(rows) > limit
        visible = rows[:limit]
        return TaskPage(
            items=tuple(task_from_row(row) for row in visible),
            next_cursor=int(visible[-1]["row_id"]) if has_more and visible else None,
        )

    async def transition_task(
        self,
        task_id: UUID,
        target_status: TaskStatus,
        *,
        expected_version: int,
        active_runtime_ms: int | None = None,
        pi_session_id: str | None = None,
        pi_session_path: Path | None = None,
        result_text: str | None = None,
        error_code: TaskErrorCode | None = None,
        error_message: str | None = None,
        pause_reason: str | None = None,
    ) -> tuple[TaskRecord, TaskEvent]:
        """CAS one validated status change and task.status_changed event."""

        async def operation(
            connection: aiosqlite.Connection,
        ) -> tuple[TaskRecord, TaskEvent]:
            current_row = await self._fetch_task_row(task_id, include_deleted=False)
            actual_version = int(current_row["version"])
            if actual_version != expected_version:
                raise TaskVersionConflict(expected_version, actual_version)
            current = TaskStatus(current_row["status"])
            validate_status_transition(current, target_status)
            occurred_at = now()
            started_at = current_row["started_at"]
            if target_status == TaskStatus.STARTING and started_at is None:
                started_at = dump_time(occurred_at)
            finished_at = current_row["finished_at"]
            if target_status in TERMINAL_TASK_STATUSES:
                finished_at = dump_time(occurred_at)
            new_version = actual_version + 1
            cursor = await connection.execute(
                """
                UPDATE tasks SET
                    status = ?, active_runtime_ms = ?,
                    pi_session_id = COALESCE(?, pi_session_id),
                    pi_session_path = COALESCE(?, pi_session_path),
                    result_text = ?, error_code = ?, error_message = ?,
                    pause_reason = ?, version = ?, updated_at = ?,
                    started_at = ?, finished_at = ?
                WHERE id = ? AND version = ? AND deleted_at IS NULL
                """,
                (
                    target_status.value,
                    active_runtime_ms
                    if active_runtime_ms is not None
                    else int(current_row["active_runtime_ms"]),
                    pi_session_id,
                    str(pi_session_path) if pi_session_path else None,
                    result_text,
                    error_code.value if error_code else None,
                    error_message,
                    pause_reason,
                    new_version,
                    dump_time(occurred_at),
                    started_at,
                    finished_at,
                    str(task_id),
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                refreshed = await self._fetch_task_row(task_id, include_deleted=False)
                raise TaskVersionConflict(expected_version, int(refreshed["version"]))
            event = await event_ops.insert_event(
                connection,
                task_id,
                await event_ops.next_sequence(connection, task_id),
                "task.status_changed",
                occurred_at,
                {"from": current.value, "to": target_status.value},
            )
            return await self.get_task(task_id), event

        return await self._write(operation)

    async def append_output(self, task_id: UUID, text: str) -> TaskEvent:
        """Append one bounded product output chunk as an audit event."""

        async def operation(connection: aiosqlite.Connection) -> TaskEvent:
            await self._fetch_task_row(task_id, include_deleted=False)
            return await event_ops.insert_event(
                connection,
                task_id,
                await event_ops.next_sequence(connection, task_id),
                "task.output_appended",
                now(),
                {"text": text},
            )

        return await self._write(operation)

    async def raise_question(
        self,
        task_id: UUID,
        question: str,
        options: tuple[str, ...],
        rpc_request_id: str | None,
        *,
        expected_version: int,
        active_runtime_ms: int | None = None,
    ) -> tuple[TaskRecord, TaskQuestion, TaskEvent]:
        """Move a running Task to waiting while persisting its question atomically."""

        async def operation(
            connection: aiosqlite.Connection,
        ) -> tuple[TaskRecord, TaskQuestion, TaskEvent]:
            return await question_ops.raise_question(
                connection,
                task_id,
                question,
                options,
                rpc_request_id,
                expected_version=expected_version,
                active_runtime_ms=active_runtime_ms,
            )

        return await self._write(operation)

    async def answer_question(
        self,
        task_id: UUID,
        question_id: UUID,
        answer: str,
        *,
        expected_version: int,
    ) -> tuple[TaskRecord, TaskQuestion, TaskEvent]:
        """Persist an answer while leaving scheduling of the waiting Task to Manager."""

        async def operation(
            connection: aiosqlite.Connection,
        ) -> tuple[TaskRecord, TaskQuestion, TaskEvent]:
            return await question_ops.answer_question(
                connection,
                task_id,
                question_id,
                answer,
                expected_version=expected_version,
            )

        return await self._write(operation)

    async def pending_question(self, task_id: UUID) -> TaskQuestion | None:
        """Return the oldest unanswered question for a Task."""

        return await question_ops.pending_question(self._db(), task_id)

    async def undelivered_answer(self, task_id: UUID) -> TaskQuestion | None:
        """Return the oldest accepted answer that has not yet reached Pi."""

        return await question_ops.undelivered_answer(self._db(), task_id)

    async def deliver_answer_and_transition(
        self,
        task_id: UUID,
        question_id: UUID,
        target_status: TaskStatus,
        *,
        expected_version: int,
    ) -> tuple[TaskRecord, TaskQuestion, TaskEvent]:
        """Atomically mark one answer delivered and advance its waiting/starting Task."""

        async def operation(
            connection: aiosqlite.Connection,
        ) -> tuple[TaskRecord, TaskQuestion, TaskEvent]:
            return await question_ops.deliver_answer_and_transition(
                connection,
                task_id,
                question_id,
                target_status,
                expected_version=expected_version,
            )

        return await self._write(operation)

    async def tombstone_task(self, task_id: UUID) -> tuple[TaskRecord, TaskEvent]:
        """Commit one terminal Task tombstone without touching its session files."""

        task = await self.get_task(task_id, include_deleted=True)
        if task.deleted_at is not None:
            row = await (
                await self._db().execute(
                    """SELECT * FROM task_events WHERE task_id = ? AND event_type = 'task.deleted'
                       ORDER BY event_id DESC LIMIT 1""",
                    (str(task_id),),
                )
            ).fetchone()
            if row is None:
                raise TaskPersistenceError("Deleted Task has no deletion event.")
            return task, event_from_row(row)
        if task.status not in TERMINAL_TASK_STATUSES:
            raise TaskStateConflict(task.status, TaskStatus.CANCELLED)

        async def operation(
            connection: aiosqlite.Connection,
        ) -> tuple[TaskRecord, TaskEvent]:
            current = await self._fetch_task_row(task_id, include_deleted=True)
            if current["deleted_at"] is not None:
                row = await (
                    await connection.execute(
                        """SELECT * FROM task_events
                           WHERE task_id = ? AND event_type = 'task.deleted'
                           ORDER BY event_id DESC LIMIT 1""",
                        (str(task_id),),
                    )
                ).fetchone()
                if row is None:
                    raise TaskPersistenceError("Deleted Task has no deletion event.")
                return task_from_row(current), event_from_row(row)
            occurred_at = now()
            await connection.execute(
                """
                UPDATE tasks SET deleted_at = ?, updated_at = ?, version = version + 1
                WHERE id = ? AND deleted_at IS NULL
                """,
                (dump_time(occurred_at), dump_time(occurred_at), str(task_id)),
            )
            event = await event_ops.insert_event(
                connection,
                task_id,
                await event_ops.next_sequence(connection, task_id),
                "task.deleted",
                occurred_at,
                {"status": current["status"]},
            )
            return await self.get_task(task_id, include_deleted=True), event

        return await self._write(operation)

    async def events_after(
        self,
        event_id: int,
        task_id: UUID | None,
        limit: int,
    ) -> list[TaskEvent]:
        """Replay ordered business events after a global SSE cursor."""

        return await event_ops.events_after(self._db(), event_id, task_id, limit)

    async def recoverable_tasks(self) -> list[TaskRecord]:
        """Return non-terminal, non-deleted Tasks in original FIFO order."""

        terminal = tuple(status.value for status in TERMINAL_TASK_STATUSES)
        rows = await (
            await self._db().execute(
                f"""
                SELECT * FROM tasks
                WHERE deleted_at IS NULL AND status NOT IN ({','.join('?' for _ in terminal)})
                ORDER BY row_id
                """,
                terminal,
            )
        ).fetchall()
        return [task_from_row(row) for row in rows]


__all__ = [
    "TaskPersistenceError",
    "TaskStore",
    "TaskStoreMigrationError",
]
