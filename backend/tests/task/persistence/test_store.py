"""验证 TaskStore façade 的 CRUD、CAS 与统一写事务边界。

本文件只覆盖 Store 自身拥有的 Task 查询/状态写入和 rollback；schema、Question、Event
与 session deletion 的专项行为分别位于同目录对应测试文件。
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from uuid import UUID

from backend.task.models import (
    CreateTaskCommand,
    TaskNotFound,
    TaskStateConflict,
    TaskStatus,
    TaskVersionConflict,
)
from backend.task.persistence.store import TaskPersistenceError, TaskStore


class TaskStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database_path = self.root / "task/task-runtime.sqlite3"
        self.store = TaskStore(self.database_path)
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

    async def test_create_and_list_use_fifo_row_ids(self):
        first = await self.store.create_task(self.command(1))
        second = await self.store.create_task(self.command(2))

        page = await self.store.list_tasks(limit=1)
        next_page = await self.store.list_tasks(
            after_row_id=page.next_cursor,
            limit=10,
        )

        self.assertEqual((first.row_id, second.row_id), (1, 2))
        self.assertEqual([item.id for item in page.items], [first.id])
        self.assertEqual(page.next_cursor, first.row_id)
        self.assertEqual([item.id for item in next_page.items], [second.id])

    async def test_rerun_requires_a_visible_terminal_source(self):
        source = await self.store.create_task(self.command(1))
        with self.assertRaises(TaskStateConflict):
            await self.store.create_task(self.command(2, rerun_of=source.id))

        source, _ = await self.store.transition_task(
            source.id,
            TaskStatus.CANCELLED,
            expected_version=source.version,
        )
        rerun = await self.store.create_task(self.command(3, rerun_of=source.id))
        self.assertEqual(rerun.rerun_of_task_id, source.id)

        await self.store.tombstone_task(source.id)
        with self.assertRaises(TaskNotFound):
            await self.store.create_task(self.command(4, rerun_of=source.id))

    async def test_transition_is_atomic_and_uses_compare_and_swap(self):
        task = await self.store.create_task(self.command())
        starting, event = await self.store.transition_task(
            task.id,
            TaskStatus.STARTING,
            expected_version=task.version,
            pi_session_id=str(task.id),
            pi_session_path=self.root / "sessions" / str(task.id),
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
                BEGIN SELECT RAISE(ABORT, 'event rejected'); END
                """
            )

        with self.assertRaisesRegex(
            TaskPersistenceError,
            "rejected a state or event write",
        ):
            await self.store.transition_task(
                task.id,
                TaskStatus.STARTING,
                expected_version=task.version,
            )

        unchanged = await self.store.get_task(task.id)
        self.assertEqual(unchanged.status, TaskStatus.QUEUED)
        self.assertEqual(unchanged.version, 1)


if __name__ == "__main__":
    unittest.main()
