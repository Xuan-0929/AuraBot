"""Strict internal HTTP models for native Plan tools."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ...agent.tools.native.plan import STEP_ID_PATTERN


class InternalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanStepDefinitionBody(InternalModel):
    id: str = Field(pattern=STEP_ID_PATTERN)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    expected_output: str = Field(min_length=1)


class CreatePlanBody(InternalModel):
    task_name: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    steps: list[PlanStepDefinitionBody] = Field(
        min_length=1,
        max_length=30,
    )


class ReadPlanBody(InternalModel):
    task_name: str = Field(min_length=1)


class UpdatePlanBody(InternalModel):
    task_name: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    steps: list[PlanStepDefinitionBody] = Field(
        min_length=1,
        max_length=30,
    )


class CompletePlanStepBody(InternalModel):
    task_name: str = Field(min_length=1)
    step_id: str = Field(pattern=STEP_ID_PATTERN)
    completion_summary: str = Field(min_length=1)


__all__ = [
    "CompletePlanStepBody",
    "CreatePlanBody",
    "PlanStepDefinitionBody",
    "ReadPlanBody",
    "UpdatePlanBody",
]
