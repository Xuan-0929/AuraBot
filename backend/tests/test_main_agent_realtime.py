"""Exercises the unreleased minimal Pet voice protocol through its token, hub, and WebSocket boundaries.

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
    RealtimeHub,
    SUBPROTOCOL,
    router,
)
from backend.main_agent.runtime import RendererClause


ORIGIN = "http://127.0.0.1:6400"


class FakeRuntime:
    def __init__(self) -> None:
        self.acks: list[tuple[str, bool]] = []

    async def renderer_ack(self, clause_id: str, *, complete: bool) -> bool:
        self.acks.append((clause_id, complete))
        return complete


class FakeSpeech:
    def __init__(self) -> None:
        self.acks: list[tuple[str, bool]] = []
        self.cancelled: list[str] = []

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
        await self.hub.set_active("profile_default", 1, self.runtime, self.speech)

    async def test_port_sends_one_clause_raw_audio_end_and_physical_ack(self) -> None:
        socket = FakeSocket()
        session = await self.hub.attach(socket)
        port = await self.hub.acquire_renderer_port()
        assert port is not None
        clause = RendererClause("c1", "hello", "invoke", "input", 1)
        await port.offer_clause(clause)
        await port.send_audio(b"\0\0" * 480)
        await port.finish_audio("c1")
        await self.hub.renderer_ack(session, "c1", complete=True)

        self.assertEqual(socket.sent[0], {"type": "session.ready", "profile_id": "profile_default"})
        self.assertEqual(socket.sent[1], {"type": "clause.offer", "clause_id": "c1", "text": "hello"})
        self.assertEqual(socket.sent[2], b"\0\0" * 480)
        self.assertEqual(socket.sent[3], {"type": "clause.audio_end", "clause_id": "c1"})
        self.assertEqual(self.runtime.acks, [("c1", True)])
        self.assertEqual(self.speech.acks, [("c1", True)])

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
            "session.ready", "clause.offer", "playback.pause", "playback.resume", "playback.pause", "clause.cancel",
        ])
        self.assertEqual(self.speech.cancelled, ["confirmed_barge_in"])

    async def test_replacement_closes_old_session_and_stale_port_cannot_target_new_one(self) -> None:
        old_socket = FakeSocket()
        await self.hub.attach(old_socket)
        old_port = await self.hub.acquire_renderer_port()
        new_socket = FakeSocket()
        await self.hub.attach(new_socket)
        self.assertTrue(old_socket.closed)
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

        async def activate(profile_id: str, soul: str) -> int:
            self.assertEqual(soul, "Aura soul")
            await self.hub.set_active(profile_id, 3, self.runtime, self.speech)
            return 3

        self.hub.profile_activator = activate
        app = FastAPI()
        app.state.main_agent_realtime = self.hub
        app.include_router(router)
        self.client = TestClient(app)

    def test_profile_token_opens_ready_session_and_accepts_raw_mic_and_ack_only(self) -> None:
        response = self.client.post(
            "/api/v1/main-agent/realtime-token",
            headers={"origin": ORIGIN},
            json={"profileId": "profile_default", "soul": "Aura soul"},
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
            self.assertEqual(websocket.receive_json(), {"type": "session.ready", "profile_id": "profile_default"})
            websocket.send_bytes(b"\0" * 640)
            websocket.send_json({"type": "renderer.ack", "clause_id": "unknown", "complete": False})
            self.assertEqual(websocket.receive_json(), {"type": "session.error", "code": "unknown_clause"})

    def test_token_rejects_invalid_identity_empty_soul_and_wrong_origin(self) -> None:
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
            json={"profileId": "profile_default", "soul": "Aura"},
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
