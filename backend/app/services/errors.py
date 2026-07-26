"""Stable errors exposed by the backend transport boundary."""

from __future__ import annotations


class ApplicationError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


__all__ = ["ApplicationError"]
