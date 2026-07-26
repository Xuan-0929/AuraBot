"""验证 Plan 聚合、值对象和公开导出；以手写步骤定义驱动领域生命周期并约束 Harness 所消费的 Plan 模块，
不负责 MCP 路由、LLM 调用或 Task 调度。"""

import unittest
from dataclasses import FrozenInstanceError

from backend.agent.task.harness import plan as plan_module
from backend.agent.task.harness.plan import (
    Plan,
    PlanOperationError,
    PlanStep,
    PlanStepDefinition,
    build_snapshot,
    validate_plan_definition,
)


def step(step_id: str) -> PlanStepDefinition:
    return PlanStepDefinition(
        id=step_id,
        title=f"Title {step_id}",
        description=f"Do {step_id}",
        expected_output=f"Output {step_id}",
    )


class PlanDomainTests(unittest.TestCase):
    def test_plan_module_preserves_its_public_export_contract(self):
        self.assertEqual(
            set(plan_module.__all__),
            {
                "CompletePlanStepResult",
                "CreatePlanResult",
                "MAX_PLAN_STEPS",
                "MIN_PLAN_STEPS",
                "PLAN_ERROR_MESSAGES",
                "Plan",
                "PlanOperationError",
                "PlanSnapshot",
                "PlanStep",
                "PlanStepDefinition",
                "PlanStepStatus",
                "STEP_ID_PATTERN",
                "UpdatePlanResult",
                "build_snapshot",
                "validate_plan_definition",
            },
        )

    def test_plan_aggregate_owns_lifecycle_transitions(self):
        plan = Plan.create("Ship", (step("s1"), step("s2")))

        self.assertEqual(plan.snapshot.current_step_id, "s1")
        completed = plan.complete_step("s1", "Built")
        self.assertEqual(completed.next_step_id, "s2")

        updated = plan.update(
            "Ship safely",
            (step("s1"), step("s3")),
        )
        self.assertEqual(updated.current_step_id, "s3")
        self.assertEqual(plan.snapshot.goal, "Ship safely")

        final = plan.complete_step("s3", "Verified")
        self.assertTrue(final.is_complete)
        self.assertTrue(plan.snapshot.is_complete)

    def test_protocol_entities_are_frozen_and_steps_are_tuples(self):
        snapshot = build_snapshot(
            "Goal",
            (
                PlanStep(
                    id="s1",
                    title="Title s1",
                    description="Do s1",
                    expected_output="Output s1",
                    status="pending",
                    completion_summary=None,
                ),
            ),
        )

        self.assertIsInstance(snapshot.steps, tuple)
        with self.assertRaises(FrozenInstanceError):
            snapshot.goal = "Changed"
        with self.assertRaises(FrozenInstanceError):
            snapshot.steps[0].status = "completed"

    def test_definition_validation_rejects_invalid_counts_ids_and_duplicates(self):
        invalid_cases = (
            ("Goal", ()),
            ("Goal", tuple(step(f"s{index}") for index in range(31))),
            ("Goal", (step("bad id"),)),
            ("Goal", (step("duplicate"), step("duplicate"))),
        )
        for goal, steps in invalid_cases:
            with self.subTest(goal=goal, count=len(steps)):
                with self.assertRaises(PlanOperationError) as raised:
                    validate_plan_definition(goal, steps)
                self.assertEqual(raised.exception.code, "invalid_plan")

    def test_snapshot_rejects_non_prefix_completion(self):
        with self.assertRaises(PlanOperationError) as raised:
            build_snapshot(
                "Goal",
                (
                    PlanStep(
                        "s1", "One", "Do one", "One done", "pending", None
                    ),
                    PlanStep(
                        "s2", "Two", "Do two", "Two done", "completed", "done"
                    ),
                ),
            )
        self.assertEqual(raised.exception.code, "invalid_plan")


if __name__ == "__main__":
    unittest.main()
