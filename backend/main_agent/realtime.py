"""Own the single profile-bound text/voice WebSocket, capability tokens and renderer delivery.

Acoustic activity overlays the session work state; silent voice sessions report no_audio.
Controlled speech preparation errors reach the renderer without provider details. Sessions begin in text mode; explicit commands activate speech or submit text. Only accepted user segments
and say_to_user clauses cross into desktop history. Text ACKs commit draft delivery, while voice ACKs
require completed audio; binary PCM and playback gates remain confined to voice mode. Typed input remains
available during voice mode, resetting microphone input while preserving spoken delivery. ProfileResources
is the sole Speech owner; this transport reads it and retires Runtime session state exactly once."""

# Cumulative source-consumption receipts bound PCM in flight to two seconds; they never commit history.

# Activation validates the whole UTF-8 SOUL document and adds only controlled name metadata.
from __future__ import annotations

import asyncio
import json
import secrets
import sqlite3
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException, Request, Response, WebSocket, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.websockets import WebSocketDisconnect

from .lifecycle import ActiveProfileManager, ProfileResources
from .metrics import speech_logger
from ..task.models import TaskStatus


SUBPROTOCOL = "aurabot.main-agent.v1"
PLAYBACK_WINDOW_SAMPLES = 48_000
PLAYBACK_CHUNK_SAMPLES = 4_800


class ProtocolError(ValueError):
    """Stable protocol rejection suitable for a session.error code."""


@dataclass(frozen=True, slots=True)
class TokenRecord:
    origin: str
    profile_id: str
    generation: int
    expires_at: float


class CapabilityTokens:
    """Keep a small set of short-lived, single-use Profile generation capabilities."""

    def __init__(self, ttl_seconds: float = 15.0, max_tokens: int = 256) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_tokens = max_tokens
        self._tokens: OrderedDict[str, TokenRecord] = OrderedDict()

    def issue(self, origin: str, profile_id: str, generation: int) -> str:
        now = time.monotonic()
        self._expire(now)
        token = secrets.token_urlsafe(32)
        self._tokens[token] = TokenRecord(origin, profile_id, generation, now + self.ttl_seconds)
        while len(self._tokens) > self.max_tokens:
            self._tokens.popitem(last=False)
        return token

    def consume(
        self,
        token: str,
        origin: str,
        *,
        active_profile_id: str | None,
        active_generation: int,
    ) -> TokenRecord | None:
        record = self._tokens.pop(token, None)
        if record is None:
            return None
        if (
            record.expires_at < time.monotonic()
            or not secrets.compare_digest(record.origin, origin)
            or record.profile_id != active_profile_id
            or record.generation != active_generation
        ):
            return None
        return record

    def _expire(self, now: float) -> None:
        for token, record in tuple(self._tokens.items()):
            if record.expires_at < now:
                self._tokens.pop(token, None)


class MicFrameQueue:
    """Bound exact microphone frames and discard the oldest on temporary consumer lag."""

    def __init__(self, max_frames: int = 50) -> None:
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(max_frames)

    def put(self, frame: bytes) -> bool:
        if not isinstance(frame, bytes) or len(frame) != 640:
            raise ValueError("microphone frame must be exactly 640 bytes")
        dropped = False
        if self._queue.full():
            self._queue.get_nowait()
            self._queue.task_done()
            dropped = True
        self._queue.put_nowait(frame)
        return dropped

    async def get(self) -> bytes:
        return await self._queue.get()

    def get_nowait(self) -> bytes:
        return self._queue.get_nowait()

    def clear(self) -> None:
        while not self._queue.empty():
            self._queue.get_nowait()
            self._queue.task_done()


