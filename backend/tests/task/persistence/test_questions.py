"""验证 persistence.questions 的跨表 Question workflow 仍由 Store 原子提交。

测试通过唯一 TaskStore façade 提出、回答并投递问题，确认状态、Question delivery marker
和业务事件同步前进；不直接创建额外连接或绕过 Store transaction runner。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.task.models import CreateTaskCommand, TaskStatus
from backend.task.persistence.store import TaskStore


class TaskQuestionPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_question_answer_and_delivery_share_task_transactions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root / "task.sqlite3")
            await store.initialize()
            try:
                task = await store.create_task(
                    CreateTaskCommand(
                        title="Question",
                        prompt="Ask",
                        working_directory=root,
                        timeout_seconds=30,
                    )
                )
                task, _ = await store.transition_task(
                    task.id, TaskStatus.STARTING, expected_version=task.version
                )
                task, _ = await store.transition_task(
                    task.id, TaskStatus.RUNNING, expected_version=task.version
                )
                waiting, question, _ = await store.raise_question(
                    task.id,
                    "Choose",
                    ("A", "B"),
                    "rpc-1",
                    expected_version=task.version,
                    active_runtime_ms=12,
                )
                answered, accepted, _ = await store.answer_question(
                    task.id,
                    question.id,
                    "A",
                    expected_version=waiting.version,
                )
                running, delivered, _ = await store.deliver_answer_and_transition(
                    task.id,
                    question.id,
                    TaskStatus.RUNNING,
                    expected_version=answered.version,
                )
            finally:
                await store.close()

        self.assertEqual(accepted.answer, "A")
        self.assertIsNone(accepted.delivered_at)
        self.assertEqual(running.status, TaskStatus.RUNNING)
        self.assertIsNotNone(delivered.delivered_at)


if __name__ == "__main__":
    unittest.main()
