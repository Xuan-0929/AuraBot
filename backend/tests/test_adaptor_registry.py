"""
验证 AdaptorRegistry 的 factory 路由、去重与 Task-local 实例化。

测试使用真实 Task 和 adaptor 类型，不覆盖 ToolExecutionPipeline、MCP 调用或 runtime 调度。
"""

import unittest

from backend.agent.task.domain import Task, TaskType
from backend.agent.task.harness.adaptors.base import (
    Adaptor,
    AdaptorRouteError,
)
from backend.agent.task.harness.adaptors.registry import (
    AdaptorRegistry,
)
from backend.agent.task.harness.adaptors.file_system import (
    FileSystemAdaptor,
)
from backend.agent.task.harness.adaptors.plan_context import (
    PlanContextAdaptor,
)


class RecordingAdaptor(Adaptor):
    def __init__(self, task: Task):
        self.task = task

    def adapt(self, entity):
        return entity


def make_task(task_id: int = 1) -> Task:
    return Task(
        task_id=task_id,
        task_title="Registry test",
        parent_session_id="main-session",
        task_type=TaskType.ReAct,
        task_prompt="Test the Adaptor registry.",
        workdir=f"/workspace/{task_id}",
    )


class AdaptorRegistryTests(unittest.TestCase):
    def test_creates_task_local_instances_from_named_factories(self):
        registry = AdaptorRegistry(
            {"RecordingAdaptor": RecordingAdaptor}
        )
        first_task = make_task(1)
        second_task = make_task(2)

        first = registry.create("RecordingAdaptor", first_task)
        second = registry.create("RecordingAdaptor", second_task)

        self.assertIsInstance(first, RecordingAdaptor)
        self.assertIs(first.task, first_task)
        self.assertIs(second.task, second_task)
        self.assertIsNot(first, second)

        with self.assertRaises(TypeError):
            registry.factories["LaterAdaptor"] = RecordingAdaptor

    def test_create_many_deduplicates_names_in_first_use_order(self):
        calls = []
        registry = AdaptorRegistry(
            {
                "First": lambda task: calls.append("First")
                or RecordingAdaptor(task),
                "Second": lambda task: calls.append("Second")
                or RecordingAdaptor(task),
            }
        )

        instances = registry.create_many(
            ["First", "Second", "First"],
            make_task(),
        )

        self.assertEqual(tuple(instances), ("First", "Second"))
        self.assertEqual(calls, ["First", "Second"])

    def test_default_registry_builds_filesystem_adaptor_for_task(self):
        adaptor = AdaptorRegistry().create(
            "FileSystemAdaptor",
            make_task(),
        )

        self.assertIsInstance(adaptor, FileSystemAdaptor)
        self.assertEqual(adaptor.current_directory, "/workspace/1")

    def test_default_registry_builds_task_local_plan_context_adaptor(self):
        adaptor = AdaptorRegistry().create(
            "PlanContextAdaptor",
            make_task(),
        )

        self.assertIsInstance(adaptor, PlanContextAdaptor)

    def test_rejects_unknown_or_invalid_factories(self):
        with self.assertRaisesRegex(AdaptorRouteError, "registered"):
            AdaptorRegistry().create("UnknownAdaptor", make_task())

        with self.assertRaisesRegex(TypeError, "must return Adaptor"):
            AdaptorRegistry(
                {"InvalidAdaptor": lambda task: object()}
            ).create("InvalidAdaptor", make_task())

        with self.assertRaises(TypeError):
            AdaptorRegistry({"": RecordingAdaptor})


if __name__ == "__main__":
    unittest.main()
