"""
持久化 AuraBot Task 产品状态、问题与可重放业务事件。

TaskStore 独占一份 Python-owned SQLite，使用单连接写锁和显式事务保证状态与事件原子
提交；它不决定状态图、不保存 Pi raw JSON，也不访问 Electron/Memory 数据库。逻辑
删除前只清理由 session root 与 Task UUID 推导的受控目录。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import aiosqlite

from .models import (
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

SCHEMA_VERSION = 2
logger = logging.getLogger(__name__)


class TaskStoreMigrationError(RuntimeError):
    """Raised when the on-disk Task schema cannot be safely opened."""


class TaskPersistenceError(RuntimeError):
    """Raised when a Task write cannot be durably committed to SQLite."""


class TaskSessionCleanupError(RuntimeError):
    """Raised when session quarantine cannot converge safely with its tombstone."""

    def __init__(self, *, tombstone_committed: bool) -> None:
        self.tombstone_committed = tombstone_committed
        super().__init__("Task session cleanup failed.")


def _now() -> datetime:
    return datetime.now(UTC)


def _dump_time(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def _load_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


class TaskStore:
    """Concrete asynchronous Task SQLite store with no interface indirection."""

    def __init__(self, database_path: Path, session_root: Path) -> None:
        self.database_path = database_path.resolve()
        self.session_root = session_root.resolve()
        self._connection: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Open, migrate and verify the dedicated database idempotently."""

        if self._connection is not None:
            return
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_root.mkdir(parents=True, exist_ok=True)
        connection = await aiosqlite.connect(self.database_path)
        connection.row_factory = aiosqlite.Row
        try:
            await connection.execute("PRAGMA journal_mode = WAL")
            await connection.execute("PRAGMA foreign_keys = ON")
            await connection.execute("PRAGMA busy_timeout = 5000")
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
            await connection.commit()
            cursor = await connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
            )
            row = await cursor.fetchone()
            current = int(row["version"])
            if current > SCHEMA_VERSION:
                raise TaskStoreMigrationError(
                    f"Task database schema {current} is newer than supported {SCHEMA_VERSION}."
                )
            if current < 1:
                await self._apply_initial_schema(connection)
                current = SCHEMA_VERSION
            if current < 2:
                await self._apply_v2_schema(connection)
            violations = await (await connection.execute("PRAGMA foreign_key_check")).fetchall()
            if violations:
                raise TaskStoreMigrationError(
                    f"Task database foreign key check failed: {len(violations)} violation(s)."
                )
        except BaseException:
            await connection.close()
            raise
        self._connection = connection

    async def _apply_initial_schema(self, connection: aiosqlite.Connection) -> None:
        await connection.execute("BEGIN IMMEDIATE")
        try:
            statements = (
                """CREATE TABLE tasks (
                    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    working_directory TEXT NOT NULL,
                    status TEXT NOT NULL,
                    timeout_seconds INTEGER NOT NULL CHECK (timeout_seconds > 0),
                    active_runtime_ms INTEGER NOT NULL DEFAULT 0 CHECK (active_runtime_ms >= 0),
                    pi_session_id TEXT,
                    pi_session_path TEXT,
                    result_text TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    pause_reason TEXT,
                    rerun_of_task_id TEXT REFERENCES tasks(id),
                    version INTEGER NOT NULL CHECK (version >= 1),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    deleted_at TEXT
                )""",
                "CREATE INDEX tasks_status_row_id_idx ON tasks(status, row_id)",
                "CREATE INDEX tasks_visible_row_id_idx ON tasks(deleted_at, row_id)",
                """CREATE TABLE task_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES tasks(id),
                    task_sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(task_id, task_sequence)
                )""",
                "CREATE INDEX task_events_task_event_idx ON task_events(task_id, event_id)",
                """CREATE TABLE task_questions (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(id),
                    question TEXT NOT NULL,
                    options_json TEXT NOT NULL,
                    rpc_request_id TEXT,
                    answer TEXT,
                    created_at TEXT NOT NULL,
                    answered_at TEXT,
                    delivered_at TEXT
                )""",
                """CREATE INDEX task_questions_pending_idx
                    ON task_questions(task_id, answered_at, created_at)""",
            )
            # aiosqlite/sqlite3 executescript() implicitly commits an existing
            # transaction. Execute each fixed statement separately so schema DDL
            # and the migration record share the same rollback boundary.
            for statement in statements:
                await connection.execute(statement)
            await connection.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                (1, "initial_pi_task_runtime_schema", _dump_time(_now())),
            )
            await connection.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                (2, "durable_question_delivery", _dump_time(_now())),
            )
            await connection.commit()
        except BaseException:
            await connection.rollback()
            raise

    async def _apply_v2_schema(self, connection: aiosqlite.Connection) -> None:
        """Upgrade v1 questions atomically and conservatively mark terminal answers delivered."""

        await connection.execute("BEGIN IMMEDIATE")
        try:
            await connection.execute(
                "ALTER TABLE task_questions ADD COLUMN delivered_at TEXT"
            )
            terminal = tuple(status.value for status in TERMINAL_TASK_STATUSES)
            await connection.execute(
                f"""
                UPDATE task_questions SET delivered_at = answered_at
                WHERE answered_at IS NOT NULL AND task_id IN (
                    SELECT id FROM tasks WHERE status IN ({','.join('?' for _ in terminal)})
                )
                """,
                terminal,
            )
            await connection.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                (2, "durable_question_delivery", _dump_time(_now())),
            )
            await connection.commit()
        except BaseException:
            await connection.rollback()
            raise

    async def close(self) -> None:
        """Close the SQLite connection idempotently."""

        connection, self._connection = self._connection, None
        if connection is not None:
            await connection.close()

    def _db(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("TaskStore is not initialized.")
        return self._connection

    @staticmethod
    async def _rollback_write(
        connection: aiosqlite.Connection, error: BaseException
    ) -> None:
        """Rollback one write and classify SQLite durability failures for Manager."""

        await connection.rollback()
        if isinstance(error, sqlite3.Error):
            raise TaskPersistenceError(
                "The Task database rejected a state or event write."
            ) from error
        raise error

    async def create_task(self, command: CreateTaskCommand) -> TaskRecord:
        """Insert one queued Task and its task.created event atomically."""

        if (
            command.timeout_seconds is None
            or isinstance(command.timeout_seconds, bool)
            or not 1 <= command.timeout_seconds <= 86_400
        ):
            raise TaskCommandError("timeout_seconds must be an integer from 1 to 86400.")

        task_id = uuid4()
        occurred_at = _now()
        async with self._write_lock:
            connection = self._db()
            await connection.execute("BEGIN IMMEDIATE")
            try:
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
                        str(command.rerun_of_task_id) if command.rerun_of_task_id else None,
                        _dump_time(occurred_at),
                        _dump_time(occurred_at),
                    ),
                )
                await self._insert_event(
                    task_id,
                    1,
                    "task.created",
                    occurred_at,
                    {"status": TaskStatus.QUEUED.value},
                )
                await connection.commit()
                row = await self._fetch_task_row(task_id, include_deleted=True)
                assert int(row["row_id"]) == cursor.lastrowid
                return self._task_from_row(row)
            except BaseException as error:
                await self._rollback_write(connection, error)

    async def get_task(
        self, task_id: UUID, include_deleted: bool = False
    ) -> TaskRecord:
        """Return a Task snapshot or hide logical deletions by default."""

        return self._task_from_row(
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
            items=tuple(self._task_from_row(row) for row in visible),
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

        async with self._write_lock:
            connection = self._db()
            await connection.execute("BEGIN IMMEDIATE")
            try:
                current_row = await self._fetch_task_row(task_id, include_deleted=False)
                actual_version = int(current_row["version"])
                if actual_version != expected_version:
                    raise TaskVersionConflict(expected_version, actual_version)
                current = TaskStatus(current_row["status"])
                validate_status_transition(current, target_status)
                occurred_at = _now()
                started_at = current_row["started_at"]
                if target_status == TaskStatus.STARTING and started_at is None:
                    started_at = _dump_time(occurred_at)
                finished_at = current_row["finished_at"]
                if target_status in TERMINAL_TASK_STATUSES:
                    finished_at = _dump_time(occurred_at)
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
                        _dump_time(occurred_at),
                        started_at,
                        finished_at,
                        str(task_id),
                        expected_version,
                    ),
                )
                if cursor.rowcount != 1:
                    refreshed = await self._fetch_task_row(task_id, include_deleted=False)
                    raise TaskVersionConflict(expected_version, int(refreshed["version"]))
                sequence = await self._next_sequence(task_id)
                event = await self._insert_event(
                    task_id,
                    sequence,
                    "task.status_changed",
                    occurred_at,
                    {"from": current.value, "to": target_status.value},
                )
                await connection.commit()
                return await self.get_task(task_id), event
            except BaseException as error:
                await self._rollback_write(connection, error)

    async def append_output(self, task_id: UUID, text: str) -> TaskEvent:
        """Append one bounded product output chunk as an audit event."""

        async with self._write_lock:
            connection = self._db()
            await connection.execute("BEGIN IMMEDIATE")
            try:
                await self._fetch_task_row(task_id, include_deleted=False)
                event = await self._insert_event(
                    task_id,
                    await self._next_sequence(task_id),
                    "task.output_appended",
                    _now(),
                    {"text": text},
                )
                await connection.commit()
                return event
            except BaseException as error:
                await self._rollback_write(connection, error)

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

        question_id = uuid4()
        async with self._write_lock:
            connection = self._db()
            await connection.execute("BEGIN IMMEDIATE")
            try:
                row = await self._fetch_task_row(task_id, include_deleted=False)
                actual = int(row["version"])
                if actual != expected_version:
                    raise TaskVersionConflict(expected_version, actual)
                current = TaskStatus(row["status"])
                validate_status_transition(current, TaskStatus.WAITING)
                occurred_at = _now()
                await connection.execute(
                    """
                    INSERT INTO task_questions(
                        id, task_id, question, options_json, rpc_request_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(question_id),
                        str(task_id),
                        question,
                        json.dumps(options, ensure_ascii=False),
                        rpc_request_id,
                        _dump_time(occurred_at),
                    ),
                )
                await connection.execute(
                    """
                    UPDATE tasks SET status = ?, active_runtime_ms = ?,
                        version = ?, updated_at = ?
                    WHERE id = ? AND version = ?
                    """,
                    (
                        TaskStatus.WAITING.value,
                        active_runtime_ms
                        if active_runtime_ms is not None
                        else int(row["active_runtime_ms"]),
                        actual + 1,
                        _dump_time(occurred_at),
                        str(task_id),
                        actual,
                    ),
                )
                event = await self._insert_event(
                    task_id,
                    await self._next_sequence(task_id),
                    "task.question_raised",
                    occurred_at,
                    {
                        "questionId": str(question_id),
                        "question": question,
                        "options": list(options),
                    },
                )
                await connection.commit()
                task = await self.get_task(task_id)
                saved = await self._get_question(question_id)
                return task, saved, event
            except BaseException as error:
                await self._rollback_write(connection, error)

    async def answer_question(
        self,
        task_id: UUID,
        question_id: UUID,
        answer: str,
        *,
        expected_version: int,
    ) -> tuple[TaskRecord, TaskQuestion, TaskEvent]:
        """Persist an answer while leaving scheduling of the waiting Task to Manager."""

        async with self._write_lock:
            connection = self._db()
            await connection.execute("BEGIN IMMEDIATE")
            try:
                row = await self._fetch_task_row(task_id, include_deleted=False)
                actual = int(row["version"])
                if actual != expected_version:
                    raise TaskVersionConflict(expected_version, actual)
                if TaskStatus(row["status"]) != TaskStatus.WAITING:
                    raise TaskStateConflict(TaskStatus(row["status"]), TaskStatus.WAITING)
                question_row = await (
                    await connection.execute(
                        """
                        SELECT * FROM task_questions
                        WHERE id = ? AND task_id = ? AND answered_at IS NULL
                        """,
                        (str(question_id), str(task_id)),
                    )
                ).fetchone()
                if question_row is None:
                    raise TaskNotFound(f"Pending Task question not found: {question_id}")
                occurred_at = _now()
                await connection.execute(
                    "UPDATE task_questions SET answer = ?, answered_at = ? WHERE id = ?",
                    (answer, _dump_time(occurred_at), str(question_id)),
                )
                await connection.execute(
                    "UPDATE tasks SET version = ?, updated_at = ? WHERE id = ? AND version = ?",
                    (actual + 1, _dump_time(occurred_at), str(task_id), actual),
                )
                event = await self._insert_event(
                    task_id,
                    await self._next_sequence(task_id),
                    "task.question_answered",
                    occurred_at,
                    {"questionId": str(question_id), "answer": answer},
                )
                await connection.commit()
                return (
                    await self.get_task(task_id),
                    await self._get_question(question_id),
                    event,
                )
            except BaseException as error:
                await self._rollback_write(connection, error)

    async def pending_question(self, task_id: UUID) -> TaskQuestion | None:
        """Return the oldest unanswered question for a Task."""

        row = await (
            await self._db().execute(
                """
                SELECT * FROM task_questions
                WHERE task_id = ? AND answered_at IS NULL
                ORDER BY created_at LIMIT 1
                """,
                (str(task_id),),
            )
        ).fetchone()
        return self._question_from_row(row) if row else None

    async def undelivered_answer(self, task_id: UUID) -> TaskQuestion | None:
        """Return the oldest accepted answer that has not yet reached Pi."""

        row = await (
            await self._db().execute(
                """
                SELECT * FROM task_questions
                WHERE task_id = ? AND answered_at IS NOT NULL AND delivered_at IS NULL
                ORDER BY answered_at LIMIT 1
                """,
                (str(task_id),),
            )
        ).fetchone()
        return self._question_from_row(row) if row else None

    async def deliver_answer_and_transition(
        self,
        task_id: UUID,
        question_id: UUID,
        target_status: TaskStatus,
        *,
        expected_version: int,
    ) -> tuple[TaskRecord, TaskQuestion, TaskEvent]:
        """Atomically mark one answer delivered and advance its waiting/starting Task."""

        async with self._write_lock:
            connection = self._db()
            await connection.execute("BEGIN IMMEDIATE")
            try:
                row = await self._fetch_task_row(task_id, include_deleted=False)
                actual = int(row["version"])
                if actual != expected_version:
                    raise TaskVersionConflict(expected_version, actual)
                current = TaskStatus(row["status"])
                validate_status_transition(current, target_status)
                question = await (
                    await connection.execute(
                        """SELECT * FROM task_questions
                           WHERE id = ? AND task_id = ? AND answered_at IS NOT NULL""",
                        (str(question_id), str(task_id)),
                    )
                ).fetchone()
                if question is None:
                    raise TaskNotFound(f"Answered Task question not found: {question_id}")
                occurred_at = _now()
                await connection.execute(
                    "UPDATE task_questions SET delivered_at = COALESCE(delivered_at, ?) WHERE id = ?",
                    (_dump_time(occurred_at), str(question_id)),
                )
                await connection.execute(
                    "UPDATE tasks SET status = ?, version = ?, updated_at = ? WHERE id = ? AND version = ?",
                    (target_status.value, actual + 1, _dump_time(occurred_at), str(task_id), actual),
                )
                event = await self._insert_event(
                    task_id,
                    await self._next_sequence(task_id),
                    "task.status_changed",
                    occurred_at,
                    {"from": current.value, "to": target_status.value},
                )
                await connection.commit()
                return (
                    await self.get_task(task_id),
                    await self._get_question(question_id),
                    event,
                )
            except BaseException as error:
                await self._rollback_write(connection, error)

    async def _get_question(self, question_id: UUID) -> TaskQuestion:
        row = await (
            await self._db().execute(
                "SELECT * FROM task_questions WHERE id = ?", (str(question_id),)
            )
        ).fetchone()
        if row is None:
            raise TaskNotFound(f"Task question not found: {question_id}")
        return self._question_from_row(row)

    async def soft_delete(self, task_id: UUID) -> tuple[TaskRecord, TaskEvent]:
        """Quarantine a session, commit one tombstone, then strongly purge it."""

        self._require_safe_deleting_root()
        task = await self.get_task(task_id, include_deleted=True)
        if task.deleted_at is not None:
            canonical, staged = self._session_paths(task_id)
            if self._lexists(canonical) and self._lexists(staged):
                raise TaskSessionCleanupError(tombstone_committed=True)
            if self._lexists(canonical):
                staged.parent.mkdir(parents=True, exist_ok=True)
                os.replace(canonical, staged)
            await self._purge_staged_session(task_id)
            row = await (
                await self._db().execute(
                    """SELECT * FROM task_events WHERE task_id = ? AND event_type = 'task.deleted'
                       ORDER BY event_id DESC LIMIT 1""",
                    (str(task_id),),
                )
            ).fetchone()
            if row is None:
                raise TaskPersistenceError("Deleted Task has no deletion event.")
            deleted_event = self._event_from_row(row)
            return task, deleted_event
        if task.status not in TERMINAL_TASK_STATUSES:
            raise TaskStateConflict(task.status, TaskStatus.CANCELLED)

        moved = self._stage_session(task_id)

        async with self._write_lock:
            connection = self._db()
            await connection.execute("BEGIN IMMEDIATE")
            try:
                current = await self._fetch_task_row(task_id, include_deleted=True)
                occurred_at = _now()
                await connection.execute(
                    """
                    UPDATE tasks SET deleted_at = ?, updated_at = ?, version = version + 1
                    WHERE id = ? AND deleted_at IS NULL
                    """,
                    (_dump_time(occurred_at), _dump_time(occurred_at), str(task_id)),
                )
                event = await self._insert_event(
                    task_id,
                    await self._next_sequence(task_id),
                    "task.deleted",
                    occurred_at,
                    {"status": current["status"]},
                )
                await connection.commit()
                deleted = await self.get_task(task_id, include_deleted=True)
            except BaseException as error:
                try:
                    await connection.rollback()
                    if moved:
                        self._restore_staged_session(task_id)
                except BaseException:
                    logger.critical(
                        "Failed to restore Task session after tombstone rollback",
                        exc_info=True,
                    )
                    raise TaskSessionCleanupError(tombstone_committed=False) from error
                if isinstance(error, sqlite3.Error):
                    raise TaskPersistenceError(
                        "The Task database rejected a state or event write."
                    ) from error
                raise
        try:
            await self._purge_staged_session(task_id)
        except OSError as error:
            raise TaskSessionCleanupError(tombstone_committed=True) from error
        return deleted, event

    def _session_paths(self, task_id: UUID) -> tuple[Path, Path]:
        canonical_id = UUID(str(task_id))
        name = str(canonical_id)
        return self.session_root / name, self.session_root / ".deleting" / name

    @staticmethod
    def _lexists(path: Path) -> bool:
        return os.path.lexists(path)

    def _require_safe_deleting_root(self) -> None:
        deleting = self.session_root / ".deleting"
        if deleting.is_symlink() or (self._lexists(deleting) and not deleting.is_dir()):
            logger.critical("Task deletion quarantine root is unsafe: %s", deleting)
            raise TaskSessionCleanupError(tombstone_committed=False)

    def _stage_session(self, task_id: UUID) -> bool:
        canonical, staged = self._session_paths(task_id)
        if self._lexists(canonical) and self._lexists(staged):
            raise TaskSessionCleanupError(tombstone_committed=False)
        if not self._lexists(canonical):
            return False
        staged.parent.mkdir(parents=True, exist_ok=True)
        os.replace(canonical, staged)
        return True

    def _restore_staged_session(self, task_id: UUID) -> None:
        canonical, staged = self._session_paths(task_id)
        if not self._lexists(staged):
            return
        if self._lexists(canonical):
            raise TaskSessionCleanupError(tombstone_committed=False)
        os.replace(staged, canonical)

    async def _purge_staged_session(self, task_id: UUID) -> None:
        _, staged = self._session_paths(task_id)
        if not self._lexists(staged):
            return
        if staged.is_symlink() or not staged.is_dir():
            staged.unlink()
        else:
            shutil.rmtree(staged)

    async def recover_session_deletions(self) -> None:
        """Reconcile UUID quarantine entries from durable tombstones at startup."""

        deleting = self.session_root / ".deleting"
        self._require_safe_deleting_root()
        if not deleting.is_dir():
            return
        for staged in deleting.iterdir():
            try:
                task_id = UUID(staged.name)
            except ValueError:
                logger.warning("Ignoring non-UUID Task deletion entry: %s", staged)
                continue
            if str(task_id) != staged.name:
                logger.warning("Ignoring non-canonical Task deletion entry: %s", staged)
                continue
            try:
                task = await self.get_task(task_id, include_deleted=True)
            except TaskNotFound:
                logger.warning("Ignoring unknown Task deletion entry: %s", staged)
                continue
            canonical, _ = self._session_paths(task_id)
            if self._lexists(canonical):
                raise TaskSessionCleanupError(tombstone_committed=task.deleted_at is not None)
            if task.deleted_at is None:
                self._restore_staged_session(task_id)
            else:
                await self._purge_staged_session(task_id)

    async def events_after(
        self,
        event_id: int,
        task_id: UUID | None,
        limit: int,
    ) -> list[TaskEvent]:
        """Replay ordered business events after a global SSE cursor."""

        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        parameters: list[Any] = [event_id]
        clause = "event_id > ?"
        if task_id is not None:
            clause += " AND task_id = ?"
            parameters.append(str(task_id))
        parameters.append(limit)
        rows = await (
            await self._db().execute(
                f"SELECT * FROM task_events WHERE {clause} ORDER BY event_id LIMIT ?",
                parameters,
            )
        ).fetchall()
        return [self._event_from_row(row) for row in rows]

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
        return [self._task_from_row(row) for row in rows]

    async def _next_sequence(self, task_id: UUID) -> int:
        row = await (
            await self._db().execute(
                """
                SELECT COALESCE(MAX(task_sequence), 0) + 1 AS next_sequence
                FROM task_events WHERE task_id = ?
                """,
                (str(task_id),),
            )
        ).fetchone()
        return int(row["next_sequence"])

    async def _insert_event(
        self,
        task_id: UUID,
        sequence: int,
        event_type: str,
        occurred_at: datetime,
        payload: Mapping[str, Any],
    ) -> TaskEvent:
        cursor = await self._db().execute(
            """
            INSERT INTO task_events(
                task_id, task_sequence, event_type, occurred_at, payload_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(task_id),
                sequence,
                event_type,
                _dump_time(occurred_at),
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        return TaskEvent(
            event_id=int(cursor.lastrowid),
            task_sequence=sequence,
            task_id=task_id,
            type=event_type,
            occurred_at=occurred_at,
            payload=dict(payload),
        )

    @staticmethod
    def _task_from_row(row: aiosqlite.Row) -> TaskRecord:
        return TaskRecord(
            id=UUID(row["id"]),
            row_id=int(row["row_id"]),
            title=row["title"],
            prompt=row["prompt"],
            working_directory=Path(row["working_directory"]),
            status=TaskStatus(row["status"]),
            timeout_seconds=int(row["timeout_seconds"]),
            active_runtime_ms=int(row["active_runtime_ms"]),
            pi_session_id=row["pi_session_id"],
            pi_session_path=Path(row["pi_session_path"]) if row["pi_session_path"] else None,
            result_text=row["result_text"],
            error_code=TaskErrorCode(row["error_code"]) if row["error_code"] else None,
            error_message=row["error_message"],
            pause_reason=row["pause_reason"],
            rerun_of_task_id=UUID(row["rerun_of_task_id"])
            if row["rerun_of_task_id"]
            else None,
            version=int(row["version"]),
            created_at=_load_time(row["created_at"]),
            updated_at=_load_time(row["updated_at"]),
            started_at=_load_time(row["started_at"]),
            finished_at=_load_time(row["finished_at"]),
            deleted_at=_load_time(row["deleted_at"]),
        )

    @staticmethod
    def _event_from_row(row: aiosqlite.Row) -> TaskEvent:
        return TaskEvent(
            event_id=int(row["event_id"]),
            task_sequence=int(row["task_sequence"]),
            task_id=UUID(row["task_id"]),
            type=row["event_type"],
            occurred_at=_load_time(row["occurred_at"]),
            payload=json.loads(row["payload_json"]),
        )

    @staticmethod
    def _question_from_row(row: aiosqlite.Row) -> TaskQuestion:
        return TaskQuestion(
            id=UUID(row["id"]),
            task_id=UUID(row["task_id"]),
            question=row["question"],
            options=tuple(json.loads(row["options_json"])),
            rpc_request_id=row["rpc_request_id"],
            answer=row["answer"],
            created_at=_load_time(row["created_at"]),
            answered_at=_load_time(row["answered_at"]),
            delivered_at=_load_time(row["delivered_at"]),
        )


__all__ = [
    "SCHEMA_VERSION",
    "TaskPersistenceError",
    "TaskSessionCleanupError",
    "TaskStore",
    "TaskStoreMigrationError",
]
