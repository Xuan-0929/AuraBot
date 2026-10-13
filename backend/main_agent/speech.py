"""Own the active Profile's async speech providers, local VAD/EOU, input coordinator, and delivery.

DashScope-specific callback fields and credentials terminate inside the adapters and become normalized
``TranscriptUpdate`` facts before Main Agent Core can observe them; every non-empty production snapshot,
including a mutable-only partial, requests existing PREPARE admission. Session callbacks enter Core in arrival
order; SAY-scoped protocol cancellation removes only that utterance from the existing delivery queue;
graceful finish drains provider-final text, while cancellation retains bounded transport cleanup
ownership. TTS exposes only clause audio bytes, spooled to a bounded temporary file when playback lags.
Silero VAD and Smart Turn
v3.2 share process-wide ONNX sessions, verify both model and license assets before loading, and configure
ONNX Runtime for sequential single-thread CPU inference; missing optional packages or assets are reported
as unavailable capabilities rather than preventing a non-speech Backend from importing or starting.
``SpeechService`` drains the active RealtimeHub microphone queue and supervises sequential clause delivery;
it serializes microphone processing with turn resets so typed input can reuse speech workers and detectors.
Static assets are verified on first load, outside the PCM hot path; resetting a turn invalidates old sinks
before closing STT and never cancels a running VAD thread before resetting its recurrent state.
It owns only speech resources and never shuts down MainAgentRuntime.
Continuous VAD qualification precedes Runtime/STT activation, retaining 400 ms pre-roll.
RMS remains observable during onset; the existing joint RMS/VAD gate still detects pauses after activation.
Delivery explicitly closes synthesis when playback-credit waits are cancelled, joining spool cleanup.
Hub-owned frozen diagnostic scopes observe 500 ms acoustic windows, VAD readiness and
session-specific STT finalization alongside EOU/TTS transitions synchronously;
they never retain audio, provider payloads, exceptions, or add workers to the speech pipeline.
STT sends and finalization share one coordinator lock; the adapter serializes actual SDK calls and
owns one stop task even after timeout. Failed recognition retires Runtime input instead of using partials.
SpeechService implements the same voice lifecycle and renderer ACK interface as RealtimeVoiceEngine;
Pipeline keeps its local turn policy while Profile capabilities own shared business effects.
"""

# Acoustic edges are sent to RealtimeHub independently of STT confirmation and Smart Turn completion.
from __future__ import annotations

import asyncio
import importlib
import time
import tempfile
import math
import sys
from array import array
import uuid
from collections import deque
from collections.abc import AsyncIterable, Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Protocol, runtime_checkable

from .metrics import speech_logger
from .debug import AcousticWindow

# Only these local preparation codes may cross the realtime transport.
SPEECH_PREPARATION_ERRORS = frozenset({
    "speech_disabled", "speech_api_key_missing", "speech_optional_dependency_missing",
    *(f"speech_{name}_{reason}" for name in ("vad", "eou") for reason in (
        "model_asset_missing", "license_asset_missing", "license_unverified",
        "optional_dependency_missing", "model_load_failed", "inference_failed",
    )),
})


TranscriptSink = Callable[["TranscriptUpdate"], Awaitable[None]]
_CLEANUP_TIMEOUT_SECONDS = 0.25
# Initial bounded budget for final network callbacks; tune with finish_ended elapsedMs on real devices.
_STT_FINISH_TIMEOUT_SECONDS = 1.0
MAX_TTS_SPOOL_BYTES = 24_000 * 2 * 120


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

    async def finish(self) -> None: ...

    async def close(self) -> None: ...


@runtime_checkable
class StreamingSttProvider(Protocol):
    """Start normalized streaming recognition without exposing provider event objects to Core."""

    async def start(self, on_update: TranscriptSink) -> StreamingSttSession: ...


