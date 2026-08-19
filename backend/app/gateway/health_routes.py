"""
提供与 Task runtime 无关的 Backend 进程健康检查。

路由只返回稳定的服务身份与存活状态，由 backend.main 挂载；它不读取模型、Memory、
TaskManager 或外部依赖健康，因此可作为桌面启动器的最小就绪信号。
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, object]:
    """Return the stable health-only response."""

    return {"ok": True, "service": "backend"}


__all__ = ["health", "router"]
