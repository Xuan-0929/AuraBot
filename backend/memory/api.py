from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .embedding import create_embedding_provider
from .service import MemoryService
from .stores.qdrant import QdrantLocalVectorStore
from .stores.sqlite import SQLiteMemoryStore


class RememberRequest(BaseModel):
    profile_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    source_id: str | None = None
    role: str | None = None
    memory_type: str | None = None
    importance: float = Field(default=0.5, ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RecallRequest(BaseModel):
    profile_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    limit: int = Field(default=8, ge=1, le=50)
    filters: dict[str, Any] = Field(default_factory=dict)
    context_hints: dict[str, Any] = Field(default_factory=dict)


class ForgetRequest(BaseModel):
    profile_id: str = Field(min_length=1)
    memory_ids: list[str] = Field(default_factory=list)
    episode_ids: list[str] = Field(default_factory=list)
    reason: str | None = None


class ImproveRequest(BaseModel):
    profile_id: str = Field(min_length=1)


def create_memory_router(service: MemoryService) -> APIRouter:
    router = APIRouter(prefix="/memory", tags=["memory"])

    @router.post("/remember")
    def remember(request: RememberRequest) -> dict[str, Any]:
        try:
            return service.remember(**request.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/recall")
    def recall(request: RecallRequest) -> dict[str, Any]:
        try:
            return service.recall(**request.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/forget")
    def forget(request: ForgetRequest) -> dict[str, Any]:
        try:
            return service.forget(**request.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/improve")
    def improve(request: ImproveRequest) -> dict[str, Any]:
        try:
            return service.improve(request.profile_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get("/due")
    def due(
        profile_id: str = Query(min_length=1),
        now_iso: str = Query(min_length=1),
    ) -> dict[str, Any]:
        try:
            return service.due(profile_id, now_iso)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get("/debug/{profile_id}")
    def debug(profile_id: str) -> dict[str, Any]:
        try:
            return service.debug(profile_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get("/health")
    def health() -> dict[str, Any]:
        return service.health()

    return router


def create_default_memory_service(
    base_path: str | Path = "data/memory",
    embedding_config: dict[str, Any] | None = None,
) -> MemoryService:
    base_path = Path(base_path)
    embedding_provider = create_embedding_provider(embedding_config)

    return MemoryService(
        metadata_store=SQLiteMemoryStore(base_path / "memory.sqlite3"),
        vector_store=QdrantLocalVectorStore(
            path=base_path / "qdrant",
            vector_size=embedding_provider.dimension,
        ),
        embedding_provider=embedding_provider,
    )
