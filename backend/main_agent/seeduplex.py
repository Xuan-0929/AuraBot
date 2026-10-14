"""Translate SeedDuplex's public Realtime protocol into voice-engine events.

This adapter owns authentication, the WebSocket, PCM framing, session configuration,
and provider event decoding. It never executes tools or owns Profile memory/Task state.
The voice engine consumes normalized events and returns results for observed call IDs;
explicit mute events keep the remote session alive while microphone frames are absent.
Failures expose stable codes rather than credentials or provider payloads to the renderer.
The shared debug scope observes connection phases, upload edges and bounded error facts;
one connection identity joins engine events, while audio frames only increment counters.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from .settings import RealtimeVoiceSettings
from .debug import DebugScope
from .metrics import speech_logger
from .tool_runtime import ToolCall


ENDPOINT = "wss://openspeech.bytedance.com/api/v3/duplex/realtime/dialogue"
_KINDS = {
    "conversation.item.input_audio_transcription.started": "input_started",
    "conversation.item.input_audio_transcription.delta": "transcript_delta",
    "conversation.item.input_audio_transcription.completed": "transcript_done",
    "conversation.item.input_audio_transcription.failed": "transcript_failed",
    "response.output_text.delta": "text_delta",
    "response.output_text.done": "text_done",
    "response.output_audio.started": "audio_started",
    "response.output_audio.delta": "audio",
    "response.output_audio.done": "audio_done",
    "response.function_call_arguments.done": "tools",
    "response.done": "response_done",
    "response.canceled": "cancel_ack",
    "conversation.item.added": "items_added",
    "conversation.item.updated": "items_updated",
    "conversation.item.retrieved": "items_retrieved",
    "session.updated": "session_updated",
    "session.closed": "closed",
}


@dataclass(frozen=True, slots=True)
class VoiceEvent:
    kind: str
    response_id: str = ""
    item_id: str = ""
    event_id: str = ""
    text: str = ""
    audio: bytes = b""
    calls: tuple[ToolCall, ...] = ()
    items: tuple[dict[str, Any], ...] = ()
    question_id: str = ""


class RealtimeVoiceAdaptor(Protocol):
    """Provider boundary consumed by the engine, independent of Profile business services."""
    async def start(self, *, instructions: str, tools: list[dict[str, Any]]) -> None: ...
    async def receive(self) -> VoiceEvent: ...
    async def send_audio(self, pcm: bytes) -> None: ...
    async def mute(self) -> None: ...
    async def update_instructions(self, instructions: str) -> str: ...
    async def initialize_history(self, items: list[dict[str, Any]]) -> str: ...
    async def replace_assistant(self, item_id: str, text: str) -> str: ...
    async def cancel_response(self) -> None: ...
    async def tool_results(self, results: dict[str, Any]) -> None: ...
    async def close(self) -> None: ...


class SeedDuplexAdaptor:
    def __init__(self, settings: RealtimeVoiceSettings, *, connect: Any = None, debug: DebugScope | None = None) -> None:
        self.settings = settings
        self.debug = debug
        self.connection_id = uuid.uuid4().hex
        self._sent_frames = 0
        self._started = time.monotonic()
        self._connect = connect
        self._socket: Any = None
        self._write_lock = asyncio.Lock()
        self._muted = False
        self._session: dict[str, Any] = {}
        self._calls: dict[str, str] = {}

    def _trace(self, name: str, *, level: str = "info", **details: Any) -> None:
        if self.debug is not None:
            self.debug.emit("input" if name.startswith("realtime.upload.") else "connection",
                name, level=level, connectionId=self.connection_id, **details)
        if level != "info" or name == "realtime.connection.closed":
            try:
                speech_logger.log(30 if level != "info" else 20, "%s connection=%s facts=%s",
                    name, self.connection_id, json.dumps(details, ensure_ascii=True))
            except Exception:
                pass  # Diagnostics must not replace the original wire outcome.

    async def start(self, *, instructions: str, tools: list[dict[str, Any]]) -> None:
        self._started = time.monotonic()
        self._trace("realtime.connection.starting", modelName=self.settings.model)
        if not self.settings.api_key:
            raise RuntimeError("realtime_api_key_missing")
        if not self.settings.voice:
            raise RuntimeError("realtime_voice_missing")
        if self._socket is not None:
            raise RuntimeError("realtime_already_connected")
        connect = self._connect
        if connect is None:
            from websockets.asyncio.client import connect
        self._session = {
            "model": self.settings.model,
            "instructions": instructions,
            "audio": {
                "input": {"format": {"type": "pcm", "rate": 16000}},
                "output": {"format": {"type": "pcm_s16le", "rate": 24000}, "voice": self.settings.voice},
            },
            "tools": tools,
        }
        operation = "connect"
        try:
            self._socket = await connect(ENDPOINT, additional_headers={"X-Api-Key": self.settings.api_key},
                                         open_timeout=10, close_timeout=3, ping_interval=None, max_size=2 * 1024 * 1024)
            self._trace("realtime.connection.connected", elapsedMs=(time.monotonic() - self._started) * 1000)
            operation = "session.create"
            await self._send("session.create", session=self._session)
            operation = "session.created.wait"
            async with asyncio.timeout(10):
                while True:
                    event = await self._read()
                    if event["type"] == "session.created":
                        break
            operation = "mute"
            await self.mute()
            self._trace("realtime.connection.ready", elapsedMs=(time.monotonic() - self._started) * 1000)
        except BaseException as exc:
            self._trace("realtime.connection.failed", level="warning", operation=operation,
                exceptionType=type(exc).__name__, cancelled=isinstance(exc, asyncio.CancelledError),
                elapsedMs=(time.monotonic() - self._started) * 1000)
            await self.close()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise RuntimeError("realtime_connection_failed") from None

    async def _send(self, kind: str, **fields: Any) -> str:
        event_id = uuid.uuid4().hex
        try:
            async with asyncio.timeout(10), self._write_lock:
                if self._socket is None:
                    raise RuntimeError("realtime_disconnected")
                await self._socket.send(json.dumps({"type": kind, "event_id": event_id, **fields}, ensure_ascii=False, allow_nan=False))
        except Exception as exc:
            self._trace("realtime.send.failed", level="error", operation=kind,
                eventId=event_id, exceptionType=type(exc).__name__, sentFrames=self._sent_frames)
            raise
        return event_id

    async def _read(self) -> dict[str, Any]:
        if self._socket is None:
            raise RuntimeError("realtime_disconnected")
        try:
            event = json.loads(await self._socket.recv())
        except (ValueError, UnicodeError):
            self._trace("realtime.receive.failed", level="error", code="realtime_protocol_error", operation="decode")
            raise RuntimeError("realtime_protocol_error") from None
        except Exception as exc:
            self._trace("realtime.receive.failed", level="error", operation="receive", exceptionType=type(exc).__name__)
            raise
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            self._trace("realtime.receive.failed", level="error", operation="event_shape", code="realtime_protocol_error")
            raise RuntimeError("realtime_protocol_error")
        if event["type"] == "error":
            error = event.get("error")
            code = error.get("code") if isinstance(error, dict) else None
            # Only numeric provider codes are retained; free-form error strings may contain secrets.
            self._trace("realtime.provider.failed", level="error", code="realtime_provider_error",
                eventId=event.get("event_id", "")[:256] if isinstance(event.get("event_id"), str) else "",
                providerCode=code if type(code) is int else None)
            raise RuntimeError("realtime_provider_error")
        return event

    async def send_audio(self, pcm: bytes) -> None:
        if not isinstance(pcm, bytes) or len(pcm) != 640:
            raise ValueError("Realtime microphone frames must be 20 ms PCM16 at 16 kHz")
        if self._muted:
            await self._send("input_audio_unmute.commit")
            self._muted = False
            self._trace("realtime.upload.unmuted")
        await self._send("input_audio_buffer.append", audio=base64.b64encode(pcm).decode("ascii"))
        self._sent_frames += 1
        if self._sent_frames == 1:
            self._trace("realtime.upload.first", sentFrames=1, sentBytes=len(pcm))

    async def mute(self) -> None:
        if not self._muted:
            await self._send("input_audio_mute.commit")
            self._muted = True
            self._trace("realtime.upload.muted", sentFrames=self._sent_frames)

    async def update_instructions(self, instructions: str) -> str:
        self._session = {**self._session, "instructions": instructions}
        return await self._send("session.update", session=self._session)

    async def initialize_history(self, items: list[dict[str, Any]]) -> str:
        if len(items) > 40 or len(items) % 2 or any(item.get("role") != ("user" if i % 2 == 0 else "assistant") for i, item in enumerate(items)):
            raise ValueError("Realtime history requires at most 20 complete QA pairs")
        return await self._send("conversation.item.create", items=items)

    async def replace_assistant(self, item_id: str, text: str) -> str:
        if not item_id:
            raise ValueError("assistant item identity is required")
        return await self._send("conversation.item.update", items=[{
            "id": item_id, "content": [{"type": "input_text", "text": text}],
        }])

    async def cancel_response(self) -> None:
        await self._send("response.cancel")

    async def tool_results(self, results: dict[str, Any]) -> None:
        if not results or not set(results).issubset(self._calls):
            raise ValueError("tool results must refer to observed provider call IDs")
        await self._send("conversation.item.create", items=[{
            "type": "message", "role": "tool", "call_id": call_id,
            "content": [{"type": "input_text", "text": json.dumps(result, ensure_ascii=False, allow_nan=False)}],
        } for call_id, result in results.items()])

    async def receive(self) -> VoiceEvent:
        event = await self._read()
        kind = _KINDS.get(event["type"], "other")
        try:
            response_id = event.get("response_id", "")
            item_id = event.get("item_id", "")
            question_id = event.get("question_id", "")
            if any(not isinstance(value, str) or len(value) > 256 for value in (response_id, item_id, question_id)):
                raise RuntimeError("realtime_protocol_error")
            text = event.get("transcript", event.get("text", event.get("delta", ""))) if kind in {"transcript_delta", "transcript_done", "text_delta", "text_done"} else ""
            if not isinstance(text, str):
                raise RuntimeError("realtime_protocol_error")
            pcm = b""
            if kind == "audio":
                try:
                    pcm = base64.b64decode(event["delta"], validate=True)
                except (KeyError, TypeError, ValueError, binascii.Error):
                    raise RuntimeError("realtime_protocol_error") from None
                if not response_id or not pcm or len(pcm) % 2:
                    raise RuntimeError("realtime_protocol_error")
            calls = []
            items = event.get("items", [])
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                raise RuntimeError("realtime_protocol_error")
            if kind == "tools":
                if not 1 <= len(items) <= 8:
                    raise RuntimeError("realtime_protocol_error")
                for item in items:
                    call_id, name, raw = item.get("call_id"), item.get("name"), item.get("arguments")
                    if not isinstance(call_id, str) or not call_id or len(call_id) > 256 or not isinstance(name, str) or not name:
                        raise RuntimeError("realtime_protocol_error")
                    error = None
                    try:
                        arguments = json.loads(raw) if isinstance(raw, str) else None
                        if not isinstance(arguments, dict):
                            raise ValueError()
                    except ValueError:
                        arguments, error = {}, "schema_invalid"
                    try:
                        fingerprint = json.dumps([name, arguments, error], sort_keys=True, allow_nan=False)
                    except ValueError:
                        raise RuntimeError("realtime_protocol_error") from None
                    if any(call.call_id == call_id for call in calls) or (call_id in self._calls and self._calls[call_id] != fingerprint):
                        raise RuntimeError("realtime_protocol_error")
                    calls.append(ToolCall(call_id, name, arguments, raw_arguments=raw if isinstance(raw, str) else "", parse_error=error))
                    self._calls[call_id] = fingerprint
                if len(self._calls) > 4096:
                    raise RuntimeError("realtime_session_limit")
            event_id = event.get("event_id", "")
            return VoiceEvent(kind, response_id, item_id, event_id[:256] if isinstance(event_id, str) else "",
                text, pcm, tuple(calls), tuple(items), question_id)
        except Exception as exc:
            self._trace("realtime.receive.failed", level="error", operation="normalize", eventKind=kind,
                exceptionType=type(exc).__name__)
            raise

    async def close(self) -> None:
        socket, self._socket = self._socket, None
        if socket is not None:
            try:
                async with asyncio.timeout(3):
                    await socket.send(json.dumps({"type": "session.close", "event_id": uuid.uuid4().hex}))
            except Exception as exc:
                self._trace("realtime.connection.close_failed", level="warning",
                    operation="session.close", exceptionType=type(exc).__name__)
            finally:
                try:
                    async with asyncio.timeout(3):
                        await socket.close()
                except Exception as exc:
                    self._trace("realtime.connection.close_failed", level="warning",
                        operation="socket.close", exceptionType=type(exc).__name__)
            self._trace("realtime.connection.closed", sentFrames=self._sent_frames,
                sentBytes=self._sent_frames * 640, elapsedMs=(time.monotonic() - self._started) * 1000)
        self._calls.clear()
        self._muted = False
