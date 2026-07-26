import unittest
from unittest.mock import patch

from backend.agent.task.Task import Task, TaskType
from backend.agent.task.harness.PlanAndSolveHarness import (
    PlanAndSolveHarness,
)
from backend.agent.task.harness.ReActHarness import ReActHarness
from backend.agent.tools.Tool import Tool
from backend.agent.tools.ToolGroup import ToolGroup
from backend.agent.tools.core.ToolExceptions import DuplicateToolNameError
from backend.agent.tools.native.PlanToolGroup import (
    PLAN_TOOL_GROUP_NAME,
    PLAN_TOOL_NAMES,
    create_plan_tool_group,
)


SERVER_URL = "http://127.0.0.1:7410/mcp"


class FakeLLMClient:
    pass


def task(task_type, groups=None):
    return Task(
        1,
        "Task",
        "session",
        task_type,
        "Work",
        tool_groups=groups,
    )


def plan_harness(plan_task):
    with patch(
        "backend.config.get_config",
        return_value={"local_mcp_server": SERVER_URL},
    ):
        return PlanAndSolveHarness(
            task=plan_task,
            llm_client=FakeLLMClient(),
        )


class HarnessToolGroupInjectionTests(unittest.TestCase):
    def test_react_declares_no_required_tool_groups(self):
        ordinary = ToolGroup("ordinary", [], "Ordinary")
        react_task = task(TaskType.ReAct, [ordinary])

        ReActHarness(task=react_task, llm_client=FakeLLMClient())

        self.assertEqual(ReActHarness.required_tool_group_names, ())
        self.assertNotIn("inject_tool_group", ReActHarness.__dict__)
        self.assertEqual(react_task.tool_groups, (ordinary,))

    def test_plan_harness_declares_and_installs_required_group(self):
        ordinary = ToolGroup("ordinary", [], "Ordinary")
        plan_task = task(TaskType.PlanAndSolve, [ordinary])

        harness = plan_harness(plan_task)

        self.assertEqual(
            PlanAndSolveHarness.required_tool_group_names,
            (PLAN_TOOL_GROUP_NAME,),
        )
        self.assertNotIn("inject_tool_group", PlanAndSolveHarness.__dict__)
        self.assertIs(harness.task, plan_task)
        self.assertEqual(plan_task.tool_groups[0].name, PLAN_TOOL_GROUP_NAME)
        self.assertIs(plan_task.tool_groups[1], ordinary)
        self.assertEqual(
            tuple(tool.name for tool in plan_task.tool_groups[0].tools),
            PLAN_TOOL_NAMES,
        )
        self.assertTrue(
            all(
                tool.server_url == SERVER_URL
                for tool in plan_task.tool_groups[0].tools
            )
        )

    def test_repeated_harness_injection_is_idempotent(self):
        plan_task = task(TaskType.PlanAndSolve)
        plan_harness(plan_task)
        first = plan_task.tool_groups

        plan_harness(plan_task)

        self.assertIs(plan_task.tool_groups, first)
        self.assertEqual(len(plan_task.tool_groups), 1)

    def test_matching_existing_group_is_moved_first(self):
        ordinary = ToolGroup("ordinary", [], "Ordinary")
        canonical = create_plan_tool_group(SERVER_URL)
        plan_task = task(TaskType.PlanAndSolve, [ordinary, canonical])

        plan_harness(plan_task)

        self.assertIs(plan_task.tool_groups[0], canonical)
        self.assertIs(plan_task.tool_groups[1], ordinary)

    def test_conflicting_reserved_group_is_rejected_atomically(self):
        conflict = ToolGroup(PLAN_TOOL_GROUP_NAME, [], "Wrong")
        plan_task = task(TaskType.PlanAndSolve, [conflict])
        original = plan_task.tool_groups

        with self.assertRaisesRegex(ValueError, "canonical"):
            plan_harness(plan_task)

        self.assertIs(plan_task.tool_groups, original)

    def test_duplicate_plan_tool_in_other_group_uses_existing_error(self):
        occupied = ToolGroup(
            "occupied",
            [
                Tool(
                    "create_plan",
                    "Wrong",
                    SERVER_URL,
                    input_schema={"type": "object"},
                )
            ],
            "Occupied",
        )
        plan_task = task(TaskType.PlanAndSolve, [occupied])
        original = plan_task.tool_groups

        with self.assertRaises(DuplicateToolNameError):
            plan_harness(plan_task)

        self.assertIs(plan_task.tool_groups, original)


if __name__ == "__main__":
    unittest.main()
