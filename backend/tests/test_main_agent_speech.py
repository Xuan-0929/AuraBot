"""Check production callback speech normalization, PREPARE admission, and physical delivery.

The tests protect mutable-only DashScope partials through the real coordinator and Runtime admission limit,
stable segment identity, authoritative playback events, first-chunk TTS streaming, Speech Ledger phases,
ACK serialization, cancellation, and SpeechService ownership without real providers or microphones.
VAD-only onset qualification through volume dips, lossless burst spooling, log rotation and cancellation while awaiting playback
credit exercise the real coordinators with controlled provider callbacks and PCM fixtures.
STT regression cases delay final callbacks and overlap finish/close to protect single-owner cleanup.
"""

# Acoustic edge tests drive the real coordinator with fake detectors, without cloud or hardware.
from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from backend.main_agent import ContextStore, SnapshotProjector
from backend.main_agent.runtime import MainAgentRuntime, PrepareAdmissionPolicy, RendererClause
from backend.main_agent.speech import (
    ClauseSpeechDelivery,
    DashScopeSpeechOutputProvider,
    DashScopeStreamingSttProvider,
    SpeechInputCoordinator,
    SpeechService,
    SileroVadDetector,
    TranscriptUpdate,
)
from backend.main_agent.tool_runtime import InvokeMode, ToolSurfacePolicy


NEAR_PCM = b"\x00\x08" * 320


class CallbackTransport:
    def __init__(self) -> None:
        self.release = threading.Event()
        self.cancelled = False

    def stream(self, _text: str, on_data: Any, on_complete: Any, _on_error: Any) -> None:
        on_data(b"first")
        self.release.wait(1)
        if not self.cancelled:
            on_data(b"second")
            on_complete()

    def cancel(self) -> None:
        self.cancelled = True
        self.release.set()


class DashScopeTtsTests(unittest.IsolatedAsyncioTestCase):
    async def test_spool_limit_cancels_provider_and_closes_temporary_audio(self):
        transport = CallbackTransport()
        spool = tempfile.TemporaryFile()
        with patch("backend.main_agent.speech.MAX_TTS_SPOOL_BYTES", 2), patch("backend.main_agent.speech.tempfile.TemporaryFile", return_value=spool):
            with self.assertRaisesRegex(RuntimeError, "streaming failed"):
                _ = [chunk async for chunk in DashScopeSpeechOutputProvider(
                    "test", voice="test", transport_factory=lambda **_: transport).synthesize("hello")]
        self.assertTrue(transport.cancelled)
        self.assertTrue(spool.closed)

    async def test_diagnostic_log_has_a_bounded_lifetime(self):
        from backend.main_agent import metrics
        with tempfile.TemporaryDirectory() as directory:
            with metrics.speech_diagnostics(Path(directory)):
                for _ in range(150):
                    metrics.speech_logger.info("diagnostic test record %s", "0" * 8000)
                metrics.speech_logger.info("playback_cancelled reason=test")
                handler = metrics.speech_logger.handlers[-1]
            self.assertIsNone(handler.stream)
            files = list(Path(directory).glob("speech-diagnostics.log*"))
            self.assertEqual(len(files), 2)
            self.assertTrue(all(path.stat().st_size <= 512_000 for path in files))
            self.assertIn("playback_cancelled reason=test", (Path(directory) / "speech-diagnostics.log").read_text())
    async def test_fast_callbacks_survive_a_slow_playback_consumer(self):
        completed = threading.Event()
        class BurstTransport:
            def stream(self, _text, on_data, on_complete, _on_error):
                for index in range(80):
                    on_data(index.to_bytes(2, "little") * 4800)
                on_complete()
                completed.set()
            def cancel(self):
                pass
        provider = DashScopeSpeechOutputProvider("test", voice="test", transport_factory=lambda **_: BurstTransport())
        stream = provider.synthesize("burst")
        try:
            first = await anext(stream)
            await asyncio.to_thread(completed.wait, 1)
            await asyncio.sleep(.01)
            chunks = [first] + [chunk async for chunk in stream]
            self.assertEqual(b"".join(chunks), b"".join(i.to_bytes(2, "little") * 4800 for i in range(80)))
            self.assertLessEqual(max(map(len, chunks)), 9600)
        finally:
            await stream.aclose()
    async def test_default_transport_configures_sdk_key_before_synthesizer_creation(self) -> None:
        class FakeDashScope:
            api_key: str | None = None

        dashscope = FakeDashScope()

        class FakeTts:
            class AudioFormat:
                PCM_24000HZ_MONO_16BIT = object()

            class ResultCallback:
                pass

            class SpeechSynthesizer:
                def __init__(self, model: str, voice: str, format: object, callback: Any) -> None:
                    if dashscope.api_key != "speech-key":
                        raise RuntimeError("DashScope API key was not configured")
                    self.callback = callback

                def streaming_call(self, _text: str) -> None:
                    self.callback.on_data(b"audio")

                def streaming_complete(self) -> None:
                    self.callback.on_complete()

                def streaming_cancel(self) -> None:
                    return None

        def load_module(name: str) -> Any:
            if name == "dashscope":
                return dashscope
            if name == "dashscope.audio.tts_v2":
                return FakeTts
            raise ModuleNotFoundError(name)

        with patch("backend.main_agent.speech.importlib.import_module", side_effect=load_module):
            chunks = [chunk async for chunk in DashScopeSpeechOutputProvider(
                "cosyvoice", voice="Aura", api_key="speech-key",
            ).synthesize("hello")]

        self.assertEqual(chunks, [b"audio"])

    async def test_completed_callback_does_not_cancel_closed_transport(self) -> None:
        class CompletedTransport:
            def __init__(self) -> None:
                self.cancelled = False

            def stream(self, _text: str, on_data: Any, on_complete: Any, _on_error: Any) -> None:
                on_data(b"audio")
                on_complete()
                threading.Event().wait(0.05)

            def cancel(self) -> None:
                self.cancelled = True
                raise ConnectionError("transport already closed")

        transport = CompletedTransport()
        chunks = [chunk async for chunk in DashScopeSpeechOutputProvider(
            "cosyvoice", voice="Aura", transport_factory=lambda **_kwargs: transport,
        ).synthesize("hello")]

        self.assertEqual(chunks, [b"audio"])
        self.assertFalse(transport.cancelled)

    async def test_first_callback_chunk_is_yielded_before_stream_completion(self) -> None:
        transport = CallbackTransport()
        provider = DashScopeSpeechOutputProvider(
            "cosyvoice", voice="Aura", transport_factory=lambda **_kwargs: transport,
        )
        stream = provider.synthesize("hello").__aiter__()
        self.assertEqual(await asyncio.wait_for(anext(stream), 0.2), b"first")
        self.assertFalse(transport.release.is_set())
        transport.release.set()
        self.assertEqual(await anext(stream), b"second")
        with self.assertRaises(StopAsyncIteration):
            await anext(stream)

    async def test_generator_close_cancels_its_own_transport(self) -> None:
        transport = CallbackTransport()
        provider = DashScopeSpeechOutputProvider(
            "cosyvoice", voice="Aura", transport_factory=lambda **_kwargs: transport,
        )
        stream = provider.synthesize("hello")
        iterator = stream.__aiter__()
        self.assertEqual(await anext(iterator), b"first")
        await stream.aclose()
        self.assertTrue(transport.cancelled)


