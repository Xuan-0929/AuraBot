"""Own the optional speech pipeline, normalized providers, and lazy local VAD/EOU sessions.

DashScope-specific callback fields and credentials terminate inside the adapters and become
``TranscriptUpdate`` facts
before Main Agent Core can observe them.  TTS exposes only clause audio bytes.  Silero VAD and Smart Turn
v3.2 share process-wide ONNX sessions, verify both model and license assets before loading, and configure
ONNX Runtime for sequential single-thread CPU inference; missing optional packages or assets are reported
as unavailable capabilities rather than preventing a non-speech Backend from importing or starting.
``SpeechPipeline`` is the single lifespan owner that drains RealtimeHub microphone frames into the
coordinator and supervises sequential clause delivery; its default disabled form starts no worker.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import time
from collections import deque
from collections.abc import AsyncIterable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Protocol, runtime_checkable


TranscriptSink = Callable[["TranscriptUpdate"], Any]
_CLEANUP_TIMEOUT_SECONDS = 0.25


@dataclass(frozen=True, slots=True)
class TranscriptUpdate:
    """One provider-independent transcript snapshot split into stable and mutable text."""

    full_text: str
    stable_prefix: str
    mutable_suffix: str
    provider_event_kind: str
    provider_stability: float | None = None

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) for value in (
            self.full_text, self.stable_prefix, self.mutable_suffix, self.provider_event_kind,
        )):
            raise TypeError("transcript fields must be text")
        if self.full_text != self.stable_prefix + self.mutable_suffix:
            raise ValueError("stable_prefix and mutable_suffix must reconstruct full_text")
        if not self.provider_event_kind:
            raise ValueError("provider_event_kind must be non-empty")
        if self.provider_stability is not None and not 0 <= self.provider_stability <= 1:
            raise ValueError("provider_stability must be between zero and one")


@runtime_checkable
class StreamingSttSession(Protocol):
    """One active streaming recognition turn fed with 16 kHz mono PCM16LE bytes."""

    async def send_audio(self, pcm16le: bytes) -> None: ...

    async def close(self) -> None: ...


@runtime_checkable
class StreamingSttProvider(Protocol):
    """Start normalized streaming recognition without exposing provider event objects to Core."""

    async def start(self, on_update: TranscriptSink) -> StreamingSttSession: ...


@runtime_checkable
class SpeechOutputProvider(Protocol):
    """Synthesize one clause as ordered 24 kHz mono PCM16LE byte chunks."""

    def synthesize(self, text: str) -> AsyncIterable[bytes]: ...

    async def cancel(self) -> None: ...


class SpeechProviderUnavailable(RuntimeError):
    """Stable optional-provider failure suitable for capability degradation and diagnostics."""


class _DashScopeSttSession:
    """Fence one provider callback generation onto its owner loop and drain sink work on close."""

    def __init__(
        self, transport: Any, owner_loop: asyncio.AbstractEventLoop, sink: TranscriptSink
    ) -> None:
        self._transport = transport
        self._owner_loop = owner_loop
        self._sink = sink
        self._closed = False
        self._sink_tasks: set[asyncio.Task[Any]] = set()
        self._failure_task: asyncio.Task[None] | None = None

    def provider_event(self, event: Any) -> None:
        """Accept an SDK-thread event only for this still-open session generation."""

        if self._closed:
            return
        update = _normalize_dashscope_event(event)
        if update is None:
            return
        try:
            if asyncio.get_running_loop() is self._owner_loop:
                self._deliver(update)
            else:
                self._owner_loop.call_soon_threadsafe(self._deliver, update)
        except RuntimeError:
            try:
                self._owner_loop.call_soon_threadsafe(self._deliver, update)
            except RuntimeError:
                self._closed = True

    def _deliver(self, update: TranscriptUpdate) -> None:
        if self._closed:
            return
        try:
            result = self._sink(update)
        except BaseException:
            self._fail_sink()
            return
        if not inspect.isawaitable(result):
            return
        task = self._owner_loop.create_task(result)
        self._sink_tasks.add(task)
        task.add_done_callback(self._sink_done)

    def _sink_done(self, task: asyncio.Task[Any]) -> None:
        self._sink_tasks.discard(task)
        if task.cancelled():
            return
        try:
            error = task.exception()
        except BaseException:
            error = None
        if error is not None:
            self._fail_sink()

    def _fail_sink(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._failure_task = self._owner_loop.create_task(self._stop_after_failure())

    async def _stop_after_failure(self) -> None:
        current = asyncio.current_task()
        for task in tuple(self._sink_tasks):
            if task is not current and not task.done():
                task.cancel()
        if self._sink_tasks:
            await asyncio.gather(*tuple(self._sink_tasks), return_exceptions=True)
        try:
            await asyncio.wait_for(
                _call_maybe_blocking(self._transport.stop),
                _CLEANUP_TIMEOUT_SECONDS,
            )
        except BaseException:
            pass

    async def send_audio(self, pcm16le: bytes) -> None:
        if self._closed:
            raise RuntimeError("STT session is closed")
        if not isinstance(pcm16le, bytes) or not pcm16le:
            raise ValueError("STT audio must be non-empty bytes")
        await _maybe_await(self._transport.send_audio_frame(pcm16le))

    async def close(self) -> None:
        if self._closed:
            if self._failure_task is not None:
                await asyncio.gather(self._failure_task, return_exceptions=True)
            return
        self._closed = True
        for task in tuple(self._sink_tasks):
            if not task.done():
                task.cancel()
        if self._sink_tasks:
            await asyncio.gather(*tuple(self._sink_tasks), return_exceptions=True)
        await asyncio.wait_for(
            _call_maybe_blocking(self._transport.stop),
            _CLEANUP_TIMEOUT_SECONDS,
        )


class DashScopeStreamingSttProvider:
    """Adapt DashScope Recognition callbacks to normalized transcript snapshots."""

    def __init__(
        self,
        model_name: str,
        *,
        api_key: str = "",
        sample_rate_hz: int = 16_000,
        transport_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.model_name = _required_text(model_name, "STT model name")
        if sample_rate_hz != 16_000:
            raise ValueError("AuraBot streaming STT requires 16000 Hz audio")
        self.sample_rate_hz = sample_rate_hz
        self._api_key = api_key.strip()
        self._transport_factory = transport_factory or _dashscope_stt_transport

    async def start(self, on_update: TranscriptSink) -> StreamingSttSession:
        if not callable(on_update):
            raise TypeError("transcript sink must be callable")

        owner_loop = asyncio.get_running_loop()
        session: _DashScopeSttSession | None = None
        transport: Any | None = None

        def on_event(event: Any) -> None:
            if session is not None:
                session.provider_event(event)

        try:
            options = {
                "model_name": self.model_name,
                "sample_rate_hz": self.sample_rate_hz,
                "on_event": on_event,
            }
            if self._api_key:
                options["api_key"] = self._api_key
            transport = self._transport_factory(**options)
            session = _DashScopeSttSession(transport, owner_loop, on_update)
            await _call_maybe_blocking(transport.start)
        except BaseException as exc:
            if transport is not None and callable(getattr(transport, "stop", None)):
                try:
                    await asyncio.wait_for(
                        _call_maybe_blocking(transport.stop),
                        _CLEANUP_TIMEOUT_SECONDS,
                    )
                except BaseException:
                    pass
            if isinstance(exc, asyncio.CancelledError):
                raise
            if isinstance(exc, SpeechProviderUnavailable):
                raise
            if isinstance(exc, (ImportError, ModuleNotFoundError)):
                raise SpeechProviderUnavailable(
                    "DashScope STT dependency is unavailable"
                ) from exc
            raise SpeechProviderUnavailable("DashScope STT failed to start") from exc
        assert session is not None
        return session


class DashScopeSpeechOutputProvider:
    """Adapt configured DashScope TTS to ordered PCM chunks with explicit cancellation."""

    def __init__(
        self,
        model_name: str,
        *,
        voice: str,
        api_key: str = "",
        transport_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.model_name = _required_text(model_name, "TTS model name")
        self.voice = _required_text(voice, "TTS voice")
        self._api_key = api_key.strip()
        self._transport_factory = transport_factory or _dashscope_tts_transport
        self._current: Any | None = None
        self._lock = asyncio.Lock()

    async def synthesize(self, text: str):
        clause = _required_text(text, "TTS clause")
        try:
            options = {"model_name": self.model_name, "voice": self.voice}
            if self._api_key:
                options["api_key"] = self._api_key
            transport = self._transport_factory(**options)
        except (ImportError, ModuleNotFoundError) as exc:
            raise SpeechProviderUnavailable("DashScope TTS dependency is unavailable") from exc
        async with self._lock:
            self._current = transport
        try:
            source = await _call_maybe_blocking(transport.synthesize, clause)
            if hasattr(source, "__aiter__"):
                async for chunk in source:
                    yield _audio_chunk(chunk)
            elif isinstance(source, bytes):
                yield _audio_chunk(source)
            else:
                iterator = iter(source)
                while True:
                    present, chunk = await asyncio.to_thread(_next_sync_chunk, iterator)
                    if not present:
                        break
                    yield _audio_chunk(chunk)
        finally:
            async with self._lock:
                if self._current is transport:
                    self._current = None

    async def cancel(self) -> None:
        async with self._lock:
            transport = self._current
        if transport is not None and callable(getattr(transport, "cancel", None)):
            await _call_maybe_blocking(transport.cancel)


@dataclass(frozen=True, slots=True)
class SpeechCapabilityStatus:
    """Auditable local model availability without leaking optional-import exception details."""

    available: bool
    reason: str
    model_path: Path
    license_path: Path
    license_name: str


_ONNX_SESSIONS: dict[tuple[str, Path], Any] = {}
_ONNX_LOCK = Lock()


class _LazyLocalDetector:
    license_name = ""
    license_marker = ""
    capability = ""

    def __init__(
        self,
        model_path: str | Path,
        license_path: str | Path,
        *,
        ort_loader: Callable[[], Any] | None = None,
        numpy_loader: Callable[[], Any] | None = None,
    ) -> None:
        self.model_path = Path(model_path).expanduser().resolve()
        self.license_path = Path(license_path).expanduser().resolve()
        self._ort_loader = ort_loader or (lambda: importlib.import_module("onnxruntime"))
        self._numpy_loader = numpy_loader or (lambda: importlib.import_module("numpy"))
        self._load_reason: str | None = None

    def status(self) -> SpeechCapabilityStatus:
        if not self.model_path.is_file():
            reason = "model_asset_missing"
        elif not self.license_path.is_file():
            reason = "license_asset_missing"
        else:
            try:
                licensed = self.license_marker.casefold() in self.license_path.read_text(
                    encoding="utf-8"
                ).casefold()
            except (OSError, UnicodeError):
                licensed = False
            reason = "ready_to_load" if licensed else "license_unverified"
        if self._load_reason is not None:
            reason = self._load_reason
        return SpeechCapabilityStatus(
            reason == "ready_to_load", reason, self.model_path, self.license_path,
            self.license_name,
        )

    def load_session(self) -> Any | None:
        status = self.status()
        if not status.available:
            return None
        key = (self.capability, self.model_path)
        with _ONNX_LOCK:
            if key in _ONNX_SESSIONS:
                return _ONNX_SESSIONS[key]
            try:
                ort = self._ort_loader()
                options = ort.SessionOptions()
                options.intra_op_num_threads = 1
                options.inter_op_num_threads = 1
                options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                session = ort.InferenceSession(
                    str(self.model_path), sess_options=options,
                    providers=["CPUExecutionProvider"],
                )
            except (ImportError, ModuleNotFoundError):
                self._load_reason = "optional_dependency_missing"
                return None
            except Exception:
                self._load_reason = "model_load_failed"
                return None
            _ONNX_SESSIONS[key] = session
            return session


class SileroVadDetector(_LazyLocalDetector):
    """Run the official 16 kHz Silero ONNX contract with per-stream recurrent state."""

    license_name = "MIT"
    license_marker = "MIT License"
    capability = "silero_vad"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._pcm = bytearray()
        self._state: Any | None = None
        self._context: Any | None = None
        self._last_probability: float | None = None

    def reset(self) -> None:
        """Start a new audio stream without mutating the shared immutable ORT session."""

        self._pcm.clear()
        self._state = None
        self._context = None
        self._last_probability = None

    def speech_probability(self, pcm16le: bytes) -> float | None:
        if not isinstance(pcm16le, bytes) or not pcm16le or len(pcm16le) % 2:
            self._load_reason = "invalid_audio"
            return None
        session = self.load_session()
        if session is None:
            return None
        try:
            np = self._numpy_loader()
            self._pcm.extend(pcm16le)
            if len(self._pcm) < 1_024:
                return self._last_probability
            raw = bytes(self._pcm[:1_024])
            del self._pcm[:1_024]
            audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32_768.0
            if self._state is None:
                self._state = np.zeros((2, 1, 128), dtype=np.float32)
            if self._context is None:
                self._context = np.zeros((1, 64), dtype=np.float32)
            model_input = np.concatenate((self._context, audio.reshape(1, 512)), axis=1)
            outputs = session.run(None, {
                "input": model_input.astype(np.float32, copy=False),
                "state": self._state.astype(np.float32, copy=False),
                "sr": np.array(16_000, dtype=np.int64),
            })
            if not isinstance(outputs, (tuple, list)) or len(outputs) < 2:
                raise ValueError("Silero output contract mismatch")
            probability = float(np.asarray(outputs[0], dtype=np.float32).reshape(-1)[0])
            state = np.asarray(outputs[1], dtype=np.float32)
            if state.shape != (2, 1, 128) or not 0.0 <= probability <= 1.0:
                raise ValueError("Silero output contract mismatch")
            self._state = state
            self._context = model_input[:, -64:].copy()
            self._last_probability = probability
            return probability
        except (ImportError, ModuleNotFoundError):
            self._load_reason = "optional_dependency_missing"
        except Exception:
            self._load_reason = "inference_failed"
        self.reset()
        return None


class SmartTurnDetector(_LazyLocalDetector):
    """Run Smart Turn v3.2's fixed Whisper-feature ONNX contract on the last eight seconds."""

    license_name = "BSD-2-Clause"
    license_marker = "BSD 2-Clause"
    capability = "smart_turn_v3_2"

    def __init__(
        self, *args: Any,
        feature_extractor_factory: Callable[[], Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._feature_extractor_factory = (
            feature_extractor_factory or _smart_turn_feature_extractor
        )
        self._feature_extractor: Any | None = None

    def completion_probability(self, pcm16le: bytes) -> float | None:
        if not isinstance(pcm16le, bytes) or not pcm16le or len(pcm16le) % 2:
            self._load_reason = "invalid_audio"
            return None
        session = self.load_session()
        if session is None:
            return None
        try:
            np = self._numpy_loader()
            audio = np.frombuffer(pcm16le, dtype="<i2").astype(np.float32) / 32_768.0
            audio = audio[-128_000:]
            if audio.size < 128_000:
                padded = np.zeros(128_000, dtype=np.float32)
                padded[-audio.size:] = audio
                audio = padded
            if self._feature_extractor is None:
                self._feature_extractor = self._feature_extractor_factory()
            extracted = self._feature_extractor(
                audio, sampling_rate=16_000, return_tensors="np", padding="max_length",
                max_length=128_000, truncation=True, do_normalize=True,
            )
            features = (
                extracted.get("input_features")
                if isinstance(extracted, Mapping)
                else getattr(extracted, "input_features", None)
            )
            features = np.asarray(features, dtype=np.float32)
            if features.shape != (1, 80, 800):
                raise ValueError("Smart Turn v3.2 input contract mismatch")
            outputs = session.run(None, {"input_features": features})
            if not isinstance(outputs, (tuple, list)) or not outputs:
                raise ValueError("Smart Turn v3.2 output contract mismatch")
            probability = float(np.asarray(outputs[0], dtype=np.float32).reshape(-1)[0])
            if not 0.0 <= probability <= 1.0:
                raise ValueError("Smart Turn v3.2 probability is outside [0, 1]")
            return probability
        except (ImportError, ModuleNotFoundError):
            self._load_reason = "optional_dependency_missing"
        except Exception:
            self._load_reason = "inference_failed"
        return None


class SpeechInputCoordinator:
    """Feed continuous mic PCM through short pre-roll, VAD, normalized STT, and bounded EOU timing.

    The coordinator creates no resident worker: each incoming 20 ms frame advances the small acoustic
    state, while at most one transient endpoint task exists during a silence candidate.  VAD silence
    alone never calls StrongPause; only a complete EOU score or the fixed fallback deadline can do so.
    """

    def __init__(
        self,
        runtime: Any,
        *,
        stt_provider: StreamingSttProvider | None = None,
        vad: Any | None = None,
        eou: Any | None = None,
        pre_roll_frames: int = 10,
        frame_ms: int = 20,
        vad_threshold: float = 0.5,
        vad_stop_ms: int = 300,
        speech_confirm_ms: int = 160,
        eou_threshold: float = 0.5,
        eou_recheck_seconds: float = 0.5,
        incomplete_fallback_seconds: float = 1.8,
        unavailable_fallback_seconds: float = 0.7,
        eou_inference_timeout_seconds: float = 0.25,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], Any] | None = None,
    ) -> None:
        if pre_roll_frames < 1 or frame_ms < 1:
            raise ValueError("speech frame and pre-roll limits must be positive")
        self.runtime = runtime
        self.stt_provider = stt_provider
        self.vad = vad
        self.eou = eou
        self.pre_roll = deque(maxlen=pre_roll_frames)
        self.audio = deque(maxlen=max(1, 8_000 // frame_ms))
        self.frame_ms = frame_ms
        self.vad_threshold = vad_threshold
        self.stop_frames = max(1, vad_stop_ms // frame_ms)
        self.confirm_frames = max(1, speech_confirm_ms // frame_ms)
        self.eou_threshold = eou_threshold
        self.eou_recheck_seconds = eou_recheck_seconds
        self.incomplete_fallback_seconds = incomplete_fallback_seconds
        self.unavailable_fallback_seconds = unavailable_fallback_seconds
        self.eou_inference_timeout_seconds = eou_inference_timeout_seconds
        if eou_inference_timeout_seconds <= 0:
            raise ValueError("EOU inference timeout must be positive")
        self.clock = clock or time.monotonic
        self.sleep = sleep or asyncio.sleep
        self._active = False
        self._confirmed = False
        self._speech_frames = 0
        self._silence_frames = 0
        self._stop_issued = False
        self._has_transcript = False
        self._session: StreamingSttSession | None = None
        self._endpoint_task: asyncio.Task[None] | None = None
        self._endpoint_generation = 0
        self._closed = False

    async def accept_mic_frame(self, pcm16le: bytes) -> None:
        if self._closed:
            return
        if not isinstance(pcm16le, bytes) or not pcm16le:
            raise ValueError("mic frame must be non-empty bytes")
        self.pre_roll.append(pcm16le)
        self.audio.append(pcm16le)
        probability = await self._vad_probability(pcm16le)
        if probability is None:
            if self._active:
                await self._cancel_endpoint()
                try:
                    await _maybe_await(self.runtime.reject_speech_candidate())
                finally:
                    await self._finish_turn()
            return
        speech = probability >= self.vad_threshold
        if not self._active:
            if speech:
                await self._start_candidate()
            return

        if self._session is not None:
            try:
                await self._session.send_audio(pcm16le)
            except Exception:
                await self._close_stt()
        if speech:
            if self._stop_issued:
                await self._cancel_endpoint()
                self._stop_issued = False
                self._confirmed = False
                self._speech_frames = 0
                await _maybe_await(self.runtime.vad_start())
            self._silence_frames = 0
            self._speech_frames += 1
            if not self._confirmed and self._speech_frames >= self.confirm_frames:
                self._confirmed = True
                await _maybe_await(self.runtime.confirm_speech())
            return

        self._silence_frames += 1
        if self._stop_issued or self._silence_frames < self.stop_frames:
            return
        self._stop_issued = True
        await _maybe_await(self.runtime.vad_stop_candidate())
        if not self._confirmed:
            await _maybe_await(self.runtime.reject_speech_candidate())
            await self._finish_turn()
            return
        if not self._has_transcript:
            await _maybe_await(self.runtime.reject_speech_candidate())
            await self._finish_turn()
            return
        self._endpoint_generation += 1
        generation = self._endpoint_generation
        self._endpoint_task = asyncio.create_task(
            self._resolve_endpoint(generation), name="main-agent-speech-endpoint"
        )

    async def wait_for_endpoint(self) -> None:
        task = self._endpoint_task
        if task is not None:
            await asyncio.shield(task)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._cancel_endpoint()
        await self._finish_turn()

    async def _start_candidate(self) -> None:
        self._active = True
        self._confirmed = False
        self._speech_frames = 1
        self._silence_frames = 0
        self._stop_issued = False
        self._has_transcript = False
        await _maybe_await(self.runtime.vad_start())
        if self.stt_provider is None:
            return
        try:
            self._session = await self.stt_provider.start(self._on_transcript)
            for frame in tuple(self.pre_roll):
                await self._session.send_audio(frame)
        except Exception:
            await self._close_stt()

    async def _on_transcript(self, update: TranscriptUpdate) -> None:
        if not isinstance(update, TranscriptUpdate) or not update.full_text:
            return
        self._has_transcript = True
        if not self._confirmed:
            self._confirmed = True
            await _maybe_await(self.runtime.confirm_speech())
        await _maybe_await(self.runtime.update_transcript(
            update.full_text, request_prepare=bool(update.stable_prefix),
        ))

    async def _resolve_endpoint(self, generation: int) -> None:
        candidate_at = self.clock()
        finish = False
        try:
            probability = await asyncio.wait_for(
                self._eou_probability(), self.eou_inference_timeout_seconds
            )
            if probability is None:
                delay = max(0.0, candidate_at + self.unavailable_fallback_seconds - self.clock())
                if delay:
                    await self.sleep(delay)
                if self._endpoint_current(generation):
                    await _maybe_await(self.runtime.endpoint_fallback())
                    finish = True
                return
            if probability >= self.eou_threshold:
                if self._endpoint_current(generation):
                    await _maybe_await(self.runtime.eou_complete())
                    finish = True
                return
            await _maybe_await(self.runtime.eou_incomplete())
            deadline = candidate_at + self.incomplete_fallback_seconds
            while self._endpoint_current(generation):
                remaining = deadline - self.clock()
                if remaining <= 0:
                    await _maybe_await(self.runtime.endpoint_fallback())
                    finish = True
                    return
                await self.sleep(min(self.eou_recheck_seconds, round(remaining, 9)))
                if not self._endpoint_current(generation):
                    return
                if self.clock() >= deadline:
                    await _maybe_await(self.runtime.endpoint_fallback())
                    finish = True
                    return
                try:
                    probability = await asyncio.wait_for(
                        self._eou_probability(),
                        min(self.eou_inference_timeout_seconds, deadline - self.clock()),
                    )
                except TimeoutError:
                    probability = None
                if probability is not None and probability >= self.eou_threshold:
                    await _maybe_await(self.runtime.eou_complete())
                    finish = True
                    return
        except TimeoutError:
            delay = max(0.0, candidate_at + self.unavailable_fallback_seconds - self.clock())
            if delay:
                await self.sleep(delay)
            if self._endpoint_current(generation):
                await _maybe_await(self.runtime.endpoint_fallback())
                finish = True
        except asyncio.CancelledError:
            raise
        except Exception:
            finish = True
        finally:
            if finish and self._endpoint_current(generation):
                await self._finish_turn()

    def _endpoint_current(self, generation: int) -> bool:
        return self._stop_issued and generation == self._endpoint_generation and not self._closed

    async def _vad_probability(self, frame: bytes) -> float | None:
        if self.vad is None:
            return None
        try:
            value = await _call_maybe_blocking(self.vad.speech_probability, frame)
        except Exception:
            return None
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    async def _eou_probability(self) -> float | None:
        if self.eou is None:
            return None
        try:
            value = await _call_maybe_blocking(
                self.eou.completion_probability, b"".join(self.audio)
            )
        except Exception:
            return None
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    async def _cancel_endpoint(self) -> None:
        self._endpoint_generation += 1
        task = self._endpoint_task
        self._endpoint_task = None
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _close_stt(self) -> None:
        session = self._session
        self._session = None
        if session is not None:
            try:
                await asyncio.wait_for(session.close(), _CLEANUP_TIMEOUT_SECONDS)
            except BaseException:
                pass

    async def _finish_turn(self) -> None:
        await self._close_stt()
        self._active = False
        self._confirmed = False
        self._speech_frames = 0
        self._silence_frames = 0
        self._stop_issued = False
        self._has_transcript = False
        self.pre_roll.clear()
        self.audio.clear()
        reset = getattr(self.vad, "reset", None)
        if callable(reset):
            try:
                await _call_maybe_blocking(reset)
            except Exception:
                pass


@dataclass(frozen=True, slots=True)
class SpeechPipelineStatus:
    """Truthful lifecycle/capability state exposed by Backend composition."""

    enabled: bool
    reason: str


class SpeechPipeline:
    """Supervise one realtime microphone consumer and the optional speech-output worker."""

    def __init__(
        self,
        hub: Any,
        *,
        coordinator: SpeechInputCoordinator | Any | None = None,
        delivery: "ClauseSpeechDelivery | Any | None" = None,
        runtime: Any | None = None,
        enabled: bool = False,
        disabled_reason: str = "speech_disabled",
    ) -> None:
        if enabled and coordinator is None:
            raise ValueError("enabled speech pipeline requires a coordinator")
        self.hub = hub
        self.coordinator = coordinator
        self.delivery = delivery
        self.runtime = runtime
        self._configured_enabled = bool(enabled)
        self._status = SpeechPipelineStatus(False, disabled_reason)
        self._mic_task: asyncio.Task[None] | None = None
        self._closed = False

    @classmethod
    def disabled(cls, hub: Any, *, reason: str = "speech_disabled") -> "SpeechPipeline":
        """Create the no-SDK/no-model default capability owner."""

        return cls(hub, enabled=False, disabled_reason=reason)

    @classmethod
    def compose(
        cls,
        hub: Any,
        runtime: Any,
        *,
        stt_provider: StreamingSttProvider | None,
        output_provider: SpeechOutputProvider,
        vad: Any | None,
        eou: Any | None,
        **coordinator_options: Any,
    ) -> "SpeechPipeline":
        """Bind Task9-provided runtime/providers to the production realtime transport."""

        delivery = ClauseSpeechDelivery(
            output_provider,
            hub.acquire_renderer_port,
            settle_clause=runtime.renderer_ack,
        )
        runtime.renderer = delivery
        hub.runtime = runtime
        coordinator = SpeechInputCoordinator(
            runtime,
            stt_provider=stt_provider,
            vad=vad,
            eou=eou,
            **coordinator_options,
        )
        return cls(
            hub,
            coordinator=coordinator,
            delivery=delivery,
            runtime=runtime,
            enabled=True,
        )

    @property
    def status(self) -> SpeechPipelineStatus:
        return self._status

    @property
    def live_tasks(self) -> tuple[asyncio.Task[Any], ...]:
        tasks = (self._mic_task, getattr(self.delivery, "_worker", None))
        return tuple(task for task in tasks if task is not None and not task.done())

    async def startup(self) -> None:
        """Start only explicitly enabled components and degrade without aborting Backend startup."""

        if self._closed:
            raise RuntimeError("speech pipeline is closed")
        if not self._configured_enabled:
            return
        if self._mic_task is not None and not self._mic_task.done():
            return
        try:
            if self.delivery is not None:
                await _maybe_await(self.delivery.startup())
            self._mic_task = asyncio.create_task(
                self._consume_mic(), name="main-agent-speech-mic"
            )
            self._status = SpeechPipelineStatus(True, "running")
        except Exception:
            self._status = SpeechPipelineStatus(False, "speech_startup_failed")

    async def shutdown(self) -> None:
        """Fence microphone work, close the active turn, then settle the output worker."""

        if self._closed:
            return
        self._closed = True
        task = self._mic_task
        self._mic_task = None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self.coordinator is not None:
            try:
                await _maybe_await(self.coordinator.close())
            except BaseException:
                pass
        if self.delivery is not None:
            try:
                await _maybe_await(self.delivery.shutdown())
            except BaseException:
                pass
        if self.runtime is not None:
            try:
                await _maybe_await(self.runtime.shutdown())
            except BaseException:
                pass
        self._status = SpeechPipelineStatus(False, "stopped")

    async def _consume_mic(self) -> None:
        try:
            while True:
                frame = await self.hub.mic_queue.get()
                await _maybe_await(self.coordinator.accept_mic_frame(frame.payload))
        except asyncio.CancelledError:
            raise
        except BaseException:
            self._status = SpeechPipelineStatus(False, "mic_consumer_failed")


@dataclass(frozen=True, slots=True)
class _SpeechJob:
    clause: Any
    generation: int


class ClauseSpeechDelivery:
    """Accept clauses promptly and supervise their serial synthesis/playback through ACK settlement.

    MainAgentRuntime remains the DeliveryLedger owner.  The callable only transfers bounded ownership
    to one worker so a control invoke never waits for physical playback; the worker advances in order
    only after Runtime forwards Renderer ACK.  Failure, timeout, cancellation, replacement, and shutdown
    all interrupt the lease offer and invoke the optional idempotent ledger-settlement fallback.
    """

    def __init__(
        self,
        provider: SpeechOutputProvider,
        renderer_port_factory: Callable[[], Any],
        *,
        settle_clause: Callable[..., Any] | None = None,
        max_pending_clauses: int = 16,
        ack_timeout_seconds: float = 30.0,
        cancel_timeout_seconds: float = 0.25,
    ) -> None:
        if max_pending_clauses < 1:
            raise ValueError("pending speech clause bound must be positive")
        if ack_timeout_seconds <= 0 or cancel_timeout_seconds <= 0:
            raise ValueError("speech delivery timeouts must be positive")
        self.provider = provider
        self.renderer_port_factory = renderer_port_factory
        self.settle_clause = settle_clause
        self.ack_timeout_seconds = ack_timeout_seconds
        self.cancel_timeout_seconds = cancel_timeout_seconds
        self._queue: asyncio.Queue[_SpeechJob] = asyncio.Queue(max_pending_clauses)
        self._waiters: dict[str, asyncio.Future[bool]] = {}
        self._current_clause_id: str | None = None
        self._current_port: Any | None = None
        self._worker: asyncio.Task[None] | None = None
        self._delivery_task: asyncio.Task[None] | None = None
        self._owner_loop: asyncio.AbstractEventLoop | None = None
        self._closed = False
        self._current_cancel_reason: str | None = None
        self._cancel_generation = 0
        self._stream_id = 0

    async def startup(self) -> None:
        """Start the single delivery owner on its application event loop."""

        if self._closed:
            raise RuntimeError("speech delivery is closed")
        loop = asyncio.get_running_loop()
        if self._worker is not None and not self._worker.done():
            if self._owner_loop is not loop:
                raise RuntimeError("speech delivery belongs to another event loop")
            return
        self._owner_loop = loop
        self._worker = loop.create_task(self._run(), name="main-agent-speech-delivery")

    async def __call__(self, clause: Any) -> None:
        """Transfer one clause into the bounded worker queue and return without awaiting playback."""

        _required_text(getattr(clause, "clause_id", None), "clause id")
        _required_text(getattr(clause, "text", None), "clause text")
        await self.startup()
        try:
            self._queue.put_nowait(_SpeechJob(clause, self._cancel_generation))
        except asyncio.QueueFull as exc:
            raise SpeechProviderUnavailable("speech delivery queue is full") from exc

    async def _run(self) -> None:
        try:
            while True:
                job = await self._queue.get()
                if job.generation != self._cancel_generation:
                    try:
                        await self._settle_interrupted(str(job.clause.clause_id))
                    finally:
                        self._queue.task_done()
                    continue
                task = asyncio.create_task(
                    self._deliver(job), name="main-agent-speech-clause"
                )
                self._delivery_task = task
                try:
                    await task
                except asyncio.CancelledError:
                    if asyncio.current_task() is not None and asyncio.current_task().cancelling():
                        raise
                except BaseException:
                    await self._settle_interrupted(str(job.clause.clause_id))
                finally:
                    if self._delivery_task is task:
                        self._delivery_task = None
                    self._queue.task_done()
        except asyncio.CancelledError:
            task = self._delivery_task
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            raise

    async def _deliver(self, job: _SpeechJob) -> None:
        clause = job.clause
        clause_id = _required_text(getattr(clause, "clause_id", None), "clause id")
        loop = asyncio.get_running_loop()
        waiter = loop.create_future()
        self._waiters[clause_id] = waiter
        port = None
        complete = False
        try:
            port = self.renderer_port_factory()
            if inspect.isawaitable(port):
                port = await port
            if port is None:
                return
            if job.generation != self._cancel_generation:
                return
            self._current_clause_id = clause_id
            self._current_port = port
            if await _maybe_await(port.offer_clause(clause)) is not None:
                return
            if job.generation != self._cancel_generation:
                return
            await self._stream_clause(port, str(clause.text))
            try:
                complete = await asyncio.wait_for(
                    asyncio.shield(waiter), self.ack_timeout_seconds
                )
            except TimeoutError:
                complete = False
        except asyncio.CancelledError:
            raise
        except BaseException:
            complete = False
        finally:
            self._waiters.pop(clause_id, None)
            try:
                if not complete:
                    await self._safe_cancel_provider()
            finally:
                try:
                    if not complete and port is not None:
                        await self._safe_cancel_port_clause(
                            port, clause_id,
                            self._current_cancel_reason or "delivery_interrupted",
                        )
                finally:
                    try:
                        if not complete:
                            await self._settle_interrupted(clause_id)
                    finally:
                        if self._current_clause_id == clause_id:
                            self._current_clause_id = None
                            self._current_port = None
                            self._current_cancel_reason = None

    def acknowledge(self, clause_id: str, *, complete: bool) -> bool:
        """Resolve exactly one live playback waiter after Runtime has applied its ledger ACK."""

        waiter = self._waiters.get(clause_id)
        if waiter is None or waiter.done():
            return False
        loop = self._owner_loop
        if loop is None or loop.is_closed():
            return False

        def resolve() -> None:
            if not waiter.done():
                waiter.set_result(bool(complete))

        try:
            if asyncio.get_running_loop() is loop:
                resolve()
            else:
                loop.call_soon_threadsafe(resolve)
        except RuntimeError:
            try:
                loop.call_soon_threadsafe(resolve)
            except RuntimeError:
                return False
        return True

    async def cancel_current(self, reason: str = "confirmed_barge_in") -> None:
        """Cancel the current clause and settle every queued clause from its response generation."""

        self._cancel_generation += 1
        drained: list[_SpeechJob] = []
        while True:
            try:
                drained.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        clause_id = self._current_clause_id
        task = self._delivery_task
        self._current_cancel_reason = reason
        try:
            for job in drained:
                try:
                    await self._settle_interrupted(str(job.clause.clause_id))
                finally:
                    self._queue.task_done()
            await self._safe_cancel_provider()
        finally:
            if clause_id is not None:
                self.acknowledge(clause_id, complete=False)
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def shutdown(self) -> None:
        """Interrupt current and queued clauses, settle them, and leave no worker task behind."""

        if self._closed:
            return
        self._closed = True
        await self.cancel_current("speech_shutdown")
        worker = self._worker
        if worker is not None and not worker.done():
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        self._worker = None
        while True:
            try:
                job = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            try:
                await self._settle_interrupted(str(job.clause.clause_id))
            finally:
                self._queue.task_done()

    async def _safe_cancel_provider(self) -> None:
        try:
            await asyncio.wait_for(self.provider.cancel(), self.cancel_timeout_seconds)
        except BaseException:
            pass

    async def _settle_interrupted(self, clause_id: str) -> None:
        if self.settle_clause is None:
            return
        try:
            await _maybe_await(self.settle_clause(clause_id, complete=False))
        except BaseException:
            pass

    async def _stream_clause(self, port: Any, text: str) -> None:
        from .realtime import AudioFrame, AudioFrameKind, FINAL_FLAG

        self._stream_id = (self._stream_id + 1) & 0xFFFFFFFF
        if self._stream_id == 0:
            self._stream_id = 1
        sequence = 0
        sample_start = 0
        buffer = bytearray()
        async for chunk in self.provider.synthesize(text):
            buffer.extend(chunk)
            while len(buffer) >= 960:
                payload = bytes(buffer[:960])
                del buffer[:960]
                result = await _maybe_await(port.queue_tts(AudioFrame(
                    AudioFrameKind.TTS, 0, self._stream_id, sequence, sample_start, payload,
                )))
                if result is not None:
                    raise RuntimeError(f"renderer rejected TTS frame: {result}")
                sequence += 1
                sample_start += 480
        if len(buffer) % 2:
            raise ValueError("TTS PCM chunk ended on a partial sample")
        payload = bytes(buffer).ljust(960, b"\0")
        result = await _maybe_await(port.queue_tts(AudioFrame(
            AudioFrameKind.TTS, FINAL_FLAG, self._stream_id, sequence, sample_start, payload,
        )))
        if result is not None:
            raise RuntimeError(f"renderer rejected final TTS frame: {result}")

    async def _safe_cancel_port_clause(
        self, port: Any, clause_id: str, reason: str
    ) -> None:
        cancel = getattr(port, "cancel_clause", None)
        if callable(cancel):
            try:
                await asyncio.wait_for(
                    _call_maybe_blocking(cancel, clause_id, reason),
                    self.cancel_timeout_seconds,
                )
            except BaseException:
                pass


def _normalize_dashscope_event(event: Any) -> TranscriptUpdate | None:
    if isinstance(event, Mapping):
        sentence = event.get("sentence", event)
        outer = event
    else:
        getter = getattr(event, "get_sentence", None)
        sentence = getter() if callable(getter) else None
        outer = {}
    if not isinstance(sentence, Mapping):
        return None
    text = sentence.get("text")
    if not isinstance(text, str) or not text:
        return None
    ended = bool(sentence.get("sentence_end", sentence.get("is_sentence_end", False)))
    stable = sentence.get("stable_prefix", sentence.get("stable_text", ""))
    if not isinstance(stable, str) or not text.startswith(stable):
        stable = ""
    if ended:
        stable = text
    stability = outer.get("stability") if isinstance(outer, Mapping) else None
    if not isinstance(stability, (int, float)) or isinstance(stability, bool):
        stability = None
    return TranscriptUpdate(
        text, stable, text[len(stable):], "sentence_end" if ended else "partial",
        float(stability) if stability is not None else None,
    )


def _dashscope_stt_transport(**kwargs: Any) -> Any:
    asr = importlib.import_module("dashscope.audio.asr")
    on_event = kwargs["on_event"]

    class Callback(asr.RecognitionCallback):
        def on_event(self, result: Any) -> None:
            sentence = result.get_sentence()
            on_event({
                "sentence": {
                    "text": sentence.get("text", ""),
                    "sentence_end": bool(asr.RecognitionResult.is_sentence_end(sentence)),
                }
            })

    return asr.Recognition(
        model=kwargs["model_name"], format="pcm",
        sample_rate=kwargs["sample_rate_hz"], callback=Callback(),
        semantic_punctuation_enabled=False,
        **({"api_key": kwargs["api_key"]} if kwargs.get("api_key") else {}),
    )


class _DashScopeTtsTransport:
    def __init__(self, synthesizer: Any) -> None:
        self._synthesizer = synthesizer

    def synthesize(self, text: str) -> bytes:
        return self._synthesizer.call(text)

    def cancel(self) -> None:
        cancel = getattr(self._synthesizer, "streaming_cancel", None)
        if callable(cancel):
            cancel()


def _dashscope_tts_transport(**kwargs: Any) -> _DashScopeTtsTransport:
    tts = importlib.import_module("dashscope.audio.tts_v2")
    audio_format = getattr(tts.AudioFormat, "PCM_24000HZ_MONO_16BIT")
    return _DashScopeTtsTransport(tts.SpeechSynthesizer(
        model=kwargs["model_name"], voice=kwargs["voice"], format=audio_format,
        **({"api_key": kwargs["api_key"]} if kwargs.get("api_key") else {}),
    ))


def _smart_turn_feature_extractor() -> Any:
    transformers = importlib.import_module("transformers")
    return transformers.WhisperFeatureExtractor(chunk_length=8)


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def _call_maybe_blocking(function: Callable[..., Any], *args: Any) -> Any:
    """Run synchronous provider SDK work in the shared executor without blocking the owner loop."""

    if inspect.iscoroutinefunction(function):
        return await function(*args)
    value = await asyncio.to_thread(function, *args)
    return await value if inspect.isawaitable(value) else value


def _next_sync_chunk(iterator: Any) -> tuple[bool, Any]:
    try:
        return True, next(iterator)
    except StopIteration:
        return False, None


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    return value


def _audio_chunk(value: Any) -> bytes:
    if not isinstance(value, bytes) or not value:
        raise ValueError("speech audio chunks must be non-empty bytes")
    return value


__all__ = [
    "ClauseSpeechDelivery", "DashScopeSpeechOutputProvider", "DashScopeStreamingSttProvider", "SileroVadDetector",
    "SmartTurnDetector", "SpeechCapabilityStatus", "SpeechInputCoordinator", "SpeechOutputProvider",
    "SpeechPipeline", "SpeechPipelineStatus",
    "SpeechProviderUnavailable", "StreamingSttProvider", "StreamingSttSession",
    "TranscriptUpdate",
]
