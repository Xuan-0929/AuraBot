"""Verify MainAgentRuntime conversation, idle Memory, and Observation control at public boundaries.

These tests cover ActiveInput causality, trigger priority, StrongPause, grants/effect fences, detached
Observation continuation, cancellable conversation archiving, bounded real Snapshots, and physical Renderer
ACK. They avoid private scheduler dictionaries, task names, and fake Renderer compatibility branches.
"""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import threading
import time
import unittest
from collections import deque
from pathlib import Path

from backend.main_agent import (
    ClauseState,
    ContextStore,
    SnapshotProjector,
    WorkspaceReadExecutor,
    WorkspaceReader,
)
from backend.main_agent.runtime import MainAgentRuntime, RendererClause
from backend.main_agent.settings import WorkspaceRegistry
from backend.main_agent.tool_runtime import InvokeMode, ToolSurfacePolicy
from backend.main_agent.tool_runtime.stream import segment_say_text


def tool_delta(call_id: str, name: str, arguments: str) -> dict:
    return {"choices": [{"delta": {"tool_calls": [{
        "index": 0, "id": call_id,
        "function": {"name": name, "arguments": arguments},
    }]}}]}


class ScriptedProvider:
    def __init__(self, *scripts) -> None:
        self.scripts = deque(scripts)
        self.requests = []

    async def __call__(self, request):
        self.requests.append(request)
        script = self.scripts.popleft() if self.scripts else ()
        value = script(request) if callable(script) else script
        if hasattr(value, "__aiter__"):
            async for chunk in value:
                yield chunk
        else:
            for chunk in value:
                yield chunk


class RuntimeTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.store = ContextStore(Path(self.directory.name) / "context.sqlite3")
        self.offers: list[RendererClause] = []

        async def renderer(clause: RendererClause) -> None:
            self.offers.append(clause)

        self.renderer = renderer
        self.runtimes: list[MainAgentRuntime] = []

    async def asyncTearDown(self) -> None:
        for runtime in self.runtimes:
            await runtime.shutdown()
        self.store.close()
        self.directory.cleanup()

    def runtime(self, provider, *, executors=None, renderer=None, **kwargs) -> MainAgentRuntime:
        runtime = MainAgentRuntime(
            context_store=self.store,
            snapshot_projector=SnapshotProjector(),
            tool_policy=ToolSurfacePolicy(
                workspace_ids=("repo",), observation_tools=("web_search", "web_read"),
            ),
            provider=provider,
            renderer=renderer or self.renderer,
            executors=executors or {},
            soul="test soul",
            **kwargs,
        )
        self.runtimes.append(runtime)
        return runtime

    async def wait_for_requests(self, runtime: MainAgentRuntime, provider: ScriptedProvider, count: int) -> None:
        async with asyncio.timeout(1):
            while len(provider.requests) < count:
                await runtime.wait_until_idle()
                await asyncio.sleep(0.001)


class MemoryServiceDouble:
    def __init__(self, outcomes=()) -> None:
        self.outcomes = deque(outcomes)
        self.batches = []
        self.completed = asyncio.Event()
        self.recall_result = {"memories": []}
        self.recall_queries: list[str] = []

    def recall(self, **kwargs):
        self.recall_queries.append(kwargs["query"])
        return self.recall_result

    async def recall_context_async(self, *, max_chars=2_400, **kwargs):
        result = self.recall(**kwargs)
        packed = result.get("packed_context")
        if packed is not None:
            return packed
        memories = result.get("memories", ())
        text = "\n".join(str(item.get("content", "")) for item in memories)[:max_chars]
        return {
            "text": text,
            "memory_ids": [item["memory_id"] for item in memories if item.get("memory_id")],
            "char_count": len(text),
            "max_chars": max_chars,
        }

    async def remember_conversation(self, **kwargs):
        self.batches.append(kwargs)
        outcome = self.outcomes.popleft() if self.outcomes else {"state": "active"}
        if isinstance(outcome, BaseException):
            raise outcome
        outcome = dict(outcome)
        outcome.setdefault("summary", "已归档的近期对话摘要")
        self.completed.set()
        return outcome


