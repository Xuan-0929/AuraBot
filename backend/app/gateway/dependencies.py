"""FastAPI dependency access to the application-owned runtime."""

from __future__ import annotations

from fastapi import Request

from ..runtime import BackendRuntime
from ..services.plan_tool_service import PlanToolApplicationService
from ..services.task_service import TaskApplicationService


def get_backend_runtime(request: Request) -> BackendRuntime:
    runtime = getattr(request.app.state, "backend_runtime", None)
    if runtime is None:
        raise RuntimeError("BackendRuntime is not initialized")
    return runtime


def get_task_service(request: Request) -> TaskApplicationService:
    return get_backend_runtime(request).task_service


def get_plan_tool_service(
    request: Request,
) -> PlanToolApplicationService:
    return get_backend_runtime(request).plan_tool_service


__all__ = [
    "get_backend_runtime",
    "get_plan_tool_service",
    "get_task_service",
]
