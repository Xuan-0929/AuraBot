"""Plan-and-Solve Harness implementation."""

import asyncio
from collections.abc import Sequence

from ...llm_client import Message, OpenAITool
from ...tools.core.model import (
    FinalResponse,
    ToolCallBatch,
    ToolExecutionResult,
)
from ...tools.native.plan import (
    PLAN_TOOL_GROUP_NAME,
    PLAN_TOOL_NAMES,
)
from ..model import TaskState
from .harness import Harness, HarnessInterruptedError
from .plan import (
    CompletePlanStepResult,
    CreatePlanResult,
    PlanSnapshot,
    PlanStepDefinition,
    UpdatePlanResult,
)
from .planning import PlanningRuntime
from .plan_response import (
    PlanAndSolveFinalResponseError,
    parse_plan_final_status,
)
from .prompts import system_prompt_PlanAndSolve
from .utils import build_common_prompt_values


class PlanAndSolveLimitError(RuntimeError):
    """Raised when the Plan-and-Solve loop exhausts an execution limit."""


class PlanAndSolveHarness(Harness):
    """Task-local planning and step-progress loop."""

    MAX_PLAN_ROUNDS = 60
    MAX_TOOL_CALLS = 100
    PLAN_PROGRESS_GRACE_ROUNDS = 5
    required_tool_group_names = (PLAN_TOOL_GROUP_NAME,)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._planning_runtime = PlanningRuntime(self.task)

    @property
    def planning_runtime(self) -> PlanningRuntime:
        return self._planning_runtime

    @property
    def plan(self) -> PlanSnapshot | None:
        """Return the latest immutable Plan snapshot."""

        return self.planning_runtime.plan

    def create_plan(
        self,
        goal: str,
        steps: Sequence[PlanStepDefinition],
    ) -> CreatePlanResult:
        return self.planning_runtime.create(goal, steps)

    def read_plan(self) -> PlanSnapshot:
        return self.planning_runtime.read()

    def update_plan(
        self,
        goal: str,
        steps: Sequence[PlanStepDefinition],
    ) -> UpdatePlanResult:
        return self.planning_runtime.update(goal, steps)

    def complete_plan_step(
        self,
        step_id: str,
        completion_summary: str,
    ) -> CompletePlanStepResult:
        return self.planning_runtime.complete_step(
            step_id,
            completion_summary,
        )

    def build_system_prompt(self) -> str:
        """Build the Plan-and-Solve prompt with the native format API."""

        return system_prompt_PlanAndSolve.format(
            **build_common_prompt_values(self.task)
        ).strip()

    async def run(self) -> str:
        """Run a bounded planning loop and retain Task context."""

        if self.task.state is TaskState.Interrupted:
            raise HarnessInterruptedError
        if self.task.state is not TaskState.Initializing:
            raise RuntimeError(
                "PlanAndSolveHarness.run() can only execute a new Task"
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
        return result

    def _initialize_context(self) -> None:
        self.task.initialize_messages(
            (
                {"role": "system", "content": self.build_system_prompt()},
                {"role": "user", "content": self.build_user_prompt()},
            )
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
            self.task.append_message(dict(choice.message))
            response = self.parse_completion(choice)

            if isinstance(response, FinalResponse):
                final_status = parse_plan_final_status(response.content)
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

            results = await self.execute_tool_calls(response.calls)
            self._require_task_state(TaskState.Pushing)
            self.task.append_messages(
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
            return self.task.request_messages()
        if (
            not force_plan_reminder
            and rounds_without_successful_plan_tool
            < self.PLAN_PROGRESS_GRACE_ROUNDS
        ):
            return self.task.request_messages()
        return self.task.request_messages(
            (
                {
                "role": "system",
                "content": self._build_plan_progress_reminder(plan),
                },
            )
        )

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

PlanAndSolveInterruptedError = HarnessInterruptedError


__all__ = [
    "PlanAndSolveFinalResponseError",
    "PlanAndSolveHarness",
    "PlanAndSolveInterruptedError",
    "PlanAndSolveLimitError",
]
