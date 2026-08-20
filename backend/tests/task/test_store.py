"""
验证 Python-owned Task SQLite 的事务、事件顺序、问题与受控 session 清理。

测试在临时数据库和真实文件系统上运行，不触碰 Electron 或 Memory 数据库；仅在模拟
不可删除目录时替换 shutil.rmtree，以证明数据库不会先提交逻辑删除。
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from backend.task.models import (
    CreateTaskCommand,
    TaskErrorCode,
    TaskNotFound,
    TaskStateConflict,
    TaskStatus,
    TaskVersionConflict,
)
from backend.task.store import TaskPersistenceError, TaskStore, TaskStoreMigrationError


class TaskStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database_path = self.root / "task/task-runtime.sqlite3"
        self.session_root = self.root / "task/sessions"
        self.store = TaskStore(self.database_path, self.session_root)
        await self.store.initialize()

    async def asyncTearDown(self):
        await self.store.close()
        self.temp.cleanup()

    def command(self, index: int = 1, *, rerun_of: UUID | None = None):
        working = self.root / f"project-{index}"
        working.mkdir(exist_ok=True)
        return CreateTaskCommand(
            title=f"Task {index}",
            prompt=f"Prompt {index}",
            working_directory=working.resolve(),
            timeout_seconds=1800,
            rerun_of_task_id=rerun_of,
        )

    async def test_fresh_schema_is_idempotent_and_separate(self):
        await self.store.initialize()
        with sqlite3.connect(self.database_path) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            foreign_key_violations = connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()

        self.assertTrue(
            {"schema_migrations", "tasks", "task_events", "task_questions"}
            <= tables
        )
        self.assertEqual(foreign_key_violations, [])
        self.assertNotIn("sessions", tables)
        self.assertNotIn("memories", tables)

    async def test_create_list_and_event_order_use_fifo_row_ids(self):
        first = await self.store.create_task(self.command(1))
        second = await self.store.create_task(self.command(2))

        page = await self.store.list_tasks(limit=1)
        next_page = await self.store.list_tasks(after_row_id=page.next_cursor, limit=10)
        events = await self.store.events_after(0, None, 10)

        self.assertEqual((first.row_id, second.row_id), (1, 2))
        self.assertEqual([item.id for item in page.items], [first.id])
        self.assertEqual(page.next_cursor, first.row_id)
        self.assertEqual([item.id for item in next_page.items], [second.id])
        self.assertIsNone(second.rerun_of_task_id)
        self.assertEqual([event.type for event in events], ["task.created", "task.created"])
        self.assertEqual([event.event_id for event in events], [1, 2])
        self.assertEqual([event.task_sequence for event in events], [1, 1])

    async def test_rerun_requires_a_visible_terminal_source(self):
        source = await self.store.create_task(self.command(1))
        with self.assertRaises(TaskStateConflict):
            await self.store.create_task(self.command(2, rerun_of=source.id))

        source, _ = await self.store.transition_task(
            source.id, TaskStatus.CANCELLED, expected_version=source.version
        )
        rerun = await self.store.create_task(self.command(3, rerun_of=source.id))
        self.assertEqual(rerun.rerun_of_task_id, source.id)

        await self.store.soft_delete(source.id)
        with self.assertRaises(TaskNotFound):
            await self.store.create_task(self.command(4, rerun_of=source.id))

    async def test_transition_is_atomic_and_uses_compare_and_swap(self):
        task = await self.store.create_task(self.command())
        starting, event = await self.store.transition_task(
            task.id,
            TaskStatus.STARTING,
            expected_version=task.version,
            pi_session_id=str(task.id),
            pi_session_path=self.session_root / str(task.id),
        )

        self.assertEqual(starting.status, TaskStatus.STARTING)
        self.assertEqual(starting.version, 2)
        self.assertEqual(event.task_sequence, 2)
        with self.assertRaises(TaskVersionConflict):
            await self.store.transition_task(
                task.id,
                TaskStatus.RUNNING,
                expected_version=1,
            )
        with self.assertRaises(TaskStateConflict):
            await self.store.transition_task(
                task.id,
                TaskStatus.COMPLETED,
                expected_version=starting.version,
            )

    async def test_event_insert_failure_rolls_back_status_change(self):
        task = await self.store.create_task(self.command())
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                CREATE TRIGGER reject_status_event
                BEFORE INSERT ON task_events
                WHEN NEW.event_type = 'task.status_changed'
                BEGIN
                  SELECT RAISE(ABORT, 'event rejected');
                END
                """
            )

        with self.assertRaisesRegex(TaskPersistenceError, "rejected a state or event write"):
            await self.store.transition_task(
                task.id,
                TaskStatus.STARTING,
                expected_version=task.version,
            )

        unchanged = await self.store.get_task(task.id)
        self.assertEqual(unchanged.status, TaskStatus.QUEUED)
        self.assertEqual(unchanged.version, 1)

    async def test_question_lifecycle_is_persisted_with_business_events(self):
        task = await self.store.create_task(self.command())
        task, _ = await self.store.transition_task(
            task.id, TaskStatus.STARTING, expected_version=task.version
        )
        task, _ = await self.store.transition_task(
            task.id, TaskStatus.RUNNING, expected_version=task.version
        )

        waiting, question, raised = await self.store.raise_question(
            task.id,
            "选择部署环境",
            ("staging", "production"),
            "rpc-ui-1",
            expected_version=task.version,
            active_runtime_ms=123,
        )
        answered, saved_question, answered_event = await self.store.answer_question(
            task.id,
            question.id,
            "staging",
            expected_version=waiting.version,
        )

        self.assertEqual(waiting.status, TaskStatus.WAITING)
        self.assertEqual(waiting.active_runtime_ms, 123)
        self.assertEqual(raised.type, "task.question_raised")
        self.assertEqual(saved_question.answer, "staging")
        self.assertEqual(answered.status, TaskStatus.WAITING)
        self.assertEqual(answered_event.type, "task.question_answered")
        self.assertEqual(await self.store.pending_question(task.id), None)

    async def test_soft_delete_hides_terminal_task_and_keeps_audit_rows(self):
        task = await self.store.create_task(self.command())
        task, _ = await self.store.transition_task(
            task.id, TaskStatus.CANCELLED, expected_version=task.version
        )
        session = self.session_root / str(task.id)
        session.mkdir(parents=True)
        (session / "session.jsonl").write_text("data", encoding="utf-8")

        deleted, event = await self.store.soft_delete(task.id)

        self.assertIsNotNone(deleted.deleted_at)
        self.assertEqual(event.type, "task.deleted")
        self.assertFalse(session.exists())
        with self.assertRaises(TaskNotFound):
            await self.store.get_task(task.id)
        visible = await self.store.get_task(task.id, include_deleted=True)
        events = await self.store.events_after(0, task.id, 100)
        self.assertEqual(visible.id, task.id)
        self.assertEqual(events[-1].type, "task.deleted")

    async def test_soft_delete_requires_terminal_and_unlinks_only_symlink(self):
        active = await self.store.create_task(self.command())
        with self.assertRaises(TaskStateConflict):
            await self.store.soft_delete(active.id)

        task = await self.store.create_task(self.command(2))
        task, _ = await self.store.transition_task(
            task.id, TaskStatus.CANCELLED, expected_version=task.version
        )
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("keep", encoding="utf-8")
        self.session_root.mkdir(parents=True, exist_ok=True)
        session_link = self.session_root / str(task.id)
        session_link.symlink_to(outside, target_is_directory=True)

        await self.store.soft_delete(task.id)

        self.assertFalse(session_link.exists())
        self.assertEqual((outside / "keep.txt").read_text(encoding="utf-8"), "keep")

    async def test_cleanup_failure_does_not_mark_deleted_and_retry_succeeds(self):
        task = await self.store.create_task(self.command())
        task, _ = await self.store.transition_task(
            task.id, TaskStatus.STARTING, expected_version=task.version
        )
        task, _ = await self.store.transition_task(
            task.id, TaskStatus.FAILED, expected_version=task.version,
            error_code=TaskErrorCode.INTERNAL_ERROR,
            error_message="failed",
        )
        session = self.session_root / str(task.id)
        session.mkdir(parents=True)

        with patch("backend.task.store.shutil.rmtree", side_effect=OSError("denied")):
            with self.assertRaisesRegex(OSError, "denied"):
                await self.store.soft_delete(task.id)

        self.assertIsNone((await self.store.get_task(task.id)).deleted_at)
        await self.store.soft_delete(task.id)
        with self.assertRaises(TaskNotFound):
            await self.store.get_task(task.id)


class TaskStoreMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_initial_schema_rolls_back_when_version_record_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "task.sqlite3"
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
                    BEGIN
                      SELECT RAISE(ABORT, 'migration record rejected');
                    END
                    """
                )

            store = TaskStore(database, root / "sessions")
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
            root = Path(directory)
            database = root / "task.sqlite3"
            with sqlite3.connect(database) as connection:
                connection.execute(
                    "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, name TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO schema_migrations(version, name) VALUES (999, 'future')"
                )
            store = TaskStore(database, root / "sessions")
            with self.assertRaises(TaskStoreMigrationError):
                await store.initialize()
            await store.close()


if __name__ == "__main__":
    unittest.main()