class FakeVad:
    def __init__(self, values: list[float]) -> None:
        self.values = values

    def speech_probability(self, _frame: bytes) -> float:
        return self.values.pop(0)

    def reset(self) -> None:
        return None


class FakeSttSession:
    def __init__(self, sink: Any) -> None:
        self.sink = sink
        self.frames: list[bytes] = []

    async def send_audio(self, frame: bytes) -> None:
        self.frames.append(frame)

    async def close(self) -> None:
        return None

    async def finish(self) -> None:
        await self.close()


class FakeStt:
    def __init__(self) -> None:
        self.sessions: list[FakeSttSession] = []

    async def start(self, sink: Any) -> FakeSttSession:
        session = FakeSttSession(sink)
        self.sessions.append(session)
        return session


class FakeDashScopeSttTransport:
    def __init__(self, on_event: Any) -> None:
        self.on_event = on_event
        self.frames: list[bytes] = []
        self.stopped = False

    def start(self) -> None:
        return None

    def send_audio_frame(self, frame: bytes) -> None:
        self.frames.append(frame)

    def stop(self) -> None:
        self.stopped = True

    def emit_partial(self, text: str) -> None:
        self.on_event({"sentence": {"text": text, "sentence_end": False}})


class FakeInputRuntime:
    def __init__(self) -> None:
        self.updates: list[tuple[str, str | None, bool]] = []

    async def speech_input_failed(self, segment_id, *, has_transcript=True) -> None: pass
    async def vad_start(self) -> None: pass
    async def confirm_speech(self) -> None: pass
    async def reject_speech_candidate(self) -> None: pass
    async def vad_stop_candidate(self) -> None: pass
    async def update_transcript(self, text: str, *, segment_id: str | None, request_prepare: bool) -> None:
        self.updates.append((text, segment_id, request_prepare))


class FakePlayback:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def speech_candidate_started(self) -> None: self.events.append("candidate")
    async def speech_confirmed(self) -> None: self.events.append("confirmed")


class SpeechInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_speech_waits_for_endpoint_commit_after_stt_finish(self):
        entered, release = asyncio.Event(), asyncio.Event()
        runtime, stt = AsyncMock(), FakeStt()
        async def commit():
            entered.set()
            await release.wait()
        runtime.endpoint_fallback.side_effect = commit
        coordinator = SpeechInputCoordinator(runtime, stt_provider=stt,
            vad=FakeVad([1.0, 0.0, 1.0]), speech_confirm_ms=20,
            vad_stop_ms=20, unavailable_fallback_seconds=0)
        incoming = None
        try:
            await coordinator.accept_mic_frame(NEAR_PCM)
            await stt.sessions[0].sink(TranscriptUpdate("final", "final", "", "final"))
            await coordinator.accept_mic_frame(b"\0" * 640)
            await entered.wait()
            incoming = asyncio.create_task(coordinator.accept_mic_frame(NEAR_PCM))
            await asyncio.sleep(.01)
            release.set()
            await incoming
            self.assertEqual(len(stt.sessions), 2)
        finally:
            release.set()
            if incoming is not None:
                await incoming
            await coordinator.close()

    async def test_finish_timeout_closes_once_and_settles_input(self):
        runtime, stt = AsyncMock(), FakeStt()
        coordinator = SpeechInputCoordinator(runtime, stt_provider=stt,
            vad=FakeVad([1.0, 0.0]), speech_confirm_ms=20,
            vad_stop_ms=20, unavailable_fallback_seconds=0)
        try:
            await coordinator.accept_mic_frame(NEAR_PCM)
            session = stt.sessions[0]
            session.finish = asyncio.Event().wait
            session.close = AsyncMock()
            with patch("backend.main_agent.speech._STT_FINISH_TIMEOUT_SECONDS", .02):
                await coordinator.accept_mic_frame(b"\0" * 640)
                await coordinator.wait_for_endpoint()
            runtime.speech_input_failed.assert_awaited_once()
            session.close.assert_awaited_once()
            runtime.endpoint_fallback.assert_not_awaited()
        finally:
            await coordinator.close()

    async def test_new_speech_during_finish_gets_a_fresh_segment(self):
        entered, release = asyncio.Event(), asyncio.Event()
        runtime, stt = AsyncMock(), FakeStt()
        coordinator = SpeechInputCoordinator(runtime, stt_provider=stt,
            vad=FakeVad([1.0, 0.0, 1.0]), speech_confirm_ms=20,
            vad_stop_ms=20, unavailable_fallback_seconds=0)
        incoming = None
        try:
            await coordinator.accept_mic_frame(NEAR_PCM)
            old = stt.sessions[0]
            async def finish():
                entered.set()
                await release.wait()
                await old.sink(TranscriptUpdate("old final", "old final", "", "final"))
            old.finish = finish
            await coordinator.accept_mic_frame(b"\0" * 640)
            await entered.wait()
            incoming = asyncio.create_task(coordinator.accept_mic_frame(NEAR_PCM))
            await asyncio.sleep(.01)
            release.set()
            await incoming
            runtime.endpoint_fallback.assert_awaited_once()
            self.assertEqual(len(stt.sessions), 2)
            await stt.sessions[1].sink(TranscriptUpdate("new", "", "new", "partial"))
            await old.sink(TranscriptUpdate("late old", "", "late old", "partial"))
            updates = runtime.update_transcript.await_args_list
            self.assertEqual([call.args[0] for call in updates], ["old final", "new"])
            self.assertNotEqual(updates[0].kwargs["segment_id"], updates[1].kwargs["segment_id"])
            self.assertEqual(stt.sessions[1].frames, [NEAR_PCM])
        finally:
            release.set()
            if incoming is not None:
                await incoming
            await coordinator.close()

    async def test_finish_failure_settles_input_and_isolates_next_segment(self):
        for failure in (RuntimeError("stop failed"), TimeoutError()):
            with self.subTest(failure=type(failure).__name__):
                runtime = AsyncMock()
                stt = FakeStt()
                coordinator = SpeechInputCoordinator(runtime, stt_provider=stt,
                    vad=FakeVad([1.0, 0.0, 1.0]), speech_confirm_ms=20,
                    vad_stop_ms=20, unavailable_fallback_seconds=0)
                try:
                    await coordinator.accept_mic_frame(NEAR_PCM)
                    old = stt.sessions[0]
                    await old.sink(TranscriptUpdate("partial", "", "partial", "partial"))
                    old.finish = AsyncMock(side_effect=failure)
                    await coordinator.accept_mic_frame(b"\0" * 640)
                    await coordinator.wait_for_endpoint()
                    runtime.speech_input_failed.assert_awaited_once()
                    runtime.endpoint_fallback.assert_not_awaited()
                    await coordinator.accept_mic_frame(NEAR_PCM)
                    await old.sink(TranscriptUpdate("late old final", "late old final", "", "final"))
                    self.assertEqual(runtime.update_transcript.await_count, 1)
                    self.assertEqual(len(stt.sessions), 2)
                finally:
                    await coordinator.close()

    async def test_finish_waits_for_inflight_send(self):
        entered, release = asyncio.Event(), asyncio.Event()
        stt = FakeStt()
        coordinator = SpeechInputCoordinator(FakeInputRuntime(), stt_provider=stt,
            vad=FakeVad([1.0, 0.0]), speech_confirm_ms=20)
        await coordinator.accept_mic_frame(NEAR_PCM)
        session = stt.sessions[0]
        async def send(_frame):
            entered.set()
            await release.wait()
        session.send_audio = send
        session.finish = AsyncMock()
        sending = asyncio.create_task(coordinator.accept_mic_frame(b"\0" * 640))
        await entered.wait()
        finishing = asyncio.create_task(coordinator._close_stt(finalize=True))
        try:
            await asyncio.sleep(0)
            session.finish.assert_not_awaited()
            release.set()
            await sending
            self.assertTrue(await finishing)
            session.finish.assert_awaited_once()
        finally:
            release.set()
            await coordinator.close()

    async def test_delayed_final_transcript_survives_normal_finish(self):
        for delay in (.15, .3, .6):
            with self.subTest(delay=delay):
                class Transport(FakeDashScopeSttTransport):
                    def stop(self):
                        threading.Event().wait(delay)
                        self.on_event({"sentence": {"text": "final words", "sentence_end": True}})
                runtime = FakeInputRuntime()
                coordinator = SpeechInputCoordinator(runtime,
                    stt_provider=DashScopeStreamingSttProvider("test", transport_factory=lambda **kw: Transport(kw["on_event"])),
                    vad=FakeVad([1.0]), speech_confirm_ms=20)
                try:
                    await coordinator.accept_mic_frame(NEAR_PCM)
                    self.assertTrue(await coordinator._close_stt(finalize=True))
                    self.assertEqual(runtime.updates[-1][0], "final words")
                finally:
                    await coordinator.close()

    async def test_finish_and_close_share_one_transport_stop(self):
        entered, release = threading.Event(), threading.Event()
        class Transport(FakeDashScopeSttTransport):
            stops = 0
            def stop(self):
                self.stops += 1
                entered.set()
                release.wait(1)
        transport = Transport(lambda _: None)
        session = await DashScopeStreamingSttProvider("test", transport_factory=lambda **_: transport).start(AsyncMock())
        finishing = asyncio.create_task(session.finish())
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            closing = asyncio.create_task(session.close())
            await asyncio.sleep(.02)
            release.set()
            await asyncio.gather(finishing, closing, return_exceptions=True)
            self.assertEqual(transport.stops, 1)
        finally:
            release.set()
            await session.close()

    async def test_short_continuation_does_not_reject_an_already_confirmed_turn(self):
        class Runtime(FakeInputRuntime):
            def __init__(self):
                super().__init__()
                self.rejected = False
                self.completed = False
            async def reject_speech_candidate(self):
                self.rejected = True
            async def endpoint_fallback(self):
                self.completed = True
        runtime, stt = Runtime(), FakeStt()
        coordinator = SpeechInputCoordinator(runtime, stt_provider=stt,
            vad=FakeVad([1.0] * 15 + [0.0, 1.0, 0.0]), vad_stop_ms=20, unavailable_fallback_seconds=.1)
        try:
            for _ in range(15):
                await coordinator.accept_mic_frame(NEAR_PCM)
            await stt.sessions[0].sink(TranscriptUpdate("a complete thought", "", "a complete thought", "partial"))
            await coordinator.accept_mic_frame(b"\0" * 640)
            await coordinator.accept_mic_frame(NEAR_PCM)
            await coordinator.accept_mic_frame(b"\0" * 640)
            await coordinator.wait_for_endpoint()
            self.assertFalse(runtime.rejected)
            self.assertTrue(runtime.completed)
        finally:
            await coordinator.close()
    async def test_onset_rejects_non_speech_and_intermittent_vad_but_preserves_volume_dips(self):
        runtime, playback, stt = AsyncMock(), FakePlayback(), FakeStt()
        values = [0.0] * 30 + [1.0, 0.0] * 20 + [1.0] * 15
        coordinator = SpeechInputCoordinator(runtime, vad=FakeVad(values), stt_provider=stt, playback_lifecycle=playback)
        quiet_voice = b"\x40\x00" * 320
        near_voice = b"\x00\x08" * 320
        try:
            for _ in range(30):
                await coordinator.accept_mic_frame(near_voice)
            self.assertEqual(playback.events, [])
            self.assertEqual(stt.sessions, [])
            for index in range(40):
                await coordinator.accept_mic_frame(near_voice if index % 2 == 0 else b"\0" * 640)
            self.assertEqual(playback.events, [])
            onset = [near_voice] * 9 + [quiet_voice] + [near_voice] * 5
            for frame in onset[:14]:
                await coordinator.accept_mic_frame(frame)
            self.assertEqual(playback.events, [])
            self.assertEqual(runtime.mock_calls, [])
            await coordinator.accept_mic_frame(onset[-1])
            self.assertEqual(playback.events, ["candidate", "confirmed"])
            self.assertEqual(len(stt.sessions), 1)
            self.assertGreaterEqual(len(stt.sessions[0].frames), 15)
            self.assertEqual(stt.sessions[0].frames[-15:], onset)
            self.assertEqual(runtime.confirm_speech.await_count, 1)
            await stt.sessions[0].sink(TranscriptUpdate("hello", "", "hello", "partial"))
            self.assertEqual(runtime.confirm_speech.await_count, 1)
        finally:
            await coordinator.close()

    async def test_background_calibration_does_not_veto_vad_onset_before_or_after_reset(self):
        runtime, stt = AsyncMock(), FakeStt()
        coordinator = SpeechInputCoordinator(runtime, vad=FakeVad([0.0] * 100 + [1.0] * 30), stt_provider=stt)
        try:
            for _ in range(100):
                await coordinator.accept_mic_frame(b"\x00\x02" * 320)
            self.assertEqual(stt.sessions, [])
            for _ in range(15):
                await coordinator.accept_mic_frame(b"\x00\x04" * 320)
            self.assertEqual(len(stt.sessions), 1)
            await coordinator.reset_input()
            for _ in range(15):
                await coordinator.accept_mic_frame(b"\x00\x04" * 320)
            self.assertEqual(len(stt.sessions), 2)
        finally:
            await coordinator.close()
    async def test_acoustic_edges_cover_pause_resume_and_reset_without_transcripts(self) -> None:
        class Playback(FakePlayback):
            async def speech_activity(self, *, active):
                self.events.append("start" if active else "stop")
            async def speech_candidate_rejected(self):
                self.events.append("rejected")
        playback = Playback()
        coordinator = SpeechInputCoordinator(FakeInputRuntime(), vad=FakeVad([0, 1, 0, 1]),
            playback_lifecycle=playback, vad_stop_ms=20, speech_confirm_ms=20)
        frame = NEAR_PCM
        await coordinator.accept_mic_frame(frame)
        self.assertEqual(playback.events, [])
        await coordinator.accept_mic_frame(frame)
        self.assertEqual(playback.events, ["candidate", "confirmed"])
        await coordinator.accept_mic_frame(frame)
        self.assertIn("stop", playback.events)
        await coordinator.wait_for_endpoint()
        self.assertIn("rejected", playback.events)
        await coordinator.accept_mic_frame(frame)
        self.assertEqual(playback.events[-2:], ["candidate", "confirmed"])
        await coordinator.reset_input()
        self.assertEqual(playback.events[-1], "stop")

    async def test_acoustic_restart_during_pending_endpoint(self) -> None:
        class Playback(FakePlayback):
            async def speech_activity(self, *, active):
                self.events.append("start" if active else "stop")
        class Coordinator(SpeechInputCoordinator):
            async def _eou_probability(self):
                await asyncio.Event().wait()
        playback = Playback()
        coordinator = Coordinator(FakeInputRuntime(), vad=FakeVad([1, 1, 0, 1]),
            playback_lifecycle=playback, vad_stop_ms=20, speech_confirm_ms=20)
        try:
            for _ in range(3):
                await coordinator.accept_mic_frame(NEAR_PCM)
            self.assertEqual(playback.events, ["candidate", "confirmed", "stop"])
            await coordinator.accept_mic_frame(NEAR_PCM)
            self.assertEqual(playback.events[-1], "start")
            self.assertEqual(playback.events.count("confirmed"), 1)
        finally:
            await coordinator.close()

    async def test_cancelled_dashscope_close_keeps_transport_cleanup_owned(self) -> None:
        entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        transports = []
        async def sink(_update):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
        def factory(**kwargs):
            transport = FakeDashScopeSttTransport(kwargs["on_event"])
            transports.append(transport)
            return transport
        session = await DashScopeStreamingSttProvider("test", transport_factory=factory).start(sink)
        transports[0].emit_partial("partial")
        await entered.wait()
        closing = asyncio.create_task(session.close())
        await cleaning.wait()
        closing.cancel()
        await asyncio.gather(closing, return_exceptions=True)
        self.assertFalse(transports[0].stopped)
        release.set()
        await session.close()
        self.assertTrue(transports[0].stopped)

    async def test_shutdown_joins_real_stt_close_before_retiring_microphone(self) -> None:
        from types import SimpleNamespace
        from backend.main_agent.realtime import RealtimeHub
        entered, release = threading.Event(), threading.Event()
        transports = []
        class ClosingTransport(FakeDashScopeSttTransport):
            def stop(self):
                entered.set()
                release.wait(1)
                self.stopped = True
        def factory(**kwargs):
            transport = ClosingTransport(kwargs["on_event"])
            transports.append(transport)
            return transport
        hub = RealtimeHub()
        coordinator = SpeechInputCoordinator(FakeInputRuntime(),
            stt_provider=DashScopeStreamingSttProvider("test", transport_factory=factory),
            vad=FakeVad([1.0, 0.0]), vad_stop_ms=20, speech_confirm_ms=20)
        service = SpeechService(hub, coordinator, SimpleNamespace(startup=AsyncMock(), shutdown=AsyncMock()))
        await service.startup()
        mic = service._mic_task
        closing = None
        try:
            await coordinator.accept_mic_frame(NEAR_PCM)
            closing = asyncio.create_task(service.shutdown())
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            await asyncio.sleep(.01)
            self.assertFalse(closing.done())
            release.set()
            await closing
            self.assertTrue(mic.done())
            self.assertTrue(transports[0].stopped)
        finally:
            release.set()
            if closing is not None:
                await closing
            await service.shutdown()

    async def test_dashscope_callbacks_keep_arrival_order_across_confirmation_await(self) -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        class BlockedRuntime(FakeInputRuntime):
            async def update_transcript(self, text, **kwargs):
                if not self.updates:
                    entered.set()
                    await release.wait()
                await super().update_transcript(text, **kwargs)
        transports = []
        def factory(**kwargs):
            transport = FakeDashScopeSttTransport(kwargs["on_event"])
            transports.append(transport)
            return transport
        runtime = BlockedRuntime()
        coordinator = SpeechInputCoordinator(runtime,
            stt_provider=DashScopeStreamingSttProvider("test", transport_factory=factory),
            vad=FakeVad([1.0]), speech_confirm_ms=20, playback_lifecycle=FakePlayback())
        try:
            await coordinator.accept_mic_frame(NEAR_PCM)
            transports[0].emit_partial("old partial")
            await entered.wait()
            transports[0].on_event({"sentence": {"text": "complete final words", "sentence_end": True}})
            await asyncio.sleep(0)
            release.set()
            self.assertTrue(await coordinator._close_stt(finalize=True))
            self.assertEqual([text for text, _, _ in runtime.updates], ["old partial", "complete final words"])
        finally:
            release.set()
            await coordinator.close()

    async def test_dashscope_mutable_partials_request_bounded_prepare_before_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transports: list[FakeDashScopeSttTransport] = []

            def transport_factory(**kwargs: Any) -> FakeDashScopeSttTransport:
                transport = FakeDashScopeSttTransport(kwargs["on_event"])
                transports.append(transport)
                return transport

            requests = []
            offers = []

            async def provider(request):
                requests.append(request)
                if False:
                    yield {}

            async def renderer(clause):
                offers.append(clause)

            context = ContextStore(Path(directory) / "context.sqlite3")
            runtime = MainAgentRuntime(
                context_store=context,
                snapshot_projector=SnapshotProjector(),
                tool_policy=ToolSurfacePolicy(),
                provider=provider,
                renderer=renderer,
                executors={},
                soul="test",
                prepare_admission=PrepareAdmissionPolicy(
                    min_interval_seconds=0,
                    min_growth_units=1,
                    max_wait_seconds=0,
                    punctuation_growth_units=1,
                    max_prepares_per_input=2,
                ),
            )
            coordinator = SpeechInputCoordinator(
                runtime,
                stt_provider=DashScopeStreamingSttProvider(
                    "paraformer-realtime",
                    transport_factory=transport_factory,
                ),
                vad=FakeVad([1.0]),
                speech_confirm_ms=20,
            )
            try:
                await coordinator.accept_mic_frame(NEAR_PCM)
                for partial in ("我", "我想", "我想查", "我想查天气"):
                    transports[0].emit_partial(partial)
                    await asyncio.sleep(0)
                    await asyncio.sleep(0)
                    await runtime.wait_until_idle()

                self.assertEqual(len(requests), 2)
                self.assertTrue(all(request.mode is InvokeMode.PREPARE for request in requests))
                self.assertTrue(all(request.response_grant is None for request in requests))
                self.assertIsNone(runtime.current_response_grant)
                self.assertEqual(offers, [])
                self.assertEqual(runtime.active_input.latest_transcript, "我想查天气")
            finally:
                await coordinator.close()
                await runtime.shutdown()
                context.close()

    async def test_stt_updates_in_one_turn_share_segment_and_drive_authoritative_playback(self) -> None:
        runtime = FakeInputRuntime()
        stt = FakeStt()
        playback = FakePlayback()
        coordinator = SpeechInputCoordinator(
            runtime,
            stt_provider=stt,
            vad=FakeVad([1.0]),
            playback_lifecycle=playback,
            speech_confirm_ms=20,
        )
        await coordinator.accept_mic_frame(NEAR_PCM)
        sink = stt.sessions[0].sink
        await sink(TranscriptUpdate("你", "你", "", "partial"))
        await sink(TranscriptUpdate("你好", "你好", "", "partial"))

        self.assertEqual([item[0] for item in runtime.updates], ["你", "你好"])
        self.assertEqual(runtime.updates[0][1], runtime.updates[1][1])
        self.assertTrue(runtime.updates[0][1])
        self.assertEqual(playback.events, ["candidate", "confirmed"])
        await coordinator.close()


