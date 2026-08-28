"""Exercise normalized speech-provider and lazy local-model boundaries without paid network calls.

The tests inject tiny DashScope transports and ONNX Runtime doubles, so ordinary Backend verification
never imports optional speech packages, downloads model assets, or contacts a provider.  They prove that
provider-private transcript facts are normalized before Core sees them and that local model loading is
licensed, lazy, process-wide, and configured for one low-resource CPU thread.
"""

from __future__ import annotations

import asyncio
import tempfile
import threading
import types
import unittest
from collections import deque
from pathlib import Path

import numpy as np

from backend.main_agent import ClauseState, ContextStore, SnapshotProjector
from backend.main_agent.runtime import MainAgentRuntime
from backend.main_agent.settings import MainAgentSettings
from backend.main_agent.speech import (
    ClauseSpeechDelivery,
    DashScopeSpeechOutputProvider,
    DashScopeStreamingSttProvider,
    SileroVadDetector,
    SpeechInputCoordinator,
    SpeechPipeline,
    SpeechProviderUnavailable,
    SmartTurnDetector,
    TranscriptUpdate,
)
from backend.main_agent.realtime import (
    AudioFrame,
    AudioFrameKind,
    FINAL_FLAG,
    RealtimeHub,
    _Session,
    _handle_audio,
    pack_audio_frame,
)
from backend.main_agent.tool_runtime import ToolSurfacePolicy


def tool_delta(index: int, call_id: str, name: str, arguments: str) -> dict:
    return {
        "choices": [{"delta": {"tool_calls": [{
            "index": index,
            "id": call_id,
            "function": {"name": name, "arguments": arguments},
        }]}}]
    }


class FakeSttTransport:
    def __init__(self, *, model_name, sample_rate_hz, on_event) -> None:
        self.model_name = model_name
        self.sample_rate_hz = sample_rate_hz
        self.on_event = on_event
        self.frames: list[bytes] = []
        self.started = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def send_audio_frame(self, frame: bytes) -> None:
        self.frames.append(frame)

    def stop(self) -> None:
        self.closed = True


class FakeTtsTransport:
    def __init__(self, *, model_name, voice) -> None:
        self.model_name = model_name
        self.voice = voice
        self.cancelled = False

    def synthesize(self, text: str):
        self.text = text
        return (b"first", b"second")

    def cancel(self) -> None:
        self.cancelled = True


class ProviderBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_dashscope_tts_yields_callback_audio_before_stream_completion(self) -> None:
        release = threading.Event()
        completed = threading.Event()

        class StreamingTransport:
            def stream(self, text, on_data, on_complete, _on_error) -> None:
                self.text = text
                on_data(b"first")
                release.wait(1)
                on_data(b"second")
                completed.set()
                on_complete()

            def cancel(self) -> None:
                release.set()

        transport = StreamingTransport()
        provider = DashScopeSpeechOutputProvider(
            "cosyvoice-v3-flash",
            voice="longanyang",
            transport_factory=lambda **_kwargs: transport,
        )
        stream = provider.synthesize("你好。")

        first = await asyncio.wait_for(anext(stream), 0.2)

        self.assertEqual(first, b"first")
        self.assertFalse(completed.is_set())
        release.set()
        self.assertEqual(await anext(stream), b"second")
        with self.assertRaises(StopAsyncIteration):
            await anext(stream)

    async def test_dashscope_stt_normalizes_private_sentence_events(self) -> None:
        transports: list[FakeSttTransport] = []
        updates: list[TranscriptUpdate] = []

        def factory(**kwargs):
            transport = FakeSttTransport(**kwargs)
            transports.append(transport)
            return transport

        provider = DashScopeStreamingSttProvider(
            "fun-asr-realtime", transport_factory=factory
        )
        session = await provider.start(updates.append)
        await session.send_audio(bytes(640))
        transports[0].on_event({
            "sentence": {"text": "我想查", "sentence_end": False},
            "stability": 0.7,
        })
        transports[0].on_event({
            "sentence": {"text": "我想查天气", "sentence_end": True},
        })
        await session.close()

        self.assertEqual(transports[0].model_name, "fun-asr-realtime")
        self.assertEqual(transports[0].sample_rate_hz, 16_000)
        self.assertEqual(transports[0].frames, [bytes(640)])
        self.assertTrue(transports[0].closed)
        self.assertEqual(updates, [
            TranscriptUpdate("我想查", "", "我想查", "partial", 0.7),
            TranscriptUpdate("我想查天气", "我想查天气", "", "sentence_end", None),
        ])
        self.assertFalse(hasattr(updates[1], "is_final"))

    async def test_stt_start_failure_stops_partial_transport_and_normalizes_error(self) -> None:
        class PartialTransport(FakeSttTransport):
            def start(self) -> None:
                self.started = True
                raise RuntimeError("socket opened, callback thread failed")

        transports: list[PartialTransport] = []

        def factory(**kwargs):
            transport = PartialTransport(**kwargs)
            transports.append(transport)
            return transport

        provider = DashScopeStreamingSttProvider(
            "fun-asr-realtime", transport_factory=factory
        )

        with self.assertRaisesRegex(
            SpeechProviderUnavailable, "DashScope STT failed to start"
        ):
            await provider.start(lambda _update: None)

        self.assertTrue(transports[0].started)
        self.assertTrue(transports[0].closed)

    async def test_stt_worker_thread_callback_is_marshaled_to_owner_loop(self) -> None:
        transports: list[FakeSttTransport] = []
        updates: list[TranscriptUpdate] = []
        sink_threads: list[int] = []
        callback_errors: list[BaseException] = []
        owner_thread = threading.get_ident()

        def factory(**kwargs):
            transport = FakeSttTransport(**kwargs)
            transports.append(transport)
            return transport

        provider = DashScopeStreamingSttProvider(
            "fun-asr-realtime", transport_factory=factory
        )

        def sink(update: TranscriptUpdate) -> None:
            sink_threads.append(threading.get_ident())
            updates.append(update)

        session = await provider.start(sink)

        def emit() -> None:
            try:
                transports[0].on_event({
                    "sentence": {"text": "线程转写", "sentence_end": True}
                })
            except BaseException as exc:
                callback_errors.append(exc)

        worker = threading.Thread(target=emit)
        worker.start()
        await asyncio.to_thread(worker.join)
        for _ in range(10):
            if updates:
                break
            await asyncio.sleep(0)

        self.assertEqual(callback_errors, [])
        self.assertEqual([item.full_text for item in updates], ["线程转写"])
        self.assertEqual(sink_threads, [owner_thread])
        await session.close()

    async def test_stt_close_rejects_late_provider_event(self) -> None:
        transports: list[FakeSttTransport] = []
        updates: list[TranscriptUpdate] = []

        def factory(**kwargs):
            transport = FakeSttTransport(**kwargs)
            transports.append(transport)
            return transport

        session = await DashScopeStreamingSttProvider(
            "fun-asr-realtime", transport_factory=factory
        ).start(updates.append)
        await session.close()
        transports[0].on_event({
            "sentence": {"text": "迟到内容", "sentence_end": True}
        })
        await asyncio.sleep(0)

        self.assertEqual(updates, [])

    async def test_stt_close_drains_raising_async_sink_without_loop_leak(self) -> None:
        transports: list[FakeSttTransport] = []
        leaked: list[dict] = []
        loop = asyncio.get_running_loop()
        previous = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: leaked.append(context))

        async def raising_sink(_update: TranscriptUpdate) -> None:
            raise RuntimeError("sink exploded")

        def factory(**kwargs):
            transport = FakeSttTransport(**kwargs)
            transports.append(transport)
            return transport

        try:
            session = await DashScopeStreamingSttProvider(
                "fun-asr-realtime", transport_factory=factory
            ).start(raising_sink)
            transports[0].on_event({
                "sentence": {"text": "会失败", "sentence_end": True}
            })
            await asyncio.sleep(0)
            await session.close()
            await asyncio.sleep(0)
        finally:
            loop.set_exception_handler(previous)

        self.assertEqual(leaked, [])

    async def test_dashscope_tts_uses_configured_model_and_yields_transport_audio(self) -> None:
        transports: list[FakeTtsTransport] = []

        def factory(**kwargs):
            transport = FakeTtsTransport(**kwargs)
            transports.append(transport)
            return transport

        provider = DashScopeSpeechOutputProvider(
            "cosyvoice-v3-flash", voice="longanyang", transport_factory=factory
        )

        chunks = [chunk async for chunk in provider.synthesize("你好。")]

        self.assertEqual(chunks, [b"first", b"second"])
        self.assertEqual(transports[0].model_name, "cosyvoice-v3-flash")
        self.assertEqual(transports[0].voice, "longanyang")
        self.assertEqual(transports[0].text, "你好。")


