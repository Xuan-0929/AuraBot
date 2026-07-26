"""
负责 AuraBot 原生 Plan Tool 的 loopback-only HTTP 路由。

route 将严格请求模型转换为 PlanStepDefinition 并委托应用服务，返回领域结果字典；
本模块不实现 Plan 生命周期、不查找 Task runtime，也不暴露公开用户 API。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from ...agent.task.domain.plan import PlanStepDefinition
from ..services.plan_tool_service import PlanToolApplicationService
from .dependencies import get_plan_tool_service
from .plan_tool_models import (
    CompletePlanStepBody,
    CreatePlanBody,
    ReadPlanBody,
    UpdatePlanBody,
)

router = APIRouter(prefix="/tool/plan", include_in_schema=False)
PlanServiceDependency = Annotated[
    PlanToolApplicationService,
    Depends(get_plan_tool_service),
]


@router.post("/create")
async def create_plan(
    body: CreatePlanBody,
    service: PlanServiceDependency,
):
    return service.create(
        body.task_name,
        body.goal,
        _definitions(body.steps),
    )


@router.post("/read")
async def read_plan(
    body: ReadPlanBody,
    service: PlanServiceDependency,
):
    return service.read(body.task_name)


@router.post("/update")
async def update_plan(
    body: UpdatePlanBody,
    service: PlanServiceDependency,
):
    return service.update(
        body.task_name,
        body.goal,
        _definitions(body.steps),
    )


@router.post("/step/complete")
async def plan_step_complete(
    body: CompletePlanStepBody,
    service: PlanServiceDependency,
):
    return service.complete_step(
        body.task_name,
        body.step_id,
        body.completion_summary,
    )


def _definitions(models) -> tuple[PlanStepDefinition, ...]:
    return tuple(
        PlanStepDefinition(**model.model_dump())
        for model in models
    )


__all__ = ["router"]
