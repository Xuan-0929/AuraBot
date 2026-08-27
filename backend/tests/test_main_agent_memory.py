"""Exercise automatic Main Agent recall and its realtime-safe revision fencing.

The tests use a real temporary ``memory.sqlite3`` for lexical/active truth and inject only the
optional vector boundary. They verify lexical-first degradation, shared-task cancellation
isolation, bounded task ownership, deterministic dedupe/source filtering, hard output bounds, and
Runtime lock release plus StrongPause fencing while recall is pending. Durable coverage exercises
idle sealing, retries, restart recovery, forget supersession, lease heartbeats/loss, transactional
migration rollback, the Qdrant close/open registry fence, and public gateway contracts.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.gateway.memory_routes import router as memory_router
from backend.main_agent import ContextStore, SnapshotProjector
from backend.main_agent.memory import MemoryBridge, MemoryWorker
from backend.main_agent.metrics import LifecycleMetrics
from backend.main_agent.runtime import MainAgentRuntime
from backend.main_agent.tool_runtime import InvokeMode, ToolSurfacePolicy
from backend.memory.stores.sqlite import SQLiteMemoryStore
from backend.memory.stores.qdrant import QdrantLocalVectorStore
from backend.memory.types import MemoryCandidate


class StaticEmbedding:
    dimension = 2

    def embed(self, _text: str) -> list[float]:
        return [1.0, 0.0]


class ControlledVectorStore:
    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.calls = 0
        self.results: list[dict[str, object]] = []
        self.failure: Exception | None = None

    async def search(self, _vector, *, profile_id, limit, filters=None):
        del profile_id, limit, filters
        self.calls += 1
        self.started.set()
        await self.release.wait()
        if self.failure is not None:
            raise self.failure
        return list(self.results)

    def upsert(self, memory_id, vector, payload):
        del memory_id, vector, payload

    def delete(self, memory_ids):
        del memory_ids

    def health(self):
        return {"backend": "controlled"}


class MemoryBridgeRecallTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.store = SQLiteMemoryStore(Path(self.directory.name) / "memory.sqlite3")
        self.vector = ControlledVectorStore()
        self.bridge = MemoryBridge(
            profile_id="aura",
            store=self.store,
            vector_store=self.vector,
            embedding_provider=StaticEmbedding(),
            vector_wait_seconds=0.02,
            cache_size=2,
        )

    async def asyncTearDown(self) -> None:
        await self.bridge.shutdown()
        self.store.close()
        self.directory.cleanup()

    def remember(self, content: str, *, source_segment_ids=()) -> str:
        episode_id = self.store.create_episode(
            profile_id="aura",
            source_type="main_agent_episode",
            source_id=source_segment_ids[0] if source_segment_ids else None,
            role="user",
            content=content,
            metadata={"source_segment_ids": list(source_segment_ids)},
        )
        return self.store.create_memory(
            profile_id="aura",
            episode_id=episode_id,
            candidate=MemoryCandidate(
                content=content,
                metadata={"source_segment_ids": list(source_segment_ids)},
            ),
        )

    async def test_vector_timeout_degrades_to_lexical_and_does_not_cancel_shared_task(self) -> None:
        metrics = LifecycleMetrics()
        self.bridge.metrics = metrics
        lexical_id = self.remember("用户喜欢热拿铁")

        first, second = await asyncio.gather(
            self.bridge.recall(query="热拿铁", revision=1),
            self.bridge.recall(query="热拿铁", revision=1),
        )

        self.assertEqual([item["id"] for item in first], [lexical_id])
        self.assertEqual(second, first)
        self.assertEqual(self.vector.calls, 1)
        self.assertFalse(next(iter(self.bridge.vector_tasks)).cancelled())

        self.vector.results = [{"memory_id": lexical_id, "score": 0.9}]
        self.vector.release.set()
        await asyncio.wait_for(next(iter(self.bridge.vector_tasks)), 1)
        cached = await self.bridge.recall(query="热拿铁", revision=1)
        self.assertEqual(cached, first)
        telemetry = metrics.snapshot()
        self.assertEqual(telemetry["counters"]["memory.recall.lexical"], 3)
        self.assertEqual(telemetry["counters"]["memory.recall.vector_timeout"], 2)
        self.assertEqual(telemetry["counters"]["memory.recall.degraded"], 2)
        self.assertIn("memory.recall.latency", telemetry["timings"])

    async def test_vector_starts_concurrently_while_lexical_fts_is_blocked(self) -> None:
        self.remember("用户喜欢热拿铁")
        lexical_release = threading.Event()
        original = self.store.search_fts

        def blocked_fts(**kwargs):
            lexical_release.wait(1)
            return original(**kwargs)

        self.store.search_fts = blocked_fts  # type: ignore[method-assign]
        recall = asyncio.create_task(self.bridge.recall(query="热拿铁", revision=1))
        try:
            await asyncio.wait_for(self.vector.started.wait(), 0.05)
            self.assertFalse(recall.done())
            self.vector.release.set()
            lexical_release.set()
            self.assertEqual(len(await recall), 1)
        finally:
            lexical_release.set()
            await asyncio.gather(recall, return_exceptions=True)

    async def test_never_returning_vector_work_has_a_hard_owned_task_limit(self) -> None:
        recalls = [
            asyncio.create_task(self.bridge.recall(query=f"unknown-{index}", revision=index))
            for index in range(20)
        ]

        await asyncio.gather(*recalls)

        self.assertLessEqual(self.vector.calls, 2)
        self.assertLessEqual(len(self.bridge.vector_tasks), 2)
        self.assertTrue(all(not task.done() for task in self.bridge.vector_tasks))
        await self.bridge.shutdown()
        self.assertEqual(self.bridge.vector_tasks, ())

    async def test_lexical_failure_releases_waiter_so_lru_can_cancel_orphan_vector_work(self) -> None:
        original = self.store.search_fts
        self.bridge.vector_task_limit = 3

        def fail_fts(**_kwargs):
            raise RuntimeError("sqlite unavailable")

        self.store.search_fts = fail_fts  # type: ignore[method-assign]
        with self.assertRaisesRegex(RuntimeError, "sqlite unavailable"):
            await self.bridge.recall(query="first", revision=1)
        orphan = next(iter(self.bridge.vector_tasks))
        self.store.search_fts = original  # type: ignore[method-assign]

        await self.bridge.recall(query="second", revision=2)
        await self.bridge.recall(query="third", revision=3)
        await asyncio.sleep(0)

        self.assertTrue(orphan.cancelled())

    async def test_failure_dedupe_source_overlap_bounds_and_revision_cache(self) -> None:
        visible_id = self.remember("用户喜欢热拿铁", source_segment_ids=("segment-visible",))
        first_id = self.remember("用户喜欢低酸咖啡豆")
        duplicate_content_id = self.remember("用户喜欢低酸咖啡豆")
        long_ids = [self.remember(f"向量记忆 {index} " + "长" * 600) for index in range(8)]
        self.vector.results = [
            {"memory_id": visible_id, "score": 1.0},
            {"memory_id": first_id, "score": 0.9},
            {"memory_id": duplicate_content_id, "score": 0.8},
            *({"memory_id": memory_id, "score": 0.7} for memory_id in long_ids),
        ]
        self.vector.release.set()

        recalled = await self.bridge.recall(
            query="咖啡",
            revision=7,
            visible_source_segment_ids=("segment-visible",),
            visible_source_texts=("用户这一轮已经说过喜欢热拿铁",),
        )

        contents = [item["content"] for item in recalled]
        self.assertNotIn(visible_id, [item["id"] for item in recalled])
        self.assertEqual(contents.count("用户喜欢低酸咖啡豆"), 1)
        self.assertLessEqual(len(recalled), 6)
        self.assertLessEqual(sum(len(content) for content in contents), 2_400)
        self.assertEqual(self.vector.calls, 1)

        await self.bridge.recall(query="咖啡", revision=8)
        self.assertEqual(self.vector.calls, 2)
        await self.bridge.recall(query="咖啡", revision=9)
        self.assertLessEqual(self.bridge.cache_entries, 2)

    async def test_vector_failure_returns_lexical_only(self) -> None:
        memory_id = self.remember("用户喜欢热拿铁")
        self.vector.failure = RuntimeError("qdrant unavailable")
        self.vector.release.set()

        recalled = await self.bridge.recall(query="热拿铁", revision=1)

        self.assertEqual([item["id"] for item in recalled], [memory_id])


class RuntimeRecallFenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_explicit_or_complete_user_input_is_recorded_once(self) -> None:
        directory = tempfile.TemporaryDirectory()
        context = ContextStore(Path(directory.name) / "main-agent.sqlite3")

        class Recorder:
            def __init__(self) -> None:
                self.segments = []

            async def recall(self, **_kwargs):
                return ()

            async def record_confirmed_input(self, **segment):
                self.segments.append(segment)

        recorder = Recorder()
        metrics = LifecycleMetrics()
        runtime = MainAgentRuntime(
            context_store=context,
            snapshot_projector=SnapshotProjector(),
            tool_policy=ToolSurfacePolicy(),
            provider=lambda _request: [],
            renderer=lambda _clause: None,
            executors={},
            soul="test",
            memory_bridge=recorder,
            metrics=metrics,
        )
        try:
            await runtime.startup()
            await runtime.update_transcript("临时", request_prepare=False)
            await runtime.update_transcript("最终语音", request_prepare=False)
            await runtime.request_continue("non_user_event")
            self.assertEqual(recorder.segments, [])

            await runtime.eou_complete()
            await runtime.wait_until_idle()
            self.assertEqual(len(recorder.segments), 1)
            self.assertEqual(recorder.segments[0]["content"], "最终语音")

            await runtime.submit_text("桌面文字")
            await runtime.wait_until_idle()
            self.assertEqual(len(recorder.segments), 2)
            self.assertEqual(recorder.segments[1]["content"], "桌面文字")
            telemetry = metrics.snapshot()
            self.assertEqual(telemetry["counters"]["main.runtime.startup"], 1)
            self.assertIn("main.invoke.latency", telemetry["timings"])
        finally:
            await runtime.shutdown()
            context.close()
            directory.cleanup()

    async def test_pending_recall_never_holds_runtime_lock_and_stale_result_is_discarded(self) -> None:
        directory = tempfile.TemporaryDirectory()
        context = ContextStore(Path(directory.name) / "main-agent.sqlite3")
        started = asyncio.Event()
        release = asyncio.Event()
        requests = []

        class Recall:
            async def recall(self, *, query, revision, **_kwargs):
                started.set()
                await release.wait()
                return [{"id": f"memory-{revision}", "content": query}]

            async def record_confirmed_input(self, **_kwargs):
                return None

            async def shutdown(self):
                return None

        def provider(request):
            requests.append(request)
            return []

        runtime = MainAgentRuntime(
            context_store=context,
            snapshot_projector=SnapshotProjector(),
            tool_policy=ToolSurfacePolicy(),
            provider=provider,
            renderer=lambda _clause: None,
            executors={},
            soul="test",
            memory_bridge=Recall(),
        )
        try:
            await runtime.submit_text("first")
            await asyncio.wait_for(started.wait(), 1)
            await asyncio.wait_for(runtime.vad_start(), 0.05)
            await runtime.reject_speech_candidate()
            await runtime.submit_text("second")
            release.set()
            await runtime.wait_until_idle()

            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0].snapshot.transcript, "second")
            self.assertEqual(requests[0].snapshot.memory_facts[0]["id"], "memory-2")
        finally:
            await runtime.shutdown()
            context.close()
            directory.cleanup()

    async def test_strong_pause_supersedes_blocked_prepare_recall_before_respond(self) -> None:
        directory = tempfile.TemporaryDirectory()
        context = ContextStore(Path(directory.name) / "main-agent.sqlite3")
        started = asyncio.Event()
        release = asyncio.Event()
        requests = []

        class Recall:
            async def recall(self, **_kwargs):
                started.set()
                await release.wait()
                return ()

            async def record_confirmed_input(self, **_kwargs):
                return None

        def provider(request):
            requests.append(request)
            return []

        runtime = MainAgentRuntime(
            context_store=context,
            snapshot_projector=SnapshotProjector(),
            tool_policy=ToolSurfacePolicy(),
            provider=provider,
            renderer=lambda _clause: None,
            executors={},
            soul="test",
            memory_bridge=Recall(),
        )
        try:
            await runtime.update_transcript("这是一个等待中的准备输入", request_prepare=False)
            await runtime.request_prepare("blocked", high_priority=True)
            await asyncio.wait_for(started.wait(), 1)

            endpoint = asyncio.create_task(runtime.eou_complete())
            await asyncio.sleep(0)
            self.assertFalse(endpoint.done())
            release.set()
            await endpoint
            await runtime.wait_until_idle()

            self.assertEqual([request.mode for request in requests], [InvokeMode.RESPOND])
        finally:
            release.set()
            await runtime.shutdown()
            context.close()
            directory.cleanup()


class ManualClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class StubbornSleeper:
    """A timer fake that can wake even after cancellation to exercise the generation fence."""

    def __init__(self) -> None:
        self.calls: list[tuple[float, asyncio.Event]] = []

    async def __call__(self, delay: float) -> None:
        event = asyncio.Event()
        self.calls.append((delay, event))
        try:
            await event.wait()
        except asyncio.CancelledError:
            await event.wait()

    def release_all(self) -> None:
        for _delay, event in self.calls:
            event.set()


class FakeExtractor:
    def __init__(self, memories: list[MemoryCandidate], *, failures: int = 0) -> None:
        self.memories = memories
        self.failures = failures
        self.calls = 0

    def extract(self, *, content, metadata, memory_type=None, importance=0.5):
        del content, metadata, memory_type, importance
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("extract failed")
        return list(self.memories), []


class OutboxVectorStore:
    def __init__(self, *, failures: int = 0) -> None:
        self.failures = failures
        self.upsert_calls = 0
        self.points: dict[str, dict] = {}
        self.deleted: list[str] = []

    def upsert(self, memory_id, vector, payload):
        self.upsert_calls += 1
        if self.upsert_calls <= self.failures:
            raise RuntimeError("qdrant failed")
        self.points[memory_id] = {"vector": vector, "payload": payload}

    def search(self, vector, *, profile_id, limit, filters=None):
        del vector, profile_id, limit, filters
        return []

    def delete(self, memory_ids):
        self.deleted.extend(memory_ids)
        for memory_id in memory_ids:
            self.points.pop(memory_id, None)

    def health(self):
        return {"backend": "outbox-fake"}


class HeartbeatSleeper:
    def __init__(self) -> None:
        self.waiters: list[tuple[float, asyncio.Event]] = []

    async def __call__(self, delay: float) -> None:
        event = asyncio.Event()
        self.waiters.append((delay, event))
        await event.wait()

    async def pulse(self, clock: ManualClock) -> None:
        while not self.waiters:
            await asyncio.sleep(0)
        delay, event = self.waiters.pop(0)
        clock.advance(delay)
        event.set()
        for _ in range(10):
            await asyncio.sleep(0)


class SlowAsyncExtractor:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def extract(self, **_kwargs):
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return [MemoryCandidate(content="用户喜欢热拿铁")], []


class SlowFailingAsyncExtractor(SlowAsyncExtractor):
    async def extract(self, **_kwargs):
        self.calls += 1
        self.started.set()
        await self.release.wait()
        raise RuntimeError("extract failed after lease handoff")


class SlowAsyncVectorStore(OutboxVectorStore):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def upsert(self, memory_id, vector, payload):
        self.upsert_calls += 1
        self.started.set()
        await self.release.wait()
        self.points[memory_id] = {"vector": vector, "payload": payload}


class SlowFailingAsyncVectorStore(SlowAsyncVectorStore):
    async def upsert(self, memory_id, vector, payload):
        del memory_id, vector, payload
        self.upsert_calls += 1
        self.started.set()
        await self.release.wait()
        raise RuntimeError("upsert failed after lease handoff")


class EpisodeSealTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirmed_inputs_reset_five_minute_generation_and_seal_one_immutable_job(self) -> None:
        directory = tempfile.TemporaryDirectory()
        store = SQLiteMemoryStore(Path(directory.name) / "memory.sqlite3")
        sleeper = StubbornSleeper()
        bridge = MemoryBridge(
            profile_id="aura",
            store=store,
            idle_seconds=300,
            idle_sleep=sleeper,
        )
        try:
            await bridge.record_confirmed_input(
                segment_id="input-1:1", content="第一句", input_id="input-1", revision=1
            )
            await asyncio.sleep(0)
            await bridge.record_confirmed_input(
                segment_id="input-1:2", content="最终一句", input_id="input-1", revision=2
            )
            await asyncio.sleep(0)
            self.assertEqual([delay for delay, _event in sleeper.calls], [300, 300])

            sleeper.release_all()
            await bridge.wait_for_seals()
            jobs = store.browse_extraction_jobs(profile_id="aura")

            self.assertEqual(len(jobs), 1)
            self.assertEqual(
                [item["segment_id"] for item in jobs[0]["payload"]["segments"]],
                ["input-1:1", "input-1:2"],
            )
            self.assertEqual(jobs[0]["state"], "pending")
            self.assertEqual(len(store.debug_snapshot("aura")["episodes"]), 1)
        finally:
            await bridge.shutdown()
            store.close()
            directory.cleanup()

    async def test_pending_episode_survives_shutdown_and_resumes_only_remaining_idle_time(self) -> None:
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name) / "memory.sqlite3"
        clock = ManualClock()
        first_store = SQLiteMemoryStore(path)
        first = MemoryBridge(
            profile_id="aura", store=first_store, idle_seconds=300, clock=clock
        )
        await first.record_confirmed_input(
            segment_id="input-1:1", content="跨重启保留", input_id="input-1", revision=1
        )
        await first.shutdown()
        first_store.close()

        clock.advance(100)
        second_store = SQLiteMemoryStore(path)
        sleeper = StubbornSleeper()
        second = MemoryBridge(
            profile_id="aura", store=second_store, idle_seconds=300,
            idle_sleep=sleeper, clock=clock,
        )
        try:
            await second.startup()
            await asyncio.sleep(0)
            self.assertEqual([delay for delay, _event in sleeper.calls], [200])
            sleeper.release_all()
            await second.wait_for_seals()
            jobs = second_store.browse_extraction_jobs(profile_id="aura")
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["payload"]["segments"][0]["content"], "跨重启保留")
        finally:
            sleeper.release_all()
            await second.shutdown()
            second_store.close()
            directory.cleanup()

    async def test_overdue_pending_episode_seals_immediately_once_across_repeated_startup(self) -> None:
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name) / "memory.sqlite3"
        clock = ManualClock()
        first_store = SQLiteMemoryStore(path)
        first = MemoryBridge(
            profile_id="aura", store=first_store, idle_seconds=300, clock=clock
        )
        await first.record_confirmed_input(
            segment_id="input-1:1", content="已经超时", input_id="input-1", revision=1
        )
        await first.shutdown()
        first_store.close()

        clock.advance(301)
        second_store = SQLiteMemoryStore(path)
        second = MemoryBridge(
            profile_id="aura", store=second_store, idle_seconds=300,
            idle_sleep=asyncio.sleep, clock=clock,
        )
        try:
            await asyncio.gather(second.startup(), second.startup())
            await second.wait_for_seals()
            self.assertEqual(
                len(second_store.browse_extraction_jobs(profile_id="aura")), 1
            )
            self.assertEqual(len(second_store.debug_snapshot("aura")["episodes"]), 1)
        finally:
            await second.shutdown()
            second_store.close()
            directory.cleanup()


class DurableMemoryWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "memory.sqlite3"
        self.store = SQLiteMemoryStore(self.path)
        self.clock = ManualClock()

    async def asyncTearDown(self) -> None:
        self.store.close()
        self.directory.cleanup()

    def seal(self, content: str = "用户喜欢热拿铁") -> str:
        result = self.store.seal_episode_job(
            profile_id="aura",
            segments=({"segment_id": "input-1:1", "content": content},),
            now=self.clock(),
        )
        return result["job_id"]

    async def test_partial_qdrant_failure_retries_only_outbox_after_five_seconds(self) -> None:
        job_id = self.seal()
        extractor = FakeExtractor([MemoryCandidate(content="用户喜欢热拿铁")])
        vector = OutboxVectorStore(failures=1)
        metrics = LifecycleMetrics()
        worker = MemoryWorker(clock=self.clock, metrics=metrics)
        worker.register(
            profile_id="aura",
            store=self.store,
            extractor=extractor,
            vector_store=vector,
            embedding_provider=StaticEmbedding(),
        )

        await worker.drain_ready()
        job = self.store.get_extraction_job(job_id)
        outbox = self.store.browse_outbox(profile_id="aura")
        self.assertEqual(job["state"], "completed")
        self.assertEqual(extractor.calls, 1)
        self.assertEqual(outbox[0]["state"], "pending")
        self.assertEqual(outbox[0]["next_attempt_at"], 5.0)

        self.clock.advance(5)
        await worker.drain_ready()
        self.assertEqual(extractor.calls, 1)
        self.assertEqual(self.store.browse_outbox(profile_id="aura")[0]["state"], "completed")
        self.assertEqual(len(vector.points), 1)
        telemetry = metrics.snapshot()["counters"]
        self.assertEqual(telemetry["memory.job.completed"], 1)
        self.assertEqual(telemetry["memory.outbox.retry"], 1)
        self.assertEqual(telemetry["memory.outbox.completed"], 1)
        await worker.shutdown()

    async def test_extraction_retry_schedule_is_exact_and_manual_retry_recovers_failed_job(self) -> None:
        job_id = self.seal()
        extractor = FakeExtractor([], failures=99)
        worker = MemoryWorker(clock=self.clock)
        worker.register(
            profile_id="aura", store=self.store, extractor=extractor,
            vector_store=OutboxVectorStore(), embedding_provider=StaticEmbedding(),
        )

        for expected_delay in (5.0, 30.0, 300.0):
            await worker.drain_ready()
            job = self.store.get_extraction_job(job_id)
            self.assertEqual(job["next_attempt_at"], self.clock() + expected_delay)
            self.clock.advance(expected_delay)
        await worker.drain_ready()
        self.assertEqual(self.store.get_extraction_job(job_id)["state"], "failed")

        retried = self.store.retry_failed(profile_id="aura", job_id=job_id, now=self.clock())
        self.assertEqual(retried["state"], "pending")
        self.assertEqual(retried["attempt_count"], 0)
        await worker.shutdown()

    async def test_zero_memory_is_completed_and_restart_recovers_an_expired_lease(self) -> None:
        zero_job = self.seal("闲聊")
        extractor = FakeExtractor([])
        worker = MemoryWorker(clock=self.clock, lease_seconds=1)
        worker.register(
            profile_id="aura", store=self.store, extractor=extractor,
            vector_store=OutboxVectorStore(), embedding_provider=StaticEmbedding(),
        )
        await worker.drain_ready()
        self.assertEqual(self.store.get_extraction_job(zero_job)["state"], "completed")
        self.assertEqual(self.store.browse_outbox(profile_id="aura"), [])

        restart_job = self.seal("需要恢复")
        claimed = self.store.claim_extraction_job(
            owner="dead-worker", lease_seconds=1, now=self.clock()
        )
        self.assertEqual(claimed["job_id"], restart_job)
        self.clock.advance(2)
        await worker.drain_ready()
        self.assertEqual(self.store.get_extraction_job(restart_job)["state"], "completed")
        await worker.shutdown()

    async def test_start_is_idempotent_and_owns_one_event_driven_task(self) -> None:
        worker = MemoryWorker(clock=self.clock)
        worker.register(
            profile_id="aura", store=self.store, extractor=FakeExtractor([]),
            vector_store=OutboxVectorStore(), embedding_provider=StaticEmbedding(),
        )
        await worker.startup()
        task = worker.live_task
        await worker.startup()
        self.assertIs(worker.live_task, task)
        self.assertIsNotNone(task)
        await worker.shutdown()
        self.assertIsNone(worker.live_task)

    async def test_forget_cancels_failed_upsert_so_retry_cannot_resurrect_vector(self) -> None:
        self.seal()
        vector = OutboxVectorStore(failures=1)
        worker = MemoryWorker(clock=self.clock)
        worker.register(
            profile_id="aura", store=self.store,
            extractor=FakeExtractor([MemoryCandidate(content="用户喜欢热拿铁")]),
            vector_store=vector, embedding_provider=StaticEmbedding(),
        )
        bridge = MemoryBridge(profile_id="aura", store=self.store, worker=worker)
        try:
            await worker.drain_ready()
            memory_id = self.store.list_active_memories("aura")[0]["memory_id"]
            self.assertEqual(vector.upsert_calls, 1)

            await bridge.forget(memory_ids=(memory_id,), reason="user request")
            await worker.drain_ready()
            self.clock.advance(5)
            await worker.drain_ready()

            self.assertEqual(vector.upsert_calls, 1)
            self.assertNotIn(memory_id, vector.points)
            self.assertIn(memory_id, vector.deleted)
        finally:
            await bridge.shutdown()
            await worker.shutdown()

    async def test_lease_heartbeat_covers_slow_extraction_and_vector_upsert_once(self) -> None:
        job_id = self.seal()
        sleeper = HeartbeatSleeper()
        extractor = SlowAsyncExtractor()
        vector = SlowAsyncVectorStore()
        worker = MemoryWorker(
            clock=self.clock,
            lease_seconds=30,
            heartbeat_seconds=10,
            heartbeat_sleep=sleeper,
        )
        worker.register(
            profile_id="aura", store=self.store, extractor=extractor,
            vector_store=vector, embedding_provider=StaticEmbedding(),
        )

        drain = asyncio.create_task(worker.drain_ready())
        await asyncio.wait_for(extractor.started.wait(), 1)
        for _ in range(4):
            await sleeper.pulse(self.clock)
        self.assertGreater(self.clock(), 31)
        extractor.release.set()
        await asyncio.wait_for(vector.started.wait(), 1)
        for _ in range(4):
            await sleeper.pulse(self.clock)
        vector.release.set()
        await asyncio.wait_for(drain, 1)

        self.assertEqual(extractor.calls, 1)
        self.assertEqual(vector.upsert_calls, 1)
        self.assertEqual(self.store.get_extraction_job(job_id)["state"], "completed")
        self.assertEqual(self.store.browse_outbox(profile_id="aura")[0]["state"], "completed")
        await worker.shutdown()

    async def test_late_extraction_lease_loss_does_not_escape_or_release_new_owner(self) -> None:
        job_id = self.seal()
        sleeper = HeartbeatSleeper()
        extractor = SlowFailingAsyncExtractor()
        worker = MemoryWorker(
            clock=self.clock,
            lease_seconds=30,
            heartbeat_seconds=10,
            heartbeat_sleep=sleeper,
        )
        worker.register(
            profile_id="aura", store=self.store, extractor=extractor,
            vector_store=OutboxVectorStore(), embedding_provider=StaticEmbedding(),
        )

        drain = asyncio.create_task(worker.drain_ready())
        await asyncio.wait_for(extractor.started.wait(), 1)
        await sleeper.pulse(self.clock)
        with self.store._get_connection():
            self.store._get_connection().execute(
                """
                update memory_extraction_jobs
                set lease_owner = 'replacement-owner', lease_expires_at = 100
                where id = ?
                """,
                (job_id,),
            )
        extractor.release.set()

        self.assertEqual(await asyncio.wait_for(drain, 1), 1)
        job = self.store.get_extraction_job(job_id)
        self.assertEqual(extractor.calls, 1)
        self.assertEqual(job["state"], "processing")
        self.assertEqual(job["lease_owner"], "replacement-owner")
        await worker.startup()
        self.assertIsNotNone(worker.live_task)
        await worker.shutdown()

    async def test_late_upsert_lease_loss_does_not_escape_or_release_new_owner(self) -> None:
        job_id = self.seal()
        claimed = self.store.claim_extraction_job(
            owner="seed-owner", lease_seconds=30, now=self.clock()
        )
        self.store.complete_extraction_job(
            job_id=job_id,
            owner="seed-owner",
            candidates=(MemoryCandidate(content="用户喜欢热拿铁"),),
            now=self.clock(),
        )
        outbox_id = self.store.browse_outbox(profile_id="aura")[0]["outbox_id"]
        self.assertEqual(claimed["job_id"], job_id)

        sleeper = HeartbeatSleeper()
        vector = SlowFailingAsyncVectorStore()
        worker = MemoryWorker(
            clock=self.clock,
            lease_seconds=30,
            heartbeat_seconds=10,
            heartbeat_sleep=sleeper,
        )
        worker.register(
            profile_id="aura", store=self.store, extractor=FakeExtractor([]),
            vector_store=vector, embedding_provider=StaticEmbedding(),
        )

        drain = asyncio.create_task(worker.drain_ready())
        await asyncio.wait_for(vector.started.wait(), 1)
        await sleeper.pulse(self.clock)
        with self.store._get_connection():
            self.store._get_connection().execute(
                """
                update memory_vector_outbox
                set lease_owner = 'replacement-owner', lease_expires_at = 100
                where id = ?
                """,
                (outbox_id,),
            )
        vector.release.set()

        self.assertEqual(await asyncio.wait_for(drain, 1), 1)
        outbox = self.store.browse_outbox(profile_id="aura")[0]
        self.assertEqual(vector.upsert_calls, 1)
        self.assertEqual(outbox["state"], "processing")
        self.assertEqual(outbox["lease_owner"], "replacement-owner")
        await worker.shutdown()


class MemoryMigrationFailClosedTests(unittest.TestCase):
    def test_malformed_and_future_schema_versions_are_rejected_without_writes(self) -> None:
        for value in ("not-an-integer", "99"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "memory.sqlite3"
                connection = sqlite3.connect(path)
                connection.executescript(
                    """
                    create table memory_schema_info (
                      key text primary key, value text not null, updated_at text not null
                    );
                    create table sentinel (value text not null);
                    insert into sentinel values ('unchanged');
                    """
                )
                connection.execute(
                    "insert into memory_schema_info values ('schema_version', ?, 'before')",
                    (value,),
                )
                connection.commit()
                before = path.read_bytes()
                connection.close()

                with self.assertRaisesRegex(RuntimeError, "schema version"):
                    SQLiteMemoryStore(path)

                self.assertEqual(path.read_bytes(), before)

    def test_failed_v3_migration_rolls_back_every_ddl_and_keeps_v2(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.sqlite3"
            initialized = SQLiteMemoryStore(path)
            initialized.close()
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                drop index idx_memory_extraction_claim;
                drop index idx_memory_extraction_lease;
                drop index idx_memory_outbox_claim;
                drop index idx_memory_outbox_lease;
                drop table memory_extraction_jobs;
                drop table memory_vector_outbox;
                update memory_schema_info set value = '2', updated_at = 'before'
                  where key = 'schema_version';
                create table memory_vector_outbox (id text primary key);
                """
            )
            connection.close()

            with self.assertRaises(sqlite3.OperationalError):
                SQLiteMemoryStore(path)

            check = sqlite3.connect(path)
            version = check.execute(
                "select value from memory_schema_info where key = 'schema_version'"
            ).fetchone()[0]
            extraction = check.execute(
                "select 1 from sqlite_master where type='table' and name='memory_extraction_jobs'"
            ).fetchone()
            check.close()
            self.assertEqual(version, "2")
            self.assertIsNone(extraction)


