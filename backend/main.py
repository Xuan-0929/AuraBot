"""
创建 AuraBot Backend 的 FastAPI 应用并绑定进程生命周期。

lifespan 启动 BackendRuntime、注册 gateway routes，并在退出时集中关闭应用资源；
本模块只负责装配入口，不实现 Task、Harness、Plan 或工具业务逻辑。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from .config import config, initialize_config

initialize_config()

from .app.gateway.errors import application_error_handler
from .app.gateway.health_routes import router as health_router
from .app.gateway.plan_tool_routes import router as plan_tool_router
from .app.gateway.task_events import router as task_events_router
from .app.gateway.task_routes import router as task_router
from .app.runtime import BackendRuntime
from .app.services.errors import ApplicationError

RuntimeFactory = Callable[[Mapping[str, Any]], BackendRuntime]


def create_app(
    *,
    runtime_factory: RuntimeFactory | None = None,
) -> FastAPI:
    factory = runtime_factory or BackendRuntime.from_config

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        runtime = factory(config)
        application.state.backend_runtime = runtime
        runtime.bind_event_loop()
        try:
            yield
        finally:
            await runtime.aclose()
            application.state.backend_runtime = None

    application = FastAPI(
        title="AuraBot Backend",
        lifespan=lifespan,
    )
    application.add_exception_handler(
        ApplicationError,
        application_error_handler,
    )
    application.include_router(health_router)
    application.include_router(task_events_router)
    application.include_router(task_router)
    application.include_router(plan_tool_router)
    return application


app = create_app()

__all__ = ["app", "config", "create_app"]
