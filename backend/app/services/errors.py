"""Stable errors exposed by the backend transport boundary."""

from __future__ import annotations


class ApplicationError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


__all__ = ["ApplicationError"]
