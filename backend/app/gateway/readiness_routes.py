"""Expose local-only Backend readiness and bounded Main Agent metrics snapshots.

Unlike the exact process liveness route, readiness reflects lifespan admission and local owner state.
It reads no model, embedding, Qdrant, provider, network, or Task subprocess health endpoint and never
creates product Tasks. A retained finite startup error code explains a disabled Pi runtime without
running another probe or exposing raw exceptions, subprocess output, paths or credentials.
Missing Windows Bash is an informational code consumed only by Settings.
Immutable launch options and fixture identity accompany readiness, never user settings. Metrics are an optional bounded in-memory diagnostic snapshot with no content.
"""

from __future__ import annotations

import os
from contextlib import AsyncExitStack
from typing import Any

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, ConfigDict
from ...launch_options import LaunchOptions, MEMORY_FIXTURE


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
    profiles = getattr(application.state, "main_agent_profiles", None)
    components["activeProfileId"] = getattr(profiles, "active_profile_id", None)
    options = getattr(application.state, "launch_options", LaunchOptions())
    return {
        "launchOptions": options.public(),
        "memoryFixture": MEMORY_FIXTURE if options.lock_memory else None,
        "ready": bool(getattr(application.state, "backend_ready", False)),
        "configRevision": getattr(application.state, "config_revision", 0),
        "processId": os.getpid(),
        "instanceId": os.environ.get("AURABOT_BACKEND_INSTANCE", ""),
        "capabilities": getattr(application.state, "capabilities", {}),
        "taskRuntimeError": getattr(getattr(application.state, "task_manager", None), "runtime_error", None),
        "taskNotice": getattr(getattr(application.state, "task_manager", None), "runtime_notice", None),
        "components": components,
    }


class RestartPrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    force: bool = False


@router.post("/restart/prepare")
async def prepare_restart(body: RestartPrepareRequest, request: Request) -> dict[str, Any]:
    """Atomically inspect and fence every work admission owner before Electron stops this process."""
    hub = request.app.state.main_agent_realtime
    if not hub.origin_allowed(request.headers.get("origin")):
        raise HTTPException(403, "origin_not_allowed")
    manager = request.app.state.task_manager
    async with AsyncExitStack() as locks:
        await locks.enter_async_context(hub.activation_lock)
        state_lock = getattr(manager, "_state_lock", None)
        if state_lock is not None:
            await locks.enter_async_context(state_lock)
        runtime = hub.runtime
        if runtime is not None:
            await locks.enter_async_context(runtime._lock)
        task_count = len(getattr(manager, "_runs", {}))
        store = getattr(manager, "_store", None)
        if store is not None:
            task_count = int(store._db().execute(
                "SELECT COUNT(*) FROM tasks WHERE deleted_at IS NULL AND status IN ('queued','running','waiting')"
            ).fetchone()[0])
        archive = getattr(runtime, "_memory_archive_task", None)
        busy = {
            "tasks": task_count,
            "memoryWriting": archive is not None and not archive.done(),
            "mainAgent": bool(runtime and runtime.restart_busy()),
        }
        if any(busy.values()) and not body.force:
            return {"ready": False, "busy": busy}
        hub.accepting = False
        manager.accepting = False
        if runtime is not None:
            runtime.fence_admission()
        request.app.state.backend_ready = False
        return {"ready": True, "busy": busy}


@router.get("/metrics")
async def metrics(request: Request) -> dict[str, Any]:
    sink = request.app.state.main_agent_metrics
    return sink.snapshot()


__all__ = ["metrics", "readiness", "router"]
