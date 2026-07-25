from collections.abc import Sequence
from threading import RLock

from ...tools.native.PlanToolGroup import create_plan_tool_group
from ..Task import TaskInactiveError
from .Harness import Harness
from .Plan import (
    CompletePlanStepResult,
    CreatePlanResult,
    PlanOperationError,
    PlanSnapshot,
    PlanStep,
    PlanStepDefinition,
    UpdatePlanResult,
    build_snapshot,
    validate_plan_definition,
)
from .prompts import system_prompt_PlanAndSolve
from .utils import build_common_prompt_values


class PlanAndSolveHarness(Harness):
    """Task-local planning and checkpoint loop."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._plan: PlanSnapshot | None = None
        self._plan_lock = RLock()

    @property
    def plan(self) -> PlanSnapshot | None:
        """Return the latest immutable Plan snapshot."""

        with self._plan_lock:
            return self._plan

    def inject_tool_group(self) -> None:
        """Install the canonical native Plan tools required by this Harness."""

        self._inject_required_tool_groups(
            (
                create_plan_tool_group(),
            )
        )

    def create_plan(
        self,
        goal: str,
        steps: Sequence[PlanStepDefinition],
    ) -> CreatePlanResult:
        definitions = self._normalize_definitions(steps)
        validate_plan_definition(goal, definitions)

        try:
            with self.task.active_state_guard(), self._plan_lock:
                if self._plan is not None:
                    if self._plan.is_complete:
                        raise PlanOperationError("invalid_task_state")
                    if (
                        self._plan.goal != goal
                        or tuple(
                            step.definition for step in self._plan.steps
                        ) != definitions
                    ):
                        raise PlanOperationError("plan_already_exists")
                    return CreatePlanResult(
                        current_step_id=self._require_current_step_id(
                            self._plan
                        ),
                        total_step_count=self._plan.total_step_count,
                    )

                plan_steps = tuple(
                    PlanStep(
                        id=step.id,
                        title=step.title,
                        description=step.description,
                        expected_output=step.expected_output,
                        status="pending",
                        completion_summary=None,
                    )
                    for step in definitions
                )
                self._plan = build_snapshot(goal, plan_steps)
                return CreatePlanResult(
                    current_step_id=self._require_current_step_id(self._plan),
                    total_step_count=self._plan.total_step_count,
                )
        except TaskInactiveError as exc:
            raise PlanOperationError("invalid_task_state") from exc

    def read_plan(self) -> PlanSnapshot:
        try:
            with self.task.active_state_guard(), self._plan_lock:
                if self._plan is None:
                    raise PlanOperationError("plan_not_found")
                return self._plan
        except TaskInactiveError as exc:
            raise PlanOperationError("invalid_task_state") from exc

    def update_plan(
        self,
        goal: str,
        steps: Sequence[PlanStepDefinition],
    ) -> UpdatePlanResult:
        definitions = self._normalize_definitions(steps)
        validate_plan_definition(goal, definitions)

        try:
            with self.task.active_state_guard(), self._plan_lock:
                current = self._plan
                if current is None:
                    raise PlanOperationError("plan_not_found")
                if current.is_complete:
                    raise PlanOperationError("invalid_task_state")

                completed_count = current.completed_step_count
                completed_definitions = tuple(
                    step.definition for step in current.steps[:completed_count]
                )
                if (
                    len(definitions) < completed_count
                    or definitions[:completed_count] != completed_definitions
                ):
                    raise PlanOperationError("completed_step_immutable")
                if len(definitions) == completed_count:
                    raise PlanOperationError("invalid_plan")

                completed_steps = current.steps[:completed_count]
                pending_steps = tuple(
                    PlanStep(
                        id=step.id,
                        title=step.title,
                        description=step.description,
                        expected_output=step.expected_output,
                        status="pending",
                        completion_summary=None,
                    )
                    for step in definitions[completed_count:]
                )
                self._plan = build_snapshot(
                    goal,
                    (*completed_steps, *pending_steps),
                )
                return UpdatePlanResult(
                    current_step_id=self._require_current_step_id(self._plan),
                    total_step_count=self._plan.total_step_count,
                )
        except TaskInactiveError as exc:
            raise PlanOperationError("invalid_task_state") from exc

    def complete_plan_step(
        self,
        step_id: str,
        completion_summary: str,
    ) -> CompletePlanStepResult:
        if (
            not isinstance(step_id, str)
            or not isinstance(completion_summary, str)
            or not completion_summary
        ):
            raise PlanOperationError("invalid_plan")

        try:
            with self.task.active_state_guard(), self._plan_lock:
                current = self._plan
                if current is None:
                    raise PlanOperationError("plan_not_found")

                matching_index = next(
                    (
                        index
                        for index, step in enumerate(current.steps)
                        if step.id == step_id
                    ),
                    None,
                )
                if matching_index is None:
                    raise PlanOperationError("step_not_found")
                matching_step = current.steps[matching_index]
                if matching_step.status == "completed":
                    if matching_step.completion_summary != completion_summary:
                        raise PlanOperationError("step_already_completed")
                    return CompletePlanStepResult(
                        completed_step_id=step_id,
                        next_step_id=current.current_step_id,
                        is_complete=current.is_complete,
                    )
                if step_id != current.current_step_id:
                    raise PlanOperationError("step_not_current")

                completed_step = PlanStep(
                    id=matching_step.id,
                    title=matching_step.title,
                    description=matching_step.description,
                    expected_output=matching_step.expected_output,
                    status="completed",
                    completion_summary=completion_summary,
                )
                updated_steps = (
                    *current.steps[:matching_index],
                    completed_step,
                    *current.steps[matching_index + 1 :],
                )
                self._plan = build_snapshot(current.goal, updated_steps)
                return CompletePlanStepResult(
                    completed_step_id=step_id,
                    next_step_id=self._plan.current_step_id,
                    is_complete=self._plan.is_complete,
                )
        except TaskInactiveError as exc:
            raise PlanOperationError("invalid_task_state") from exc

    @staticmethod
    def _require_current_step_id(plan: PlanSnapshot) -> str:
        if plan.current_step_id is None:
            raise PlanOperationError("invalid_task_state")
        return plan.current_step_id

    @staticmethod
    def _normalize_definitions(
        steps: Sequence[PlanStepDefinition],
    ) -> tuple[PlanStepDefinition, ...]:
        try:
            return tuple(steps)
        except TypeError as exc:
            raise PlanOperationError("invalid_plan") from exc

    def build_system_prompt(self) -> str:
        """Build the Plan-and-Solve prompt with the native format API."""

        return system_prompt_PlanAndSolve.format(
            **build_common_prompt_values(self.task)
        ).strip()

    async def run(self) -> object:
        raise NotImplementedError(
            "Plan-and-Solve agent loop is not implemented yet"
        )
