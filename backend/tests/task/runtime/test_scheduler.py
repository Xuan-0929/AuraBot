"""
验证轻量 Scheduler 只负责 FIFO 队列、并发槽和 shutdown admission。

测试不创建 SQLite 或 subprocess；TaskRuntimeEngine 通过同一公开方法入队、移除、准入
与释放，确保调度策略可独立证明且不会拥有执行资源。
"""

from __future__ import annotations

import asyncio
import unittest
from uuid import uuid4

from backend.task.runtime.scheduler import FIFOScheduler, SchedulerClosed


class FIFOSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_admits_fifo_up_to_limit_and_waits_for_release(self):
        scheduler = FIFOScheduler(concurrency_limit=2)
        task_ids = [uuid4(), uuid4(), uuid4()]
        for task_id in task_ids:
            await scheduler.enqueue(task_id)

        self.assertEqual(await scheduler.admit(), task_ids[0])
        self.assertEqual(await scheduler.admit(), task_ids[1])
        third = asyncio.create_task(scheduler.admit())
        await asyncio.sleep(0)
        self.assertFalse(third.done())

        await scheduler.release(task_ids[0])
        self.assertEqual(await asyncio.wait_for(third, 0.1), task_ids[2])

    async def test_remove_and_reenqueue_puts_task_at_tail_without_duplicates(self):
        scheduler = FIFOScheduler(concurrency_limit=1)
        first, second = uuid4(), uuid4()
        await scheduler.enqueue(first)
        await scheduler.enqueue(second)
        await scheduler.enqueue(first)
        self.assertTrue(await scheduler.remove(first))
        await scheduler.enqueue(first)

        self.assertEqual(await scheduler.admit(), second)
        await scheduler.release(second)
        self.assertEqual(await scheduler.admit(), first)

    async def test_shutdown_rejects_new_and_wakes_waiters(self):
        scheduler = FIFOScheduler(concurrency_limit=1)
        waiter = asyncio.create_task(scheduler.admit())
        await asyncio.sleep(0)
        await scheduler.shutdown()

        with self.assertRaises(SchedulerClosed):
            await waiter
        with self.assertRaises(SchedulerClosed):
            await scheduler.enqueue(uuid4())


if __name__ == "__main__":
    unittest.main()
