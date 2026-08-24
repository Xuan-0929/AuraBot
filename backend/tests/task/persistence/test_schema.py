"""验证 Task SQLite v2 fresh schema、升级、回滚和 future-version 拒绝。

测试直接通过 TaskStore 打开临时数据库，以确认 persistence.schema 在 Store-owned
connection 上执行，且 DDL 与 migration record 始终位于同一 rollback boundary。
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.task.persistence.schema import SCHEMA_VERSION
from backend.task.persistence.store import TaskStore, TaskStoreMigrationError


class TaskSchemaTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def create_v1_database(database: Path) -> None:
        with sqlite3.connect(database) as connection:
            connection.executescript(
                """
                CREATE TABLE schema_migrations(
                    version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL
                );
                INSERT INTO schema_migrations VALUES(1, 'v1', '2026-01-01T00:00:00Z');
                CREATE TABLE tasks(id TEXT PRIMARY KEY, status TEXT NOT NULL);
                CREATE TABLE task_questions(
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, question TEXT NOT NULL,
                    options_json TEXT NOT NULL, rpc_request_id TEXT, answer TEXT,
                    created_at TEXT NOT NULL, answered_at TEXT
                );
                """
            )

    async def test_fresh_v2_schema_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "task.sqlite3"
            store = TaskStore(database)
            await store.initialize()
            await store.initialize()
            await store.close()
            with sqlite3.connect(database) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(task_questions)"
                    )
                }
                version = connection.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()[0]
            self.assertEqual(version, SCHEMA_VERSION)
            self.assertIn("delivered_at", columns)
            self.assertTrue({"tasks", "task_events", "task_questions"} <= tables)

    async def test_v1_upgrade_only_backfills_terminal_answer_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "task.sqlite3"
            self.create_v1_database(database)
            terminal_id = "00000000-0000-4000-8000-000000000001"
            active_id = "00000000-0000-4000-8000-000000000002"
            with sqlite3.connect(database) as connection:
                connection.executemany(
                    "INSERT INTO tasks(id, status) VALUES (?, ?)",
                    ((terminal_id, "completed"), (active_id, "waiting")),
                )
                connection.executemany(
                    """INSERT INTO task_questions(
                           id, task_id, question, options_json, answer,
                           created_at, answered_at
                       ) VALUES (?, ?, 'q', '[]', 'a', '2026-01-01', '2026-01-02')""",
                    (("q-terminal", terminal_id), ("q-active", active_id)),
                )
            store = TaskStore(database)
            await store.initialize()
            await store.close()
            with sqlite3.connect(database) as connection:
                rows = dict(
                    connection.execute(
                        "SELECT id, delivered_at FROM task_questions ORDER BY id"
                    )
                )
                version = connection.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()[0]
            self.assertEqual(version, 2)
            self.assertEqual(rows["q-terminal"], "2026-01-02")
            self.assertIsNone(rows["q-active"])

    async def test_v2_migration_rolls_back_schema_when_record_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "task.sqlite3"
            self.create_v1_database(database)
            with sqlite3.connect(database) as connection:
                connection.execute(
                    """CREATE TRIGGER reject_v2 BEFORE INSERT ON schema_migrations
                       WHEN NEW.version = 2 BEGIN SELECT RAISE(ABORT, 'v2 rejected'); END"""
                )
            store = TaskStore(database)
            with self.assertRaisesRegex(sqlite3.IntegrityError, "v2 rejected"):
                await store.initialize()
            await store.close()
            with sqlite3.connect(database) as connection:
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(task_questions)"
                    )
                }
            self.assertNotIn("delivered_at", columns)

    async def test_initial_schema_rolls_back_when_version_record_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "task.sqlite3"
            with sqlite3.connect(database) as connection:
                connection.execute(
                    """
                    CREATE TABLE schema_migrations(
                        version INTEGER PRIMARY KEY,
                        name TEXT NOT NULL,
                        applied_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TRIGGER reject_initial_task_migration
                    BEFORE INSERT ON schema_migrations
                    BEGIN SELECT RAISE(ABORT, 'migration record rejected'); END
                    """
                )
            store = TaskStore(database)
            with self.assertRaisesRegex(sqlite3.IntegrityError, "migration record rejected"):
                await store.initialize()
            await store.close()
            with sqlite3.connect(database) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
            self.assertNotIn("tasks", tables)
            self.assertNotIn("task_events", tables)
            self.assertNotIn("task_questions", tables)

    async def test_unknown_future_schema_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "task.sqlite3"
            with sqlite3.connect(database) as connection:
                connection.execute(
                    "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, name TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO schema_migrations(version, name) VALUES (999, 'future')"
                )
            store = TaskStore(database)
            with self.assertRaises(TaskStoreMigrationError):
                await store.initialize()
            await store.close()


if __name__ == "__main__":
    unittest.main()
