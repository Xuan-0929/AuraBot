"""Exercise Realtime voice against the real Profile runtime, Hub and SQLite context.

Only the external speech model is scripted. Tests cover audio-before-text delivery,
physical playback acknowledgements, interruption fencing, tool deduplication and the
Realtime-only text-input restriction without loading local VAD, STT or TTS models.
Diagnostic assertions use the real Hub recorder to check identities, bounded milestones,
tool outcomes and interrupted-context repair without changing physical ACK semantics.
Empty provider audio streams must report their own error without pretending an interruption
occurred or committing unheard text; the event-worker path performs the same cleanup as live voice.
Scripted context snapshots cover delayed assistant commits, serialized cancellation,
readback mismatch and new generations racing repair while accepted tools remain durable.
"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.main_agent.context import ContextStore
from backend.main_agent.lifecycle import ProfileResources
from backend.main_agent.realtime import RealtimeHub
from backend.main_agent.realtime_voice import RealtimeVoiceEngine
from backend.main_agent.runtime import MainAgentRuntime
from backend.main_agent.seeduplex import VoiceEvent
from backend.main_agent.snapshot import SnapshotProjector
from backend.main_agent.tool_runtime import ToolCall, ToolSurfacePolicy
from backend.tests.test_main_agent_realtime import FakeSocket, ORIGIN
from backend.tests.test_main_agent_tasks import Manager, Timers
from backend.tests.test_conversation_capabilities import Memory


class Adaptor:
    def __init__(self):
        self.events = asyncio.Queue()
        self.results = []
        self.updates = []
        self.closed = False
        self.cancel_count = 0
        self.retrieve_count = 0
        self.replacements = []
        self.items = [
            {"id": "u1", "role": "user", "content": [{"type": "input_text", "text": "hello"}]},
            *({"id": id, "role": "assistant", "content": [{"type": "input_text", "text": "unheard"}]}
              for id in ("a1", "r1")),
        ]

    async def start(self, **config):
        self.config = config

    async def receive(self):
        return await self.events.get()

    async def send_audio(self, _pcm):
        pass

    async def mute(self):
        pass

    async def close(self):
        self.closed = True

    async def initialize_history(self, items):
        self.history = items

    async def tool_results(self, results):
        self.results.append(results)

    async def update_instructions(self, instructions):
        self.updates.append(instructions)

    async def cancel_response(self):
        self.cancel_count += 1
        self.events.put_nowait(VoiceEvent("cancel_ack"))

    async def retrieve_items(self):
        self.retrieve_count += 1
        self.events.put_nowait(VoiceEvent("items_retrieved", items=tuple(dict(item) for item in self.items)))

    async def replace_assistant(self, item_id, text):
        self.replacements.append(item_id)
        for item in self.items:
            if item["id"] == item_id:
                item["content"] = [{"type": "input_text", "text": text}]
        # Real SeedDuplex ACKs may contain only type and event_id, without items.
        self.events.put_nowait(VoiceEvent("items_updated", event_id="repair-1"))
        return "repair-1"


class RealtimeVoiceEngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.context = ContextStore(Path(self.temp.name) / "context.sqlite3")
        self.hub = RealtimeHub(allowed_origins={ORIGIN})
        self.provider_calls = []
        self.searches = []
        self.manager, self.timers = Manager(), Timers()

        async def provider(request):
            self.provider_calls.append(request)
            if False:
                yield {}

        async def search(call):
            self.searches.append(call.arguments)
            return {"sources": ["s1"]}

        self.runtime = MainAgentRuntime(context_store=self.context, snapshot_projector=SnapshotProjector(),
            tool_policy=ToolSurfacePolicy(observation_tools=("web_search",), workspace_ids=("repo",), task_controls=("cancel_task", "inspect_tasks")), provider=provider,
            renderer=self.hub.render, executors={"web_search": search}, soul="Profile Soul", owner_profile_id="profile",
            task_manager=self.manager, clock=self.timers.clock, proposal_sleep=self.timers.sleep)
        self.runtime.text_available = False
        await self.runtime.startup()
        self.adaptor = Adaptor()
        self.profile = ProfileResources("profile", self.runtime, None, None, voice_mode="realtime")
        self.profile.speech_factory = lambda: RealtimeVoiceEngine(self.hub, self.runtime, self.adaptor)
        await self.hub.set_active("profile", 1, self.profile)
        self.socket = FakeSocket()
        self.session = await self.hub.attach(self.socket)
        await self.hub.command(self.session, {"type": "voice.start", "command_id": "start"})

    async def asyncTearDown(self):
        await self.hub.stop_accepting()
        await self.runtime.shutdown()
        self.context.close()
        self.temp.cleanup()

    async def wait_event(self, kind):
        async with asyncio.timeout(1):
            while True:
                events = [e for e in self.socket.sent if isinstance(e, dict) and e.get("type") == kind]
                if events:
                    return events[-1]
                await asyncio.sleep(0)

    async def test_diagnostics_link_input_and_sparse_audio_milestones(self):
        self.hub.debug.configure(True, "capture")
        engine = self.profile.speech
        await engine._event(VoiceEvent("input_started", item_id="u1"))
        await engine._event(VoiceEvent("transcript_done", item_id="u1", text="hello"))
        for _ in range(100):
            await engine._event(VoiceEvent("audio", response_id="r1", item_id="a1", question_id="q1", audio=bytes(2)))
        await engine._event(VoiceEvent("text_done", response_id="r1", text="reply"))
        await engine._event(VoiceEvent("audio_done", response_id="r1"))
        await engine._event(VoiceEvent("response_done", response_id="r1"))
        offer = await self.wait_event("clause.audio_end")
        await self.hub.renderer_ack(self.session, offer["clause_id"], complete=True)
        events = self.hub.debug.snapshot()["events"]
        named = {e["name"]: e for e in events}
        self.assertEqual(named["realtime.input.started"]["segmentId"], named["realtime.transcript.completed"]["segmentId"])
        self.assertEqual(sum(e["name"] == "realtime.audio.first" for e in events), 1)
        self.assertLess(len(events), 25)
        done = named["realtime.audio.completed"]
        self.assertEqual(done["details"]["receivedBytes"], 200)
        self.assertEqual(done["details"]["questionId"], "q1")
        self.assertGreaterEqual(named["realtime.playback.first_chunk_sent"]["details"]["elapsedMs"], 0)

    async def test_diagnostics_report_failed_transcription_tool_and_repair(self):
        self.hub.debug.configure(True, "capture")
        engine = self.profile.speech
        await engine._event(VoiceEvent("transcript_failed", item_id="u1"))
        await engine._tools_called(VoiceEvent("tools", response_id="r1", calls=(
            ToolCall("bad-tool", "unknown_tool", {}),)), engine._input_epoch)
        await engine._event(VoiceEvent("audio", response_id="r1", item_id="a1", question_id="q1", audio=bytes(2)))
        await self.wait_event("clause.offer")
        await engine._event(VoiceEvent("input_started", item_id="u2"))
        async with asyncio.timeout(1):
            while engine._repair_count:
                await asyncio.sleep(0)
        for _ in range(50):
            await engine._event(VoiceEvent("audio", response_id="r1", question_id="q1", audio=bytes(2)))
        await engine.shutdown()
        events = {e["name"]: e for e in self.hub.debug.snapshot()["events"]}
        self.assertEqual(events["realtime.transcript.failed"]["level"], "warning")
        self.assertEqual(events["realtime.tool.settled"]["details"]["code"], "tool_not_exposed")
        self.assertEqual(events["realtime.tool.results_sent"]["details"]["responseId"], "r1")
        self.assertIn("realtime.context.repaired", events)
        self.assertEqual(events["realtime.stopped"]["details"]["droppedEvents"], 50)

    async def assert_playback_timeout(self, timeout, operation):
        self.hub.debug.configure(True, "capture")
        original_wait = asyncio.wait_for

        async def fail_selected_wait(awaitable, seconds):
            if seconds == timeout:
                awaitable.close()
                raise TimeoutError("private timeout detail")
            return await original_wait(awaitable, seconds)

        engine = self.profile.speech
        with patch("backend.main_agent.realtime_voice.asyncio.wait_for", side_effect=fail_selected_wait):
            await engine._event(VoiceEvent("audio", response_id="r1", item_id="a1", audio=bytes(2)))
            if timeout == 30:
                await engine._event(VoiceEvent("text_done", response_id="r1", text="reply"))
            await engine._event(VoiceEvent("audio_done", response_id="r1"))
            error = await self.wait_event("session.error")
            self.assertEqual(error["code"], "realtime_disconnected")
            await engine.shutdown()
        events = self.hub.debug.snapshot()["events"]
        failed = next(e for e in events if e["name"] == "realtime.playback.failed")
        self.assertEqual(failed["details"]["operation"], operation)
        self.assertEqual(failed["details"]["exceptionType"], "TimeoutError")
        self.assertNotIn("private timeout detail", str(events))

    async def test_text_timeout_keeps_stage_in_diagnostics(self):
        await self.assert_playback_timeout(5, "text_ready")

    async def test_ack_timeout_keeps_stage_in_diagnostics(self):
        await self.assert_playback_timeout(30, "playback_ack")

    async def test_diagnostic_sink_failure_does_not_break_transcription(self):
        self.hub.debug.configure(True, "capture")
        with patch("backend.main_agent.realtime_voice.speech_logger.log", side_effect=OSError("disk failed")):
            await self.profile.speech._event(VoiceEvent("transcript_failed", item_id="u1"))
        self.assertFalse(self.adaptor.closed)

    async def test_audio_starts_before_final_text_and_only_ack_commits_history(self):
        self.assertEqual(self.session.mode, "voice")
        self.adaptor.events.put_nowait(VoiceEvent("transcript_done", item_id="u1", text="Hello"))
        self.adaptor.events.put_nowait(VoiceEvent("audio", response_id="r1", item_id="a1", audio=bytes(640)))
        offer = await self.wait_event("clause.offer")
        async with asyncio.timeout(1):
            while not any(isinstance(x, bytes) for x in self.socket.sent):
                await asyncio.sleep(0)
        self.assertFalse(any(t["role"] == "assistant" for t in self.context.fold().conversation_turns))
        self.adaptor.events.put_nowait(VoiceEvent("text_done", response_id="r1", text="Hello back"))
        self.adaptor.events.put_nowait(VoiceEvent("audio_done", response_id="r1"))
        await self.wait_event("clause.audio_end")
        self.assertTrue(await self.hub.renderer_ack(self.session, offer["clause_id"], complete=True))
        self.assertEqual(self.context.fold().conversation_turns[-1]["content"], "Hello back")
        self.assertEqual(self.provider_calls, [])

    async def test_interruption_fences_late_audio_and_excludes_unheard_reply(self):
        self.adaptor.events.put_nowait(VoiceEvent("audio", response_id="r1", item_id="a1", audio=bytes(640)))
        await self.wait_event("clause.offer")
        self.adaptor.events.put_nowait(VoiceEvent("input_started", item_id="u2"))
        await self.wait_event("clause.cancel")
        self.adaptor.events.put_nowait(VoiceEvent("audio", response_id="r1", audio=bytes(640)))
        self.adaptor.events.put_nowait(VoiceEvent("text_done", response_id="r1", text="never heard"))
        await asyncio.sleep(.01)
        self.assertIsNone(self.session.current_clause)
        self.assertEqual(self.context.fold().conversation_turns, ())

    async def test_realtime_rejects_text_but_allows_text_after_voice_stop(self):
        self.runtime.text_available = True
        await self.hub.command(self.session, {"type": "text.submit", "command_id": "t1", "text": "blocked"})
        self.assertEqual(self.socket.sent[-1]["code"], "realtime_text_disabled")
        await self.hub.command(self.session, {"type": "voice.stop", "command_id": "stop"})
        await self.hub.command(self.session, {"type": "text.submit", "command_id": "t2", "text": "allowed"})
        await self.runtime.wait_until_idle()
        self.assertEqual(len(self.provider_calls), 1)

    async def test_duplicate_tool_events_execute_once(self):
        event = VoiceEvent("tools", response_id="r1", calls=(ToolCall("call-1", "web_search", {"query": "weather"}),))
        self.adaptor.events.put_nowait(event)
        self.adaptor.events.put_nowait(event)
        async with asyncio.timeout(1):
            while len(self.adaptor.results) < 2:
                await asyncio.sleep(0)
        self.assertEqual(self.searches, [{"query": "weather"}])

    async def test_official_audio_started_without_response_id_is_only_a_hint(self):
        await self.profile.speech._event(VoiceEvent("audio_started"))
        self.assertFalse(self.context.has_unsettled_clauses())

    async def test_cancelled_reply_cannot_admit_a_late_new_tool_call(self):
        engine = self.profile.speech
        await engine._event(VoiceEvent("audio", response_id="r1", item_id="a1", audio=bytes(640)))
        await self.wait_event("clause.offer")
        await engine._event(VoiceEvent("input_started"))
        await engine._tools_called(VoiceEvent("tools", response_id="r1", calls=(
            ToolCall("late", "web_search", {"query": "obsolete"}),)), engine._input_epoch)
        self.assertEqual(self.searches, [])
        self.assertEqual(self.adaptor.results, [])

    async def test_new_response_id_from_retired_question_cannot_restart_audio(self):
        engine = self.profile.speech
        await engine._event(VoiceEvent("audio", response_id="r1", item_id="a1", question_id="q1", audio=bytes(640)))
        await self.wait_event("clause.offer")
        await engine._event(VoiceEvent("input_started"))
        await engine._event(VoiceEvent("audio", response_id="late-r2", question_id="q1", audio=bytes(640)))
        self.assertNotIn("late-r2", engine._replies)

    async def test_no_item_identity_on_interrupt_ends_session_without_inventing_history(self):
        self.adaptor.items = [item for item in self.adaptor.items if item["role"] == "user"]
        self.profile.speech.REPAIR_TIMEOUT = .15
        self.adaptor.events.put_nowait(VoiceEvent("audio", response_id="r1", audio=bytes(640)))
        await self.wait_event("clause.offer")
        self.adaptor.events.put_nowait(VoiceEvent("input_started"))
        error = await self.wait_event("session.error")
        self.assertEqual(error["code"], "realtime_context_unconfirmed")
        async with asyncio.timeout(1):
            while not self.adaptor.closed:
                await asyncio.sleep(0)
        self.assertFalse(self.context.has_unsettled_clauses())
        self.assertEqual(self.context.fold().conversation_turns, ())

    async def wait_repaired(self):
        async with asyncio.timeout(1):
            while self.profile.speech._repair_count:
                await asyncio.sleep(0)
        self.assertFalse(self.profile.speech._repair_failed)
        self.assertTrue(self.profile.speech._context_ready.is_set())

    async def interrupt(self, *, audio_done=False):
        engine = self.profile.speech
        await engine._event(VoiceEvent("audio", response_id="r1", question_id="u1", audio=bytes(640)))
        await self.wait_event("clause.offer")
        if audio_done:
            await engine._event(VoiceEvent("text_done", response_id="r1", text="unheard"))
            await engine._event(VoiceEvent("audio_done", response_id="r1"))
            await self.wait_event("clause.audio_end")
        await engine._event(VoiceEvent("input_started", item_id="u2"))
        await self.wait_event("clause.cancel")

    async def test_missing_wire_item_id_is_resolved_and_next_turn_plays_in_same_session(self):
        await self.interrupt(audio_done=True)
        await self.wait_repaired()
        self.assertFalse(self.adaptor.closed)
        self.assertEqual(self.adaptor.replacements, ["r1"])
        self.assertEqual(self.adaptor.cancel_count, 0)
        self.assertEqual(self.adaptor.items[0]["content"][0]["text"], "hello")
        engine = self.profile.speech
        await engine._event(VoiceEvent("audio", response_id="r1", audio=bytes(640)))
        self.assertIsNone(self.session.current_clause)
        for event in (
            VoiceEvent("transcript_done", item_id="u2", text="new question"),
            VoiceEvent("audio", response_id="r2", question_id="u2", audio=bytes(640)),
            VoiceEvent("text_done", response_id="r2", text="new reply"),
            VoiceEvent("audio_done", response_id="r2"),
        ):
            await engine._event(event)
        async with asyncio.timeout(1):
            while not (self.session.current_clause or "").endswith(":r2") or not self.session.audio_finished:
                await asyncio.sleep(0)
        await self.hub.renderer_ack(self.session, self.session.current_clause, complete=True)
        self.assertEqual([t["content"] for t in self.context.fold().conversation_turns], ["new question", "new reply"])

    async def test_delayed_item_commit_and_repeated_interrupts_share_one_cancel(self):
        original = self.adaptor.retrieve_items
        first = True

        async def delayed():
            nonlocal first
            if first:
                first = False
                self.adaptor.events.put_nowait(VoiceEvent("items_retrieved", items=()))
            else:
                await original()

        self.adaptor.retrieve_items = delayed
        await self.interrupt()
        await self.profile.speech._event(VoiceEvent("input_started", item_id="u3"))
        await self.wait_repaired()
        self.assertEqual(self.adaptor.cancel_count, 1)
        self.assertEqual(self.adaptor.retrieve_count, 2)
        self.assertEqual(self.adaptor.replacements, ["r1"])

    async def test_multiple_retired_replies_use_one_cancel_and_serial_updates(self):
        engine = self.profile.speech
        await engine._event(VoiceEvent("audio", response_id="r1", item_id="a1", audio=bytes(640)))
        await engine._event(VoiceEvent("audio", response_id="r2", item_id="r1", audio=bytes(640)))
        await self.wait_event("clause.offer")
        await engine._event(VoiceEvent("input_started", item_id="u2"))
        await self.wait_repaired()
        self.assertEqual(self.adaptor.cancel_count, 1)
        self.assertEqual(self.adaptor.replacements, ["a1", "r1"])
        self.assertFalse(self.context.has_unsettled_clauses())

    async def test_update_ack_without_matching_readback_fails_closed(self):
        async def ack_only(item_id, text):
            self.adaptor.events.put_nowait(VoiceEvent("items_updated", items=({"id": item_id},)))
        self.adaptor.replace_assistant = ack_only
        await self.interrupt()
        error = await self.wait_event("session.error")
        self.assertEqual(error["code"], "realtime_context_unconfirmed")
        self.assertFalse(any(t["role"] == "assistant" for t in self.context.fold().conversation_turns))

    async def test_user_item_with_response_id_is_never_overwritten(self):
        self.profile.speech.REPAIR_TIMEOUT = .15
        self.adaptor.items = [{"id": "r1", "role": "user"}]
        await self.interrupt()
        self.assertEqual((await self.wait_event("session.error"))["code"], "realtime_context_unconfirmed")
        self.assertEqual(self.adaptor.replacements, [])

    async def test_new_generation_during_repair_is_not_played_or_allowed_tools(self):
        await self.interrupt()
        self.adaptor.events.put_nowait(VoiceEvent("tools", response_id="r2", question_id="u2",
            calls=(ToolCall("unsafe", "web_search", {"query": "unrepaired"}),)))
        self.assertEqual((await self.wait_event("session.error"))["code"], "realtime_context_unconfirmed")
        self.assertEqual(self.searches, [])
        self.assertEqual(self.adaptor.results, [])

    async def test_new_audio_during_repair_cannot_be_buffered_as_safe_continuation(self):
        await self.interrupt()
        self.adaptor.events.put_nowait(VoiceEvent("audio", response_id="r2", question_id="u2", audio=bytes(640)))
        self.assertEqual((await self.wait_event("session.error"))["code"], "realtime_context_unconfirmed")
        self.assertNotIn("r2", self.profile.speech._replies)

    async def test_update_ack_for_another_item_cannot_confirm_repair(self):
        self.profile.speech.REPAIR_TIMEOUT = .15
        async def wrong_ack(item_id, text):
            self.adaptor.events.put_nowait(VoiceEvent("items_updated", items=({"id": "unrelated"},)))
        self.adaptor.replace_assistant = wrong_ack
        await self.interrupt()
        self.assertEqual((await self.wait_event("session.error"))["code"], "realtime_context_unconfirmed")
        self.assertEqual(self.adaptor.retrieve_count, 1)

    async def test_stop_during_repair_cancels_wait_without_session_error(self):
        async def no_snapshot():
            pass
        self.adaptor.retrieve_items = no_snapshot
        await self.interrupt()
        await self.hub.command(self.session, {"type": "voice.stop", "command_id": "stop"})
        self.assertTrue(self.adaptor.closed)
        self.assertFalse(any(isinstance(e, dict) and e.get("type") == "session.error" for e in self.socket.sent))

    async def test_missing_cancel_ack_does_not_block_verified_context_repair(self):
        async def no_ack():
            self.adaptor.cancel_count += 1
        self.adaptor.cancel_response = no_ack
        await self.interrupt()
        await self.wait_repaired()
        self.assertEqual(self.adaptor.cancel_count, 1)
        self.assertEqual(self.adaptor.replacements, ["r1"])

    async def test_provider_completed_generation_before_barge_in_needs_no_cancel(self):
        await self.profile.speech._event(VoiceEvent("audio", response_id="r1", audio=bytes(640)))
        await self.wait_event("clause.offer")
        await self.profile.speech._event(VoiceEvent("response_done"))
        await self.profile.speech._event(VoiceEvent("input_started", item_id="u2"))
        await self.wait_repaired()
        self.assertEqual(self.adaptor.cancel_count, 0)

    async def test_accepted_tool_survives_interrupt_without_old_cloud_continuation(self):
        started, release, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        self.addCleanup(release.set)
        async def search(call):
            started.set()
            await release.wait()
            completed.set()
            return {"sources": []}
        self.runtime.capabilities.executors["web_search"] = search
        self.adaptor.events.put_nowait(VoiceEvent("tools", response_id="r1",
            calls=(ToolCall("slow", "web_search", {"query": "slow"}),)))
        await asyncio.wait_for(started.wait(), 1)
        await self.interrupt()
        await self.wait_repaired()
        release.set()
        await asyncio.wait_for(completed.wait(), 1)
        await asyncio.sleep(.01)
        self.assertEqual(self.adaptor.results, [])

    async def test_queued_audio_is_released_on_stop_and_no_history_is_committed(self):
        engine = self.profile.speech
        await engine._event(VoiceEvent("audio", response_id="r1", audio=bytes(640)))
        await engine.shutdown()
        self.assertEqual(engine._replies["r1"].buffered_bytes, 0)
        self.assertFalse(self.context.has_unsettled_clauses())

    async def test_explicit_stop_during_audio_does_not_attempt_cloud_repair(self):
        self.adaptor.events.put_nowait(VoiceEvent("audio", response_id="r1", audio=bytes(640)))
        await self.wait_event("clause.offer")
        await self.hub.command(self.session, {"type": "voice.stop", "command_id": "stop"})
        await asyncio.sleep(0)
        self.assertFalse(any(isinstance(e, dict) and e.get("type") == "session.error" for e in self.socket.sent))
        self.assertFalse(self.context.has_unsettled_clauses())

    async def test_browser_detach_discards_reply_without_repairing_a_closed_session(self):
        self.adaptor.events.put_nowait(VoiceEvent("audio", response_id="r1", audio=bytes(640)))
        await self.wait_event("clause.offer")
        await self.hub.detach(self.session)
        self.assertTrue(self.adaptor.closed)
        self.assertFalse(self.context.has_unsettled_clauses())

    async def test_history_preserves_unanswered_confirmed_input_as_facts(self):
        self.context.append_event("user_segment", {"segment_id": "u1", "text": "remember my request"})
        engine = self.profile.speech
        self.assertEqual(engine._history(), [])
        self.assertIn("remember my request", engine._instructions())

    async def test_text_only_response_end_does_not_leave_memory_blocked(self):
        self.hub.debug.configure(True, "capture")
        for event in (
            VoiceEvent("text_done", response_id="r1", text="unheard"),
            VoiceEvent("audio_started", response_id="r1"),
            VoiceEvent("audio_done", response_id="r1"),
            VoiceEvent("response_done"),
        ):
            self.adaptor.events.put_nowait(event)
        error = await self.wait_event("session.error")
        self.assertEqual(error["code"], "realtime_audio_missing")
        async with asyncio.timeout(1):
            while not self.adaptor.closed:
                await asyncio.sleep(0)
        self.assertFalse(self.context.has_unsettled_clauses())
        self.assertFalse(any(t["role"] == "assistant" for t in self.context.fold().conversation_turns))
        names = [e["name"] for e in self.hub.debug.snapshot()["events"]]
        self.assertIn("realtime.response.discarded", names)
        self.assertNotIn("realtime.context.repairing", names)

    async def test_realtime_input_and_voice_stop_preserve_accepted_task_proposal(self):
        engine = self.profile.speech
        event = VoiceEvent("tools", calls=(ToolCall("task1", "task_assign", {
            "title": "Research", "prompt": "Investigate", "workspace_id": "repo"}),))
        await engine._tools_called(event, engine._input_epoch)
        await engine._event(VoiceEvent("input_started"))
        self.assertEqual(self.runtime.capabilities.tasks.proposals[0].status, "pending")
        await self.hub.command(self.session, {"type": "voice.stop", "command_id": "stop"})
        await self.timers.advance(3.1)
        async with asyncio.timeout(1):
            while not self.manager.created:
                await asyncio.sleep(0)
        self.assertEqual(len(self.manager.created), 1)
        self.assertEqual(self.manager.created[0].owner_profile_id, "profile")

    async def test_task_fact_update_preserves_persona_without_invoking_text_model(self):
        async with self.runtime._lock:
            self.runtime._accept_task_fact_locked({"kind": "task", "id": "task1", "status": "completed", "title": "Research"})
        async with asyncio.timeout(1):
            while not self.adaptor.updates:
                await asyncio.sleep(0)
        self.assertIn("Profile Soul", self.adaptor.updates[-1])
        self.assertIn("completed", self.adaptor.updates[-1])
        self.assertEqual(self.provider_calls, [])
        self.assertFalse(any(isinstance(e, dict) and e.get("type") == "clause.offer" for e in self.socket.sent))

    async def test_voice_close_leaves_frozen_archive_owned_by_profile(self):
        memory = Memory()
        self.runtime.conversation_memory.service = memory
        self.runtime.conversation_memory.idle_seconds = 0
        await self.profile.speech._event(VoiceEvent("transcript_done", item_id="u1", text="archive me"))
        await asyncio.wait_for(memory.started.wait(), 1)
        await self.hub.command(self.session, {"type": "voice.stop", "command_id": "stop"})
        self.assertIsNotNone(self.context.pending_conversation_archive())
        memory.release.set()
        async with asyncio.timeout(1):
            while self.context.pending_conversation_archive() is not None:
                await asyncio.sleep(0)
        self.assertEqual(len(memory.batches), 1)
        self.assertEqual(self.context.fold().conversation_summaries[-1]["content"], "archived")

    async def test_accepted_tool_finishes_after_close_without_result_replay(self):
        started, release, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        self.addCleanup(release.set)
        async def search(call):
            started.set()
            await release.wait()
            completed.set()
            return {"sources": []}
        self.runtime.capabilities.executors["web_search"] = search
        self.adaptor.events.put_nowait(VoiceEvent("tools", calls=(ToolCall("slow", "web_search", {"query": "slow"}),)))
        await asyncio.wait_for(started.wait(), 1)
        await self.hub.command(self.session, {"type": "voice.stop", "command_id": "stop"})
        release.set()
        await asyncio.wait_for(completed.wait(), 1)
        self.assertEqual(self.adaptor.results, [])

    async def test_batch_returns_individual_validation_and_timeout_errors(self):
        async def timeout(call):
            raise TimeoutError()
        self.runtime.capabilities.executors["web_search"] = timeout
        await self.profile.speech._tools_called(VoiceEvent("tools", calls=(
            ToolCall("bad", "web_search", {}),
            ToolCall("unknown", "invented", {}),
            ToolCall("timeout", "web_search", {"query": "slow"}),
        )), 0)
        self.assertEqual(self.adaptor.results[-1], {
            "bad": {"error": "schema_invalid"}, "unknown": {"error": "tool_not_exposed"}, "timeout": {"error": "tool_timeout"},
        })

    async def test_old_transcript_revision_cannot_replace_new_prefetch(self):
        engine = self.profile.speech
        queries = []
        engine.memory.prefetch = queries.append
        await engine._event(VoiceEvent("input_started", item_id="u1"))
        await engine._event(VoiceEvent("transcript_delta", item_id="u1", text="old"))
        await engine._event(VoiceEvent("input_started", item_id="u2"))
        await engine._event(VoiceEvent("transcript_delta", item_id="u2", text="new"))
        await engine._event(VoiceEvent("transcript_done", item_id="u1", text="old final"))
        self.assertEqual(queries, ["old", "new"])

    async def test_avatar_late_arrival_is_validated_and_retired_after_playback(self):
        engine = self.profile.speech
        await engine._event(VoiceEvent("audio", response_id="r1", item_id="a1", audio=bytes(640)))
        offer = await self.wait_event("clause.offer")
        await engine._tools_called(VoiceEvent("tools", response_id="r1", calls=(ToolCall("avatar1", "set_avatar", {"expression": "neutral"}),)), 0)
        update = await self.wait_event("clause.avatar")
        self.assertEqual(update["clause_id"], offer["clause_id"])
        await engine._tools_called(VoiceEvent("tools", response_id="r1", calls=(ToolCall("bad-avatar", "set_avatar", {"motion": "invented"}),)), 0)
        self.assertEqual(self.adaptor.results[-1]["bad-avatar"], {"error": "schema_invalid"})
        await engine._event(VoiceEvent("text_done", response_id="r1", text="hello"))
        await engine._event(VoiceEvent("audio_done", response_id="r1"))
        await self.wait_event("clause.audio_end")
        await self.hub.renderer_ack(self.session, offer["clause_id"], complete=True)
        await engine._tools_called(VoiceEvent("tools", response_id="r1", calls=(ToolCall("avatar2", "set_avatar", {"expression": "neutral"}),)), 0)
        self.assertEqual(self.adaptor.results[-1]["avatar2"], {"status": "expired"})
