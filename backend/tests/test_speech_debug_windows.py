"""Verify microphone diagnostics through the coordinator and recorder interfaces.

Controlled clocks and PCM frames exercise window accounting, capture isolation and
STT teardown serialization without cloud providers; finalization must exclude new sends.
Onset diagnostics must follow VAD-only confirmation while retaining independent RMS measurements.
"""
import asyncio
import struct
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from backend.main_agent.debug import DebugRecorder
from backend.main_agent.speech import SpeechInputCoordinator, SileroVadDetector, TranscriptUpdate
from backend.tests.test_main_agent_speech import FakeInputRuntime, FakeStt, FakePlayback


class Vad:
    value = 1.0

    def speech_probability(self, _frame):
        return self.value

    def reset(self):
        pass


class SpeechDebugWindowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 0.0
        self.recorder = DebugRecorder()
        self.runtime = FakeInputRuntime()
        self.runtime.debug = self.recorder.bind("profile", 1, 1)
        self.recorder.configure(True, "capture")
        self.vad, self.stt = Vad(), FakeStt()
        self.coordinator = SpeechInputCoordinator(self.runtime, vad=self.vad,
            stt_provider=self.stt, clock=lambda: self.now, speech_confirm_ms=2000)

    async def asyncTearDown(self):
        await self.coordinator.close()

    async def frame(self, time, rms=.04, vad=1.0):
        self.now, self.vad.value = time, vad
        await self.coordinator.accept_mic_frame(struct.pack("<h", round(rms * 32768)) * 320)

    def windows(self):
        return [e["details"] for e in self.recorder.snapshot()["events"] if e["name"] == "acoustics"]

    async def test_counts_ranges_resets_and_post_frame_counters(self):
        await self.frame(0)
        await self.frame(.05, .001)  # Low RMS does not reset onset confirmation.
        await self.frame(.10)
        await self.frame(.15, .04, 0)  # VAD reset
        await self.frame(.20)
        await self.frame(.25, .001, 0)  # VAD reset even when RMS is also low.
        await self.frame(.30)
        await self.frame(.35, .0001, None)  # unavailable reset
        await self.frame(.40, .001, 0)  # zero -> zero is not a reset
        self.assertEqual(self.windows(), [])
        await self.frame(.50)
        window = self.windows()[0]
        self.assertEqual(window["windowFrames"], 10)
        self.assertEqual(window["windowMs"], 500)
        self.assertEqual(window["vadPassFrames"], 6)
        self.assertEqual(window["rmsPassFrames"], 6)
        self.assertEqual(window["qualifiedFrames"], 6)
        self.assertEqual(window["maxQualifiedRunMs"], 60)
        self.assertEqual(window["confirmMs"], 20)
        self.assertEqual(window["maxConfirmMs"], 60)
        self.assertEqual(window["resetRmsCount"], 0)
        self.assertEqual(window["resetVadCount"], 2)
        self.assertEqual(window["resetBothCount"], 0)
        self.assertEqual(window["resetUnavailableCount"], 1)
        self.assertEqual(window["lastResetReason"], "vad_unavailable")
        self.assertEqual(window["lastResetFromMs"], 20)
        self.assertAlmostEqual(window["rmsMin"], 3 / 32768)
        self.assertAlmostEqual(window["rmsMax"], 1311 / 32768)
        self.assertEqual(window["vadUnavailableFrames"], 1)

    async def test_volume_dip_diagnostics_match_actual_stt_onset(self):
        self.coordinator.confirm_frames = 15
        for i in range(14):
            await self.frame(i * .02, rms=.001 if i == 9 else .04)
        self.assertEqual(self.stt.sessions, [])
        await self.frame(.28, rms=.001)
        self.assertEqual(len(self.stt.sessions), 1)
        await self.coordinator.reset_input()
        window = self.windows()[0]
        self.assertEqual(window["onsetGate"], "vad")
        self.assertEqual(window["confirmMs"], 300)
        self.assertEqual(window["maxQualifiedRunMs"], 300)
        self.assertEqual(window["qualifiedFrames"], 15)
        self.assertEqual(window["rmsPassFrames"], 13)
        self.assertTrue(window["speech"])
        self.assertLess(window["rms"], window["threshold"])
        self.assertEqual(window["resetRmsCount"], 0)
        self.assertIsNone(window["lastResetReason"])

    async def test_confirmation_continues_across_windows_but_window_run_is_clipped(self):
        for i in range(51):
            await self.frame(i * .02)
        first, second = self.windows()
        self.assertEqual(first["maxConfirmMs"], 520)
        self.assertEqual(second["confirmMs"], 1020)
        self.assertEqual(second["maxQualifiedRunMs"], 500)
        self.assertEqual(second["resetRmsCount"], 0)
        self.assertIsNone(second["lastResetReason"])

    async def test_clear_toggle_and_retired_scope_cannot_leak_old_frames(self):
        await self.frame(0, .09)
        self.recorder.clear("next")
        await self.frame(.2, .03)
        self.recorder.configure(False, "next")
        self.recorder.configure(True, "next")  # no PCM between toggles
        await self.frame(.4, .04)
        await self.frame(.9, .04)
        self.assertEqual(len(self.windows()), 1)
        self.assertEqual(self.windows()[0]["windowFrames"], 2)
        self.assertAlmostEqual(self.windows()[0]["rmsMax"], 1311 / 32768)
        self.recorder.bind("new_profile", 2, 1)
        before = self.recorder.cursor
        await self.frame(1.5)
        await self.coordinator.reset_input()
        self.assertEqual(self.recorder.cursor, before)

    async def test_stop_flushes_partial_window_and_disabled_capture_is_silent(self):
        await self.frame(0)
        await self.frame(.1)
        await self.coordinator.reset_input()
        self.assertEqual(self.windows()[0]["windowFrames"], 2)
        self.assertTrue(self.windows()[0]["partial"])
        self.recorder.configure(False, "capture")
        count = self.recorder.cursor
        await self.frame(1)
        await self.coordinator.reset_input()
        self.assertEqual(self.recorder.cursor, count)

    async def test_turn_end_flush_includes_frames_received_while_cleanup_waits(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class Playback(FakePlayback):
            async def speech_activity(self, *, active):
                entered.set()
                await release.wait()
        self.coordinator.playback_lifecycle = Playback()
        await self.frame(0)
        task = asyncio.create_task(self.coordinator._finish_turn())
        try:
            await entered.wait()
            await self.frame(.1)
        finally:
            release.set()
            await task
        self.assertEqual(sum(w["windowFrames"] for w in self.windows()), 2)
        self.assertTrue(self.windows()[-1]["partial"])

    async def test_preroll_send_error_identifies_send_operation(self):
        class Session:
            async def send_audio(self, _frame):
                raise RuntimeError("private message")
            async def close(self):
                pass
        class Provider:
            async def start(self, _sink):
                return Session()
        self.coordinator.stt_provider = Provider()
        self.coordinator.confirm_frames = 1
        await self.frame(0)
        failure = next(e["details"] for e in self.recorder.snapshot()["events"] if e["name"] == "error")
        self.assertEqual(failure["code"], "stt_start_failed")
        self.assertEqual(failure["operation"], "send_audio")

    async def test_stt_finish_excludes_new_audio_sends(self):
        entered, release = asyncio.Event(), asyncio.Event()

        class Session:
            async def send_audio(self, _frame):
                if entered.is_set():
                    raise RuntimeError("private provider message")

            async def finish(self):
                entered.set()
                await release.wait()

            async def close(self):
                pass

        class Provider:
            async def start(self, _sink):
                return Session()

        self.coordinator.stt_provider = Provider()
        self.coordinator.confirm_frames = 1
        await self.frame(0)
        task = asyncio.create_task(self.coordinator._close_stt(finalize=True))
        try:
            await entered.wait()
            await self.frame(.02, vad=0.0)
        finally:
            release.set()
            await task
        events = self.recorder.snapshot()["events"]
        self.assertFalse([e for e in events if e["name"] == "error"])
        self.assertNotIn("private provider message", str(events))
        self.assertTrue({"finish_started", "finish_ended"} <= {e["name"] for e in events})

    async def test_vad_status_transitions_are_not_repeated_every_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            detector = SileroVadDetector(Path(directory) / "model", Path(directory) / "license")
            # The ONNX seam receives real numpy buffers; asset loading is exercised separately below.
            import numpy as np
            detector._session = SimpleNamespace(run=lambda *_: [np.array([.9]), np.zeros((2, 1, 128))])
            self.coordinator.vad = detector
            with patch.object(Path, "read_text", side_effect=AssertionError("hot-path asset read")), \
                 patch.object(Path, "is_file", side_effect=AssertionError("hot-path asset stat")):
                for i in range(27):
                    await self.frame(i * .02)
                detector._session.run = lambda *_: (_ for _ in ()).throw(ValueError("secret"))
                await self.frame(.54)
                await self.frame(.56)
                await self.coordinator.reset_input()
            states = [e["details"]["vadStatus"] for e in self.recorder.snapshot()["events"] if e["name"] == "vad_status"]
            self.assertEqual(states, ["warming_up", "ready", "inference_failed"])
            windows = self.windows()
            self.assertEqual(windows[0]["vadWarmupFrames"], 1)
            self.assertEqual(windows[-1]["vadErrorFrames"], 2)
            self.assertNotIn("secret", str(self.recorder.snapshot()))
            missing = SileroVadDetector(Path(directory) / "missing", Path(directory) / "license")
            self.assertIsNone(missing.speech_probability(b"\0" * 640))
            self.assertEqual(missing.diagnostic_state, ("unavailable", "model_asset_missing"))

    async def test_debug_on_and_off_preserve_two_turn_behavior(self):
        class Runtime(FakeInputRuntime):
            completed = 0
            async def eou_complete(self):
                self.completed += 1

        outcomes = []
        for enabled in (False, True):
            recorder, runtime, stt, vad, playback = DebugRecorder(), Runtime(), FakeStt(), Vad(), FakePlayback()
            runtime.debug = recorder.bind("profile", 1, 1)
            recorder.configure(enabled, "capture")
            coordinator = SpeechInputCoordinator(runtime, stt_provider=stt, vad=vad,
                playback_lifecycle=playback, eou=SimpleNamespace(completion_probability=lambda _: 1.0))
            try:
                for _ in range(2):
                    vad.value = 1.0
                    for i in range(15):
                        await coordinator.accept_mic_frame(b"\0\x08" * 320)
                        self.assertEqual(len(stt.sessions), runtime.completed + int(i == 14))
                    await stt.sessions[-1].sink(TranscriptUpdate("hello", "", "hello", "partial"))
                    vad.value = 0.0
                    for _ in range(15):
                        await coordinator.accept_mic_frame(b"\0" * 640)
                    await coordinator.wait_for_endpoint()
                outcomes.append((runtime.completed, [x[0] for x in runtime.updates],
                    [s.frames for s in stt.sessions], playback.events))
                windows = [e for e in recorder.snapshot()["events"] if e["name"] == "acoustics"]
                self.assertEqual(len(windows), 2 if enabled else 0)
                if enabled:
                    self.assertTrue(all(e["details"]["partial"] for e in windows))
            finally:
                await coordinator.close()
        self.assertEqual(outcomes[0], outcomes[1])

    async def test_stt_errors_include_operation_without_exception_message(self):
        for operation in ("start", "send_audio", "finish", "close"):
            with self.subTest(operation=operation):
                class Session:
                    fail_send = False
                    async def send_audio(self, _frame):
                        if self.fail_send:
                            raise ValueError("secret send")
                    async def finish(self):
                        if operation == "finish":
                            raise ValueError("secret finish")
                    async def close(self):
                        if operation == "close":
                            raise ValueError("secret close")
                session = Session()
                class Provider:
                    async def start(self, _sink):
                        if operation == "start":
                            raise ValueError("secret start")
                        return session
                self.recorder.clear(operation)
                self.coordinator.stt_provider = Provider()
                self.coordinator.confirm_frames = 1
                await self.frame(0)
                if operation == "send_audio":
                    session.fail_send = True
                    await self.frame(.02)
                else:
                    await self.coordinator._close_stt(finalize=operation == "finish")
                events = self.recorder.snapshot()["events"]
                failure = next(e["details"] for e in events if e["name"] == "error")
                self.assertEqual(failure["operation"], operation)
                self.assertEqual(failure["finalizing"], operation == "finish")
                self.assertFalse(failure["endpointPending"])
                self.assertEqual(failure["exceptionType"], "ValueError")
                self.assertNotIn("secret", str(events))
                await self.coordinator.reset_input()
