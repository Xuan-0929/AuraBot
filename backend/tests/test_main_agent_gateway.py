"""Verify lazy Backend composition, local readiness, metrics bounds, and shutdown ownership.

The Backend must start without constructing a Profile Runtime, model, Memory database, Qdrant client,
or SpeechService. An explicit Profile activation opens those through the Profile owner; shutdown fences the
Hub before closing the active Profile and finally the shared TaskManager.
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault(
    "AURABOT_CONFIG_PATH", str(Path(__file__).parent / "fixtures" / "runtime_config.json")
)

from backend.main import create_app
from backend.main_agent.metrics import LifecycleMetrics
from backend.main_agent.lifecycle import ProfileResources
from backend.tests.test_main_agent_realtime import FakeRuntime
from backend.main_agent.realtime import RealtimeHub


class Owner:
    def __init__(self, name: str, calls: list[str]) -> None:
        self.name = name
        self.calls = calls

    async def startup(self) -> None:
        self.calls.append(f"start:{self.name}")

    async def shutdown(self) -> None:
        self.calls.append(f"stop:{self.name}")


class Profiles:
    active_profile_id = None
    runtime = None
    memory = None

    def __init__(self, hub: RealtimeHub, calls: list[str]) -> None:
        self.hub = hub
        self.calls = calls

    async def activate(self, profile_id: str, soul: str, **snapshot) -> int:
        self.calls.append(f"activate:{profile_id}:{soul}")
        self.active_profile_id = profile_id
        await self.hub.set_active(profile_id, 1, ProfileResources(profile_id, FakeRuntime(), None, None, revision=snapshot["revision"]))
        return 1

    async def renew_session(self, profile_id: str, revision: int) -> int:
        await self.hub.set_active(profile_id, 2, self.hub.profile)
        return 2

    async def shutdown(self) -> None:
        self.calls.append("stop:profiles")


class GatewayLifecycleTests(unittest.TestCase):
    def test_startup_is_profile_lazy_and_shutdown_fences_realtime_first(self) -> None:
        calls: list[str] = []
        manager = Owner("task", calls)
        hub = RealtimeHub(allowed_origins={"http://127.0.0.1:6400"})
        profiles = Profiles(hub, calls)
        app = create_app(task_manager=manager, realtime_hub=hub, profile_manager=profiles)

        with TestClient(app) as client:
            self.assertEqual(client.get("/health").json(), {"ok": True, "service": "backend"})
            readiness = client.get("/api/v1/readiness").json()
            self.assertTrue(readiness["ready"])
            self.assertIsNone(profiles.active_profile_id)
            activated = client.post("/api/v1/main-agent/profile/activate",
                headers={"origin": "http://127.0.0.1:6400"},
                json={"profile_id": "profile_a", "revision": 1, "soul": "Soul A"})
            self.assertEqual(activated.status_code, 200)
            response = client.post(
                "/api/v1/main-agent/realtime-token",
                headers={"origin": "http://127.0.0.1:6400"},
                json={"profileId": "profile_a", "revision": 1},
            )
            self.assertEqual(response.status_code, 201)
            self.assertEqual(profiles.active_profile_id, "profile_a")

        self.assertEqual(calls[0], "start:task")
        self.assertEqual(calls[-2:], ["stop:profiles", "stop:task"])
        self.assertFalse(hub.accepting)


class MetricsTests(unittest.TestCase):
    def test_counters_and_timings_remain_hard_bounded(self) -> None:
        metrics = LifecycleMetrics(max_series=3, max_samples=4)
        for index in range(20):
            metrics.increment(f"counter.{index % 5}")
            metrics.observe(f"latency.{index % 5}", index / 1000)
        snapshot = metrics.snapshot()
        self.assertLessEqual(len(snapshot["counters"]), 3)
        self.assertLessEqual(len(snapshot["timings"]), 3)
        self.assertTrue(all(item["samples"] <= 4 for item in snapshot["timings"].values()))