class BlockingMemoryService(MemoryServiceDouble):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def remember_conversation(self, **kwargs):
        self.batches.append(kwargs)
        self.started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


class FailThenReleaseMemoryService(MemoryServiceDouble):
    def __init__(self) -> None:
        super().__init__()
        self.failed = asyncio.Event()
        self.release = asyncio.Event()

    async def remember_conversation(self, **kwargs):
        self.batches.append(kwargs)
        if len(self.batches) == 1:
            self.failed.set()
            raise RuntimeError("temporary")
        await self.release.wait()
        self.completed.set()
        return {"state": "active", "summary": "失败后成功归档"}


class ThreadBlockingRecallMemoryService(MemoryServiceDouble):
    def __init__(self, *results) -> None:
        super().__init__()
        self.results = deque(results or ({"text": "旧记忆", "memory_ids": ["old"]},))
        self.started = threading.Event()
        self.release = threading.Event()
        self.queries: list[str] = []
        self.recall_started_at = 0.0

    def recall(self, **kwargs):
        self.queries.append(kwargs["query"])
        self.recall_started_at = time.monotonic()
        self.started.set()
        self.release.wait(1)
        result = self.results.popleft() if self.results else {"text": "", "memory_ids": []}
        text = result["text"]
        return {
            "packed_context": {
                **result,
                "char_count": len(text),
                "max_chars": kwargs.get("context_hints", {}).get("context_budget_chars", 2_400),
            },
        }

    async def recall_context_async(self, *, max_chars=2_400, **kwargs):
        result = await asyncio.to_thread(
            self.recall,
            **kwargs,
            context_hints={"context_budget_chars": max_chars},
        )
        return result["packed_context"]


