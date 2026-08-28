"""Own the ``aurabot.main-agent.v1`` WebSocket trust boundary and realtime media framing.

The module issues origin-bound one-shot capabilities, validates bounded JSON and 24-byte AUR1 PCM
frames, arbitrates one process-global Renderer lease, and translates only normalized desktop events to
MainAgentRuntime. It also owns event-driven bounded media queues, loop-affine session command ports, a
control-priority writer, and the authoritative Renderer playback gate driven by backend VAD lifecycle
events; media is never copied to a replacement lease. Speech providers remain outside this module and
may consume the bounded mic queue or capture an immutable ``RendererLeasePort`` before offering clauses
or enqueueing 24 kHz TTS frames.

Protocol v1 control types are intentionally explicit: client ``session.open``, ``text.submit``,
``mic.start``, ``mic.stop``, ``speech.candidate``, ``speech.rejected``, ``speech.confirmed``,
``renderer.ack``, and ``mode.set``; server ``session.opened``, ``session.replaced``, ``session.error``,
``state.snapshot``, ``command.ack``, ``clause.offer``, ``clause.cancel``, ``playback.pause``,
``playback.resume``, ``playback.stop``, and ``queue.overrun``.  Every control message has a positive
integer ``seq``.  Mutating client commands have
a UUID ``command_id``; renderer ACK/mode commands also carry the current ``renderer_epoch``.

AUR1 is little-endian ``<4sBBHIIQ``: magic, kind (1=16 kHz mic, 2=24 kHz TTS), flags,
reserved-zero, stream id, frame sequence, and starting sample index.  Payload is PCM16LE mono: exactly
320 samples/640 bytes for a 20 ms mic frame and 480 samples/960 bytes for a 20 ms TTS frame.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hmac
import inspect
import json
import math
import secrets
import struct
import threading
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass, is_dataclass, replace
from enum import IntEnum
from typing import Any

import anyio
from fastapi import APIRouter, HTTPException, Request, Response, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel


SUBPROTOCOL = "aurabot.main-agent.v1"
MAX_CONTROL_BYTES = 65_536
MAX_JSON_DEPTH = 8
MAX_STRING_CHARS = 16_384
MAX_COMMANDS = 1_024
MAX_CONTROL_EVENTS = 128
MAX_SERVER_SEQUENCE = 2**53 - 1
MIC_QUEUE_FRAMES = 50
TTS_QUEUE_FRAMES = 25
DISCONTINUITY_FLAG = 0x01
FINAL_FLAG = 0x02
_ALLOWED_FLAGS = DISCONTINUITY_FLAG | FINAL_FLAG
_HEADER = struct.Struct("<4sBBHIIQ")
AUR1_HEADER_SIZE = _HEADER.size


class ProtocolError(ValueError):
    """A stable public protocol rejection without any credential or private exception text."""

    def __init__(self, code: str, message: str | None = None, *, fatal: bool = False) -> None:
        super().__init__(message or code.replace("_", " "))
        self.code = code
        self.fatal = fatal


class _CrossLoopLock:
    """Serialize one process-global lease across ASGI loops without blocking or polling their threads."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locked = False
        self._waiters: deque[tuple[asyncio.AbstractEventLoop, asyncio.Future[None]]] = deque()

    async def acquire(self) -> None:
        loop = asyncio.get_running_loop()
        waiter = loop.create_future()
        with self._guard:
            if not self._locked:
                self._locked = True
                return
            self._waiters.append((loop, waiter))
        try:
            await waiter
        except asyncio.CancelledError:
            granted = False
            with self._guard:
                try:
                    self._waiters.remove((loop, waiter))
                except ValueError:
                    granted = True
            if granted:
                self.release()
            raise

    def release(self) -> None:
        with self._guard:
            while self._waiters:
                loop, waiter = self._waiters.popleft()
                if waiter.cancelled() or loop.is_closed():
                    continue
                try:
                    loop.call_soon_threadsafe(self._grant, waiter)
                except RuntimeError:
                    continue
                else:
                    return
            self._locked = False

    @staticmethod
    def _grant(waiter: asyncio.Future[None]) -> None:
        if not waiter.done():
            waiter.set_result(None)

    async def __aenter__(self) -> "_CrossLoopLock":
        await self.acquire()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self.release()


class AudioFrameKind(IntEnum):
    """Direction-specific PCM formats encoded by the AUR1 header kind byte."""

    MIC = 1
    TTS = 2


@dataclass(frozen=True, slots=True)
class AudioFrame:
    """One validated PCM frame with transport ordering coordinates."""

    kind: AudioFrameKind
    flags: int
    stream_id: int
    frame_seq: int
    sample_start: int
    payload: bytes

    @property
    def sample_count(self) -> int:
        return len(self.payload) // 2

    @property
    def discontinuity(self) -> bool:
        return bool(self.flags & DISCONTINUITY_FLAG)


def pack_audio_frame(frame: AudioFrame) -> bytes:
    """Validate and encode one exact v1 AUR1 PCM frame."""

    _validate_audio_frame(frame)
    return _HEADER.pack(
        b"AUR1",
        int(frame.kind),
        frame.flags,
        0,
        frame.stream_id,
        frame.frame_seq,
        frame.sample_start,
    ) + frame.payload


def parse_audio_frame(data: bytes | bytearray | memoryview) -> AudioFrame:
    """Decode one bounded AUR1 message, rejecting ambiguous codecs, lengths, and reserved bits."""

    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ProtocolError("invalid_audio", "audio message must be bytes")
    encoded = bytes(data)
    if len(encoded) < AUR1_HEADER_SIZE:
        raise ProtocolError("invalid_audio_size", "audio frame is shorter than its 24-byte header")
    magic, raw_kind, flags, reserved, stream_id, frame_seq, sample_start = _HEADER.unpack_from(encoded)
    if magic != b"AUR1":
        raise ProtocolError("invalid_audio_magic")
    try:
        kind = AudioFrameKind(raw_kind)
    except ValueError as exc:
        raise ProtocolError("invalid_audio_kind") from exc
    if reserved != 0 or flags & ~_ALLOWED_FLAGS:
        raise ProtocolError("invalid_audio_flags")
    frame = AudioFrame(kind, flags, stream_id, frame_seq, sample_start, encoded[AUR1_HEADER_SIZE:])
    _validate_audio_frame(frame)
    return frame


def _validate_audio_frame(frame: AudioFrame) -> None:
    if not isinstance(frame, AudioFrame):
        raise ProtocolError("invalid_audio")
    try:
        kind = AudioFrameKind(frame.kind)
    except (TypeError, ValueError) as exc:
        raise ProtocolError("invalid_audio_kind") from exc
    if isinstance(frame.flags, bool) or not isinstance(frame.flags, int) or frame.flags & ~_ALLOWED_FLAGS:
        raise ProtocolError("invalid_audio_flags")
    bounds = (frame.stream_id, frame.frame_seq)
    if any(isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF for value in bounds):
        raise ProtocolError("invalid_audio_coordinate")
    if isinstance(frame.sample_start, bool) or not isinstance(frame.sample_start, int) or not 0 <= frame.sample_start <= 0xFFFFFFFFFFFFFFFF:
        raise ProtocolError("invalid_audio_sample")
    expected = 640 if kind is AudioFrameKind.MIC else 960
    if not isinstance(frame.payload, bytes) or len(frame.payload) != expected:
        raise ProtocolError("invalid_audio_payload", f"{kind.name.lower()} payload must be {expected} bytes")


