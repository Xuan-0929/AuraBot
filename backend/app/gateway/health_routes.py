"""
提供 Backend 进程健康状态与 Task 准入可用性的只读 HTTP route。

route 从应用依赖读取 BackendRuntime 当前生命周期状态并构造轻量响应；
本模块不启动或关闭 runtime，也不执行 Task 查询和调度。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from ..runtime import BackendRuntime
from .dependencies import get_backend_runtime

router = APIRouter()
RuntimeDependency = Annotated[BackendRuntime, Depends(get_backend_runtime)]


@router.get("/health")
async def health(runtime: RuntimeDependency) -> dict[str, object]:
    return {
        "ok": True,
        "service": "backend",
        "accepting_tasks": not runtime.task_manager.closing,
        "running_count": runtime.task_manager.running_count,
        "queued_count": runtime.task_manager.queued_count,
    }


__all__ = ["health", "router"]
