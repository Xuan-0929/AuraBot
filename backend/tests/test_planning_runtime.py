import unittest

from backend.agent.task.model import Task, TaskType
from backend.agent.task.harness.plan import PlanStepDefinition
from backend.agent.task.harness.planning import (
    PlanningHarness,
    PlanningRuntime,
)
from backend.app.services.plan_tool_service import PlanToolApplicationService


def definition(step_id: str) -> PlanStepDefinition:
    return PlanStepDefinition(
        id=step_id,
        title=f"Title {step_id}",
        description=f"Do {step_id}",
        expected_output=f"Output {step_id}",
    )


class _PlanningHarnessStub:
    def __init__(self, task: Task):
        self.task = task
        self.planning_runtime = PlanningRuntime(task)


class _TaskManagerStub:
    def __init__(self, runtime):
        self.runtime = runtime

    def get_runtime(self, task_name: str):
        if task_name == self.runtime.task.task_title:
            return self.runtime
        return None


class PlanningRuntimeTests(unittest.TestCase):
    def test_runtime_owns_plan_and_enforces_task_lifecycle(self):
        task = Task(
            1,
            "Plan task",
            "session",
            TaskType.PlanAndSolve,
            "Ship",
        )
        runtime = PlanningRuntime(task)

        created = runtime.create("Ship", (definition("s1"),))
        self.assertEqual(created.current_step_id, "s1")
        self.assertEqual(runtime.read().goal, "Ship")

        completed = runtime.complete_step("s1", "Verified")
        self.assertTrue(completed.is_complete)

    def test_application_service_depends_on_structural_planning_protocol(self):
        task = Task(
            1,
            "Plan task",
            "session",
            TaskType.PlanAndSolve,
            "Ship",
        )
        harness = _PlanningHarnessStub(task)
        self.assertIsInstance(harness, PlanningHarness)
        service = PlanToolApplicationService(_TaskManagerStub(harness))

        created = service.create(
            "Plan task",
            "Ship",
            (definition("s1"),),
        )

        self.assertEqual(created["current_step_id"], "s1")


if __name__ == "__main__":
    unittest.main()
