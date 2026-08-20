"""
验证版本化 Task REST/SSE Gateway 只暴露 AuraBot 业务模型。

测试注入具体内存 FakeTaskManager 检查输入映射、camelCase 输出、乐观并发错误、SSE
cursor 与 lifespan；fake 不持有 subprocess，进程和持久化行为由 task 子目录测试覆盖。
"""

from __future__ import annotations

import asyncio
import os
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from fastapi.testclient import TestClient

os.environ.setdefault(
    "AURABOT_CONFIG_PATH",
    str(Path(__file__).parent / "fixtures" / "runtime_config.yaml"),
)

from backend.main import create_app
from backend.task.models import (
    CreateTaskCommand,
    TaskEvent,
    TaskPage,
    TaskRecord,
    TaskStateConflict,
    TaskStatus,
    TaskVersionConflict,
)


def task_record(status: TaskStatus = TaskStatus.QUEUED) -> TaskRecord:
    now = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    return TaskRecord(
        id=UUID("00000000-0000-4000-8000-000000000001"),
        row_id=1,
        title="Fix login",
        prompt="Run tests and fix login.",
        working_directory=Path("/tmp/project"),
        status=status,
        timeout_seconds=1800,
        active_runtime_ms=0,
        pi_session_id="private-session",
        pi_session_path=Path("/private/session.jsonl"),
        result_text=None,
        error_code=None,
        error_message=None,
        pause_reason=None,
        rerun_of_task_id=None,
        version=1,
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
        deleted_at=None,
    )


class FakeTaskManager:
    def __init__(self) -> None:
        self.task = task_record()
        self.created_command: CreateTaskCommand | None = None
        self.started = False
        self.stopped = False
        self.after_event_id: int | None = None
        self.event_filter: UUID | None = None
        self.raise_state_conflict = False
        self.raise_unexpected = False
        self.event_delay = 0.0

    async def startup(self) -> None:
        self.started = True

    async def shutdown(self) -> None:
        self.stopped = True

    async def create_task(self, command: CreateTaskCommand) -> TaskRecord:
        if self.raise_unexpected:
            raise RuntimeError("secret stack detail")
        self.created_command = command
        return self.task

    async def list_tasks(self, **_kwargs) -> TaskPage:
        return TaskPage(items=(self.task,), next_cursor=None)

    async def get_task(self, _task_id: UUID, include_deleted: bool = False) -> TaskRecord:
        del include_deleted
        return self.task

    async def pause_task(self, _task_id: UUID, expected_version: int) -> TaskRecord:
        if expected_version != self.task.version:
            raise TaskVersionConflict(expected_version, self.task.version)
        self.task = replace(self.task, status=TaskStatus.PAUSED, version=2)
        return self.task

    async def resume_task(self, _task_id: UUID, expected_version: int) -> TaskRecord:
        if self.raise_state_conflict:
            raise TaskStateConflict(TaskStatus.RUNNING, TaskStatus.QUEUED)
        if expected_version != self.task.version:
            raise TaskVersionConflict(expected_version, self.task.version)
        return self.task

    async def cancel_task(self, _task_id: UUID, expected_version: int) -> TaskRecord:
        if expected_version != self.task.version:
            raise TaskVersionConflict(expected_version, self.task.version)
        self.task = replace(self.task, status=TaskStatus.CANCELLED, version=2)
        return self.task

    async def delete_task(self, _task_id: UUID) -> None:
        return None

    async def iter_events(self, after_event_id: int, task_id: UUID | None = None):
        self.after_event_id = after_event_id
        self.event_filter = task_id
        if self.event_delay:
            await asyncio.sleep(self.event_delay)
        yield TaskEvent(
            event_id=382,
            task_sequence=7,
            task_id=self.task.id,
            type="task.status_changed",
            occurred_at=self.task.updated_at,
            payload={"from": "running", "to": "completed"},
        )


