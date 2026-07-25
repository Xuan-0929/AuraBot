"""AuraBot backend application entry point."""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import config, initialize_config

initialize_config()

from .app.gateway.chat_routes import router as chat_router
from .app.gateway.debug_routes import router as debug_router
from .app.gateway.plan_tool_routes import router as plan_tool_router
from .app.gateway.status_routes import health, router as status_router, services
from .app.gateway.task_routes import router as task_router
from .agent.task.TaskManager import TaskManager


@asynccontextmanager
async def lifespan(application: FastAPI):
    manager = TaskManager()
    manager.bind_event_loop(asyncio.get_running_loop())
    try:
        yield
    finally:
        manager.shutdown()


app = FastAPI(title="AuraBot Backend Gateway", lifespan=lifespan)

app.include_router(status_router)
app.include_router(chat_router)
app.include_router(task_router)
app.include_router(debug_router)
app.include_router(plan_tool_router)

__all__ = ["app", "config", "health", "services"]
