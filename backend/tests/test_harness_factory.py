import unittest

from backend.agent.task.Harness import Harness
from backend.agent.task.HarnessFactory import HarnessFactory
from backend.agent.task.PlanAndSaveHarness import PlanAndSaveHarness
from backend.agent.task.ReActHarness import ReActHarness
from backend.agent.task.Task import Task, TaskType


class FakeLLMClient:
    pass


class GoalHarness(Harness):
    async def run(self) -> object:
        return "goal result"


def make_task(task_id: int, task_type: TaskType) -> Task:
    return Task(
        task_id=task_id,
        task_title=f"task-{task_id}",
        parent_session_id="main-session",
        task_type=task_type,
        task_prompt="do work",
    )


class HarnessFactoryTests(unittest.TestCase):
    def setUp(self):
        self.llm_client = FakeLLMClient()
        self.factory = HarnessFactory(llm_client=self.llm_client)

    def test_creates_react_harness_for_react_task(self):
        task = make_task(1, TaskType.ReAct)

        harness = self.factory.create(task)

        self.assertIsInstance(harness, ReActHarness)
        self.assertIs(harness.task, task)
        self.assertIs(harness.llm_client, self.llm_client)

    def test_creates_plan_harness_for_plan_and_save_task(self):
        task = make_task(2, TaskType.PlanAndSave)

        harness = self.factory.create(task)

        self.assertIsInstance(harness, PlanAndSaveHarness)
        self.assertIs(harness.task, task)
        self.assertIs(harness.llm_client, self.llm_client)

    def test_each_creation_returns_an_isolated_harness(self):
        task = make_task(3, TaskType.ReAct)

        first = self.factory.create(task)
        second = self.factory.create(task)

        self.assertIsNot(first, second)
        self.assertIs(first.llm_client, second.llm_client)

    def test_goal_is_unsupported_until_registered(self):
        task = make_task(4, TaskType.Goal)

        with self.assertRaisesRegex(ValueError, "No harness registered.*Goal"):
            self.factory.create(task)

    def test_registers_custom_harness(self):
        self.factory.register(TaskType.Goal, GoalHarness)
        task = make_task(5, TaskType.Goal)

        harness = self.factory.create(task)

        self.assertIsInstance(harness, GoalHarness)
        self.assertIn(TaskType.Goal, self.factory.registered_task_types())

    def test_rejects_duplicate_registration_without_replace(self):
        with self.assertRaisesRegex(ValueError, "already registered.*ReAct"):
            self.factory.register(TaskType.ReAct, GoalHarness)

    def test_replaces_existing_registration_when_requested(self):
        self.factory.register(
            TaskType.ReAct,
            GoalHarness,
            replace=True,
        )

        harness = self.factory.create(make_task(6, TaskType.ReAct))

        self.assertIsInstance(harness, GoalHarness)

    def test_rejects_non_harness_registration(self):
        with self.assertRaisesRegex(TypeError, "must inherit from Harness"):
            self.factory.register(TaskType.Goal, object)

    def test_rejects_non_task_input(self):
        with self.assertRaisesRegex(TypeError, "task must be a Task"):
            self.factory.create(object())


if __name__ == "__main__":
    unittest.main()