class QdrantRegistryRaceTests(unittest.TestCase):
    def test_new_open_waits_until_last_slow_close_releases_same_path(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        clients = []

        class SlowClient:
            def __init__(self, *, path):
                self.path = path
                clients.append(self)

            def collection_exists(self, _name):
                return True

            def close(self):
                entered.set()
                release.wait(1)

        with tempfile.TemporaryDirectory() as directory, patch(
            "qdrant_client.QdrantClient", SlowClient
        ):
            path = Path(directory) / "qdrant"
            first = QdrantLocalVectorStore(path=path, vector_size=2)
            close_thread = threading.Thread(target=first.close)
            opened: list[QdrantLocalVectorStore] = []
            errors: list[BaseException] = []

            def open_again() -> None:
                try:
                    opened.append(QdrantLocalVectorStore(path=path, vector_size=2))
                except BaseException as exc:
                    errors.append(exc)

            close_thread.start()
            self.assertTrue(entered.wait(1))
            open_thread = threading.Thread(target=open_again)
            open_thread.start()
            time.sleep(0.03)
            self.assertTrue(open_thread.is_alive())
            self.assertEqual(len(clients), 1)

            release.set()
            close_thread.join(1)
            open_thread.join(1)
            try:
                self.assertEqual(errors, [])
                self.assertEqual(len(clients), 2)
                self.assertEqual(len(opened), 1)
            finally:
                if opened:
                    opened[0].close()

    def test_concurrent_close_of_one_wrapper_decrements_shared_reference_once(self) -> None:
        clients = []

        class CountingClient:
            def __init__(self, *, path):
                self.path = path
                self.close_calls = 0
                clients.append(self)

            def collection_exists(self, _name):
                return True

            def close(self):
                self.close_calls += 1

        class RendezvousCondition:
            def __init__(self, condition):
                self.condition = condition
                self.barrier = threading.Barrier(2)
                self.entry_lock = threading.Lock()
                self.entries = 0

            def __enter__(self):
                with self.entry_lock:
                    self.entries += 1
                    rendezvous = self.entries <= 2
                if rendezvous:
                    self.barrier.wait(1)
                return self.condition.__enter__()

            def __exit__(self, *args):
                return self.condition.__exit__(*args)

            def notify_all(self):
                self.condition.notify_all()

            def wait(self):
                self.condition.wait()

        with tempfile.TemporaryDirectory() as directory, patch(
            "qdrant_client.QdrantClient", CountingClient
        ):
            path = Path(directory) / "qdrant"
            first = QdrantLocalVectorStore(path=path, vector_size=2)
            other = QdrantLocalVectorStore(path=path, vector_size=2)
            import backend.memory.stores.qdrant as qdrant_module

            rendezvous = RendezvousCondition(qdrant_module._CLIENT_CONDITION)
            with patch.object(qdrant_module, "_CLIENT_CONDITION", rendezvous):
                closes = [threading.Thread(target=first.close) for _ in range(2)]
                for thread in closes:
                    thread.start()
                for thread in closes:
                    thread.join(1)
                    self.assertFalse(thread.is_alive())

            self.assertEqual(len(clients), 1)
            self.assertEqual(clients[0].close_calls, 0)
            other.close()
            self.assertEqual(clients[0].close_calls, 1)


class MemoryForgetAndGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_forget_immediately_filters_same_revision_cache_and_queues_durable_delete(self) -> None:
        directory = tempfile.TemporaryDirectory()
        store = SQLiteMemoryStore(Path(directory.name) / "memory.sqlite3")
        vector = OutboxVectorStore()
        bridge = MemoryBridge(
            profile_id="aura", store=store, vector_store=vector,
            embedding_provider=StaticEmbedding(),
        )
        episode = store.create_episode(
            profile_id="aura", source_type="chat", source_id="one",
            role="user", content="用户喜欢热拿铁", metadata={},
        )
        memory_id = store.create_memory(
            profile_id="aura", episode_id=episode,
            candidate=MemoryCandidate(content="用户喜欢热拿铁"),
        )
        try:
            before = await bridge.recall(query="热拿铁", revision=1)
            forgotten = await bridge.forget(memory_ids=(memory_id,), reason="user request")
            after = await bridge.recall(query="热拿铁", revision=1)

            self.assertEqual([item["id"] for item in before], [memory_id])
            self.assertEqual(forgotten["forgottenCount"], 1)
            self.assertEqual(after, ())
            outbox = store.browse_outbox(profile_id="aura")
            self.assertEqual(outbox[0]["operation"], "delete")
            self.assertEqual(outbox[0]["memory_id"], memory_id)
        finally:
            await bridge.shutdown()
            store.close()
            directory.cleanup()

    async def test_browse_and_retry_hide_payload_and_lease_internals(self) -> None:
        directory = tempfile.TemporaryDirectory()
        store = SQLiteMemoryStore(Path(directory.name) / "memory.sqlite3")
        clock = ManualClock()
        job_id = store.seal_episode_job(
            profile_id="aura",
            segments=({"segment_id": "one", "content": "失败"},),
            now=clock(),
        )["job_id"]
        for _delay in (5.0, 30.0, 300.0):
            claimed = store.claim_extraction_job(owner="worker", lease_seconds=10, now=clock())
            released = store.release_extraction_job(
                job_id=claimed["job_id"], owner="worker", error="bad", now=clock()
            )
            clock.value = released["next_attempt_at"]
        claimed = store.claim_extraction_job(owner="worker", lease_seconds=10, now=clock())
        store.release_extraction_job(
            job_id=claimed["job_id"], owner="worker", error="bad", now=clock()
        )
        bridge = MemoryBridge(profile_id="aura", store=store)
        try:
            browsed = await bridge.browse()
            retried = await bridge.retry(job_id)
            encoded = repr((browsed, retried))
            self.assertNotIn("payload", encoded)
            self.assertNotIn("lease", encoded)
            self.assertEqual(browsed["jobs"][0]["kind"], "extraction")
            self.assertEqual(retried["state"], "pending")
            self.assertEqual(retried["attemptCount"], 0)
        finally:
            await bridge.shutdown()
            store.close()
            directory.cleanup()


class MemoryGatewayContractTests(unittest.TestCase):
    def test_versioned_browse_forget_retry_use_camel_case_and_stable_errors(self) -> None:
        class Bridge:
            async def browse(self, *, limit=50):
                return {"items": [{"memoryId": "memory-1", "content": "fact"}], "jobs": []}

            async def forget(self, *, memory_ids=(), episode_ids=(), reason=None):
                self.forgot = (memory_ids, episode_ids, reason)
                return {"forgottenCount": 1, "memoryIds": list(memory_ids)}

            async def retry(self, job_id):
                if job_id == "missing":
                    raise KeyError(job_id)
                return {"jobId": job_id, "state": "pending", "attemptCount": 0}

        app = FastAPI()
        app.state.main_agent_memory = Bridge()
        app.include_router(memory_router)
        with TestClient(app) as client:
            browse = client.get("/api/v1/memories?limit=10")
            forget = client.post(
                "/api/v1/memories/forget",
                json={"memoryIds": ["memory-1"], "episodeIds": [], "reason": "request"},
            )
            retry = client.post("/api/v1/memory-jobs/job-1/retry")
            missing = client.post("/api/v1/memory-jobs/missing/retry")

        self.assertEqual(browse.status_code, 200)
        self.assertEqual(browse.json()["items"][0]["memoryId"], "memory-1")
        self.assertEqual(forget.json()["forgottenCount"], 1)
        self.assertEqual(retry.json(), {"jobId": "job-1", "state": "pending", "attemptCount": 0})
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["error"]["code"], "memory_job_not_found")


if __name__ == "__main__":
    unittest.main()