@runtime_checkable
class SpeechOutputProvider(Protocol):
    """Synthesize one clause as ordered 24 kHz mono PCM16LE byte chunks."""

    def synthesize(self, text: str) -> AsyncIterable[bytes]: ...


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
        self._sink_lock = asyncio.Lock()
        self._failure_task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._ending = False
        self._io_lock = Lock()
        self._stop_task: asyncio.Task[None] | None = None

    def _transport_call(self, method: Callable[..., Any], *args: Any) -> None:
        # Cancelling to_thread does not stop its thread. Serialize the actual SDK operations here.
        with self._io_lock:
            method(*args)

    async def _stop(self, timeout: float) -> None:
        self._ending = True
        if self._stop_task is None:
            self._stop_task = self._owner_loop.create_task(
                asyncio.to_thread(self._transport_call, self._transport.stop))
            self._stop_task.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        await asyncio.wait_for(asyncio.shield(self._stop_task), timeout)

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
        task = self._owner_loop.create_task(self._deliver_in_order(update))
        self._sink_tasks.add(task)
        task.add_done_callback(self._sink_done)

    async def _deliver_in_order(self, update: TranscriptUpdate) -> None:
        # Callback arrival order survives awaits inside confirmation and Runtime admission.
        async with self._sink_lock:
            await self._sink(update)

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
            await self._stop(_CLEANUP_TIMEOUT_SECONDS)
        except BaseException:
            pass

    async def send_audio(self, pcm16le: bytes) -> None:
        if self._closed or self._ending:
            raise RuntimeError("STT session is closed")
        if not isinstance(pcm16le, bytes) or not pcm16le:
            raise ValueError("STT audio must be non-empty bytes")
        await asyncio.to_thread(self._transport_call, self._transport.send_audio_frame, pcm16le)

    async def finish(self) -> None:
        """Flush provider-final transcription, then drain its callbacks before sealing this turn."""
        if self._closed:
            await self.close()
            raise RuntimeError("STT session is closed")
        try:
            await self._stop(_STT_FINISH_TIMEOUT_SECONDS)
            # SDK-thread callbacks are scheduled before stop returns; let their sink tasks enter the set.
            await asyncio.sleep(0)
            if self._closed:
                raise RuntimeError("STT finalization was aborted")
            self._closed = True
            if self._sink_tasks:
                await asyncio.gather(*tuple(self._sink_tasks))
        finally:
            self._closed = True
            tasks = tuple(self._sink_tasks)
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self) -> None:
        """Keep abort cleanup owned even if the microphone caller itself is cancelled."""
        if self._close_task is not None:
            await asyncio.shield(self._close_task)
            return
        if self._closed:
            if self._failure_task is not None:
                await asyncio.shield(self._failure_task)
            return
        self._closed = True
        self._close_task = self._owner_loop.create_task(self._close_transport())
        await asyncio.shield(self._close_task)

    async def _close_transport(self) -> None:
        tasks = tuple(self._sink_tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await self._stop(_CLEANUP_TIMEOUT_SECONDS)
        except Exception:
            pass


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
            await asyncio.to_thread(transport.start)
        except BaseException as exc:
            if transport is not None:
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(transport.stop),
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
    """Create one independent callback-streaming DashScope transport per synthesized clause."""

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

    async def synthesize(self, text: str):
        clause = _required_text(text, "TTS clause")
        try:
            options = {"model_name": self.model_name, "voice": self.voice}
            if self._api_key:
                options["api_key"] = self._api_key
            transport = self._transport_factory(**options)
        except (ImportError, ModuleNotFoundError) as exc:
            raise SpeechProviderUnavailable("DashScope TTS dependency is unavailable") from exc
        source = self._callback_stream(transport, clause)
        try:
            async for chunk in source:
                yield chunk
        finally:
            await source.aclose()

    async def _callback_stream(self, transport: Any, clause: str):
        """Spool fast SDK callbacks to a bounded temporary file while playback applies backpressure.

        The existing SDK thread writes PCM; the event loop reads at most one 200 ms chunk at a time.
        A single wakeup flag coalesces callback notifications. No audio survives generator cleanup.
        """
        loop = asyncio.get_running_loop()
        changed = asyncio.Event()
        guard = Lock()
        spool = tempfile.TemporaryFile()
        written = 0
        read = 0
        terminal = False
        abandoned = False
        failure: BaseException | None = None
        notification_pending = False

        def wake() -> None:
            nonlocal notification_pending
            with guard:
                notification_pending = False
            changed.set()

        def publish(kind: str, value: Any = None) -> None:
            nonlocal written, terminal, failure, notification_pending
            with guard:
                if terminal or abandoned:
                    return
                try:
                    if kind == "data":
                        data = _audio_chunk(value)
                        if written + len(data) > MAX_TTS_SPOOL_BYTES:
                            speech_logger.warning("tts_spool_limit bytes=%d", written + len(data))
                            raise SpeechProviderUnavailable("TTS clause exceeds 120 seconds")
                        spool.seek(written)
                        spool.write(data)
                        written += len(data)
                    elif kind == "error":
                        failure = value if isinstance(value, BaseException) else RuntimeError("TTS provider error")
                        terminal = True
                    else:
                        terminal = True
                except BaseException as exc:
                    failure, terminal = exc, True
                if not notification_pending:
                    notification_pending = True
                    loop.call_soon_threadsafe(wake)

        def run_stream() -> None:
            try:
                transport.stream(clause, lambda chunk: publish("data", chunk),
                                 lambda: publish("complete"), lambda error: publish("error", error))
            except BaseException as exc:
                publish("error", exc)

        worker = asyncio.create_task(asyncio.to_thread(run_stream), name="dashscope-tts-stream")
        try:
            while True:
                changed.clear()
                with guard:
                    error = failure
                    done = terminal and read == written
                    spool.seek(read)
                    chunk = spool.read(min(9_600, written - read))
                    read += len(chunk)
                if error is not None:
                    raise SpeechProviderUnavailable("DashScope TTS streaming failed") from error
                if chunk:
                    yield chunk
                elif done:
                    break
                else:
                    await changed.wait()
        finally:
            with guard:
                abandoned = True
                cancel = not terminal or failure is not None
            try:
                if cancel:
                    await asyncio.to_thread(transport.cancel)
            finally:
                try:
                    await asyncio.gather(worker, return_exceptions=True)
                finally:
                    spool.close()


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
        self._session: Any | None = None

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
        if self._load_reason is not None:
            return None
        if self._session is not None:
            return self._session
        status = self.status()
        if not status.available:
            self._load_reason = status.reason
            return None
        key = (self.capability, self.model_path)
        with _ONNX_LOCK:
            if key in _ONNX_SESSIONS:
                self._session = _ONNX_SESSIONS[key]
                return self._session
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
            self._session = session
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

    @property
    def diagnostic_state(self) -> tuple[str, str | None]:
        """Describe cached inference state without loading models or reading static assets."""
        if self._load_reason is not None:
            status = "inference_failed" if self._load_reason in {"inference_failed", "invalid_audio"} else "unavailable"
            return status, self._load_reason
        if self._last_probability is not None:
            return "ready", None
        if self._session is not None:
            return "warming_up", None
        return "unavailable", "not_loaded"

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

    Continuous VAD must pass for 300 ms before any Runtime/STT side effects, including through
    brief volume dips. Pre-roll retains the onset; transcript callbacks cannot bypass confirmation.
    RMS and relative noise continue to qualify activity within an already active turn.
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
        playback_lifecycle: Any | None = None,
        pre_roll_frames: int = 20,
        frame_ms: int = 20,
        vad_threshold: float = 0.6,
        vad_stop_ms: int = 300,
        speech_confirm_ms: int = 300,
        min_speech_rms: float = 0.02,
        speech_noise_ratio: float = 3.0,
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
        self.debug = vars(runtime).get("debug")
        self.stt_provider = stt_provider
        self.vad = vad
        self.eou = eou
        self.playback_lifecycle = playback_lifecycle
        self.pre_roll = deque(maxlen=pre_roll_frames)
        self.audio = deque(maxlen=max(1, 8_000 // frame_ms))
        self.frame_ms = frame_ms
        self.vad_threshold = vad_threshold
        self.stop_frames = max(1, vad_stop_ms // frame_ms)
        self.confirm_frames = max(1, math.ceil(speech_confirm_ms / frame_ms))
        self.min_speech_rms = min_speech_rms
        self.speech_noise_ratio = speech_noise_ratio
        self._noise_floor = 0.003
        self._diagnostic_frames = 0
        self.eou_threshold = eou_threshold
        self.eou_recheck_seconds = eou_recheck_seconds
        self.incomplete_fallback_seconds = incomplete_fallback_seconds
        self.unavailable_fallback_seconds = unavailable_fallback_seconds
        self.eou_inference_timeout_seconds = eou_inference_timeout_seconds
        if eou_inference_timeout_seconds <= 0:
            raise ValueError("EOU inference timeout must be positive")
        self.clock = clock or time.monotonic
        self.sleep = sleep or asyncio.sleep
        self._acoustics = AcousticWindow(self.debug, self.clock)
        self._vad_failed = False
        self._finalizing_session: StreamingSttSession | None = None
        self._active = False
        self._speech_frames = 0
        self._silence_frames = 0
        self._stop_issued = False
        self._has_transcript = False
        self._session: StreamingSttSession | None = None
        self._stt_lock = asyncio.Lock()
        self._segment_id: str | None = None
        self._endpoint_task: asyncio.Task[None] | None = None
        self._eou_work: asyncio.Task[Any] | None = None
        self._endpoint_generation = 0
        self._endpoint_committing = False
        self._closed = False

    def _debug(self, stage: str, name: str, **details: Any) -> None:
        debug = self.debug
        if debug is not None and debug.enabled:
            debug.emit(stage, name, segmentId=self._segment_id, **details)

    def debug_snapshot(self) -> None:
        debug = self.debug
        if debug is None or not debug.enabled:
            return
        debug.recorder.update_state(debug, "input", active=self._active,
            onsetGate="vad",
            confirmMs=self._speech_frames * self.frame_ms, silenceMs=self._silence_frames * self.frame_ms,
            confirmTargetMs=self.confirm_frames * self.frame_ms, silenceTargetMs=self.stop_frames * self.frame_ms,
            noiseFloor=self._noise_floor, threshold=max(self.min_speech_rms, self._noise_floor * self.speech_noise_ratio))
        active = getattr(self.runtime, "active_input", None)
        text = next((item.text for item in active.segments if item.segment_id == self._segment_id), "") if active else ""
        debug.recorder.update_state(debug, "stt", segmentId=self._segment_id, active=self._session is not None,
            hasTranscript=self._has_transcript, text=text)
        debug.recorder.update_state(debug, "eou", segmentId=self._segment_id,
            endpointPending=self._endpoint_task is not None and not self._endpoint_task.done(),
            threshold=self.eou_threshold, fallbackMs=self.incomplete_fallback_seconds * 1000,
            timeoutMs=self.eou_inference_timeout_seconds * 1000)

    async def accept_mic_frame(self, pcm16le: bytes) -> None:
        if self._closed:
            return
        if not isinstance(pcm16le, bytes) or len(pcm16le) != self.frame_ms * 32:
            raise ValueError("mic frame must contain exactly one PCM16 frame")
        self.pre_roll.append(pcm16le)
        self.audio.append(pcm16le)
        probability = await self._vad_probability(pcm16le)
        before_confirm_ms, was_active = self._speech_frames * self.frame_ms, self._active
        observing = self.debug is not None and self.debug.enabled
        rms = 0.0
        if probability is not None or observing:
            samples = array("h", pcm16le)
            if sys.byteorder != "little":
                samples.byteswap()
            rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples)) / 32768
        threshold = max(self.min_speech_rms, self._noise_floor * self.speech_noise_ratio)
        if probability is None:
            self._speech_frames = 0
            self._observe_acoustics(probability, rms, threshold, before_confirm_ms, was_active)
            if self._active:
                await self._cancel_endpoint()
                try:
                    await self._reject_candidate()
                finally:
                    await self._finish_turn()
            return
        speech = probability >= self.vad_threshold and rms >= threshold
        if self._finalizing_session is not None or self._endpoint_committing:
            if not speech or self._endpoint_task is None:
                self._observe_acoustics(probability, rms, threshold, before_confirm_ms, was_active)
                return
            # Once stop starts it cannot be undone. Preserve this onset while the bounded finish
            # settles, then let subsequent queued PCM qualify a fresh segment.
            await self.wait_for_endpoint()
            if self._closed:
                return
            self.pre_roll.append(pcm16le)
            self.audio.append(pcm16le)
        self._diagnostic_frames += 1
        if self._diagnostic_frames % 250 == 0:
            speech_logger.info("mic_gate onset_gate=vad rms=%.4f noise=%.4f threshold=%.4f vad=%.3f active=%s", rms, self._noise_floor, threshold, probability, self._active)
        if not self._active:
            if probability < self.vad_threshold:
                self._noise_floor += 0.05 * (rms - self._noise_floor)
            # A quiet phoneme must not erase otherwise continuous VAD evidence at onset.
            self._speech_frames = self._speech_frames + 1 if probability >= self.vad_threshold else 0
            self._observe_acoustics(probability, rms, threshold, before_confirm_ms, was_active)
            if self._speech_frames >= self.confirm_frames:
                speech_logger.info("speech_confirmed onset_gate=vad rms=%.4f threshold=%.4f vad=%.3f duration_ms=%d", rms, threshold, probability, self._speech_frames * self.frame_ms)
                await self._start_candidate()
            return

        if self._session is not None:
            session = self._session
            try:
                async with self._stt_lock:
                    if self._session is session and self._finalizing_session is None:
                        await session.send_audio(pcm16le)
            except Exception as error:
                self._stt_error("stt_send_failed", "send_audio", error, session)
                await self._fail_input()
                return
        if speech:
            if self._stop_issued:
                await self._cancel_endpoint()
                self._stop_issued = False
                self._speech_frames = 0
                await self.runtime.vad_start()
                # This utterance already passed VAD confirmation. A brief continuation must
                # not discard its earlier transcript by requiring another full onset window.
                await self.runtime.confirm_speech()
                await self._playback("speech_activity", active=True)
            self._silence_frames = 0
            self._observe_acoustics(probability, rms, threshold, before_confirm_ms, was_active)
            return

        self._speech_frames = 0
        self._silence_frames += 1
        self._observe_acoustics(probability, rms, threshold, before_confirm_ms, was_active)
        if self._stop_issued or self._silence_frames < self.stop_frames:
            return
        self._stop_issued = True
        self._debug("input", "silence", silenceMs=self._silence_frames * self.frame_ms)
        await self.runtime.vad_stop_candidate()
        await self._playback("speech_activity", active=False)
        self._endpoint_generation += 1
        generation = self._endpoint_generation
        self._endpoint_task = asyncio.create_task(
            self._resolve_endpoint(generation), name="main-agent-speech-endpoint"
        )

    def _observe_acoustics(self, probability: float | None, rms: float, threshold: float,
                           before_confirm_ms: int, was_active: bool) -> None:
        if self.debug is None or not self.debug.enabled:
            return
        try:
            if self._vad_failed:
                status, reason = "inference_failed", "inference_failed"
            elif probability is not None:
                status, reason = "ready", None
            else:
                status, reason = getattr(self.vad, "diagnostic_state", ("unavailable", "unknown"))
            self._acoustics.observe(frame_ms=self.frame_ms, before_confirm_ms=before_confirm_ms,
                was_active=was_active, segmentId=self._segment_id, rms=rms,
                noiseFloor=self._noise_floor, threshold=threshold, vad=probability,
                vadThreshold=self.vad_threshold, vadStatus=status, vadReason=reason,
                onsetGate="vad",
                speech=probability is not None and probability >= self.vad_threshold
                    and (not self._active or rms >= threshold),
                active=self._active, confirmMs=self._speech_frames * self.frame_ms,
                silenceMs=self._silence_frames * self.frame_ms,
                confirmTargetMs=self.confirm_frames * self.frame_ms,
                silenceTargetMs=self.stop_frames * self.frame_ms)
        except Exception:
            # Observation must not alter microphone processing even for a broken diagnostic adapter.
            self.debug.recorder.dropped += 1

    def _stt_error(self, code: str, operation: str, error: Exception,
                   session: StreamingSttSession | None) -> None:
        self._debug("stt", "error", level="error", code=code, operation=operation,
            finalizing=session is not None and self._finalizing_session is session,
            endpointPending=self._endpoint_task is not None and not self._endpoint_task.done(),
            exceptionType=type(error).__name__)

    async def wait_for_endpoint(self) -> None:
        task = self._endpoint_task
        if task is not None:
            await asyncio.shield(task)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.reset_input()

    async def reset_input(self) -> None:
        """Abort the old turn without final transcription, preserving reusable detectors/providers."""

        self._acoustics.flush()
        self._active = False
        self._segment_id = None
        self._noise_floor = 0.003
        await self._cancel_endpoint()
        if self._eou_work is not None:
            await asyncio.gather(asyncio.shield(self._eou_work), return_exceptions=True)
            self._eou_work = None
        await self._finish_turn()

    async def _start_candidate(self) -> None:
        self._active = True
        self._speech_frames = self.confirm_frames
        self._silence_frames = 0
        self._stop_issued = False
        self._has_transcript = False
        self._segment_id = str(uuid.uuid4())
        self._debug("input", "confirmed", onsetGate="vad", confirmMs=self._speech_frames * self.frame_ms)
        await self._playback("speech_candidate_started")
        await self.runtime.vad_start()
        await self._playback("speech_confirmed")
        await self.runtime.confirm_speech()
        if self.stt_provider is None:
            return
        segment_id = self._segment_id
        async def on_transcript(update: TranscriptUpdate) -> None:
            await self._on_transcript(update, segment_id)
        operation = "start"
        try:
            self._debug("stt", "start", active=True)
            self._session = await self.stt_provider.start(on_transcript)
            operation = "send_audio"
            for frame in tuple(self.pre_roll):
                await self._session.send_audio(frame)
        except Exception as error:
            self._stt_error("stt_start_failed", operation, error, self._session)
            await self._fail_input()

    async def _on_transcript(self, update: TranscriptUpdate, segment_id: str | None) -> None:
        if not self._transcript_current(segment_id) or not update.full_text:
            return
        self._has_transcript = True
        if self.debug is not None and self.debug.enabled:
            self._debug("stt", "update", text=update.full_text, stablePrefix=update.stable_prefix,
                mutableSuffix=update.mutable_suffix, stability=update.provider_stability, active=True)
        await self.runtime.update_transcript(
            update.full_text,
            segment_id=segment_id,
            request_prepare=True,
        )

    def _transcript_current(self, segment_id: str | None) -> bool:
        return not self._closed and self._active and segment_id == self._segment_id

    async def _resolve_endpoint(self, generation: int) -> None:
        candidate_at = self.clock()
        self._debug("eou", "start", endpointPending=True, threshold=self.eou_threshold)
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
                    await self._playback("speech_endpoint", complete=True)
                    if not await self._finalize_endpoint():
                        finish = True
                        return
                    self._debug("eou", "fallback", elapsedMs=(self.clock() - candidate_at) * 1000)
                    await self.runtime.endpoint_fallback()
                    finish = True
                return
            if probability >= self.eou_threshold:
                if self._endpoint_current(generation):
                    await self._playback("speech_endpoint", complete=True)
                    if not await self._finalize_endpoint():
                        finish = True
                        return
                    self._debug("eou", "complete", probability=probability, elapsedMs=(self.clock() - candidate_at) * 1000)
                    await self.runtime.eou_complete()
                    finish = True
                return
            await self._playback("speech_endpoint", complete=False)
            self._debug("eou", "incomplete", probability=probability, incomplete=True)
            await self.runtime.eou_incomplete()
            deadline = candidate_at + self.incomplete_fallback_seconds
            while self._endpoint_current(generation):
                remaining = deadline - self.clock()
                if remaining <= 0:
                    await self._playback("speech_endpoint", complete=True)
                    if not await self._finalize_endpoint():
                        finish = True
                        return
                    self._debug("eou", "fallback", elapsedMs=(self.clock() - candidate_at) * 1000)
                    await self.runtime.endpoint_fallback()
                    finish = True
                    return
                await self.sleep(min(self.eou_recheck_seconds, round(remaining, 9)))
                if not self._endpoint_current(generation):
                    return
                if self.clock() >= deadline:
                    await self._playback("speech_endpoint", complete=True)
                    if not await self._finalize_endpoint():
                        finish = True
                        return
                    self._debug("eou", "fallback", elapsedMs=(self.clock() - candidate_at) * 1000)
                    await self.runtime.endpoint_fallback()
                    finish = True
                    return
                try:
                    probability = await asyncio.wait_for(
                        self._eou_probability(),
                        min(self.eou_inference_timeout_seconds, deadline - self.clock()),
                    )
                except TimeoutError:
                    self._debug("eou", "timeout", level="warning", code="eou_recheck_timeout")
                    probability = None
                if probability is not None and probability >= self.eou_threshold:
                    await self._playback("speech_endpoint", complete=True)
                    if not await self._finalize_endpoint():
                        finish = True
                        return
                    self._debug("eou", "complete", probability=probability, elapsedMs=(self.clock() - candidate_at) * 1000)
                    await self.runtime.eou_complete()
                    finish = True
                    return
        except TimeoutError:
            self._debug("eou", "timeout", level="warning", code="eou_timeout",
                elapsedMs=(self.clock() - candidate_at) * 1000)
            delay = max(0.0, candidate_at + self.unavailable_fallback_seconds - self.clock())
            if delay:
                await self.sleep(delay)
            if self._endpoint_current(generation):
                await self._playback("speech_endpoint", complete=True)
                if not await self._finalize_endpoint():
                    finish = True
                    return
                self._debug("eou", "fallback", elapsedMs=(self.clock() - candidate_at) * 1000)
                await self.runtime.endpoint_fallback()
                finish = True
        except asyncio.CancelledError:
            self._debug("eou", "cancel", cancelled=True, endpointPending=False)
            raise
        except Exception:
            self._debug("eou", "error", level="warning", code="eou_unavailable")
            if self._endpoint_current(generation):
                await self._playback("speech_endpoint", complete=True)
                if not await self._finalize_endpoint():
                    finish = True
                    return
                self._debug("eou", "fallback", elapsedMs=(self.clock() - candidate_at) * 1000)
                await self.runtime.endpoint_fallback()
            finish = True
        finally:
            if finish and self._endpoint_current(generation):
                await self._finish_turn()

    async def _finalize_endpoint(self) -> bool:
        """Allow a confirmed utterance to receive its first transcript from provider stop."""
        self._endpoint_committing = True
        if not await self._close_stt(finalize=True):
            await self._fail_input()
            return False
        if not self._has_transcript:
            await self._reject_candidate()
            return False
        return True

    def _endpoint_current(self, generation: int) -> bool:
        return self._stop_issued and generation == self._endpoint_generation and not self._closed

    async def _vad_probability(self, frame: bytes) -> float | None:
        self._vad_failed = False
        if self.vad is None:
            return None
        try:
            value = await asyncio.to_thread(self.vad.speech_probability, frame)
        except Exception:
            self._vad_failed = True
            return None
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    async def _eou_probability(self) -> float | None:
        started = self.clock()
        if self.eou is None:
            self._debug("eou", "unavailable", probability=None, code="eou_unavailable")
            return None
        try:
            # A timed-out endpoint stops waiting, not its inference thread. Reuse remains serial,
            # and reset joins this owner before another turn can use the detector.
            if self._eou_work is not None:
                await asyncio.shield(self._eou_work)
            self._eou_work = asyncio.create_task(asyncio.to_thread(
                self.eou.completion_probability, b"".join(self.audio)
            ))
            value = await asyncio.shield(self._eou_work)
        except Exception:
            self._debug("eou", "error", level="warning", code="eou_inference_failed")
            return None
        self._debug("eou", "probability", probability=value if isinstance(value, (int, float)) else None,
            elapsedMs=(self.clock() - started) * 1000, threshold=self.eou_threshold)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    async def _cancel_endpoint(self) -> None:
        self._endpoint_generation += 1
        task = self._endpoint_task
        self._endpoint_task = None
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _close_stt(self, *, finalize: bool = False) -> bool:
        session = self._session
        if session is None:
            return True
        segment_id = self._segment_id
        started = self.clock()
        if finalize:
            self._finalizing_session = session
            self._debug("stt", "finish_started", operation="finish", finalizing=True)
        try:
            async with self._stt_lock:
                if self._session is not session:
                    return True
                self._session = None
                try:
                    if finalize:
                        await asyncio.wait_for(session.finish(), _STT_FINISH_TIMEOUT_SECONDS)
                    else:
                        await session.close()
                except BaseException:
                    # Join the adapter's existing stop owner; never launch a competing stop.
                    await session.close()
                    raise
        except asyncio.CancelledError:
            # Reset/shutdown can join cancellation-safe adapter cleanup on a subsequent close.
            if self._session is None:
                self._session = session
            raise
        except Exception as error:
            self._stt_error("stt_finish_failed" if finalize else "stt_close_failed",
                "finish" if finalize else "close", error, session)
            if self._session is session:
                self._session = None
            self._debug("stt", "end", active=False, complete=False)
            return False
        finally:
            if finalize:
                if self._finalizing_session is session:
                    self._finalizing_session = None
                if self.debug is not None:
                    self.debug.emit("stt", "finish_ended", segmentId=segment_id,
                        operation="finish", finalizing=False, elapsedMs=(self.clock() - started) * 1000)
        if self._session is session:
            self._session = None
        self._debug("stt", "end", active=False, complete=finalize)
        return True

    async def _fail_input(self) -> None:
        segment_id = self._segment_id
        self._segment_id = None  # Fence late callbacks before any awaited cleanup.
        await self._cancel_endpoint()
        try:
            await self.runtime.speech_input_failed(segment_id, has_transcript=self._has_transcript)
        finally:
            await self._playback("speech_candidate_rejected")
            await self._finish_turn()

    async def _playback(self, method_name: str, **fields: Any) -> None:
        lifecycle = self.playback_lifecycle
        try:
            if method_name == "speech_activity":
                await lifecycle.speech_activity(**fields)
            elif method_name == "speech_candidate_started":
                await lifecycle.speech_candidate_started()
            elif method_name == "speech_confirmed":
                await lifecycle.speech_confirmed()
            elif method_name == "speech_candidate_rejected":
                await lifecycle.speech_candidate_rejected()
            else:
                await lifecycle.speech_endpoint(**fields)
        except Exception:
            # Renderer transport failure must not terminate microphone recognition.
            pass

    async def _reject_candidate(self) -> None:
        self._debug("input", "rejected", reason="no_transcript")
        await self._playback("speech_candidate_rejected")
        await self.runtime.reject_speech_candidate()

    async def _finish_turn(self) -> None:
        self._debug("eou", "end", endpointPending=False)
        await self._playback("speech_activity", active=False)
        await self._close_stt()
        self._acoustics.flush()
        self._active = False
        self._speech_frames = 0
        self._silence_frames = 0
        self._stop_issued = False
        self._has_transcript = False
        self._segment_id = None
        self._endpoint_committing = False
        self.pre_roll.clear()
        self.audio.clear()
        if self.vad is not None:
            try:
                self.vad.reset()
            except Exception:
                pass


class SpeechService:
    """Own the active Profile's coordinator, delivery worker, and one microphone consumer."""

    mode = "pipeline"

    def __init__(self, hub: Any, coordinator: Any, delivery: Any) -> None:
        self.hub = hub
        self.coordinator = coordinator
        self.delivery = delivery
        self._mic_task: asyncio.Task[None] | None = None
        self._input_lock = asyncio.Lock()
        self._input_generation = 0
        self._closed = False

    @classmethod
    def compose(
        cls,
        hub: Any,
        runtime: Any,
        *,
        stt_provider: StreamingSttProvider,
        output_provider: SpeechOutputProvider,
        vad: Any,
        eou: Any,
    ) -> "SpeechService":
        delivery = ClauseSpeechDelivery(output_provider, hub.acquire_renderer_port, runtime)
        return cls(
            hub,
            SpeechInputCoordinator(
                runtime,
                stt_provider=stt_provider,
                vad=vad,
                eou=eou,
                playback_lifecycle=hub,
            ),
            delivery,
        )

    async def startup(self) -> None:
        if self._closed:
            raise RuntimeError("speech service is closed")
        if self._mic_task is not None and not self._mic_task.done():
            return
        await self.delivery.startup()
        self._mic_task = asyncio.create_task(self._consume_mic(), name="main-agent-speech-mic")

    def acknowledge(self, clause_id: str, *, complete: bool) -> bool:
        return self.delivery.acknowledge(clause_id, complete=complete)

    async def renderer_ack(self, clause_id: str, *, complete: bool) -> bool:
        return await self.delivery.runtime.renderer_ack(clause_id, complete=complete)

    async def cancel_current(self, reason: str) -> None:
        await self.delivery.cancel_current(reason)

    async def reset_input(self) -> None:
        """Join the current PCM operation, then retire capture while keeping both workers alive."""

        async with self._input_lock:
            self._input_generation += 1
            self.hub.mic_queue.clear()
            await self.coordinator.reset_input()

    async def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        async with self._input_lock:
            task, self._mic_task = self._mic_task, None
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await self.coordinator.close()
        await self.delivery.shutdown()

    async def _consume_mic(self) -> None:
        while True:
            frame = await self.hub.mic_queue.get()
            generation = self._input_generation
            async with self._input_lock:
                if not self._closed and generation == self._input_generation:
                    await self.coordinator.accept_mic_frame(frame)


@dataclass(frozen=True, slots=True)
class _SpeechJob:
    clause: Any


class ClauseSpeechDelivery:
    """Serialize TTS clauses and wait for the Pet's physical ACK before advancing."""

    def __init__(
        self,
        provider: SpeechOutputProvider,
        renderer_port_factory: Callable[[], Awaitable[Any]],
        runtime: Any,
        *,
        max_pending_clauses: int = 16,
        ack_timeout_seconds: float = 30.0,
    ) -> None:
        if max_pending_clauses < 1:
            raise ValueError("pending speech clause bound must be positive")
        if ack_timeout_seconds <= 0:
            raise ValueError("speech ACK timeout must be positive")
        self.provider = provider
        self.renderer_port_factory = renderer_port_factory
        self.runtime = runtime
        self.debug = vars(runtime).get("debug")
        self.ack_timeout_seconds = ack_timeout_seconds
        self._queue: asyncio.Queue[_SpeechJob] = asyncio.Queue(max_pending_clauses)
        self._waiters: dict[str, asyncio.Future[bool]] = {}
        self._current_clause_id: str | None = None
        self._current_say_id: str | None = None
        self._current_port: Any | None = None
        self._worker: asyncio.Task[None] | None = None
        self._delivery_task: asyncio.Task[None] | None = None
        self._closed = False

    def debug_snapshot(self) -> None:
        debug = self.debug
        if debug is not None and debug.enabled:
            debug.recorder.update_state(debug, "tts", clauseId=self._current_clause_id,
                sayId=self._current_say_id, queuedClauses=self._queue.qsize(),
                running=self._delivery_task is not None and not self._delivery_task.done())

    def _debug(self, name: str, clause: Any, **details: Any) -> None:
        debug = self.debug
        if debug is not None and debug.enabled:
            debug.emit("tts", name, clauseId=clause.clause_id, sayId=getattr(clause, "say_id", None),
                inputId=getattr(clause, "input_id", None), invokeId=getattr(clause, "invoke_id", None), **details)

    async def startup(self) -> None:
        if self._closed:
            raise RuntimeError("speech delivery is closed")
        if self._worker is not None and not self._worker.done():
            return
        self._worker = asyncio.create_task(self._run(), name="main-agent-speech-delivery")

    async def __call__(self, clause: Any) -> None:
        clause_id = _required_text(getattr(clause, "clause_id", None), "clause id")
        _required_text(getattr(clause, "text", None), "clause text")
        await self.startup()
        try:
            self._queue.put_nowait(_SpeechJob(clause))
        except asyncio.QueueFull as exc:
            self._debug("failure", clause, level="warning", code="tts_queue_full")
            await self.runtime.renderer_failed(clause_id)
            raise SpeechProviderUnavailable("speech delivery queue is full") from exc
        await self.runtime.renderer_tts_queued(clause_id)
        self._debug("queued", clause, queuedClauses=self._queue.qsize())

    async def _run(self) -> None:
        while True:
            job = await self._queue.get()
            self._current_clause_id = str(job.clause.clause_id)
            self._current_say_id = getattr(job.clause, "say_id", None)
            task = asyncio.create_task(self._deliver(job), name="main-agent-speech-clause")
            self._delivery_task = task
            try:
                await task
            except asyncio.CancelledError:
                if asyncio.current_task() and asyncio.current_task().cancelling():
                    raise
            finally:
                self._delivery_task = None
                self._queue.task_done()

    async def _deliver(self, job: _SpeechJob) -> None:
        clause = job.clause
        clause_id = _required_text(getattr(clause, "clause_id", None), "clause id")
        loop = asyncio.get_running_loop()
        waiter = loop.create_future()
        self._waiters[clause_id] = waiter
        port: Any | None = None
        failed = False
        started = time.monotonic()
        try:
            port = await self.renderer_port_factory()
            if port is None:
                raise SpeechProviderUnavailable("renderer is unavailable")
            self._current_clause_id = clause_id
            self._current_say_id = getattr(clause, "say_id", None)
            self._current_port = port
            if await port.offer_clause(clause) is not None:
                raise SpeechProviderUnavailable("renderer rejected clause")
            played = False
            first_audio = True
            self._debug("start", clause, running=True)
            buffer = bytearray()
            stream = self.provider.synthesize(str(clause.text)).__aiter__()
            try:
                async for chunk in stream:
                    buffer.extend(_audio_chunk(chunk))
                    size = len(buffer) - len(buffer) % 2
                    if not size:
                        continue
                    payload = bytes(buffer[:size])
                    del buffer[:size]
                    if first_audio:
                        first_audio = False
                        self._debug("first_audio", clause, firstAudio=True, elapsedMs=(time.monotonic() - started) * 1000)
                    if await port.send_audio(payload) is not None:
                        raise SpeechProviderUnavailable("renderer rejected audio")
                    if not played:
                        played = True
                        await self.runtime.renderer_playing(clause_id)
            finally:
                close = getattr(stream, "aclose", None)
                if close is not None:
                    await close()
            if buffer:
                raise SpeechProviderUnavailable("TTS ended on a partial PCM sample")
            if not played:
                raise SpeechProviderUnavailable("TTS returned no audio")
            if await port.finish_audio(clause_id) is not None:
                raise SpeechProviderUnavailable("renderer rejected audio end")
            self._debug("end", clause, running=False, elapsedMs=(time.monotonic() - started) * 1000)
            if not await asyncio.wait_for(asyncio.shield(waiter), self.ack_timeout_seconds):
                await self.runtime.renderer_ack(clause_id, complete=False)
        except asyncio.CancelledError:
            self._debug("cancel", clause, cancelled=True, running=False)
            raise
        except BaseException as exc:
            self._debug("failure", clause, level="error", code="ack_timeout" if isinstance(exc, TimeoutError) else "tts_delivery_failed", running=False)
            failed = True
            speech_logger.warning("delivery_failed clause=%s error=%s", clause_id, type(exc).__name__)
            await self.runtime.renderer_failed(clause_id)
        finally:
            self._waiters.pop(clause_id, None)
            if failed and port is not None:
                try:
                    await port.cancel_clause(clause_id, "delivery_failed")
                except BaseException:
                    pass
            if self._current_clause_id == clause_id:
                self._current_clause_id = None
                self._current_say_id = None
                self._current_port = None

    def acknowledge(self, clause_id: str, *, complete: bool) -> bool:
        waiter = self._waiters.get(clause_id)
        if waiter is None or waiter.done():
            return False
        waiter.set_result(bool(complete))
        return True

    async def cancel_current(self, reason: str = "confirmed_barge_in", *, say_id: str | None = None) -> None:
        """Cancel all pending delivery, or only the invalidated SAY without disturbing another scope."""
        drained: list[_SpeechJob] = []
        retained: list[_SpeechJob] = []
        while True:
            try:
                job = self._queue.get_nowait()
                (drained if say_id is None or getattr(job.clause, "say_id", None) == say_id else retained).append(job)
            except asyncio.QueueEmpty:
                break
        for job in retained:
            self._queue.task_done()
            self._queue.put_nowait(job)
        selected = say_id is None or self._current_say_id == say_id
        clause_id = self._current_clause_id if selected else None
        port = self._current_port if selected else None
        task = self._delivery_task if selected else None
        for job in drained:
            await self.runtime.renderer_ack(str(job.clause.clause_id), complete=False)
            self._queue.task_done()
        if clause_id is not None:
            if port is not None:
                try:
                    await port.cancel_clause(clause_id, reason)
                except BaseException:
                    pass
            else:
                await self.runtime.renderer_ack(clause_id, complete=False)
            self.acknowledge(clause_id, complete=False)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.cancel_current("speech_shutdown")
        worker = self._worker
        if worker is not None and not worker.done():
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        self._worker = None


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
    """Own one callback-mode DashScope synthesizer per streamed clause."""

    def __init__(self, module: Any, options: dict[str, Any]) -> None:
        self._module = module
        self._options = options
        self._synthesizer: Any | None = None

    def stream(self, text: str, on_data: Any, on_complete: Any, on_error: Any) -> None:
        module = self._module

        class Callback(module.ResultCallback):
            def on_open(self) -> None:
                return None

            def on_complete(self) -> None:
                on_complete()

            def on_error(self, message: Any) -> None:
                on_error(RuntimeError(str(message)))

            def on_close(self) -> None:
                return None

            def on_data(self, data: bytes) -> None:
                on_data(data)

        self._synthesizer = module.SpeechSynthesizer(
            callback=Callback(), **self._options
        )
        self._synthesizer.streaming_call(text)
        self._synthesizer.streaming_complete()

    def cancel(self) -> None:
        if self._synthesizer is not None:
            self._synthesizer.streaming_cancel()


def _dashscope_tts_transport(**kwargs: Any) -> _DashScopeTtsTransport:
    dashscope = importlib.import_module("dashscope")
    tts = importlib.import_module("dashscope.audio.tts_v2")
    if kwargs.get("api_key"):
        dashscope.api_key = kwargs["api_key"]
    audio_format = tts.AudioFormat.PCM_24000HZ_MONO_16BIT
    return _DashScopeTtsTransport(tts, {
        "model": kwargs["model_name"],
        "voice": kwargs["voice"],
        "format": audio_format,
    })


def _smart_turn_feature_extractor() -> Any:
    transformers = importlib.import_module("transformers")
    return transformers.WhisperFeatureExtractor(chunk_length=8)


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
    "SpeechService",
    "SpeechProviderUnavailable", "StreamingSttProvider", "StreamingSttSession",
    "TranscriptUpdate",
]
