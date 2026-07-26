import os
import asyncio
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

os.environ.setdefault(
    "AURABOT_CONFIG_PATH",
    str(Path(__file__).parent / "fixtures" / "runtime_config.yaml"),
)

from backend.app.gateway.task_events import encode_sse_event
from backend.app.services.errors import ApplicationError
from backend.agent.task.model import TaskState, TaskType
from backend.agent.task.runtime import TaskListSnapshot, TaskSnapshot
from backend.config import config as global_config
from backend.main import create_app


TASK_VIEW = {
    "task_id": 1,
    "task_name": "Inspect repo",
    "task_type": "react",
    "state": "initializing",
    "queue_position": 1,
    "created_at": "2026-07-26T00:00:00Z",
    "started_at": None,
    "settled_at": None,
    "result": None,
    "error": None,
}

TASK_SNAPSHOT = TaskSnapshot(
    task_id=1,
    task_name="Inspect repo",
    task_type=TaskType.ReAct,
    state=TaskState.Initializing,
    queue_position=1,
    created_at=datetime(2026, 7, 26, tzinfo=UTC),
    started_at=None,
    settled_at=None,
)


class FakeTaskService:
    def __init__(self):
        self.started = []
        self.cleared = []

    def start(self, spec):
        asyncio.get_running_loop()
        self.started.append(spec)
        return TASK_SNAPSHOT

    def list(self):
        return TaskListSnapshot(
            running_count=0,
            queued_count=1,
            tasks=(TASK_SNAPSHOT,),
        )

    def get(self, task_name):
        if task_name == "missing":
            raise ApplicationError(
                "task_not_found",
                "No Task has that name.",
            )
        return TASK_SNAPSHOT

    def interrupt(self, task_name):
        return replace(
            TASK_SNAPSHOT,
            state=TaskState.Interrupted,
            queue_position=None,
        )

    def clear(self, task_name):
        self.cleared.append(task_name)


class FakeRuntime:
    def __init__(self):
        self.task_service = FakeTaskService()
        self.plan_tool_service = object()
        self.task_manager = SimpleNamespace(
            running_count=0,
            queued_count=1,
            closing=False,
        )
        self.settings = SimpleNamespace(sse_keepalive_seconds=15)
        self.bound = False
        self.closed = False

    def bind_event_loop(self):
        self.bound = True

    async def aclose(self):
        self.closed = True


class GatewayEndpointTests(unittest.TestCase):
    def setUp(self):
        self.runtime = FakeRuntime()
        self.app = create_app(
            runtime_factory=lambda config: self.runtime
        )

    def test_main_initializes_global_config_and_runtime_lifespan(self):
        with TestClient(self.app) as client:
            response = client.get("/health")
            self.assertTrue(self.runtime.bound)

        self.assertIs(global_config, global_config)
        self.assertTrue(global_config.initialized)
        self.assertTrue(self.runtime.closed)
        self.assertEqual(
            response.json(),
            {
                "ok": True,
                "service": "backend",
                "accepting_tasks": True,
                "running_count": 0,
                "queued_count": 1,
            },
        )

    def test_task_routes_forward_strict_models_to_application_service(self):
        with TestClient(self.app) as client:
            created = client.post(
                "/task",
                json={
                    "task_name": "Inspect repo",
                    "parent_session_id": "main",
                    "task_type": "react",
                    "prompt": "Inspect the repository.",
                    "tool_groups": ["FileSystem"],
                    "skill_groups": ["Research"],
                    "timeout_ms": 120000,
                },
            )
            listed = client.get("/task")
            fetched = client.get("/task/Inspect repo")
            interrupted = client.post("/task/Inspect repo/interrupt")
            cleared = client.delete("/task/Inspect repo")

        self.assertEqual(created.status_code, 202)
        self.assertEqual(created.json(), TASK_VIEW)
        start_spec = self.runtime.task_service.started[0]
        self.assertEqual(start_spec.task_name, "Inspect repo")
        self.assertEqual(start_spec.task_type.value, "ReAct")
        self.assertEqual(start_spec.tool_group_names, ("FileSystem",))
        self.assertEqual(start_spec.skill_group_names, ("Research",))
        self.assertEqual(listed.json()["queued_count"], 1)
        self.assertEqual(fetched.json(), TASK_VIEW)
        self.assertEqual(interrupted.json()["state"], "interrupted")
        self.assertEqual(cleared.status_code, 204)
        self.assertEqual(
            self.runtime.task_service.cleared,
            ["Inspect repo"],
        )

    def test_task_models_forbid_unknown_fields_and_invalid_type(self):
        with TestClient(self.app) as client:
            extra = client.post(
                "/task",
                json={
                    "task_name": "Task",
                    "parent_session_id": "main",
                    "task_type": "react",
                    "prompt": "Work",
                    "unknown": True,
                },
            )
            invalid_type = client.post(
                "/task",
                json={
                    "task_name": "Task",
                    "parent_session_id": "main",
                    "task_type": "goal",
                    "prompt": "Work",
                },
            )

        self.assertEqual(extra.status_code, 422)
        self.assertEqual(invalid_type.status_code, 422)

    def test_application_errors_use_stable_error_envelope(self):
        with TestClient(self.app) as client:
            response = client.get("/task/missing")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            {
                "error": {
                    "code": "task_not_found",
                    "message": "No Task has that name.",
                }
            },
        )

    def test_old_demo_gateway_routes_are_removed(self):
        with TestClient(self.app) as client:
            for path in (
                "/services",
                "/tasks",
                "/chat/main-session",
                "/debug/events",
            ):
                self.assertEqual(client.get(path).status_code, 404)

    def test_sse_encoding_has_id_event_and_compact_json(self):
        encoded = encode_sse_event(
            {
                "stream_id": "stream",
                "revision": 3,
                "event": "task.started",
                "payload": {"task": {"task_name": "任务"}},
            }
        )

        self.assertEqual(
            encoded,
            'id: stream:3\n'
            'event: task.started\n'
            'data: {"stream_id":"stream","revision":3,'
            '"event":"task.started","payload":{"task":{"task_name":"任务"}}}\n\n',
        )


if __name__ == "__main__":
    unittest.main()
