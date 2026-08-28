"""Verify the profile-scoped Main Agent Memory boundary through durable public behavior.

These tests intentionally avoid worker leases, cache internals, task names, and vector outboxes. They
exercise stable segment persistence, idle sealing, structured extraction retry, recall, immediate
forget, restart recovery, and explicit-profile HTTP routing.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.gateway.memory_routes import router as memory_router
from backend.main_agent.memory import ProfileMemory
from backend.memory.types import MemoryCandidate


class FakeExtractor:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict] = []

    def extract(self, *, content, metadata, memory_type=None, importance=0.5):
        self.calls.append({"content": content, "metadata": metadata})
        if self.fail:
            raise RuntimeError("model unavailable")
        return ([MemoryCandidate(
            content=f"记忆:{content}", memory_type="semantic", metadata=dict(metadata),
        )], [])


class FakeEmbedding:
    dimension = 2

    def embed(self, text: str) -> list[float]:
        return [float(len(text)), 1.0]


class FakeVectors:
    def __init__(self) -> None:
        self.items: dict[str, dict] = {}
        self.fail_delete = False
        self.search_delay = 0.0

    def upsert(self, memory_id, vector, payload):
        self.items[memory_id] = dict(payload)

    def search(self, vector, *, profile_id, limit, filters=None):
        if self.search_delay:
            import time
            time.sleep(self.search_delay)
        return [
            {"memory_id": memory_id, **payload}
            for memory_id, payload in list(self.items.items())[:limit]
        ]

    def delete(self, memory_ids):
        if self.fail_delete:
            raise RuntimeError("qdrant offline")
        for memory_id in memory_ids:
            self.items.pop(memory_id, None)


class ProfileMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "memory.sqlite3"
        self.extractor = FakeExtractor()
        self.vectors = FakeVectors()
        self.memory = ProfileMemory(
            profile_id="profile_a", path=self.path, extractor=self.extractor,
            embedding_provider=FakeEmbedding(), vector_store=self.vectors,
            idle_seconds=0.03, retry_delays=(0.01, 0.02, 0.03),
        )
        await self.memory.startup()

    async def asyncTearDown(self) -> None:
        await self.memory.shutdown()
        self.temp.cleanup()

    async def test_same_segment_updates_in_place_and_idle_seal_extracts_once(self) -> None:
        await self.memory.record_confirmed_input(
            segment_id="seg-1", text="我喜欢", input_id="input-1", revision=1,
        )
        await self.memory.record_confirmed_input(
            segment_id="seg-1", text="我喜欢红茶", input_id="input-1", revision=2,
        )
        await self.memory.wait_until_idle()

        self.assertEqual(len(self.extractor.calls), 1)
        self.assertEqual(self.extractor.calls[0]["content"], "我喜欢红茶")
        self.assertEqual(self.extractor.calls[0]["metadata"]["source_segment_ids"], ["seg-1"])
        result = await self.memory.recall(query="红茶", revision=2)
        self.assertEqual([item["content"] for item in result], ["记忆:我喜欢红茶"])
        semantic = await self.memory.recall(query="完全不同的词", revision=2)
        self.assertEqual([item["content"] for item in semantic], ["记忆:我喜欢红茶"])

    async def test_shutdown_keeps_pending_episode_and_restart_seals_when_expired(self) -> None:
        await self.memory.record_confirmed_input(
            segment_id="seg-1", text="下周去上海", input_id="input-1", revision=1,
        )
        await self.memory.shutdown()
        await asyncio.sleep(0.04)

        replacement = ProfileMemory(
            profile_id="profile_a", path=self.path, extractor=self.extractor,
            idle_seconds=0.03, retry_delays=(0.01, 0.02, 0.03),
        )
        self.memory = replacement
        await replacement.startup()
        await replacement.wait_until_idle()
        self.assertEqual(len(self.extractor.calls), 1)

    async def test_failed_extraction_retries_then_can_be_manually_retried(self) -> None:
        self.extractor.fail = True
        await self.memory.record_confirmed_input(
            segment_id="seg-1", text="需要保存", input_id="input-1", revision=1,
        )
        await self.memory.wait_until_idle()
        listing = await self.memory.browse()
        self.assertEqual(listing["jobs"][0]["state"], "failed")

        self.extractor.fail = False
        await self.memory.retry(listing["jobs"][0]["jobId"])
        await self.memory.wait_until_idle()
        self.assertEqual((await self.memory.browse())["jobs"][0]["state"], "completed")

    async def test_vector_timeout_degrades_to_lexical_and_forget_is_immediate(self) -> None:
        await self.memory.record_confirmed_input(
            segment_id="seg-1", text="喜欢红茶", input_id="input-1", revision=1,
        )
        await self.memory.wait_until_idle()
        self.vectors.search_delay = 0.1
        self.memory.vector_wait_seconds = 0.01

        recalled = await self.memory.recall(query="红茶", revision=1)
        memory_id = recalled[0]["memory_id"]
        self.vectors.fail_delete = True
        forgotten = await self.memory.forget(memory_ids=(memory_id,))

        self.assertEqual(forgotten["forgottenMemoryIds"], [memory_id])
        self.assertEqual(await self.memory.recall(query="红茶", revision=1), ())
        self.assertEqual((await self.memory.browse())["jobs"][0]["kind"], "vector_delete")

    async def test_visible_segment_is_filtered_from_recall(self) -> None:
        await self.memory.record_confirmed_input(
            segment_id="seg-1", text="我喜欢红茶", input_id="input-1", revision=1,
        )
        await self.memory.wait_until_idle()
        result = await self.memory.recall(
            query="红茶", revision=1, visible_source_segment_ids=("seg-1",),
        )
        self.assertEqual(result, ())


class ActiveProfileMemoryRoutesTests(unittest.TestCase):
    def test_routes_require_the_matching_active_profile(self) -> None:
        class Profiles:
            active_profile_id = "profile_a"

            async def browse_memory(self, profile_id, limit):
                return {"profileId": profile_id, "items": [], "jobs": []}

            async def forget_memory(self, profile_id, **kwargs):
                return {"forgottenMemoryIds": list(kwargs["memory_ids"])}

            async def retry_memory(self, profile_id, job_id):
                return {"jobId": job_id, "state": "pending"}

        app = FastAPI()
        app.state.main_agent_profiles = Profiles()
        app.include_router(memory_router)
        with TestClient(app) as client:
            active = client.get("/api/v1/profiles/profile_a/memories")
            inactive = client.get("/api/v1/profiles/profile_b/memories")
            forgotten = client.post(
                "/api/v1/profiles/profile_a/memories/forget",
                json={"memoryIds": ["mem-1"]},
            )

        self.assertEqual(active.status_code, 200)
        self.assertEqual(active.json()["profileId"], "profile_a")
        self.assertEqual(inactive.status_code, 409)
        self.assertEqual(inactive.json()["error"]["code"], "profile_not_active")
        self.assertEqual(forgotten.json()["forgottenMemoryIds"], ["mem-1"])


if __name__ == "__main__":
    unittest.main()
