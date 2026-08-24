"""验证同步两表 TaskStore 的 schema、FIFO、CAS、问题和事务语义。

测试直接调用标准库 sqlite3 Store，不运行事件循环或 Pi；它固定数据库作为唯一排队
真相，并确保状态、问题与可重放事件共享同一个事务边界。
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.task.models import (
    CreateTaskCommand,
    TaskNotFound,
    TaskStateConflict,
    TaskStatus,
    TaskVersionConflict,
)
from backend.task.store import TaskPersistenceError, TaskStore, TaskStoreSchemaError


class TaskStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database_path = self.root / "task/task-runtime.sqlite3"
        self.store = TaskStore(self.database_path)
        self.store.initialize()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def command(self, index: int = 1, *, rerun_of=None) -> CreateTaskCommand:
        working = self.root / f"project-{index}"
        working.mkdir(exist_ok=True)
        return CreateTaskCommand(
            title=f"Task {index}",
            prompt=f"Prompt {index}",
            working_directory=working.resolve(),
            timeout_seconds=1800,
            rerun_of_task_id=rerun_of,
        )

    def test_initialize_creates_only_two_tables_and_is_idempotent(self):
        self.store.initialize()

        with sqlite3.connect(self.database_path) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                )
            }
            version = connection.execute("PRAGMA user_version").fetchone()[0]

        self.assertEqual(tables, {"tasks", "task_events"})
        self.assertEqual(version, 1)

    def test_rejects_legacy_and_unknown_schema_without_mutating_it(self):
        self.store.close()
        self.database_path.unlink()
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("CREATE TABLE schema_migrations(version INTEGER)")
            connection.execute("INSERT INTO schema_migrations VALUES (2)")

        with self.assertRaisesRegex(TaskStoreSchemaError, "back up.*entire Task data"):
            TaskStore(self.database_path).initialize()

        with sqlite3.connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute("SELECT version FROM schema_migrations").fetchone()[0],
                2,
            )
            connection.execute("DROP TABLE schema_migrations")
            connection.execute("PRAGMA user_version = 99")

        with self.assertRaisesRegex(TaskStoreSchemaError, "user_version=99"):
            TaskStore(self.database_path).initialize()

    def test_create_list_and_claim_use_sqlite_fifo(self):
        tasks = [self.store.create_task(self.command(index)) for index in range(1, 7)]

        claimed = self.store.claim_queued(5)
        page = self.store.list_tasks(limit=10)

        self.assertEqual([task.id for task in claimed], [task.id for task in tasks[:5]])
        self.assertTrue(all(task.status == TaskStatus.RUNNING for task in claimed))
        self.assertEqual(page.items[-1].status, TaskStatus.QUEUED)
        self.assertTrue(all(task.started_at is not None for task in claimed))

    def test_transition_cas_and_event_failure_rollback_together(self):
        task = self.store.create_task(self.command())
        running = self.store.claim_queued(1)[0]

        with self.assertRaises(TaskVersionConflict):
            self.store.transition_task(
                task.id,
                TaskStatus.PAUSED,
                expected_version=task.version,
            )
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                CREATE TRIGGER reject_status_event
                BEFORE INSERT ON task_events
                WHEN NEW.event_type = 'task.status_changed'
                BEGIN SELECT RAISE(ABORT, 'event rejected'); END
                """
            )
        with self.assertRaises(TaskPersistenceError):
            self.store.transition_task(
                task.id,
                TaskStatus.PAUSED,
                expected_version=running.version,
            )

        unchanged = self.store.get_task(task.id)
        self.assertEqual(unchanged.status, TaskStatus.RUNNING)
        self.assertEqual(unchanged.version, running.version)

    def test_question_is_folded_into_task_and_answer_returns_queued(self):
        task = self.store.create_task(self.command())
        running = self.store.claim_queued(1)[0]

        waiting, raised = self.store.raise_question(
            task.id,
            "Choose",
            ("A", "B"),
            expected_version=running.version,
            active_runtime_ms=123,
        )
        answered, answer_event = self.store.answer_question(
            task.id,
            waiting.question_id,
            "A",
            expected_version=waiting.version,
        )

        self.assertEqual(waiting.status, TaskStatus.WAITING)
        self.assertEqual(waiting.question_options, ("A", "B"))
        self.assertEqual(raised.type, "task.question_raised")
        self.assertEqual(answered.status, TaskStatus.QUEUED)
        self.assertEqual(answered.question_answer, "A")
        self.assertEqual(answer_event.type, "task.question_answered")

        resumed = self.store.claim_queued(1)[0]
        cleared = self.store.clear_question(
            task.id,
            expected_version=resumed.version,
        )
        self.assertIsNone(cleared.question_id)
        self.assertIsNone(cleared.question_answer)

    def test_rerun_and_tombstone_keep_visibility_contract(self):
        source = self.store.create_task(self.command())
        with self.assertRaises(TaskStateConflict):
            self.store.create_task(self.command(2, rerun_of=source.id))
        source, _ = self.store.transition_task(
            source.id,
            TaskStatus.CANCELLED,
            expected_version=source.version,
        )
        rerun = self.store.create_task(self.command(3, rerun_of=source.id))
        self.assertEqual(rerun.rerun_of_task_id, source.id)

        deleted, event = self.store.tombstone_task(source.id)
        repeated, repeated_event = self.store.tombstone_task(source.id)
        self.assertIsNotNone(deleted.deleted_at)
        self.assertEqual(repeated, deleted)
        self.assertEqual(repeated_event.event_id, event.event_id)
        self.assertIn(source.id, self.store.deleted_task_ids())
        with self.assertRaises(TaskNotFound):
            self.store.get_task(source.id)


if __name__ == "__main__":
    unittest.main()