class AudioFrameTracker:
    """Enforce per-kind/stream frame and sample order while allowing explicit discontinuities."""

    def __init__(self, *, max_terminal_streams: int = 128) -> None:
        if max_terminal_streams < 1:
            raise ValueError("terminal stream bound must be positive")
        self._max_terminal_streams = max_terminal_streams
        self._expected: dict[tuple[AudioFrameKind, int], tuple[int, int]] = {}
        self._terminal: OrderedDict[
            tuple[AudioFrameKind, int], tuple[int, int, int]
        ] = OrderedDict()

    @property
    def terminal_stream_count(self) -> int:
        return len(self._terminal)

    def accept(self, frame: AudioFrame) -> str:
        _validate_audio_frame(frame)
        key = (AudioFrameKind(frame.kind), frame.stream_id)
        terminal = self._terminal.get(key)
        if terminal is not None:
            if (
                frame.flags & FINAL_FLAG
                and (frame.frame_seq, frame.sample_start, frame.sample_count) == terminal
            ):
                return "duplicate"
            raise ProtocolError("audio_stream_reused", "terminal audio stream id was reused")
        expected = self._expected.get(key)
        if expected is not None and frame.frame_seq < expected[0]:
            return "duplicate"
        if frame.discontinuity:
            result = "discontinuity" if expected is not None else "accepted"
        else:
            if expected is None and (frame.frame_seq != 0 or frame.sample_start != 0):
                raise ProtocolError("audio_gap", "new stream must start at frame and sample zero")
            if expected is not None:
                if frame.frame_seq != expected[0]:
                    raise ProtocolError("audio_gap", "audio frame sequence gap")
                if frame.sample_start != expected[1]:
                    raise ProtocolError("audio_sample_order", "audio sample position is not contiguous")
            result = "accepted"
        self._expected[key] = (frame.frame_seq + 1, frame.sample_start + frame.sample_count)
        if frame.flags & FINAL_FLAG:
            self._expected.pop(key, None)
            self._terminal[key] = (frame.frame_seq, frame.sample_start, frame.sample_count)
            while len(self._terminal) > self._max_terminal_streams:
                self._terminal.popitem(last=False)
        return result

    def clear(
        self, *, preserve_terminals: bool = False, retire_active: bool = False
    ) -> None:
        if preserve_terminals and retire_active:
            for key in self._expected:
                self._terminal[key] = (-1, -1, -1)
            while len(self._terminal) > self._max_terminal_streams:
                self._terminal.popitem(last=False)
        self._expected.clear()
        if not preserve_terminals:
            self._terminal.clear()

    def discard(self, frame: AudioFrame) -> None:
        """Undo one newly accepted stream when its session-level admission fails."""

        key = (AudioFrameKind(frame.kind), frame.stream_id)
        self._expected.pop(key, None)
        self._terminal.pop(key, None)


class BoundedAudioQueue:
    """Event-driven fixed-frame queue with direction-specific deterministic overflow behavior."""

    def __init__(self, capacity: int, kind: AudioFrameKind) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("audio queue capacity must be positive")
        self.capacity = capacity
        self.kind = AudioFrameKind(kind)
        self._items: deque[AudioFrame] = deque()
        self._ready = asyncio.Event()

    def put(self, frame: AudioFrame) -> str | None:
        if AudioFrameKind(frame.kind) is not self.kind:
            raise ProtocolError("audio_direction_mismatch")
        _validate_audio_frame(frame)
        overrun: str | None = None
        if len(self._items) >= self.capacity:
            if self.kind is AudioFrameKind.MIC:
                self._items.popleft()
                if self._items:
                    self._items[0] = replace(
                        self._items[0], flags=self._items[0].flags | DISCONTINUITY_FLAG
                    )
                else:
                    frame = replace(frame, flags=frame.flags | DISCONTINUITY_FLAG)
                overrun = "drop_oldest"
            else:
                self.clear()
                return "cancel_stream"
        self._items.append(frame)
        self._ready.set()
        return overrun

    async def get(self) -> AudioFrame:
        while not self._items:
            self._ready.clear()
            await self._ready.wait()
        item = self._items.popleft()
        if not self._items:
            self._ready.clear()
        return item

    def clear(self) -> None:
        self._items.clear()
        self._ready.clear()

    def empty(self) -> bool:
        return not self._items

    def __len__(self) -> int:
        return len(self._items)


SendPort = Callable[[dict[str, Any] | bytes], Awaitable[None]]


class OutboundWriter:
    """Serialize bounded priority controls and media without polling or stranded send waiters."""

    def __init__(
        self,
        send: SendPort,
        *,
        max_audio_frames: int = TTS_QUEUE_FRAMES,
        max_control_events: int = MAX_CONTROL_EVENTS,
    ) -> None:
        if isinstance(max_audio_frames, bool) or not isinstance(max_audio_frames, int) or max_audio_frames < 1:
            raise ValueError("outbound audio bound must be positive")
        if isinstance(max_control_events, bool) or not isinstance(max_control_events, int) or max_control_events < 1:
            raise ValueError("outbound control bound must be positive")
        self._send = send
        self._max_audio_frames = max_audio_frames
        self._max_control_events = max_control_events
        self._control: deque[
            tuple[dict[str, Any], asyncio.Future[None] | None, str | None]
        ] = deque()
        self._critical_keys: set[str] = set()
        self._diagnostics: OrderedDict[str, tuple[dict[str, Any], asyncio.Future[None] | None]] = OrderedDict()
        self._audio: deque[bytes] = deque()
        self._ready = asyncio.Event()
        self._closed = False
        self._active_waiter: asyncio.Future[None] | None = None

    @property
    def pending_control_count(self) -> int:
        return len(self._control) + len(self._diagnostics)

    def queue_control(
        self,
        event: dict[str, Any],
        *,
        diagnostic_key: str | None = None,
        priority: bool = True,
    ) -> bool:
        if self._closed:
            return False
        if diagnostic_key is not None:
            if (
                diagnostic_key in self._diagnostics
                or diagnostic_key in self._critical_keys
            ):
                return False
            if priority:
                while self.pending_control_count >= self._max_control_events and self._diagnostics:
                    self._diagnostics.popitem(last=False)
                if self.pending_control_count >= self._max_control_events:
                    return False
                self._critical_keys.add(diagnostic_key)
                self._control.append((event, None, diagnostic_key))
            else:
                if self.pending_control_count >= self._max_control_events:
                    return False
                self._diagnostics[diagnostic_key] = (event, None)
        else:
            while self.pending_control_count >= self._max_control_events and self._diagnostics:
                self._diagnostics.popitem(last=False)
            if self.pending_control_count >= self._max_control_events:
                return False
            self._control.append((event, None, None))
        self._ready.set()
        return True

    async def send_control(self, event: dict[str, Any]) -> None:
        if self._closed:
            raise ConnectionError("outbound writer is closed")
        waiter = asyncio.get_running_loop().create_future()
        while self.pending_control_count >= self._max_control_events and self._diagnostics:
            self._diagnostics.popitem(last=False)
        if self.pending_control_count >= self._max_control_events:
            raise ConnectionError("outbound control queue is full")
        self._control.append((event, waiter, None))
        self._ready.set()
        await waiter

    def queue_audio(self, data: bytes) -> str | None:
        if self._closed:
            return "closed"
        if len(self._audio) >= self._max_audio_frames:
            self._audio.clear()
            return "cancel_stream"
        self._audio.append(data)
        self._ready.set()
        return None

    async def send_next(self) -> None:
        while not self._control and not self._diagnostics and not self._audio:
            if self._closed:
                return
            self._ready.clear()
            await self._ready.wait()
        waiter: asyncio.Future[None] | None = None
        if self._control:
            value, waiter, key = self._control.popleft()
            if key is not None:
                self._critical_keys.discard(key)
        elif self._diagnostics:
            _, (value, waiter) = self._diagnostics.popitem(last=False)
        else:
            value = self._audio.popleft()
        if not self._control and not self._diagnostics and not self._audio:
            self._ready.clear()
        self._active_waiter = waiter
        try:
            await self._send(value)
        except asyncio.CancelledError:
            if waiter is not None and not waiter.done():
                waiter.set_exception(ConnectionError("outbound writer was cancelled"))
            raise
        except Exception as exc:
            if waiter is not None and not waiter.done():
                waiter.set_exception(exc)
            raise
        else:
            if waiter is not None and not waiter.done():
                waiter.set_result(None)
        finally:
            if self._active_waiter is waiter:
                self._active_waiter = None

    async def run(self) -> None:
        while not self._closed:
            await self.send_next()

    def flush_audio(self) -> None:
        self._audio.clear()

    def close(self, error: Exception | None = None) -> None:
        self._closed = True
        self._audio.clear()
        failure = error or ConnectionError("outbound writer is closed")
        if self._active_waiter is not None and not self._active_waiter.done():
            self._active_waiter.set_exception(failure)
        while self._control:
            _, waiter, key = self._control.popleft()
            if key is not None:
                self._critical_keys.discard(key)
            if waiter is not None and not waiter.done():
                waiter.set_exception(failure)
        while self._diagnostics:
            _, (_, waiter) = self._diagnostics.popitem(last=False)
            if waiter is not None and not waiter.done():
                waiter.set_exception(failure)
        self._ready.set()


