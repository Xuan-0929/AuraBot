"""
组合 AuraBot FastAPI Backend、固定健康路由、Main Agent 实时/语音网关与 Python Pi Task Control Plane。

模块导入时只加载经过白名单过滤的 YAML；FastAPI lifespan 组合并启动 TaskManager、一个
process-wide MemoryWorker、active-profile MemoryBridge、MainAgentRuntime、RealtimeHub 与
SpeechPipeline，再按 ownership 逆序安全关闭。聊天 Provider 与语音可选 SDK 均惰性到真实
invoke/audio；enabled speech 在 compose 前只用 find_spec 检查实际 lazy-import 模块，缺少
密钥、依赖或本地资产会降级而不是联网探测。静态/TestClient、readiness 与
GET /health 不探测模型、embedding、Qdrant 网络或付费 Provider，且健康响应保持精确兼容。
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import config, initialize_config

initialize_config()

from .app.gateway.health_routes import router as health_router
from .app.gateway.memory_routes import router as memory_router
from .app.gateway.readiness_routes import router as readiness_router
from .app.gateway.errors import register_backend_exception_handlers
from .app.gateway.task_routes import (
    register_task_exception_handlers,
    router as task_router,
)
from .task.manager import TaskManager
from .task.settings import TaskSettings
from .main_agent.context import ContextStore
from .main_agent.memory import MemoryBridge, MemoryWorker
from .main_agent.metrics import LifecycleMetrics
from .main_agent.provider import build_chat_provider
from .main_agent.observations import WorkspaceListExecutor, WorkspaceReadExecutor, WorkspaceReader
from .main_agent.profiles import ProfileConflictError, ProfileStore, ProfileValidationError
from .main_agent.realtime import RealtimeHub, router as main_agent_realtime_router
from .main_agent.runtime import MainAgentRuntime
from .main_agent.settings import MainAgentSettings
from .main_agent.snapshot import SnapshotProjector
from .main_agent.speech import (
    DashScopeSpeechOutputProvider,
    DashScopeStreamingSttProvider,
    SileroVadDetector,
    SmartTurnDetector,
    SpeechPipeline,
)
from .main_agent.tool_runtime import ToolSurfacePolicy
from .memory.embedding import create_embedding_provider
from .memory.extractor import RuleBasedMemoryExtractor
from .memory.stores.qdrant import QdrantLocalVectorStore
from .memory.stores.sqlite import SQLiteMemoryStore


logger = logging.getLogger(__name__)


def build_task_manager() -> TaskManager:
    """Construct the concrete Task subsystem from the initialized YAML."""

    settings = TaskSettings.from_config(config, config.path)
    settings.validate_runtime()
    return TaskManager(settings)


def create_app(
    task_manager: TaskManager | None = None,
    realtime_hub: RealtimeHub | None = None,
    speech_pipeline: SpeechPipeline | None = None,
    main_agent_runtime: MainAgentRuntime | Any | None = None,
    memory_bridge: MemoryBridge | Any | None = None,
    memory_worker: MemoryWorker | Any | None = None,
    metrics: LifecycleMetrics | None = None,
    provider_factory: Any | None = None,
    speech_factory: Any | None = None,
) -> FastAPI:
    """Build the Backend and optionally inject concrete Task and realtime owners for tests/composition."""

    main_agent_realtime = realtime_hub or RealtimeHub()
    main_agent_speech = speech_pipeline or SpeechPipeline.disabled(main_agent_realtime)
    if main_agent_speech.hub is not main_agent_realtime:
        raise ValueError("speech pipeline and realtime hub must share one owner")

    metric_sink = metrics or LifecycleMetrics()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        manager = task_manager or build_task_manager()
        worker = memory_worker or MemoryWorker(metrics=metric_sink)
        settings = MainAgentSettings.from_config(config, config.path)
        speech_owner = speech_pipeline or SpeechPipeline.disabled(main_agent_realtime)
        bridge = memory_bridge
        runtime = main_agent_runtime
        owned: dict[str, Any] = {}
        if bridge is None or runtime is None:
            default = _build_default_main_agent(
                manager=manager,
                hub=main_agent_realtime,
                worker=worker,
                bridge=bridge,
                runtime=runtime,
                metrics=metric_sink,
                settings=settings,
                provider_factory=provider_factory,
            )
            bridge = default["bridge"]
            runtime = default["runtime"]
            owned = default
        assert bridge is not None and runtime is not None
        if speech_pipeline is None:
            if settings.speech.enabled:
                try:
                    factory = speech_factory or _build_default_speech
                    speech_owner = factory(settings.speech, main_agent_realtime, runtime)
                except Exception:
                    metric_sink.increment("speech.composition.unavailable")
                    speech_owner = SpeechPipeline.disabled(
                        main_agent_realtime, reason="speech_unavailable"
                    )
            else:
                speech_owner = SpeechPipeline.disabled(main_agent_realtime)
        if speech_owner.hub is not main_agent_realtime:
            raise ValueError("speech pipeline and realtime hub must share one owner")
        main_agent_realtime.runtime = runtime
        application.state.task_manager = manager
        application.state.main_agent_memory_worker = worker
        application.state.main_agent_memory = bridge
        application.state.main_agent_runtime = runtime
        application.state.main_agent_speech = speech_owner
        application.state.backend_ready = False
        metric_sink.increment("backend.startup")
        logger.info("backend_lifespan_starting")
        started: list[Any] = []
        try:
            for component in (manager, worker, bridge, runtime):
                await component.startup()
                started.append(component)
            start_accepting = getattr(main_agent_realtime, "start_accepting", None)
            if callable(start_accepting):
                start_accepting()
            await speech_owner.startup()
            started.append(speech_owner)
            application.state.backend_ready = True
            try:
                yield
            finally:
                application.state.backend_ready = False
        finally:
            await _shutdown_immune(
                main_agent_realtime,
                speech_owner if speech_owner in started else None,
                runtime if runtime in started else None,
                bridge if bridge in started else None,
                worker if worker in started else None,
                manager if manager in started else None,
            )
            for closeable in owned.get("closeables", ()):
                try:
                    closeable.close()
                except Exception:
                    pass
            for closeable in owned.get("async_closeables", ()):
                try:
                    await closeable.aclose()
                except BaseException:
                    pass
            metric_sink.increment("backend.shutdown")
            logger.info("backend_lifespan_stopped")

    application = FastAPI(title="AuraBot Backend", lifespan=lifespan)
    # WebSocket upgrades may begin before a lifespan-managed Task runtime is needed; the process-global
    # realtime lease therefore exists from app construction and is reused by lifespan composition.
    application.state.main_agent_realtime = main_agent_realtime
    application.state.main_agent_speech = main_agent_speech
    application.state.main_agent_memory_worker = memory_worker
    application.state.main_agent_memory = memory_bridge
    application.state.main_agent_runtime = main_agent_runtime
    application.state.main_agent_metrics = metric_sink
    application.state.backend_ready = False
    application.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(main_agent_realtime.allowed_origins),
        allow_methods=["POST"],
        allow_headers=[],
    )
    application.include_router(health_router)
    application.include_router(task_router)
    application.include_router(memory_router)
    application.include_router(readiness_router)
    application.include_router(main_agent_realtime_router)
    register_task_exception_handlers(application)
    register_backend_exception_handlers(application)
    return application


def _build_default_main_agent(
    *,
    manager: TaskManager,
    hub: RealtimeHub,
    worker: MemoryWorker,
    bridge: MemoryBridge | None,
    runtime: MainAgentRuntime | None,
    metrics: LifecycleMetrics,
    settings: MainAgentSettings,
    provider_factory: Any | None,
) -> dict[str, Any]:
    """Build the smallest offline-safe active-profile composition inside lifespan."""

    raw_main_agent = config.get("main_agent", {})
    profile_id = raw_main_agent.get("profile_id", "default") if isinstance(raw_main_agent, dict) else "default"
    profiles = ProfileStore(settings.profile_root)
    try:
        profile = profiles.read(profile_id)
    except ProfileValidationError:
        try:
            profile = profiles.create(profile_id, soul="")
        except ProfileConflictError:
            profile = profiles.read(profile_id)
    closeables: list[Any] = []
    if bridge is None:
        store = SQLiteMemoryStore(profile.path / "memory.sqlite3")
        raw_memory = config.get("memory", {})
        embedding_config = raw_memory.get("embedding", {}) if isinstance(raw_memory, dict) else {}
        embedding = create_embedding_provider(embedding_config)
        vectors = QdrantLocalVectorStore(
            path=profile.path / "qdrant", vector_size=embedding.dimension
        )
        bridge = MemoryBridge(
            profile_id=profile.profile_id,
            store=store,
            vector_store=vectors,
            embedding_provider=embedding,
            extractor=RuleBasedMemoryExtractor(),
            worker=worker,
            metrics=metrics,
        )
        closeables.extend((vectors, store))
    if runtime is None:
        context = ContextStore(profile.path / "main-agent.sqlite3")
        reader = WorkspaceReader(settings.workspaces)

        async def renderer(clause: Any) -> bool | None:
            port = await hub.acquire_renderer_port()
            if port is None:
                return False
            return False if await port.offer_clause(clause) is not None else None

        workspace_ids = tuple(
            entry.get("id")
            for entry in config.get("app", {}).get("workspaces", [])
            if isinstance(entry, dict) and isinstance(entry.get("id"), str)
        )
        provider_builder = provider_factory or build_chat_provider
        provider = provider_builder(settings.chat_model, metrics)
        runtime = MainAgentRuntime(
            context_store=context,
            snapshot_projector=SnapshotProjector(),
            tool_policy=ToolSurfacePolicy(
                workspace_ids=workspace_ids,
                task_controls=(
                    "inspect_tasks", "answer_task_question", "pause_task",
                    "resume_task", "cancel_task",
                ),
            ),
            provider=provider,
            renderer=renderer,
            executors={
                "workspace_list": WorkspaceListExecutor(reader),
                "workspace_read": WorkspaceReadExecutor(reader),
            },
            soul=profile.soul.text,
            memory_bridge=bridge,
            task_manager=manager,
            owner_profile_id=profile.profile_id,
            metrics=metrics,
        )
        closeables.append(context)
    return {
        "bridge": bridge,
        "runtime": runtime,
        "closeables": tuple(closeables),
        "async_closeables": (provider,) if runtime is not None and "provider" in locals() else (),
    }


def _build_default_speech(settings: Any, hub: RealtimeHub, runtime: MainAgentRuntime) -> SpeechPipeline:
    """Compose lazy DashScope/local speech owners or return a truthful local-only degradation."""

    if not settings.api_key:
        return SpeechPipeline.disabled(hub, reason="speech_api_key_missing")
    vad = SileroVadDetector(settings.silero_model_path, settings.silero_license_path)
    eou = SmartTurnDetector(settings.smart_turn_model_path, settings.smart_turn_license_path)
    reasons = (vad.status().reason, eou.status().reason)
    if any(reason != "ready_to_load" for reason in reasons):
        return SpeechPipeline.disabled(hub, reason="speech_assets_unavailable")
    for module_name in (
        "dashscope.audio.asr",
        "dashscope.audio.tts_v2",
        "onnxruntime",
        "numpy",
        "transformers",
    ):
        try:
            if importlib.util.find_spec(module_name) is None:
                return SpeechPipeline.disabled(
                    hub, reason="speech_optional_dependency_missing"
                )
        except Exception:
            return SpeechPipeline.disabled(
                hub, reason="speech_optional_dependency_missing"
            )
    return SpeechPipeline.compose(
        hub,
        runtime,
        stt_provider=DashScopeStreamingSttProvider(
            settings.stt_model_name, api_key=settings.api_key
        ),
        output_provider=DashScopeSpeechOutputProvider(
            settings.tts_model_name, voice=settings.tts_voice, api_key=settings.api_key
        ),
        vad=vad,
        eou=eou,
    )


async def _shutdown_immune(
    hub: Any,
    speech: Any | None,
    runtime: Any | None,
    bridge: Any | None,
    worker: Any | None,
    manager: Any | None,
) -> None:
    """Finish the ownership-ordered shutdown even when lifespan cancellation arrives."""

    async def close_all() -> None:
        stop_accepting = getattr(hub, "stop_accepting", None)
        if callable(stop_accepting):
            result = stop_accepting()
            if asyncio.iscoroutine(result):
                await result
        for component in (speech, runtime, bridge, worker, manager):
            shutdown = getattr(component, "shutdown", None)
            if callable(shutdown):
                try:
                    await shutdown()
                except BaseException:
                    pass

    task = asyncio.create_task(close_all(), name="backend-lifespan-shutdown")
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    await asyncio.gather(task, return_exceptions=True)
    if cancelled:
        raise asyncio.CancelledError


app = create_app()

__all__ = ["app", "build_task_manager", "config", "create_app"]
