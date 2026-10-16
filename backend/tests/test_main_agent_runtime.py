"""Verify MainAgentRuntime causal control, sealed Memory work, and Observation behavior.

The tests cover ActiveInput causality, trigger priority, StrongPause, grants/effect fences, detached
Observation continuation, seal-time retirement, non-cancellable new-input archive ownership, same-boundary
retry, bounded real Snapshots, and physical Renderer ACK through public Runtime behavior.
No-say invocations remain silent; tool receipts are retained only in content-free backend diagnostics.
Public SAY segmentation checks preserve text, count English words as two units, and group at complete sentence ends without forced length cuts.
Recognition failure retires uncertain input and prevents it leaking into the next request's context.
Weak pauses alone start speculation; duplicate endpoints, batched results, task notifications and
pending speech snapshots exercise the production scheduler without network or model calls.
"""

from __future__ import annotations

import asyncio
import tempfile
import threading
import time
import unittest
from collections import deque
from pathlib import Path

from backend.main_agent import (
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
        result = dict(outcome)
        result.setdefault("summary", "已归档的近期对话摘要")
        self.completed.set()
        return result


class BlockingSealedMemoryService(MemoryServiceDouble):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def remember_conversation(self, **kwargs):
        self.batches.append(kwargs)
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        self.completed.set()
        return {"state": "active", "summary": "旧对话已归档"}


class FailThenReleaseMemoryService(MemoryServiceDouble):
    def __init__(self) -> None:
        super().__init__()
        self.failed = asyncio.Event()
        self.retry_started = asyncio.Event()
        self.release = asyncio.Event()

    async def remember_conversation(self, **kwargs):
        self.batches.append(kwargs)
        if len(self.batches) == 1:
            self.failed.set()
            raise RuntimeError("temporary")
        self.retry_started.set()
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
    async def test_sealed_archive_does_not_block_or_cancel_new_causal_input(self) -> None:
        memory = BlockingSealedMemoryService()
        provider = ScriptedProvider((), ())
        runtime = self.runtime(
            provider, memory_service=memory, owner_profile_id="profile-a",
            memory_idle_seconds=0.01,
        )

        await runtime.submit_text("需要归档的旧输入")
        await self.wait_for_requests(runtime, provider, 1)
        old_input_id = provider.requests[0].input_id
        await asyncio.wait_for(memory.started.wait(), 1)
        self.assertIsNone(runtime.active_input)

        runtime.memory_idle_seconds = 60
        grant = await asyncio.wait_for(runtime.submit_text("archive 中的新问题"), 0.2)
        await self.wait_for_requests(runtime, provider, 2)
        new_input = runtime.active_input

        self.assertNotEqual(new_input.input_id, old_input_id)
        self.assertEqual(new_input.revision, 1)
        self.assertEqual(provider.requests[-1].mode, InvokeMode.RESPOND)
        self.assertEqual(provider.requests[-1].input_id, new_input.input_id)
        self.assertEqual(runtime.current_response_grant.grant_id, grant.grant_id)
        self.assertFalse(memory.cancelled.is_set())
        self.assertFalse(memory.release.is_set())

        memory.release.set()
        await asyncio.wait_for(memory.completed.wait(), 1)
        async with asyncio.timeout(1):
            while not self.store.fold().conversation_summaries:
                await asyncio.sleep(0.001)

        self.assertFalse(memory.cancelled.is_set())
        self.assertEqual(runtime.active_input, new_input)
        self.assertEqual(runtime.current_response_grant.grant_id, grant.grant_id)
        self.assertEqual(provider.requests[-1].input_id, new_input.input_id)
        self.assertEqual(
            [turn["content"] for turn in self.store.fold().conversation_turns],
            ["archive 中的新问题"],
        )
        self.assertEqual(
            memory.batches[0]["turns"],
            ({"role": "user", "content": "需要归档的旧输入"},),
        )

    async def test_failed_archive_retries_same_pending_boundary_after_idle(self) -> None:
        memory = FailThenReleaseMemoryService()
        runtime = self.runtime(
            ScriptedProvider(), memory_service=memory, owner_profile_id="profile-a",
            memory_idle_seconds=0.01,
        )

        await runtime.submit_text("失败后再试")
        await asyncio.wait_for(memory.failed.wait(), 1)

        self.assertIsNone(runtime.active_input)
        pending = self.store.pending_conversation_archive()
        self.assertIsNotNone(pending)
        self.assertEqual(
            self.store.conversation_archive_turns(pending),
            ({"role": "user", "content": "失败后再试"},),
        )
        self.assertEqual(
            self.store.conversation_batch().turns,
            ({"role": "user", "content": "失败后再试"},),
        )

        await asyncio.wait_for(memory.retry_started.wait(), 1)
        self.assertEqual(len(memory.batches), 2)
        self.assertEqual(memory.batches[0]["source_id"], memory.batches[1]["source_id"])
        self.assertEqual(memory.batches[0]["turns"], memory.batches[1]["turns"])
        self.assertEqual(self.store.pending_conversation_archive(), pending)

        memory.release.set()
        await asyncio.wait_for(memory.completed.wait(), 1)
        async with asyncio.timeout(1):
            while self.store.pending_conversation_archive() is not None:
                await asyncio.sleep(0.001)
        self.assertIsNone(self.store.conversation_batch())

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
    async def test_transcripts_wait_for_weak_pause_and_duplicate_endpoints_respond_once(self):
        provider = ScriptedProvider()
        runtime = self.runtime(provider)
        for text in ("我想", "我想查一下上海明天的天气。"):
            await runtime.update_transcript(text, segment_id="voice")
            await runtime.wait_until_idle()
        self.assertEqual(provider.requests, [])
        await runtime.vad_stop_candidate()
        await runtime.eou_incomplete()
        await runtime.wait_until_idle()
        self.assertEqual([r.mode for r in provider.requests], [InvokeMode.PREPARE])
        self.assertNotIn("say_to_user", provider.requests[0].tool_surface.names)
        self.assertNotIn("task_assign", provider.requests[0].tool_surface.names)
        grants = await asyncio.gather(runtime.eou_complete(), runtime.endpoint_fallback())
        await runtime.wait_until_idle()
        await runtime.update_transcript("我想查一下上海明天的天气。", segment_id="voice")
        again = await runtime.eou_complete()
        await runtime.wait_until_idle()
        self.assertEqual(grants[0], grants[1])
        self.assertEqual(again, grants[0])
        self.assertEqual([r.mode for r in provider.requests], [InvokeMode.PREPARE, InvokeMode.RESPOND])

    async def test_speculation_reuses_completed_results_without_waiting_for_slow_reads(self):
        release = asyncio.Event()
        async def search(call):
            if call.arguments["query"] == "slow":
                await release.wait()
            return {"answer": call.arguments["query"]}
        calls = [tool_delta("fast", "web_search", '{"query":"fast"}'),
                 tool_delta("slow", "web_search", '{"query":"slow"}')]
        calls[1]["choices"][0]["delta"]["tool_calls"][0]["index"] = 1
        provider = ScriptedProvider(calls, (), ())
        runtime = self.runtime(provider, executors={"web_search": search})
        await runtime.update_transcript("查询信息", segment_id="voice")
        await runtime.eou_incomplete()
        await runtime.wait_until_idle()
        async with asyncio.timeout(1):
            while not any(o.get("status") == "completed" for o in self.store.fold().observations):
                await asyncio.sleep(0)
        self.assertEqual(len(provider.requests), 1)  # No CONTINUE before a strong pause.
        await asyncio.wait_for(runtime.eou_complete(), .5)
        await asyncio.wait_for(runtime.wait_until_idle(), .5)
        response = provider.requests[1]
        self.assertEqual(response.mode, InvokeMode.RESPOND)
        self.assertEqual({o["status"] for o in response.snapshot.observations}, {"completed", "running"})
        release.set()
        await runtime.harness.wait_for_observations()
        await runtime.wait_until_idle()
        self.assertEqual([r.mode for r in provider.requests], [InvokeMode.PREPARE, InvokeMode.RESPOND, InvokeMode.CONTINUE])
        self.assertEqual(len(provider.requests[-1].snapshot.runtime_state["new_observation_ids"]), 1)

    async def test_completed_read_batch_and_speech_delivery_state_are_consumed_once(self):
        release = asyncio.Event()
        async def search(call):
            await release.wait()
            return {"query": call.arguments["query"]}
        calls = [tool_delta("say", "say_to_user", '{"text":"我来查一下。结果稍后告诉你。"}')]
        for index in (1, 2):
            call = tool_delta(f"read-{index}", "web_search", '{"query":"' + str(index) + '"}')
            call["choices"][0]["delta"]["tool_calls"][0]["index"] = index
            calls.append(call)
        provider = ScriptedProvider(calls, ())
        runtime = self.runtime(provider, executors={"web_search": search})
        await runtime.submit_text("查两份资料")
        await runtime.wait_until_idle()
        await runtime.renderer_ack(self.offers[0].clause_id, complete=True, mode="text")
        release.set()
        await runtime.harness.wait_for_observations()
        await runtime.wait_until_idle()
        self.assertEqual(len(provider.requests), 2)
        state = provider.requests[1].snapshot.runtime_state
        self.assertEqual(len(state["new_observation_ids"]), 2)
        self.assertEqual([c["text"] for c in state["speech_delivery"]["delivered"]], ["我来查一下。"])
        self.assertEqual([c["text"] for c in state["speech_delivery"]["scheduled"]], ["结果稍后告诉你。"])
        self.assertNotIn("结果稍后告诉你。", str(provider.requests[1].snapshot.conversation_turns))
        await runtime.wait_until_idle()
        self.assertEqual(len(provider.requests), 2)

    async def test_late_read_from_old_input_does_not_wake_new_conversation(self):
        release = asyncio.Event()
        async def search(_call):
            await release.wait()
            return {"answer": "old"}
        provider = ScriptedProvider((tool_delta("read", "web_search", '{"query":"old"}'),), ())
        runtime = self.runtime(provider, executors={"web_search": search})
        await runtime.submit_text("old")
        await runtime.wait_until_idle()
        # Seal the old scope just as the existing memory owner does before a later conversation.
        runtime._retire_sealed_input_locked()
        await runtime.submit_text("new")
        await runtime.wait_until_idle()
        release.set()
        await runtime.harness.wait_for_observations()
        await runtime.wait_until_idle()
        self.assertEqual(len(provider.requests), 2)

    async def test_task_progress_updates_context_without_waking_and_completion_is_new_work(self):
        provider = ScriptedProvider()
        runtime = self.runtime(provider)
        await runtime.submit_text("安排任务")
        await runtime.wait_until_idle()
        fact = {"id": "task:t", "kind": "task", "task_id": "t", "title": "探索目录"}
        for version, status in enumerate(("queued", "running"), 1):
            async with runtime._lock:
                runtime._accept_task_fact_locked({**fact, "version": version, "status": status})
            await runtime.wait_until_idle()
        self.assertEqual(len(provider.requests), 1)
        complete = {**fact, "version": 3, "status": "completed", "result": "已完成"}
        async with runtime._lock:
            runtime._accept_task_fact_locked(complete)
        await runtime.wait_until_idle()
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(provider.requests[-1].snapshot.runtime_state["new_task_fact_ids"], ["task:t"])
        async with runtime._lock:
            runtime._accept_task_fact_locked(complete)
        await runtime.wait_until_idle()
        self.assertEqual(len(provider.requests), 2)
        await runtime.renderer_disconnected()
        async with runtime._lock:
            runtime._accept_task_fact_locked({**complete, "version": 4, "status": "failed"})
        await runtime.wait_until_idle()
        self.assertEqual(len(provider.requests), 2)

    async def test_stt_start_failure_after_previous_input_is_visible(self):
        runtime = self.runtime(ScriptedProvider())
        from unittest.mock import AsyncMock
        runtime.error_listener = AsyncMock()
        await runtime.update_transcript("previous words", segment_id="previous")
        await runtime.confirm_speech()
        await runtime.speech_input_failed("new", has_transcript=False)
        self.assertIsNone(runtime.active_input)
        runtime.error_listener.assert_awaited_once_with("stt_transcription_failed")

    async def test_failed_speech_retires_partial_and_fences_late_failure(self):
        runtime = self.runtime(ScriptedProvider())
        errors = []
        async def report(code):
            errors.append(code)
        runtime.error_listener = report
        await runtime.update_transcript("uncertain command", segment_id="old")
        self.store.append_event("think_note", {"text": "act on uncertain command"})
        old_id = runtime.active_input.input_id
        await runtime.speech_input_failed("old")
        self.assertIsNone(runtime.active_input)
        await runtime.wait_until_idle()
        self.assertNotIn("uncertain command", str(self.store.fold().conversation_turns))
        self.assertFalse(self.store.fold().think_note)
        await runtime.update_transcript("new words", segment_id="new")
        self.assertNotEqual(runtime.active_input.input_id, old_id)
        await runtime.speech_input_failed("old")
        self.assertEqual(runtime.active_input.latest_transcript, "new words")
        self.assertEqual(errors, ["stt_transcription_failed"])

    async def test_stable_segment_replaces_in_place_and_new_utterance_appends(self) -> None:
        runtime = self.runtime(ScriptedProvider())
        await runtime.update_transcript("我想", segment_id="seg-1")
        await runtime.update_transcript("我想查天气", segment_id="seg-1")
        await runtime.update_transcript("再看看上海", segment_id="seg-2")

        active = runtime.active_input
        self.assertIsNotNone(active)
        self.assertEqual([(item.segment_id, item.text) for item in active.segments], [
            ("seg-1", "我想查天气"), ("seg-2", "再看看上海"),
        ])
        self.assertEqual(active.latest_transcript, "我想查天气\n再看看上海")

    async def test_pending_response_supersedes_running_prepare(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def held_prepare(_request):
            started.set()
            await release.wait()
            if False:
                yield {}

        provider = ScriptedProvider(held_prepare, ())
        runtime = self.runtime(provider)
        await runtime.update_transcript("first", segment_id="seg-1")
        await runtime.request_prepare("checkpoint", high_priority=True)
        await asyncio.wait_for(started.wait(), 1)
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
        await runtime.update_transcript("先说一半", segment_id="seg-1")
        await runtime.request_prepare("checkpoint", high_priority=True)
        await asyncio.wait_for(started.wait(), 1)
        await runtime.update_transcript("完整问题", segment_id="seg-1")

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
    async def test_provider_failure_reports_once_and_counts_settlement(self) -> None:
        from backend.main_agent.metrics import LifecycleMetrics

        async def broken(_request):
            raise RuntimeError("private provider detail")
            yield {}

        for script in (broken, ({"choices": "invalid"},)):
            with self.subTest(script=type(script).__name__):
                metrics = LifecycleMetrics()
                runtime = self.runtime(ScriptedProvider(script), metrics=metrics)
                errors = []

                async def error(code):
                    errors.append(code)

                runtime.error_listener = error
                await runtime.submit_text("hello")
                await runtime.wait_until_idle()
                self.assertEqual(errors, ["main_model_error"])
                self.assertEqual(metrics.snapshot()["counters"].get("main.receipt.provider_stream_error"), 1)

    async def test_rejected_task_call_only_records_backend_diagnostics(self) -> None:
        from backend.main_agent.metrics import LifecycleMetrics

        provider = ScriptedProvider((tool_delta(
            "task-1", "task_assign",
            '{"title":"demo","prompt":"demo","workspace_id":"unknown"}',
        ),))
        metrics = LifecycleMetrics()
        runtime = self.runtime(provider, metrics=metrics)
        events = []

        async def error(code):
            events.append(("error", code))

        async def state(value):
            events.append(("state", value))

        runtime.error_listener = error
        runtime.state_listener = state
        await runtime.submit_text("create a demo task")
        await runtime.wait_until_idle()
        self.assertEqual(events, [("state", "idle")])
        self.assertEqual(metrics.snapshot()["counters"].get("main.receipt.schema_invalid"), 1)
        self.assertEqual(metrics.snapshot()["counters"].get("main.invoke.silent"), 1)
        self.assertEqual(self.store.fold().task_facts, ())

    async def test_no_say_is_silent_with_or_without_ordinary_content(self) -> None:
        from backend.main_agent.metrics import LifecycleMetrics

        for script in ((), ({"choices": [{"delta": {"content": "ordinary reply"}}]},)):
            with self.subTest(has_content=bool(script)):
                metrics = LifecycleMetrics()
                provider = ScriptedProvider(script)
                runtime = self.runtime(provider, metrics=metrics)
                errors = []

                async def error(code):
                    errors.append(code)

                runtime.error_listener = error
                await runtime.submit_text("hello")
                await runtime.wait_until_idle()
                self.assertEqual(errors, [])
                self.assertEqual(self.offers, [])
                self.assertEqual(metrics.snapshot()["counters"].get("main.invoke.silent"), 1)

    async def test_effect_fence_rejects_say_after_local_speech_candidate(self) -> None:
        started = asyncio.Event()
        proceed = asyncio.Event()

        async def late_say(_request):
            started.set()
            await proceed.wait()
            yield {"choices": [{"delta": {"content": "ordinary content beside fenced speech"}}]}
            yield tool_delta("say-1", "say_to_user", '{"text":"stale"}')

        provider = ScriptedProvider(late_say)
        runtime = self.runtime(provider)
        errors = []

        async def error(code):
            errors.append(code)

        runtime.error_listener = error
        await runtime.submit_text("question")
        await asyncio.wait_for(started.wait(), 1)
        await runtime.vad_start()
        proceed.set()
        await runtime.wait_until_idle()

        self.assertEqual(self.offers, [])
        self.assertEqual(provider.requests[0].settlement.receipts[0].code, "speech_fenced")
        self.assertEqual(errors, [])

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
            tool_delta("say-1", "say_to_user", '{"text":"First sentence。second"}'),
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
            ("user", "hello"), ("assistant", "First sentence。"),
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
    def test_soft_punctuation_and_whitespace_never_cut_a_sentence(self):
        text = "Codex 更新完成，已经从 0.147.0 升级到 0.153.4：可以查看 backend、frontend——然后重启；\n接着使用"
        for whitespace in (" ", "\t"):
            value = text.replace(" ", whitespace)
            self.assertEqual(segment_say_text(value, final=False), ())
            self.assertEqual(segment_say_text(value, final=True), (value,))

    def test_long_sentences_have_no_forced_length_cut(self):
        for text in ("中" * 500, "hello " * 200, "😊" * 200):
            self.assertEqual(segment_say_text(text, final=False), ())
            self.assertEqual(segment_say_text(text, final=True), (text,))
            self.assertEqual(segment_say_text(text + "。尾段", final=True), (text + "。", "尾段"))

    def test_first_greeting_merges_and_following_sentences_accumulate_45_units(self):
        head = "你好！我来帮你检查这个项目。"
        second = "甲" * 20 + "。" + "乙" * 24 + "。" + "丙。"
        tail = "结束。"
        self.assertEqual(segment_say_text(head + second + tail, final=True), (head, second, tail))
        self.assertEqual(segment_say_text("你好！", final=True), ("你好！",))
        self.assertEqual(segment_say_text("Hello! I can help you. Short tail", final=True),
                         ("Hello! I can help you.", " Short tail"))

    def test_english_words_count_as_two_and_only_sentence_ends_release(self):
        from backend.main_agent.tool_runtime.stream import speech_units
        self.assertEqual(speech_units("Hello, world!你好。"), 6)
        self.assertEqual(speech_units("alpha-beta:gamma/delta"), 8)
        self.assertEqual(speech_units("one\ttwo\nthree"), 6)
        first = "准备开始。"
        under = "word " * 22 + "."  # 44 units: keep waiting across this sentence end.
        ready = " 中。"  # Reaches 45 units only at this complete sentence end.
        self.assertEqual(segment_say_text(first + under + ready + "尾段", final=True),
                         (first, under + ready, "尾段"))
        over = "word " * 23 + "."
        self.assertEqual(segment_say_text(first + over + "尾段", final=True), (first, over, "尾段"))

    def test_sentence_closers_domains_and_decimals_preserve_every_codepoint(self):
        text = '他说：“现在已经准备好了！”This version is 3.14 at example.com/path. 最后一句'
        clauses = segment_say_text(text, final=True)
        self.assertEqual(clauses[0], '他说：“现在已经准备好了！”')
        self.assertEqual("".join(clauses), text)
        self.assertTrue(all(clauses))
