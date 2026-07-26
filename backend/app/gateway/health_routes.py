"""Backend process health and Task admission status."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from ..runtime import BackendRuntime
from .dependencies import get_backend_runtime

router = APIRouter()
RuntimeDependency = Annotated[BackendRuntime, Depends(get_backend_runtime)]


@router.get("/health")
async def health(runtime: RuntimeDependency) -> dict[str, object]:
    return {
        "ok": True,
        "service": "backend",
        "accepting_tasks": not runtime.task_manager.closing,
        "running_count": runtime.task_manager.running_count,
        "queued_count": runtime.task_manager.queued_count,
    }


__all__ = ["health", "router"]
