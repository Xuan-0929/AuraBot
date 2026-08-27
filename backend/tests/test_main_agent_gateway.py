"""Verify Task-9 Backend composition, local readiness, bounded metrics, and shutdown ownership.

The tests inject lifecycle-only fakes so FastAPI startup and static routes never open model,
embedding, Qdrant, speech-provider, or Pi subprocess connections.  They preserve the exact legacy
``/health`` body while checking that readiness is an independent local-state view and shutdown first
fences realtime/invoke admission before closing Memory and finally TaskManager.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

os.environ.setdefault(
    "AURABOT_CONFIG_PATH",
    str(Path(__file__).parent / "fixtures" / "runtime_config.yaml"),
)

from backend.main import create_app
from backend.main_agent.metrics import LifecycleMetrics
from backend.main_agent.realtime import RealtimeHub
from backend.main_agent.settings import SpeechSettings
from backend.main_agent.speech import SpeechPipeline


class LifecycleFake:
    def __init__(self, name: str, calls: list[str]) -> None:
        self.name = name
        self.calls = calls
        self.started = False
        self.closed = False

    async def startup(self) -> None:
        self.calls.append(f"start:{self.name}")
        self.started = True

    async def shutdown(self) -> None:
        self.calls.append(f"stop:{self.name}")
        self.closed = True


class HubFake:
    allowed_origins = frozenset()

    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.runtime = None
        self.accepting = False

    def start_accepting(self) -> None:
        self.calls.append("start:realtime")
        self.accepting = True

    async def stop_accepting(self) -> None:
        self.calls.append("stop:realtime")
        self.accepting = False


class SpeechFake(LifecycleFake):
    def __init__(self, hub: HubFake, calls: list[str]) -> None:
        super().__init__("speech", calls)
        self.hub = hub
        self.status = type("Status", (), {"enabled": False, "reason": "fake"})()


class CompositionAndReadinessTests(unittest.TestCase):
    def test_lifespan_order_readiness_and_exact_health_are_independent(self) -> None:
        calls: list[str] = []
        manager = LifecycleFake("task", calls)
        memory = LifecycleFake("memory", calls)
        worker = LifecycleFake("worker", calls)
        runtime = LifecycleFake("runtime", calls)
        hub = HubFake(calls)
        speech = SpeechFake(hub, calls)
        app = create_app(
            task_manager=manager,
            realtime_hub=hub,
            speech_pipeline=speech,
            main_agent_runtime=runtime,
            memory_bridge=memory,
            memory_worker=worker,
        )

        with TestClient(app) as client:
            self.assertEqual(client.get("/health").json(), {"ok": True, "service": "backend"})
            ready = client.get("/api/v1/readiness")
            self.assertEqual(ready.status_code, 200)
            self.assertTrue(ready.json()["ready"])
            self.assertEqual(
                calls,
                ["start:task", "start:worker", "start:memory", "start:runtime", "start:realtime", "start:speech"],
            )

        self.assertEqual(
            calls[-6:],
            ["stop:realtime", "stop:speech", "stop:runtime", "stop:memory", "stop:worker", "stop:task"],
        )
        with TestClient(app, raise_server_exceptions=False) as client:
            # A fake owner that refuses restart degrades readiness, but health stays exact/independent.
            health = client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json(), {"ok": True, "service": "backend"})

    def test_readiness_never_calls_paid_or_external_health_methods(self) -> None:
        calls: list[str] = []

        class Memory(LifecycleFake):
            def health(self):
                raise AssertionError("external health probe must not run")

        manager = LifecycleFake("task", calls)
        memory = Memory("memory", calls)
        worker = LifecycleFake("worker", calls)
        runtime = LifecycleFake("runtime", calls)
        hub = HubFake(calls)
        speech = SpeechFake(hub, calls)
        app = create_app(
            task_manager=manager, realtime_hub=hub, speech_pipeline=speech,
            main_agent_runtime=runtime, memory_bridge=memory, memory_worker=worker,
        )
        with TestClient(app) as client:
            response = client.get("/api/v1/readiness")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ready"])


class MetricsTests(unittest.TestCase):
    def test_counters_and_timings_are_structured_and_hard_bounded(self) -> None:
        metrics = LifecycleMetrics(max_series=3, max_samples=4)
        for index in range(20):
            metrics.increment(f"counter.{index % 5}")
            metrics.observe(f"latency.{index % 5}", index / 1000)

        snapshot = metrics.snapshot()

        self.assertLessEqual(len(snapshot["counters"]), 3)
        self.assertLessEqual(len(snapshot["timings"]), 3)
        self.assertTrue(all(item["samples"] <= 4 for item in snapshot["timings"].values()))
        self.assertNotIn("content", repr(snapshot).lower())


class ProductionSpeechCompositionTests(unittest.TestCase):
    def _owners(self, hub):
        calls: list[str] = []
        return (
            calls,
            LifecycleFake("task", calls),
            LifecycleFake("worker", calls),
            LifecycleFake("memory", calls),
            LifecycleFake("runtime", calls),
            hub,
        )

    def _speech_settings(self, root: Path) -> SpeechSettings:
        model = root / "model.onnx"
        model.write_bytes(b"local-test-model")
        silero_license = root / "silero.txt"
        silero_license.write_text("MIT License", encoding="utf-8")
        smart_license = root / "smart.txt"
        smart_license.write_text("BSD 2-Clause", encoding="utf-8")
        return SpeechSettings(
            enabled=True,
            api_key="test-key",
            stt_model_name="fun-asr-realtime",
            tts_model_name="cosyvoice-v3-flash",
            tts_voice="longanyang",
            silero_model_path=model,
            silero_license_path=silero_license,
            smart_turn_model_path=model,
            smart_turn_license_path=smart_license,
        )

    def test_disabled_speech_never_calls_the_provider_factory(self) -> None:
        hub = RealtimeHub(allowed_origins=set())
        calls, manager, worker, memory, runtime, _ = self._owners(hub)
        factory_calls = 0

        def speech_factory(*_args):
            nonlocal factory_calls
            factory_calls += 1
            raise AssertionError("disabled speech must not construct providers")

        app = create_app(
            task_manager=manager,
            realtime_hub=hub,
            main_agent_runtime=runtime,
            memory_bridge=memory,
            memory_worker=worker,
            speech_factory=speech_factory,
        )
        with TestClient(app):
            self.assertEqual(factory_calls, 0)
            self.assertFalse(app.state.main_agent_speech.status.enabled)

    def test_enabled_speech_factory_composes_and_starts_the_mic_consumer(self) -> None:
        hub = RealtimeHub(allowed_origins=set())
        calls, manager, worker, memory, runtime, _ = self._owners(hub)

        class Coordinator:
            async def accept_mic_frame(self, _frame: bytes) -> None:
                return None

            async def close(self) -> None:
                return None

        class Delivery:
            async def startup(self) -> None:
                return None

            async def shutdown(self) -> None:
                return None

        def speech_factory(_settings, factory_hub, _runtime):
            return SpeechPipeline(
                factory_hub,
                coordinator=Coordinator(),
                delivery=Delivery(),
                enabled=True,
            )

        enabled = SimpleNamespace(speech=SimpleNamespace(enabled=True))
        with patch("backend.main.MainAgentSettings.from_config", return_value=enabled):
            app = create_app(
                task_manager=manager,
                realtime_hub=hub,
                main_agent_runtime=runtime,
                memory_bridge=memory,
                memory_worker=worker,
                speech_factory=speech_factory,
            )
            with TestClient(app):
                self.assertTrue(app.state.main_agent_speech.status.enabled)
                self.assertEqual(len(app.state.main_agent_speech.live_tasks), 1)

    def test_enabled_default_speech_composition_is_local_and_starts_mic_consumer(self) -> None:
        hub = RealtimeHub(allowed_origins=set())
        calls, manager, worker, memory, runtime, _ = self._owners(hub)
        runtime.renderer_ack = lambda *_args, **_kwargs: True
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            enabled = SimpleNamespace(speech=self._speech_settings(root))
            with (
                patch("backend.main.MainAgentSettings.from_config", return_value=enabled),
                patch("importlib.util.find_spec", return_value=object()),
            ):
                app = create_app(
                    task_manager=manager,
                    realtime_hub=hub,
                    main_agent_runtime=runtime,
                    memory_bridge=memory,
                    memory_worker=worker,
                )
                with TestClient(app):
                    pipeline = app.state.main_agent_speech
                    self.assertTrue(pipeline.status.enabled)
                    self.assertIn(
                        "main-agent-speech-mic",
                        {task.get_name() for task in pipeline.live_tasks},
                    )

    def test_missing_optional_speech_module_disables_before_provider_construction(self) -> None:
        hub = RealtimeHub(allowed_origins=set())
        calls, manager, worker, memory, runtime, _ = self._owners(hub)
        runtime.renderer_ack = lambda *_args, **_kwargs: True
        with tempfile.TemporaryDirectory() as directory:
            enabled = SimpleNamespace(
                speech=self._speech_settings(Path(directory))
            )

            def availability(module_name: str):
                return None if module_name == "dashscope.audio.asr" else object()

            with (
                patch("backend.main.MainAgentSettings.from_config", return_value=enabled),
                patch("importlib.util.find_spec", side_effect=availability),
                patch(
                    "backend.main.DashScopeStreamingSttProvider",
                    side_effect=AssertionError("provider must not be constructed"),
                ),
            ):
                app = create_app(
                    task_manager=manager,
                    realtime_hub=hub,
                    main_agent_runtime=runtime,
                    memory_bridge=memory,
                    memory_worker=worker,
                )
                with TestClient(app):
                    self.assertFalse(app.state.main_agent_speech.status.enabled)
                    self.assertEqual(
                        app.state.main_agent_speech.status.reason,
                        "speech_optional_dependency_missing",
                    )

    def test_optional_speech_preflight_error_uses_stable_disabled_reason(self) -> None:
        for error in (RuntimeError("broken finder"), OSError("finder unavailable")):
            with self.subTest(error=type(error).__name__):
                hub = RealtimeHub(allowed_origins=set())
                calls, manager, worker, memory, runtime, _ = self._owners(hub)
                runtime.renderer_ack = lambda *_args, **_kwargs: True
                with tempfile.TemporaryDirectory() as directory:
                    enabled = SimpleNamespace(
                        speech=self._speech_settings(Path(directory))
                    )
                    with (
                        patch("backend.main.MainAgentSettings.from_config", return_value=enabled),
                        patch("importlib.util.find_spec", side_effect=error),
                        patch(
                            "backend.main.DashScopeStreamingSttProvider",
                            side_effect=AssertionError("provider must not be constructed"),
                        ),
                    ):
                        app = create_app(
                            task_manager=manager,
                            realtime_hub=hub,
                            main_agent_runtime=runtime,
                            memory_bridge=memory,
                            memory_worker=worker,
                        )
                        with TestClient(app):
                            self.assertFalse(app.state.main_agent_speech.status.enabled)
                            self.assertEqual(
                                app.state.main_agent_speech.status.reason,
                                "speech_optional_dependency_missing",
                            )


if __name__ == "__main__":
    unittest.main()
