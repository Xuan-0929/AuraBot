"""Exercise MainAgentRuntime scheduling, grants, barriers, and renderer-confirmed text delivery.

The tests drive normalized transcript/VAD/EOU facts into the real runtime, capture immutable provider
requests, and use injected renderer/tool ports only at external boundaries.  They prove strict control
single-flight while detached safe observations survive, and prove that Canonical Context receives only
clauses acknowledged as completely rendered.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from collections import deque
from pathlib import Path

from backend.main_agent import ContextStore, SnapshotProjector
from backend.main_agent.runtime import (
    MainAgentRuntime,
    PrepareAdmissionPolicy,
    RendererClause,
)
from backend.main_agent.tool_runtime import InvokeMode, ToolSurfacePolicy
from backend.main_agent.tool_runtime.stream import segment_say_text


def tool_delta(index: int, call_id: str, name: str, arguments: str) -> dict:
    return {
        "choices": [{"delta": {"tool_calls": [{
            "index": index,
            "id": call_id,
            "function": {"name": name, "arguments": arguments},
        }]}}]
    }


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class ScriptedProvider:
    def __init__(self, *scripts) -> None:
        self.scripts = deque(scripts)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        script = self.scripts.popleft() if self.scripts else []
        return script(request) if callable(script) else script


class RuntimeTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.store = ContextStore(Path(self.directory.name) / "main-agent.sqlite3")
        self.offers: list[RendererClause] = []

        async def renderer(clause: RendererClause):
            self.offers.append(clause)

        self.renderer = renderer
        self.runtimes: list[MainAgentRuntime] = []

    async def asyncTearDown(self) -> None:
        for runtime in self.runtimes:
            await runtime.shutdown()
        self.store.close()
        self.directory.cleanup()

    def runtime(
        self,
        provider,
        *,
        renderer=None,
        executors=None,
        clock=None,
        admission=None,
    ) -> MainAgentRuntime:
        runtime = MainAgentRuntime(
            context_store=self.store,
            snapshot_projector=SnapshotProjector(),
            tool_policy=ToolSurfacePolicy(
                workspace_ids=("repo",),
                observation_tools=("web_search", "web_read"),
            ),
            provider=provider,
            renderer=renderer or self.renderer,
            executors=executors or {},
            soul="test soul",
            clock=clock,
            prepare_admission=admission,
        )
        self.runtimes.append(runtime)
        return runtime


class TriggerAndGrantTests(RuntimeTestCase):
    async def test_invoke_diagnostics_keep_only_a_bounded_recent_history(self) -> None:
        runtime = self.runtime(ScriptedProvider())

        for index in range(140):
            await runtime.submit_text(f"input {index}")
            await runtime.wait_until_idle()

        self.assertLessEqual(len(runtime.invoke_requests), 128)
        self.assertEqual(
            runtime.invoke_requests[-1].snapshot.conversation_turns[-1]["content"],
            "input 139",
        )

    async def test_confirmed_barge_in_revokes_grant_and_cancels_control_but_noise_does_not(self) -> None:
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

        provider = ScriptedProvider(held_response)
        runtime = self.runtime(provider)
        grant = await runtime.submit_text("先回答")
        await asyncio.wait_for(started.wait(), 1)

        await runtime.vad_start()
        await runtime.reject_speech_candidate()
        await asyncio.sleep(0)
        self.assertFalse(cancelled.is_set())
        self.assertEqual(runtime.current_response_grant, grant)

        await runtime.vad_start()
        await runtime.confirm_speech()
        await asyncio.wait_for(cancelled.wait(), 1)
        await runtime.wait_until_idle()

        self.assertIsNone(runtime.current_response_grant)
        self.assertTrue(provider.requests[0].settlement.cancelled)

    async def test_raw_vad_stop_never_grants_or_schedules_a_response(self) -> None:
        provider = ScriptedProvider()
        runtime = self.runtime(provider)

        await runtime.update_transcript("还没说完", request_prepare=False)
        await runtime.vad_stop_candidate()
        await runtime.wait_until_idle()

        self.assertIsNone(runtime.current_response_grant)
        self.assertEqual(provider.requests, [])

    async def test_eou_incomplete_requests_prepare_without_creating_a_grant(self) -> None:
        provider = ScriptedProvider([])
        runtime = self.runtime(provider)
        await runtime.update_transcript("我想查一下", request_prepare=False)

        admitted = await runtime.eou_incomplete()
        await runtime.wait_until_idle()

        self.assertTrue(admitted)
        self.assertIsNone(runtime.current_response_grant)
        self.assertEqual([item.mode for item in provider.requests], [InvokeMode.PREPARE])
        self.assertNotIn("say_to_user", provider.requests[0].tool_surface.names)

    async def test_eou_incomplete_revokes_a_prior_grant_even_without_new_stable_text(self) -> None:
        provider = ScriptedProvider([], [])
        runtime = self.runtime(provider)
        await runtime.submit_text("先前问题")
        await runtime.wait_until_idle()
        self.assertIsNotNone(runtime.current_response_grant)

        await runtime.vad_start()
        self.assertTrue(await runtime.eou_incomplete())
        await runtime.wait_until_idle()

        self.assertIsNone(runtime.current_response_grant)
        self.assertEqual(provider.requests[1].mode, InvokeMode.PREPARE)

    async def test_eou_incomplete_settles_active_response_and_discards_response_pending_work(self) -> None:
        response_started = asyncio.Event()
        cancelling = asyncio.Event()
        release_cancel = asyncio.Event()

        async def blocked_response(_request):
            response_started.set()
            try:
                await asyncio.Event().wait()
                if False:
                    yield {}
            finally:
                cancelling.set()
                await release_cancel.wait()

        provider = ScriptedProvider(blocked_response, [])
        runtime = self.runtime(provider)
        await runtime.submit_text("先回答")
        await asyncio.wait_for(response_started.wait(), 1)
        await runtime.request_continue("stale-response-work")

        incomplete = asyncio.create_task(runtime.eou_incomplete())
        try:
            await asyncio.sleep(0)
            self.assertFalse(incomplete.done())
            await asyncio.wait_for(cancelling.wait(), 1)
        finally:
            release_cancel.set()
        self.assertTrue(await asyncio.wait_for(incomplete, 1))
        await runtime.wait_until_idle()

        self.assertIsNone(runtime.current_response_grant)
        self.assertEqual([request.mode for request in provider.requests], [
            InvokeMode.RESPOND, InvokeMode.PREPARE,
        ])

    async def test_cancelled_eou_incomplete_releases_its_barrier_and_later_prepare_drains(self) -> None:
        response_started = asyncio.Event()
        cancellation_entered = asyncio.Event()
        release_cancellation = asyncio.Event()

        async def held_response(_request):
            response_started.set()
            try:
                await asyncio.Event().wait()
                if False:
                    yield {}
            finally:
                cancellation_entered.set()
                await release_cancellation.wait()

        provider = ScriptedProvider(held_response, [])
        runtime = self.runtime(provider)
        await runtime.submit_text("先回答")
        await asyncio.wait_for(response_started.wait(), 1)

        incomplete = asyncio.create_task(runtime.eou_incomplete())
        await asyncio.wait_for(cancellation_entered.wait(), 1)
        try:
            incomplete.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await incomplete
            self.assertIsNone(runtime._barrier_intent)
            self.assertTrue(await runtime.request_prepare("later", high_priority=True))
        finally:
            release_cancellation.set()
        await runtime.wait_until_idle()

        self.assertEqual([request.mode for request in provider.requests], [
            InvokeMode.RESPOND, InvokeMode.PREPARE,
        ])

    async def test_cancelled_older_eou_incomplete_never_clears_newer_barrier_intent(self) -> None:
        response_started = asyncio.Event()
        cancellation_entered = asyncio.Event()
        release_cancellation = asyncio.Event()

        async def held_response(_request):
            response_started.set()
            try:
                await asyncio.Event().wait()
                if False:
                    yield {}
            finally:
                cancellation_entered.set()
                await release_cancellation.wait()

        provider = ScriptedProvider(held_response, [])
        runtime = self.runtime(provider)
        await runtime.submit_text("先回答")
        await asyncio.wait_for(response_started.wait(), 1)

        older = asyncio.create_task(runtime.eou_incomplete())
        await asyncio.wait_for(cancellation_entered.wait(), 1)
        newer = asyncio.create_task(runtime.eou_incomplete())
        await asyncio.sleep(0)
        newer_intent = runtime._barrier_intent
        self.assertIsNotNone(newer_intent)

        try:
            older.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await older
            self.assertIs(runtime._barrier_intent, newer_intent)
        finally:
            release_cancellation.set()
        self.assertTrue(await asyncio.wait_for(newer, 1))
        await runtime.wait_until_idle()
        self.assertEqual([request.mode for request in provider.requests], [
            InvokeMode.RESPOND, InvokeMode.PREPARE,
        ])

    async def test_eou_incomplete_contains_deferred_drain_factory_failure_and_recovers(self) -> None:
        provider = ScriptedProvider([])
        runtime = self.runtime(provider)
        await runtime.update_transcript("未说完", request_prepare=False)
        loop = asyncio.get_running_loop()
        previous_factory = loop.get_task_factory()
        previous_handler = loop.get_exception_handler()
        endpoint_ref = {}
        endpoint_done_at_factory = []
        loop_errors = []
        failed = False

        def failing_drain_factory(loop, coroutine, context=None):
            nonlocal failed
            code = getattr(coroutine, "cr_code", None)
            if getattr(code, "co_qualname", "").endswith("._drain") and not failed:
                failed = True
                endpoint_done_at_factory.append(endpoint_ref["task"].done())
                coroutine.close()
                raise RuntimeError("injected drain factory failure")
            if previous_factory is not None:
                return previous_factory(loop, coroutine, context=context)
            return asyncio.Task(coroutine, loop=loop, context=context)

        loop.set_task_factory(failing_drain_factory)
        loop.set_exception_handler(lambda _loop, context: loop_errors.append(context))

        error = None
        admitted = None
        try:
            try:
                endpoint = asyncio.create_task(runtime.eou_incomplete())
                endpoint_ref["task"] = endpoint
                admitted = await endpoint
                await asyncio.sleep(0)
            except BaseException as exc:
                error = exc
        finally:
            loop.set_task_factory(previous_factory)
            loop.set_exception_handler(previous_handler)
        await runtime.wait_until_idle()

        self.assertIsNone(error)
        self.assertTrue(admitted)
        self.assertEqual(endpoint_done_at_factory, [True])
        self.assertEqual(loop_errors, [])
        self.assertIsNone(runtime.pending_trigger)
        self.assertIsNone(runtime._barrier_intent)
        self.assertEqual(
            [request.mode for request in provider.requests], [InvokeMode.PREPARE]
        )

    async def test_eou_complete_and_bounded_fallback_each_create_current_response_grants(self) -> None:
        provider = ScriptedProvider([], [])
        runtime = self.runtime(provider)
        await runtime.update_transcript("完整问题", request_prepare=False)

        complete_grant = await runtime.eou_complete()
        await runtime.wait_until_idle()
        await runtime.update_transcript("完整问题，再补充", request_prepare=False)
        fallback_grant = await runtime.endpoint_fallback()
        await runtime.wait_until_idle()

        self.assertNotEqual(complete_grant.grant_id, fallback_grant.grant_id)
        self.assertEqual(fallback_grant.input_id, runtime.active_input.input_id)
        self.assertEqual(fallback_grant.revision, runtime.active_input.revision)
        self.assertEqual(fallback_grant.context_version, runtime.latest_context_version)
        self.assertEqual([item.mode for item in provider.requests], [
            InvokeMode.RESPOND, InvokeMode.RESPOND,
        ])

    async def test_explicit_text_submit_creates_a_grant_and_respond_snapshot(self) -> None:
        provider = ScriptedProvider([])
        runtime = self.runtime(provider)

        grant = await runtime.submit_text("文字问题")
        await runtime.wait_until_idle()

        request = provider.requests[0]
        self.assertEqual(request.mode, InvokeMode.RESPOND)
        self.assertEqual(request.snapshot.transcript, "文字问题")
        self.assertEqual(request.response_grant, grant)
        self.assertEqual(request.tool_surface.response_grant, grant)
        self.assertIn("say_to_user", request.tool_surface.names)
        self.assertEqual(
            {item["function"]["name"] for item in request.snapshot.allowed_tool_surface},
            request.tool_surface.names,
        )

    async def test_stt_segments_replace_one_utterance_and_append_the_next(self) -> None:
        provider = ScriptedProvider([])
        runtime = self.runtime(provider)

        await runtime.update_transcript(
            "第一版", segment_id="utterance-1", request_prepare=False
        )
        await runtime.update_transcript(
            "第一句话", segment_id="utterance-1", request_prepare=False
        )
        await runtime.update_transcript(
            "再补充一句", segment_id="utterance-2", request_prepare=False
        )

        self.assertEqual(
            [(item.segment_id, item.text) for item in runtime.active_input.segments],
            [("utterance-1", "第一句话"), ("utterance-2", "再补充一句")],
        )
        self.assertEqual(runtime.active_input.latest_transcript, "第一句话\n再补充一句")
        self.assertEqual(
            [item["content"] for item in self.store.fold().conversation_turns],
            ["第一句话", "再补充一句"],
        )

    async def test_revision_advance_revokes_the_old_grant_and_interrupts_unheard_clauses(self) -> None:
        offered = asyncio.Event()

        async def response(_request):
            yield tool_delta(0, "say", "say_to_user", '{"text":"旧回答。"}')
            offered.set()
            await asyncio.Event().wait()

        provider = ScriptedProvider(response)
        runtime = self.runtime(provider)
        await runtime.submit_text("第一版")
        await asyncio.wait_for(offered.wait(), 1)
        for _ in range(10):
            if self.offers:
                break
            await asyncio.sleep(0)

        old_revision = runtime.active_input.revision
        await runtime.update_transcript("第一版，补充", request_prepare=False)
        await runtime.wait_until_idle()

        self.assertGreater(runtime.active_input.revision, old_revision)
        self.assertIsNone(runtime.current_response_grant)
        self.assertFalse(await runtime.renderer_ack(self.offers[0].clause_id, complete=True))
        self.assertEqual(self.store.fold().delivered_clauses, ())

    async def test_an_accepted_task_commit_starts_the_next_active_input(self) -> None:
        provider = ScriptedProvider([])
        runtime = self.runtime(provider)
        await runtime.update_transcript("task request", request_prepare=False)
        first_input_id = runtime.active_input.input_id

        await runtime.mark_external_commit()
        await runtime.update_transcript("next input", request_prepare=False)

        self.assertNotEqual(runtime.active_input.input_id, first_input_id)
        self.assertEqual(runtime.active_input.revision, 1)


class AdmissionAndCoalescingTests(RuntimeTestCase):
    async def test_prepare_requires_minimum_growth_and_interval(self) -> None:
        clock = FakeClock()
        provider = ScriptedProvider([], [])
        runtime = self.runtime(
            provider,
            clock=clock,
            admission=PrepareAdmissionPolicy(
                min_interval_seconds=10,
                min_growth_units=4,
                max_wait_seconds=30,
                punctuation_growth_units=2,
                max_prepares_per_input=2,
            ),
        )

        self.assertFalse(await runtime.update_transcript("甲乙丙", request_prepare=True))
        self.assertTrue(await runtime.update_transcript("甲乙丙丁", request_prepare=True))
        await runtime.wait_until_idle()
        clock.advance(5)
        self.assertFalse(await runtime.update_transcript("甲乙丙丁戊己庚辛", request_prepare=True))
        clock.advance(5)
        self.assertTrue(await runtime.update_transcript("甲乙丙丁戊己庚辛壬", request_prepare=True))
        await runtime.wait_until_idle()

        self.assertEqual([item.mode for item in provider.requests], [
            InvokeMode.PREPARE, InvokeMode.PREPARE,
        ])

    async def test_prepare_punctuation_reward_and_max_wait_prevent_starvation(self) -> None:
        punctuation_clock = FakeClock()
        punctuation_provider = ScriptedProvider([])
        punctuation_runtime = self.runtime(
            punctuation_provider,
            clock=punctuation_clock,
            admission=PrepareAdmissionPolicy(
                min_interval_seconds=0,
                min_growth_units=20,
                max_wait_seconds=30,
                punctuation_growth_units=3,
                max_prepares_per_input=2,
            ),
        )
        self.assertFalse(await punctuation_runtime.update_transcript("甲乙", request_prepare=True))
        self.assertTrue(await punctuation_runtime.update_transcript("甲乙丙。", request_prepare=True))
        await punctuation_runtime.wait_until_idle()

        wait_store = ContextStore(Path(self.directory.name) / "wait.sqlite3")
        wait_clock = FakeClock()
        wait_provider = ScriptedProvider([])
        wait_runtime = MainAgentRuntime(
            context_store=wait_store,
            snapshot_projector=SnapshotProjector(),
            tool_policy=ToolSurfacePolicy(),
            provider=wait_provider,
            renderer=self.renderer,
            executors={},
            soul="test soul",
            clock=wait_clock,
            prepare_admission=PrepareAdmissionPolicy(
                min_interval_seconds=1,
                min_growth_units=100,
                max_wait_seconds=5,
                punctuation_growth_units=100,
                max_prepares_per_input=2,
            ),
        )
        self.runtimes.append(wait_runtime)
        self.addAsyncCleanup(asyncio.to_thread, wait_store.close)
        self.assertFalse(await wait_runtime.update_transcript("a", request_prepare=True))
        wait_clock.advance(5)
        self.assertTrue(await wait_runtime.update_transcript("ab", request_prepare=True))
        await wait_runtime.wait_until_idle()

        self.assertEqual(len(punctuation_provider.requests), 1)
        self.assertEqual(len(wait_provider.requests), 1)

    async def test_eou_incomplete_bypasses_growth_and_interval_but_not_input_budget(self) -> None:
        clock = FakeClock()
        provider = ScriptedProvider([], [])
        runtime = self.runtime(
            provider,
            clock=clock,
            admission=PrepareAdmissionPolicy(
                min_interval_seconds=100,
                min_growth_units=100,
                max_wait_seconds=100,
                punctuation_growth_units=100,
                max_prepares_per_input=2,
            ),
        )
        await runtime.update_transcript("a", request_prepare=False)

        self.assertTrue(await runtime.eou_incomplete())
        await runtime.wait_until_idle()
        self.assertTrue(await runtime.eou_incomplete())
        await runtime.wait_until_idle()
        self.assertFalse(await runtime.eou_incomplete())

        self.assertEqual(len(provider.requests), 2)

    async def test_pending_triggers_coalesce_to_respond_over_continue_over_prepare(self) -> None:
        prepare_started = asyncio.Event()

        async def blocked_prepare(_request):
            prepare_started.set()
            await asyncio.Event().wait()
            if False:
                yield {}

        provider = ScriptedProvider(blocked_prepare, [])
        runtime = self.runtime(provider)
        await runtime.update_transcript("abcd", request_prepare=False)
        self.assertTrue(await runtime.request_prepare("checkpoint", high_priority=True))
        await asyncio.wait_for(prepare_started.wait(), 1)

        await runtime.request_prepare("newer-checkpoint", high_priority=True)
        await runtime.request_continue("observation-completed")
        self.assertEqual(runtime.pending_trigger.mode, InvokeMode.CONTINUE)
        await runtime.eou_complete()
        await runtime.wait_until_idle()

        self.assertEqual([item.mode for item in provider.requests], [
            InvokeMode.PREPARE, InvokeMode.RESPOND,
        ])
        self.assertIsNone(runtime.pending_trigger)


class SchedulingAndObservationTests(RuntimeTestCase):
    async def test_strong_pause_settles_prepare_before_freezing_latest_respond_snapshot(self) -> None:
        prepare_started = asyncio.Event()
        prepare_closed = asyncio.Event()
        ordering: list[str] = []

        async def prepare(_request):
            try:
                prepare_started.set()
                await asyncio.Event().wait()
                if False:
                    yield {}
            finally:
                ordering.append("prepare-settled")
                prepare_closed.set()

        def respond(request):
            self.assertTrue(prepare_closed.is_set())
            ordering.append("respond-frozen")
            self.assertEqual(request.snapshot.transcript, "最新文本")
            return []

        provider = ScriptedProvider(prepare, respond)
        runtime = self.runtime(provider)
        await runtime.update_transcript("初始文本", request_prepare=False)
        await runtime.request_prepare("checkpoint", high_priority=True)
        await asyncio.wait_for(prepare_started.wait(), 1)
        await runtime.update_transcript("最新文本", request_prepare=False)

        await runtime.eou_complete()
        await runtime.wait_until_idle()

        self.assertEqual(ordering, ["prepare-settled", "respond-frozen"])
        self.assertTrue(provider.requests[0].settlement.cancelled)

    async def test_strong_pause_finalizes_grant_after_held_prepare_settlement_using_latest_revision(self) -> None:
        prepare_started = asyncio.Event()
        cancellation_entered = asyncio.Event()
        release_cancellation = asyncio.Event()

        async def held_prepare(_request):
            prepare_started.set()
            try:
                await asyncio.Event().wait()
                if False:
                    yield {}
            finally:
                cancellation_entered.set()
                await release_cancellation.wait()

        provider = ScriptedProvider(held_prepare, [])
        runtime = self.runtime(provider)
        await runtime.update_transcript("较早文本", request_prepare=False)
        await runtime.request_prepare("checkpoint", high_priority=True)
        await asyncio.wait_for(prepare_started.wait(), 1)

        pause = asyncio.create_task(runtime.eou_complete())
        await asyncio.wait_for(cancellation_entered.wait(), 1)
        await runtime.update_transcript("取消期间到达的最新文本", request_prepare=False)
        release_cancellation.set()
        grant = await asyncio.wait_for(pause, 1)
        await runtime.wait_until_idle()

        self.assertEqual(len(provider.requests), 2)
        respond = provider.requests[1]
        self.assertEqual([request.mode for request in provider.requests], [
            InvokeMode.PREPARE, InvokeMode.RESPOND,
        ])
        self.assertEqual(respond.snapshot.transcript, "取消期间到达的最新文本")
        self.assertEqual(grant, runtime.current_response_grant)
        self.assertEqual(grant.revision, runtime.active_input.revision)
        self.assertEqual(grant.context_version, runtime.latest_context_version)

    async def test_cancelled_strong_pause_releases_its_barrier_and_later_prepare_drains(self) -> None:
        prepare_started = asyncio.Event()
        cancellation_entered = asyncio.Event()
        release_cancellation = asyncio.Event()

        async def held_prepare(_request):
            prepare_started.set()
            try:
                await asyncio.Event().wait()
                if False:
                    yield {}
            finally:
                cancellation_entered.set()
                await release_cancellation.wait()

        provider = ScriptedProvider(held_prepare, [])
        runtime = self.runtime(provider)
        await runtime.update_transcript("问题", request_prepare=False)
        await runtime.request_prepare("checkpoint", high_priority=True)
        await asyncio.wait_for(prepare_started.wait(), 1)

        pause = asyncio.create_task(runtime.eou_complete())
        await asyncio.wait_for(cancellation_entered.wait(), 1)
        try:
            pause.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pause
            self.assertIsNone(runtime._barrier_intent)
            self.assertTrue(await runtime.request_prepare("later", high_priority=True))
        finally:
            release_cancellation.set()
        await runtime.wait_until_idle()

        self.assertEqual([request.mode for request in provider.requests], [
            InvokeMode.PREPARE, InvokeMode.PREPARE,
        ])

    async def test_cancelled_older_strong_pause_never_clears_newer_barrier_intent(self) -> None:
        prepare_started = asyncio.Event()
        cancellation_entered = asyncio.Event()
        release_cancellation = asyncio.Event()

        async def held_prepare(_request):
            prepare_started.set()
            try:
                await asyncio.Event().wait()
                if False:
                    yield {}
            finally:
                cancellation_entered.set()
                await release_cancellation.wait()

        provider = ScriptedProvider(held_prepare, [])
        runtime = self.runtime(provider)
        await runtime.update_transcript("问题", request_prepare=False)
        await runtime.request_prepare("checkpoint", high_priority=True)
        await asyncio.wait_for(prepare_started.wait(), 1)

        older = asyncio.create_task(runtime.eou_complete())
        await asyncio.wait_for(cancellation_entered.wait(), 1)
        newer = asyncio.create_task(runtime.endpoint_fallback())
        await asyncio.sleep(0)
        newer_intent = runtime._barrier_intent
        self.assertIsNotNone(newer_intent)

        try:
            older.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await older
            self.assertIs(runtime._barrier_intent, newer_intent)
        finally:
            release_cancellation.set()
        grant = await asyncio.wait_for(newer, 1)
        await runtime.wait_until_idle()
        self.assertEqual(grant, runtime.current_response_grant)
        self.assertEqual([request.mode for request in provider.requests], [
            InvokeMode.PREPARE, InvokeMode.RESPOND,
        ])

    async def test_strong_pause_returns_before_drain_factory_can_cancel_it(self) -> None:
        provider = ScriptedProvider([])
        runtime = self.runtime(provider)
        await runtime.update_transcript("完整问题", request_prepare=False)
        loop = asyncio.get_running_loop()
        previous_factory = loop.get_task_factory()
        endpoint_ref = {}
        endpoint_done_at_factory = []

        def cancelling_drain_factory(loop, coroutine, context=None):
            if previous_factory is not None:
                task = previous_factory(loop, coroutine, context=context)
            else:
                task = asyncio.Task(coroutine, loop=loop, context=context)
            code = getattr(coroutine, "cr_code", None)
            if getattr(code, "co_qualname", "").endswith("._drain"):
                endpoint = endpoint_ref["task"]
                endpoint_done_at_factory.append(endpoint.done())
                endpoint.cancel()
            return task

        loop.set_task_factory(cancelling_drain_factory)

        cancelled = False
        grant = None
        try:
            try:
                endpoint = asyncio.create_task(runtime.eou_complete())
                endpoint_ref["task"] = endpoint
                grant = await endpoint
            except asyncio.CancelledError:
                cancelled = True
        finally:
            loop.set_task_factory(previous_factory)
        await runtime.wait_until_idle()

        self.assertFalse(cancelled)
        self.assertEqual(endpoint_done_at_factory, [True])
        self.assertEqual(grant, runtime.current_response_grant)
        self.assertIsNone(runtime._barrier_intent)
        self.assertEqual(
            [request.mode for request in provider.requests], [InvokeMode.RESPOND]
        )

    async def test_one_idle_wait_retries_a_drain_cancelled_by_its_task_factory(self) -> None:
        provider = ScriptedProvider([])
        runtime = self.runtime(provider)
        await runtime.update_transcript("checkpoint", request_prepare=False)
        loop = asyncio.get_running_loop()
        previous_factory = loop.get_task_factory()
        previous_handler = loop.get_exception_handler()
        loop_errors = []
        drain_attempts = 0

        def cancel_once_drain_factory(loop, coroutine, context=None):
            nonlocal drain_attempts
            if previous_factory is not None:
                task = previous_factory(loop, coroutine, context=context)
            else:
                task = asyncio.Task(coroutine, loop=loop, context=context)
            code = getattr(coroutine, "cr_code", None)
            if getattr(code, "co_qualname", "").endswith("._drain"):
                drain_attempts += 1
                if drain_attempts == 1:
                    task.cancel()
            return task

        loop.set_task_factory(cancel_once_drain_factory)
        loop.set_exception_handler(lambda _loop, context: loop_errors.append(context))

        error = None
        try:
            self.assertTrue(await runtime.request_prepare("checkpoint", high_priority=True))
            try:
                await runtime.wait_until_idle()
            except BaseException as exc:
                error = exc
        finally:
            loop.set_task_factory(previous_factory)
            loop.set_exception_handler(previous_handler)

        self.assertIsNone(error)
        self.assertEqual(drain_attempts, 2)
        self.assertEqual([request.mode for request in provider.requests], [InvokeMode.PREPARE])
        self.assertIsNone(runtime.pending_trigger)
        self.assertIsNone(runtime._drain_task)
        self.assertFalse(runtime._drain_scheduled)
        self.assertEqual(loop_errors, [])

    async def test_cancelling_idle_waiter_does_not_cancel_its_internal_drain(self) -> None:
        provider_started = asyncio.Event()
        provider_release = asyncio.Event()

        async def held_response(_request):
            provider_started.set()
            await provider_release.wait()
            if False:
                yield {}

        provider = ScriptedProvider(held_response)
        runtime = self.runtime(provider)
        await runtime.submit_text("question")

        waiter = asyncio.create_task(runtime.wait_until_idle())
        await asyncio.wait_for(provider_started.wait(), 1)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter

        self.assertIsNotNone(runtime._drain_task)
        self.assertFalse(runtime._drain_task.cancelled())
        provider_release.set()
        await runtime.wait_until_idle()

        self.assertEqual([request.mode for request in provider.requests], [InvokeMode.RESPOND])
        self.assertIsNone(runtime.pending_trigger)
        self.assertIsNone(runtime._drain_task)

    async def test_control_single_flight_extends_through_renderer_executor_settlement(self) -> None:
        renderer_entered = asyncio.Event()
        renderer_release = asyncio.Event()

        async def blocking_renderer(clause):
            self.offers.append(clause)
            renderer_entered.set()
            await renderer_release.wait()

        provider = ScriptedProvider(
            [tool_delta(0, "say", "say_to_user", '{"text":"第一句。"}')],
            [],
        )
        runtime = self.runtime(provider, renderer=blocking_renderer)
        await runtime.submit_text("问题")
        await asyncio.wait_for(renderer_entered.wait(), 1)

        await runtime.request_continue("queued")
        await asyncio.sleep(0)
        self.assertEqual(len(provider.requests), 1)
        renderer_release.set()
        await runtime.wait_until_idle()

        self.assertEqual([request.mode for request in provider.requests], [
            InvokeMode.RESPOND, InvokeMode.CONTINUE,
        ])

    async def test_detached_observation_survives_and_requests_continue_with_latest_context(self) -> None:
        observation_started = asyncio.Event()
        observation_release = asyncio.Event()

        async def search(_call):
            observation_started.set()
            await observation_release.wait()
            return {"answer": "safe fact"}

        provider = ScriptedProvider(
            [tool_delta(0, "search", "web_search", '{"query":"x"}')],
            [],
            [],
        )
        runtime = self.runtime(provider, executors={"web_search": search})
        await runtime.update_transcript("查一下", request_prepare=False)
        await runtime.request_prepare("checkpoint", high_priority=True)
        await runtime.wait_until_idle()
        await asyncio.wait_for(observation_started.wait(), 1)

        grant = await runtime.eou_complete()
        await runtime.wait_until_idle()
        respond = provider.requests[1]
        self.assertEqual(respond.response_grant.grant_id, grant.grant_id)
        self.assertEqual(respond.snapshot.observations[0]["status"], "running")

        observation_release.set()
        await runtime.harness.wait_for_observations()
        await runtime.wait_until_idle()
        continued = provider.requests[2]
        self.assertEqual(continued.mode, InvokeMode.CONTINUE)
        self.assertEqual(continued.snapshot.observations[0]["status"], "completed")
        self.assertEqual(continued.snapshot.observations[0]["result"], {"answer": "safe fact"})

    async def test_observation_origin_is_frozen_at_detach_before_delayed_executor_start(self) -> None:
        observation_gate = asyncio.Event()

        async def search(_call):
            return {"answer": "late fact"}

        provider = ScriptedProvider(
            [tool_delta(0, "search", "web_search", '{"query":"x"}')],
            [],
        )
        runtime = self.runtime(provider, executors={"web_search": search})
        await runtime.update_transcript("原始版本", request_prepare=False)
        origin_revision = runtime.active_input.revision

        loop = asyncio.get_running_loop()
        previous_factory = loop.get_task_factory()

        def delayed_observation_factory(loop, coroutine, context=None):
            code = getattr(coroutine, "cr_code", None)
            qualified_name = getattr(code, "co_qualname", "")
            if "ToolHarness._detach_observation.<locals>.execute" in qualified_name:
                original = coroutine

                async def delayed():
                    await observation_gate.wait()
                    return await original

                coroutine = delayed()
            if previous_factory is not None:
                return previous_factory(loop, coroutine, context=context)
            return asyncio.Task(coroutine, loop=loop, context=context)

        loop.set_task_factory(delayed_observation_factory)
        try:
            await runtime.request_prepare("checkpoint", high_priority=True)
            await runtime.wait_until_idle()
        finally:
            loop.set_task_factory(previous_factory)

        await runtime.update_transcript("后续版本", request_prepare=False)
        observation_gate.set()
        await runtime.harness.wait_for_observations()
        await runtime.wait_until_idle()

        observation = self.store.fold().observations[0]
        self.assertEqual(observation["status"], "completed")
        self.assertEqual(observation["origin_revision"], origin_revision)
        self.assertEqual(observation["origin_input_id"], runtime.active_input.input_id)
        self.assertEqual(runtime._observation_origins, {})

    async def test_reused_observation_call_id_keeps_each_detach_origin_when_finishing_out_of_order(self) -> None:
        releases = {"first": asyncio.Event(), "second": asyncio.Event()}

        async def search(call):
            label = str(call.arguments["query"])
            await releases[label].wait()
            return {"label": label}

        provider = ScriptedProvider(
            [tool_delta(0, "reused", "web_search", '{"query":"first"}')],
            [tool_delta(0, "reused", "web_search", '{"query":"second"}')],
            [],
            [],
        )
        runtime = self.runtime(provider, executors={"web_search": search})
        await runtime.update_transcript("第一版", request_prepare=False)
        first_revision = runtime.active_input.revision
        await runtime.request_prepare("first", high_priority=True)
        await runtime.wait_until_idle()

        await runtime.update_transcript("第二版", request_prepare=False)
        second_revision = runtime.active_input.revision
        await runtime.request_prepare("second", high_priority=True)
        await runtime.wait_until_idle()

        releases["second"].set()
        try:
            async with asyncio.timeout(1):
                while not any(
                    item.get("result") == {"label": "second"}
                    for item in self.store.fold().observations
                ):
                    await asyncio.sleep(0)
            intermediate = self.store.fold().observations
            self.assertEqual(len(intermediate), 2)
            self.assertEqual(
                {item["status"] for item in intermediate}, {"running", "completed"}
            )
        finally:
            releases["first"].set()
        await runtime.harness.wait_for_observations()
        await runtime.wait_until_idle()

        observations = self.store.fold().observations
        self.assertEqual(len(observations), 2)
        self.assertEqual({item["call_id"] for item in observations}, {"reused"})
        by_label = {item["result"]["label"]: item for item in observations}
        self.assertEqual(by_label["first"]["origin_revision"], first_revision)
        self.assertEqual(by_label["second"]["origin_revision"], second_revision)
        self.assertNotIn("unknown", {item["origin_input_id"] for item in observations})
        self.assertEqual(runtime._observation_origins, {})

    async def test_continue_uses_a_current_grant_but_never_creates_one(self) -> None:
        without_provider = ScriptedProvider([])
        without = self.runtime(without_provider)
        await without.update_transcript("ungranted", request_prepare=False)
        await without.request_continue("fact")
        await without.wait_until_idle()
        self.assertIsNone(without.current_response_grant)
        self.assertNotIn("say_to_user", without_provider.requests[0].tool_surface.names)
        self.assertNotIn("task_assign", without_provider.requests[0].tool_surface.names)

        granted_store = ContextStore(Path(self.directory.name) / "granted.sqlite3")
        granted_provider = ScriptedProvider([], [])
        granted = MainAgentRuntime(
            context_store=granted_store,
            snapshot_projector=SnapshotProjector(),
            tool_policy=ToolSurfacePolicy(workspace_ids=("repo",)),
            provider=granted_provider,
            renderer=self.renderer,
            executors={},
            soul="test soul",
        )
        self.runtimes.append(granted)
        self.addAsyncCleanup(asyncio.to_thread, granted_store.close)
        grant = await granted.submit_text("granted")
        await granted.wait_until_idle()
        await granted.request_continue("fact")
        await granted.wait_until_idle()

        continuation = granted_provider.requests[1]
        self.assertEqual(continuation.response_grant.grant_id, grant.grant_id)
        self.assertEqual(
            continuation.response_grant.context_version,
            continuation.context_version,
        )
        self.assertIn("say_to_user", continuation.tool_surface.names)
        self.assertIn("task_assign", continuation.tool_surface.names)


class SilentDeliveryTests(RuntimeTestCase):
    async def test_ack_aware_renderer_serializes_clauses_until_runtime_commits_each_ack(self) -> None:
        class AckAwareRenderer:
            def __init__(self) -> None:
                self.offers: list[RendererClause] = []
                self.waiters = {}
                self.changed = asyncio.Event()

            async def __call__(self, clause):
                self.offers.append(clause)
                waiter = asyncio.get_running_loop().create_future()
                self.waiters[clause.clause_id] = waiter
                self.changed.set()
                return await waiter

            def acknowledge(self, clause_id, *, complete):
                waiter = self.waiters.get(clause_id)
                if waiter is None or waiter.done():
                    return False
                waiter.set_result(complete)
                return True

        renderer = AckAwareRenderer()
        provider = ScriptedProvider([
            tool_delta(0, "say", "say_to_user", '{"text":"第一句。第二句。"}')
        ])
        runtime = self.runtime(provider, renderer=renderer)
        await runtime.submit_text("问题")
        await asyncio.wait_for(renderer.changed.wait(), 1)
        first = renderer.offers[0]
        renderer.changed.clear()

        self.assertTrue(await runtime.renderer_ack(first.clause_id, complete=True))
        await asyncio.wait_for(renderer.changed.wait(), 1)
        self.assertEqual([offer.text for offer in renderer.offers], ["第一句。", "第二句。"])
        self.assertTrue(await runtime.renderer_ack(renderer.offers[1].clause_id, complete=True))
        await runtime.wait_until_idle()

        self.assertEqual(self.store.fold().delivered_clauses, ("第一句。", "第二句。"))

    async def test_only_complete_renderer_ack_commits_offered_clauses(self) -> None:
        provider = ScriptedProvider([
            tool_delta(0, "say", "say_to_user", '{"text":"第一句。未完成尾句"}')
        ])
        runtime = self.runtime(provider)
        await runtime.submit_text("问题")
        await runtime.wait_until_idle()

        self.assertEqual([offer.text for offer in self.offers], ["第一句。", "未完成尾句"])
        self.assertEqual(self.store.fold().delivered_clauses, ())
        self.assertTrue(await runtime.renderer_ack(self.offers[0].clause_id, complete=True))
        self.assertFalse(await runtime.renderer_ack(self.offers[1].clause_id, complete=False))
        self.assertEqual(self.store.fold().delivered_clauses, ("第一句。",))

    async def test_ordinary_final_text_and_silent_settlement_do_not_synthesize_fallback(self) -> None:
        provider = ScriptedProvider(
            [{"choices": [{"delta": {"content": "hidden final text"}}]}],
            [],
        )
        runtime = self.runtime(provider)
        await runtime.submit_text("one")
        await runtime.wait_until_idle()
        await runtime.request_continue("silent")
        await runtime.wait_until_idle()

        self.assertEqual(self.offers, [])
        self.assertEqual(self.store.fold().delivered_clauses, ())
        self.assertTrue(all(request.settlement.settled for request in provider.requests))
        self.assertTrue(provider.requests[0].settlement.assistant_content_discarded)

    async def test_renderer_can_return_complete_ack_without_a_tts_or_fallback_path(self) -> None:
        offers: list[RendererClause] = []

        async def acknowledging_renderer(clause):
            offers.append(clause)
            return True

        provider = ScriptedProvider([
            tool_delta(0, "say", "say_to_user", '{"text":"纯文字回复"}')
        ])
        runtime = self.runtime(provider, renderer=acknowledging_renderer)
        await runtime.submit_text("文字模式")
        await runtime.wait_until_idle()

        self.assertEqual([offer.text for offer in offers], ["纯文字回复"])
        self.assertEqual(self.store.fold().delivered_clauses, ("纯文字回复",))


class ClauseSegmentationTests(unittest.TestCase):
    def test_natural_soft_hard_and_final_boundaries_preserve_every_codepoint(self) -> None:
        natural = "短句。"
        soft = "甲" * 23 + "，"
        hard = "乙" * 80
        tail = "最后尾句"
        text = natural + soft + hard + tail

        clauses = segment_say_text(text, final=True)

        self.assertEqual(clauses, (natural, soft, hard, tail))
        self.assertEqual("".join(clauses), text)
        self.assertLessEqual(max(map(len, clauses)), 80)

    def test_nonfinal_segmentation_keeps_short_tail_until_final_flush(self) -> None:
        text = "没有自然结尾"

        self.assertEqual(segment_say_text(text, final=False), ())
        self.assertEqual(segment_say_text(text, final=True), (text,))

    def test_ascii_periods_in_decimals_abbreviations_and_urls_never_split_prematurely(self) -> None:
        for text in ("3.14", "e.g.", "https://example.com/path"):
            with self.subTest(text=text):
                self.assertEqual(segment_say_text(text, final=False), ())
                self.assertEqual(segment_say_text(text, final=True), (text,))


if __name__ == "__main__":
    unittest.main()
