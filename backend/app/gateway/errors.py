"""HTTP mapping for stable Application Service failures."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from ..services.errors import ApplicationError


async def application_error_handler(
    request: Request,
    error: ApplicationError,
) -> JSONResponse:
    del request
    return JSONResponse(
        status_code=error.status_code,
        content={
            "error": {
                "code": error.code,
                "message": error.message,
            }
        },
    )


__all__ = ["application_error_handler"]
