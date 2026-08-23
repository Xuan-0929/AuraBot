"""
定义整个 AuraBot Backend 共用的错误 envelope、响应构造器与未知异常边界。

本模块由 Backend 组合入口注册：它记录未知异常的完整 stack，并只向客户端返回脱敏
500；Task 等业务 Gateway 仅注册明确的 domain exception，并复用这里的公共 DTO。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class CamelModel(BaseModel):
    """Shared JSON alias rules for every public Backend DTO."""

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
    )


class ErrorBody(CamelModel):
    """Stable public error fields shared by every Backend module."""

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(CamelModel):
    """Top-level Backend error envelope."""

    error: ErrorBody


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    """Build one camelCase JSON error without exposing internal objects."""

    body = ErrorResponse(error=ErrorBody(code=code, message=message, details={}))
    return JSONResponse(status_code=status_code, content=body.model_dump(by_alias=True))


def register_backend_exception_handlers(application: FastAPI) -> None:
    """Install the final sanitized boundary for exceptions no domain owns."""

    async def unexpected(_request: Request, exception: Exception) -> JSONResponse:
        logger.exception("Unexpected Backend request failure", exc_info=exception)
        return error_response(
            500, "internal_error", "Backend request failed internally."
        )

    application.add_exception_handler(Exception, unexpected)


__all__ = [
    "ErrorBody",
    "ErrorResponse",
    "CamelModel",
    "error_response",
    "register_backend_exception_handlers",
]
