import unittest

from backend.agent.task.Task import Task, TaskType
from backend.agent.task.harness.Harness import Harness
from backend.agent.task.harness.PlanAndSolveHarness import (
    PlanAndSolveHarness,
)
from backend.agent.task.harness.ReActHarness import ReActHarness
from backend.agent.tools.Tool import Tool
from backend.agent.tools.ToolGroup import ToolGroup


class FakeLLMClient:
    pass


class PlanAndSolveHarnessTests(unittest.TestCase):
    def test_is_concrete_harness_with_plan_specific_prompt(self):
        task = Task(
            task_id=1,
            task_title="Plan",
            parent_session_id="main-session",
            task_type=TaskType.PlanAndSolve,
            task_prompt="Complete the project.",
            workdir="/workspace",
            constraints="Checkpoint each phase.",
        )
        harness = PlanAndSolveHarness(
            task=task,
            llm_client=FakeLLMClient(),
        )

        prompt = harness.build_system_prompt()

        self.assertIsInstance(harness, Harness)
        self.assertIn("Plan-and-Solve agent", prompt)
        self.assertIn("Create a finite phase plan", prompt)
        self.assertIn("checkpoint", prompt)
        self.assertIn("Working directory:\n/workspace", prompt)
        self.assertNotIn("20 ReAct rounds", prompt)

    def test_inherits_common_tool_protocol_methods(self):
        self.assertNotIn("build_openai_tools", PlanAndSolveHarness.__dict__)
        self.assertNotIn("parse_completion", PlanAndSolveHarness.__dict__)
        self.assertNotIn("build_tool_result_messages", PlanAndSolveHarness.__dict__)

    def test_react_and_plan_harnesses_share_identical_tool_encoding(self):
        tool_groups = [
            ToolGroup(
                "utilities",
                [
                    Tool(
                        "ping",
                        "Return pong",
                        "http://localhost:6410/mcp",
                        input_schema={"type": "object", "properties": {}},
                    )
                ],
                "Utility tools",
            )
        ]
        react_task = Task(
            task_id=2,
            task_title="React",
            parent_session_id="main-session",
            task_type=TaskType.ReAct,
            task_prompt="React.",
            tool_groups=tool_groups,
        )
        plan_task = Task(
            task_id=3,
            task_title="Plan",
            parent_session_id="main-session",
            task_type=TaskType.PlanAndSolve,
            task_prompt="Plan.",
            tool_groups=tool_groups,
        )

        react = ReActHarness(
            task=react_task,
            llm_client=FakeLLMClient(),
        )
        plan = PlanAndSolveHarness(
            task=plan_task,
            llm_client=FakeLLMClient(),
        )

        self.assertEqual(
            react.build_openai_tools(),
            plan.build_openai_tools(),
        )


if __name__ == "__main__":
    unittest.main()
