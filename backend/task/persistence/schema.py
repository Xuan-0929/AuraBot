"""创建、升级并验证 AuraBot Task SQLite schema。

本模块拥有固定 v2 DDL 和 migration rollback boundary，但不拥有数据库文件或长期连接；
TaskStore 打开 connection、设置 PRAGMA 后调用 initialize_schema，并在成功后继续提供服务。
"""

from __future__ import annotations

import aiosqlite

from ..models import TERMINAL_TASK_STATUSES
from .codec import dump_time, now

SCHEMA_VERSION = 2


class TaskStoreMigrationError(RuntimeError):
    """Raised when the on-disk Task schema cannot be safely opened."""


async def initialize_schema(connection: aiosqlite.Connection) -> None:
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
    row = await (
        await connection.execute(
            "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
        )
    ).fetchone()
    current = int(row["version"])
    if current > SCHEMA_VERSION:
        raise TaskStoreMigrationError(
            f"Task database schema {current} is newer than supported {SCHEMA_VERSION}."
        )
    if current < 1:
        await _apply_initial_schema(connection)
        current = SCHEMA_VERSION
    if current < 2:
        await _apply_v2_schema(connection)
    violations = await (
        await connection.execute("PRAGMA foreign_key_check")
    ).fetchall()
    if violations:
        raise TaskStoreMigrationError(
            f"Task database foreign key check failed: {len(violations)} violation(s)."
        )


async def _apply_initial_schema(connection: aiosqlite.Connection) -> None:
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
        for statement in statements:
            await connection.execute(statement)
        await connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (1, "initial_pi_task_runtime_schema", dump_time(now())),
        )
        await connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (2, "durable_question_delivery", dump_time(now())),
        )
        await connection.commit()
    except BaseException:
        await connection.rollback()
        raise


async def _apply_v2_schema(connection: aiosqlite.Connection) -> None:
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
            (2, "durable_question_delivery", dump_time(now())),
        )
        await connection.commit()
    except BaseException:
        await connection.rollback()
        raise


__all__ = ["SCHEMA_VERSION", "TaskStoreMigrationError", "initialize_schema"]
