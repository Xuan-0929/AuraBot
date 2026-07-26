"""
提供 Task 启动、列表、详情与中断的公共 HTTP transport。

route 使用 Pydantic model 校验输入，调用 TaskApplicationService，再把不可变 snapshot
映射为响应；本模块不访问内部 record、不调度 Harness，也不拥有错误业务语义。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from ..services.errors import ApplicationError
from ..services.task_service import TaskApplicationService
from .dependencies import get_task_service
from .task_models import CreateTaskBody, TaskListResponse, TaskView

router = APIRouter(prefix="/task", tags=["task"])
TaskServiceDependency = Annotated[
    TaskApplicationService,
    Depends(get_task_service),
]


@router.post("", response_model=TaskView, status_code=status.HTTP_202_ACCEPTED)
async def create_task(
    body: CreateTaskBody,
    service: TaskServiceDependency,
):
    try:
        return TaskView.from_snapshot(service.start(body.to_spec()))
    except (TypeError, ValueError) as exc:
        raise ApplicationError(
            "invalid_task_request",
            "The Task request is not valid for this runtime.",
        ) from exc


@router.get("", response_model=TaskListResponse)
async def list_tasks(service: TaskServiceDependency):
    return TaskListResponse.from_snapshot(service.list())


@router.get("/{task_name}", response_model=TaskView)
async def get_task(task_name: str, service: TaskServiceDependency):
    return TaskView.from_snapshot(service.get(task_name))


@router.post("/{task_name}/interrupt", response_model=TaskView)
async def interrupt_task(task_name: str, service: TaskServiceDependency):
    return TaskView.from_snapshot(service.interrupt(task_name))


@router.delete("/{task_name}", status_code=status.HTTP_204_NO_CONTENT)
async def clear_task(
    task_name: str,
    service: TaskServiceDependency,
) -> Response:
    service.clear(task_name)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
