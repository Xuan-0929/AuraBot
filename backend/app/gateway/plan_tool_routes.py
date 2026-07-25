"""Loopback-only backend endpoints used by AuraBot native Plan tools."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ...agent.task.TaskManager import TaskManager
from ...agent.task.harness.Plan import (
    PlanOperationError,
    PlanStepDefinition,
)
from ...agent.task.harness.PlanAndSolveHarness import PlanAndSolveHarness
from .plan_tool_models import (
    CompletePlanStepBody,
    CreatePlanBody,
    ReadPlanBody,
    UpdatePlanBody,
)


router = APIRouter(prefix="/tool", include_in_schema=False)

ERROR_STATUS = {
    "invalid_plan": 400,
    "task_not_found": 404,
    "plan_not_found": 404,
    "step_not_found": 404,
    "invalid_task_type": 409,
    "invalid_task_state": 409,
    "plan_already_exists": 409,
    "completed_step_immutable": 409,
    "step_not_current": 409,
    "step_already_completed": 409,
}
ROUTING_ERROR_MESSAGES = {
    "task_not_found": "No active or retained Task runtime has that name.",
    "invalid_task_type": "The requested Task runtime is not Plan-and-Solve.",
}


def get_task_manager() -> TaskManager:
    return TaskManager()


TaskManagerDependency = Annotated[TaskManager, Depends(get_task_manager)]


@router.post("/create_plan")
def create_plan(
    body: CreatePlanBody,
    manager: TaskManagerDependency,
):
    harness_or_error = _get_plan_harness(manager, body.task_name)
    if isinstance(harness_or_error, JSONResponse):
        return harness_or_error
    try:
        result = harness_or_error.create_plan(
            body.goal,
            _definitions(body.steps),
        )
    except PlanOperationError as exc:
        return _plan_error(exc)
    return asdict(result)


@router.post("/read_plan")
def read_plan(
    body: ReadPlanBody,
    manager: TaskManagerDependency,
):
    harness_or_error = _get_plan_harness(manager, body.task_name)
    if isinstance(harness_or_error, JSONResponse):
        return harness_or_error
    try:
        result = harness_or_error.read_plan()
    except PlanOperationError as exc:
        return _plan_error(exc)
    return asdict(result)


@router.post("/update_plan")
def update_plan(
    body: UpdatePlanBody,
    manager: TaskManagerDependency,
):
    harness_or_error = _get_plan_harness(manager, body.task_name)
    if isinstance(harness_or_error, JSONResponse):
        return harness_or_error
    try:
        result = harness_or_error.update_plan(
            body.goal,
            _definitions(body.steps),
        )
    except PlanOperationError as exc:
        return _plan_error(exc)
    return asdict(result)


@router.post("/plan_step_complete")
def plan_step_complete(
    body: CompletePlanStepBody,
    manager: TaskManagerDependency,
):
    harness_or_error = _get_plan_harness(manager, body.task_name)
    if isinstance(harness_or_error, JSONResponse):
        return harness_or_error
    try:
        result = harness_or_error.complete_plan_step(
            body.step_id,
            body.completion_summary,
        )
    except PlanOperationError as exc:
        return _plan_error(exc)
    return asdict(result)


def _definitions(models) -> tuple[PlanStepDefinition, ...]:
    return tuple(
        PlanStepDefinition(**model.model_dump())
        for model in models
    )


def _get_plan_harness(
    manager: TaskManager,
    task_name: str,
) -> PlanAndSolveHarness | JSONResponse:
    runtime = manager.get_runtime(task_name)
    if runtime is None:
        return _error_response(
            "task_not_found",
            ROUTING_ERROR_MESSAGES["task_not_found"],
        )
    if not isinstance(runtime, PlanAndSolveHarness):
        return _error_response(
            "invalid_task_type",
            ROUTING_ERROR_MESSAGES["invalid_task_type"],
        )
    return runtime


def _plan_error(error: PlanOperationError) -> JSONResponse:
    return _error_response(error.code, error.message)


def _error_response(code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=ERROR_STATUS[code],
        content={"error": {"code": code, "message": message}},
    )


__all__ = [
    "ERROR_STATUS",
    "ROUTING_ERROR_MESSAGES",
    "get_task_manager",
    "router",
]
