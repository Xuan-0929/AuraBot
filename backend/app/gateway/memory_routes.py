"""Expose thin versioned browse/forget/retry routes for the active profile's Memory bridge.

The gateway accepts and emits stable camelCase product DTOs, delegates all state transitions to
``MemoryBridge``, and maps only known validation/not-found outcomes.  It never returns extraction
payloads, leases, provider inputs, vectors, Qdrant internals, or raw SQLite errors; the legacy
``/memory`` router remains separate and unchanged for existing callers.
"""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Request
from pydantic import ConfigDict, Field

from .errors import CamelModel, error_response


class ForgetMemoriesRequest(CamelModel):
    model_config = ConfigDict(extra="forbid")

    memory_ids: list[str] = Field(default_factory=list, max_length=100)
    episode_ids: list[str] = Field(default_factory=list, max_length=100)
    reason: str | None = Field(default=None, max_length=500)


router = APIRouter(prefix="/api/v1", tags=["main-agent-memory"])


def _bridge(request: Request) -> Any:
    return cast(Any, request.app.state.main_agent_memory)


@router.get("/memories")
async def browse_memories(request: Request, limit: int = 50) -> dict[str, Any]:
    if not 1 <= limit <= 100:
        return error_response(400, "invalid_memory_request", "limit must be from 1 to 100")
    return await _bridge(request).browse(limit=limit)


@router.post("/memories/forget")
async def forget_memories(body: ForgetMemoriesRequest, request: Request) -> Any:
    if not body.memory_ids and not body.episode_ids:
        return error_response(
            400, "invalid_memory_request", "memoryIds or episodeIds is required"
        )
    try:
        return await _bridge(request).forget(
            memory_ids=tuple(body.memory_ids),
            episode_ids=tuple(body.episode_ids),
            reason=body.reason,
        )
    except ValueError as exc:
        return error_response(400, "invalid_memory_request", str(exc))


@router.post("/memory-jobs/{job_id}/retry")
async def retry_memory_job(job_id: str, request: Request) -> Any:
    try:
        return await _bridge(request).retry(job_id)
    except KeyError:
        return error_response(404, "memory_job_not_found", "Memory job was not found or is not failed.")


__all__ = ["ForgetMemoriesRequest", "router"]
