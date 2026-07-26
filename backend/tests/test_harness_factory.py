import unittest
from unittest.mock import patch

from backend.agent.task.harness.Harness import Harness
from backend.agent.task.harness.HarnessFactory import (
    HarnessBuildContext,
    HarnessFactory,
)
from backend.agent.task.harness.PlanAndSolveHarness import PlanAndSolveHarness
from backend.agent.task.harness.ReActHarness import ReActHarness
from backend.agent.task.harness.adaptors.AdaptorRegistry import (
    AdaptorRegistry,
)
from backend.agent.task.Task import Task, TaskType
from backend.agent.capabilities.lease import CapabilitySession
from backend.agent.skills.SkillGroupManager import SkillGroupManager
from backend.agent.tools.ToolGroupManager import ToolGroupManager
from backend.agent.tools.native.PlanToolGroup import create_plan_tool_group


class FakeLLMClient:
    pass


class GoalHarness(Harness):
    def build_system_prompt(self) -> str:
        return "Goal"

    async def run(self) -> object:
        return "goal result"


class AbstractHarness(Harness):
    pass


def goal_builder(context: HarnessBuildContext) -> Harness:
    return GoalHarness(
        task=context.task,
        llm_client=context.task_llm_client,
        tool_call_gateway=context.tool_call_gateway,
        adaptor_registry=context.adaptor_registry,
        capability_session=context.capability_session,
    )


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
        config_patcher = patch(
            "backend.config.get_config",
            return_value={
                "local_mcp_server": "http://127.0.0.1:6410/mcp"
            },
        )
        config_patcher.start()
        self.addCleanup(config_patcher.stop)
        self.llm_client = FakeLLMClient()
        self.factory = HarnessFactory(task_llm_client=self.llm_client)

    def test_creates_react_harness_for_react_task(self):
        task = make_task(1, TaskType.ReAct)

        harness = self.factory.create(task)

        self.assertIsInstance(harness, ReActHarness)
        self.assertIs(harness.task, task)
        self.assertIs(harness.llm_client, self.llm_client)
        self.assertEqual(task.tool_groups, ())

    def test_factory_exposes_task_specific_llm_dependency_name(self):
        factory = HarnessFactory(task_llm_client=self.llm_client)

        self.assertIs(factory.task_llm_client, self.llm_client)

    def test_creates_plan_harness_for_plan_and_solve_task(self):
        task = make_task(2, TaskType.PlanAndSolve)

        harness = self.factory.create(task)

        self.assertIsInstance(harness, PlanAndSolveHarness)
        self.assertIs(harness.task, task)
        self.assertIs(harness.llm_client, self.llm_client)
        self.assertEqual(task.tool_groups[0].name, "Plan")

    def test_plan_harness_acquires_native_group_through_capability_session(self):
        plan_group = create_plan_tool_group("http://127.0.0.1:7410/mcp")
        tool_manager = ToolGroupManager(
            loader=lambda name: None,
            native_factories={"Plan": lambda: plan_group},
        )
        capability_session = CapabilitySession(
            tool_manager,
            SkillGroupManager(loader=lambda name: None),
        )
        task = make_task(20, TaskType.PlanAndSolve)

        harness = self.factory.create(
            task,
            capability_session=capability_session,
        )

        self.assertIs(harness.capability_session, capability_session)
        self.assertIs(task.tool_groups[0], plan_group)
        self.assertEqual(tool_manager.reference_count("Plan"), 1)
        capability_session.close()
        self.assertEqual(tool_manager.reference_count("Plan"), 0)

    def test_each_creation_returns_an_isolated_harness(self):
        task = make_task(3, TaskType.ReAct)

        first = self.factory.create(task)
        second = self.factory.create(task)

        self.assertIsNot(first, second)
        self.assertIs(first.llm_client, second.llm_client)
        self.assertIs(first.adaptor_registry, second.adaptor_registry)
        self.assertIsNot(
            first.tool_call_gateway,
            second.tool_call_gateway,
        )

    def test_injects_registry_and_builds_gateway_for_each_task(self):
        registry = AdaptorRegistry()
        gateway_calls = []
        factory = HarnessFactory(
            task_llm_client=self.llm_client,
            adaptor_registry=registry,
            tool_call_gateway_factory=lambda task: gateway_calls.append(task)
            or object(),
        )
        task = make_task(30, TaskType.ReAct)

        harness = factory.create(task)

        self.assertIs(harness.adaptor_registry, registry)
        self.assertEqual(gateway_calls, [task])

    def test_goal_is_unsupported_until_registered(self):
        task = make_task(4, TaskType.Goal)

        with self.assertRaisesRegex(ValueError, "No harness registered.*Goal"):
            self.factory.create(task)

    def test_registers_custom_harness(self):
        self.factory.register(TaskType.Goal, goal_builder)
        task = make_task(5, TaskType.Goal)

        harness = self.factory.create(task)

        self.assertIsInstance(harness, GoalHarness)
        self.assertIn(TaskType.Goal, self.factory.registered_task_types())

    def test_rejects_duplicate_registration_without_replace(self):
        with self.assertRaisesRegex(ValueError, "already registered.*ReAct"):
            self.factory.register(TaskType.ReAct, goal_builder)

    def test_replaces_existing_registration_when_requested(self):
        self.factory.register(
            TaskType.ReAct,
            goal_builder,
            replace=True,
        )

        harness = self.factory.create(make_task(6, TaskType.ReAct))

        self.assertIsInstance(harness, GoalHarness)

    def test_rejects_non_callable_builder(self):
        with self.assertRaisesRegex(TypeError, "builder must be callable"):
            self.factory.register(TaskType.Goal, object())

    def test_rejects_builder_that_returns_non_harness(self):
        self.factory.register(TaskType.Goal, lambda context: object())

        with self.assertRaisesRegex(TypeError, "builder must return Harness"):
            self.factory.create(make_task(7, TaskType.Goal))

    def test_rejects_non_task_input(self):
        with self.assertRaisesRegex(TypeError, "task must be a Task"):
            self.factory.create(object())

if __name__ == "__main__":
    unittest.main()
