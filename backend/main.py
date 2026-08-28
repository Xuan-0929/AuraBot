"""Compose AuraBot around one token-activated Profile and the shared Memory implementation.

Process startup owns only TaskManager, metrics, and the minimal RealtimeHub. The first valid Pet token
request supplies Electron's Profile identity and effective Soul, causing ``ActiveProfileManager`` to
open that Profile's Context, ``backend.memory.MemoryService``, model/tools, and SpeechService. A serialized
Profile switch closes those resources before opening another directory; no inactive Memory worker, second
schema, Backend Profile file, or Backend Soul file exists.
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

from .app.gateway.errors import register_backend_exception_handlers
from .app.gateway.health_routes import router as health_router
from .app.gateway.readiness_routes import router as readiness_router
from .app.gateway.task_routes import register_task_exception_handlers, router as task_router
from .main_agent.context import ContextStore
from .main_agent.lifecycle import ActiveProfileManager, ProfileResources
from .main_agent.metrics import LifecycleMetrics
from .main_agent.observations import (
    TavilyTools,
    WorkspaceListExecutor,
    WorkspaceReadExecutor,
    WorkspaceReader,
)
from .main_agent.provider import build_chat_provider
from .main_agent.realtime import RealtimeHub, router as main_agent_realtime_router
from .main_agent.runtime import MainAgentRuntime
from .main_agent.settings import MainAgentSettings
from .main_agent.snapshot import SnapshotProjector
from .main_agent.speech import (
    DashScopeSpeechOutputProvider,
    DashScopeStreamingSttProvider,
    SileroVadDetector,
    SmartTurnDetector,
    SpeechService,
)
from .main_agent.tool_runtime import ToolSurfacePolicy
from .memory.embedding import create_embedding_provider
from .memory.extraction.providers import OpenAICompatibleMemoryExtractionModel
from .memory.extractor import StructuredMemoryExtractor
from .memory.service import MemoryService
from .memory.stores.qdrant import QdrantLocalVectorStore
from .memory.stores.sqlite import SQLiteMemoryStore
from .task.manager import TaskManager
from .task.settings import TaskSettings


logger = logging.getLogger(__name__)


def build_task_manager() -> TaskManager:
    """Construct the shared durable Task control plane from initialized configuration."""

    settings = TaskSettings.from_config(config, config.path)
    settings.validate_runtime()
    return TaskManager(settings)


def build_memory_extractor(settings: Any) -> StructuredMemoryExtractor:
    """Reuse the chat-model role for strict, background structured Memory extraction."""

    return StructuredMemoryExtractor(OpenAICompatibleMemoryExtractionModel(
        base_url=settings.base_url,
        model_name=settings.model_name,
        api_key=settings.api_key,
        timeout_seconds=settings.timeout_seconds,
    ))


def create_app(
    task_manager: Any | None = None,
    realtime_hub: RealtimeHub | None = None,
    profile_manager: ActiveProfileManager | Any | None = None,
    profile_factory: Any | None = None,
    metrics: LifecycleMetrics | None = None,
    provider_factory: Any | None = None,
    speech_factory: Any | None = None,
) -> FastAPI:
    """Build FastAPI with injectable process/profile ownership seams for public-boundary tests."""

    hub = realtime_hub or RealtimeHub()
    metric_sink = metrics or LifecycleMetrics()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        manager = task_manager or build_task_manager()
        settings = MainAgentSettings.from_config(config, config.path)
        profiles = profile_manager
        if profiles is None:
            async def factory(profile_id: str, path: Path, soul: str) -> ProfileResources:
                if profile_factory is not None:
                    return await profile_factory(profile_id, path, soul)
                return await _build_profile(
                    profile_id=profile_id,
                    path=path,
                    soul=soul,
                    manager=manager,
                    hub=hub,
                    settings=settings,
                    metrics=metric_sink,
                    provider_factory=provider_factory,
                    speech_factory=speech_factory,
                )

            profiles = ActiveProfileManager(settings.profile_root, hub, factory)
        hub.profile_activator = profiles.activate
        application.state.task_manager = manager
        application.state.main_agent_profiles = profiles
        application.state.backend_ready = False
        metric_sink.increment("backend.startup")
        await manager.startup()
        hub.start_accepting()
        application.state.backend_ready = True
        logger.info("backend_lifespan_started")
        try:
            yield
        finally:
            application.state.backend_ready = False
            await _shutdown_immune(hub, profiles, manager)
            metric_sink.increment("backend.shutdown")
            logger.info("backend_lifespan_stopped")

    application = FastAPI(title="AuraBot Backend", lifespan=lifespan)
    application.state.main_agent_realtime = hub
    application.state.main_agent_profiles = profile_manager
    application.state.main_agent_metrics = metric_sink
    application.state.backend_ready = False
    application.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(hub.allowed_origins),
        allow_methods=["POST"],
        allow_headers=[],
    )
    application.include_router(health_router)
    application.include_router(task_router)
    application.include_router(readiness_router)
    application.include_router(main_agent_realtime_router)
    register_task_exception_handlers(application)
    register_backend_exception_handlers(application)
    return application


async def _build_profile(
    *,
    profile_id: str,
    path: Path,
    soul: str,
    manager: Any,
    hub: RealtimeHub,
    settings: MainAgentSettings,
    metrics: LifecycleMetrics,
    provider_factory: Any | None,
    speech_factory: Any | None,
) -> ProfileResources:
    """Compose and start the sole active Profile, cleaning partial owners on any failure."""

    if not settings.speech.enabled:
        raise RuntimeError("speech_disabled")
    detectors = _prepare_speech(settings.speech) if speech_factory is None else None
    context = ContextStore(path / "context.sqlite3")
    embedding = create_embedding_provider(config.get("memory", {}).get("embedding", {}))
    vectors = QdrantLocalVectorStore(path=path / "qdrant", vector_size=embedding.dimension)
    memory = MemoryService(
        metadata_store=SQLiteMemoryStore(path / "memory.sqlite3"),
        embedding_provider=embedding,
        extractor=build_memory_extractor(settings.chat_model),
        vector_store=vectors,
    )
    provider = (provider_factory or build_chat_provider)(settings.chat_model, metrics)
    reader = WorkspaceReader(settings.workspaces)
    executors: dict[str, Any] = {
        "workspace_list": WorkspaceListExecutor(reader),
        "workspace_read": WorkspaceReadExecutor(reader),
    }
    observation_tools: tuple[str, ...] = ()
    tavily: TavilyTools | None = None
    if settings.web.tavily_api_key:
        tavily = TavilyTools(settings.web.tavily_api_key)
        executors.update({"web_search": tavily.search, "web_read": tavily.read})
        observation_tools = ("web_search", "web_read")

    async def speech_unavailable(_clause: Any) -> None:
        raise RuntimeError("speech_unavailable")

    runtime = MainAgentRuntime(
        context_store=context,
        snapshot_projector=SnapshotProjector(),
        tool_policy=ToolSurfacePolicy(
            workspace_ids=settings.workspaces.ids,
            observation_tools=observation_tools,
            task_controls=(
                "inspect_tasks", "answer_task_question", "pause_task", "resume_task", "cancel_task",
            ),
        ),
        provider=provider,
        renderer=speech_unavailable,
        executors=executors,
        soul=soul,
        memory_service=memory,
        task_manager=manager,
        owner_profile_id=profile_id,
        metrics=metrics,
    )
    closeables: list[Any] = [context, provider]
    if tavily is not None:
        closeables.append(tavily)
    resources = ProfileResources(
        profile_id, runtime, None, memory, closeables=tuple(closeables)
    )
    try:
        if speech_factory is not None:
            resources.speech = speech_factory(settings.speech, hub, runtime)
        else:
            assert detectors is not None
            resources.speech = _build_speech(settings.speech, hub, runtime, *detectors)
        await runtime.startup()
        await resources.speech.startup()
        return resources
    except BaseException:
        await resources.shutdown()
        raise


def _prepare_speech(settings: Any) -> tuple[SileroVadDetector, SmartTurnDetector]:
    """Validate speech configuration before opening any Profile database or model client."""

    if not settings.api_key:
        raise RuntimeError("speech_api_key_missing")
    vad = SileroVadDetector(settings.silero_model_path, settings.silero_license_path)
    eou = SmartTurnDetector(settings.smart_turn_model_path, settings.smart_turn_license_path)
    if vad.status().reason != "ready_to_load" or eou.status().reason != "ready_to_load":
        raise RuntimeError("speech_assets_unavailable")
    for module_name in (
        "dashscope.audio.asr", "dashscope.audio.tts_v2", "onnxruntime", "numpy", "transformers",
    ):
        if importlib.util.find_spec(module_name) is None:
            raise RuntimeError("speech_optional_dependency_missing")
    return vad, eou


def _build_speech(
    settings: Any,
    hub: RealtimeHub,
    runtime: MainAgentRuntime,
    vad: SileroVadDetector,
    eou: SmartTurnDetector,
) -> SpeechService:
    """Build the single production async DashScope/VAD/EOU speech shape."""

    return SpeechService.compose(
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


async def _shutdown_immune(hub: Any, profiles: Any, manager: Any) -> None:
    """Complete the short ownership-ordered shutdown even if lifespan itself is cancelled."""

    async def close_all() -> None:
        await hub.stop_accepting()
        await profiles.shutdown()
        await manager.shutdown()

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
