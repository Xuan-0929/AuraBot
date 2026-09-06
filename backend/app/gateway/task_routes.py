"""Stream the active desktop Profile's recent Task titles and states.

TaskManager owns bounded snapshots and change notifications. The gateway serializes those snapshots,
keeps idle connections alive and cancels its pending read on disconnect; Task controls and durable
business-event replay remain internal to Main Agent.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import cast

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from ...task import TaskManager

router = APIRouter(prefix="/api/v1", tags=["tasks"])
SSE_KEEPALIVE_SECONDS = 15.0


@router.get("/task-events")
async def task_events(
    request: Request,
    owner_profile_id: str = Query(alias="ownerProfileId", min_length=1, max_length=128),
) -> StreamingResponse:
    async def stream() -> AsyncIterator[str]:
        manager = cast(TaskManager, request.app.state.task_manager)
        iterator = manager.iter_status_snapshots(owner_profile_id)
        pending = asyncio.create_task(anext(iterator), name="task-sse-next-snapshot")
        try:
            while True:
                done, _ = await asyncio.wait({pending}, timeout=SSE_KEEPALIVE_SECONDS)
                if not done:
                    yield ": keepalive\n\n"
                    continue
                try:
                    tasks = pending.result()
                except StopAsyncIteration:
                    return
                payload = json.dumps({"tasks": tasks}, ensure_ascii=False, separators=(",", ":"))
                yield f"event: task.snapshot\ndata: {payload}\n\n"
                pending = asyncio.create_task(anext(iterator), name="task-sse-next-snapshot")
        finally:
            if not pending.done():
                pending.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending
            await iterator.aclose()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


__all__ = ["router"]
