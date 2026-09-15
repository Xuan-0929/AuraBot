"""Exercises the unreleased minimal Pet voice protocol through its token, hub, and WebSocket boundaries.

The suite checks acoustic listening edges independently of voice enablement and work settlement.
It also drives bounded PCM credit, late receipts, socket cancellation ordering and redacted device settings.
The suite protects Profile/Soul activation, Origin/generation/single-use capabilities, raw 20 ms microphone
frames, one current clause, raw TTS plus audio_end, physical ACK, playback gating, replacement, and bounded
drop-oldest input. Removed AUR1 coordinates, commands, epochs, modes, diagnostics, and cross-loop machinery
have no compatibility tests.
"""

from __future__ import annotations

import asyncio
import unittest
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.main_agent.realtime import (
    CapabilityTokens,
    MicFrameQueue,
    ProtocolError,
    RealtimeHub,
    SUBPROTOCOL,
    router,
)
from backend.main_agent.runtime import RendererClause
from backend.main_agent.lifecycle import ProfileResources


ORIGIN = "http://127.0.0.1:6400"


class FakeRuntime:
    def __init__(self) -> None:
        self.acks: list[tuple[str, bool]] = []
        self.disconnects = 0

    async def renderer_disconnected(self) -> None:
        self.disconnects += 1

    async def renderer_ack(self, clause_id: str, *, complete: bool, mode: str = "voice") -> bool:
        self.acks.append((clause_id, complete))
        return complete


class FakeSpeech:
    def __init__(self) -> None:
        self.acks: list[tuple[str, bool]] = []
        self.cancelled: list[str] = []

    async def shutdown(self) -> None:
        await self.cancel_current("speech_shutdown")

    async def startup(self) -> None:
        pass

    def acknowledge(self, clause_id: str, *, complete: bool) -> None:
        self.acks.append((clause_id, complete))

    async def cancel_current(self, reason: str) -> None:
        self.cancelled.append(reason)


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[Any] = []
        self.closed = False

    async def send_json(self, value: dict[str, Any]) -> None:
        self.sent.append(value)

    async def send_bytes(self, value: bytes) -> None:
        self.sent.append(value)

    async def close(self, **_kwargs: Any) -> None:
        self.closed = True


class CapabilityTests(unittest.TestCase):
    def test_token_is_single_use_and_bound_to_origin_profile_and_generation(self) -> None:
        tokens = CapabilityTokens(ttl_seconds=15)
        token = tokens.issue(ORIGIN, "profile_default", 7)
        record = tokens.consume(token, ORIGIN, active_profile_id="profile_default", active_generation=7)
        self.assertEqual((record.profile_id, record.generation), ("profile_default", 7))
        self.assertIsNone(tokens.consume(token, ORIGIN, active_profile_id="profile_default", active_generation=7))
        stale = tokens.issue(ORIGIN, "profile_default", 7)
        self.assertIsNone(tokens.consume(stale, ORIGIN, active_profile_id="profile_other", active_generation=8))

    def test_mic_queue_drops_oldest_and_accepts_only_exact_raw_frame(self) -> None:
        queue = MicFrameQueue(max_frames=2)
        self.assertFalse(queue.put(b"a" * 640))
        self.assertFalse(queue.put(b"b" * 640))
        self.assertTrue(queue.put(b"c" * 640))
        self.assertEqual(queue.get_nowait(), b"b" * 640)
        with self.assertRaises(ValueError):
            queue.put(b"short")


class HubTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.runtime = FakeRuntime()
        self.speech = FakeSpeech()
        self.hub = RealtimeHub(allowed_origins={ORIGIN})
        await self.hub.set_active("profile_default", 1, ProfileResources("profile_default", self.runtime, self.speech, None))

    async def test_voice_settings_accept_only_content_free_device_properties(self):
        socket = FakeSocket()
        session = await self.hub.attach(socket)
        await self.hub.command(session, {"type": "voice.start", "command_id": "settings", "audio_settings": {
            "sampleRate": 48000, "autoGainControl": False, "echoCancellation": True,
        }})
        self.assertEqual(socket.sent[-1]["status"], "accepted")
        await self.hub.command(session, {"type": "voice.start", "command_id": "invalid", "audio_settings": {"deviceId": "private"}})
        self.assertEqual(socket.sent[-1]["status"], "rejected")

    async def test_audio_window_waits_for_consumption_and_cancel_releases_sender(self):
        socket = FakeSocket()
        session = await self.hub.attach(socket)
        session.mode = "voice"
        port = await self.hub.acquire_renderer_port()
        await port.offer_clause(RendererClause("window", "hello", "invoke", "input", 1))
        pcm = b"\x01\x00" * 240_000
        sending = asyncio.create_task(port.send_audio(pcm))
        try:
            await asyncio.sleep(.01)
            self.assertFalse(sending.done(), "ten seconds of PCM must not overrun the two-second window")
            self.assertEqual(sum(len(x) for x in socket.sent if isinstance(x, bytes)), 96_000)
            await self.hub.renderer_progress(session, "window", 24_000)
            await asyncio.sleep(.01)
            self.assertEqual(sum(len(x) for x in socket.sent if isinstance(x, bytes)), 144_000)
            await port.cancel_clause("window", "test_cancel")
            self.assertEqual(await asyncio.wait_for(sending, .2), "unknown_clause")
            await port.offer_clause(RendererClause("next", "hello", "invoke", "input", 1))
            await self.hub.renderer_progress(session, "window", 48_000)
            self.assertFalse(await self.hub.renderer_ack(session, "window", complete=False))
            self.assertEqual(session.current_clause, "next")
        finally:
            sending.cancel()
            await asyncio.gather(sending, return_exceptions=True)

    async def test_audio_window_streams_complete_burst_without_loss(self):
        socket = FakeSocket()
        session = await self.hub.attach(socket)
        session.mode = "voice"
        port = await self.hub.acquire_renderer_port()
        await port.offer_clause(RendererClause("burst", "hello", "invoke", "input", 1))
        pcm = bytes(range(256)) * 1875  # Exactly ten seconds of 24 kHz PCM16.
        sending = asyncio.create_task(port.send_audio(pcm))
        try:
            consumed = 0
            for _ in range(100):
                await asyncio.sleep(0)
                sent = sum(len(x) for x in socket.sent if isinstance(x, bytes)) // 2
                self.assertLessEqual(sent - consumed, 48_000)
                consumed = sent
                await self.hub.renderer_progress(session, "burst", consumed)
                if sending.done():
                    break
            self.assertIsNone(await asyncio.wait_for(sending, .2))
            self.assertEqual(b"".join(x for x in socket.sent if isinstance(x, bytes)), pcm)
            with self.assertRaises(ProtocolError):
                await self.hub.renderer_progress(session, "burst", consumed + 1)
        finally:
            sending.cancel()
            await asyncio.gather(sending, return_exceptions=True)

    async def test_audio_waiting_on_socket_cannot_follow_its_cancel_marker(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class SlowCancelSocket(FakeSocket):
            async def send_json(self, value):
                if value["type"] == "clause.cancel":
                    entered.set()
                    await release.wait()
                await super().send_json(value)
        socket = SlowCancelSocket()
        session = await self.hub.attach(socket)
        session.mode = "voice"
        port = await self.hub.acquire_renderer_port()
        await port.offer_clause(RendererClause("race", "hello", "invoke", "input", 1))
        cancelling = asyncio.create_task(port.cancel_clause("race", "test_cancel"))
        await entered.wait()
        sending = asyncio.create_task(port.send_audio(b"\x01\x00" * 4800))
        await asyncio.sleep(0)
        release.set()
        try:
            await cancelling
            self.assertEqual(await sending, "unknown_clause")
            self.assertFalse(any(isinstance(value, bytes) for value in socket.sent))
        finally:
            release.set()
            await asyncio.gather(cancelling, sending, return_exceptions=True)

    async def test_listening_tracks_acoustic_activity_and_preserves_work(self) -> None:
        socket = FakeSocket()
        session = await self.hub.attach(socket)
        session.mode = "voice"
        def state():
            return [e["state"] for e in socket.sent if isinstance(e, dict) and e["type"] == "agent.state"][-1]
        await self.hub.agent_state("idle")
        self.assertEqual(state(), "no_audio")
        await self.hub.speech_candidate_started()
        self.assertEqual(state(), "listening")
        await self.hub.agent_state("idle")
        self.assertEqual(state(), "listening")
        await self.hub.speech_activity(active=False)
        self.assertEqual(state(), "no_audio")
        await self.hub.agent_state("thinking")
        await self.hub.speech_candidate_started()
        await self.hub.speech_candidate_rejected()
        self.assertEqual(state(), "thinking")
        await self.hub.speech_activity(active=True)
        await self.hub.speech_activity(active=False)
        await self.hub.speech_confirmed()  # Late transcription is not a VAD start.
        self.assertEqual(state(), "thinking")
        await self.hub.agent_state("idle")
        self.assertEqual(state(), "no_audio")
        session.mode = "text"
        await self.hub.agent_state("idle")
        self.assertEqual(state(), "idle")

    async def test_voice_barge_in_noise_and_replacement_have_no_stale_listening(self) -> None:
        socket = FakeSocket()
        session = await self.hub.attach(socket)
        session.mode = "voice"
        port = await self.hub.acquire_renderer_port()
        await port.offer_clause(RendererClause("c-vad", "hello", "invoke", "input", 1))
        def state():
            return [e["state"] for e in socket.sent if isinstance(e, dict) and e["type"] == "agent.state"][-1]
        await self.hub.speech_candidate_started()
        self.assertEqual(state(), "listening")
        await self.hub.speech_candidate_rejected()
        self.assertEqual(state(), "speaking")
        self.assertEqual(session.current_clause, "c-vad")
        await self.hub.speech_candidate_started()
        await self.hub.speech_confirmed()
        self.assertEqual(state(), "listening")
        self.assertIsNone(session.current_clause)
        await self.hub.speech_activity(active=False)
        self.assertEqual(state(), "no_audio")
        await self.hub.speech_candidate_started()
        replacement = FakeSocket()
        fresh = await self.hub.attach(replacement)
        fresh.mode = "voice"
        await self.hub.agent_state("idle")
        self.assertEqual(replacement.sent[-1], {"type": "agent.state", "state": "no_audio"})

    async def test_port_sends_one_clause_raw_audio_end_and_physical_ack(self) -> None:
        socket = FakeSocket()
        session = await self.hub.attach(socket)
        session.mode = "voice"
        port = await self.hub.acquire_renderer_port()
        assert port is not None
        clause = RendererClause("c1", "hello", "invoke", "input", 1)
        await port.offer_clause(clause)
        await port.send_audio(b"\0\0" * 480)
        await port.finish_audio("c1")
        await self.hub.renderer_ack(session, "c1", complete=True)

        self.assertEqual(socket.sent[0], {"type": "session.ready", "profile_id": "profile_default", "generation": 1, "config_revision": 0})
        self.assertEqual(socket.sent[1], {"type": "clause.offer", "clause_id": "c1", "text": "hello", "mode": "voice",
            "profile_id": "profile_default", "generation": 1, "config_revision": 0, "say_id": "", "call_id": "", "invoke_id": "invoke", "avatar": {}})
        self.assertEqual(socket.sent[3], b"\0\0" * 480)
        self.assertEqual(socket.sent[4], {"type": "clause.audio_end", "clause_id": "c1"})
        self.assertEqual(self.runtime.acks, [("c1", True)])
        self.assertEqual(self.speech.acks, [("c1", True)])

    async def test_voice_complete_ack_is_rejected_until_audio_end(self) -> None:
        session = await self.hub.attach(FakeSocket())
        session.mode = "voice"
        port = await self.hub.acquire_renderer_port()
        await port.offer_clause(RendererClause("voice", "Hello", "invoke", "input", 1))
        with self.assertRaisesRegex(ProtocolError, "audio_incomplete"):
            await self.hub.renderer_ack(session, "voice", complete=True)
        self.assertEqual(session.current_clause, "voice")
        self.assertEqual(self.runtime.acks, [])
        await port.finish_audio("voice")
        self.assertTrue(await self.hub.renderer_ack(session, "voice", complete=True))

    async def test_playback_gate_and_confirmed_barge_in_cancel_current_clause(self) -> None:
        socket = FakeSocket()
        await self.hub.attach(socket)
        port = await self.hub.acquire_renderer_port()
        assert port is not None
        await port.offer_clause(RendererClause("c1", "hello", "invoke", "input", 1))
        await self.hub.speech_candidate_started()
        await self.hub.speech_candidate_rejected()
        await self.hub.speech_candidate_started()
        await self.hub.speech_confirmed()

        self.assertEqual([event["type"] for event in socket.sent if isinstance(event, dict)], [
            "session.ready", "clause.offer", "agent.state", "playback.resume", "clause.cancel", "agent.state",
        ])
        self.assertEqual(self.speech.cancelled, ["confirmed_barge_in"])

    async def test_replacement_closes_old_session_and_stale_port_cannot_target_new_one(self) -> None:
        old_socket = FakeSocket()
        await self.hub.attach(old_socket)
        old_port = await self.hub.acquire_renderer_port()
        new_socket = FakeSocket()
        await self.hub.attach(new_socket)
        self.assertTrue(old_socket.closed)
        self.assertEqual(self.runtime.disconnects, 1)
        assert old_port is not None
        self.assertEqual(
            await old_port.offer_clause(RendererClause("old", "stale", "invoke", "input", 1)),
            "session_replaced",
        )
        self.assertEqual(new_socket.sent[0]["type"], "session.ready")


class GatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = FakeRuntime()
        self.speech = FakeSpeech()
        self.hub = RealtimeHub(allowed_origins={ORIGIN})

        async def activate(profile_id: str, soul: str, **snapshot) -> int:
            self.assertEqual(soul, "Aura soul")
            await self.hub.set_active(profile_id, 3, ProfileResources(profile_id, self.runtime, self.speech, None, revision=snapshot["revision"]))
            return 3

        async def renew(profile_id: str, revision: int) -> int:
            await self.hub.set_active(profile_id, 4, self.hub.profile)
            return 4
        self.hub.profile_activator = activate
        self.hub.profile_session_renewer = renew
        app = FastAPI()
        app.state.main_agent_realtime = self.hub
        app.include_router(router)
        self.client = TestClient(app)

    def test_websocket_consumption_receipts_release_audio_without_committing_delivery(self):
        self.client.post("/api/v1/main-agent/profile/activate", headers={"origin": ORIGIN},
            json={"profile_id": "profile_default", "revision": 1, "soul": "Aura soul"})
        token = self.client.post("/api/v1/main-agent/realtime-token", headers={"origin": ORIGIN},
            json={"profileId": "profile_default", "revision": 1}).json()["token"]
        original_command = self.hub.command
        pcm = b"\x12\x34" * 240_000
        async def command(session, event):
            await original_command(session, event)
            if event["type"] == "voice.start":
                async def stream():
                    port = await self.hub.acquire_renderer_port()
                    await port.offer_clause(RendererClause("wire", "hello", "invoke", "input", 1))
                    if await port.send_audio(pcm) is None:
                        await port.finish_audio("wire")
                self.sending = asyncio.create_task(stream())
        self.hub.command = command
        with self.client.websocket_connect("/api/v1/main-agent/realtime", headers={"origin": ORIGIN},
            subprotocols=[SUBPROTOCOL, f"aurabot.capability.{token}"]) as websocket:
            websocket.receive_json()
            websocket.send_json({"type": "voice.start", "command_id": "start"})
            websocket.receive_json()
            self.assertEqual(websocket.receive_json()["status"], "accepted")
            self.assertEqual(websocket.receive_json()["type"], "clause.offer")
            websocket.receive_json()
            received = bytearray()
            while len(received) < len(pcm):
                received.extend(websocket.receive_bytes())
                websocket.send_json({"type": "renderer.progress", "clause_id": "wire", "consumed_samples": len(received) // 2})
            self.assertEqual(received, pcm)
            self.assertEqual(websocket.receive_json()["type"], "clause.audio_end")
            self.assertEqual(self.runtime.acks, [])
            websocket.send_json({"type": "renderer.ack", "clause_id": "wire", "complete": True})
            self.assertEqual(websocket.receive_json()["type"], "agent.state")
            self.assertEqual(self.runtime.acks, [("wire", True)])

    def test_profile_token_opens_ready_session_and_accepts_raw_mic_and_ack_only(self) -> None:
        request = {"profileId": "profile_default", "revision": 1}
        self.assertEqual(self.client.post("/api/v1/main-agent/realtime-token", headers={"origin": ORIGIN}, json=request).status_code, 409)
        activated = self.client.post("/api/v1/main-agent/profile/activate", headers={"origin": ORIGIN},
            json={"profile_id": "profile_default", "revision": 1, "soul": "Aura soul", "adaptor": {}, "model_version": ""})
        self.assertEqual(activated.status_code, 200)
        response = self.client.post(
            "/api/v1/main-agent/realtime-token",
            headers={"origin": ORIGIN},
            json={"profileId": "profile_default", "revision": 1},
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["subprotocol"], SUBPROTOCOL)
        protocols = [SUBPROTOCOL, f"aurabot.capability.{body['token']}"]
        with self.client.websocket_connect(
            "/api/v1/main-agent/realtime",
            headers={"origin": ORIGIN},
            subprotocols=protocols,
        ) as websocket:
            self.assertEqual(websocket.receive_json(), {"type": "session.ready", "profile_id": "profile_default", "generation": 4, "config_revision": 1})
            websocket.send_json({"type": "voice.start", "command_id": "voice-1"})
            self.assertEqual(websocket.receive_json(), {"type": "agent.state", "state": "no_audio"})
            self.assertEqual(websocket.receive_json()["status"], "accepted")
            websocket.send_bytes(b"\0" * 640)
            websocket.send_json({"type": "renderer.ack", "clause_id": "unknown", "complete": False})
            self.assertEqual(websocket.receive_json(), {"type": "session.error", "code": "unknown_clause"})

    def test_activation_exposes_only_stable_cleanup_block_reason(self) -> None:
        async def blocked(*args, **kwargs):
            raise RuntimeError("profile_cleanup_incomplete")
        self.hub.profile_activator = blocked
        response = self.client.post("/api/v1/main-agent/profile/activate", headers={"origin": ORIGIN},
            json={"profile_id": "profile_default", "revision": 1, "soul": "private Soul"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"detail": "profile_cleanup_incomplete"})
        self.assertNotIn("private Soul", response.text)

    def test_token_rejects_invalid_identity_old_soul_contract_and_wrong_origin(self) -> None:
        for body in (
            {"profileId": "../escape", "soul": "Aura"},
            {"profileId": "profile_default", "soul": ""},
        ):
            response = self.client.post(
                "/api/v1/main-agent/realtime-token", headers={"origin": ORIGIN}, json=body,
            )
            self.assertEqual(response.status_code, 422)
        response = self.client.post(
            "/api/v1/main-agent/realtime-token",
            headers={"origin": "null"},
            json={"profileId": "profile_default", "revision": 1},
        )
        self.assertEqual(response.status_code, 403)
