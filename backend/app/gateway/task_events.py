"""SSE transport for Task snapshot-plus-delta observation."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from ..runtime import BackendRuntime
from ..services.task_service import TaskApplicationService
from .dependencies import get_backend_runtime, get_task_service

router = APIRouter(prefix="/task", tags=["task"])
TaskServiceDependency = Annotated[
    TaskApplicationService,
    Depends(get_task_service),
]
RuntimeDependency = Annotated[BackendRuntime, Depends(get_backend_runtime)]


@router.get("/events")
async def task_events(
    service: TaskServiceDependency,
    runtime: RuntimeDependency,
) -> StreamingResponse:
    subscription = service.subscribe()

    async def generate() -> AsyncIterator[str]:
        try:
            while True:
                try:
                    event = await subscription.next_event(
                        runtime.settings.sse_keepalive_seconds
                    )
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                except StopAsyncIteration:
                    return
                else:
                    yield encode_sse_event(event)
        finally:
            subscription.close()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def encode_sse_event(event: dict[str, Any]) -> str:
    stream_id = event["stream_id"]
    revision = event["revision"]
    event_name = event["event"]
    data = json.dumps(
        event,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        f"id: {stream_id}:{revision}\n"
        f"event: {event_name}\n"
        f"data: {data}\n\n"
    )


__all__ = ["encode_sse_event", "router", "task_events"]
