"""验证 Task session quarantine、数据库 tombstone 与启动恢复的一致性。

测试使用真实临时 SQLite 和文件系统，覆盖强清理、幂等重试、DB rollback 恢复、启动
restore/purge/fail-closed 以及 symlink 安全；仅用 mock 制造真实的 rmtree 失败边界。
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.task.models import (
    CreateTaskCommand,
    TaskErrorCode,
    TaskNotFound,
    TaskStateConflict,
    TaskStatus,
)
from backend.task.persistence.sessions import (
    TaskSessionCleanupError,
    TaskSessionDeletion,
)
from backend.task.persistence.store import TaskPersistenceError, TaskStore


class TaskSessionDeletionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.session_root = self.root / "task/sessions"
        self.store = TaskStore(self.root / "task/task-runtime.sqlite3")
        self.deletion = TaskSessionDeletion(self.store, self.session_root)
        await self.store.initialize()
        await self.deletion.initialize()

    async def asyncTearDown(self):
        await self.store.close()
        self.temp.cleanup()

    def command(self, index: int = 1) -> CreateTaskCommand:
        working = self.root / f"project-{index}"
        working.mkdir(exist_ok=True)
        return CreateTaskCommand(
            title=f"Task {index}",
            prompt=f"Prompt {index}",
            working_directory=working,
            timeout_seconds=30,
        )

    async def terminal_task(self, index: int = 1):
        task = await self.store.create_task(self.command(index))
        task, _ = await self.store.transition_task(
            task.id,
            TaskStatus.CANCELLED,
            expected_version=task.version,
        )
        return task

    async def test_delete_tombstones_and_strongly_removes_session_idempotently(self):
        task = await self.terminal_task()
        session = self.session_root / str(task.id)
        session.mkdir(parents=True)
        (session / "session.jsonl").write_text("data", encoding="utf-8")

        deleted, event = await self.deletion.delete(task.id)

        self.assertIsNotNone(deleted.deleted_at)
        self.assertEqual(event.type, "task.deleted")
        self.assertFalse(session.exists())
        with self.assertRaises(TaskNotFound):
            await self.store.get_task(task.id)
        visible = await self.store.get_task(task.id, include_deleted=True)
        version = visible.version

        session.mkdir(parents=True)
        repeated, repeated_event = await self.deletion.delete(task.id)
        events = await self.store.events_after(0, task.id, 100)
        self.assertEqual(repeated.version, version)
        self.assertEqual(repeated_event.event_id, event.event_id)
        self.assertFalse(session.exists())
        self.assertEqual([item.type for item in events].count("task.deleted"), 1)

    async def test_delete_requires_terminal_and_unlinks_only_session_symlink(self):
        active = await self.store.create_task(self.command())
        with self.assertRaises(TaskStateConflict):
            await self.deletion.delete(active.id)

        task = await self.terminal_task(2)
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("keep", encoding="utf-8")
        session_link = self.session_root / str(task.id)
        session_link.symlink_to(outside, target_is_directory=True)

        await self.deletion.delete(task.id)

        self.assertFalse(session_link.exists())
        self.assertEqual((outside / "keep.txt").read_text(encoding="utf-8"), "keep")

    async def test_cleanup_failure_after_tombstone_is_retryable(self):
        task = await self.store.create_task(self.command())
        task, _ = await self.store.transition_task(
            task.id,
            TaskStatus.STARTING,
            expected_version=task.version,
        )
        task, _ = await self.store.transition_task(
            task.id,
            TaskStatus.FAILED,
            expected_version=task.version,
            error_code=TaskErrorCode.INTERNAL_ERROR,
            error_message="failed",
        )
        session = self.session_root / str(task.id)
        session.mkdir(parents=True)

        with patch(
            "backend.task.persistence.sessions.shutil.rmtree",
            side_effect=OSError("denied"),
        ):
            with self.assertRaises(TaskSessionCleanupError) as raised:
                await self.deletion.delete(task.id)

        staged = self.session_root / ".deleting" / str(task.id)
        self.assertTrue(raised.exception.tombstone_committed)
        self.assertTrue(staged.exists())
        with self.assertRaises(TaskNotFound):
            await self.store.get_task(task.id)
        await self.deletion.delete(task.id)
        self.assertFalse(staged.exists())

    async def test_tombstone_failure_restores_quarantined_session(self):
        task = await self.terminal_task()
        canonical = self.session_root / str(task.id)
        canonical.mkdir(parents=True)
        (canonical / "keep").write_text("data", encoding="utf-8")
        with sqlite3.connect(self.store.database_path) as connection:
            connection.execute(
                """CREATE TRIGGER reject_delete_event BEFORE INSERT ON task_events
                   WHEN NEW.event_type = 'task.deleted'
                   BEGIN SELECT RAISE(ABORT, 'delete rejected'); END"""
            )

        with self.assertRaises(TaskPersistenceError):
            await self.deletion.delete(task.id)

        self.assertTrue((canonical / "keep").exists())
        self.assertFalse((self.session_root / ".deleting" / str(task.id)).exists())
        self.assertIsNone((await self.store.get_task(task.id)).deleted_at)

    async def test_startup_restores_live_purges_deleted_and_ignores_unknown(self):
        live = await self.store.create_task(self.command(1))
        deleted = await self.terminal_task(2)
        await self.deletion.delete(deleted.id)
        deleting = self.session_root / ".deleting"
        deleting.mkdir(parents=True, exist_ok=True)
        (deleting / str(live.id)).mkdir()
        deleted_staged = deleting / str(deleted.id)
        deleted_staged.mkdir()
        unknown = deleting / "not-a-task"
        unknown.mkdir()

        await self.deletion.reconcile()

        self.assertTrue((self.session_root / str(live.id)).is_dir())
        self.assertFalse(deleted_staged.exists())
        self.assertTrue(unknown.exists())

    async def test_startup_fails_closed_when_both_session_paths_exist(self):
        task = await self.store.create_task(self.command())
        canonical = self.session_root / str(task.id)
        staged = self.session_root / ".deleting" / str(task.id)
        canonical.mkdir(parents=True)
        staged.mkdir(parents=True)

        with self.assertRaises(TaskSessionCleanupError):
            await self.deletion.reconcile()

        self.assertTrue(canonical.exists())
        self.assertTrue(staged.exists())

    async def test_delete_never_follows_symlinked_quarantine_root(self):
        task = await self.terminal_task()
        outside = self.root / "outside-quarantine"
        outside.mkdir()
        (outside / "keep").write_text("safe", encoding="utf-8")
        (self.session_root / ".deleting").symlink_to(
            outside,
            target_is_directory=True,
        )

        with self.assertRaises(TaskSessionCleanupError):
            await self.deletion.delete(task.id)

        self.assertEqual((outside / "keep").read_text(encoding="utf-8"), "safe")
        self.assertIsNone((await self.store.get_task(task.id)).deleted_at)


if __name__ == "__main__":
    unittest.main()
