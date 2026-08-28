"""Prove the Main Agent realtime trust boundary, lease semantics, and bounded audio protocol.

These tests exercise the real token store, frame/tombstone parser, loop-affine bounded writer, immutable
lease producer port, cancellation-safe session state machine, and supervised FastAPI WebSocket route. A
tiny runtime double stands only at MainAgentRuntime; cross-loop framing, epoch fencing, and cleanup remain
production behavior.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import struct
import threading
import types
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

os.environ.setdefault(
    "AURABOT_CONFIG_PATH",
    str(Path(__file__).parent / "fixtures" / "runtime_config.yaml"),
)

from backend.main import create_app
from backend.main_agent.realtime import (
    AUR1_HEADER_SIZE,
    AudioFrame,
    AudioFrameKind,
    AudioFrameTracker,
    BoundedAudioQueue,
    CapabilityTokens,
    OutboundWriter,
    ProtocolError,
    RealtimeHub,
    pack_audio_frame,
    parse_audio_frame,
    validate_control_message,
    _Session,
    _handle_audio,
    _handle_control,
    _open_session,
    _run_writer,
    _supervise_session,
)
from backend.main_agent.speech import ClauseSpeechDelivery


ORIGIN = "http://127.0.0.1:6400"
PROTOCOL = "aurabot.main-agent.v1"


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class FakeRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.active_input = None
        self.latest_context_version = 0

    async def submit_text(self, text: str) -> None:
        self.calls.append(("text", text))

    async def vad_start(self) -> None:
        self.calls.append(("vad_start",))

    async def vad_stop_candidate(self) -> None:
        self.calls.append(("vad_stop",))

    async def reject_speech_candidate(self) -> None:
        self.calls.append(("speech_rejected",))

    async def update_transcript(
        self,
        text: str,
        *,
        segment_id: str | None = None,
        request_prepare: bool = True,
    ) -> None:
        self.calls.append(("transcript", text, request_prepare))

    async def eou_complete(self) -> None:
        self.calls.append(("speech_confirmed",))

    async def renderer_ack(self, clause_id: str, *, complete: bool) -> bool:
        self.calls.append(("ack", clause_id, complete))
        return clause_id == "known"

    async def renderer_disconnected(self) -> None:
        self.calls.append(("renderer_disconnected",))


class CapabilityTokenTests(unittest.TestCase):
    def test_token_is_short_lived_single_use_and_bound_to_origin(self) -> None:
        clock = Clock()
        tokens = CapabilityTokens(ttl_seconds=5, clock=clock)
        token = tokens.issue(ORIGIN)

        self.assertTrue(tokens.consume(token, ORIGIN))
        self.assertFalse(tokens.consume(token, ORIGIN))

        second = tokens.issue(ORIGIN)
        self.assertFalse(tokens.consume(second, "http://evil.invalid"))
        clock.now += 6
        self.assertFalse(tokens.consume(second, ORIGIN))

    def test_token_store_is_bounded(self) -> None:
        tokens = CapabilityTokens(ttl_seconds=5, max_tokens=2, clock=Clock())
        first = tokens.issue(ORIGIN)
        tokens.issue(ORIGIN)
        tokens.issue(ORIGIN)

        self.assertFalse(tokens.consume(first, ORIGIN))


class ControlValidationTests(unittest.TestCase):
    def test_control_requires_object_type_and_strictly_positive_sequence(self) -> None:
        self.assertEqual(
            validate_control_message('{"type":"mic.stop","seq":1}'),
            {"type": "mic.stop", "seq": 1},
        )
        for raw in ("[]", '{"type":"","seq":1}', '{"type":"mic.stop","seq":0}'):
            with self.subTest(raw=raw), self.assertRaises(ProtocolError):
                validate_control_message(raw)

    def test_control_rejects_oversize_deep_and_long_string_json(self) -> None:
        with self.assertRaisesRegex(ProtocolError, "size"):
            validate_control_message(" " * 65_537)
        deep = {"type": "x", "seq": 1, "v": "ok"}
        for _ in range(9):
            deep = {"type": "x", "seq": 1, "v": deep}
        with self.assertRaisesRegex(ProtocolError, "depth"):
            validate_control_message(json.dumps(deep))
        with self.assertRaisesRegex(ProtocolError, "string"):
            validate_control_message(json.dumps({"type": "x", "seq": 1, "v": "x" * 16_385}))


class AudioFrameTests(unittest.TestCase):
    def test_aur1_header_is_exactly_24_bytes_and_round_trips_mic_frame(self) -> None:
        payload = bytes(640)
        encoded = pack_audio_frame(
            AudioFrame(AudioFrameKind.MIC, 0, 7, 4, 1280, payload)
        )

        self.assertEqual(AUR1_HEADER_SIZE, 24)
        self.assertEqual(encoded[:4], b"AUR1")
        self.assertEqual(len(encoded), 664)
        self.assertEqual(parse_audio_frame(encoded), AudioFrame(AudioFrameKind.MIC, 0, 7, 4, 1280, payload))

    def test_binary_parser_rejects_bad_header_kind_flags_and_payload(self) -> None:
        valid = pack_audio_frame(AudioFrame(AudioFrameKind.MIC, 0, 1, 0, 0, bytes(640)))
        cases = (
            valid[:23],
            b"NOPE" + valid[4:],
            valid[:4] + bytes([9]) + valid[5:],
            valid[:5] + bytes([0x80]) + valid[6:],
            valid[:-2],
        )
        for encoded in cases:
            with self.subTest(size=len(encoded)), self.assertRaises(ProtocolError):
                parse_audio_frame(encoded)

    def test_tracker_accepts_order_rejects_duplicate_and_requires_flagged_gap(self) -> None:
        tracker = AudioFrameTracker()
        one = AudioFrame(AudioFrameKind.MIC, 0, 3, 0, 0, bytes(640))
        two = AudioFrame(AudioFrameKind.MIC, 0, 3, 1, 320, bytes(640))
        self.assertEqual(tracker.accept(one), "accepted")
        self.assertEqual(tracker.accept(two), "accepted")
        self.assertEqual(tracker.accept(two), "duplicate")
        with self.assertRaisesRegex(ProtocolError, "gap"):
            tracker.accept(AudioFrame(AudioFrameKind.MIC, 0, 3, 3, 960, bytes(640)))
        self.assertEqual(
            tracker.accept(AudioFrame(AudioFrameKind.MIC, 1, 3, 3, 960, bytes(640))),
            "discontinuity",
        )

    def test_tracker_rejects_sample_position_regression(self) -> None:
        tracker = AudioFrameTracker()
        tracker.accept(AudioFrame(AudioFrameKind.MIC, 0, 1, 0, 0, bytes(640)))
        with self.assertRaisesRegex(ProtocolError, "sample"):
            tracker.accept(AudioFrame(AudioFrameKind.MIC, 0, 1, 1, 0, bytes(640)))

    def test_tracker_tombstones_final_streams_and_rejects_reuse(self) -> None:
        tracker = AudioFrameTracker(max_terminal_streams=2)
        final = AudioFrame(AudioFrameKind.MIC, 2, 9, 0, 0, bytes(640))

        self.assertEqual(tracker.accept(final), "accepted")
        self.assertEqual(tracker.accept(final), "duplicate")
        with self.assertRaisesRegex(ProtocolError, "reused"):
            tracker.accept(AudioFrame(AudioFrameKind.MIC, 0, 9, 0, 0, bytes(640)))

        tracker.accept(AudioFrame(AudioFrameKind.MIC, 2, 10, 0, 0, bytes(640)))
        tracker.accept(AudioFrame(AudioFrameKind.MIC, 2, 11, 0, 0, bytes(640)))
        self.assertLessEqual(tracker.terminal_stream_count, 2)

        active = AudioFrame(AudioFrameKind.MIC, 0, 12, 0, 0, bytes(640))
        tracker.accept(active)
        tracker.clear(preserve_terminals=True, retire_active=True)
        with self.assertRaisesRegex(ProtocolError, "reused"):
            tracker.accept(active)


class QueueAndWriterTests(unittest.IsolatedAsyncioTestCase):
    async def test_mic_queue_drops_oldest_and_tts_queue_cancels_stream_on_overrun(self) -> None:
        mic = BoundedAudioQueue(2, AudioFrameKind.MIC)
        tts = BoundedAudioQueue(1, AudioFrameKind.TTS)
        frames = [AudioFrame(AudioFrameKind.MIC, 0, 1, i, i * 320, bytes(640)) for i in range(3)]

        self.assertIsNone(mic.put(frames[0]))
        self.assertIsNone(mic.put(frames[1]))
        self.assertEqual(mic.put(frames[2]), "drop_oldest")
        recovered = await mic.get()
        self.assertEqual(recovered.frame_seq, 1)
        self.assertTrue(recovered.discontinuity)

        first = AudioFrame(AudioFrameKind.TTS, 0, 8, 0, 0, bytes(960))
        second = AudioFrame(AudioFrameKind.TTS, 0, 8, 1, 480, bytes(960))
        self.assertIsNone(tts.put(first))
        self.assertEqual(tts.put(second), "cancel_stream")
        self.assertTrue(tts.empty())

    async def test_writer_always_sends_queued_control_before_audio_without_polling(self) -> None:
        sent: list[object] = []

        async def send(value: object) -> None:
            sent.append(value)

        writer = OutboundWriter(send)
        writer.queue_audio(b"audio")
        writer.queue_control({"type": "state.snapshot", "seq": 2})
        await writer.send_next()
        await writer.send_next()

        self.assertEqual(sent[0], {"type": "state.snapshot", "seq": 2})
        self.assertEqual(sent[1], b"audio")

    async def test_writer_cancels_and_flushes_a_tts_stream_at_its_hard_bound(self) -> None:
        async def send(_value: object) -> None:
            return None

        writer = OutboundWriter(send, max_audio_frames=1)

        self.assertIsNone(writer.queue_audio(b"first"))
        self.assertEqual(writer.queue_audio(b"second"), "cancel_stream")
        self.assertEqual(writer.queue_audio(b"replacement"), None)

    async def test_writer_bounds_and_coalesces_diagnostics_but_prioritizes_state(self) -> None:
        sent: list[object] = []

        async def send(value: object) -> None:
            sent.append(value)

        writer = OutboundWriter(send, max_control_events=3)
        for sequence in range(10_000):
            writer.queue_control(
                {"type": "queue.overrun", "seq": sequence},
                diagnostic_key="mic.drop_oldest",
                priority=False,
            )
        writer.queue_control({"type": "state.snapshot", "seq": 10_001})
        await writer.send_next()
        await writer.send_next()

        self.assertEqual([value["type"] for value in sent], ["state.snapshot", "queue.overrun"])
        self.assertLessEqual(writer.pending_control_count, 3)

    async def test_writer_close_fails_an_awaiting_control_and_does_not_swallow_cancel(self) -> None:
        async def send(_value: object) -> None:
            return None

        writer = OutboundWriter(send)
        waiter = asyncio.create_task(writer.send_control({"type": "state.snapshot", "seq": 1}))
        await asyncio.sleep(0)
        writer.close()

        with self.assertRaisesRegex(ConnectionError, "closed"):
            await waiter

    async def test_writer_failure_closes_transport_so_receiver_cannot_be_stranded(self) -> None:
        socket = FakeSocket()
        hub = RealtimeHub(allowed_origins={ORIGIN})
        session = _Session(socket, hub)

        async def fail(_value: object) -> None:
            raise OSError("transport failed")

        session.writer = OutboundWriter(fail)
        session.writer.queue_control({"type": "state.snapshot", "seq": 1})
        with self.assertRaises(OSError):
            await _run_writer(session)

        self.assertEqual(socket.closed, [(1011, "realtime writer failed")])

    async def test_writer_close_fails_a_control_waiter_already_inside_the_send_port(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def send(_value: object) -> None:
            entered.set()
            await release.wait()

        writer = OutboundWriter(send)
        control = asyncio.create_task(writer.send_control({"type": "state.snapshot", "seq": 1}))
        sending = asyncio.create_task(writer.send_next())
        await entered.wait()
        writer.close()

        with self.assertRaisesRegex(ConnectionError, "closed"):
            await control
        release.set()
        await sending


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[object] = []
        self.closed: list[tuple[int, str]] = []

    async def send_json(self, value: object) -> None:
        self.sent.append(value)

    async def send_bytes(self, value: bytes) -> None:
        self.sent.append(value)

    async def close(self, *, code: int, reason: str) -> None:
        self.closed.append((code, reason))


class BlockingSocket(FakeSocket):
    def __init__(self) -> None:
        super().__init__()
        self.send_entered = asyncio.Event()
        self.send_release = asyncio.Event()

    async def send_json(self, value: object) -> None:
        self.send_entered.set()
        await self.send_release.wait()
        self.sent.append(value)

    async def close(self, *, code: int, reason: str) -> None:
        self.closed.append((code, reason))
        self.send_release.set()


class PublishingSocket(FakeSocket):
    def __init__(self) -> None:
        super().__init__()
        self.send_number = 0
        self.entered = [asyncio.Event(), asyncio.Event()]
        self.release = [asyncio.Event(), asyncio.Event()]

    async def send_json(self, value: object) -> None:
        index = self.send_number
        self.send_number += 1
        self.entered[index].set()
        await self.release[index].wait()
        self.sent.append(value)


class BlockingCloseSocket(FakeSocket):
    def __init__(self) -> None:
        super().__init__()
        self.close_entered = asyncio.Event()
        self.close_release = asyncio.Event()

    async def close(self, *, code: int, reason: str) -> None:
        self.close_entered.set()
        await self.close_release.wait()
        self.closed.append((code, reason))


class FailingRouteSocket(FakeSocket):
    def __init__(self) -> None:
        super().__init__()
        self.receiver_cancelled = False

    async def send_json(self, _value: object) -> None:
        raise OSError("writer failed")

    async def receive(self) -> dict[str, object]:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.receiver_cancelled = True
            raise


class LeaseRaceRuntime(FakeRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.text_entered = asyncio.Event()
        self.text_release = asyncio.Event()
        self.ack_entered = asyncio.Event()
        self.ack_release = asyncio.Event()

    async def submit_text(self, text: str) -> None:
        self.calls.append(("text.begin", text))
        self.text_entered.set()
        await self.text_release.wait()
        self.calls.append(("text.end", text))

    async def renderer_ack(self, clause_id: str, *, complete: bool) -> bool:
        self.calls.append(("ack.begin", clause_id, complete))
        self.ack_entered.set()
        await self.ack_release.wait()
        self.calls.append(("ack.end", clause_id, complete))
        return True


class RealtimeLeaseRaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.runtime = LeaseRaceRuntime()
        self.hub = RealtimeHub(runtime=self.runtime, allowed_origins={ORIGIN})
        self.sessions: list[_Session] = []

    async def asyncTearDown(self) -> None:
        for session in self.sessions:
            session.writer.close()
            if session.writer_task is not None:
                session.writer_task.cancel()
        await asyncio.gather(
            *(session.writer_task for session in self.sessions if session.writer_task is not None),
            return_exceptions=True,
        )

    async def session(self) -> _Session:
        session = _Session(FakeSocket(), self.hub)
        session.writer_task = asyncio.create_task(session.writer.run())
        session.client_id = str(uuid.uuid4())
        session.mode = "voice"
        session.opened = True
        await self.hub.claim(session)
        session.ready = True
        self.sessions.append(session)
        return session

    async def test_delayed_runtime_mutation_serializes_replacement_and_stale_followups_fail(self) -> None:
        old = await self.session()
        command = {
            "type": "text.submit", "seq": 2, "command_id": str(uuid.uuid4()), "text": "old"
        }
        submit = asyncio.create_task(_handle_control(old, command))
        await self.runtime.text_entered.wait()

        replacement = asyncio.create_task(self.session())
        await asyncio.sleep(0)
        self.assertFalse(replacement.done())
        self.runtime.text_release.set()
        await submit
        new = await replacement

        with self.assertRaisesRegex(ProtocolError, "replaced"):
            await _handle_control(old, {
                "type": "text.submit", "seq": 3, "command_id": str(uuid.uuid4()), "text": "stale"
            })
        with self.assertRaisesRegex(ProtocolError, "replaced"):
            await _handle_audio(old, pack_audio_frame(
                AudioFrame(AudioFrameKind.MIC, 0, 1, 0, 0, bytes(640))
            ))
        self.assertIs(self.hub._session, new)
        self.assertNotIn(("text.begin", "stale"), self.runtime.calls)

    async def test_three_claimants_leave_only_last_owner_and_old_release_cannot_clear_it(self) -> None:
        first = await self.session()
        second_task = asyncio.create_task(self.session())
        third_task = asyncio.create_task(self.session())
        second, third = await asyncio.gather(second_task, third_task)
        self.assertIs(self.hub._session, third)
        self.assertGreater(third.epoch, second.epoch)

        third.mic_active = True
        self.hub.mic_queue.put(AudioFrame(AudioFrameKind.MIC, 0, 8, 0, 0, bytes(640)))
        disconnected_before = self.runtime.calls.count(("renderer_disconnected",))
        await self.hub.release(first)

        self.assertEqual(len(self.hub.mic_queue), 1)
        self.assertIs(self.hub._session, third)
        self.assertEqual(self.runtime.calls.count(("renderer_disconnected",)), disconnected_before)

    async def test_candidate_pauses_without_settlement_and_replacement_resets_it(self) -> None:
        old = await self.session()
        old.mic_active = True
        old.register_clause_offer("known")
        await _handle_control(old, {
            "type": "speech.candidate", "seq": 2, "command_id": str(uuid.uuid4())
        })
        self.assertIn(("vad_start",), self.runtime.calls)
        self.assertNotIn(("ack.begin", "known", False), self.runtime.calls)
        self.assertIn("known", old.offered_clauses)

        replacement = asyncio.create_task(self.session())
        await self.runtime.ack_entered.wait()
        self.assertFalse(replacement.done())
        self.runtime.ack_release.set()
        new = await replacement

        self.assertIn(("speech_rejected",), self.runtime.calls)
        self.assertFalse(new.speech_candidate)
        self.assertFalse(self.hub.snapshot(new)["speech_candidate"])

    async def test_confirmed_barge_in_renderer_reentry_never_deadlocks_lease(self) -> None:
        class Output:
            async def synthesize(self, _text):
                yield bytes(960)

            async def cancel(self):
                return None

        session = await self.session()
        port = await self.hub.acquire_renderer_port()
        assert port is not None
        delivery = ClauseSpeechDelivery(Output(), lambda: port)

        async def confirm_speech(_runtime) -> None:
            await delivery.cancel_current()

        self.runtime.confirm_speech = types.MethodType(confirm_speech, self.runtime)
        self.runtime.ack_release.set()
        playing = asyncio.create_task(delivery(types.SimpleNamespace(
            clause_id="reentrant", text="播放中。", invoke_id="invoke",
            input_id="input", revision=1, motion_token=None, expression_token=None,
        )))
        while "reentrant" not in session.offered_clauses:
            await asyncio.sleep(0)
        session.mic_active = True
        session.speech_candidate = True

        command = asyncio.create_task(_handle_control(session, {
            "type": "speech.confirmed", "seq": 2,
            "command_id": str(uuid.uuid4()), "transcript": "继续说", "complete": True,
        }))
        try:
            await asyncio.wait_for(command, 0.2)
        finally:
            delivery.acknowledge("reentrant", complete=False)
            if not playing.done():
                playing.cancel()
            await asyncio.gather(playing, return_exceptions=True)
            await delivery.shutdown()

    async def test_delayed_complete_ack_is_atomic_with_epoch_replacement(self) -> None:
        old = await self.session()
        old.register_clause_offer("known")
        acknowledge = asyncio.create_task(_handle_control(old, {
            "type": "renderer.ack",
            "seq": 2,
            "command_id": str(uuid.uuid4()),
            "renderer_epoch": old.epoch,
            "clause_id": "known",
            "complete": True,
        }))
        await self.runtime.ack_entered.wait()
        replacement = asyncio.create_task(self.session())
        await asyncio.sleep(0)
        self.assertFalse(replacement.done())

        self.runtime.ack_release.set()
        await acknowledge
        new = await replacement

        self.assertNotIn("known", old.offered_clauses)
        self.assertIs(self.hub._session, new)
        self.assertEqual(self.runtime.calls.count(("ack.begin", "known", True)), 1)

    async def test_only_one_live_mic_stream_is_admitted_and_final_allows_the_next(self) -> None:
        session = await self.session()
        session.mic_active = True
        await _handle_audio(session, pack_audio_frame(
            AudioFrame(AudioFrameKind.MIC, 0, 1, 0, 0, bytes(640))
        ))
        with self.assertRaisesRegex(ProtocolError, "stream conflict"):
            await _handle_audio(session, pack_audio_frame(
                AudioFrame(AudioFrameKind.MIC, 0, 2, 0, 0, bytes(640))
            ))
        await _handle_audio(session, pack_audio_frame(
            AudioFrame(AudioFrameKind.MIC, 2, 1, 1, 320, bytes(640))
        ))
        self.assertIsNone(session.mic_stream_id)
        await _handle_audio(session, pack_audio_frame(
            AudioFrame(AudioFrameKind.MIC, 2, 1, 1, 320, bytes(640))
        ))
        self.assertIsNone(session.mic_stream_id)
        await _handle_audio(session, pack_audio_frame(
            AudioFrame(AudioFrameKind.MIC, 0, 2, 0, 0, bytes(640))
        ))
        self.assertEqual(session.mic_stream_id, 2)

    async def test_final_disconnect_rejects_candidate_once_then_disconnects_runtime(self) -> None:
        session = await self.session()
        session.speech_candidate = True
        await self.hub.release(session)
        await self.hub.release(session)

        self.assertEqual(self.runtime.calls.count(("speech_rejected",)), 1)
        self.assertEqual(self.runtime.calls.count(("renderer_disconnected",)), 1)

    async def test_blocked_old_send_is_revoked_without_holding_replacement_lock(self) -> None:
        socket = BlockingSocket()
        old = _Session(socket, self.hub)
        old.opened = True
        old.writer_task = asyncio.create_task(old.writer.run())
        await self.hub.claim(old)
        self.sessions.append(old)
        blocked = asyncio.create_task(old.control("state.snapshot", renderer_epoch=old.epoch))
        await socket.send_entered.wait()

        new = await asyncio.wait_for(self.session(), timeout=0.2)
        await asyncio.gather(old.writer_task, return_exceptions=True)
        await old.wait_revoked()

        self.assertIs(self.hub._session, new)
        self.assertTrue(old.writer_task.done())
        self.assertTrue(socket.closed)
        with self.assertRaises((ConnectionError, asyncio.CancelledError, ProtocolError)):
            await blocked

    async def test_old_renderer_lease_port_cannot_retarget_new_session_cross_loop(self) -> None:
        old = await self.session()
        port = await self.hub.acquire_renderer_port()
        self.assertIsNotNone(port)
        new = await self.session()
        frame = AudioFrame(AudioFrameKind.TTS, 0, 77, 0, 0, bytes(960))

        stale = await asyncio.to_thread(
            lambda: asyncio.run(port.queue_tts(frame), debug=True)
        )
        current = await self.hub.acquire_renderer_port()
        admitted = await asyncio.to_thread(
            lambda: asyncio.run(current.queue_tts(frame), debug=True)
        )
        clause = types.SimpleNamespace(
            clause_id="known",
            text="hello",
            invoke_id="invoke",
            input_id="input",
            revision=1,
            motion_token=None,
            expression_token=None,
        )
        offered = await asyncio.to_thread(
            lambda: asyncio.run(current.offer_clause(clause), debug=True)
        )
        await asyncio.sleep(0)

        self.assertEqual(stale, "stale_renderer_lease")
        self.assertIsNone(admitted)
        self.assertIsNone(offered)
        self.assertTrue(any(
            isinstance(event, dict) and event.get("type") == "clause.offer"
            for event in new.websocket.sent
        ))
        self.assertIs(self.hub._session, new)

    async def test_cleanup_completes_once_before_release_cancellation_propagates(self) -> None:
        session = await self.session()
        session.speech_candidate = True
        entered = asyncio.Event()
        release = asyncio.Event()

        async def reject() -> None:
            self.runtime.calls.append(("speech_rejected",))
            entered.set()
            await release.wait()

        self.runtime.reject_speech_candidate = reject
        cleanup = asyncio.create_task(self.hub.release(session))
        await entered.wait()
        cleanup.cancel()
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await cleanup
        await self.hub.release(session)

        self.assertIsNone(self.hub._session)
        self.assertEqual(self.runtime.calls.count(("speech_rejected",)), 1)
        self.assertEqual(self.runtime.calls.count(("renderer_disconnected",)), 1)
        self.assertTrue(session.writer_task.done())
        self.assertTrue(session.websocket.closed)

    async def test_replacement_cleanup_and_old_writer_finish_before_cancellation_propagates(self) -> None:
        old = await self.session()
        old.speech_candidate = True
        entered = asyncio.Event()
        release = asyncio.Event()

        async def reject() -> None:
            self.runtime.calls.append(("speech_rejected",))
            entered.set()
            await release.wait()

        self.runtime.reject_speech_candidate = reject
        new = _Session(FakeSocket(), self.hub)
        new.opened = True
        new.writer_task = asyncio.create_task(new.writer.run())
        self.sessions.append(new)
        replacement = asyncio.create_task(self.hub.claim(new))
        await entered.wait()
        replacement.cancel()
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await replacement

        self.assertIs(self.hub._session, new)
        self.assertTrue(old.writer_task.done())
        self.assertEqual(self.runtime.calls.count(("speech_rejected",)), 1)
        self.assertNotIn(("renderer_disconnected",), self.runtime.calls)

    async def test_repeated_mic_start_is_rejected_without_flushing_live_queue(self) -> None:
        session = await self.session()
        first = {"type": "mic.start", "seq": 2, "command_id": str(uuid.uuid4())}
        second = {"type": "mic.start", "seq": 3, "command_id": str(uuid.uuid4())}
        await _handle_control(session, first)
        frame = AudioFrame(AudioFrameKind.MIC, 0, 4, 0, 0, bytes(640))
        await _handle_audio(session, pack_audio_frame(frame))
        await _handle_control(session, second)
        await asyncio.sleep(0)

        acks = [event for event in session.websocket.sent if isinstance(event, dict) and event.get("type") == "command.ack"]
        self.assertEqual(acks[-1]["status"], "rejected")
        self.assertEqual(acks[-1]["code"], "mic_already_active")
        self.assertEqual(len(self.hub.mic_queue), 1)

    async def test_release_cancellation_waits_for_transport_settlement_then_propagates(self) -> None:
        socket = BlockingCloseSocket()
        session = _Session(socket, self.hub)
        session.opened = True
        session.ready = True
        session.writer_task = asyncio.create_task(session.writer.run())
        await self.hub.claim(session)
        self.sessions.append(session)

        releasing = asyncio.create_task(self.hub.release(session))
        await socket.close_entered.wait()
        revoke_waiter = asyncio.create_task(session.wait_revoked())
        revoke_waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await revoke_waiter
        self.assertFalse(session._revoke_result.cancelled())
        releasing.cancel()
        await asyncio.sleep(0)
        self.assertFalse(releasing.done())
        releasing.cancel()
        await asyncio.sleep(0)
        self.assertFalse(releasing.done())
        socket.close_release.set()
        with self.assertRaises(asyncio.CancelledError):
            await releasing

        self.assertTrue(session.writer_task.done())
        self.assertIsNone(self.hub._session)

    async def test_renderer_port_is_unavailable_until_opened_and_snapshot_are_published(self) -> None:
        socket = PublishingSocket()
        session = _Session(socket, self.hub)
        session.writer_task = asyncio.create_task(session.writer.run())
        self.sessions.append(session)
        opening = asyncio.create_task(_open_session(session, {
            "type": "session.open",
            "seq": 1,
            "command_id": str(uuid.uuid4()),
            "client_id": str(uuid.uuid4()),
            "mode": "voice",
        }))
        await socket.entered[0].wait()
        self.assertIsNone(await self.hub.acquire_renderer_port())
        socket.release[0].set()
        await socket.entered[1].wait()
        self.assertIsNone(await self.hub.acquire_renderer_port())
        socket.release[1].set()
        await opening

        self.assertIsNotNone(await self.hub.acquire_renderer_port())

    async def test_failed_offer_enqueue_after_replacement_interrupts_clause_exactly_once(self) -> None:
        old = await self.session()
        port = await self.hub.acquire_renderer_port()
        entered = asyncio.Event()
        release = asyncio.Event()
        original = old.queue_control

        async def delayed(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        old.queue_control = delayed
        clause = types.SimpleNamespace(
            clause_id="known", text="hello", invoke_id="invoke", input_id="input",
            revision=1, motion_token=None, expression_token=None,
        )
        offering = asyncio.create_task(port.offer_clause(clause))
        await entered.wait()
        self.runtime.ack_release.set()
        await self.session()
        release.set()
        self.assertEqual(await offering, "stale_renderer_lease")

        self.assertEqual(self.runtime.calls.count(("ack.begin", "known", False)), 1)

    async def test_failed_offer_enqueue_while_current_interrupts_and_settles_once(self) -> None:
        session = await self.session()
        port = await self.hub.acquire_renderer_port()
        session.queue_control = types.MethodType(
            lambda _self, *args, **kwargs: asyncio.sleep(0, result=False), session
        )
        self.runtime.ack_release.set()
        clause = types.SimpleNamespace(
            clause_id="known", text="hello", invoke_id="invoke", input_id="input",
            revision=1, motion_token=None, expression_token=None,
        )

        self.assertEqual(await port.offer_clause(clause), "offer_enqueue_failed")
        self.assertNotIn("known", session.offered_clauses)
        self.assertEqual(self.runtime.calls.count(("ack.begin", "known", False)), 1)

    async def test_revoking_a_session_whose_owner_loop_is_closed_completes_locally(self) -> None:
        holder: list[_Session] = []

        def build_closed_owner() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            async def build() -> None:
                session = _Session(FakeSocket(), self.hub)
                session.opened = True
                session.ready = True
                holder.append(session)

            loop.run_until_complete(build())
            loop.close()

        thread = threading.Thread(target=build_closed_owner)
        thread.start()
        thread.join()
        old = holder[0]
        old.epoch = 1
        self.hub._session = old
        self.hub._epoch = 1

        new = await self.session()
        await old.wait_revoked()

        self.assertIs(self.hub._session, new)
        self.assertTrue(old._transport_close_started)


class RouteSupervisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_writer_failure_cancels_blocked_receiver_and_route_finishes(self) -> None:
        socket = FailingRouteSocket()
        hub = RealtimeHub(allowed_origins={ORIGIN})
        session = _Session(socket, hub)
        session.opened = True
        await hub.claim(session)
        session.writer.queue_control({"type": "state.snapshot", "seq": 1})

        await asyncio.wait_for(_supervise_session(session), timeout=0.2)

        self.assertTrue(socket.receiver_cancelled)
        self.assertIn((1011, "realtime writer failed"), socket.closed)

    async def test_parent_cancellation_settles_both_children_then_propagates(self) -> None:
        socket = FailingRouteSocket()
        hub = RealtimeHub(allowed_origins={ORIGIN})
        session = _Session(socket, hub)
        supervised = asyncio.create_task(_supervise_session(session))
        await asyncio.sleep(0)
        supervised.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await supervised
        self.assertTrue(session.writer_task.done())
        self.assertTrue(socket.receiver_cancelled)


class OriginConfigurationTests(unittest.TestCase):
    def test_explicit_empty_origins_is_deny_all_while_none_uses_defaults(self) -> None:
        self.assertTrue(RealtimeHub(allowed_origins=None).origin_allowed(ORIGIN))
        self.assertFalse(RealtimeHub(allowed_origins=set()).origin_allowed(ORIGIN))


class CleanWebSocketContext:
    """Adapt Starlette's legacy TestClient teardown to structured child-task WebSocket routes."""

    def __init__(self, context) -> None:
        self.context = context

    def __enter__(self):
        return self.context.__enter__()

    def __exit__(self, exc_type, exc, traceback):
        try:
            return self.context.__exit__(exc_type, exc, traceback)
        except concurrent.futures.CancelledError:
            return exc_type is None


class RealtimeGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = FakeRuntime()
        self.clock = Clock()
        self.hub = RealtimeHub(
            runtime=self.runtime,
            allowed_origins={ORIGIN},
            token_store=CapabilityTokens(ttl_seconds=5, clock=self.clock),
        )
        self.client = TestClient(create_app(realtime_hub=self.hub))

    def tearDown(self) -> None:
        self.client.close()

    def token(self) -> str:
        response = self.client.post(
            "/api/v1/main-agent/realtime-token", headers={"origin": ORIGIN}
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["access-control-allow-origin"], ORIGIN)
        return response.json()["token"]

    def connect(self, token: str | None = None):
        capability = token or self.token()
        return CleanWebSocketContext(self.client.websocket_connect(
            "/api/v1/main-agent/realtime",
            subprotocols=[PROTOCOL, f"aurabot.capability.{capability}"],
            headers={"origin": ORIGIN},
        ))

    def open(self, websocket, *, mode: str = "voice") -> dict:
        websocket.send_json({
            "type": "session.open",
            "seq": 1,
            "command_id": str(uuid.uuid4()),
            "client_id": str(uuid.uuid4()),
            "mode": mode,
        })
        opened = websocket.receive_json()
        self.assertEqual(opened["type"], "session.opened")
        snapshot = websocket.receive_json()
        self.assertEqual(snapshot["type"], "state.snapshot")
        return opened

    def test_token_endpoint_and_websocket_enforce_origin_subprotocol_expiry_and_reuse(self) -> None:
        self.assertEqual(
            self.client.post("/api/v1/main-agent/realtime-token", headers={"origin": "http://evil.invalid"}).status_code,
            403,
        )
        token = self.token()
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect(
                "/api/v1/main-agent/realtime",
                subprotocols=[f"aurabot.capability.{token}"],
                headers={"origin": ORIGIN},
            ):
                pass
        with self.connect(token) as websocket:
            self.open(websocket)
        with self.assertRaises(WebSocketDisconnect):
            with self.connect(token):
                pass
        expired = self.token()
        self.clock.now += 6
        with self.assertRaises(WebSocketDisconnect):
            with self.connect(expired):
                pass

    def test_session_open_is_required_validated_and_snapshot_is_authoritative(self) -> None:
        with self.connect() as websocket:
            websocket.send_json({"type": "mic.start", "seq": 1})
            error = websocket.receive_json()
            self.assertEqual((error["type"], error["code"]), ("session.error", "session_open_required"))
            with self.assertRaises(WebSocketDisconnect):
                websocket.receive_json()

        with self.connect() as websocket:
            opened = self.open(websocket, mode="text")
            self.assertEqual(opened["protocol"], PROTOCOL)
            self.assertEqual(websocket.accepted_subprotocol, PROTOCOL)

    def test_command_uuid_idempotency_and_monotonic_json_sequence(self) -> None:
        command_id = str(uuid.uuid4())
        with self.connect() as websocket:
            self.open(websocket)
            command = {"type": "text.submit", "seq": 2, "command_id": command_id, "text": "hello"}
            websocket.send_json(command)
            first = websocket.receive_json()
            command["seq"] = 3
            websocket.send_json(command)
            duplicate = websocket.receive_json()
            self.assertEqual(first["status"], "accepted")
            self.assertEqual(duplicate["status"], "duplicate")
            self.assertEqual(self.runtime.calls, [("text", "hello")])
            websocket.send_json({"type": "mic.stop", "seq": 3, "command_id": str(uuid.uuid4())})
            self.assertEqual(websocket.receive_json()["code"], "sequence_not_monotonic")

    def test_command_uuid_reuse_with_different_payload_is_rejected_as_conflict(self) -> None:
        command_id = str(uuid.uuid4())
        with self.connect() as websocket:
            self.open(websocket)
            websocket.send_json({"type": "text.submit", "seq": 2, "command_id": command_id, "text": "one"})
            websocket.receive_json()
            websocket.send_json({"type": "text.submit", "seq": 3, "command_id": command_id, "text": "two"})
            conflict = websocket.receive_json()

        self.assertEqual((conflict["status"], conflict["code"]), ("rejected", "command_id_conflict"))
        self.assertEqual(self.runtime.calls.count(("text", "one")), 1)
        self.assertNotIn(("text", "two"), self.runtime.calls)

    def test_replacement_lease_increments_epoch_and_fences_old_ack(self) -> None:
        first_context = self.connect()
        first = first_context.__enter__()
        first_open = self.open(first)
        try:
            with self.connect() as second:
                second_open = self.open(second)
                self.assertGreater(second_open["renderer_epoch"], first_open["renderer_epoch"])
                replaced = first.receive_json()
                self.assertEqual(replaced["type"], "session.replaced")
                second.send_json({
                    "type": "renderer.ack",
                    "seq": 2,
                    "command_id": str(uuid.uuid4()),
                    "renderer_epoch": first_open["renderer_epoch"],
                    "clause_id": "known",
                    "complete": True,
                })
                ack = second.receive_json()
                self.assertEqual((ack["type"], ack["status"], ack["code"]), (
                    "command.ack", "rejected", "stale_renderer_epoch",
                ))
        finally:
            first_context.__exit__(None, None, None)

    def test_unknown_clause_ack_is_rejected_and_disconnect_cleans_runtime_state(self) -> None:
        with self.connect() as websocket:
            opened = self.open(websocket)
            websocket.send_json({"type": "mic.start", "seq": 2, "command_id": str(uuid.uuid4())})
            self.assertEqual(websocket.receive_json()["status"], "accepted")
            websocket.send_json({
                "type": "renderer.ack",
                "seq": 3,
                "command_id": str(uuid.uuid4()),
                "renderer_epoch": opened["renderer_epoch"],
                "clause_id": "missing",
                "complete": True,
            })
            self.assertEqual(websocket.receive_json()["code"], "unknown_clause")
        self.assertIn(("renderer_disconnected",), self.runtime.calls)
        self.assertFalse(self.hub.connected)

    def test_unknown_interrupted_clause_ack_is_also_rejected(self) -> None:
        with self.connect() as websocket:
            opened = self.open(websocket)
            websocket.send_json({
                "type": "renderer.ack",
                "seq": 2,
                "command_id": str(uuid.uuid4()),
                "renderer_epoch": opened["renderer_epoch"],
                "clause_id": "missing",
                "complete": False,
            })
            self.assertEqual(websocket.receive_json()["code"], "unknown_clause")

    def test_mode_switch_flushes_mic_and_emits_playback_stop(self) -> None:
        with self.connect() as websocket:
            opened = self.open(websocket)
            websocket.send_json({"type": "mic.start", "seq": 2, "command_id": str(uuid.uuid4())})
            websocket.receive_json()
            websocket.send_bytes(pack_audio_frame(
                AudioFrame(AudioFrameKind.MIC, 0, 4, 0, 0, bytes(640))
            ))
            websocket.send_json({
                "type": "mode.set",
                "seq": 3,
                "command_id": str(uuid.uuid4()),
                "renderer_epoch": opened["renderer_epoch"],
                "mode": "text",
            })
            events = [websocket.receive_json(), websocket.receive_json(), websocket.receive_json()]
            self.assertEqual(
                [event["type"] for event in events],
                ["playback.stop", "state.snapshot", "command.ack"],
            )
            self.assertEqual(self.hub.mode, "text")

    def test_speech_candidate_pauses_playback_before_ack_and_blocks_tts(self) -> None:
        with self.connect() as websocket:
            self.open(websocket)
            websocket.send_json({"type": "mic.start", "seq": 2, "command_id": str(uuid.uuid4())})
            websocket.receive_json()
            websocket.send_json({"type": "speech.candidate", "seq": 3, "command_id": str(uuid.uuid4())})
            events = [websocket.receive_json(), websocket.receive_json()]

            self.assertEqual([event["type"] for event in events], ["playback.pause", "command.ack"])
            frame = AudioFrame(AudioFrameKind.TTS, 0, 12, 0, 0, bytes(960))
            port = websocket.portal.call(self.hub.acquire_renderer_port)
            self.assertEqual(
                websocket.portal.call(port.queue_tts, frame), "playback_blocked"
            )
            self.assertIn(("vad_start",), self.runtime.calls)
            self.assertFalse(any(call[:1] == ("ack",) for call in self.runtime.calls))

            websocket.send_json({
                "type": "speech.rejected", "seq": 4, "command_id": str(uuid.uuid4())
            })
            events = [websocket.receive_json(), websocket.receive_json(), websocket.receive_json()]
            self.assertEqual(
                [event["type"] for event in events],
                ["playback.resume", "state.snapshot", "command.ack"],
            )
            self.assertIsNone(websocket.portal.call(port.queue_tts, frame))

    def test_backend_vad_lifecycle_controls_the_same_renderer_playback_gate(self) -> None:
        with self.connect() as websocket:
            self.open(websocket)
            websocket.send_json({"type": "mic.start", "seq": 2, "command_id": str(uuid.uuid4())})
            websocket.receive_json()

            websocket.portal.call(self.hub.speech_candidate_started)
            self.assertEqual(websocket.receive_json()["type"], "playback.pause")
            self.assertTrue(self.hub._session.playback_blocked)

            websocket.portal.call(self.hub.speech_candidate_rejected)
            rejected = [websocket.receive_json(), websocket.receive_json()]
            self.assertEqual(
                [event["type"] for event in rejected],
                ["playback.resume", "state.snapshot"],
            )
            self.assertFalse(self.hub._session.playback_blocked)

            websocket.portal.call(self.hub.speech_candidate_started)
            self.assertEqual(websocket.receive_json()["type"], "playback.pause")
            websocket.portal.call(self.hub.speech_confirmed)
            self.assertEqual(websocket.receive_json()["type"], "playback.stop")

            websocket.portal.call(lambda: self.hub.speech_endpoint(complete=False))
            self.assertEqual(websocket.receive_json()["type"], "state.snapshot")
            self.assertTrue(self.hub._session.playback_blocked)

            websocket.portal.call(lambda: self.hub.speech_endpoint(complete=True))
            completed = [websocket.receive_json(), websocket.receive_json()]
            self.assertEqual(
                [event["type"] for event in completed],
                ["playback.resume", "state.snapshot"],
            )
            self.assertFalse(self.hub._session.playback_blocked)

    def test_mic_stop_without_candidate_does_not_forge_a_vad_endpoint(self) -> None:
        with self.connect() as websocket:
            self.open(websocket)
            websocket.send_json({"type": "mic.start", "seq": 2, "command_id": str(uuid.uuid4())})
            websocket.receive_json()
            websocket.send_json({"type": "mic.stop", "seq": 3, "command_id": str(uuid.uuid4())})
            self.assertEqual(websocket.receive_json()["status"], "accepted")

        self.assertNotIn(("vad_stop",), self.runtime.calls)

    def test_speech_rejection_requires_a_live_candidate(self) -> None:
        with self.connect() as websocket:
            self.open(websocket)
            websocket.send_json({"type": "speech.rejected", "seq": 2, "command_id": str(uuid.uuid4())})
            rejected = websocket.receive_json()

        self.assertEqual((rejected["status"], rejected["code"]), (
            "rejected", "speech_candidate_required",
        ))
        self.assertNotIn(("speech_rejected",), self.runtime.calls)


if __name__ == "__main__":
    unittest.main()