@dataclass(slots=True, eq=False)
class VoiceSession:
    hub: "RealtimeHub"
    socket: Any
    profile_id: str
    generation: int
    send_lock: asyncio.Lock
    current_clause: str | None = None
    current_say_id: str | None = None
    closed: bool = False
    mode: str = "text"
    clause_mode: str = "text"
    clause_done: asyncio.Event | None = None
    audio_finished: bool = False
    audio_cancelled: bool = False
    input_active: bool = False
    work_state: str = "idle"
    sent_samples: int = 0
    consumed_samples: int = 0
    playback_progress: asyncio.Event = field(default_factory=asyncio.Event)
    retired_clauses: OrderedDict[str, None] = field(default_factory=OrderedDict)

    async def send(self, value: dict[str, Any] | bytes, *, clause_id: str | None = None) -> None:
        async with self.send_lock:
            if self.closed or self.hub.session is not self:
                raise ProtocolError("session_replaced")
            if clause_id is not None and (self.current_clause != clause_id or self.audio_cancelled):
                raise ProtocolError("unknown_clause")
            if isinstance(value, bytes):
                await self.socket.send_bytes(value)
            else:
                await self.socket.send_json(value)


@dataclass(frozen=True, slots=True)
class RendererPort:
    """Session-identity capability used only by the active serial speech delivery worker."""

    hub: "RealtimeHub"
    session: VoiceSession

    async def offer_clause(self, clause: Any) -> str | None:
        return await self.hub.offer_clause(self, clause)

    async def send_audio(self, pcm: bytes) -> str | None:
        return await self.hub.send_audio(self, pcm)

    async def finish_audio(self, clause_id: str) -> str | None:
        return await self.hub.finish_audio(self, clause_id)

    async def cancel_clause(self, clause_id: str, reason: str) -> str | None:
        return await self.hub.cancel_clause(self, clause_id, reason)


ProfileActivator = Callable[[str, str], Awaitable[int]]