class ConversationMemoryLifecycleTests(RuntimeTestCase):
    async def test_startup_recovers_playing_clause_before_resuming_idle_archive(self) -> None:
        self.store.append_event(
            "user_segment",
            {"segment_id": "persisted", "input_id": "input-old", "text": "跨重启保留"},
        )
        self.store.create_clause("ghost", "未播放完成")
        self.store.transition_clause("ghost", ClauseState.TTS_QUEUED)
        self.store.transition_clause("ghost", ClauseState.PLAYING)
        self.store.set_last_user_activity(10.0)
        path = self.store.path
        self.store.close()
        self.store = ContextStore(path)
        memory = MemoryServiceDouble()
        runtime = self.runtime(
            ScriptedProvider(), memory_service=memory, owner_profile_id="profile-a",
            memory_idle_seconds=300.0, wall_clock=lambda: 400.0,
        )

        await runtime.startup()
        await asyncio.wait_for(memory.completed.wait(), 0.2)

        self.assertEqual(self.store.get_clause("ghost").state, ClauseState.INTERRUPTED)
        self.assertEqual(
            memory.batches[0]["turns"],
            ({"role": "user", "content": "跨重启保留"},),
        )

    async def test_startup_preserves_delivered_clause_and_canonical_turn(self) -> None:
        self.store.create_clause("delivered", "已经听到")
        self.store.transition_clause("delivered", ClauseState.TTS_QUEUED)
        self.store.transition_clause("delivered", ClauseState.PLAYING)
        self.assertTrue(self.store.ack_clause("delivered", complete=True))
        path = self.store.path
        self.store.close()
        self.store = ContextStore(path)
        runtime = self.runtime(ScriptedProvider())

        await runtime.startup()

        self.assertEqual(self.store.get_clause("delivered").state, ClauseState.DELIVERED)
        self.assertEqual(
            self.store.conversation_batch().turns,
            ({"role": "assistant", "content": "已经听到"},),
        )

    async def test_startup_immediately_archives_an_expired_persisted_idle_deadline(self) -> None:
        self.store.append_event(
            "user_segment",
            {"segment_id": "persisted", "input_id": "input-old", "text": "跨重启保留"},
        )
        self.store.set_last_user_activity(10.0)
        memory = MemoryServiceDouble()
        runtime = self.runtime(
            ScriptedProvider(), memory_service=memory, owner_profile_id="profile-a",
            memory_idle_seconds=300.0, wall_clock=lambda: 400.0,
        )

        await runtime.startup()
        await asyncio.wait_for(memory.completed.wait(), 1)

        self.assertEqual(
            memory.batches[0]["turns"],
            ({"role": "user", "content": "跨重启保留"},),
        )
        self.assertEqual(self.store.fold().conversation_turns, ())
        self.assertIsNone(self.store.get_last_user_activity())

    async def test_idle_archive_waits_for_assistant_delivery_to_settle(self) -> None:
        memory = MemoryServiceDouble()
        provider = ScriptedProvider((tool_delta("say", "say_to_user", '{"text":"我记住了。"}'),))
        runtime = self.runtime(
            provider, memory_service=memory, owner_profile_id="profile-a",
            memory_idle_seconds=0.01,
        )

        await runtime.submit_text("我喜欢红茶")
        await runtime.wait_until_idle()
        await asyncio.sleep(0.03)
        self.assertEqual(memory.batches, [])

        clause = self.offers[0]
        await runtime.renderer_tts_queued(clause.clause_id)
        await runtime.renderer_playing(clause.clause_id)
        await runtime.renderer_ack(clause.clause_id, complete=True)
        await asyncio.wait_for(memory.completed.wait(), 1)

        self.assertEqual(
            memory.batches[0]["turns"],
            (
                {"role": "user", "content": "我喜欢红茶"},
                {"role": "assistant", "content": "我记住了。"},
            ),
        )

    async def test_confirmed_text_is_archived_after_idle_and_success_clears_only_conversation(self) -> None:
        memory = MemoryServiceDouble()
        runtime = self.runtime(
            ScriptedProvider(), memory_service=memory, owner_profile_id="profile-a",
            memory_idle_seconds=0.01,
        )
        self.store.append_event("observation", {"id": "o1", "status": "completed"})

        await runtime.submit_text("我喜欢红茶")
        await asyncio.wait_for(memory.completed.wait(), 1)

        self.assertEqual(memory.batches[0]["profile_id"], "profile-a")
        self.assertEqual(memory.batches[0]["turns"], ({"role": "user", "content": "我喜欢红茶"},))
        self.assertEqual(self.store.fold().conversation_turns, ())
        self.assertEqual(self.store.fold().observations, ({"id": "o1", "status": "completed"},))
        self.assertIsNone(self.store.get_last_user_activity())

    async def test_vad_start_cancels_inflight_extraction_without_clearing_buffer(self) -> None:
        memory = BlockingMemoryService()
        runtime = self.runtime(
            ScriptedProvider(), memory_service=memory, owner_profile_id="profile-a",
            memory_idle_seconds=0.01,
        )
        await runtime.submit_text("先保留这段")
        await asyncio.wait_for(memory.started.wait(), 1)

        await runtime.vad_start()
        await asyncio.wait_for(memory.cancelled.wait(), 1)

        self.assertEqual(
            [turn["content"] for turn in self.store.fold().conversation_turns],
            ["先保留这段"],
        )

    async def test_failed_write_retries_after_another_idle_interval(self) -> None:
        memory = FailThenReleaseMemoryService()
        runtime = self.runtime(
            ScriptedProvider(), memory_service=memory, owner_profile_id="profile-a",
            memory_idle_seconds=0.01,
        )

        await runtime.submit_text("失败后再试")
        original_input = runtime.active_input
        await asyncio.wait_for(memory.failed.wait(), 1)

        self.assertEqual(runtime.active_input, original_input)
        self.assertEqual(
            self.store.conversation_batch().turns,
            ({"role": "user", "content": "失败后再试"},),
        )
        memory.release.set()
        await asyncio.wait_for(memory.completed.wait(), 1)
        async with asyncio.timeout(1):
            while self.store.conversation_batch() is not None:
                await asyncio.sleep(0)

        self.assertEqual(len(memory.batches), 2)
        self.assertEqual(self.store.fold().conversation_turns, ())
        self.assertIsNone(runtime.active_input)

    async def test_silent_archive_retires_active_input_and_keeps_summary_for_next_snapshot(self) -> None:
        summary = "用户正在比较方案 A 和方案 B，并倾向继续使用方案 B。"
        memory = MemoryServiceDouble(({"state": "active", "summary": summary},))
        provider = ScriptedProvider((), ())
        runtime = self.runtime(
            provider, memory_service=memory, owner_profile_id="profile-a",
            memory_idle_seconds=0.01,
        )

        await runtime.submit_text("比较方案 A 和方案 B")
        old_input_id = runtime.active_input.input_id
        await asyncio.wait_for(memory.completed.wait(), 1)
        async with asyncio.timeout(1):
            while self.store.conversation_batch() is not None:
                await asyncio.sleep(0)

        self.assertIsNone(runtime.active_input)
        self.assertEqual(self.store.fold().conversation_summaries[0]["content"], summary)
        runtime.memory_idle_seconds = 60
        await runtime.submit_text("那就按照第二个继续")
        await self.wait_for_requests(runtime, provider, 2)

        current = runtime.active_input
        self.assertNotEqual(current.input_id, old_input_id)
        self.assertEqual(current.revision, 1)
        self.assertEqual(current.latest_transcript, "那就按照第二个继续")
        self.assertEqual(memory.recall_queries[-1], "那就按照第二个继续")
        snapshot = provider.requests[-1].snapshot
        self.assertEqual(snapshot.conversation_summaries[0]["content"], summary)
        self.assertEqual(
            [turn["content"] for turn in snapshot.conversation_turns],
            ["那就按照第二个继续"],
        )

    async def test_context_archive_failure_keeps_input_and_retries_same_source(self) -> None:
        failure = threading.Event()
        self.store._connection.create_function(
            "notify_archive_failure", 0, lambda: failure.set() or 0,
        )
        self.store._connection.executescript(
            """
            create trigger fail_runtime_archive_delete
            before delete on context_events
            when old.event_type = 'user_segment'
            begin
              select notify_archive_failure();
              select raise(abort, 'archive delete failed');
            end;
            """
        )
        memory = MemoryServiceDouble((
            {"state": "active", "summary": "失败后仍需保留"},
            {"state": "active", "summary": "失败后仍需保留"},
        ))
        runtime = self.runtime(
            ScriptedProvider(), memory_service=memory, owner_profile_id="profile-a",
            memory_idle_seconds=0.01,
        )

        await runtime.submit_text("归档失败时保留")
        original_input = runtime.active_input
        self.assertTrue(await asyncio.to_thread(failure.wait, 0.5))

        self.assertEqual(runtime.active_input, original_input)
        self.assertEqual(
            self.store.conversation_batch().turns,
            ({"role": "user", "content": "归档失败时保留"},),
        )
        self.assertEqual(self.store.fold().conversation_summaries, ())
        connection = sqlite3.connect(self.store.path)
        connection.execute("drop trigger fail_runtime_archive_delete")
        connection.close()
        async with asyncio.timeout(0.5):
            while len(memory.batches) < 2 or self.store.conversation_batch() is not None:
                await asyncio.sleep(0.001)

        self.assertEqual(memory.batches[0]["source_id"], memory.batches[1]["source_id"])
        self.assertIsNone(runtime.active_input)
        self.assertEqual(len(self.store.fold().conversation_summaries), 1)

    async def test_recall_memories_are_projected_into_each_invoke_snapshot(self) -> None:
        memory = MemoryServiceDouble()
        memory.recall_result = {"memories": [{"memory_id": "m1", "content": "用户喜欢红茶"}]}
        provider = ScriptedProvider(())
        runtime = self.runtime(
            provider, memory_service=memory, owner_profile_id="profile-a",
            memory_idle_seconds=60,
        )

        await runtime.submit_text("还记得吗")
        await runtime.wait_until_idle()

        self.assertEqual(
            provider.requests[0].snapshot.memory_facts,
            ({"text": "用户喜欢红茶", "memory_ids": ["m1"]},),
        )
        self.assertNotIn("content", provider.requests[0].snapshot.memory_facts[0])

    async def test_blocking_sync_recall_does_not_delay_vad_start(self) -> None:
        memory = ThreadBlockingRecallMemoryService()
        runtime = self.runtime(
            ScriptedProvider(), memory_service=memory, owner_profile_id="profile-a",
            memory_idle_seconds=60,
        )

        async def vad_after_recall_starts() -> float:
            await asyncio.to_thread(memory.started.wait, 0.5)
            await runtime.vad_start()
            return time.monotonic() - memory.recall_started_at

        vad = asyncio.create_task(vad_after_recall_starts())
        timer = threading.Timer(0.2, memory.release.set)
        timer.start()
        try:
            await runtime.submit_text("不要阻塞 VAD")
            elapsed = await asyncio.wait_for(vad, 0.5)
        finally:
            memory.release.set()
            timer.cancel()

        self.assertLess(elapsed, 0.08)

    async def test_recall_timeout_fails_soft_and_provider_still_runs(self) -> None:
        memory = ThreadBlockingRecallMemoryService()
        provider = ScriptedProvider(())
        runtime = self.runtime(
            provider, memory_service=memory, owner_profile_id="profile-a",
            memory_idle_seconds=60, memory_recall_timeout_seconds=0.03,
        )
        started_at = time.monotonic()
        try:
            await runtime.submit_text("超时也要回答")
            await self.wait_for_requests(runtime, provider, 1)
        finally:
            memory.release.set()

        self.assertLess(time.monotonic() - started_at, 0.15)
        self.assertEqual(provider.requests[0].snapshot.memory_facts, ())

    async def test_stale_recall_result_is_discarded_after_revision_changes(self) -> None:
        memory = ThreadBlockingRecallMemoryService(
            {"text": "旧 revision 的记忆", "memory_ids": ["old"]},
            {"text": "新 revision 的记忆", "memory_ids": ["new"]},
        )
        provider = ScriptedProvider(())
        runtime = self.runtime(
            provider, memory_service=memory, owner_profile_id="profile-a",
            memory_idle_seconds=60, memory_recall_timeout_seconds=0.5,
        )

        await runtime.submit_text("第一段")
        self.assertTrue(await asyncio.to_thread(memory.started.wait, 0.2))
        await runtime.submit_text("第二段")
        memory.release.set()
        await self.wait_for_requests(runtime, provider, 1)

        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(provider.requests[0].revision, 2)
        self.assertEqual(
            provider.requests[0].snapshot.memory_facts,
            ({"text": "新 revision 的记忆", "memory_ids": ["new"]},),
        )
        self.assertEqual(memory.queries, ["第一段", "第一段\n第二段"])