class CapabilityTokens:
    """Issue bounded, opaque, origin-bound, one-shot capabilities for browser WebSocket upgrades."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 15.0,
        max_tokens: int = 128,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
            raise ValueError("capability TTL must be positive")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1:
            raise ValueError("capability token bound must be positive")
        self.ttl_seconds = ttl_seconds
        self.max_tokens = max_tokens
        self.clock = clock or time.monotonic
        self._tokens: OrderedDict[str, tuple[str, float]] = OrderedDict()

    def issue(self, origin: str) -> str:
        normalized = _origin(origin)
        now = self.clock()
        self._prune(now)
        while len(self._tokens) >= self.max_tokens:
            self._tokens.popitem(last=False)
        token = secrets.token_urlsafe(32)
        self._tokens[token] = (normalized, now + self.ttl_seconds)
        return token

    def consume(self, token: str, origin: str) -> bool:
        if not isinstance(token, str) or not token or len(token) > 256:
            return False
        try:
            normalized = _origin(origin)
        except ValueError:
            return False
        now = self.clock()
        self._prune(now)
        record = self._tokens.pop(token, None)
        return bool(
            record is not None
            and record[1] >= now
            and hmac.compare_digest(record[0], normalized)
        )

    def _prune(self, now: float) -> None:
        expired = [token for token, (_, deadline) in self._tokens.items() if deadline < now]
        for token in expired:
            self._tokens.pop(token, None)


def validate_control_message(raw: str) -> dict[str, Any]:
    """Parse one bounded finite JSON object and enforce its common type/sequence envelope."""

    if not isinstance(raw, str):
        raise ProtocolError("invalid_json_type")
    if len(raw.encode("utf-8")) > MAX_CONTROL_BYTES:
        raise ProtocolError("message_size_limit", "control message exceeds size limit")

    def reject_constant(_value: str) -> None:
        raise ProtocolError("invalid_json_number")

    try:
        value = json.loads(raw, parse_constant=reject_constant)
    except ProtocolError:
        raise
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ProtocolError("invalid_json") from exc
    if not isinstance(value, dict):
        raise ProtocolError("invalid_control_object")
    _validate_json_value(value, 1)
    event_type = value.get("type")
    sequence = value.get("seq")
    if not isinstance(event_type, str) or not event_type:
        raise ProtocolError("invalid_event_type")
    if (
        isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or not 1 <= sequence <= MAX_SERVER_SEQUENCE
    ):
        raise ProtocolError("invalid_sequence")
    return value


def _validate_json_value(value: Any, depth: int) -> None:
    if depth > MAX_JSON_DEPTH:
        raise ProtocolError("json_depth_limit", "JSON exceeds depth limit")
    if isinstance(value, str):
        if len(value) > MAX_STRING_CHARS:
            raise ProtocolError("json_string_limit", "JSON string exceeds limit")
        return
    if value is None or isinstance(value, bool) or isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ProtocolError("invalid_json_number")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item, depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > MAX_STRING_CHARS:
                raise ProtocolError("json_string_limit")
            _validate_json_value(item, depth + 1)
        return
    raise ProtocolError("invalid_json_value")


@dataclass(slots=True)
class _ClauseOffer:
    """Single-settlement ownership token for one renderer clause offer."""

    clause_id: str
    renderer_epoch: int
    settled: bool = False


@dataclass(frozen=True, slots=True)
class _CleanupActions:
    """Runtime side effects captured by a lease-owned synchronous session transition."""

    tokens: tuple[_ClauseOffer, ...]
    reject_candidate: bool
    notify_disconnected: bool


@dataclass
class _Session:
    websocket: WebSocket
    hub: "RealtimeHub"
    epoch: int = 0
    client_id: str = ""
    mode: str = "voice"
    incoming_sequence: int = 0
    outgoing_sequence: int = 0
    opened: bool = False
    mic_active: bool = False
    playback_blocked: bool = False
    speech_candidate: bool = False
    mic_stream_id: int | None = None
    revoked: bool = False
    ready: bool = False
    cleanup_state: str = "live"

    def __post_init__(self) -> None:
        self.loop = asyncio.get_running_loop()

        async def send(value: dict[str, Any] | bytes) -> None:
            await self.hub._send_owned(self, value)

        self.writer = OutboundWriter(send, max_audio_frames=self.hub.tts_queue_frames)
        self.writer_task: asyncio.Task[None] | None = None
        self.commands: OrderedDict[str, str] = OrderedDict()
        self.offered_clauses: dict[str, _ClauseOffer] = {}
        self.tracker = AudioFrameTracker()
        self._revoke_result: concurrent.futures.Future[None] | None = None
        self._transport_close_started = False

    def event(self, event_type: str, **fields: Any) -> dict[str, Any]:
        self.outgoing_sequence += 1
        return {"type": event_type, "seq": self.outgoing_sequence, **fields}

    async def control(self, event_type: str, **fields: Any) -> None:
        async def send() -> None:
            if self.revoked:
                raise ProtocolError("session_replaced")
            await self.writer.send_control(self.event(event_type, **fields))

        await self._run_async(send)

    async def queue_control(
        self,
        event_type: str,
        *,
        diagnostic_key: str | None = None,
        priority: bool = True,
        expected_epoch: int | None = None,
        **fields: Any,
    ) -> bool:
        def enqueue() -> bool:
            if self.revoked or (
                expected_epoch is not None and self.epoch != expected_epoch
            ):
                return False
            return self.writer.queue_control(
                self.event(event_type, **fields),
                diagnostic_key=diagnostic_key,
                priority=priority,
            )

        return bool(await self._run_sync(enqueue))

    async def queue_audio(self, data: bytes, *, expected_epoch: int) -> str | None:
        def enqueue() -> str | None:
            if self.revoked or self.epoch != expected_epoch:
                return "stale_renderer_lease"
            return self.writer.queue_audio(data)

        return await self._run_sync(enqueue)

    async def flush_audio(self, *, expected_epoch: int | None = None) -> bool:
        def flush() -> bool:
            if expected_epoch is not None and (self.revoked or self.epoch != expected_epoch):
                return False
            self.writer.flush_audio()
            return True

        return bool(await self._run_sync(flush))

    async def close_writer(self, error: Exception | None = None) -> None:
        await self._run_sync(lambda: self.writer.close(error))

    async def close_transport(self, *, code: int, reason: str) -> None:
        async def close() -> None:
            if self._transport_close_started:
                return
            self._transport_close_started = True
            try:
                await asyncio.wait_for(
                    self.websocket.close(code=code, reason=reason), timeout=0.05
                )
            except Exception:
                pass

        await self._run_async(close)

    async def _run_sync(self, callback: Callable[[], Any]) -> Any:
        if asyncio.get_running_loop() is self.loop:
            return callback()
        result: concurrent.futures.Future[Any] = concurrent.futures.Future()

        def invoke() -> None:
            if result.cancelled():
                return
            try:
                result.set_result(callback())
            except Exception as exc:
                result.set_exception(exc)

        self.loop.call_soon_threadsafe(invoke)
        return await asyncio.wrap_future(result)

    async def _run_async(self, factory: Callable[[], Awaitable[Any]]) -> Any:
        if asyncio.get_running_loop() is self.loop:
            return await factory()
        future = asyncio.run_coroutine_threadsafe(factory(), self.loop)
        try:
            return await asyncio.wrap_future(future)
        except asyncio.CancelledError:
            future.cancel()
            raise

    def revoke(self, new_epoch: int | None) -> None:
        """Promptly cancel the loop-owned writer and perform terminal transport I/O off the lease lock."""

        if self._revoke_result is not None:
            return
        result: concurrent.futures.Future[None] = concurrent.futures.Future()
        self._revoke_result = result

        def revoke_local() -> None:
            self.writer.close(ConnectionError("renderer lease replaced"))
            if self.writer_task is not None and not self.writer_task.done():
                self.writer_task.cancel()
            asyncio.create_task(self._finish_revoke(new_epoch, result))

        try:
            self.loop.call_soon_threadsafe(revoke_local)
        except RuntimeError:
            # A closed owner loop is already terminal: publish local completion so
            # a replacement on another loop never inherits a stranded revoke.
            self._transport_close_started = True
            if not result.done():
                result.set_result(None)

    async def wait_revoked(self) -> None:
        if self._revoke_result is not None:
            await asyncio.shield(asyncio.wrap_future(self._revoke_result))

    def register_clause_offer(self, clause_id: str) -> _ClauseOffer:
        """Register one offer token while the caller holds the Hub lease lock."""

        token = _ClauseOffer(clause_id, self.epoch)
        self.offered_clauses[clause_id] = token
        return token

    async def _finish_revoke(
        self, new_epoch: int | None, result: concurrent.futures.Future[None]
    ) -> None:
        try:
            if self.writer_task is not None:
                await asyncio.gather(self.writer_task, return_exceptions=True)
            if new_epoch is not None:
                try:
                    await asyncio.wait_for(
                        self.websocket.send_json(
                            self.event("session.replaced", renderer_epoch=new_epoch)
                        ),
                        timeout=0.05,
                    )
                except Exception:
                    pass
            await self.close_transport(
                code=4001 if new_epoch is not None else 1000,
                reason=(
                    "renderer lease replaced"
                    if new_epoch is not None
                    else "renderer disconnected"
                ),
            )
        finally:
            if not result.done():
                result.set_result(None)

    async def replaced(self, new_epoch: int) -> None:
        self.revoke(new_epoch)


@dataclass(frozen=True, slots=True)
class RendererLeasePort:
    """Immutable producer capability that can only address the Renderer generation it captured."""

    _hub: "RealtimeHub"
    _session: _Session
    renderer_epoch: int

    async def offer_clause(self, clause: Any) -> str | None:
        return await self._hub._offer_clause(self, clause)

    async def queue_tts(self, frame: AudioFrame) -> str | None:
        return await self._hub.queue_tts(self, frame)

    async def cancel_clause(self, clause_id: str, reason: str) -> str | None:
        return await self._hub._cancel_clause(self, clause_id, reason)


class RealtimeHub:
    """Process-global renderer lease, runtime adapter, and bounded media owner."""

    def __init__(
        self,
        *,
        runtime: Any | None = None,
        allowed_origins: set[str] | frozenset[str] | None = None,
        token_store: CapabilityTokens | None = None,
        mic_queue_frames: int = MIC_QUEUE_FRAMES,
        tts_queue_frames: int = TTS_QUEUE_FRAMES,
    ) -> None:
        self.runtime = runtime
        self.accepting = True
        configured_origins = (
            {"http://127.0.0.1:6400", "http://localhost:6400"}
            if allowed_origins is None
            else allowed_origins
        )
        self.allowed_origins = frozenset(_origin(item) for item in configured_origins)
        self.tokens = token_store or CapabilityTokens()
        self.mic_queue = BoundedAudioQueue(mic_queue_frames, AudioFrameKind.MIC)
        if isinstance(tts_queue_frames, bool) or not isinstance(tts_queue_frames, int) or tts_queue_frames < 1:
            raise ValueError("TTS queue capacity must be positive")
        self.tts_queue_frames = tts_queue_frames
        self.tts_tracker = AudioFrameTracker()
        self._session: _Session | None = None
        self._epoch = 0
        self._lease_lock = _CrossLoopLock()
        self._effect_lock = _CrossLoopLock()

    def start_accepting(self) -> None:
        """Open lifespan-owned admission without probing Renderer or providers."""

        self.accepting = True

    async def stop_accepting(self) -> None:
        """Fence new tokens/sessions and revoke the currently owned Renderer lease."""

        self.accepting = False
        session = self._session
        if session is not None:
            await self.release(session)

    @property
    def connected(self) -> bool:
        return self._session is not None and self._session.opened

    @property
    def renderer_epoch(self) -> int:
        return self._epoch

    @property
    def mode(self) -> str | None:
        return self._session.mode if self._session is not None and self._session.opened else None

    def origin_allowed(self, origin: str | None) -> bool:
        if origin is None:
            return False
        try:
            return _origin(origin) in self.allowed_origins
        except ValueError:
            return False

    async def claim(self, session: _Session) -> int:
        async def transition() -> tuple[_Session | None, int]:
            async with self._effect_lock:
                async with self._lease_lock:
                    old = self._session
                    if old is session:
                        return None, session.epoch
                    actions = None
                    if old is not None:
                        actions = self._begin_cleanup_locked(old, replaced=True)
                        old.revoked = True
                        old.ready = False
                    self._epoch += 1
                    session.epoch = self._epoch
                    session.revoked = False
                    session.ready = False
                    self._session = session
                    self.mic_queue.clear()
                    self.tts_tracker.clear()
                    epoch = session.epoch
                if actions is not None:
                    await self._run_cleanup_actions(actions)
                return old, epoch

        (old, epoch), cancelled = await _complete_on_cancel(transition())
        if old is not None:
            old.revoke(epoch)
        if cancelled:
            if old is not None:
                await old.wait_revoked()
            raise asyncio.CancelledError
        return epoch

    async def release(self, session: _Session) -> None:
        async def cleanup_owned() -> bool:
            async with self._effect_lock:
                async with self._lease_lock:
                    if self._session is not session:
                        return False
                    actions = self._begin_cleanup_locked(session, replaced=False)
                    session.revoked = True
                    session.ready = False
                    self._session = None
                await self._run_cleanup_actions(actions)
                session.revoke(None)
                await session.wait_revoked()
                return True

        _owned, cancelled = await _complete_on_cancel(cleanup_owned())
        if cancelled:
            raise asyncio.CancelledError

    def _require_current_locked(self, session: _Session) -> None:
        if (
            self._session is not session
            or not session.opened
            or session.epoch != self._epoch
        ):
            raise ProtocolError("session_replaced")

    async def _send_owned(self, session: _Session, value: dict[str, Any] | bytes) -> None:
        if not session.opened:
            await self._send_raw(session, value)
            return
        async with self._lease_lock:
            self._require_current_locked(session)
        await self._send_raw(session, value)

    @staticmethod
    async def _send_raw(session: _Session, value: dict[str, Any] | bytes) -> None:
        if isinstance(value, bytes):
            await session.websocket.send_bytes(value)
        else:
            await session.websocket.send_json(value)

    def _begin_cleanup_locked(
        self, session: _Session, *, replaced: bool
    ) -> _CleanupActions:
        """Retire session-owned state under the lease lock and return deferred side effects."""

        session.ready = False
        if session.cleanup_state != "live":
            return _CleanupActions((), False, False)
        session.cleanup_state = "cleaning"
        reject_candidate = session.speech_candidate and self.runtime is not None
        session.mic_active = False
        session.playback_blocked = True
        session.speech_candidate = False
        session.mic_stream_id = None
        session.tracker.clear(preserve_terminals=True, retire_active=True)
        self.mic_queue.clear()
        self.tts_tracker.clear()
        tokens = tuple(
            token for token in tuple(session.offered_clauses.values())
            if self._take_clause_offer_locked(session, token)
        )
        session.offered_clauses.clear()
        session.cleanup_state = "cleaned"
        return _CleanupActions(
            tokens, reject_candidate,
            bool(self.runtime is not None and session.opened and not replaced),
        )

    async def _run_cleanup_actions(self, actions: _CleanupActions) -> None:
        """Settle captured Runtime effects with no lease lock held."""

        if self.runtime is None:
            return
        if actions.reject_candidate:
            try:
                await _maybe_await(self.runtime.reject_speech_candidate())
            except Exception:
                pass
        for token in actions.tokens:
            try:
                await self._notify_clause_settlement(token, complete=False)
            except Exception:
                pass
        if actions.notify_disconnected:
            try:
                disconnected = getattr(self.runtime, "renderer_disconnected", None)
                if callable(disconnected):
                    await _maybe_await(disconnected())
                else:
                    await _maybe_await(self.runtime.vad_stop_candidate())
            except Exception:
                pass

    async def acquire_renderer_port(self) -> RendererLeasePort | None:
        """Capture the current immutable Renderer producer capability, or return no lease."""

        async with self._lease_lock:
            session = self._session
            if session is None or not session.opened or not session.ready or session.revoked:
                return None
            return RendererLeasePort(self, session, session.epoch)

    async def speech_candidate_started(self) -> None:
        """Pause the current voice Renderer as soon as backend VAD sees a candidate."""

        async with self._lease_lock:
            session = self._session
            if (
                session is None
                or not session.opened
                or not session.ready
                or session.revoked
                or session.mode != "voice"
                or not session.mic_active
            ):
                return
            already_blocked = session.speech_candidate and session.playback_blocked
            session.speech_candidate = True
            session.playback_blocked = True
            epoch = session.epoch
        if not already_blocked:
            await session.queue_control(
                "playback.pause",
                expected_epoch=epoch,
                renderer_epoch=epoch,
                reason="speech_candidate",
            )

    async def speech_candidate_rejected(self) -> None:
        """Release a false-positive VAD block without discarding queued playback."""

        async with self._lease_lock:
            session = self._session
            if session is None or not session.opened or session.revoked:
                return
            had_candidate = session.speech_candidate
            session.speech_candidate = False
            session.playback_blocked = session.mode == "text"
            epoch = session.epoch
            voice = session.mode == "voice"
        if had_candidate and voice:
            await session.queue_control(
                "playback.resume",
                expected_epoch=epoch,
                renderer_epoch=epoch,
                reason="speech_rejected",
            )
            await session.queue_control(
                "state.snapshot",
                expected_epoch=epoch,
                **self.snapshot(session),
            )

    async def speech_confirmed(self) -> None:
        """Flush offered and queued speech after authoritative barge-in confirmation."""

        async with self._lease_lock:
            session = self._session
            if (
                session is None
                or not session.opened
                or session.revoked
                or not session.speech_candidate
            ):
                return
            session.playback_blocked = True
        await _cancel_playback(session, "confirmed_barge_in")

    async def speech_endpoint(self, *, complete: bool) -> None:
        """Keep incomplete speech blocked, or reopen voice delivery immediately before RESPOND."""

        async with self._lease_lock:
            session = self._session
            if session is None or not session.opened or session.revoked:
                return
            epoch = session.epoch
            if complete:
                session.speech_candidate = False
                session.playback_blocked = session.mode == "text"
            else:
                session.playback_blocked = True
            voice = session.mode == "voice"
        if complete and voice:
            await session.queue_control(
                "playback.resume",
                expected_epoch=epoch,
                renderer_epoch=epoch,
                reason="eou_complete",
            )
        await session.queue_control(
            "state.snapshot",
            expected_epoch=epoch,
            **self.snapshot(session),
        )

    def _require_port_locked(self, port: RendererLeasePort) -> _Session:
        session = port._session
        if (
            port._hub is not self
            or self._session is not session
            or session.revoked
            or not session.ready
            or session.epoch != port.renderer_epoch
            or self._epoch != port.renderer_epoch
        ):
            raise ProtocolError("stale_renderer_lease")
        return session

    async def _offer_clause(self, port: RendererLeasePort, clause: Any) -> str | None:
        """Offer one clause only through the immutable lease generation supplied by its producer."""

        async with self._lease_lock:
            try:
                session = self._require_port_locked(port)
            except ProtocolError:
                return "stale_renderer_lease"
            clause_id = str(clause.clause_id)
            if clause_id in session.offered_clauses:
                return "duplicate_clause"
            token = session.register_clause_offer(clause_id)
            epoch = port.renderer_epoch
        queued = await session.queue_control(
            "clause.offer",
            expected_epoch=epoch,
            renderer_epoch=epoch,
            clause_id=clause_id,
            text=str(clause.text),
            invoke_id=str(clause.invoke_id),
            input_id=str(clause.input_id),
            revision=int(clause.revision),
            motion_token=clause.motion_token,
            expression_token=clause.expression_token,
        )
        if queued:
            return None
        async with self._lease_lock:
            try:
                self._require_port_locked(port)
            except ProtocolError:
                return "stale_renderer_lease"
            taken = self._take_clause_offer_locked(session, token)
        if taken:
            try:
                await self._notify_clause_settlement(token, complete=False)
            except Exception:
                pass
        return "offer_enqueue_failed"

    @staticmethod
    def _take_clause_offer_locked(session: _Session, token: _ClauseOffer) -> bool:
        """Claim one offer settlement while the caller holds the lease lock."""

        if token.settled or session.offered_clauses.get(token.clause_id) is not token:
            return False
        token.settled = True
        session.offered_clauses.pop(token.clause_id, None)
        return True

    async def _notify_clause_settlement(
        self, token: _ClauseOffer, *, complete: bool
    ) -> bool:
        """Apply the captured clause settlement after the lease lock has been released."""

        if self.runtime is None:
            return False
        return bool(
            await _maybe_await(
                self.runtime.renderer_ack(token.clause_id, complete=complete)
            )
        )

    async def _settle_clause_offer(
        self, session: _Session, token: _ClauseOffer, *, complete: bool
    ) -> bool:
        async with self._lease_lock:
            self._require_current_locked(session)
            taken = self._take_clause_offer_locked(session, token)
        if not taken:
            return False
        return await self._notify_clause_settlement(token, complete=complete)

    async def _cancel_clause(
        self, port: RendererLeasePort, clause_id: str, reason: str
    ) -> str | None:
        """Interrupt one offered clause through its captured Renderer generation."""

        async with self._lease_lock:
            try:
                session = self._require_port_locked(port)
            except ProtocolError:
                return "stale_renderer_lease"
            token = session.offered_clauses.get(clause_id)
            if token is None:
                return "unknown_clause"
            self._take_clause_offer_locked(session, token)
            self.tts_tracker.clear(preserve_terminals=True, retire_active=True)
            epoch = port.renderer_epoch
        try:
            await self._notify_clause_settlement(token, complete=False)
        except Exception:
            pass
        await session.flush_audio(expected_epoch=epoch)
        await session.queue_control(
            "clause.cancel", expected_epoch=epoch, renderer_epoch=epoch,
            clause_id=clause_id, reason=reason,
        )
        await session.queue_control(
            "playback.stop", expected_epoch=epoch,
            diagnostic_key=f"playback.stop.{reason}", renderer_epoch=epoch, reason=reason,
        )
        return None

    async def queue_tts(self, port: RendererLeasePort, frame: AudioFrame) -> str | None:
        """Queue one 24 kHz TTS frame; overflow cancels the stream and tells the active Renderer."""

        if AudioFrameKind(frame.kind) is not AudioFrameKind.TTS:
            raise ProtocolError("audio_direction_mismatch")
        async with self._lease_lock:
            try:
                session = self._require_port_locked(port)
            except ProtocolError:
                return "stale_renderer_lease"
            if session.playback_blocked:
                return "playback_blocked"
            ordering = self.tts_tracker.accept(frame)
            if ordering == "duplicate":
                return "drop_duplicate"
            encoded = pack_audio_frame(frame)
            epoch = port.renderer_epoch
        result = await session.queue_audio(encoded, expected_epoch=epoch)
        if result == "cancel_stream":
            async with self._lease_lock:
                try:
                    self._require_port_locked(port)
                except ProtocolError:
                    return "stale_renderer_lease"
                self.tts_tracker.clear(preserve_terminals=True, retire_active=True)
            await session.flush_audio(expected_epoch=epoch)
            await session.queue_control(
                "queue.overrun",
                expected_epoch=epoch,
                diagnostic_key="tts.cancel_stream",
                priority=False,
                direction="tts",
                action="cancel_stream",
                stream_id=frame.stream_id,
            )
            await session.queue_control(
                "playback.stop",
                expected_epoch=epoch,
                diagnostic_key="playback.stop.tts_overrun",
                renderer_epoch=epoch,
                reason="tts_overrun",
            )
        return result

    def snapshot(self, session: _Session) -> dict[str, Any]:
        active = getattr(self.runtime, "active_input", None) if self.runtime is not None else None
        if active is None:
            active_value = None
        elif is_dataclass(active):
            active_value = asdict(active)
        else:
            active_value = {
                "input_id": str(getattr(active, "input_id", "")),
                "revision": int(getattr(active, "revision", 0)),
                "latest_transcript": str(getattr(active, "latest_transcript", "")),
            }
        return {
            "renderer_epoch": session.epoch,
            "mode": session.mode,
            "connected": True,
            "mic_active": session.mic_active,
            "playback_blocked": session.playback_blocked,
            "speech_candidate": session.speech_candidate,
            "active_input": active_value,
            "context_version": int(getattr(self.runtime, "latest_context_version", 0)),
        }


class RealtimeTokenResponse(BaseModel):
    """Public response for a one-shot browser upgrade capability."""

    token: str
    expires_in_ms: int
    subprotocol: str


router = APIRouter(prefix="/api/v1/main-agent", tags=["main-agent-realtime"])


def _hub(scope: Request | WebSocket) -> RealtimeHub:
    return scope.app.state.main_agent_realtime


@router.post(
    "/realtime-token",
    response_model=RealtimeTokenResponse,
    status_code=status.HTTP_201_CREATED,
)
async def issue_realtime_token(request: Request, response: Response) -> RealtimeTokenResponse:
    """Issue one short-lived capability only to a configured desktop origin."""

    hub = _hub(request)
    if not hub.accepting:
        raise HTTPException(status_code=503, detail="realtime is not accepting sessions")
    origin = request.headers.get("origin")
    if not hub.origin_allowed(origin):
        raise HTTPException(status_code=403, detail="origin is not allowed")
    assert origin is not None
    response.headers["Cache-Control"] = "no-store"
    return RealtimeTokenResponse(
        token=hub.tokens.issue(origin),
        expires_in_ms=int(hub.tokens.ttl_seconds * 1_000),
        subprotocol=SUBPROTOCOL,
    )


@router.websocket("/realtime")
async def main_agent_realtime(websocket: WebSocket) -> None:
    """Run one validated renderer session until replacement, disconnect, or fatal protocol error."""

    hub = _hub(websocket)
    if not hub.accepting:
        await websocket.close(code=1013, reason="realtime is shutting down")
        return
    origin = websocket.headers.get("origin")
    offered = [item.strip() for item in websocket.headers.get("sec-websocket-protocol", "").split(",")]
    capabilities = [item.removeprefix("aurabot.capability.") for item in offered if item.startswith("aurabot.capability.")]
    if not hub.origin_allowed(origin) or SUBPROTOCOL not in offered or len(capabilities) != 1:
        await websocket.close(code=4403, reason="realtime handshake rejected")
        return
    assert origin is not None
    if not hub.tokens.consume(capabilities[0], origin):
        await websocket.close(code=4403, reason="realtime capability rejected")
        return

    await websocket.accept(subprotocol=SUBPROTOCOL)
    session = _Session(websocket, hub)
    try:
        await _supervise_session(session)
    finally:
        with anyio.CancelScope(shield=True):
            try:
                await hub.release(session)
            finally:
                await session.close_writer()


async def _supervise_session(session: _Session) -> None:
    """Race the loop-owned writer and receiver, explicitly settle both, and contain writer failure."""

    if session.writer_task is None:
        session.writer_task = asyncio.create_task(
            _run_writer(session), name="main-agent-realtime-writer"
        )
    receiver_task = asyncio.create_task(
        _receive_loop(session), name="main-agent-realtime-receiver"
    )
    tasks = {session.writer_task, receiver_task}
    try:
        _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except asyncio.CancelledError:
        settlement = asyncio.create_task(_cancel_and_settle(tasks))
        await _await_shielded(settlement)
        raise
    for task in pending:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    writer_error: Exception | None = None
    for task in tasks:
        if task.cancelled():
            continue
        try:
            task.result()
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            if task is session.writer_task:
                writer_error = exc
            else:
                raise
    if writer_error is not None:
        await session.close_transport(code=1011, reason="realtime writer failed")


async def _cancel_and_settle(tasks: set[asyncio.Task[Any]]) -> None:
    """Cancel and retrieve every supervised child result before returning."""

    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _run_writer(session: _Session) -> None:
    """Close the transport on writer exit so a blocked receiver is always supervised and released."""

    try:
        await session.writer.run()
    except asyncio.CancelledError:
        raise
    except ProtocolError as exc:
        if exc.code == "session_replaced":
            return
        await session.close_transport(code=1011, reason="realtime writer failed")
        raise
    except Exception:
        await session.close_transport(code=1011, reason="realtime writer failed")
        raise


async def _receive_loop(session: _Session) -> None:
    while True:
        try:
            message = await session.websocket.receive()
        except WebSocketDisconnect:
            return
        message_type = message.get("type")
        if message_type == "websocket.disconnect":
            return
        raw_text = message.get("text")
        raw_bytes = message.get("bytes")
        if raw_text is not None:
            try:
                event = validate_control_message(raw_text)
                await _handle_control(session, event)
            except ProtocolError as exc:
                await session.control("session.error", code=exc.code, fatal=exc.fatal)
                if exc.fatal:
                    await session.close_transport(code=4400, reason="protocol error")
                    return
        elif raw_bytes is not None:
            try:
                await _handle_audio(session, bytes(raw_bytes))
            except ProtocolError as exc:
                await session.control("session.error", code=exc.code, fatal=exc.fatal)
        else:
            await session.control("session.error", code="invalid_websocket_message", fatal=True)
            await session.close_transport(code=4400, reason="protocol error")
            return


async def _handle_control(session: _Session, event: dict[str, Any]) -> None:
    sequence = int(event["seq"])
    event_type = str(event["type"])

    if not session.opened:
        if sequence <= session.incoming_sequence:
            raise ProtocolError("sequence_not_monotonic")
        session.incoming_sequence = sequence
        if event_type != "session.open":
            raise ProtocolError("session_open_required", fatal=True)
        await _open_session(session, event)
        return

    async with session.hub._effect_lock:
        async with session.hub._lease_lock:
            session.hub._require_current_locked(session)
            if sequence <= session.incoming_sequence:
                raise ProtocolError("sequence_not_monotonic")
            session.incoming_sequence = sequence
            if event_type == "session.open":
                raise ProtocolError("session_already_open")
        await _handle_control_owned(session, event, event_type)


async def _handle_control_owned(
    session: _Session, event: dict[str, Any], event_type: str
) -> None:
    """Apply one command while the Hub lease lock fences its complete Runtime mutation."""

    command_id = _uuid_field(event, "command_id")
    fingerprint = _command_fingerprint(event)
    previous = session.commands.get(command_id)
    if previous is not None:
        if previous != fingerprint:
            await _command_ack(session, command_id, "rejected", "command_id_conflict")
        else:
            await _command_ack(session, command_id, "duplicate")
        return
    if len(session.commands) >= MAX_COMMANDS:
        await _command_ack(session, command_id, "rejected", "command_limit")
        return
    session.commands[command_id] = fingerprint

    try:
        if event_type == "text.submit":
            _keys(event, {"type", "seq", "command_id", "text"})
            text = _text_field(event, "text", max_chars=8_192)
            if session.hub.runtime is not None:
                await _maybe_await(session.hub.runtime.submit_text(text))
        elif event_type == "mic.start":
            _keys(event, {"type", "seq", "command_id"})
            if session.mode != "voice":
                raise ProtocolError("voice_mode_required")
            if session.mic_active:
                raise ProtocolError("mic_already_active")
            session.mic_active = True
            session.mic_stream_id = None
            session.tracker.clear(preserve_terminals=True)
        elif event_type == "mic.stop":
            _keys(event, {"type", "seq", "command_id"})
            await _stop_mic(session)
        elif event_type == "speech.candidate":
            _keys(event, {"type", "seq", "command_id"})
            if not session.mic_active:
                raise ProtocolError("mic_not_active")
            session.speech_candidate = True
            session.playback_blocked = True
            if session.hub.runtime is not None:
                await _maybe_await(session.hub.runtime.vad_start())
            await session.queue_control(
                "playback.pause", renderer_epoch=session.epoch, reason="speech_candidate"
            )
        elif event_type == "speech.rejected":
            _keys(event, {"type", "seq", "command_id"})
            if not session.speech_candidate:
                raise ProtocolError("speech_candidate_required")
            if session.hub.runtime is not None:
                await _maybe_await(session.hub.runtime.reject_speech_candidate())
            session.speech_candidate = False
            session.playback_blocked = session.mode == "text"
            if session.mode == "voice":
                await session.queue_control(
                    "playback.resume", renderer_epoch=session.epoch, reason="speech_rejected"
                )
            await session.queue_control("state.snapshot", **session.hub.snapshot(session))
        elif event_type == "speech.confirmed":
            _keys(event, {"type", "seq", "command_id", "transcript", "complete"})
            transcript = _text_field(event, "transcript", max_chars=8_192)
            complete = _bool_field(event, "complete")
            if not session.speech_candidate:
                raise ProtocolError("speech_candidate_required")
            if session.hub.runtime is not None:
                confirm = getattr(session.hub.runtime, "confirm_speech", None)
                if callable(confirm):
                    await _maybe_await(confirm())
                await _maybe_await(session.hub.runtime.update_transcript(
                    transcript,
                    segment_id=f"diagnostic-{command_id}",
                    request_prepare=False,
                ))
                if complete:
                    await _maybe_await(session.hub.runtime.eou_complete())
                else:
                    await _maybe_await(session.hub.runtime.eou_incomplete())
            await _cancel_playback(session, "confirmed_barge_in")
            session.speech_candidate = False
            session.playback_blocked = session.mode == "text" or not complete
            await session.queue_control("state.snapshot", **session.hub.snapshot(session))
        elif event_type == "renderer.ack":
            _keys(event, {"type", "seq", "command_id", "renderer_epoch", "clause_id", "complete"})
            _current_epoch(session, event)
            clause_id = _text_field(event, "clause_id", max_chars=256)
            complete = _bool_field(event, "complete")
            token = session.offered_clauses.get(clause_id)
            if session.hub.runtime is None or token is None:
                raise ProtocolError("unknown_clause")
            accepted = await session.hub._settle_clause_offer(
                session, token, complete=complete
            )
            if not accepted and complete:
                raise ProtocolError("unknown_clause")
        elif event_type == "mode.set":
            _keys(event, {"type", "seq", "command_id", "renderer_epoch", "mode"})
            _current_epoch(session, event)
            mode = _mode_field(event)
            await _switch_mode(session, mode)
            await session.queue_control("state.snapshot", **session.hub.snapshot(session))
        else:
            raise ProtocolError("unknown_event_type")
    except ProtocolError as exc:
        await _command_ack(session, command_id, "rejected", exc.code)
        return
    except asyncio.CancelledError:
        raise
    except Exception:
        await _command_ack(session, command_id, "rejected", "runtime_unavailable")
        return
    await _command_ack(session, command_id, "accepted")


async def _open_session(session: _Session, event: dict[str, Any]) -> None:
    _keys(event, {"type", "seq", "command_id", "client_id", "mode"})
    command_id = _uuid_field(event, "command_id")
    session.client_id = _uuid_field(event, "client_id")
    session.mode = _mode_field(event)
    session.playback_blocked = session.mode == "text"
    session.opened = True
    try:
        await session.hub.claim(session)
    except Exception:
        session.opened = False
        raise
    session.commands[command_id] = _command_fingerprint(event)
    await session.control(
        "session.opened",
        protocol=SUBPROTOCOL,
        client_id=session.client_id,
        renderer_epoch=session.epoch,
        mode=session.mode,
    )
    await session.control("state.snapshot", **session.hub.snapshot(session))
    async with session.hub._lease_lock:
        session.hub._require_current_locked(session)
        session.ready = True


async def _handle_audio(session: _Session, encoded: bytes) -> None:
    if not session.opened:
        raise ProtocolError("session_open_required", fatal=True)
    frame = parse_audio_frame(encoded)
    if frame.kind is not AudioFrameKind.MIC:
        raise ProtocolError("audio_direction_mismatch")
    async with session.hub._lease_lock:
        session.hub._require_current_locked(session)
        if session.mode != "voice" or not session.mic_active:
            raise ProtocolError("mic_not_active")
        result = session.tracker.accept(frame)
        if result == "duplicate":
            await session.queue_control(
                "queue.overrun",
                diagnostic_key=f"mic.duplicate.{frame.stream_id}",
                priority=False,
                direction="mic",
                action="drop_duplicate",
                stream_id=frame.stream_id,
            )
            return
        if session.mic_stream_id is not None and frame.stream_id != session.mic_stream_id:
            session.tracker.discard(frame)
            raise ProtocolError("mic_stream_conflict")
        if session.mic_stream_id is None:
            session.mic_stream_id = frame.stream_id
        overrun = session.hub.mic_queue.put(frame)
        if result == "discontinuity":
            await session.queue_control(
                "queue.overrun",
                diagnostic_key=f"mic.discontinuity.{frame.stream_id}",
                priority=False,
                direction="mic",
                action="discontinuity",
                stream_id=frame.stream_id,
            )
        if overrun is not None:
            await session.queue_control(
                "queue.overrun",
                diagnostic_key=f"mic.{overrun}.{frame.stream_id}",
                priority=False,
                direction="mic",
                action=overrun,
                stream_id=frame.stream_id,
            )
        if frame.flags & FINAL_FLAG:
            session.mic_stream_id = None


async def _switch_mode(session: _Session, mode: str) -> None:
    if mode == session.mode:
        return
    session.playback_blocked = True
    await _stop_mic(session)
    await _cancel_playback(session, "mode_switch")
    session.playback_blocked = mode == "text"
    session.mode = mode


async def _cancel_playback(session: _Session, reason: str) -> None:
    """Flush queued media and reject every offered clause before one explicit stop control."""

    async with session.hub._lease_lock:
        session.hub._require_current_locked(session)
        session.hub.tts_tracker.clear(preserve_terminals=True, retire_active=True)
        tokens = tuple(
            token for token in tuple(session.offered_clauses.values())
            if session.hub._take_clause_offer_locked(session, token)
        )
        epoch = session.epoch
    await session.flush_audio(expected_epoch=epoch)
    for token in tokens:
        try:
            await session.hub._notify_clause_settlement(token, complete=False)
        except Exception:
            pass
        await session.queue_control(
            "clause.cancel", expected_epoch=epoch, renderer_epoch=epoch,
            clause_id=token.clause_id, reason=reason,
        )
    await session.queue_control(
        "playback.stop",
        expected_epoch=epoch,
        diagnostic_key=f"playback.stop.{reason}",
        renderer_epoch=epoch,
        reason=reason,
    )


async def _stop_mic(session: _Session) -> None:
    was_active = session.mic_active
    had_candidate = session.speech_candidate
    session.mic_active = False
    session.speech_candidate = False
    session.mic_stream_id = None
    session.tracker.clear(preserve_terminals=True, retire_active=True)
    session.hub.mic_queue.clear()
    if was_active and had_candidate and session.hub.runtime is not None:
        await _maybe_await(session.hub.runtime.reject_speech_candidate())


async def _command_ack(
    session: _Session, command_id: str, status_value: str, code: str | None = None
) -> None:
    fields: dict[str, Any] = {"command_id": command_id, "status": status_value}
    if code is not None:
        fields["code"] = code
    if not await session.queue_control("command.ack", **fields):
        raise ProtocolError("control_queue_overrun", fatal=True)


def _keys(event: Mapping[str, Any], allowed: set[str]) -> None:
    unexpected = set(event) - allowed
    missing = allowed - set(event)
    if unexpected or missing:
        raise ProtocolError("invalid_event_shape")


def _uuid_field(event: Mapping[str, Any], field: str) -> str:
    value = event.get(field)
    if not isinstance(value, str) or len(value) > 64:
        raise ProtocolError(f"invalid_{field}")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ProtocolError(f"invalid_{field}") from exc
    if str(parsed) != value.lower():
        raise ProtocolError(f"invalid_{field}")
    return str(parsed)


def _text_field(event: Mapping[str, Any], field: str, *, max_chars: int) -> str:
    value = event.get(field)
    if not isinstance(value, str) or not value.strip() or len(value) > max_chars:
        raise ProtocolError(f"invalid_{field}")
    return value


def _bool_field(event: Mapping[str, Any], field: str) -> bool:
    value = event.get(field)
    if not isinstance(value, bool):
        raise ProtocolError(f"invalid_{field}")
    return value


def _mode_field(event: Mapping[str, Any]) -> str:
    mode = event.get("mode")
    if mode not in {"voice", "text"}:
        raise ProtocolError("invalid_mode")
    return str(mode)


def _current_epoch(session: _Session, event: Mapping[str, Any]) -> None:
    epoch = event.get("renderer_epoch")
    if (
        isinstance(epoch, bool)
        or not isinstance(epoch, int)
        or epoch != session.epoch
        or session.hub._session is not session
        or session.hub.renderer_epoch != epoch
    ):
        raise ProtocolError("stale_renderer_epoch")


def _command_fingerprint(event: Mapping[str, Any]) -> str:
    return json.dumps(
        {key: value for key, value in event.items() if key != "seq"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _origin(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError("origin must be bounded text")
    if value.endswith("/"):
        value = value[:-1]
    if not (value.startswith("http://") or value.startswith("https://")):
        raise ValueError("origin must be HTTP(S)")
    if "/" in value.split("://", 1)[1]:
        raise ValueError("origin cannot contain a path")
    return value.lower()


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def _complete_on_cancel(awaitable: Awaitable[Any]) -> tuple[Any, bool]:
    """Finish one ownership transition after caller cancellation and report whether to propagate it."""

    task = asyncio.create_task(awaitable)
    return await _await_shielded(task)


async def _await_shielded(task: asyncio.Task[Any]) -> tuple[Any, bool]:
    """Await one owned task through repeated caller cancellation without cancelling it."""

    cancelled = False
    while True:
        try:
            return await asyncio.shield(task), cancelled
        except asyncio.CancelledError:
            cancelled = True
            if task.done():
                return task.result(), cancelled


__all__ = [
    "AUR1_HEADER_SIZE",
    "AudioFrame",
    "AudioFrameKind",
    "AudioFrameTracker",
    "BoundedAudioQueue",
    "CapabilityTokens",
    "DISCONTINUITY_FLAG",
    "FINAL_FLAG",
    "MAX_CONTROL_BYTES",
    "OutboundWriter",
    "ProtocolError",
    "RendererLeasePort",
    "RealtimeHub",
    "SUBPROTOCOL",
    "pack_audio_frame",
    "parse_audio_frame",
    "router",
    "validate_control_message",
]
