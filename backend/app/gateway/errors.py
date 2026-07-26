"""
负责把稳定 ApplicationError 语义转换为 HTTP 错误响应。

gateway route 捕获 service 层错误后交给本模块选择状态码和结构化 payload；
本模块不捕获领域异常、不执行业务操作，也不泄漏内部 runtime record。
"""

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
