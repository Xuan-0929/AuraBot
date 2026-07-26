import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from backend.agent.task.model import Task, TaskState, TaskType
from backend.agent.task.harness.harness import Harness
from backend.agent.task.harness.plan import (
    PlanOperationError,
    PlanStepDefinition,
)
from backend.agent.task.harness.plan_and_solve import (
    PlanAndSolveHarness,
)
from backend.agent.task.harness.react import ReActHarness
from backend.agent.tools.tool import Tool
from backend.agent.tools.tool_group import ToolGroup


class FakeLLMClient:
    pass


PLAN_MCP_SERVER_URL = "http://127.0.0.1:6410/mcp"


def definition(
    step_id: str,
    *,
    title: str | None = None,
) -> PlanStepDefinition:
    return PlanStepDefinition(
        id=step_id,
        title=title or f"Title {step_id}",
        description=f"Do {step_id}",
        expected_output=f"Output {step_id}",
    )


def plan_harness(task: Task) -> PlanAndSolveHarness:
    with patch(
        "backend.config.get_config",
        return_value={"local_mcp_server": PLAN_MCP_SERVER_URL},
    ):
        return PlanAndSolveHarness(
            task=task,
            llm_client=FakeLLMClient(),
        )


def make_harness() -> PlanAndSolveHarness:
    return plan_harness(
        Task(
            task_id=100,
            task_title="Runtime plan",
            parent_session_id="main-session",
            task_type=TaskType.PlanAndSolve,
            task_prompt="Complete the plan.",
        )
    )


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
        harness = plan_harness(task)

        prompt = harness.build_system_prompt()

        self.assertIsInstance(harness, Harness)
        self.assertIn("Plan-and-Solve agent", prompt)
        self.assertIn("create_plan to create a finite phase plan", prompt)
        self.assertIn("checkpoint", prompt)
        self.assertIn("Working directory:\n/workspace", prompt)
        self.assertNotIn("20 ReAct rounds", prompt)
        self.assertIsNone(harness.plan)

    def test_inherits_common_tool_protocol_methods(self):
        self.assertNotIn("build_openai_tools", PlanAndSolveHarness.__dict__)
        self.assertNotIn("parse_completion", PlanAndSolveHarness.__dict__)
        self.assertNotIn("build_tool_result_messages", PlanAndSolveHarness.__dict__)

    def test_plan_harness_prepends_required_tools_to_common_encoding(self):
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
        plan = plan_harness(plan_task)

        react_definitions = react.build_openai_tools()
        plan_definitions = plan.build_openai_tools()

        self.assertEqual(
            [
                definition["function"]["name"]
                for definition in plan_definitions[:4]
            ],
            [
                "create_plan",
                "read_plan",
                "update_plan",
                "plan_step_complete",
            ],
        )
        self.assertEqual(plan_definitions[4:], react_definitions)

    def test_create_read_update_complete_normal_flow(self):
        harness = make_harness()
        steps = (definition("s1"), definition("s2"))

        created = harness.create_plan("Ship", steps)
        self.assertTrue(created.created)
        self.assertEqual(created.current_step_id, "s1")
        self.assertEqual(harness.read_plan().completed_step_count, 0)

        first = harness.complete_plan_step("s1", "Built and verified")
        self.assertEqual(first.next_step_id, "s2")
        updated = harness.update_plan(
            "Ship safely",
            (definition("s1"), definition("s3")),
        )
        self.assertEqual(updated.current_step_id, "s3")
        snapshot = harness.read_plan()
        self.assertEqual(snapshot.goal, "Ship safely")
        self.assertEqual([step.id for step in snapshot.steps], ["s1", "s3"])
        self.assertEqual(snapshot.steps[0].completion_summary, "Built and verified")

        completed = harness.complete_plan_step("s3", "Tests passed")
        self.assertIsNone(completed.next_step_id)
        self.assertTrue(completed.is_complete)
        self.assertTrue(harness.read_plan().is_complete)

    def test_create_is_idempotent_without_resetting_progress(self):
        harness = make_harness()
        steps = (definition("s1"), definition("s2"))
        harness.create_plan("Ship", steps)
        harness.complete_plan_step("s1", "done")

        replayed = harness.create_plan("Ship", steps)

        self.assertEqual(replayed.current_step_id, "s2")
        self.assertEqual(harness.plan.completed_step_count, 1)

    def test_create_conflict_and_completed_plan_write_rejections(self):
        harness = make_harness()
        harness.create_plan("Ship", (definition("s1"),))

        with self.assertRaises(PlanOperationError) as different:
            harness.create_plan("Different", (definition("s1"),))
        self.assertEqual(different.exception.code, "plan_already_exists")

        harness.complete_plan_step("s1", "done")
        for operation in (
            lambda: harness.create_plan("Ship", (definition("s1"),)),
            lambda: harness.update_plan("Ship", (definition("s1"),)),
        ):
            with self.assertRaises(PlanOperationError) as complete:
                operation()
            self.assertEqual(complete.exception.code, "invalid_task_state")

    def test_update_protects_completed_prefix_and_keeps_pending_step(self):
        harness = make_harness()
        harness.create_plan(
            "Ship",
            (definition("s1"), definition("s2")),
        )
        harness.complete_plan_step("s1", "done")

        immutable_cases = (
            (definition("changed"), definition("s2")),
            (definition("s1", title="Changed"), definition("s2")),
        )
        for definitions in immutable_cases:
            with self.subTest(definitions=definitions):
                with self.assertRaises(PlanOperationError) as raised:
                    harness.update_plan("Ship", definitions)
                self.assertEqual(
                    raised.exception.code,
                    "completed_step_immutable",
                )

        with self.assertRaises(PlanOperationError) as no_pending:
            harness.update_plan("Ship", (definition("s1"),))
        self.assertEqual(no_pending.exception.code, "invalid_plan")

    def test_complete_rejects_missing_skip_and_changed_replay(self):
        harness = make_harness()
        harness.create_plan(
            "Ship",
            (definition("s1"), definition("s2")),
        )

        with self.assertRaises(PlanOperationError) as missing:
            harness.complete_plan_step("missing", "done")
        self.assertEqual(missing.exception.code, "step_not_found")
        with self.assertRaises(PlanOperationError) as skipped:
            harness.complete_plan_step("s2", "done")
        self.assertEqual(skipped.exception.code, "step_not_current")

        harness.complete_plan_step("s1", "original")
        harness.update_plan(
            "Ship",
            (definition("s1"), definition("s3")),
        )
        replay = harness.complete_plan_step("s1", "original")
        self.assertEqual(replay.next_step_id, "s3")
        with self.assertRaises(PlanOperationError) as changed:
            harness.complete_plan_step("s1", "different")
        self.assertEqual(changed.exception.code, "step_already_completed")

    def test_concurrent_completion_has_one_transition_and_safe_replays(self):
        harness = make_harness()
        harness.create_plan(
            "Ship",
            (definition("s1"), definition("s2")),
        )

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(
                executor.map(
                    lambda _: harness.complete_plan_step("s1", "done"),
                    range(4),
                )
            )

        self.assertTrue(all(result.next_step_id == "s2" for result in results))
        self.assertEqual(harness.plan.completed_step_count, 1)

    def test_terminal_task_rejects_plan_operations(self):
        harness = make_harness()
        harness.create_plan("Ship", (definition("s1"),))
        harness.task.interrupt()

        for operation in (
            harness.read_plan,
            lambda: harness.complete_plan_step("s1", "done"),
        ):
            with self.assertRaises(PlanOperationError) as raised:
                operation()
            self.assertEqual(raised.exception.code, "invalid_task_state")

if __name__ == "__main__":
    unittest.main()
