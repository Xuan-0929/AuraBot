"""任务运行时注册表与调度器组件的特征测试。"""

import unittest
from datetime import UTC, datetime

import backend.agent.task.runtime as task_runtime
from backend.agent.task.model import Task, TaskType
from backend.agent.task.runtime import _TaskRuntimeRecord, TaskStartSpec
from backend.agent.task.runtime_registry import TaskRuntimeRegistry
from backend.agent.task.scheduler import TaskScheduler


def record(task_id: int, name: str) -> _TaskRuntimeRecord:
    spec = TaskStartSpec(
        task_name=name,
        parent_session_id="main",
        task_type=TaskType.ReAct,
        task_prompt="Work",
    )
    return _TaskRuntimeRecord(
        task=Task(
            task_id,
            name,
            "main",
            TaskType.ReAct,
            "Work",
        ),
        start_spec=spec,
        created_at=datetime.now(UTC),
    )


class TaskRuntimeRegistryTests(unittest.TestCase):
    def test_runtime_public_exports_hide_mutable_runtime_records(self):
        self.assertNotIn("_TaskRuntimeRecord", task_runtime.__all__)

    def test_registry_enforces_id_and_name_identity(self):
        registry = TaskRuntimeRegistry()
        first = record(1, "first")
        registry.add(first)

        with self.assertRaisesRegex(ValueError, "Task ID"):
            registry.add(record(1, "second"))
        with self.assertRaisesRegex(ValueError, "Task name"):
            registry.add(record(2, "first"))

        self.assertIs(registry.get("first"), first)
        self.assertEqual(registry.list(), (first,))
        self.assertFalse(registry.remove(record(1, "first")))
        self.assertTrue(registry.remove(first))
        self.assertIsNone(registry.get("first"))


class TaskSchedulerTests(unittest.TestCase):
    def test_scheduler_preserves_fifo_and_enforces_running_slots(self):
        scheduler = TaskScheduler(max_running=1)
        first = record(1, "first")
        second = record(2, "second")
        scheduler.enqueue(first)
        scheduler.enqueue(second)

        self.assertEqual(first.queue_position, 1)
        self.assertEqual(second.queue_position, 2)
        self.assertIs(scheduler.take_ready(), first)
        scheduler.mark_running(first)
        self.assertIsNone(scheduler.take_ready())

        scheduler.mark_settled(first)
        self.assertIs(scheduler.take_ready(), second)
        self.assertIsNone(second.queue_position)


if __name__ == "__main__":
    unittest.main()