class ActiveInputAndSchedulingTests(RuntimeTestCase):
    async def test_stable_segment_replaces_in_place_and_new_utterance_appends(self) -> None:
        runtime = self.runtime(ScriptedProvider())
        await runtime.update_transcript("我想", segment_id="seg-1", request_prepare=False)
        await runtime.update_transcript("我想查天气", segment_id="seg-1", request_prepare=False)
        await runtime.update_transcript("再看看上海", segment_id="seg-2", request_prepare=False)

        active = runtime.active_input
        self.assertIsNotNone(active)
        self.assertEqual([(item.segment_id, item.text) for item in active.segments], [
            ("seg-1", "我想查天气"), ("seg-2", "再看看上海"),
        ])
        self.assertEqual(active.latest_transcript, "我想查天气\n再看看上海")

    async def test_pending_triggers_coalesce_to_respond_over_continue_over_prepare(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def held_prepare(_request):
            started.set()
            await release.wait()
            if False:
                yield {}

        provider = ScriptedProvider(held_prepare, ())
        runtime = self.runtime(provider)
        await runtime.update_transcript("first", segment_id="seg-1", request_prepare=False)
        await runtime.request_prepare("checkpoint", high_priority=True)
        await asyncio.wait_for(started.wait(), 1)
        await runtime.request_continue("observation")
        await runtime.submit_text("second")
        release.set()
        await runtime.wait_until_idle()

        self.assertEqual([request.mode for request in provider.requests], [
            InvokeMode.PREPARE, InvokeMode.RESPOND,
        ])

    async def test_strong_pause_settles_prepare_before_current_respond_snapshot(self) -> None:
        started = asyncio.Event()
        settled = asyncio.Event()

        async def held_prepare(_request):
            started.set()
            try:
                await asyncio.Event().wait()
                if False:
                    yield {}
            finally:
                settled.set()

        provider = ScriptedProvider(held_prepare, ())
        runtime = self.runtime(provider)
        await runtime.update_transcript("先说一半", segment_id="seg-1", request_prepare=False)
        await runtime.request_prepare("checkpoint", high_priority=True)
        await asyncio.wait_for(started.wait(), 1)
        await runtime.update_transcript("完整问题", segment_id="seg-1", request_prepare=False)

        grant = await runtime.eou_complete()
        await runtime.wait_until_idle()

        self.assertTrue(settled.is_set())
        self.assertEqual(provider.requests[-1].mode, InvokeMode.RESPOND)
        self.assertEqual(provider.requests[-1].snapshot.conversation_turns[-1]["content"], "完整问题")
        self.assertEqual(grant.revision, runtime.active_input.revision)

    async def test_noise_preserves_response_but_confirmed_barge_in_revokes_it(self) -> None:
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def held_response(_request):
            started.set()
            try:
                await asyncio.Event().wait()
                if False:
                    yield {}
            finally:
                cancelled.set()

        runtime = self.runtime(ScriptedProvider(held_response))
        grant = await runtime.submit_text("请回答")
        await asyncio.wait_for(started.wait(), 1)
        await runtime.vad_start()
        await runtime.reject_speech_candidate()
        self.assertEqual(runtime.current_response_grant, grant)
        self.assertFalse(cancelled.is_set())

        await runtime.vad_start()
        await runtime.confirm_speech()
        await asyncio.wait_for(cancelled.wait(), 1)
        self.assertIsNone(runtime.current_response_grant)


class EffectsObservationAndDeliveryTests(RuntimeTestCase):
    async def test_effect_fence_rejects_say_after_local_speech_candidate(self) -> None:
        started = asyncio.Event()
        proceed = asyncio.Event()

        async def late_say(_request):
            started.set()
            await proceed.wait()
            yield tool_delta("say-1", "say_to_user", '{"text":"stale"}')

        provider = ScriptedProvider(late_say)
        runtime = self.runtime(provider)
        await runtime.submit_text("question")
        await asyncio.wait_for(started.wait(), 1)
        await runtime.vad_start()
        proceed.set()
        await runtime.wait_until_idle()

        self.assertEqual(self.offers, [])
        self.assertEqual(provider.requests[0].settlement.receipts[0].code, "speech_fenced")

    async def test_safe_observation_detaches_and_triggers_continue(self) -> None:
        completed = asyncio.Event()

        async def search(call):
            await asyncio.sleep(0.01)
            completed.set()
            return {"answer": call.arguments["query"]}

        provider = ScriptedProvider(
            (tool_delta("search-1", "web_search", '{"query":"AuraBot"}'),),
            (),
        )
        runtime = self.runtime(provider, executors={"web_search": search})
        await runtime.submit_text("search")
        await runtime.wait_until_idle()
        await asyncio.wait_for(completed.wait(), 1)
        for _ in range(20):
            await runtime.wait_until_idle()
            if len(provider.requests) == 2:
                break
            await asyncio.sleep(0.01)

        self.assertEqual([request.mode for request in provider.requests], [
            InvokeMode.RESPOND, InvokeMode.CONTINUE,
        ])
        observations = self.store.fold().observations
        self.assertEqual(observations[-1]["status"], "completed")

    async def test_large_search_result_is_visible_in_bounded_continue_snapshot(self) -> None:
        async def search(_call):
            return {
                "query": "AuraBot",
                "results": [{
                    "source_id": "source-1",
                    "title": "AuraBot",
                    "snippet": "search-result-" + "x" * 5000,
                }],
            }

        provider = ScriptedProvider(
            (tool_delta("search-large", "web_search", '{"query":"AuraBot"}'),),
            (),
        )
        runtime = self.runtime(provider, executors={"web_search": search})

        await runtime.submit_text("search")
        await self.wait_for_requests(runtime, provider, 2)

        continuation = provider.requests[1]
        self.assertEqual(continuation.mode, InvokeMode.CONTINUE)
        self.assertEqual(len(continuation.snapshot.observations), 1)
        observation = continuation.snapshot.observations[0]
        self.assertEqual(observation["status"], "completed")
        self.assertTrue(observation["truncated"])
        self.assertIn("search-result", observation["result"]["results"][0]["snippet"])
        self.assertLessEqual(len(continuation.snapshot.prompt), 12_000)

    async def test_large_workspace_read_keeps_content_and_next_page_in_continue_snapshot(self) -> None:
        root = Path(self.directory.name).resolve() / "workspace"
        root.mkdir()
        (root / "large.txt").write_text(
            "".join(f"line-{index:03d}-" + "x" * 110 + "\n" for index in range(200)),
            encoding="utf-8",
        )
        reader = WorkspaceReader(WorkspaceRegistry({"repo": root}))
        provider = ScriptedProvider(
            (tool_delta(
                "workspace-large",
                "workspace_read",
                '{"workspace_id":"repo","path":"large.txt","start_line":1,"max_lines":200}',
            ),),
            (),
        )
        runtime = self.runtime(
            provider,
            executors={"workspace_read": WorkspaceReadExecutor(reader)},
        )

        await runtime.submit_text("read workspace")
        await self.wait_for_requests(runtime, provider, 2)

        continuation = provider.requests[1]
        canonical = self.store.fold().observations[-1]
        observation = continuation.snapshot.observations[0]
        self.assertEqual(canonical["status"], "completed")
        self.assertEqual(observation["kind"], "workspace_read")
        self.assertTrue(observation["truncated"])
        self.assertTrue(observation["result"]["content"])
        self.assertIsNotNone(observation["result"]["next_start_line"])
        self.assertLessEqual(len(continuation.snapshot.prompt), 12_000)

    async def test_only_complete_playing_ack_commits_assistant_text(self) -> None:
        provider = ScriptedProvider((
            tool_delta("say-1", "say_to_user", '{"text":"first。second"}'),
        ))
        runtime = self.runtime(provider)
        await runtime.submit_text("hello")
        await runtime.wait_until_idle()
        first, second = self.offers

        await runtime.renderer_tts_queued(first.clause_id)
        await runtime.renderer_playing(first.clause_id)
        self.assertTrue(await runtime.renderer_ack(first.clause_id, complete=True))
        await runtime.renderer_tts_queued(second.clause_id)
        await runtime.renderer_playing(second.clause_id)
        self.assertFalse(await runtime.renderer_ack(second.clause_id, complete=False))

        conversation = self.store.fold().conversation_turns
        self.assertEqual([(turn["role"], turn["content"]) for turn in conversation], [
            ("user", "hello"), ("assistant", "first。"),
        ])

    async def test_silent_invoke_and_ordinary_content_never_create_speech(self) -> None:
        provider = ScriptedProvider(
            ({"choices": [{"delta": {"content": "not user-visible"}}]},),
            (),
        )
        runtime = self.runtime(provider)
        await runtime.submit_text("one")
        await runtime.wait_until_idle()
        await runtime.submit_text("two")
        await runtime.wait_until_idle()
        self.assertEqual(self.offers, [])


class ClauseSegmentationTests(unittest.TestCase):
    def test_natural_boundaries_preserve_every_codepoint(self) -> None:
        text = "你好！This is a longer clause, with 3.14 and example.com/path. 最后一句"
        clauses = segment_say_text(text, final=True)
        self.assertEqual("".join(clauses), text)
        self.assertTrue(all(clauses))
