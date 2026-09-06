"""Verify the desktop Task gateway exposes only Profile-scoped status snapshots.

A finite snapshot source exercises the actual FastAPI route, required Profile selection, SSE payload,
keepalive and removed REST surface. Manager/store tests cover durable state and notification behavior.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from backend.app.gateway.task_routes import router, task_events


class SnapshotManager:
    def __init__(self):
        self.profile = None
        self.delay = 0
        self.closed = False
        self.waiting = asyncio.Event()

    async def iter_status_snapshots(self, owner_profile_id):
        self.profile = owner_profile_id
        try:
            yield [{"id": "task-1", "title": "Fix login", "status": "queued"}]
            self.waiting.set()
            await asyncio.sleep(self.delay)
            yield [{"id": "task-1", "title": "Fix login", "status": "completed"}]
        finally:
            self.closed = True


def application(manager):
    app = FastAPI()
    app.state.task_manager = manager
    app.include_router(router)
    return app


class TaskGatewayTests(unittest.TestCase):
    def test_snapshot_contract_requires_profile_and_exposes_only_title_status(self):
        manager = SnapshotManager()
        with TestClient(application(manager)) as client:
            self.assertEqual(client.get("/api/v1/task-events").status_code, 422)
            self.assertEqual(client.get("/api/v1/task-events?ownerProfileId=").status_code, 422)
            response = client.get(
                "/api/v1/task-events?ownerProfileId=profile-a",
                headers={"Last-Event-ID": "irrelevant-old-cursor"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(manager.profile, "profile-a")
        blocks = response.text.strip().split("\n\n")
        self.assertEqual(len(blocks), 2)
        for block in blocks:
            event, data = block.split("\n")
            self.assertEqual(event, "event: task.snapshot")
            payload = json.loads(data.removeprefix("data: "))
            self.assertEqual(set(payload), {"tasks"})
            self.assertEqual(set(payload["tasks"][0]), {"id", "title", "status"})
        self.assertNotIn("id:", response.text)
        self.assertTrue(manager.closed)

    def test_keepalive_does_not_cancel_the_pending_snapshot(self):
        manager = SnapshotManager()
        manager.delay = 0.03
        with patch("backend.app.gateway.task_routes.SSE_KEEPALIVE_SECONDS", 0.01):
            with TestClient(application(manager)) as client:
                response = client.get("/api/v1/task-events?ownerProfileId=profile-a")
        self.assertIn(": keepalive", response.text)
        self.assertIn('"status":"completed"', response.text)
        self.assertTrue(manager.closed)

    def test_only_snapshot_route_remains_and_removed_control_routes_return_404(self):
        with TestClient(application(SnapshotManager())) as client:
            document = client.get("/openapi.json").json()
            self.assertEqual(set(document["paths"]), {"/api/v1/task-events"})
            for method, path in (
                ("GET", "/api/v1/tasks"), ("POST", "/api/v1/tasks"),
                ("GET", "/api/v1/tasks/task-1"), ("POST", "/api/v1/tasks/task-1/pause"),
                ("POST", "/api/v1/tasks/task-1/resume"), ("POST", "/api/v1/tasks/task-1/cancel"),
                ("DELETE", "/api/v1/tasks/task-1"),
            ):
                with self.subTest(method=method, path=path):
                    self.assertEqual(client.request(method, path).status_code, 404)
            parameters = document["paths"]["/api/v1/task-events"]["get"]["parameters"]
            self.assertEqual([item["name"] for item in parameters], ["ownerProfileId"])
            self.assertTrue(parameters[0]["required"])


class TaskGatewayDisconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_closes_the_pending_snapshot_iterator(self):
        manager = SnapshotManager()
        manager.delay = 30
        app = application(manager)
        request = Request({"type": "http", "app": app})
        response = await task_events(request, "profile-a")
        stream = response.body_iterator
        self.assertIn("task.snapshot", await anext(stream))
        pending = asyncio.create_task(anext(stream))
        await manager.waiting.wait()
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        await stream.aclose()
        self.assertTrue(manager.closed)
