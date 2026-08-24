"""
组合 AuraBot FastAPI Backend、固定健康路由与 Python Pi Task Control Plane。

模块导入时只加载经过白名单过滤的 YAML；FastAPI lifespan 构造/启动 TaskManager，
将其放入 app.state 供版本化 Gateway 使用，并在退出时完整 shutdown。业务逻辑不写在
本组合入口，GET /health 也不依赖 Worker 状态。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

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


def build_task_manager() -> TaskManager:
    """Construct the concrete Task subsystem from the initialized YAML."""

    settings = TaskSettings.from_config(config, config.path)
    settings.validate_runtime()
    return TaskManager(settings)


def create_app(task_manager: TaskManager | None = None) -> FastAPI:
    """Build the Backend and optionally inject a concrete manager for composition tests."""

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        manager = task_manager or build_task_manager()
        application.state.task_manager = manager
        await manager.startup()
        try:
            yield
        finally:
            await manager.shutdown()

    application = FastAPI(title="AuraBot Backend", lifespan=lifespan)
    application.include_router(health_router)
    application.include_router(task_router)
    register_task_exception_handlers(application)
    register_backend_exception_handlers(application)
    return application


app = create_app()

__all__ = ["app", "build_task_manager", "config", "create_app"]
