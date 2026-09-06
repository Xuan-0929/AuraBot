"""Persist Task records and durable events through one synchronous SQLite connection.

TaskManager owns this store in one event loop. Native SQLite constraints and one transaction keep
CAS, owner/idempotency, queue claims and events consistent. Startup accepts only the current schema;
the store also supplies bounded status snapshots and watermark replay for Main Agent reconciliation.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar
from uuid import UUID, uuid4

from .models import (
    CreateTaskCommand,
    TERMINAL_TASK_STATUSES,
    TaskCommandError,
    TaskErrorCode,
    TaskEvent,
    TaskNotFound,
    TaskPage,
    TaskRecord,
    TaskStateConflict,
    TaskStatus,
    TaskVersionConflict,
    validate_status_transition,
)

SCHEMA_VERSION = 3
_Result = TypeVar("_Result")


class TaskPersistenceError(RuntimeError):
    """SQLite 无法原子提交 Task 状态或事件。"""


class TaskStoreSchemaError(RuntimeError):
    """磁盘数据库不是当前两表模型，拒绝自动迁移。"""


def _now() -> datetime:
    return datetime.now(UTC)


def _dump(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def _load(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


class TaskStore:
    """单连接同步 SQLite Store；调用方必须从同一事件循环使用。"""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.resolve()
        self._connection: sqlite3.Connection | None = None

    def initialize(self) -> None:
        """Create the current schema or reject unsupported versions without migrating data."""
        if self._connection is not None:
            return
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            for pragma in (
                "PRAGMA journal_mode = WAL",
                "PRAGMA foreign_keys = ON",
                "PRAGMA busy_timeout = 5000",
            ):
                connection.execute(pragma)
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                )
            }
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version == 0 and not tables:
                self._create_schema(connection)
            elif version == SCHEMA_VERSION and tables == {"tasks", "task_events"}:
                pass
            else:
                raise TaskStoreSchemaError(
                    "Unsupported Task database schema "
                    f"(user_version={version}, tables={sorted(tables)}). Please back up "
                    "and delete the entire Task data directory: "
                    f"{self.database_path.parent}"
                )
        except BaseException:
            connection.close()
            raise
        self._connection = connection

    def close(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            connection.close()

    def _create_schema(self, db: sqlite3.Connection) -> None:
        statuses = ",".join(f"'{status.value}'" for status in TaskStatus)
        statements = (
            f"""CREATE TABLE tasks (
                row_id INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL, prompt TEXT NOT NULL, working_directory TEXT NOT NULL,
                workspace_id TEXT NOT NULL, owner_profile_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                timeout_seconds INTEGER NOT NULL CHECK(timeout_seconds > 0),
                rerun_of_task_id TEXT REFERENCES tasks(id),
                status TEXT NOT NULL CHECK(status IN ({statuses})),
                active_runtime_ms INTEGER NOT NULL DEFAULT 0 CHECK(active_runtime_ms >= 0),
                version INTEGER NOT NULL CHECK(version >= 1), question_id TEXT,
                question_text TEXT, question_options_json TEXT, question_answer TEXT,
                result_text TEXT, error_code TEXT, error_message TEXT, pause_reason TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, started_at TEXT,
                finished_at TEXT, deleted_at TEXT)""",
            "CREATE INDEX tasks_queue_idx ON tasks(status, deleted_at, row_id)",
            "CREATE UNIQUE INDEX tasks_owner_idempotency_idx ON tasks(owner_profile_id, idempotency_key)",
            "CREATE INDEX tasks_owner_recent_idx ON tasks(owner_profile_id, row_id DESC) WHERE deleted_at IS NULL",
            """CREATE TABLE task_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL REFERENCES tasks(id), task_sequence INTEGER NOT NULL,
                event_type TEXT NOT NULL, occurred_at TEXT NOT NULL, payload_json TEXT NOT NULL,
                UNIQUE(task_id, task_sequence))""",
            "CREATE INDEX task_events_replay_idx ON task_events(task_id, event_id)",
        )
        db.execute("BEGIN IMMEDIATE")
        try:
            for statement in statements:
                db.execute(statement)
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            db.commit()
        except BaseException:
            db.rollback()
            raise

    def _db(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("TaskStore is not initialized.")
        return self._connection

    def _write(self, operation: Callable[[sqlite3.Connection], _Result]) -> _Result:
        db = self._db()
        try:
            db.execute("BEGIN IMMEDIATE")
            result = operation(db)
            db.commit()
            return result
        except BaseException as error:
            db.rollback()
            if isinstance(error, sqlite3.Error):
                raise TaskPersistenceError(
                    "The Task database rejected a state or event write."
                ) from error
            raise

    def _row(self, task_id: UUID, include_deleted: bool = False) -> sqlite3.Row:
        clause = "" if include_deleted else " AND deleted_at IS NULL"
        row = self._db().execute(
            f"SELECT * FROM tasks WHERE id = ?{clause}", (str(task_id),)
        ).fetchone()
        if row is None:
            raise TaskNotFound(f"Task not found: {task_id}")
        return row

    def _cas(self, task_id: UUID, expected_version: int) -> sqlite3.Row:
        row = self._row(task_id)
        actual = int(row["version"])
        if actual != expected_version:
            raise TaskVersionConflict(expected_version, actual)
        return row

    @staticmethod
    def _task(row: sqlite3.Row) -> TaskRecord:
        return TaskRecord(
            id=UUID(row["id"]), row_id=int(row["row_id"]), title=row["title"],
            prompt=row["prompt"], working_directory=Path(row["working_directory"]),
            workspace_id=row["workspace_id"], owner_profile_id=row["owner_profile_id"],
            idempotency_key=row["idempotency_key"],
            status=TaskStatus(row["status"]), timeout_seconds=int(row["timeout_seconds"]),
            active_runtime_ms=int(row["active_runtime_ms"]),
            question_id=UUID(row["question_id"]) if row["question_id"] else None,
            question_text=row["question_text"],
            question_options=tuple(json.loads(row["question_options_json"] or "[]")),
            question_answer=row["question_answer"], result_text=row["result_text"],
            error_code=TaskErrorCode(row["error_code"]) if row["error_code"] else None,
            error_message=row["error_message"], pause_reason=row["pause_reason"],
            rerun_of_task_id=UUID(row["rerun_of_task_id"]) if row["rerun_of_task_id"] else None,
            version=int(row["version"]), created_at=_load(row["created_at"]),
            updated_at=_load(row["updated_at"]), started_at=_load(row["started_at"]),
            finished_at=_load(row["finished_at"]), deleted_at=_load(row["deleted_at"]),
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> TaskEvent:
        return TaskEvent(
            event_id=int(row["event_id"]), task_sequence=int(row["task_sequence"]),
            task_id=UUID(row["task_id"]), type=row["event_type"],
            occurred_at=_load(row["occurred_at"]), payload=json.loads(row["payload_json"]),
        )

    def _event(
        self, db: sqlite3.Connection, task_id: UUID, event_type: str,
        occurred_at: datetime, payload: Mapping[str, Any],
    ) -> TaskEvent:
        sequence = int(db.execute(
            "SELECT COALESCE(MAX(task_sequence), 0) + 1 FROM task_events WHERE task_id = ?",
            (str(task_id),),
        ).fetchone()[0])
        cursor = db.execute(
            "INSERT INTO task_events(task_id, task_sequence, event_type, occurred_at, "
            "payload_json) VALUES (?, ?, ?, ?, ?)",
            (str(task_id), sequence, event_type, _dump(occurred_at),
             json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
        )
        return TaskEvent(
            event_id=int(cursor.lastrowid), task_sequence=sequence, task_id=task_id,
            type=event_type, occurred_at=occurred_at, payload=dict(payload),
        )

    def create_task(self, command: CreateTaskCommand, working_directory: Path) -> TaskRecord:
        timeout = command.timeout_seconds
        if isinstance(timeout, bool) or timeout is None or not 1 <= timeout <= 86_400:
            raise TaskCommandError("timeout_seconds must be an integer from 1 to 86400.")

        def operation(db: sqlite3.Connection) -> TaskRecord:
            existing = db.execute(
                "SELECT * FROM tasks WHERE owner_profile_id=? AND idempotency_key=?",
                (command.owner_profile_id, command.idempotency_key),
            ).fetchone()
            if existing is not None:
                return self._task(existing)
            if command.rerun_of_task_id is not None:
                source = self._row(command.rerun_of_task_id)
                if TaskStatus(source["status"]) not in TERMINAL_TASK_STATUSES:
                    raise TaskStateConflict(TaskStatus(source["status"]), TaskStatus.QUEUED)
            task_id, occurred_at = uuid4(), _now()
            db.execute(
                """INSERT INTO tasks(
                    id,title,prompt,working_directory,workspace_id,owner_profile_id,idempotency_key,
                    timeout_seconds,rerun_of_task_id,
                    status,active_runtime_ms,version,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,0,1,?,?)""",
                (str(task_id), command.title, command.prompt, str(working_directory),
                 command.workspace_id, command.owner_profile_id, command.idempotency_key,
                 timeout, str(command.rerun_of_task_id) if command.rerun_of_task_id else None,
                 TaskStatus.QUEUED.value, _dump(occurred_at), _dump(occurred_at)),
            )
            self._event(db, task_id, "task.created", occurred_at,
                        {"status": TaskStatus.QUEUED.value})
            return self._task(self._row(task_id, True))
        return self._write(operation)

    def get_task(self, task_id: UUID, include_deleted: bool = False) -> TaskRecord:
        return self._task(self._row(task_id, include_deleted))

    def list_tasks(
        self, *, after_row_id: int | None = None, limit: int = 50,
        statuses: Iterable[TaskStatus] | None = None,
        owner_profile_id: str | None = None,
        include_deleted: bool = False,
    ) -> TaskPage:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        where, parameters = ["row_id > ?"], [after_row_id or 0]
        if not include_deleted:
            where.append("deleted_at IS NULL")
        if owner_profile_id is not None:
            where.append("owner_profile_id = ?")
            parameters.append(owner_profile_id)
        values = tuple(status.value for status in (statuses or ()))
        if values:
            where.append(f"status IN ({','.join('?' for _ in values)})")
            parameters.extend(values)
        rows = self._db().execute(
            f"SELECT * FROM tasks WHERE {' AND '.join(where)} ORDER BY row_id LIMIT ?",
            [*parameters, limit + 1],
        ).fetchall()
        visible = rows[:limit]
        return TaskPage(tuple(self._task(row) for row in visible),
                        int(visible[-1]["row_id"]) if len(rows) > limit else None)

    def status_snapshot(self, owner_profile_id: str) -> list[dict[str, str]]:
        """Read only the newest 100 visible titles and states using the owner index."""

        rows = self._db().execute(
            "SELECT id,title,status FROM tasks WHERE owner_profile_id=? AND deleted_at IS NULL "
            "ORDER BY row_id DESC LIMIT 100", (owner_profile_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def current_event_id(self) -> int:
        """Capture the durable watermark before paging Task truth during startup."""

        return int(self._db().execute("SELECT COALESCE(MAX(event_id), 0) FROM task_events").fetchone()[0])

    def claim_queued(self, limit: int) -> tuple[TaskRecord, ...]:
        """按 row_id 原子领取最老 queued Task。"""
        if limit <= 0:
            return ()

        def operation(db: sqlite3.Connection) -> tuple[TaskRecord, ...]:
            rows = db.execute(
                "SELECT * FROM tasks WHERE status=? AND deleted_at IS NULL "
                "ORDER BY row_id LIMIT ?", (TaskStatus.QUEUED.value, limit),
            ).fetchall()
            claimed = []
            for row in rows:
                occurred_at, task_id = _now(), UUID(row["id"])
                db.execute(
                    """UPDATE tasks SET status=?,version=version+1,updated_at=?,
                        started_at=COALESCE(started_at,?),error_code=NULL,
                        error_message=NULL,pause_reason=NULL
                        WHERE id=? AND version=? AND status=?""",
                    (TaskStatus.RUNNING.value, _dump(occurred_at), _dump(occurred_at),
                     row["id"], row["version"], TaskStatus.QUEUED.value),
                )
                self._event(db, task_id, "task.status_changed", occurred_at,
                            {"from": "queued", "to": "running"})
                claimed.append(self._task(self._row(task_id)))
            return tuple(claimed)
        return self._write(operation)

    def transition_task(
        self, task_id: UUID, target_status: TaskStatus, *, expected_version: int,
        active_runtime_ms: int | None = None, result_text: str | None = None,
        error_code: TaskErrorCode | None = None, error_message: str | None = None,
        pause_reason: str | None = None,
    ) -> tuple[TaskRecord, TaskEvent]:
        def operation(db: sqlite3.Connection) -> tuple[TaskRecord, TaskEvent]:
            row = self._cas(task_id, expected_version)
            current = TaskStatus(row["status"])
            validate_status_transition(current, target_status)
            runtime = int(row["active_runtime_ms"]) if active_runtime_ms is None else active_runtime_ms
            if runtime < 0:
                raise TaskCommandError("active_runtime_ms cannot be negative.")
            occurred_at = _now()
            started_at = row["started_at"] or (
                _dump(occurred_at) if target_status == TaskStatus.RUNNING else None)
            finished_at = (_dump(occurred_at) if target_status in TERMINAL_TASK_STATUSES
                           else row["finished_at"])
            db.execute(
                """UPDATE tasks SET status=?,active_runtime_ms=?,result_text=?,error_code=?,
                    error_message=?,pause_reason=?,version=version+1,updated_at=?,
                    started_at=?,finished_at=? WHERE id=? AND version=? AND deleted_at IS NULL""",
                (target_status.value, runtime, result_text,
                 error_code.value if error_code else None, error_message, pause_reason,
                 _dump(occurred_at), started_at, finished_at, str(task_id), expected_version),
            )
            event = self._event(db, task_id, "task.status_changed", occurred_at,
                                {"from": current.value, "to": target_status.value})
            return self._task(self._row(task_id)), event
        return self._write(operation)

    def append_output(self, task_id: UUID, text: str) -> TaskEvent:
        def operation(db: sqlite3.Connection) -> TaskEvent:
            self._row(task_id)
            return self._event(db, task_id, "task.output_appended", _now(), {"text": text})
        return self._write(operation)

    def raise_question(
        self, task_id: UUID, question: str, options: tuple[str, ...], *,
        expected_version: int, active_runtime_ms: int | None = None,
    ) -> tuple[TaskRecord, TaskEvent]:
        def operation(db: sqlite3.Connection) -> tuple[TaskRecord, TaskEvent]:
            row = self._cas(task_id, expected_version)
            current = TaskStatus(row["status"])
            validate_status_transition(current, TaskStatus.WAITING)
            question_id, occurred_at = uuid4(), _now()
            runtime = int(row["active_runtime_ms"]) if active_runtime_ms is None else active_runtime_ms
            db.execute(
                """UPDATE tasks SET status=?,active_runtime_ms=?,version=version+1,
                    question_id=?,question_text=?,question_options_json=?,question_answer=NULL,
                    updated_at=? WHERE id=? AND version=?""",
                (TaskStatus.WAITING.value, runtime, str(question_id), question,
                 json.dumps(options, ensure_ascii=False, separators=(",", ":")),
                 _dump(occurred_at), str(task_id), expected_version),
            )
            event = self._event(
                db, task_id, "task.question_raised", occurred_at,
                {"questionId": str(question_id), "question": question, "options": list(options)},
            )
            return self._task(self._row(task_id)), event
        return self._write(operation)

    def answer_question(
        self, task_id: UUID, question_id: UUID, answer: str, *, expected_version: int,
    ) -> tuple[TaskRecord, TaskEvent]:
        """持久化答案，并在同一事务把 waiting Task 放回 queued。"""
        def operation(db: sqlite3.Connection) -> tuple[TaskRecord, TaskEvent]:
            row = self._cas(task_id, expected_version)
            current = TaskStatus(row["status"])
            validate_status_transition(current, TaskStatus.QUEUED)
            if row["question_id"] != str(question_id) or row["question_answer"] is not None:
                raise TaskNotFound(f"Pending Task question not found: {question_id}")
            occurred_at = _now()
            db.execute(
                "UPDATE tasks SET status=?,question_answer=?,version=version+1,updated_at=? "
                "WHERE id=? AND version=?",
                (TaskStatus.QUEUED.value, answer, _dump(occurred_at),
                 str(task_id), expected_version),
            )
            event = self._event(db, task_id, "task.question_answered", occurred_at,
                                {"questionId": str(question_id), "answer": answer})
            return self._task(self._row(task_id)), event
        return self._write(operation)

    def clear_question(self, task_id: UUID, *, expected_version: int) -> TaskRecord:
        """continuation prompt 被接受后清除当前问题和答案。"""
        def operation(db: sqlite3.Connection) -> TaskRecord:
            row = self._cas(task_id, expected_version)
            if row["question_id"] is None:
                return self._task(row)
            if TaskStatus(row["status"]) != TaskStatus.RUNNING:
                raise TaskStateConflict(TaskStatus(row["status"]), TaskStatus.RUNNING)
            db.execute(
                """UPDATE tasks SET question_id=NULL,question_text=NULL,
                    question_options_json=NULL,question_answer=NULL,version=version+1,
                    updated_at=? WHERE id=? AND version=?""",
                (_dump(_now()), str(task_id), expected_version),
            )
            return self._task(self._row(task_id))
        return self._write(operation)

    def tombstone_task(self, task_id: UUID) -> tuple[TaskRecord, TaskEvent]:
        task = self.get_task(task_id, include_deleted=True)
        if task.deleted_at is not None:
            row = self._db().execute(
                "SELECT * FROM task_events WHERE task_id=? AND event_type='task.deleted' "
                "ORDER BY event_id DESC LIMIT 1", (str(task_id),),
            ).fetchone()
            if row is None:
                raise TaskPersistenceError("Deleted Task has no deletion event.")
            return task, self._event_from_row(row)
        if task.status not in TERMINAL_TASK_STATUSES:
            raise TaskStateConflict(task.status, TaskStatus.CANCELLED)

        def operation(db: sqlite3.Connection) -> tuple[TaskRecord, TaskEvent]:
            occurred_at = _now()
            db.execute(
                "UPDATE tasks SET deleted_at=?,updated_at=?,version=version+1 "
                "WHERE id=? AND deleted_at IS NULL",
                (_dump(occurred_at), _dump(occurred_at), str(task_id)),
            )
            event = self._event(db, task_id, "task.deleted", occurred_at,
                                {"status": task.status.value})
            return self.get_task(task_id, include_deleted=True), event
        return self._write(operation)

    def events_after(
        self, event_id: int, task_id: UUID | None, limit: int,
        *, event_types: Iterable[str] | None = None,
    ) -> list[TaskEvent]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        where, parameters = "event_id > ?", [event_id]
        if task_id is not None:
            where += " AND task_id = ?"
            parameters.append(str(task_id))
        types = tuple(event_types or ())
        if types:
            where += f" AND event_type IN ({','.join('?' for _ in types)})"
            parameters.extend(types)
        rows = self._db().execute(
            f"SELECT * FROM task_events WHERE {where} ORDER BY event_id LIMIT ?",
            [*parameters, limit],
        ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def recoverable_tasks(self) -> list[TaskRecord]:
        terminal = tuple(status.value for status in TERMINAL_TASK_STATUSES)
        rows = self._db().execute(
            f"SELECT * FROM tasks WHERE deleted_at IS NULL AND status NOT IN "
            f"({','.join('?' for _ in terminal)}) ORDER BY row_id", terminal,
        ).fetchall()
        return [self._task(row) for row in rows]

    def deleted_task_ids(self) -> tuple[UUID, ...]:
        rows = self._db().execute(
            "SELECT id FROM tasks WHERE deleted_at IS NOT NULL ORDER BY row_id"
        ).fetchall()
        return tuple(UUID(row["id"]) for row in rows)


__all__ = ["SCHEMA_VERSION", "TaskPersistenceError", "TaskStore", "TaskStoreSchemaError"]
