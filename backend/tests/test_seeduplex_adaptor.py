"""Verify the SeedDuplex wire boundary using a scripted external WebSocket.

The real adaptor must create a PCM16 session, pace input through one-frame sends,
normalize provider events, preserve real tool identities and reject malformed audio.
No provider credentials or external speech service are needed by these protocol tests.
Real diagnostic scopes verify sparse upload summaries and redacted wire failure facts.
"""

import asyncio
import base64
import json
import unittest

from backend.main_agent.seeduplex import SeedDuplexAdaptor
from backend.main_agent.settings import RealtimeVoiceSettings
from backend.main_agent.debug import DebugRecorder


class Socket:
    def __init__(self):
        self.sent = []
        self.frames = asyncio.Queue()
        self.frames.put_nowait(json.dumps({"type": "session.created", "session": {"id": "session-1"}}))
        self.closed = False

    async def send(self, frame):
        self.sent.append(json.loads(frame))

    async def recv(self):
        return await self.frames.get()

    async def close(self):
        self.closed = True


class SeedDuplexAdaptorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.socket = Socket()
        self.connection = None

        async def connect(url, **kwargs):
            self.connection = (url, kwargs)
            return self.socket

        self.adaptor = SeedDuplexAdaptor(RealtimeVoiceSettings(api_key="secret", voice="voice-id"), connect=connect)
        await self.adaptor.start(instructions="persona", tools=[])
        self.addAsyncCleanup(self.adaptor.close)

    async def test_pcm16_session_and_explicit_mute_lifecycle(self):
        self.assertEqual(self.connection[1]["additional_headers"], {"X-Api-Key": "secret"})
        created = self.socket.sent[0]
        self.assertEqual(created["type"], "session.create")
        self.assertEqual(created["session"]["audio"], {
            "input": {"format": {"type": "pcm", "rate": 16000}},
            "output": {"format": {"type": "pcm_s16le", "rate": 24000}, "voice": "voice-id"},
        })
        await self.adaptor.send_audio(bytes(640))
        self.assertEqual([x["type"] for x in self.socket.sent[-2:]], ["input_audio_unmute.commit", "input_audio_buffer.append"])
        self.assertEqual(base64.b64decode(self.socket.sent[-1]["audio"]), bytes(640))
        with self.assertRaises(ValueError):
            await self.adaptor.send_audio(bytes(638))
        await self.adaptor.mute()
        self.assertEqual(self.socket.sent[-1]["type"], "input_audio_mute.commit")

    async def test_upload_diagnostics_are_sparse_and_provider_errors_are_redacted(self):
        recorder = DebugRecorder()
        recorder.configure(True, "capture")
        self.adaptor.debug = recorder.scope
        for _ in range(100):
            await self.adaptor.send_audio(bytes(640))
        await self.adaptor.mute()
        self.socket.frames.put_nowait(json.dumps({"type": "error", "event_id": "e1",
            "error": {"code": 429, "message": "secret credential private transcript"}}))
        with self.assertRaisesRegex(RuntimeError, "realtime_provider_error"):
            await self.adaptor.receive()
        await self.adaptor.close()
        events = recorder.snapshot()["events"]
        self.assertEqual(sum(e["name"] == "realtime.upload.first" for e in events), 1)
        self.assertLess(len(events), 10)
        error = next(e for e in events if e["name"] == "realtime.provider.failed")
        self.assertEqual(error["details"]["providerCode"], 429)
        self.assertEqual(error["details"]["eventId"], "e1")
        closed = next(e for e in events if e["name"] == "realtime.connection.closed")
        self.assertEqual(closed["details"]["sentFrames"], 100)
        self.assertEqual(closed["details"]["sentBytes"], 64000)
        self.assertNotIn("private transcript", json.dumps(events))

    async def test_protocol_diagnostics_do_not_stringify_identity_payloads(self):
        recorder = DebugRecorder()
        recorder.configure(True, "capture")
        self.adaptor.debug = recorder.scope
        self.socket.frames.put_nowait(json.dumps({"type": "response.output_text.done", "response_id": "r1",
            "event_id": {"secret": "private-payload"}, "text": "reply"}))
        event = await self.adaptor.receive()
        self.assertEqual(event.event_id, "")
        self.socket.frames.put_nowait(json.dumps({"type": "response.output_audio.delta", "response_id": "r1", "delta": "bad-base64"}))
        with self.assertRaisesRegex(RuntimeError, "realtime_protocol_error"):
            await self.adaptor.receive()
        failed = next(e for e in recorder.snapshot()["events"] if e["name"] == "realtime.receive.failed")
        self.assertEqual(failed["details"]["eventKind"], "audio")
        self.assertNotIn("bad-base64", json.dumps(recorder.snapshot()))

    async def test_normalizes_audio_and_preserves_reply_identity(self):
        self.socket.frames.put_nowait(json.dumps({"type": "response.output_audio.delta", "response_id": "r1", "delta": base64.b64encode(b"\x01\x02").decode()}))
        event = await self.adaptor.receive()
        self.assertEqual((event.kind, event.response_id, event.audio), ("audio", "r1", b"\x01\x02"))
        self.socket.frames.put_nowait(json.dumps({"type": "response.output_audio.delta", "response_id": "r1", "delta": "not base64"}))
        with self.assertRaisesRegex(RuntimeError, "realtime_protocol_error"):
            await self.adaptor.receive()

    async def test_only_real_tool_call_ids_can_receive_results(self):
        self.socket.frames.put_nowait(json.dumps({"type": "response.function_call_arguments.done", "response_id": "r1", "items": [
            {"call_id": "call-1", "name": "web_search", "arguments": '{"query":"weather"}'},
        ]}))
        event = await self.adaptor.receive()
        self.assertEqual(event.calls[0].arguments, {"query": "weather"})
        with self.assertRaises(ValueError):
            await self.adaptor.tool_results({"invented": {"ok": True}})
        await self.adaptor.tool_results({"call-1": {"ok": True}})
        result = self.socket.sent[-1]
        self.assertEqual(result["type"], "conversation.item.create")
        self.assertEqual(result["items"][0]["role"], "tool")
        self.assertEqual(result["items"][0]["call_id"], "call-1")
        self.assertEqual(json.loads(result["items"][0]["content"][0]["text"]), {"ok": True})

    async def test_session_update_does_not_request_speech(self):
        await self.adaptor.update_instructions("persona\nTask facts: done")
        self.assertEqual(self.socket.sent[-1]["type"], "session.update")
        self.assertEqual(self.socket.sent[-1]["session"]["instructions"], "persona\nTask facts: done")

    async def test_call_identity_cannot_change_tools_or_repeat_inside_a_batch(self):
        original = {"call_id": "call-1", "name": "web_search", "arguments": '{"query":"weather"}'}
        for items, invalid in (([original], False), ([{**original, "name": "task_assign"}], True), ([original, original], True)):
            self.socket.frames.put_nowait(json.dumps({"type": "response.function_call_arguments.done", "items": items}))
            if invalid:
                with self.assertRaisesRegex(RuntimeError, "realtime_protocol_error"):
                    await self.adaptor.receive()
            else:
                await self.adaptor.receive()