class FakeOutput:
    def __init__(self) -> None:
        self.release = asyncio.Event()

    async def synthesize(self, text: str):
        yield b"\0\0" * len(text)
        await self.release.wait()


class FakePort:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def offer_clause(self, clause: RendererClause) -> None:
        self.events.append(("offer", clause.clause_id))

    async def send_audio(self, pcm: bytes) -> None:
        self.events.append(("audio", pcm))

    async def finish_audio(self, clause_id: str) -> None:
        self.events.append(("end", clause_id))

    async def cancel_clause(self, clause_id: str, reason: str) -> None:
        self.events.append(("cancel", clause_id, reason))


class FakeLedgerRuntime:
    def __init__(self) -> None:
        self.states: list[tuple[str, str]] = []
        self.shutdown_calls = 0

    async def renderer_tts_queued(self, clause_id: str) -> bool:
        self.states.append((clause_id, "queued")); return True

    async def renderer_playing(self, clause_id: str) -> bool:
        self.states.append((clause_id, "playing")); return True

    async def renderer_failed(self, clause_id: str) -> bool:
        self.states.append((clause_id, "failed")); return True

    async def renderer_ack(self, clause_id: str, *, complete: bool) -> bool:
        self.states.append((clause_id, "delivered" if complete else "interrupted")); return complete

    async def shutdown(self) -> None:
        self.shutdown_calls += 1


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancellation_joins_synthesis_while_waiting_for_playback_credit(self):
        entered, closed = asyncio.Event(), asyncio.Event()
        class Output:
            def synthesize(self, _text):
                async def generate():
                    try:
                        yield b"\0\0" * 4800
                    finally:
                        closed.set()
                self.stream = generate()
                return self.stream
        class BlockedPort(FakePort):
            async def send_audio(self, pcm):
                entered.set()
                await asyncio.Event().wait()
        output, port = Output(), BlockedPort()
        async def acquire():
            return port
        delivery = ClauseSpeechDelivery(output, acquire, FakeLedgerRuntime())
        try:
            await delivery(RendererClause("blocked", "hello", "invoke", "input", 1))
            await entered.wait()
            await delivery.cancel_current()
            self.assertTrue(closed.is_set(), "cancellation must close the paused synthesis generator before returning")
        finally:
            await delivery.shutdown()
            await output.stream.aclose()
    async def test_queue_first_pcm_and_ack_are_distinct_and_serialize_clauses(self) -> None:
        output = FakeOutput()
        port = FakePort()
        runtime = FakeLedgerRuntime()
        async def acquire() -> FakePort:
            return port
        delivery = ClauseSpeechDelivery(output, acquire, runtime, ack_timeout_seconds=1)
        await delivery.startup()
        await delivery(RendererClause("c1", "one", "invoke", "input", 1))
        await delivery(RendererClause("c2", "two", "invoke", "input", 1))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertIn(("c1", "queued"), runtime.states)
        self.assertIn(("c1", "playing"), runtime.states)
        self.assertNotIn(("offer", "c2"), port.events)
        output.release.set()
        for _ in range(20):
            if ("end", "c1") in port.events:
                break
            await asyncio.sleep(0)
        delivery.acknowledge("c1", complete=True)
        for _ in range(20):
            if ("offer", "c2") in port.events:
                break
            await asyncio.sleep(0)
        self.assertIn(("offer", "c2"), port.events)
        await delivery.cancel_current("test")
        await delivery.shutdown()


