"""
创建 AuraBot health-only FastAPI Backend。

模块在进程导入时加载共享 YAML 配置，并只挂载独立健康路由；它是 Uvicorn 的组合
入口，不创建 Task runtime、MCP client 或任何执行资源。
"""

from __future__ import annotations

from fastapi import FastAPI

from .config import config, initialize_config

initialize_config()

from .app.gateway.health_routes import router as health_router


def create_app() -> FastAPI:
    """Build a stateless Backend application exposing only process health."""

    application = FastAPI(title="AuraBot Backend")
    application.include_router(health_router)
    return application


app = create_app()

__all__ = ["app", "config", "create_app"]
