"""Own the single profile-bound text/voice WebSocket, capability tokens and renderer delivery.

Sessions begin in text mode; explicit commands activate speech or submit text. Only accepted user segments
and say_to_user clauses cross into desktop history. Text ACKs commit draft delivery, while voice ACKs
require completed audio; binary PCM and playback gates remain confined to voice mode. Typed input remains
available during voice mode, resetting microphone input while preserving spoken delivery. ProfileResources
is the sole Speech owner; this transport reads it and retires Runtime session state exactly once."""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException, Request, Response, WebSocket, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.websockets import WebSocketDisconnect

from .lifecycle import ProfileResources


SUBPROTOCOL = "aurabot.main-agent.v1"


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
    closed: bool = False
    mode: str = "text"
    clause_mode: str = "text"
    clause_done: asyncio.Event | None = None
    audio_finished: bool = False

    async def send(self, value: dict[str, Any] | bytes) -> None:
        async with self.send_lock:
            if self.closed or self.hub.session is not self:
                raise ProtocolError("session_replaced")
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

    async def activate(self, profile_id: str, soul: str) -> int:
        if self.profile_activator is None:
            raise RuntimeError("main_agent_unavailable")
        async with self.activation_lock:
            if not self.accepting:
                raise RuntimeError("backend_restarting")
            generation = await self.profile_activator(profile_id, soul)
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
        await session.send({"type": "session.ready", "profile_id": session.profile_id})
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
        session.current_clause = str(clause.clause_id)
        session.clause_mode = session.mode
        session.clause_done = asyncio.Event()
        session.audio_finished = False
        try:
            await session.send({"type": "clause.offer", "clause_id": session.current_clause, "text": str(clause.text), "mode": session.clause_mode})
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
        await session.send(pcm)
        return None

    async def finish_audio(self, port: RendererPort, clause_id: str) -> str | None:
        session = self._current(port)
        if session is None:
            return "session_replaced"
        if session.current_clause != clause_id:
            return "unknown_clause"
        session.audio_finished = True
        await session.send({"type": "clause.audio_end", "clause_id": clause_id})
        return None

    async def cancel_clause(self, port: RendererPort, clause_id: str, reason: str) -> str | None:
        session = self._current(port)
        if session is None:
            return "session_replaced"
        if session.current_clause != clause_id:
            return "unknown_clause"
        await session.send({"type": "clause.cancel", "clause_id": clause_id, "reason": reason})
        await self._settle_locked(session, clause_id, complete=False)
        return None

    async def renderer_ack(self, session: VoiceSession, clause_id: str, *, complete: bool) -> bool:
        async with self._lock:
            if self.session is not session or session.current_clause != clause_id:
                raise ProtocolError("unknown_clause")
            return await self._settle_locked(session, clause_id, complete=complete)

    async def _settle_locked(self, session: VoiceSession, clause_id: str, *, complete: bool) -> bool:
        if complete and session.clause_mode == "voice" and not session.audio_finished:
            raise ProtocolError("audio_incomplete")
        kwargs = {"mode": "text"} if session.clause_mode == "text" else {}
        accepted = bool(await self.runtime.renderer_ack(clause_id, complete=complete, **kwargs)) if self.runtime else False
        session.current_clause = None
        if session.clause_done is not None:
            session.clause_done.set()
        if self.speech is not None and session.clause_mode == "voice":
            self.speech.acknowledge(clause_id, complete=bool(complete and accepted))
        await self.agent_state("idle")
        return bool(complete and accepted)

    async def agent_state(self, state: str) -> None:
        if self.session is not None and not self.session.closed:
            if state == "idle":
                state = "speaking" if self.session.current_clause else ("listening" if self.session.mode == "voice" else "idle")
            try:
                await self.session.send({"type": "agent.state", "state": state})
            except Exception:
                pass

    async def session_error(self, code: str) -> None:
        if self.session is not None:
            try:
                await self.session.send({"type": "session.error", "code": code})
            except Exception:
                pass

    async def user_message(self, message_id: str, text: str, source: str) -> None:
        if self.session is not None:
            await self.session.send({"type": "user.message", "message_id": message_id, "text": text, "source": source})
            await self.agent_state("thinking")

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
                if set(event) != {"type", "command_id"}:
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
                            except Exception:
                                raise ProtocolError("speech_unavailable") from None
                        session.mode = "voice"
                        await self.agent_state("listening")
                    else:
                        if session.current_clause is not None:
                            await self.cancel_clause(RendererPort(self, session), session.current_clause, "voice_stopped")
                        await self.profile.stop_speech()
                        await self.runtime.renderer_disconnected()
                        self.mic_queue.clear()
                        session.mode = "text"
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
            await session.send({"type": "playback.pause"})

    async def speech_candidate_rejected(self) -> None:
        session = self.session
        if session is not None:
            await session.send({"type": "playback.resume"})

    async def speech_confirmed(self) -> None:
        async with self._lock:
            session = self.session
            if session is None:
                return
            clause_id = session.current_clause
            if self.speech is not None:
                await self.speech.cancel_current("confirmed_barge_in")
            if clause_id is not None and session.current_clause == clause_id:
                await session.send({"type": "clause.cancel", "clause_id": clause_id, "reason": "confirmed_barge_in"})
                await self._settle_locked(session, clause_id, complete=False)

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


class RealtimeTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    profile_id: str = Field(alias="profileId", pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    soul: str = Field(max_length=32 * 1024)

    @field_validator("soul")
    @classmethod
    def nonempty_soul(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("soul must be non-empty")
        return value


class RealtimeTokenResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    token: str
    expires_in_ms: int = Field(alias="expiresInMs")
    subprotocol: str


router = APIRouter(prefix="/api/v1/main-agent", tags=["main-agent-realtime"])


def _hub(scope: Request | WebSocket) -> RealtimeHub:
    return scope.app.state.main_agent_realtime


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
    try:
        generation = await hub.activate(body.profile_id, body.soul)
    except Exception as exc:
        raise HTTPException(503, "main_agent_unavailable") from exc
    response.headers["Cache-Control"] = "no-store"
    return RealtimeTokenResponse(
        token=hub.tokens.issue(_origin(origin), body.profile_id, generation),
        expiresInMs=int(hub.tokens.ttl_seconds * 1_000),
        subprotocol=SUBPROTOCOL,
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
