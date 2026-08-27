"""Expose local-only Backend readiness and bounded Main Agent metrics snapshots.

Unlike the exact process liveness route, readiness reflects lifespan admission and local owner state.
It reads no model, embedding, Qdrant, provider, network, or Task subprocess health endpoint and never
creates product Tasks.  Metrics are an optional bounded in-memory diagnostic snapshot with no content.
"""

from __future__ import annotations

import inspect
from typing import Any

from fastapi import APIRouter, Request


router = APIRouter(prefix="/api/v1", tags=["backend-readiness"])


@router.get("/readiness")
async def readiness(request: Request) -> dict[str, Any]:
    application = request.app
    components: dict[str, Any] = {
        "lifecycle": "ready" if getattr(application.state, "backend_ready", False) else "starting",
        "realtimeAccepting": bool(
            getattr(getattr(application.state, "main_agent_realtime", None), "accepting", False)
        ),
    }
    memory = getattr(application.state, "main_agent_memory", None)
    local_status = getattr(memory, "local_status", None)
    if callable(local_status):
        value = local_status()
        components["memory"] = await value if inspect.isawaitable(value) else value
    worker = getattr(application.state, "main_agent_memory_worker", None)
    components["memoryWorker"] = {
        "running": bool(getattr(worker, "live_task", None))
        if hasattr(worker, "live_task") else bool(getattr(worker, "started", False))
    }
    return {
        "ready": bool(getattr(application.state, "backend_ready", False)),
        "components": components,
    }


@router.get("/metrics")
async def metrics(request: Request) -> dict[str, Any]:
    sink = request.app.state.main_agent_metrics
    return sink.snapshot()


__all__ = ["metrics", "readiness", "router"]
