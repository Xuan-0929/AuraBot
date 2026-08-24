"""验证 persistence.events 的全局 cursor、单 Task sequence 与 replay 过滤。

测试只通过 TaskStore 公共门面提交状态和输出；Event helper 在同一 Store connection 与
transaction 中工作，SQLite 仍是 SSE replay 的唯一事实源。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.task.models import CreateTaskCommand, TaskStatus
from backend.task.persistence.store import TaskStore


class TaskEventPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_global_cursor_and_per_task_sequence_remain_ordered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root / "task.sqlite3")
            await store.initialize()
            try:
                first = await store.create_task(
                    CreateTaskCommand("One", "Do one", root, 30)
                )
                second = await store.create_task(
                    CreateTaskCommand("Two", "Do two", root, 30)
                )
                first, _ = await store.transition_task(
                    first.id,
                    TaskStatus.STARTING,
                    expected_version=first.version,
                )
                await store.append_output(first.id, "chunk")
                global_events = await store.events_after(0, None, 20)
                first_events = await store.events_after(0, first.id, 20)
                later_events = await store.events_after(
                    global_events[0].event_id, None, 20
                )
            finally:
                await store.close()

        self.assertEqual([event.event_id for event in global_events], [1, 2, 3, 4])
        self.assertEqual(
            [event.task_sequence for event in first_events],
            [1, 2, 3],
        )
        self.assertTrue(all(event.event_id > 1 for event in later_events))
        self.assertEqual(second.row_id, 2)


if __name__ == "__main__":
    unittest.main()
