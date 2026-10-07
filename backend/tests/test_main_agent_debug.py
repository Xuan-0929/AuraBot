"""Exercise bounded opt-in diagnostics and origin-protected HTTP capture control.

Debug routes require explicit launch permission. Tests cross the recorder and realtime route interfaces, including identity retirement,
capture rollover, safe scalar projection, and snapshots of already running sessions.
"""
import json
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.launch_options import LaunchOptions
from backend.main_agent.debug import DebugRecorder
from backend.main_agent.realtime import RealtimeHub, router


class RecorderTests(unittest.TestCase):
    def test_disabled_capture_and_identity_retirement(self):
        recorder = DebugRecorder()
        old = recorder.bind("profile_a", 1, 3)
        old.emit("stt", "update", text="ignored")
        self.assertEqual(recorder.snapshot()["events"], [])
        recorder.configure(True, "one")
        old.emit("stt", "update", text="hello")
        fresh = recorder.bind("profile_b", 2, 4)
        old.emit("stt", "update", text="retired")
        fresh.emit("model", "start", mode="RESPOND")
        result = recorder.snapshot()
        self.assertEqual(len(result["events"]), 2)
        self.assertEqual(result["events"][0]["profileId"], "profile_a")
        self.assertNotIn("stt", result["state"])
        self.assertEqual(result["state"]["model"]["profileId"], "profile_b")

    def test_bounds_redaction_cursor_and_capture_reset(self):
        recorder = DebugRecorder()
        scope = recorder.bind("profile_a", 1, 1)
        recorder.configure(True, "one")
        scope.emit("stt", "update", text="汉" * 40000, api_key="secret", pcm=b"private", reason={"raw": "private"})
        event = recorder.snapshot()["events"][0]
        self.assertTrue(event["truncated"])
        self.assertLessEqual(len(json.dumps(event, ensure_ascii=False).encode()), 32768)
        self.assertNotIn("api_key", event["details"])
        self.assertNotIn("pcm", event["details"])
        self.assertNotIn("reason", event["details"])
        for i in range(1100):
            scope.emit("input", "sample", frames=i)
        result = recorder.snapshot(after=1)
        self.assertEqual(len(result["events"]), 1000)
        self.assertGreater(result["dropped"], 0)
        cursor = result["cursor"]
        recorder.configure(True, "one")
        self.assertEqual(recorder.snapshot()["cursor"], cursor)
        recorder.clear("two")
        self.assertTrue(recorder.enabled)
        self.assertEqual(recorder.snapshot()["events"], [])
        self.assertEqual(recorder.snapshot()["cursor"], cursor)
        scope.emit("input", "sample", frames=9)
        self.assertEqual(recorder.snapshot(capture_id="one")["events"], [])
        restarted = recorder.snapshot(after=999999, instance_id="old")
        self.assertEqual(len(restarted["events"]), 1)
        self.assertGreater(restarted["dropped"], 0)

    def test_total_byte_bound(self):
        recorder = DebugRecorder()
        scope = recorder.bind("profile", 1, 1)
        recorder.configure(True, "one")
        for _ in range(200):
            scope.emit("stt", "update", text="x" * 30000)
        events = recorder.snapshot()["events"]
        self.assertLess(len(events), 100)
        self.assertLessEqual(sum(len(json.dumps(e, ensure_ascii=False).encode()) for e in events), 2 * 1024 * 1024)

    def test_sampling_and_snapshot_freshness(self):
        recorder = DebugRecorder()
        scope = recorder.bind("profile", 1, 1)
        recorder.configure(True, "one")
        with patch("backend.main_agent.debug.time.monotonic", side_effect=[10, 10.1, 10.5]):
            self.assertTrue(scope.sample_due("acoustics"))
            self.assertFalse(scope.sample_due("acoustics"))
            self.assertTrue(scope.sample_due("acoustics"))
        with patch("backend.main_agent.debug.time.time", return_value=20):
            recorder.update_state(scope, "stt", segmentId="old", stablePrefix="old text")
        with patch("backend.main_agent.debug.time.time", return_value=30):
            recorder.update_state(scope, "stt", segmentId="old", stablePrefix="old text")
            self.assertEqual(recorder.state["stt"]["updatedAt"], 20000)
            recorder.update_state(scope, "stt", segmentId="new", active=True)
            self.assertNotIn("stablePrefix", recorder.state["stt"])

    def test_recording_failure_is_observational(self):
        recorder = DebugRecorder()
        scope = recorder.bind("profile", 1, 1)
        recorder.configure(True, "one")
        with patch.object(recorder, "emit", side_effect=ValueError("private serialization error")):
            scope.emit("playback", "offer", text="reply")
        self.assertEqual(recorder.dropped, 1)
        scope.emit("playback", "offer", text="next reply")
        self.assertEqual(len(recorder.snapshot()["events"]), 1)
        self.assertNotIn("private serialization error", json.dumps(recorder.snapshot()))


