"""验证同步两表 TaskStore 的 schema、FIFO、CAS、问题和事务语义。

测试直接调用标准库 sqlite3 Store，不运行事件循环或 Pi；它固定数据库作为唯一排队
真相，并确保状态、问题与可重放事件共享同一个事务边界，包括 Manager 已验证的 workspace
解析结果及 owner/idempotency 持久字段。
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from dataclasses import replace
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
            workspace_id=f"project-{index}",
            owner_profile_id="profile-1",
            idempotency_key=f"key-{index}",
            timeout_seconds=1800,
            rerun_of_task_id=rerun_of,
        )

    def create(self, index: int = 1, *, rerun_of=None):
        command = self.command(index, rerun_of=rerun_of)
        return self.store.create_task(command, self.root / f"project-{index}")

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
        self.assertEqual(version, 3)

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

    def test_old_schema_is_rejected_without_changing_rows(self):
        task = self.create()
        self.store.close()
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("PRAGMA user_version = 2")
        with self.assertRaisesRegex(TaskStoreSchemaError, "user_version=2"):
            TaskStore(self.database_path).initialize()
        with sqlite3.connect(self.database_path) as connection:
            self.assertEqual(connection.execute("SELECT id FROM tasks").fetchone()[0], str(task.id))
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)

    def test_recent_snapshot_is_bounded_owner_scoped_and_uses_index(self):
        tasks = [self.create(index) for index in range(1, 105)]
        other = replace(self.command(105), owner_profile_id="profile-2")
        self.store.create_task(other, self.root)
        self.store.transition_task(tasks[-1].id, TaskStatus.CANCELLED, expected_version=1)
        self.store.tombstone_task(tasks[-1].id)
        snapshot = self.store.status_snapshot("profile-1")
        self.assertEqual(len(snapshot), 100)
        self.assertEqual(snapshot[0], {"id": str(tasks[-2].id), "title": "Task 103", "status": "queued"})
        self.assertEqual(snapshot[-1]["id"], str(tasks[3].id))
        with sqlite3.connect(self.database_path) as connection:
            plan = connection.execute(
                "EXPLAIN QUERY PLAN SELECT id,title,status FROM tasks "
                "WHERE owner_profile_id=? AND deleted_at IS NULL ORDER BY row_id DESC LIMIT 100",
                ("profile-1",),
            ).fetchall()
        self.assertTrue(any("tasks_owner_recent_idx" in row[3] for row in plan), plan)

    def test_reconciliation_pages_include_tombstones_and_replay_after_watermark(self):
        first, second = self.create(1), self.create(2)
        watermark = self.store.current_event_id()
        self.store.transition_task(first.id, TaskStatus.CANCELLED, expected_version=1)
        self.store.tombstone_task(first.id)
        page = self.store.list_tasks(limit=1, owner_profile_id="profile-1", include_deleted=True)
        self.assertEqual(page.items[0].id, first.id)
        self.assertIsNotNone(page.items[0].deleted_at)
        next_page = self.store.list_tasks(after_row_id=page.next_cursor, include_deleted=True)
        self.assertEqual([task.id for task in next_page.items], [second.id])
        events = self.store.events_after(watermark, None, 200, event_types=("task.deleted",))
        self.assertEqual([event.type for event in events], ["task.deleted"])
        self.store.close()
        self.store.initialize()
        self.assertEqual(self.store.current_event_id(), events[-1].event_id)
        self.assertEqual(self.store.get_task(second.id), second)

    def test_sqlite_constraints_reject_invalid_status_foreign_key_and_duplicate_key(self):
        task = self.create()
        second = self.create(2)
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            for statement in (
                "UPDATE tasks SET status='unknown'",
                "UPDATE tasks SET timeout_seconds=0",
                "UPDATE tasks SET rerun_of_task_id='missing'",
            ):
                with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(statement)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE tasks SET idempotency_key=? WHERE id=?", (task.idempotency_key, str(second.id)))

    def test_create_list_and_claim_use_sqlite_fifo(self):
        tasks = [self.create(index) for index in range(1, 7)]

        claimed = self.store.claim_queued(5)
        page = self.store.list_tasks(limit=10)

        self.assertEqual([task.id for task in claimed], [task.id for task in tasks[:5]])
        self.assertTrue(all(task.status == TaskStatus.RUNNING for task in claimed))
        self.assertEqual(page.items[-1].status, TaskStatus.QUEUED)
        self.assertTrue(all(task.started_at is not None for task in claimed))

    def test_transition_cas_and_event_failure_rollback_together(self):
        task = self.create()
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
        task = self.create()
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
        source = self.create()
        with self.assertRaises(TaskStateConflict):
            self.create(2, rerun_of=source.id)
        source, _ = self.store.transition_task(
            source.id,
            TaskStatus.CANCELLED,
            expected_version=source.version,
        )
        rerun = self.create(3, rerun_of=source.id)
        self.assertEqual(rerun.rerun_of_task_id, source.id)

        deleted, event = self.store.tombstone_task(source.id)
        repeated, repeated_event = self.store.tombstone_task(source.id)
        self.assertIsNotNone(deleted.deleted_at)
        self.assertEqual(repeated, deleted)
        self.assertEqual(repeated_event.event_id, event.event_id)
        self.assertIn(source.id, self.store.deleted_task_ids())
        with self.assertRaises(TaskNotFound):
            self.store.get_task(source.id)
