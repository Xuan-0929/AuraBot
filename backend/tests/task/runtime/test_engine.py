"""验证 TaskRuntimeEngine 对单 Task 并发命令提供唯一串行化边界。

测试不启动 dispatcher 或 Pi；它直接并发获取同一 Task guard，证明 Manager 命令与
execution 主协程将共享同一把锁，而不同实现细节不会成为测试契约。
"""

from __future__ import annotations

import asyncio
import unittest
from uuid import uuid4

from backend.task.runtime.engine import TaskRuntimeEngine


class TaskRuntimeEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_task_guard_serializes_same_task(self) -> None:
        runtime = TaskRuntimeEngine(None, None, None, None)
        task_id = uuid4()
        first_entered = asyncio.Event()
        release_first = asyncio.Event()
        order: list[str] = []

        async def first() -> None:
            async with runtime.task_guard(task_id):
                order.append("first-enter")
                first_entered.set()
                await release_first.wait()
                order.append("first-exit")

        async def second() -> None:
            await first_entered.wait()
            async with runtime.task_guard(task_id):
                order.append("second-enter")

        first_task = asyncio.create_task(first())
        second_task = asyncio.create_task(second())
        await first_entered.wait()
        await asyncio.sleep(0)
        self.assertEqual(order, ["first-enter"])

        release_first.set()
        await asyncio.gather(first_task, second_task)
        self.assertEqual(order, ["first-enter", "first-exit", "second-enter"])


if __name__ == "__main__":
    unittest.main()