class PipelineDebugTests(unittest.IsolatedAsyncioTestCase):
    async def test_reply_offer_records_correlations_and_enable_mid_clause_reads_current_text(self):
        from backend.main_agent import ContextStore
        from backend.main_agent.lifecycle import ProfileResources
        from backend.main_agent.runtime import RendererClause
        from backend.tests.test_main_agent_realtime import FakeRuntime, FakeSpeech, FakeSocket
        hub, runtime = RealtimeHub(), FakeRuntime()
        with tempfile.TemporaryDirectory() as directory:
            runtime.context_store = ContextStore(Path(directory) / "context.sqlite3")
            try:
                runtime.context_store.create_clause("clause", "assistant reply")
                await hub.set_active("profile", 9, ProfileResources("profile", runtime, FakeSpeech(), None, revision=2))
                session = await hub.attach(FakeSocket())
                port = await hub.acquire_renderer_port()
                clause = RendererClause("clause", "assistant reply", "invoke", "input", 1, say_id="say")
                await port.offer_clause(clause)
                hub.debug.configure(True, "one")
                result = hub.debug_snapshot()
                self.assertEqual(result["events"], [])
                self.assertEqual(result["state"]["playback"]["text"], "assistant reply")
                self.assertEqual(result["state"]["playback"]["clauseId"], "clause")
                await hub.renderer_ack(session, "clause", complete=True)
                second = RendererClause("second", "next reply", "invoke2", "input2", 1, say_id="say2")
                await port.offer_clause(second)
                event = next(e for e in hub.debug_snapshot()["events"] if e["name"] == "offer")
                self.assertEqual(event["details"]["text"], "next reply")
                self.assertEqual([event[k] for k in ("inputId", "invokeId", "sayId", "clauseId")],
                    ["input2", "invoke2", "say2", "second"])
                self.assertEqual(hub.debug_snapshot()["state"]["playback"]["text"], "next reply")
                await hub.detach(session)
            finally:
                runtime.context_store.close()

    async def test_enable_mid_session_snapshots_without_reconnect_or_history(self):
        from backend.main_agent.lifecycle import ProfileResources
        from backend.tests.test_main_agent_realtime import FakeRuntime, FakeSpeech, FakeSocket
        hub = RealtimeHub()
        runtime = FakeRuntime()
        await hub.set_active("profile", 9, ProfileResources("profile", runtime, FakeSpeech(), None, revision=2))
        session = await hub.attach(FakeSocket())
        session.mode = "voice"
        session.input_active = True
        session.work_state = "thinking"
        hub.debug.configure(True, "one")
        result = hub.debug_snapshot()
        self.assertEqual(result["events"], [])
        self.assertIs(hub.session, session)
        self.assertEqual(result["state"]["connection"]["mode"], "voice")
        self.assertTrue(result["state"]["connection"]["inputActive"])
        for _ in range(55):
            hub.accept_mic_frame(b"\0" * 640)
        result = hub.debug_snapshot()
        self.assertEqual(result["state"]["input"]["frames"], 55)
        self.assertEqual(result["state"]["input"]["droppedFrames"], 5)
        await hub.detach(session)

    async def test_stt_eou_events_are_observational_and_retired_scope_is_silent(self):
        from backend.main_agent.speech import SpeechInputCoordinator, TranscriptUpdate
        from backend.tests.test_main_agent_speech import FakeInputRuntime, FakeStt, FakeVad, NEAR_PCM
        class Runtime(FakeInputRuntime):
            async def eou_complete(self):
                self.completed = True
        class Eou:
            def completion_probability(self, _audio):
                return .9
        recorder = DebugRecorder()
        runtime = Runtime()
        runtime.debug = recorder.bind("profile", 1, 2)
        recorder.configure(True, "one")
        stt = FakeStt()
        coordinator = SpeechInputCoordinator(runtime, stt_provider=stt, vad=FakeVad([1, 0]),
            eou=Eou(), speech_confirm_ms=20, vad_stop_ms=20)
        try:
            await coordinator.accept_mic_frame(NEAR_PCM)
            await stt.sessions[0].sink(TranscriptUpdate("hello world", "hello ", "world", "partial"))
            await coordinator.accept_mic_frame(b"\0" * 640)
            await coordinator.wait_for_endpoint()
            events = recorder.snapshot()["events"]
            names = {(e["stage"], e["name"]) for e in events}
            self.assertTrue({("stt", "start"), ("stt", "update"), ("stt", "end"),
                ("eou", "probability"), ("eou", "complete")} <= names)
            update = next(e for e in events if e["name"] == "update")
            self.assertEqual(update["details"]["mutableSuffix"], "world")
            self.assertEqual(runtime.updates[0][0], "hello world")
            self.assertTrue(runtime.completed)
            self.assertEqual(stt.sessions[0].frames, [NEAR_PCM, b"\0" * 640])
            self.assertLessEqual(sum(e["name"] == "acoustics" for e in events), 1)
            recorder.bind("new", 2, 3)
            before = recorder.cursor
            coordinator._debug("stt", "late", text="must be discarded")
            self.assertEqual(recorder.cursor, before)
        finally:
            await coordinator.close()

    async def test_model_events_exclude_provider_content_and_exception_messages(self):
        from backend.main_agent import ContextStore, SnapshotProjector
        from backend.main_agent.runtime import MainAgentRuntime
        from backend.main_agent.tool_runtime import ToolSurfacePolicy
        async def provider(_request):
            yield {"choices": [{"delta": {"content": "private model output"}}]}
            raise RuntimeError("secret provider credentials")
        async def renderer(_clause):
            pass
        recorder = DebugRecorder()
        with tempfile.TemporaryDirectory() as directory:
            store = ContextStore(Path(directory) / "context.sqlite3")
            runtime = MainAgentRuntime(context_store=store, snapshot_projector=SnapshotProjector(),
                tool_policy=ToolSurfacePolicy(), provider=provider, renderer=renderer, executors={}, soul="private soul")
            runtime.debug = recorder.bind("profile", 1, 2)
            recorder.configure(True, "one")
            try:
                await runtime.submit_text("hello")
                await runtime.wait_until_idle()
                result = recorder.snapshot()
                names = {event["name"] for event in result["events"]}
                self.assertTrue({"trigger_admitted", "invoke_start", "first_chunk", "invoke_end", "receipt", "settled"} <= names)
                serialized = json.dumps(result)
                self.assertNotIn("private model output", serialized)
                self.assertNotIn("secret provider credentials", serialized)
                self.assertNotIn("private soul", serialized)
                self.assertIn("provider_stream_error", serialized)
            finally:
                await runtime.shutdown()
                store.close()

    async def test_tts_timing_and_playback_ack_do_not_capture_audio_or_clause_text(self):
        from backend.main_agent.speech import ClauseSpeechDelivery
        from backend.main_agent.runtime import RendererClause
        from backend.tests.test_main_agent_speech import FakeOutput, FakePort, FakeLedgerRuntime
        recorder = DebugRecorder()
        runtime, output, port = FakeLedgerRuntime(), FakeOutput(), FakePort()
        runtime.debug = recorder.bind("profile", 1, 1)
        recorder.configure(True, "one")
        async def acquire():
            return port
        delivery = ClauseSpeechDelivery(output, acquire, runtime)
        try:
            await delivery(RendererClause("clause", "private speech text", "invoke", "input", 1))
            async with asyncio.timeout(1):
                while not any(event["name"] == "first_audio" for event in recorder.snapshot()["events"]):
                    await asyncio.sleep(0)
            output.release.set()
            async with asyncio.timeout(1):
                while ("end", "clause") not in port.events:
                    await asyncio.sleep(0)
            self.assertTrue(delivery.acknowledge("clause", complete=True))
            await delivery._queue.join()
            result = recorder.snapshot()
            self.assertEqual([event["name"] for event in result["events"]], ["queued", "start", "first_audio", "end"])
            self.assertNotIn("private speech text", json.dumps(result))
            self.assertIn(("audio", b"\0\0" * len("private speech text")), port.events)
        finally:
            await delivery.shutdown()