class FakeCoordinator:
    def __init__(self) -> None:
        self.frames: list[bytes] = []
        self.closed = False

    async def accept_mic_frame(self, frame: bytes) -> None:
        self.frames.append(frame)

    async def close(self) -> None:
        self.closed = True


class FakeHub:
    def __init__(self) -> None:
        from backend.main_agent.realtime import MicFrameQueue
        self.mic_queue = MicFrameQueue()


class SpeechServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_endpoint_deadline_remains_bounded_but_reset_joins_its_inference(self) -> None:
        entered, release = threading.Event(), threading.Event()
        fallback = asyncio.Event()
        class Runtime(FakeInputRuntime):
            async def endpoint_fallback(self):
                fallback.set()
        class Eou:
            def completion_probability(self, _audio):
                entered.set()
                release.wait(2)
                raise ValueError("optional inference failed")
        stt, hub = FakeStt(), FakeHub()
        coordinator = SpeechInputCoordinator(Runtime(), stt_provider=stt, vad=FakeVad([1.0, 0.0]),
            eou=Eou(), vad_stop_ms=20, speech_confirm_ms=20, eou_inference_timeout_seconds=.01,
            unavailable_fallback_seconds=.03)
        service = SpeechService(hub, coordinator, AsyncMock())
        await service.startup()
        resetting = None
        try:
            await coordinator.accept_mic_frame(NEAR_PCM)
            await stt.sessions[0].sink(TranscriptUpdate("input", "", "input", "partial"))
            await coordinator.accept_mic_frame(NEAR_PCM)
            await asyncio.wait_for(fallback.wait(), .5)
            self.assertTrue(entered.is_set())
            resetting = asyncio.create_task(service.reset_input())
            await asyncio.sleep(.01)
            self.assertFalse(resetting.done())
            release.set()
            await resetting
        finally:
            release.set()
            if resetting is not None:
                await asyncio.gather(resetting, return_exceptions=True)
            await service.shutdown()

    async def test_input_reset_reuses_workers_and_rejects_previous_turn_callbacks(self) -> None:
        runtime = FakeInputRuntime()
        stt = FakeStt()
        hub = FakeHub()
        coordinator = SpeechInputCoordinator(runtime, stt_provider=stt, vad=FakeVad([1.0, 1.0]), speech_confirm_ms=20)
        delivery = AsyncMock()
        service = SpeechService(hub, coordinator, delivery)
        await service.startup()
        mic_worker = service._mic_task
        try:
            await coordinator.accept_mic_frame(NEAR_PCM)
            old_sink = stt.sessions[0].sink
            await old_sink(TranscriptUpdate("old input", "", "old input", "partial"))
            await service.reset_input()
            await coordinator.accept_mic_frame(NEAR_PCM)
            await old_sink(TranscriptUpdate("late old input", "", "late old input", "partial"))
            await stt.sessions[1].sink(TranscriptUpdate("new input", "", "new input", "partial"))
            self.assertEqual([text for text, _, _ in runtime.updates], ["old input", "new input"])
            self.assertIs(service._mic_task, mic_worker)
            self.assertFalse(mic_worker.done())
            self.assertIs(service.coordinator, coordinator)
            self.assertIs(service.delivery, delivery)
        finally:
            await service.shutdown()

    async def test_input_reset_waits_for_inflight_vad_before_resetting_state(self) -> None:
        entered, release = threading.Event(), threading.Event()
        class HeldVad:
            running = False
            def __init__(self):
                self.frames = []
            def speech_probability(self, frame):
                self.frames.append(frame)
                self.running = True
                entered.set()
                release.wait(2)
                self.running = False
                return 0.0
            def reset(self):
                if self.running:
                    raise AssertionError("reset raced VAD inference")
        hub = FakeHub()
        coordinator = SpeechInputCoordinator(FakeInputRuntime(), vad=HeldVad())
        service = SpeechService(hub, coordinator, AsyncMock())
        await service.startup()
        resetting = None
        try:
            hub.mic_queue.put(NEAR_PCM)
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            hub.mic_queue.put(b"\1" * 640)
            resetting = asyncio.create_task(service.reset_input())
            await asyncio.sleep(0.01)
            self.assertFalse(resetting.done())
            release.set()
            await resetting
            await asyncio.sleep(.01)
            self.assertFalse(coordinator.audio or coordinator.pre_roll)
            self.assertEqual(coordinator.vad.frames, [NEAR_PCM])
        finally:
            release.set()
            if resetting is not None:
                await asyncio.gather(resetting, return_exceptions=True)
            await service.shutdown()

    async def test_service_owns_mic_and_delivery_but_never_runtime(self) -> None:
        hub = FakeHub()
        coordinator = FakeCoordinator()
        delivery = AsyncMock()
        runtime = FakeLedgerRuntime()
        service = SpeechService(hub, coordinator, delivery)
        await service.startup()
        hub.mic_queue.put(NEAR_PCM)
        await asyncio.sleep(0)
        await service.shutdown()
        self.assertEqual(coordinator.frames, [NEAR_PCM])
        self.assertTrue(coordinator.closed)
        self.assertEqual(runtime.shutdown_calls, 0)


class LocalModelTests(unittest.TestCase):
    def test_loaded_detector_does_not_read_static_assets_on_audio_path(self) -> None:
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as root:
            model, license_path = Path(root) / "model.onnx", Path(root) / "LICENSE"
            model.write_bytes(b"test-model")
            license_path.write_text("MIT License")
            session = object()
            ort = SimpleNamespace(SessionOptions=SimpleNamespace,
                ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL=0),
                GraphOptimizationLevel=SimpleNamespace(ORT_ENABLE_ALL=0),
                InferenceSession=lambda *_a, **_kw: session)
            detector = SileroVadDetector(model, license_path, ort_loader=lambda: ort)
            self.assertIs(detector.load_session(), session)
            with patch.object(Path, "read_text", side_effect=AssertionError("audio path reads license")):
                self.assertIs(detector.load_session(), session)
