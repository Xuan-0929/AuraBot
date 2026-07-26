import asyncio
from collections.abc import Sequence
from threading import RLock
from typing import Literal

from ...LLMClient import Message, OpenAITool
from ...tools.core.ToolCallEntity import (
    FinalResponse,
    ToolCallBatch,
    ToolExecutionResult,
)
from ...tools.native.PlanToolGroup import (
    PLAN_TOOL_NAMES,
    create_plan_tool_group,
)
from ..Task import TaskInactiveError
from ..TaskEntity import TaskState
from .Harness import Harness, HarnessInterruptedError
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


class PlanAndSolveLimitError(RuntimeError):
    """Raised when the Plan-and-Solve loop exhausts an execution limit."""


class PlanAndSolveFinalResponseError(ValueError):
    """Raised when a final response violates the declared status protocol."""


PlanFinalStatus = Literal["completed", "failed", "waiting_for_info"]


class PlanAndSolveHarness(Harness):
    """Task-local planning and checkpoint loop."""

    MAX_PLAN_ROUNDS = 60
    MAX_TOOL_CALLS = 100
    PLAN_PROGRESS_GRACE_ROUNDS = 5

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

        if self.capability_session is not None:
            required = self.capability_session.acquire_tool_groups(("Plan",))
        else:
            required = (create_plan_tool_group(),)
        self._inject_required_tool_groups(
            required
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

    async def run(self) -> str:
        """Run a bounded planning/checkpoint loop and retain Task context."""

        if self.task.state is TaskState.Interrupted:
            raise HarnessInterruptedError
        if self.task.state is not TaskState.Initializing:
            raise RuntimeError(
                "PlanAndSolveHarness.run() can only execute a new Task"
            )

        runner = asyncio.current_task()
        if runner is None or not self.task.bind_runner(runner):
            raise RuntimeError(
                "Plan-and-Solve Task already has an active Harness"
            )

        try:
            openai_tools = self.build_openai_tools()
            self._initialize_context()
            self._transition_task_state(
                TaskState.Initializing,
                self._reasoning_state(),
            )

            result = await self._run_plan_loop(openai_tools)
        except asyncio.CancelledError as exc:
            # TaskManager owns the wall-clock timeout. External interrupts
            # first commit TaskState.Interrupted and then cancel the runner,
            # allowing timeout cancellation to propagate without corrupting
            # the terminal state.
            if isinstance(exc, HarnessInterruptedError):
                raise
            if self.task.state is TaskState.Interrupted:
                raise HarnessInterruptedError from exc
            raise
        except Exception:
            self.task.compare_and_set_state(
                {
                    TaskState.Initializing,
                    TaskState.Planning,
                    TaskState.Thinking,
                    TaskState.Pushing,
                },
                TaskState.Failed,
            )
            raise
        finally:
            self.task.unbind_runner(runner)

        return result

    def _initialize_context(self) -> None:
        self.task.messages.extend(
            [
                {"role": "system", "content": self.build_system_prompt()},
                {"role": "user", "content": self.build_user_prompt()},
            ]
        )

    async def _run_plan_loop(
        self,
        openai_tools: list[OpenAITool],
    ) -> str:
        tool_call_count = 0
        rounds_without_successful_plan_tool = 0
        force_plan_reminder = False

        for _ in range(self.MAX_PLAN_ROUNDS):
            reasoning_state = self._reasoning_state()
            self._require_task_state(reasoning_state)
            request_messages = self._build_request_messages(
                rounds_without_successful_plan_tool,
                force_plan_reminder,
            )
            choice = await self.llm_client.invoke(
                request_messages,
                tools=openai_tools or None,
                tool_choice="auto" if openai_tools else None,
            )
            self._transition_task_state(
                reasoning_state,
                TaskState.Pushing,
            )
            self.task.messages.append(dict(choice.message))
            response = self.parse_completion(choice)

            if isinstance(response, FinalResponse):
                final_status = self._parse_final_status(response.content)
                if final_status in {"failed", "waiting_for_info"}:
                    self._transition_task_state(
                        TaskState.Pushing,
                        TaskState.Failed,
                    )
                    return response.content

                plan = self.plan
                if plan is not None and plan.is_complete:
                    self._transition_task_state(
                        TaskState.Pushing,
                        TaskState.Completed,
                    )
                    return response.content

                rounds_without_successful_plan_tool += 1
                force_plan_reminder = True
                self._transition_task_state(
                    TaskState.Pushing,
                    self._reasoning_state(),
                )
                continue

            if not isinstance(response, ToolCallBatch):
                raise TypeError(
                    "unsupported Plan-and-Solve model response"
                )

            tool_call_count += len(response.calls)
            if tool_call_count > self.MAX_TOOL_CALLS:
                raise PlanAndSolveLimitError(
                    "Plan-and-Solve tool-call limit exceeded "
                    f"({self.MAX_TOOL_CALLS})"
                )

            resolved_calls = self.resolve_and_adapt_tool_calls(response.calls)
            results = await self.tool_call_gateway.execute_many(resolved_calls)
            self._require_task_state(TaskState.Pushing)
            self.task.messages.extend(
                self.build_tool_result_messages(results)
            )

            if self._has_successful_plan_tool_result(results):
                rounds_without_successful_plan_tool = 0
                force_plan_reminder = False
            else:
                rounds_without_successful_plan_tool += 1

            self._transition_task_state(
                TaskState.Pushing,
                self._reasoning_state(),
            )

        raise PlanAndSolveLimitError(
            "Plan-and-Solve round limit exceeded "
            f"({self.MAX_PLAN_ROUNDS})"
        )

    def _reasoning_state(self) -> TaskState:
        return (
            TaskState.Planning
            if self.plan is None
            else TaskState.Thinking
        )

    def _build_request_messages(
        self,
        rounds_without_successful_plan_tool: int,
        force_plan_reminder: bool,
    ) -> list[Message]:
        plan = self.plan
        if plan is not None and plan.is_complete:
            return self.task.messages
        if (
            not force_plan_reminder
            and rounds_without_successful_plan_tool
            < self.PLAN_PROGRESS_GRACE_ROUNDS
        ):
            return self.task.messages
        return [
            *self.task.messages,
            {
                "role": "system",
                "content": self._build_plan_progress_reminder(plan),
            },
        ]

    @staticmethod
    def _build_plan_progress_reminder(
        plan: PlanSnapshot | None,
    ) -> str:
        if plan is None:
            return (
                "Plan progress reminder: no initial runtime Plan exists. "
                "Call create_plan now with a finite, executable plan before "
                "continuing. Do not claim completion without a completed Plan."
            )
        return (
            "Plan progress reminder: advance the runtime Plan using a Plan "
            f"Tool. The current pending step is {plan.current_step_id!r}. "
            "Use read_plan or update_plan when needed, and call "
            "plan_step_complete only after observing the step's real result. "
            "Do not invent progress or claim completion while steps remain."
        )

    @staticmethod
    def _has_successful_plan_tool_result(
        results: Sequence[ToolExecutionResult],
    ) -> bool:
        return any(
            result.tool_name in PLAN_TOOL_NAMES and not result.is_error
            for result in results
        )

    @staticmethod
    def _parse_final_status(content: str) -> PlanFinalStatus:
        first_non_empty_line = next(
            (line.strip() for line in content.splitlines() if line.strip()),
            None,
        )
        statuses: dict[str, PlanFinalStatus] = {
            "Status: completed": "completed",
            "Status: failed": "failed",
            "Status: waiting_for_info": "waiting_for_info",
        }
        try:
            return statuses[first_non_empty_line]
        except (KeyError, TypeError) as exc:
            raise PlanAndSolveFinalResponseError(
                "Plan-and-Solve final response must begin with exactly "
                "'Status: completed' or 'Status: failed'"
            ) from exc


PlanAndSolveInterruptedError = HarnessInterruptedError


__all__ = [
    "PlanAndSolveFinalResponseError",
    "PlanAndSolveHarness",
    "PlanAndSolveInterruptedError",
    "PlanAndSolveLimitError",
]