class DebugRouteTests(unittest.TestCase):
    def setUp(self):
        self.hub = RealtimeHub()
        app = FastAPI()
        app.state.main_agent_realtime = self.hub
        app.state.launch_options = LaunchOptions(debug=True)
        app.include_router(router)
        self.client = TestClient(app)
        self.headers = {"origin": "http://localhost:6400"}

    def test_routes_guard_origin_and_capture_real_state(self):
        path = "/api/v1/main-agent/debug"
        self.assertEqual(self.client.get(path + "/events").status_code, 403)
        self.assertEqual(self.client.post(path + "/configure", json={"enabled": True, "captureId": "one"}).status_code, 403)
        self.assertEqual(self.client.post(path + "/clear", json={"captureId": "two"}).status_code, 403)
        response = self.client.post(path + "/configure", headers=self.headers, json={"enabled": True, "captureId": "one"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        value = response.json()
        self.assertTrue(value["enabled"])
        self.assertEqual([event["name"] for event in value["events"]], ["launch.options"])
        self.assertFalse(value["state"]["connection"]["connected"])
        self.assertIsInstance(value["state"]["connection"]["timestamp"], int)
        clear = self.client.post(path + "/clear", headers=self.headers, json={"captureId": "two"}).json()
        self.assertEqual(clear["captureId"], "two")
        self.assertTrue(clear["enabled"])


if __name__ == "__main__":
    unittest.main()
