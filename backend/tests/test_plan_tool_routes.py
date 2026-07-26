import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.agent.task.model import Task, TaskState, TaskType
from backend.agent.task.manager import TaskManager
from backend.agent.task.harness.plan_and_solve import (
    PlanAndSolveHarness,
)
from backend.agent.task.harness.react import ReActHarness
from backend.app.gateway.plan_tool_routes import (
    router,
)
from backend.app.gateway.dependencies import get_plan_tool_service
from backend.app.gateway.errors import application_error_handler
from backend.app.services.errors import ApplicationError
from backend.app.services.plan_tool_service import PlanToolApplicationService
from backend.tests.task_runtime_helpers import retain_test_harness


class FakeLLMClient:
    pass


STEPS = [
    {
        "id": "s1",
        "title": "First",
        "description": "Do first",
        "expected_output": "First done",
    },
    {
        "id": "s2",
        "title": "Second",
        "description": "Do second",
        "expected_output": "Second done",
    },
]


def runtime(task_type=TaskType.PlanAndSolve, *, name="Plan task"):
    task = Task(1, name, "session", task_type, "Work")
    harness_type = (
        PlanAndSolveHarness
        if task_type is TaskType.PlanAndSolve
        else ReActHarness
    )
    if harness_type is ReActHarness:
        return harness_type(task=task, llm_client=FakeLLMClient())
    with patch(
        "backend.config.get_config",
        return_value={"local_mcp_server": "http://127.0.0.1:6410/mcp"},
    ):
        return harness_type(task=task, llm_client=FakeLLMClient())


class PlanToolRouteTests(unittest.TestCase):
    def setUp(self):
        self.manager = TaskManager()
        application = FastAPI()
        application.add_exception_handler(
            ApplicationError,
            application_error_handler,
        )
        application.include_router(router)
        application.dependency_overrides[get_plan_tool_service] = (
            lambda: PlanToolApplicationService(self.manager)
        )
        self.client = TestClient(application)

    def tearDown(self):
        self.client.close()
        self.manager.shutdown()

    def test_complete_internal_api_flow_returns_frozen_protocol(self):
        harness = runtime()
        retain_test_harness(self.manager, harness)

        created = self.client.post(
            "/tool/plan/create",
            json={"task_name": "Plan task", "goal": "Ship", "steps": STEPS},
        )
        self.assertEqual(
            created.json(),
            {
                "created": True,
                "current_step_id": "s1",
                "total_step_count": 2,
            },
        )

        read = self.client.post(
            "/tool/plan/read",
            json={"task_name": "Plan task"},
        )
        self.assertEqual(read.status_code, 200)
        self.assertEqual(read.json()["current_step_id"], "s1")
        self.assertNotIn("task_name", read.json())

        completed = self.client.post(
            "/tool/plan/step/complete",
            json={
                "task_name": "Plan task",
                "step_id": "s1",
                "completion_summary": "Verified first",
            },
        )
        self.assertEqual(
            completed.json(),
            {
                "completed": True,
                "completed_step_id": "s1",
                "next_step_id": "s2",
                "is_complete": False,
            },
        )

        updated_steps = [STEPS[0], {**STEPS[1], "title": "Revised"}]
        updated = self.client.post(
            "/tool/plan/update",
            json={
                "task_name": "Plan task",
                "goal": "Ship safely",
                "steps": updated_steps,
            },
        )
        self.assertEqual(
            updated.json(),
            {
                "updated": True,
                "current_step_id": "s2",
                "total_step_count": 2,
            },
        )

    def test_routing_and_business_errors_have_safe_statuses(self):
        missing = self.client.post(
            "/tool/plan/read",
            json={"task_name": "missing"},
        )
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["error"]["code"], "task_not_found")

        wrong = runtime(TaskType.ReAct, name="Wrong")
        retain_test_harness(self.manager, wrong)
        invalid_type = self.client.post(
            "/tool/plan/read",
            json={"task_name": "Wrong"},
        )
        self.assertEqual(invalid_type.status_code, 409)
        self.assertEqual(
            invalid_type.json()["error"]["code"],
            "invalid_task_type",
        )

        plan = runtime(name="No plan")
        plan.task.task_id = 2
        retain_test_harness(self.manager, plan)
        not_found = self.client.post(
            "/tool/plan/read",
            json={"task_name": "No plan"},
        )
        self.assertEqual(not_found.status_code, 404)
        self.assertEqual(
            not_found.json()["error"]["code"],
            "plan_not_found",
        )

    def test_terminal_task_and_conflicts_return_409_without_leaks(self):
        harness = runtime()
        retain_test_harness(self.manager, harness)
        create_body = {
            "task_name": "Plan task",
            "goal": "Ship",
            "steps": STEPS,
        }
        self.client.post("/tool/plan/create", json=create_body)

        conflict = self.client.post(
            "/tool/plan/create",
            json={**create_body, "goal": "Different"},
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(
            conflict.json()["error"]["code"],
            "plan_already_exists",
        )

        harness.task.interrupt()
        terminal = self.client.post(
            "/tool/plan/read",
            json={"task_name": "Plan task"},
        )
        self.assertEqual(terminal.status_code, 409)
        self.assertEqual(
            terminal.json()["error"]["code"],
            "invalid_task_state",
        )
        self.assertNotIn("Plan task", terminal.text)
        self.assertNotIn("Traceback", terminal.text)

    def test_models_forbid_extra_fields_with_422(self):
        response = self.client.post(
            "/tool/plan/read",
            json={"task_name": "Task", "extra": True},
        )
        self.assertEqual(response.status_code, 422)

    def test_internal_routes_are_hidden_from_openapi(self):
        application = FastAPI()
        application.include_router(router)
        paths = application.openapi()["paths"]
        self.assertNotIn("/tool/plan/create", paths)


if __name__ == "__main__":
    unittest.main()
