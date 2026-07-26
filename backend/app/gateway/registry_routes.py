"""Reserved HTTP namespace for future capability registry operations."""

from fastapi import APIRouter

router = APIRouter(prefix="/registry", tags=["registry"])

__all__ = ["router"]