class RealtimeHub:
    """Own one active Profile identity, microphone queue, and replaceable Pet voice session."""

    def __init__(
        self,
        *,
        allowed_origins: set[str] | frozenset[str] | None = None,
        token_store: CapabilityTokens | None = None,
        profile_activator: ProfileActivator | None = None,
    ) -> None:
        configured = {"http://127.0.0.1:6400", "http://localhost:6400"} if allowed_origins is None else allowed_origins
        self.allowed_origins = frozenset(_origin(value) for value in configured)
        self.tokens = token_store or CapabilityTokens()
        self.profile_activator = profile_activator
        self.profile_session_renewer: Callable[[str, int], Awaitable[int]] | None = None
        self.mic_queue = MicFrameQueue()
        self.active_profile_id: str | None = None
        self.active_generation = 0
        self.profile: ProfileResources | None = None
        self.session: VoiceSession | None = None
        self.accepting = True
        self._lock = asyncio.Lock()
        self.activation_lock = asyncio.Lock()
        self._session_lock = asyncio.Lock()

    @property
    def runtime(self) -> Any | None:
        return self.profile.runtime if self.profile is not None else None

    @property
    def speech(self) -> Any | None:
        return self.profile.speech if self.profile is not None else None

    def origin_allowed(self, origin: str | None) -> bool:
        if origin is None:
            return False
        try:
            return _origin(origin) in self.allowed_origins
        except ValueError:
            return False

    async def activate(self, profile_id: str, soul: str, **snapshot: Any) -> int:
        if self.profile_activator is None:
            raise RuntimeError("main_agent_unavailable")
        if self.activation_lock.locked():
            raise RuntimeError("activation_in_progress")
        async with self.activation_lock:
            if not self.accepting:
                raise RuntimeError("backend_restarting")
            generation = await self.profile_activator(profile_id, soul, **snapshot)
            if self.active_profile_id != profile_id or self.active_generation != generation:
                raise RuntimeError("profile_unavailable")
            return generation

    async def set_active(
        self, profile_id: str, generation: int, profile: ProfileResources
    ) -> None:
        async with self._session_lock:
            old = self.session if (
                self.active_profile_id != profile_id or self.active_generation != generation
            ) else None
            if old is not None:
                await self._close_locked(old, "profile_switched")
            self.active_profile_id = profile_id
            self.active_generation = generation
            self.profile = profile
            profile.runtime.user_message_listener = self.user_message
            profile.runtime.state_listener = self.agent_state
            profile.runtime.error_listener = self.session_error
            profile.runtime.say_closed_listener = self.say_closed

    async def clear_active(self, profile_id: str, generation: int) -> None:
        async with self._session_lock:
            if self.active_profile_id != profile_id or self.active_generation != generation:
                return
            if self.session is not None:
                await self._close_locked(self.session, "profile_switched")
            self.active_profile_id = None
            self.profile = None

    async def attach(self, socket: Any) -> VoiceSession:
        async with self._session_lock:
            if not self.accepting or self.runtime is None or self.active_profile_id is None:
                raise ProtocolError("profile_not_active")
            if self.session is not None:
                await self._close_locked(self.session, "session_replaced")
            session = VoiceSession(
                self, socket, self.active_profile_id, self.active_generation, asyncio.Lock(),
            )
            self.session = session
            self.mic_queue.clear()
        await session.send({"type": "session.ready", "profile_id": session.profile_id,
            "generation": session.generation, "config_revision": self.profile.revision if self.profile else 0})
        return session

    async def detach(self, session: VoiceSession) -> None:
        async with self._session_lock:
            if self.session is session:
                await self._close_locked(session, "session_closed", close_socket=False)

    async def _close_locked(
        self, session: VoiceSession, reason: str, *, close_socket: bool = True
    ) -> None:
        if session.closed:
            return
        async with self._lock:
            if session.current_clause is not None:
                await self._settle_locked(session, session.current_clause, complete=False)
            session.closed = True
            if self.session is session:
                self.session = None
            self.mic_queue.clear()
        # Hold session lifetime ownership, never the ACK lock while provider cleanup drains callbacks.
        async def cleanup() -> None:
            try:
                if self.profile is not None:
                    await self.profile.stop_speech()
            finally:
                if self.runtime is not None:
                    await self.runtime.renderer_disconnected()
                if close_socket:
                    try:
                        await session.socket.close(code=4001, reason=reason)
                    except Exception:
                        pass

        task = asyncio.create_task(cleanup(), name="realtime-session-cleanup")
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        await task
        if cancelled and close_socket:
            raise asyncio.CancelledError

    async def acquire_renderer_port(self) -> RendererPort | None:
        session = self.session
        return RendererPort(self, session) if session is not None and not session.closed else None

    def _current(self, port: RendererPort) -> VoiceSession | None:
        return port.session if port.hub is self and self.session is port.session and not port.session.closed else None

    async def offer_clause(self, port: RendererPort, clause: Any) -> str | None:
        session = self._current(port)
        if session is None:
            return "session_replaced"
        if session.current_clause is not None:
            return "clause_in_progress"
        if getattr(clause, "profile_id", "") and (
            clause.profile_id != session.profile_id or clause.generation != session.generation
            or clause.config_revision != (self.profile.revision if self.profile else 0)
        ):
            return "stale_avatar_scope"
        session.current_clause = str(clause.clause_id)
        session.current_say_id = getattr(clause, "say_id", None)
        session.clause_mode = session.mode
        session.clause_done = asyncio.Event()
        session.audio_finished = False
        session.audio_cancelled = False
        session.sent_samples = session.consumed_samples = 0
        session.playback_progress.clear()
        try:
            await session.send({"type": "clause.offer", "clause_id": session.current_clause, "text": str(clause.text), "mode": session.clause_mode,
                "profile_id": session.profile_id, "generation": session.generation,
                "config_revision": self.profile.revision if self.profile else 0,
                "say_id": getattr(clause, "say_id", ""), "call_id": getattr(clause, "call_id", ""),
                "invoke_id": getattr(clause, "invoke_id", ""), "avatar": dict(getattr(clause, "avatar", None) or {})})
            await self.agent_state("speaking")
        except Exception:
            session.current_clause = None
            return "session_replaced"
        return None

    async def send_audio(self, port: RendererPort, pcm: bytes) -> str | None:
        session = self._current(port)
        if session is None:
            return "session_replaced"
        if session.current_clause is None:
            return "unknown_clause"
        if not isinstance(pcm, bytes) or not pcm or len(pcm) % 2:
            raise ValueError("TTS PCM must contain complete 16-bit samples")
        clause_id = session.current_clause
        offset = 0
        while offset < len(pcm):
            if self._current(port) is None:
                return "session_replaced"
            if session.current_clause != clause_id or session.audio_cancelled:
                return "unknown_clause"
            available = PLAYBACK_WINDOW_SAMPLES - (session.sent_samples - session.consumed_samples)
            if available <= 0:
                session.playback_progress.clear()
                try:
                    await asyncio.wait_for(session.playback_progress.wait(), 10)
                except TimeoutError:
                    speech_logger.warning("playback_stalled clause=%s sent=%d consumed=%d", clause_id, session.sent_samples, session.consumed_samples)
                    return "playback_stalled"
                continue
            size = min(len(pcm) - offset, available * 2, PLAYBACK_CHUNK_SAMPLES * 2)
            session.sent_samples += size // 2
            try:
                await session.send(pcm[offset:offset + size], clause_id=clause_id)
            except ProtocolError as exc:
                return str(exc)
            offset += size
        return None

    async def renderer_progress(self, session: VoiceSession, clause_id: str, consumed_samples: int) -> None:
        """Release source PCM credit, independently of the physical-delivery ACK."""
        if self.session is not session or clause_id in session.retired_clauses:
            return
        if session.current_clause != clause_id or session.clause_mode != "voice":
            raise ProtocolError("unknown_clause")
        if type(consumed_samples) is not int or not 0 <= consumed_samples <= session.sent_samples:
            raise ProtocolError("invalid_playback_progress")
        if consumed_samples > session.consumed_samples:
            session.consumed_samples = consumed_samples
            session.playback_progress.set()

    async def finish_audio(self, port: RendererPort, clause_id: str) -> str | None:
        session = self._current(port)
        if session is None:
            return "session_replaced"
        if session.current_clause != clause_id:
            return "unknown_clause"
        session.audio_finished = True
        try:
            await session.send({"type": "clause.audio_end", "clause_id": clause_id}, clause_id=clause_id)
        except ProtocolError as exc:
            return str(exc)
        return None

    async def cancel_clause(self, port: RendererPort, clause_id: str, reason: str) -> str | None:
        async with self._lock:
            session = self._current(port)
            if session is None:
                return "session_replaced"
            if session.current_clause != clause_id:
                return "unknown_clause"
            session.audio_cancelled = True
            speech_logger.info("playback_cancelled clause=%s reason=%s sent=%d consumed=%d", clause_id, reason, session.sent_samples, session.consumed_samples)
            session.playback_progress.set()
            await session.send({"type": "clause.cancel", "clause_id": clause_id, "reason": reason})
            await self._settle_locked(session, clause_id, complete=False)
        return None

    async def renderer_ack(self, session: VoiceSession, clause_id: str, *, complete: bool) -> bool:
        async with self._lock:
            if clause_id in session.retired_clauses:
                return False
            if self.session is not session or session.current_clause != clause_id:
                raise ProtocolError("unknown_clause")
            return await self._settle_locked(session, clause_id, complete=complete)

    async def _settle_locked(self, session: VoiceSession, clause_id: str, *, complete: bool) -> bool:
        if complete and session.clause_mode == "voice" and not session.audio_finished:
            raise ProtocolError("audio_incomplete")
        kwargs = {"mode": "text"} if session.clause_mode == "text" else {}
        accepted = bool(await self.runtime.renderer_ack(clause_id, complete=complete, **kwargs)) if self.runtime else False
        speech_logger.info("playback_settled clause=%s complete=%s accepted=%s sent=%d consumed=%d", clause_id, complete, accepted, session.sent_samples, session.consumed_samples)
        session.retired_clauses[clause_id] = None
        if len(session.retired_clauses) > 32:
            session.retired_clauses.popitem(last=False)
        session.current_clause = None
        session.playback_progress.set()
        session.current_say_id = None
        if session.clause_done is not None:
            session.clause_done.set()
        if self.speech is not None and session.clause_mode == "voice":
            self.speech.acknowledge(clause_id, complete=bool(complete and accepted))
        await self.agent_state("idle")
        return bool(complete and accepted)

    async def agent_state(self, state: str | None = None) -> None:
        session = self.session
        if session is not None and not session.closed:
            if state is not None:
                session.work_state = state
            state = "speaking" if session.current_clause else session.work_state
            if session.mode == "voice":
                if session.input_active:
                    state = "listening"
                elif state == "idle":
                    state = "no_audio"
            try:
                await session.send({"type": "agent.state", "state": state})
            except Exception:
                pass

    async def speech_activity(self, *, active: bool) -> None:
        """Project acoustic edges only; STT confirmation never starts listening."""
        session = self.session
        if session is not None and session.mode == "voice" and session.input_active != active:
            session.input_active = active
            await self.agent_state()

    async def session_error(self, code: str) -> None:
        if self.session is not None:
            try:
                await self.session.send({"type": "session.error", "code": code})
            except Exception:
                pass

    async def user_message(self, message_id: str, text: str, source: str) -> None:
        if self.session is not None:
            await self.session.send({"type": "user.message", "message_id": message_id, "text": text, "source": source,
                "profile_id": self.session.profile_id, "generation": self.session.generation})
            await self.agent_state("thinking")

    async def say_closed(self, event: dict[str, Any]) -> None:
        session = self.session
        if session is not None and event.get("profile_id") == session.profile_id and event.get("generation") == session.generation:
            await session.send({"type": "say.closed", **event})
            if event.get("cancelled"):
                if self.speech is not None:
                    await self.speech.delivery.cancel_current("say_protocol_cancelled", say_id=event["say_id"])
                elif session.current_clause is not None and session.current_say_id == event["say_id"]:
                    await self.cancel_clause(RendererPort(self, session), session.current_clause, "say_protocol_cancelled")

    async def render(self, clause: Any) -> None:
        port = await self.acquire_renderer_port()
        if port is None:
            raise ProtocolError("renderer_unavailable")
        if port.session.mode == "voice":
            if self.speech is None:
                raise ProtocolError("speech_unavailable")
            await self.speech.delivery(clause)
            return
        error = await port.offer_clause(clause)
        if error:
            raise ProtocolError(error)
        done = port.session.clause_done
        if done is not None:
            await done.wait()

    async def command(self, session: VoiceSession, event: dict[str, Any]) -> None:
        command_id = event.get("command_id")
        kind = event.get("type")
        code = None
        if not isinstance(command_id, str) or not command_id or len(command_id) > 128:
            raise ProtocolError("invalid_control")
        try:
            if not self.accepting or self.session is not session:
                raise ProtocolError("backend_restarting")
            if kind in {"text.submit", "voice.start"} and not getattr(self.runtime, "text_available", True):
                raise ProtocolError("text_model_unavailable")
            if kind == "text.submit":
                async with self.activation_lock, self._session_lock:
                    if self.session is not session or not self.accepting:
                        raise ProtocolError("backend_restarting")
                    if set(event) != {"type", "command_id", "text"} or not isinstance(event.get("text"), str) or not event["text"].strip() or len(event["text"]) > 8000:
                        raise ProtocolError("invalid_text")
                    if session.mode == "voice":
                        await self.speech.cancel_current("text_superseded")
                        await self.speech.reset_input()
                        await self.runtime.renderer_disconnected()
                    elif session.current_clause is not None:
                        await self.cancel_clause(RendererPort(self, session), session.current_clause, "text_superseded")
                    await self.runtime.submit_text(event["text"], segment_id=command_id)
            elif kind in {"voice.start", "voice.stop"}:
                allowed = {"type", "command_id", "audio_settings"} if kind == "voice.start" else {"type", "command_id"}
                if set(event) - allowed:
                    raise ProtocolError("invalid_control")
                settings = event.get("audio_settings", {})
                if not isinstance(settings, dict) or any(
                    not ((key in {"sampleRate", "channelCount"} and type(value) is int and 0 < value <= 384000)
                         or (key in {"echoCancellation", "noiseSuppression", "autoGainControl"} and type(value) is bool))
                    for key, value in settings.items()
                ):
                    raise ProtocolError("invalid_control")
                async with self.activation_lock, self._session_lock:
                    if not self.accepting or self.session is not session:
                        raise ProtocolError("backend_restarting")
                    if kind == "voice.start":
                        if session.current_clause is not None:
                            raise ProtocolError("delivery_busy")
                        if self.speech is None:
                            try:
                                await self.profile.start_speech()
                            except Exception as exc:
                                from .speech import SPEECH_PREPARATION_ERRORS
                                code = str(exc)
                                raise ProtocolError(code if code in SPEECH_PREPARATION_ERRORS else "speech_unavailable") from None
                        session.mode = "voice"
                        speech_logger.info("voice_started audio_settings=%s", settings)
                        session.input_active = False
                        await self.agent_state()
                    else:
                        if session.current_clause is not None:
                            await self.cancel_clause(RendererPort(self, session), session.current_clause, "voice_stopped")
                        await self.profile.stop_speech()
                        await self.runtime.renderer_disconnected()
                        self.mic_queue.clear()
                        session.mode = "text"
                        session.input_active = False
                        await self.agent_state("idle")
            else:
                raise ProtocolError("invalid_control")
        except ProtocolError as exc:
            code = str(exc)
        except Exception:
            code = "command_unavailable"
        await session.send({"type": "command.ack", "command_id": command_id,
                            "status": "rejected" if code else "accepted", **({"code": code} if code else {})})

    async def speech_candidate_started(self) -> None:
        session = self.session
        if session is not None:
            await self.speech_activity(active=True)

    async def speech_candidate_rejected(self) -> None:
        session = self.session
        if session is not None:
            await session.send({"type": "playback.resume"})
            await self.speech_activity(active=False)

    async def speech_confirmed(self) -> None:
        session = self.session
        if session is None:
            return
        clause_id = session.current_clause
        # Provider cleanup calls back into cancel_clause; never hold the ACK lock while joining it.
        if self.speech is not None:
            await self.speech.cancel_current("confirmed_barge_in")
        if clause_id is not None:
            await self.cancel_clause(RendererPort(self, session), clause_id, "confirmed_barge_in")

    async def speech_endpoint(self, *, complete: bool) -> None:
        if complete and self.session is not None:
            await self.session.send({"type": "playback.resume"})

    def start_accepting(self) -> None:
        self.accepting = True

    async def stop_accepting(self) -> None:
        self.accepting = False
        async with self._session_lock:
            if self.session is not None:
                await self._close_locked(self.session, "backend_shutdown")


class ProfileActivationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    revision: int = Field(ge=1, strict=True)
    soul: str = Field(min_length=1, max_length=32 * 1024)
    display_name: str | None = Field(default=None, max_length=128)
    adaptor: dict[str, Any] = Field(default_factory=dict)
    model_version: str = Field(default="", max_length=128)
    force: bool = False


class RealtimeTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    profile_id: str = Field(alias="profileId", pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    revision: int = Field(ge=1, strict=True)


class RealtimeTokenResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    token: str
    expires_in_ms: int = Field(alias="expiresInMs")
    subprotocol: str
    profile_id: str
    revision: int
    generation: int


router = APIRouter(prefix="/api/v1/main-agent", tags=["main-agent-realtime"])


def _hub(scope: Request | WebSocket) -> RealtimeHub:
    return scope.app.state.main_agent_realtime


def _require_origin(request: Request) -> RealtimeHub:
    hub = _hub(request)
    if not hub.origin_allowed(request.headers.get("origin")):
        raise HTTPException(403, "origin is not allowed")
    return hub


@router.get("/profile/activation-check")
async def profile_activation_check(request: Request) -> dict[str, Any]:
    hub = _require_origin(request)
    if hub.activation_lock.locked():
        return {"busy": True, "reason": "activation_in_progress"}
    busy = bool(hub.runtime is not None and getattr(hub.runtime, "restart_busy", lambda: False)())
    return {"busy": busy, "reason": "profile_busy" if busy else None}


@router.post("/profile/activate")
async def activate_profile(body: ProfileActivationRequest, request: Request) -> dict[str, Any]:
    hub = _require_origin(request)
    try:
        if not body.soul.strip():
            raise ValueError("soul_empty")
        if len(body.soul.encode("utf-8")) > 32 * 1024:
            raise ValueError("soul_too_large")
        soul = body.soul if body.display_name is None else (
            "Character name (identity data): " + json.dumps(body.display_name, ensure_ascii=False) + "\n\n" + body.soul)
        generation = await hub.activate(body.profile_id, soul, revision=body.revision,
            adaptor=body.adaptor, model_version=body.model_version, force=body.force)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    except RuntimeError as error:
        code = str(error)
        raise HTTPException(409 if code in {"profile_busy", "activation_in_progress"} else 503,
            code if code in {"profile_busy", "activation_in_progress", "profile_cleanup_incomplete"} else "main_agent_unavailable") from None
    except Exception:
        raise HTTPException(503, "main_agent_unavailable") from None
    capabilities = getattr(getattr(hub.runtime, "tool_policy", None), "avatar_capabilities", None)
    names = {"motions": list(capabilities.motion_names), "expressions": [name for name, _ in capabilities.expressions]} if capabilities else {"motions": [], "expressions": []}
    enabled = sum(1 for entry in body.adaptor.get("entries", {}).values() if entry.get("agent_enabled"))
    visual_status = "unconfigured" if not body.adaptor.get("model") else (
        "degraded" if not capabilities or not capabilities.model_available or enabled > len(names["motions"]) + len(names["expressions"]) else "ready")
    return {"profile_id": body.profile_id, "revision": body.revision, "generation": generation,
        "runtime_active": True, "visual_status": visual_status, "capability_names": names}


@router.get("/profile/{profile_id}/delete-check")
async def profile_delete_check(profile_id: str, request: Request) -> dict[str, Any]:
    hub = _require_origin(request)
    profiles = request.app.state.main_agent_profiles
    try:
        identifier = ActiveProfileManager.validate_profile_id(profile_id)
        path = profiles.profile_path(identifier)
    except ValueError:
        raise HTTPException(422, "invalid_profile_id") from None
    if profiles._lock.locked():
        return {"allowed": False, "reason": "activation_in_progress"}
    if identifier in profiles._retiring:
        return {"allowed": False, "reason": "profile_cleanup_incomplete"}
    if identifier == hub.active_profile_id:
        return {"allowed": False, "reason": "active_profile"}
    page = await request.app.state.task_manager.list_tasks(owner_profile_id=identifier, limit=1,
        statuses=(TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.WAITING, TaskStatus.PAUSED))
    if page.items:
        return {"allowed": False, "reason": "unfinished_tasks"}
    context = path / "context.sqlite3"
    if context.is_symlink():
        return {"allowed": False, "reason": "context_state_unavailable"}
    if context.exists():
        try:
            with sqlite3.connect(context.as_uri() + "?mode=ro", uri=True) as db:
                if db.execute("SELECT 1 FROM pending_conversation_archive LIMIT 1").fetchone() or db.execute(
                    "SELECT 1 FROM context_events WHERE event_type IN ('user_segment','assistant_clause') LIMIT 1").fetchone():
                    return {"allowed": False, "reason": "unarchived_context"}
        except sqlite3.Error:
            return {"allowed": False, "reason": "context_state_unavailable"}
    return {"allowed": True, "reason": None}


@router.post("/realtime-token", response_model=RealtimeTokenResponse, status_code=status.HTTP_201_CREATED)
async def issue_realtime_token(
    body: RealtimeTokenRequest, request: Request, response: Response
) -> RealtimeTokenResponse:
    hub = _hub(request)
    origin = request.headers.get("origin")
    if not hub.accepting:
        raise HTTPException(503, "realtime is not accepting sessions")
    if not hub.origin_allowed(origin):
        raise HTTPException(403, "origin is not allowed")
    assert origin is not None
    if hub.active_profile_id != body.profile_id or hub.profile is None or hub.profile.revision != body.revision:
        raise HTTPException(409, "profile_snapshot_not_active")
    if hub.activation_lock.locked():
        raise HTTPException(409, "activation_in_progress")
    if hub.profile_session_renewer is None:
        raise HTTPException(503, "main_agent_unavailable")
    try:
        async with hub.activation_lock:
            generation = await hub.profile_session_renewer(body.profile_id, body.revision)
    except RuntimeError as error:
        raise HTTPException(409, "activation_in_progress" if str(error) == "activation_in_progress" else "profile_snapshot_not_active") from None
    response.headers["Cache-Control"] = "no-store"
    return RealtimeTokenResponse(
        token=hub.tokens.issue(_origin(origin), body.profile_id, generation),
        expiresInMs=int(hub.tokens.ttl_seconds * 1_000),
        subprotocol=SUBPROTOCOL, profile_id=body.profile_id, revision=body.revision, generation=generation,
    )


@router.websocket("/realtime")
async def main_agent_realtime(websocket: WebSocket) -> None:
    hub = _hub(websocket)
    origin = websocket.headers.get("origin")
    offered = [value.strip() for value in websocket.headers.get("sec-websocket-protocol", "").split(",")]
    capabilities = [value.removeprefix("aurabot.capability.") for value in offered if value.startswith("aurabot.capability.")]
    if (
        not hub.accepting
        or not hub.origin_allowed(origin)
        or SUBPROTOCOL not in offered
        or len(capabilities) != 1
        or origin is None
    ):
        await websocket.close(code=4403, reason="realtime handshake rejected")
        return
    record = hub.tokens.consume(
        capabilities[0],
        _origin(origin),
        active_profile_id=hub.active_profile_id,
        active_generation=hub.active_generation,
    )
    if record is None:
        await websocket.close(code=4403, reason="realtime capability rejected")
        return
    await websocket.accept(subprotocol=SUBPROTOCOL)
    session = await hub.attach(websocket)
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                try:
                    if not hub.accepting or session.mode != "voice":
                        raise ValueError("voice is not active")
                    hub.mic_queue.put(message["bytes"])
                except ValueError:
                    await session.send({"type": "session.error", "code": "invalid_mic_frame"})
                continue
            raw = message.get("text")
            event: Any = None
            try:
                event = json.loads(raw) if isinstance(raw, str) and len(raw) <= 32768 else None
                if isinstance(event, dict) and event.get("type") in {"text.submit", "voice.start", "voice.stop"}:
                    await hub.command(session, event)
                    continue
                if isinstance(event, dict) and event.get("type") == "renderer.progress":
                    if set(event) != {"type", "clause_id", "consumed_samples"} or not isinstance(event.get("clause_id"), str):
                        raise ProtocolError("invalid_control")
                    await hub.renderer_progress(session, event["clause_id"], event["consumed_samples"])
                    continue
                if (
                    not isinstance(event, dict)
                    or set(event) != {"type", "clause_id", "complete"}
                    or event.get("type") != "renderer.ack"
                    or not isinstance(event.get("clause_id"), str)
                    or type(event.get("complete")) is not bool
                ):
                    raise ProtocolError("invalid_control")
                await hub.renderer_ack(session, event["clause_id"], complete=event["complete"])
            except (json.JSONDecodeError, ProtocolError):
                await session.send({"type": "session.error", "code": "unknown_clause" if isinstance(event, dict) and event.get("type") == "renderer.ack" else "invalid_control"})
    except WebSocketDisconnect:
        pass
    finally:
        await hub.detach(session)


def _origin(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("invalid origin")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("invalid origin")
    host = parsed.hostname.lower()
    port = parsed.port
    default = 80 if parsed.scheme == "http" else 443
    netloc = host if port in {None, default} else f"{host}:{port}"
    return urlunsplit((parsed.scheme, netloc, "", "", ""))


__all__ = [
    "CapabilityTokens", "MicFrameQueue", "ProtocolError", "RealtimeHub", "RendererPort", "SUBPROTOCOL",
    "VoiceSession", "router",
]
