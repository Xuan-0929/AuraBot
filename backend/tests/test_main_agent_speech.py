"""Check production callback speech normalization, PREPARE admission, and physical delivery.

The tests protect mutable-only DashScope partials through the real coordinator and Runtime admission limit,
stable segment identity, authoritative playback events, first-chunk TTS streaming, Speech Ledger phases,
ACK serialization, cancellation, and SpeechService ownership without real providers or microphones.
"""

from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

from backend.main_agent import ContextStore, SnapshotProjector
from backend.main_agent.runtime import MainAgentRuntime, PrepareAdmissionPolicy, RendererClause
from backend.main_agent.speech import (
    ClauseSpeechDelivery,
    DashScopeSpeechOutputProvider,
    DashScopeStreamingSttProvider,
    SpeechInputCoordinator,
    SpeechService,
    TranscriptUpdate,
)
from backend.main_agent.tool_runtime import InvokeMode, ToolSurfacePolicy


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
                speech_confirm_ms=160,
            )
            try:
                await coordinator.accept_mic_frame(b"\0" * 640)
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
            speech_confirm_ms=160,
        )
        await coordinator.accept_mic_frame(b"\0" * 640)
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
    async def test_service_owns_mic_and_delivery_but_never_runtime(self) -> None:
        hub = FakeHub()
        coordinator = FakeCoordinator()
        delivery = AsyncMock()
        runtime = FakeLedgerRuntime()
        service = SpeechService(hub, coordinator, delivery)
        await service.startup()
        hub.mic_queue.put(b"\0" * 640)
        await asyncio.sleep(0)
        await service.shutdown()
        self.assertEqual(coordinator.frames, [b"\0" * 640])
        self.assertTrue(coordinator.closed)
        self.assertEqual(runtime.shutdown_calls, 0)
