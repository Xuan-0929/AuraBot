"""AuraBot backend application entry point."""

from fastapi import FastAPI

from .config import config, initialize_config

initialize_config()

from .app.gateway.chat_routes import router as chat_router
from .app.gateway.debug_routes import router as debug_router
from .app.gateway.status_routes import health, router as status_router, services
from .app.gateway.task_routes import router as task_router

app = FastAPI(title="AuraBot Backend Gateway")

app.include_router(status_router)
app.include_router(chat_router)
app.include_router(task_router)
app.include_router(debug_router)

__all__ = ["app", "config", "health", "services"]