class TaskGatewayTests(unittest.TestCase):
    def setUp(self):
        self.manager = FakeTaskManager()
        self.client_context = TestClient(create_app(task_manager=self.manager))
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)

    def test_lifespan_and_health_contract(self):
        self.assertTrue(self.manager.started)
        self.assertEqual(
            self.client.get("/health").json(), {"ok": True, "service": "backend"}
        )
        self.client_context.__exit__(None, None, None)
        self.assertTrue(self.manager.stopped)
        self.client_context = TestClient(create_app(task_manager=FakeTaskManager()))
        self.client = self.client_context.__enter__()

    def test_create_maps_camel_case_and_hides_pi_session_fields(self):
        response = self.client.post(
            "/api/v1/tasks",
            json={
                "title": "Fix login",
                "prompt": "Run tests and fix login.",
                "workingDirectory": "/tmp/project",
                "timeoutSeconds": 1800,
                "rerunOfTaskId": None,
            },
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["workingDirectory"], "/tmp/project")
        self.assertEqual(body["activeRuntimeMs"], 0)
        self.assertEqual(body["status"], "queued")
        self.assertNotIn("piSessionId", body)
        self.assertNotIn("piSessionPath", body)
        self.assertEqual(self.manager.created_command.working_directory, Path("/tmp/project"))

    def test_create_rejects_relative_working_directory(self):
        response = self.client.post(
            "/api/v1/tasks",
            json={
                "title": "x",
                "prompt": "y",
                "workingDirectory": "relative/path",
                "timeoutSeconds": 1800,
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_list_get_pause_cancel_and_delete_contracts(self):
        listed = self.client.get("/api/v1/tasks?limit=20")
        fetched = self.client.get(f"/api/v1/tasks/{self.manager.task.id}")
        paused = self.client.post(
            f"/api/v1/tasks/{self.manager.task.id}/pause",
            json={"expectedVersion": 1},
        )
        cancelled = self.client.post(
            f"/api/v1/tasks/{self.manager.task.id}/cancel",
            json={"expectedVersion": 2},
        )
        deleted = self.client.delete(f"/api/v1/tasks/{self.manager.task.id}")

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["items"][0]["id"], str(self.manager.task.id))
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(paused.json()["status"], "paused")
        self.assertEqual(cancelled.json()["status"], "cancelled")
        self.assertEqual(deleted.status_code, 204)

    def test_version_and_state_conflicts_use_stable_error_envelope(self):
        stale = self.client.post(
            f"/api/v1/tasks/{self.manager.task.id}/pause",
            json={"expectedVersion": 99},
        )
        self.manager.raise_state_conflict = True
        state = self.client.post(
            f"/api/v1/tasks/{self.manager.task.id}/resume",
            json={"expectedVersion": 1},
        )

        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["error"]["code"], "task_version_conflict")
        self.assertEqual(state.status_code, 409)
        self.assertEqual(state.json()["error"]["code"], "task_state_conflict")
        self.assertEqual(state.json()["error"]["details"], {})

    def test_unexpected_gateway_failure_is_sanitized(self):
        manager = FakeTaskManager()
        manager.raise_unexpected = True
        with TestClient(
            create_app(task_manager=manager), raise_server_exceptions=False
        ) as client:
            response = client.post(
                "/api/v1/tasks",
                json={
                    "title": "Fix login",
                    "prompt": "Run tests.",
                    "workingDirectory": "/tmp/project",
                    "timeoutSeconds": 1800,
                },
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"]["code"], "internal_error")
        self.assertNotIn("secret stack detail", response.text)

    def test_sse_uses_last_event_id_and_business_envelope(self):
        with self.client.stream(
            "GET",
            f"/api/v1/task-events?taskId={self.manager.task.id}",
            headers={"Last-Event-ID": "381"},
        ) as response:
            body = "".join(response.iter_text())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.manager.after_event_id, 381)
        self.assertEqual(self.manager.event_filter, self.manager.task.id)
        self.assertIn("id: 382", body)
        self.assertIn("event: task.status_changed", body)
        self.assertIn('"taskSequence":7', body)
        self.assertNotIn("private-session", body)

    def test_sse_keepalive_does_not_cancel_pending_database_event(self):
        self.manager.event_delay = 0.03
        with patch(
            "backend.app.gateway.task_routes.SSE_KEEPALIVE_SECONDS",
            0.01,
        ):
            with self.client.stream("GET", "/api/v1/task-events") as response:
                body = "".join(response.iter_text())

        self.assertIn(": keepalive", body)
        self.assertIn("id: 382", body)

    def test_openapi_has_only_versioned_task_surface(self):
        paths = self.client.get("/openapi.json").json()["paths"]
        self.assertIn("/api/v1/tasks", paths)
        self.assertIn("/api/v1/tasks/{task_id}", paths)
        self.assertIn("/api/v1/task-events", paths)
        self.assertNotIn("/task", paths)
        self.assertNotIn("/task/events", paths)
        self.assertNotIn("/tool/plan", paths)
        self.assertNotIn("/api/v1/tasks/{task_id}/answer", paths)


if __name__ == "__main__":
    unittest.main()