class FakeSessionOptions:
    def __init__(self) -> None:
        self.intra_op_num_threads = None
        self.inter_op_num_threads = None
        self.execution_mode = None
        self.graph_optimization_level = None


class FakeOrt:
    class ExecutionMode:
        ORT_SEQUENTIAL = "sequential"

    class GraphOptimizationLevel:
        ORT_ENABLE_ALL = "all"

    SessionOptions = FakeSessionOptions
    calls: list[tuple[str, FakeSessionOptions]] = []

    @classmethod
    def InferenceSession(cls, path, *, sess_options, providers):
        session = object()
        cls.calls.append((path, sess_options))
        return session


class LocalDetectorTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeOrt.calls.clear()

    def test_missing_assets_degrade_without_importing_optional_dependencies(self) -> None:
        detector = SileroVadDetector(
            Path("/missing/silero.onnx"), Path("/missing/LICENSE"),
            ort_loader=lambda: self.fail("optional dependency must stay lazy"),
        )

        status = detector.status()

        self.assertFalse(status.available)
        self.assertEqual(status.reason, "model_asset_missing")
        self.assertIsNone(detector.load_session())

    def test_silero_and_smart_turn_sessions_are_lazy_singletons_with_one_cpu_thread(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            silero_model = root / "silero.onnx"
            silero_license = root / "silero.LICENSE"
            smart_model = root / "smart-turn-v3.2.onnx"
            smart_license = root / "smart-turn.LICENSE"
            for path in (silero_model, smart_model):
                path.write_bytes(b"fixture")
            silero_license.write_text("MIT License", encoding="utf-8")
            smart_license.write_text("BSD 2-Clause License", encoding="utf-8")
            silero_a = SileroVadDetector(
                silero_model, silero_license, ort_loader=lambda: FakeOrt
            )
            silero_b = SileroVadDetector(
                silero_model, silero_license, ort_loader=lambda: FakeOrt
            )
            smart = SmartTurnDetector(
                smart_model, smart_license, ort_loader=lambda: FakeOrt
            )

            self.assertEqual(FakeOrt.calls, [])
            self.assertIs(silero_a.load_session(), silero_b.load_session())
            self.assertIsNotNone(smart.load_session())

        self.assertEqual(len(FakeOrt.calls), 2)
        for _path, options in FakeOrt.calls:
            self.assertEqual(options.intra_op_num_threads, 1)
            self.assertEqual(options.inter_op_num_threads, 1)
            self.assertEqual(options.execution_mode, "sequential")
            self.assertEqual(options.graph_optimization_level, "all")

    def test_silero_runs_known_16k_contract_with_per_stream_state_and_reset(self) -> None:
        class Session:
            def __init__(self) -> None:
                self.calls = []

            def run(self, _outputs, feeds):
                self.calls.append(feeds)
                return [
                    np.array([[0.75]], dtype=np.float32),
                    np.ones((2, 1, 128), dtype=np.float32),
                ]

        session = Session()

        class Ort(FakeOrt):
            @classmethod
            def InferenceSession(cls, *_args, **_kwargs):
                return session

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / "silero.onnx"
            license_path = root / "LICENSE"
            model.write_bytes(b"fixture")
            license_path.write_text("MIT License", encoding="utf-8")
            detector = SileroVadDetector(model, license_path, ort_loader=lambda: Ort)
            half_scale = int(16_384).to_bytes(2, "little", signed=True) * 320

            self.assertIsNone(detector.speech_probability(half_scale))
            self.assertAlmostEqual(detector.speech_probability(half_scale), 0.75)
            first = session.calls[0]
            self.assertEqual(set(first), {"input", "state", "sr"})
            self.assertEqual(first["input"].shape, (1, 576))
            self.assertEqual(first["input"].dtype, np.float32)
            np.testing.assert_array_equal(first["input"][0, :64], np.zeros(64, np.float32))
            np.testing.assert_allclose(first["input"][0, 64:], 0.5)
            self.assertEqual(first["state"].shape, (2, 1, 128))
            np.testing.assert_array_equal(first["state"], np.zeros((2, 1, 128), np.float32))
            self.assertEqual(first["sr"].dtype, np.int64)

            detector.speech_probability(half_scale)
            detector.speech_probability(half_scale)
            np.testing.assert_array_equal(
                session.calls[1]["state"], np.ones((2, 1, 128), np.float32)
            )
            detector.reset()
            detector.speech_probability(half_scale)
            detector.speech_probability(half_scale)
            np.testing.assert_array_equal(
                session.calls[2]["state"], np.zeros((2, 1, 128), np.float32)
            )

    def test_smart_turn_runs_v32_feature_contract_and_normalizes_probability(self) -> None:
        class Session:
            def __init__(self) -> None:
                self.feeds = []

            def run(self, _outputs, feeds):
                self.feeds.append(feeds)
                return [np.array([[0.625]], dtype=np.float32)]

        class Extractor:
            def __init__(self) -> None:
                self.calls = []

            def __call__(self, audio, **kwargs):
                self.calls.append((audio, kwargs))
                return types.SimpleNamespace(
                    input_features=np.ones((1, 80, 800), dtype=np.float64)
                )

        session = Session()
        extractor = Extractor()

        class Ort(FakeOrt):
            @classmethod
            def InferenceSession(cls, *_args, **_kwargs):
                return session

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / "smart-turn-v3.2.onnx"
            license_path = root / "LICENSE"
            model.write_bytes(b"fixture")
            license_path.write_text("BSD 2-Clause License", encoding="utf-8")
            detector = SmartTurnDetector(
                model, license_path, ort_loader=lambda: Ort,
                feature_extractor_factory=lambda: extractor,
            )
            audio = int(16_384).to_bytes(2, "little", signed=True) * 160

            self.assertAlmostEqual(detector.completion_probability(audio), 0.625)

        prepared, kwargs = extractor.calls[0]
        self.assertEqual(prepared.shape, (128_000,))
        np.testing.assert_array_equal(prepared[:-160], np.zeros(127_840, np.float32))
        np.testing.assert_allclose(prepared[-160:], 0.5)
        self.assertEqual(kwargs, {
            "sampling_rate": 16_000, "return_tensors": "np", "padding": "max_length",
            "max_length": 128_000, "truncation": True, "do_normalize": True,
        })
        self.assertEqual(set(session.feeds[0]), {"input_features"})
        self.assertEqual(session.feeds[0]["input_features"].shape, (1, 80, 800))
        self.assertEqual(session.feeds[0]["input_features"].dtype, np.float32)

    def test_detector_inference_failure_becomes_unavailable_capability(self) -> None:
        class Session:
            def run(self, _outputs, _feeds):
                raise RuntimeError("bad graph")

        class Ort(FakeOrt):
            @classmethod
            def InferenceSession(cls, *_args, **_kwargs):
                return Session()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / "silero.onnx"
            license_path = root / "LICENSE"
            model.write_bytes(b"fixture")
            license_path.write_text("MIT License", encoding="utf-8")
            detector = SileroVadDetector(model, license_path, ort_loader=lambda: Ort)
            frame = bytes(640)

            detector.speech_probability(frame)
            self.assertIsNone(detector.speech_probability(frame))
            self.assertEqual(detector.status().reason, "inference_failed")


class SpeechSettingsTests(unittest.TestCase):
    def test_settings_preserve_configured_provider_models_and_checked_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = MainAgentSettings.from_config({
                "app": {"data_dir": "./data", "workspaces": []},
                "main_agent": {
                    "speech": {
                        "enabled": True,
                        "stt_model_name": "fun-asr-realtime",
                        "tts_model_name": "cosyvoice-v3-flash",
                        "tts_voice": "longanyang",
                        "silero_model_path": "./models/silero.onnx",
                        "silero_license_path": "./licenses/silero.txt",
                        "smart_turn_model_path": "./models/smart-turn-v3.2.onnx",
                        "smart_turn_license_path": "./licenses/smart-turn.txt",
                    }
                },
            }, root / "config.yaml")

        self.assertTrue(settings.speech.enabled)
        self.assertEqual(settings.speech.stt_model_name, "fun-asr-realtime")
        self.assertEqual(settings.speech.tts_model_name, "cosyvoice-v3-flash")
        self.assertEqual(settings.speech.tts_voice, "longanyang")
        self.assertEqual(settings.speech.silero_model_path, (root / "models/silero.onnx").resolve())
        self.assertEqual(
            settings.speech.smart_turn_license_path,
            (root / "licenses/smart-turn.txt").resolve(),
        )

    def test_speech_is_disabled_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = MainAgentSettings.from_config({
                "app": {"data_dir": "./data", "workspaces": []},
                "main_agent": {},
            }, Path(directory) / "config.yaml")

        self.assertFalse(settings.speech.enabled)


class FakeSttSession:
    def __init__(self) -> None:
        self.frames: list[bytes] = []
        self.closed = False

    async def send_audio(self, frame: bytes) -> None:
        self.frames.append(frame)

    async def close(self) -> None:
        self.closed = True


class FakeSttProvider:
    def __init__(self) -> None:
        self.sessions: list[FakeSttSession] = []
        self.sinks = []

    async def start(self, sink):
        session = FakeSttSession()
        self.sessions.append(session)
        self.sinks.append(sink)
        return session


class FakeVad:
    def __init__(self, *probabilities: float) -> None:
        self.probabilities = iter(probabilities)

    def speech_probability(self, _frame: bytes) -> float:
        return next(self.probabilities)


class FakeEou:
    def __init__(self, *probabilities: float) -> None:
        self.probabilities = iter(probabilities)

    def completion_probability(self, _audio: bytes) -> float:
        return next(self.probabilities)


class FakeSpeechRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.transcript_segment_ids: list[str | None] = []

    async def vad_start(self) -> None:
        self.calls.append(("vad_start",))

    async def confirm_speech(self) -> None:
        self.calls.append(("confirmed",))

    async def vad_stop_candidate(self) -> None:
        self.calls.append(("vad_stop_candidate",))

    async def reject_speech_candidate(self) -> None:
        self.calls.append(("rejected",))

    async def update_transcript(
        self,
        text: str,
        *,
        segment_id: str | None = None,
        request_prepare: bool = True,
    ) -> None:
        self.transcript_segment_ids.append(segment_id)
        self.calls.append(("transcript", text, request_prepare))

    async def eou_incomplete(self) -> bool:
        self.calls.append(("eou_incomplete",))
        return True

    async def eou_complete(self) -> None:
        self.calls.append(("eou_complete",))

    async def endpoint_fallback(self) -> None:
        self.calls.append(("endpoint_fallback",))


class FakePlaybackLifecycle:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def speech_candidate_started(self) -> None:
        self.calls.append("candidate")

    async def speech_candidate_rejected(self) -> None:
        self.calls.append("rejected")

    async def speech_confirmed(self) -> None:
        self.calls.append("confirmed")

    async def speech_endpoint(self, *, complete: bool) -> None:
        self.calls.append("complete" if complete else "incomplete")


class ControlledClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.waits: list[tuple[float, object]] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        import asyncio

        gate = asyncio.Event()
        self.waits.append((delay, gate))
        await gate.wait()
        self.now += delay

    async def release(self) -> float:
        import asyncio

        while not self.waits:
            await asyncio.sleep(0)
        delay, gate = self.waits.pop(0)
        gate.set()
        await asyncio.sleep(0)
        return delay


class SpeechInputTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        coordinator = getattr(self, "coordinator", None)
        if coordinator is not None:
            await coordinator.close()

    async def test_vad_start_activates_stt_with_preroll_then_streams_every_frame(self) -> None:
        runtime = FakeSpeechRuntime()
        stt = FakeSttProvider()
        self.coordinator = SpeechInputCoordinator(
            runtime, stt_provider=stt, vad=FakeVad(0.0, 0.0, 0.9, 0.9),
            pre_roll_frames=3,
        )
        frames = [bytes([index]) * 640 for index in range(1, 5)]

        for frame in frames:
            await self.coordinator.accept_mic_frame(frame)

        self.assertEqual(runtime.calls[0], ("vad_start",))
        self.assertEqual(stt.sessions[0].frames, frames)

    async def test_first_nonempty_stt_confirms_barge_in_but_only_stable_text_requests_prepare(self) -> None:
        runtime = FakeSpeechRuntime()
        stt = FakeSttProvider()
        self.coordinator = SpeechInputCoordinator(
            runtime, stt_provider=stt, vad=FakeVad(0.9), pre_roll_frames=1,
        )
        await self.coordinator.accept_mic_frame(bytes(640))

        await stt.sinks[0](TranscriptUpdate("你好", "", "你好", "partial"))
        await stt.sinks[0](TranscriptUpdate("你好呀", "你好呀", "", "sentence_end"))

        self.assertEqual(runtime.calls, [
            ("vad_start",),
            ("confirmed",),
            ("transcript", "你好", False),
            ("transcript", "你好呀", True),
        ])
        self.assertEqual(len(set(runtime.transcript_segment_ids)), 1)
        self.assertIsInstance(runtime.transcript_segment_ids[0], str)

    async def test_new_stt_session_gets_a_new_stable_segment_id(self) -> None:
        runtime = FakeSpeechRuntime()
        stt = FakeSttProvider()
        self.coordinator = SpeechInputCoordinator(
            runtime,
            stt_provider=stt,
            vad=FakeVad(0.9, 0.9),
            pre_roll_frames=1,
        )

        await self.coordinator.accept_mic_frame(bytes(640))
        await stt.sinks[0](TranscriptUpdate("第一段", "第一段", "", "stable"))
        await self.coordinator._finish_turn()
        await self.coordinator.accept_mic_frame(bytes(640))
        await stt.sinks[1](TranscriptUpdate("第二段", "第二段", "", "stable"))

        self.assertEqual(len(runtime.transcript_segment_ids), 2)
        self.assertNotEqual(
            runtime.transcript_segment_ids[0], runtime.transcript_segment_ids[1]
        )

    async def test_authoritative_playback_lifecycle_tracks_vad_and_complete_eou(self) -> None:
        runtime = FakeSpeechRuntime()
        lifecycle = FakePlaybackLifecycle()
        stt = FakeSttProvider()
        probabilities = [0.9] * 8 + [0.0] * 15
        self.coordinator = SpeechInputCoordinator(
            runtime,
            stt_provider=stt,
            vad=FakeVad(*probabilities),
            eou=FakeEou(0.9),
            playback_lifecycle=lifecycle,
        )

        for index, _ in enumerate(probabilities):
            await self.coordinator.accept_mic_frame(bytes(640))
            if index == 0:
                await stt.sinks[0](TranscriptUpdate("完成", "完成", "", "stable"))
        await self.coordinator.wait_for_endpoint()

        self.assertEqual(
            lifecycle.calls,
            ["candidate", "confirmed", "complete"],
        )

    async def test_short_noise_is_rejected_and_never_reaches_endpoint(self) -> None:
        runtime = FakeSpeechRuntime()
        probabilities = [0.9, 0.9] + [0.0] * 15
        self.coordinator = SpeechInputCoordinator(
            runtime, vad=FakeVad(*probabilities), speech_confirm_ms=160,
        )

        for _ in probabilities:
            await self.coordinator.accept_mic_frame(bytes(640))

        self.assertEqual(runtime.calls, [
            ("vad_start",), ("vad_stop_candidate",), ("rejected",),
        ])

    async def test_confirmed_audio_without_any_transcript_never_forges_response(self) -> None:
        runtime = FakeSpeechRuntime()
        runtime.active_input = None
        probabilities = [0.9] * 8 + [0.0] * 15
        self.coordinator = SpeechInputCoordinator(
            runtime, vad=FakeVad(*probabilities), eou=FakeEou(0.9),
        )

        for _ in probabilities:
            await self.coordinator.accept_mic_frame(bytes(640))
        await self.coordinator.wait_for_endpoint()

        self.assertNotIn(("eou_complete",), runtime.calls)
        self.assertNotIn(("endpoint_fallback",), runtime.calls)
        self.assertEqual(runtime.calls[-1], ("rejected",))

    async def test_stt_send_and_stop_failures_degrade_without_escaping_turn_cleanup(self) -> None:
        class BrokenSession(FakeSttSession):
            async def send_audio(self, _frame: bytes) -> None:
                raise RuntimeError("send failed")

            async def close(self) -> None:
                self.closed = True
                raise RuntimeError("stop failed")

        class BrokenProvider:
            def __init__(self) -> None:
                self.session = BrokenSession()

            async def start(self, _sink):
                return self.session

        runtime = FakeSpeechRuntime()
        provider = BrokenProvider()
        self.coordinator = SpeechInputCoordinator(
            runtime, stt_provider=provider, vad=FakeVad(0.9), pre_roll_frames=1,
        )

        await self.coordinator.accept_mic_frame(bytes(640))
        await self.coordinator.close()

        self.assertTrue(provider.session.closed)

    async def test_detector_inference_exception_degrades_without_breaking_mic_consumer(self) -> None:
        class BrokenVad:
            def speech_probability(self, _frame):
                raise RuntimeError("inference failed")

        runtime = FakeSpeechRuntime()
        self.coordinator = SpeechInputCoordinator(runtime, vad=BrokenVad())

        await self.coordinator.accept_mic_frame(bytes(640))

        self.assertEqual(runtime.calls, [])

    async def test_active_turn_vad_failure_closes_and_resets_before_next_turn(self) -> None:
        class ResettableVad:
            def __init__(self) -> None:
                self.first = True
                self.failed = False
                self.resets = 0

            def speech_probability(self, _frame: bytes) -> float:
                if self.first:
                    self.first = False
                    return 0.9
                if not self.failed:
                    self.failed = True
                    raise RuntimeError("VAD inference failed")
                return 0.9

            def reset(self) -> None:
                self.resets += 1

        runtime = FakeSpeechRuntime()
        stt = FakeSttProvider()
        vad = ResettableVad()
        self.coordinator = SpeechInputCoordinator(
            runtime, stt_provider=stt, vad=vad, pre_roll_frames=1
        )

        await self.coordinator.accept_mic_frame(bytes(640))
        await self.coordinator.accept_mic_frame(bytes(640))

        self.assertTrue(stt.sessions[0].closed)
        self.assertFalse(self.coordinator._active)
        self.assertEqual(runtime.calls[-1], ("rejected",))
        self.assertEqual(vad.resets, 1)

        await self.coordinator.accept_mic_frame(bytes(640))
        self.assertEqual(len(stt.sessions), 2)
        self.assertTrue(self.coordinator._active)

    async def test_eou_inference_timeout_uses_total_700ms_fallback_deadline(self) -> None:
        class HangingEou:
            async def completion_probability(self, _audio):
                await asyncio.Event().wait()

        runtime = FakeSpeechRuntime()
        stt = FakeSttProvider()
        probabilities = [0.9] * 8 + [0.0] * 15
        self.coordinator = SpeechInputCoordinator(
            runtime, stt_provider=stt, vad=FakeVad(*probabilities), eou=HangingEou(),
            eou_inference_timeout_seconds=0.01, unavailable_fallback_seconds=0.02,
        )
        for index, _ in enumerate(probabilities):
            await self.coordinator.accept_mic_frame(bytes(640))
            if index == 0:
                await stt.sinks[0](TranscriptUpdate("有内容", "有内容", "", "stable"))

        await asyncio.wait_for(self.coordinator.wait_for_endpoint(), 0.1)

        self.assertEqual(runtime.calls[-1], ("endpoint_fallback",))

    async def test_eou_complete_at_threshold_is_the_only_path_to_strong_pause(self) -> None:
        runtime = FakeSpeechRuntime()
        stt = FakeSttProvider()
        probabilities = [0.9] * 8 + [0.0] * 15
        self.coordinator = SpeechInputCoordinator(
            runtime, stt_provider=stt, vad=FakeVad(*probabilities), eou=FakeEou(0.5),
        )

        for index, _ in enumerate(probabilities):
            await self.coordinator.accept_mic_frame(bytes(640))
            if index == 0:
                await stt.sinks[0](TranscriptUpdate("用户内容", "", "用户内容", "partial"))
        await self.coordinator.wait_for_endpoint()

        self.assertIn(("vad_stop_candidate",), runtime.calls)
        self.assertIn(("eou_complete",), runtime.calls)
        self.assertNotIn(("endpoint_fallback",), runtime.calls)

    async def test_incomplete_rechecks_at_500ms_and_hard_falls_back_at_1800ms(self) -> None:
        runtime = FakeSpeechRuntime()
        clock = ControlledClock()
        stt = FakeSttProvider()
        probabilities = [0.9] * 8 + [0.0] * 15
        self.coordinator = SpeechInputCoordinator(
            runtime, stt_provider=stt, vad=FakeVad(*probabilities),
            eou=FakeEou(0.1, 0.2, 0.3, 0.4),
            clock=clock, sleep=clock.sleep,
        )
        for index, _ in enumerate(probabilities):
            await self.coordinator.accept_mic_frame(bytes(640))
            if index == 0:
                await stt.sinks[0](TranscriptUpdate("用户内容", "", "用户内容", "partial"))
        await clock.release()
        await clock.release()
        await clock.release()
        final_delay = await clock.release()
        await self.coordinator.wait_for_endpoint()

        self.assertEqual(final_delay, 0.3)
        self.assertEqual(runtime.calls.count(("eou_incomplete",)), 1)
        self.assertNotIn(("eou_complete",), runtime.calls)
        self.assertEqual(runtime.calls[-1], ("endpoint_fallback",))

    async def test_eou_unavailable_skips_prepare_and_falls_back_after_700ms(self) -> None:
        runtime = FakeSpeechRuntime()
        clock = ControlledClock()
        stt = FakeSttProvider()
        probabilities = [0.9] * 8 + [0.0] * 15
        self.coordinator = SpeechInputCoordinator(
            runtime, stt_provider=stt, vad=FakeVad(*probabilities), eou=None,
            clock=clock, sleep=clock.sleep,
        )
        for index, _ in enumerate(probabilities):
            await self.coordinator.accept_mic_frame(bytes(640))
            if index == 0:
                await stt.sinks[0](TranscriptUpdate("用户内容", "", "用户内容", "partial"))

        delay = await clock.release()
        await self.coordinator.wait_for_endpoint()

        self.assertEqual(delay, 0.7)
        self.assertNotIn(("eou_incomplete",), runtime.calls)
        self.assertEqual(runtime.calls[-1], ("endpoint_fallback",))


class FakeSpeechOutput:
    def __init__(self, *, gate: asyncio.Event | None = None) -> None:
        self.texts: list[str] = []
        self.cancelled = False
        self.gate = gate

    async def synthesize(self, text: str):
        self.texts.append(text)
        yield bytes([len(self.texts)]) * 960
        if self.gate is not None:
            await self.gate.wait()

    async def cancel(self) -> None:
        self.cancelled = True


class FakeRendererLease:
    def __init__(self) -> None:
        self.offers = []
        self.frames = []
        self.cancelled = []

    async def offer_clause(self, clause):
        self.offers.append(clause)
        return None

    async def queue_tts(self, frame):
        self.frames.append(frame)
        return None

    async def cancel_clause(self, clause_id: str, reason: str):
        self.cancelled.append((clause_id, reason))


def renderer_clause(clause_id: str, text: str):
    return types.SimpleNamespace(
        clause_id=clause_id, text=text, invoke_id="invoke", input_id="input",
        revision=1, motion_token=None, expression_token=None,
    )


class ClauseSpeechDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_queue_and_first_renderer_frame_drive_distinct_ledger_callbacks(self) -> None:
        release = asyncio.Event()

        class GatedOutput(FakeSpeechOutput):
            async def synthesize(self, text: str):
                self.texts.append(text)
                await release.wait()
                yield bytes(960)

        transitions: list[tuple[str, str]] = []
        lease = FakeRendererLease()
        delivery = ClauseSpeechDelivery(
            GatedOutput(),
            lambda: lease,
            mark_queued=lambda clause_id: transitions.append((clause_id, "queued")),
            mark_playing=lambda clause_id: transitions.append((clause_id, "playing")),
        )

        await delivery(renderer_clause("physical", "按阶段播放"))
        await asyncio.sleep(0)
        self.assertEqual(transitions, [("physical", "queued")])

        release.set()
        while not lease.frames:
            await asyncio.sleep(0)
        self.assertEqual(transitions, [
            ("physical", "queued"),
            ("physical", "playing"),
        ])
        self.assertTrue(delivery.acknowledge("physical", complete=True))
        await delivery.shutdown()

    async def test_enqueue_returns_before_renderer_ack_and_worker_keeps_ownership(self) -> None:
        output = FakeSpeechOutput()
        lease = FakeRendererLease()
        delivery = ClauseSpeechDelivery(output, lambda: lease)

        accepted = asyncio.create_task(delivery(renderer_clause("owned", "立即返回。")))
        while not lease.frames:
            await asyncio.sleep(0)
        try:
            self.assertIsNone(await asyncio.wait_for(asyncio.shield(accepted), 0.05))
            self.assertEqual(lease.cancelled, [])
            self.assertTrue(delivery.acknowledge("owned", complete=True))
        finally:
            if not accepted.done():
                delivery.acknowledge("owned", complete=False)
                await accepted
            shutdown = getattr(delivery, "shutdown", None)
            if callable(shutdown):
                await shutdown()

    async def test_clauses_synthesize_in_order_and_next_waits_for_full_ack(self) -> None:
        output = FakeSpeechOutput()
        lease = FakeRendererLease()
        delivery = ClauseSpeechDelivery(output, lambda: lease)

        await delivery(renderer_clause("one", "第一句。"))
        while len(lease.frames) < 1:
            await asyncio.sleep(0)
        await delivery(renderer_clause("two", "第二句。"))
        await asyncio.sleep(0)

        self.assertEqual(output.texts, ["第一句。"])
        self.assertTrue(delivery.acknowledge("one", complete=True))
        while len(output.texts) < 2:
            await asyncio.sleep(0)
        self.assertEqual(output.texts, ["第一句。", "第二句。"])
        self.assertTrue(delivery.acknowledge("two", complete=True))
        await asyncio.sleep(0)
        self.assertEqual(sum(bool(frame.flags & FINAL_FLAG) for frame in lease.frames), 2)
        await delivery.shutdown()


class SpeechPipelineLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_pipeline_consumes_hub_mic_frames_and_shuts_down_owned_components(self) -> None:
        class Coordinator:
            def __init__(self) -> None:
                self.frames: list[bytes] = []
                self.closed = False

            async def accept_mic_frame(self, frame: bytes) -> None:
                self.frames.append(frame)

            async def close(self) -> None:
                self.closed = True

        class Delivery:
            def __init__(self) -> None:
                self.started = False
                self.stopped = False

            async def startup(self) -> None:
                self.started = True

            async def shutdown(self) -> None:
                self.stopped = True

        hub = RealtimeHub()
        coordinator = Coordinator()
        delivery = Delivery()
        pipeline = SpeechPipeline(
            hub, coordinator=coordinator, delivery=delivery, enabled=True
        )

        await pipeline.startup()
        hub.mic_queue.put(AudioFrame(AudioFrameKind.MIC, 0, 7, 0, 0, bytes(640)))
        for _ in range(20):
            if coordinator.frames:
                break
            await asyncio.sleep(0)
        await pipeline.shutdown()

        self.assertEqual(coordinator.frames, [bytes(640)])
        self.assertTrue(coordinator.closed)
        self.assertTrue(delivery.started)
        self.assertTrue(delivery.stopped)
        self.assertEqual(pipeline.status.reason, "stopped")

    async def test_disabled_pipeline_starts_without_workers_or_optional_dependencies(self) -> None:
        pipeline = SpeechPipeline.disabled(RealtimeHub())

        await pipeline.startup()

        self.assertFalse(pipeline.status.enabled)
        self.assertEqual(pipeline.status.reason, "speech_disabled")
        self.assertEqual(pipeline.live_tasks, ())
        await pipeline.shutdown()

    async def test_aur1_mic_to_real_runtime_strong_pause_tts_and_ack(self) -> None:
        class AutoTranscriptSession:
            def __init__(self, sink) -> None:
                self.sink = sink
                self.sent = 0

            async def send_audio(self, _frame: bytes) -> None:
                self.sent += 1
                if self.sent == 1:
                    await self.sink(TranscriptUpdate(
                        "测试语音", "测试语音", "", "sentence_end"
                    ))

            async def close(self) -> None:
                return None

        class Stt:
            async def start(self, sink):
                return AutoTranscriptSession(sink)

        class Vad:
            def __init__(self) -> None:
                self.values = deque([0.9] * 8 + [0.0] * 15)

            def speech_probability(self, _frame: bytes) -> float:
                return self.values.popleft()

            def reset(self) -> None:
                return None

        class Eou:
            def completion_probability(self, _audio: bytes) -> float:
                return 0.8

        class Output:
            async def synthesize(self, _text: str):
                yield bytes(960)

            async def cancel(self) -> None:
                return None

        class Provider:
            def __init__(self) -> None:
                self.requests = []

            def __call__(self, request):
                self.requests.append(request)
                return [tool_delta(
                    0, "say", "say_to_user", '{"text":"已收到。"}'
                )]

        class Socket:
            async def send_json(self, _value) -> None:
                return None

            async def send_bytes(self, _value) -> None:
                return None

        with tempfile.TemporaryDirectory() as directory:
            store = ContextStore(Path(directory) / "speech-integration.sqlite3")
            provider = Provider()
            runtime = MainAgentRuntime(
                context_store=store,
                snapshot_projector=SnapshotProjector(),
                tool_policy=ToolSurfacePolicy(workspace_ids=("repo",)),
                provider=provider,
                renderer=lambda _clause: None,
                executors={},
                soul="speech integration",
            )
            hub = RealtimeHub(runtime=runtime)
            pipeline = SpeechPipeline.compose(
                hub,
                runtime,
                stt_provider=Stt(),
                output_provider=Output(),
                vad=Vad(),
                eou=Eou(),
            )
            session = _Session(Socket(), hub)
            await hub.claim(session)
            session.opened = True
            session.ready = True
            session.mic_active = True
            await pipeline.startup()
            try:
                for sequence in range(23):
                    await _handle_audio(session, pack_audio_frame(AudioFrame(
                        AudioFrameKind.MIC,
                        0,
                        9,
                        sequence,
                        sequence * 320,
                        bytes(640),
                    )))
                await pipeline.coordinator.wait_for_endpoint()
                await runtime.wait_until_idle()
                for _ in range(100):
                    if session.offered_clauses:
                        break
                    await asyncio.sleep(0)

                self.assertEqual(len(provider.requests), 1)
                self.assertEqual(store.fold().transcript, "测试语音")
                self.assertEqual(store.fold().delivered_clauses, ())
                self.assertEqual(len(session.offered_clauses), 1)
                token = next(iter(session.offered_clauses.values()))

                self.assertTrue(
                    await hub._settle_clause_offer(session, token, complete=True)
                )
                for _ in range(20):
                    if store.fold().delivered_clauses:
                        break
                    await asyncio.sleep(0)
                self.assertEqual(store.fold().delivered_clauses, ("已收到。",))
            finally:
                await hub.release(session)
                await pipeline.shutdown()
                store.close()


class ClauseSpeechDeliverySettlementTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_ack_is_interrupted_and_never_reported_complete(self) -> None:
        output = FakeSpeechOutput()
        lease = FakeRendererLease()
        delivery = ClauseSpeechDelivery(output, lambda: lease)
        await delivery(renderer_clause("partial", "半句"))
        while not lease.frames:
            await asyncio.sleep(0)

        self.assertTrue(delivery.acknowledge("partial", complete=False))
        await delivery(renderer_clause("after", "下一句"))
        while len(output.texts) < 2:
            await asyncio.sleep(0)

        self.assertEqual(output.texts, ["半句", "下一句"])
        self.assertTrue(delivery.acknowledge("after", complete=True))
        await delivery.shutdown()

    async def test_cancellation_stops_tts_and_cancels_unplayed_clause(self) -> None:
        gate = asyncio.Event()
        output = FakeSpeechOutput(gate=gate)
        lease = FakeRendererLease()
        delivery = ClauseSpeechDelivery(output, lambda: lease)
        await delivery(renderer_clause("cancelled", "未播完"))
        while not lease.frames:
            await asyncio.sleep(0)

        await delivery.cancel_current()

        self.assertTrue(output.cancelled)
        self.assertEqual(lease.cancelled, [("cancelled", "confirmed_barge_in")])
        await delivery.shutdown()

    async def test_confirmed_cancel_drains_same_generation_without_playing_second_clause(self) -> None:
        gate = asyncio.Event()
        output = FakeSpeechOutput(gate=gate)
        lease = FakeRendererLease()
        with tempfile.TemporaryDirectory() as directory:
            store = ContextStore(Path(directory) / "cancel-generation.sqlite3")
            for clause_id, text in (("first", "第一句"), ("second", "第二句")):
                store.create_clause(clause_id, text)
                store.transition_clause(clause_id, ClauseState.TTS_QUEUED)
                store.transition_clause(clause_id, ClauseState.PLAYING)
            delivery = ClauseSpeechDelivery(
                output, lambda: lease, settle_clause=store.ack_clause
            )
            try:
                await delivery(renderer_clause("first", "第一句"))
                while not lease.frames:
                    await asyncio.sleep(0)
                await delivery(renderer_clause("second", "第二句"))

                await delivery.cancel_current()

                self.assertEqual(output.texts, ["第一句"])
                self.assertEqual([item.clause_id for item in lease.offers], ["first"])
                self.assertEqual(store.get_clause("first").state, ClauseState.INTERRUPTED)
                self.assertEqual(store.get_clause("second").state, ClauseState.INTERRUPTED)
            finally:
                await delivery.shutdown()
                store.close()

    async def test_renderer_cancel_error_settles_job_and_worker_accepts_next_clause(self) -> None:
        class FirstBlockingOutput(FakeSpeechOutput):
            async def synthesize(self, text: str):
                self.texts.append(text)
                yield bytes(960)
                if len(self.texts) == 1:
                    await gate.wait()

        class BrokenCancelLease(FakeRendererLease):
            async def cancel_clause(self, clause_id: str, reason: str):
                await super().cancel_clause(clause_id, reason)
                raise RuntimeError("renderer disconnected during cancel")

        gate = asyncio.Event()
        output = FirstBlockingOutput()
        lease = BrokenCancelLease()
        with tempfile.TemporaryDirectory() as directory:
            store = ContextStore(Path(directory) / "cancel-error.sqlite3")
            for clause_id, text in (("broken", "中断句"), ("next", "下一句")):
                store.create_clause(clause_id, text)
                store.transition_clause(clause_id, ClauseState.TTS_QUEUED)
                store.transition_clause(clause_id, ClauseState.PLAYING)
            delivery = ClauseSpeechDelivery(
                output, lambda: lease, settle_clause=store.ack_clause
            )
            try:
                await delivery(renderer_clause("broken", "中断句"))
                while not lease.frames:
                    await asyncio.sleep(0)
                await delivery.cancel_current()

                self.assertEqual(store.get_clause("broken").state, ClauseState.INTERRUPTED)
                self.assertIsNotNone(delivery._worker)
                self.assertFalse(delivery._worker.done())

                await delivery(renderer_clause("next", "下一句"))
                while len(output.texts) < 2:
                    await asyncio.sleep(0)
                self.assertTrue(store.ack_clause("next", complete=True))
                self.assertTrue(delivery.acknowledge("next", complete=True))
                await asyncio.sleep(0)
                self.assertEqual(store.get_clause("next").state, ClauseState.DELIVERED)
            finally:
                await delivery.shutdown()
                store.close()


if __name__ == "__main__":
    unittest.main()
