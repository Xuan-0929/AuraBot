"""HTTP mapping for stable Application Service failures."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from ..services.errors import ApplicationError

APPLICATION_ERROR_STATUS = {
    "invalid_plan": 400,
    "invalid_task_request": 400,
    "task_not_found": 404,
    "plan_not_found": 404,
    "step_not_found": 404,
    "completed_step_immutable": 409,
    "invalid_task_state": 409,
    "invalid_task_type": 409,
    "plan_already_exists": 409,
    "step_already_completed": 409,
    "step_not_current": 409,
    "task_name_conflict": 409,
    "task_not_terminal": 409,
    "runtime_closing": 503,
}


async def application_error_handler(
    request: Request,
    error: ApplicationError,
) -> JSONResponse:
    del request
    return JSONResponse(
        status_code=APPLICATION_ERROR_STATUS.get(error.code, 500),
        content={
            "error": {
                "code": error.code,
                "message": error.message,
            }
        },
    )


__all__ = [
    "APPLICATION_ERROR_STATUS",
    "application_error_handler",
]
