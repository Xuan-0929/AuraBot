"""Expose active-Profile-only browse, forget, and retry routes for Main Agent Memory.

Every route carries ``profile_id`` explicitly and delegates to the process-wide active Profile owner.
That owner rejects inactive identities before touching a database, so the API never opens dormant
Profile resources or leaks one Profile's memories through another Profile's session.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import ConfigDict, Field

from .errors import CamelModel, error_response


class ForgetMemoriesRequest(CamelModel):
    model_config = ConfigDict(extra="forbid")

    memory_ids: list[str] = Field(default_factory=list, max_length=100)
    episode_ids: list[str] = Field(default_factory=list, max_length=100)
    reason: str | None = Field(default=None, max_length=500)


router = APIRouter(prefix="/api/v1/profiles/{profile_id}", tags=["main-agent-memory"])


def _profiles(request: Request) -> Any:
    return request.app.state.main_agent_profiles


def _active(request: Request, profile_id: str) -> Any | None:
    profiles = _profiles(request)
    if profiles.active_profile_id != profile_id:
        return None
    return profiles


@router.get("/memories")
async def browse_memories(profile_id: str, request: Request, limit: int = 50) -> Any:
    if not 1 <= limit <= 100:
        return error_response(400, "invalid_memory_request", "limit must be from 1 to 100")
    profiles = _active(request, profile_id)
    if profiles is None:
        return error_response(409, "profile_not_active", "Profile is not active.")
    return await profiles.browse_memory(profile_id, limit)


@router.post("/memories/forget")
async def forget_memories(profile_id: str, body: ForgetMemoriesRequest, request: Request) -> Any:
    profiles = _active(request, profile_id)
    if profiles is None:
        return error_response(409, "profile_not_active", "Profile is not active.")
    if not body.memory_ids and not body.episode_ids:
        return error_response(400, "invalid_memory_request", "memoryIds or episodeIds is required")
    try:
        return await profiles.forget_memory(
            profile_id, memory_ids=tuple(body.memory_ids), episode_ids=tuple(body.episode_ids),
            reason=body.reason,
        )
    except ValueError as exc:
        return error_response(400, "invalid_memory_request", str(exc))


@router.post("/memory-jobs/{job_id}/retry")
async def retry_memory_job(profile_id: str, job_id: str, request: Request) -> Any:
    profiles = _active(request, profile_id)
    if profiles is None:
        return error_response(409, "profile_not_active", "Profile is not active.")
    try:
        return await profiles.retry_memory(profile_id, job_id)
    except KeyError:
        return error_response(404, "memory_job_not_found", "Memory job was not found or is not failed.")


__all__ = ["ForgetMemoriesRequest", "router"]
