"""
组合 AuraBot FastAPI Backend、固定健康路由、Main Agent 实时/语音网关与 Python Pi Task Control Plane。

模块导入时只加载经过白名单过滤的 YAML；FastAPI lifespan 构造/启动 TaskManager，
将其放入 app.state 供版本化 Gateway 使用，并在退出时完整 shutdown。进程级 RealtimeHub
从构造期起提供受 Origin/CORS 保护的浏览器连接；SpeechPipeline 生命周期 owner 默认明确
禁用且不加载可选 SDK/模型，组合方可注入已绑定的真实或测试 pipeline。GET /health 不依赖
任何 Worker 状态。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import config, initialize_config

initialize_config()

from .app.gateway.health_routes import router as health_router
from .app.gateway.errors import register_backend_exception_handlers
from .app.gateway.task_routes import (
    register_task_exception_handlers,
    router as task_router,
)
from .task.manager import TaskManager
from .task.settings import TaskSettings
from .main_agent.realtime import RealtimeHub, router as main_agent_realtime_router
from .main_agent.speech import SpeechPipeline


def build_task_manager() -> TaskManager:
    """Construct the concrete Task subsystem from the initialized YAML."""

    settings = TaskSettings.from_config(config, config.path)
    settings.validate_runtime()
    return TaskManager(settings)


def create_app(
    task_manager: TaskManager | None = None,
    realtime_hub: RealtimeHub | None = None,
    speech_pipeline: SpeechPipeline | None = None,
) -> FastAPI:
    """Build the Backend and optionally inject concrete Task and realtime owners for tests/composition."""

    main_agent_realtime = realtime_hub or RealtimeHub()
    main_agent_speech = speech_pipeline or SpeechPipeline.disabled(main_agent_realtime)
    if main_agent_speech.hub is not main_agent_realtime:
        raise ValueError("speech pipeline and realtime hub must share one owner")

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        manager = task_manager or build_task_manager()
        application.state.task_manager = manager
        await manager.startup()
        try:
            await main_agent_speech.startup()
            try:
                yield
            finally:
                await main_agent_speech.shutdown()
        finally:
            await manager.shutdown()

    application = FastAPI(title="AuraBot Backend", lifespan=lifespan)
    # WebSocket upgrades may begin before a lifespan-managed Task runtime is needed; the process-global
    # realtime lease therefore exists from app construction and is reused by lifespan composition.
    application.state.main_agent_realtime = main_agent_realtime
    application.state.main_agent_speech = main_agent_speech
    application.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(main_agent_realtime.allowed_origins),
        allow_methods=["POST"],
        allow_headers=[],
    )
    application.include_router(health_router)
    application.include_router(task_router)
    application.include_router(main_agent_realtime_router)
    register_task_exception_handlers(application)
    register_backend_exception_handlers(application)
    return application


app = create_app()

__all__ = ["app", "build_task_manager", "config", "create_app"]
