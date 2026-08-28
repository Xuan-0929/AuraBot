"""Verify MainAgentRuntime's retained conversation-control semantics at public async boundaries.

These tests cover ActiveInput causality, trigger priority, StrongPause, grants/effect fences, detached
Observation continuation, and physical Renderer ACK. They intentionally avoid private scheduler dicts,
task names, task-factory failures, diagnostic histories, and fake Renderer compatibility branches.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from collections import deque
from pathlib import Path

from backend.main_agent import ContextStore, SnapshotProjector
from backend.main_agent.runtime import MainAgentRuntime, RendererClause
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

    def runtime(self, provider, *, executors=None, renderer=None) -> MainAgentRuntime:
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
        )
        self.runtimes.append(runtime)
        return runtime


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


if __name__ == "__main__":
    unittest.main()
