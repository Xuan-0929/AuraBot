"""
验证 Plan-and-Solve agent loop 的轮次、工具进度、最终响应与中断收敛。

测试以脚本化 LLM 和内存工具驱动真实 Harness，不覆盖外部网络或生产调度并发。
"""

import asyncio
import unittest
from unittest.mock import patch

from backend.agent.llm_client import ChatCompletionChoice
from backend.agent.task.domain import Task, TaskState, TaskType
from backend.agent.task.manager import TaskManager
from backend.agent.task.runtime import InterruptTaskOutcome
from backend.agent.task.domain.plan import PlanStepDefinition
from backend.agent.task.harness.plan_and_solve import (
    PlanAndSolveFinalResponseError,
    PlanAndSolveHarness,
    PlanAndSolveInterruptedError,
    PlanAndSolveLimitError,
)
from backend.agent.tools.tool import Tool
from backend.agent.tools.tool_group import ToolGroup
from backend.agent.tools.core.model import ToolExecutionResult
from backend.tests.task_runtime_helpers import retain_test_harness


SERVER_URL = "http://127.0.0.1:6410/mcp"
EMPTY_INPUT_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}
ONE_STEP = {
    "id": "s1",
    "title": "Build",
    "description": "Build and verify the result",
    "expected_output": "Verified result",
}


class RecordingTask(Task):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.state_history = [self.state]

    def compare_and_set_state(self, expected, target) -> bool:
        previous = self.state
        transitioned = super().compare_and_set_state(expected, target)
        if self.state is not previous:
            self.state_history.append(self.state)
        return transitioned


class ScriptedLLMClient:
    def __init__(
        self,
        choices=None,
        *,
        error: Exception | None = None,
    ):
        self.choices = list(choices or [])
        self.error = error
        self.requests = []

    async def invoke(self, messages, *, tools=None, tool_choice=None):
        self.requests.append(
            {
                "messages": [dict(message) for message in messages],
                "tools": tools,
                "tool_choice": tool_choice,
            }
        )
        if self.error is not None:
            raise self.error
        return self.choices.pop(0)


class PlanAwareGateway:
    def __init__(self, *, failed_plan_results: int = 0):
        self.harness = None
        self.failed_plan_results = failed_plan_results
        self.batches = []

    async def execute_many(self, calls):
        self.batches.append(list(calls))
        results = []
        for call in calls:
            if (
                call.tool.name
                in {
                    "create_plan",
                    "read_plan",
                    "update_plan",
                    "plan_step_complete",
                }
                and self.failed_plan_results > 0
            ):
                self.failed_plan_results -= 1
                results.append(result(call.tool.name, call.call_id, error=True))
                continue
            structured_content = self._execute(call)
            results.append(
                result(
                    call.tool.name,
                    call.call_id,
                    structured_content=structured_content,
                )
            )
        return results

    def _execute(self, call):
        if call.tool.name == "create_plan":
            response = self.harness.create_plan(
                call.arguments["goal"],
                definitions(call.arguments["steps"]),
            )
            return {
                "created": response.created,
                "current_step_id": response.current_step_id,
                "total_step_count": response.total_step_count,
            }
        if call.tool.name == "read_plan":
            plan = self.harness.read_plan()
            return {
                "goal": plan.goal,
                "current_step_id": plan.current_step_id,
                "completed_step_count": plan.completed_step_count,
                "total_step_count": plan.total_step_count,
                "is_complete": plan.is_complete,
            }
        if call.tool.name == "update_plan":
            response = self.harness.update_plan(
                call.arguments["goal"],
                definitions(call.arguments["steps"]),
            )
            return {
                "updated": response.updated,
                "current_step_id": response.current_step_id,
                "total_step_count": response.total_step_count,
            }
        if call.tool.name == "plan_step_complete":
            response = self.harness.complete_plan_step(
                call.arguments["step_id"],
                call.arguments["completion_summary"],
            )
            return {
                "completed": response.completed,
                "completed_step_id": response.completed_step_id,
                "next_step_id": response.next_step_id,
                "is_complete": response.is_complete,
            }
        return {"ok": True}


def definitions(raw_steps):
    return tuple(
        PlanStepDefinition(
            id=step["id"],
            title=step["title"],
            description=step["description"],
            expected_output=step["expected_output"],
        )
        for step in raw_steps
    )


def result(
    tool_name,
    call_id,
    *,
    structured_content=None,
    error=False,
):
    return ToolExecutionResult(
        tool_name=tool_name,
        call_id=call_id,
        content=[
            {
                "type": "text",
                "text": "error" if error else "ok",
            }
        ],
        structured_content=(
            {"error": {"code": "tool_error"}}
            if error
            else structured_content
        ),
        is_error=error,
        error_code="tool_error" if error else None,
    )


def final_choice(status="completed", summary="Done"):
    return ChatCompletionChoice(
        message={
            "role": "assistant",
            "content": (
                f"Status: {status}\n"
                f"Summary: {summary}\n"
                "Artifacts: none\n"
                "Verification: verified\n"
                "Blocker: none"
            ),
        },
        finish_reason="stop",
    )


def malformed_final_choice(content):
    return ChatCompletionChoice(
        message={"role": "assistant", "content": content},
        finish_reason="stop",
    )


def tool_choice(name, arguments, call_id):
    import json

    return ChatCompletionChoice(
        message={
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(arguments),
                    },
                }
            ],
        },
        finish_reason="tool_calls",
    )


def create_choice(call_id="create"):
    return tool_choice(
        "create_plan",
        {"goal": "Ship", "steps": [ONE_STEP]},
        call_id,
    )


def complete_choice(call_id="complete"):
    return tool_choice(
        "plan_step_complete",
        {
            "step_id": "s1",
            "completion_summary": "Built and verified",
        },
        call_id,
    )


def ordinary_tool_group():
    return ToolGroup(
        "ordinary",
        [
            Tool(
                "ping",
                "Return pong",
                SERVER_URL,
                input_schema=EMPTY_INPUT_SCHEMA,
            )
        ],
        "Ordinary tools",
    )


def make_task(*, timeout_ms=60_000, ordinary=False):
    return RecordingTask(
        task_id=1,
        task_title="Plan runtime",
        parent_session_id="main-session",
        task_type=TaskType.PlanAndSolve,
        task_prompt="Build and verify the result.",
        tool_groups=[ordinary_tool_group()] if ordinary else None,
        timeout_ms=timeout_ms,
    )


def make_harness(llm_client, *, task=None, gateway=None):
    plan_task = task or make_task()
    plan_gateway = gateway or PlanAwareGateway()
    with patch(
        "backend.config.get_config",
        return_value={"local_mcp_server": SERVER_URL},
    ):
        harness = PlanAndSolveHarness(
            task=plan_task,
            llm_client=llm_client,
            tool_call_gateway=plan_gateway,
        )
    plan_gateway.harness = harness
    return harness


def has_progress_reminder(request):
    return any(
        message.get("role") == "system"
        and str(message.get("content", "")).startswith(
            "Plan progress reminder:"
        )
        for message in request["messages"]
    )


class PlanAndSolveRunTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_complete_and_final_follow_state_machine(self):
        task = make_task()
        llm = ScriptedLLMClient(
            [create_choice(), complete_choice(), final_choice()]
        )
        harness = make_harness(llm, task=task)

        response = await harness.run()

        self.assertTrue(response.startswith("Status: completed"))
        self.assertTrue(harness.plan.is_complete)
        self.assertEqual(
            task.state_history,
            [
                TaskState.Initializing,
                TaskState.Planning,
                TaskState.Pushing,
                TaskState.Thinking,
                TaskState.Pushing,
                TaskState.Thinking,
                TaskState.Pushing,
                TaskState.Completed,
            ],
        )
        self.assertEqual(
            [message["role"] for message in task.messages],
            [
                "system",
                "user",
                "assistant",
                "tool",
                "assistant",
                "tool",
                "assistant",
            ],
        )

    async def test_preexisting_plan_waits_in_thinking(self):
        started = asyncio.Event()

        class BlockingLLM(ScriptedLLMClient):
            async def invoke(self, messages, *, tools=None, tool_choice=None):
                started.set()
                await asyncio.Event().wait()

        task = make_task()
        harness = make_harness(BlockingLLM(), task=task)
        harness.create_plan("Ship", definitions([ONE_STEP]))
        running = asyncio.create_task(harness.run())
        await started.wait()

        self.assertIs(task.state, TaskState.Thinking)
        task.interrupt()
        running.cancel()
        with self.assertRaises(PlanAndSolveInterruptedError):
            await running
        self.assertIs(task.state, TaskState.Interrupted)

    async def test_premature_completed_final_gets_immediate_ephemeral_reminder(self):
        task = make_task()
        llm = ScriptedLLMClient(
            [
                final_choice(),
                create_choice(),
                complete_choice(),
                final_choice(),
            ]
        )
        harness = make_harness(llm, task=task)

        await harness.run()

        self.assertFalse(has_progress_reminder(llm.requests[0]))
        self.assertTrue(has_progress_reminder(llm.requests[1]))
        self.assertFalse(
            any(
                str(message.get("content", "")).startswith(
                    "Plan progress reminder:"
                )
                for message in task.messages
            )
        )
        self.assertIs(task.state, TaskState.Completed)

    async def test_failed_and_legacy_waiting_final_map_to_failed(self):
        for status in ("failed", "waiting_for_info"):
            with self.subTest(status=status):
                task = make_task()
                harness = make_harness(
                    ScriptedLLMClient([final_choice(status, "Blocked")]),
                    task=task,
                )

                response = await harness.run()

                self.assertTrue(response.startswith(f"Status: {status}"))
                self.assertIs(task.state, TaskState.Failed)

    async def test_malformed_final_raises_protocol_error_and_fails(self):
        task = make_task()
        harness = make_harness(
            ScriptedLLMClient(
                [malformed_final_choice("Summary: no status")]
            ),
            task=task,
        )

        with self.assertRaises(PlanAndSolveFinalResponseError):
            await harness.run()

        self.assertIs(task.state, TaskState.Failed)

    async def test_reminder_starts_on_sixth_request_and_read_resets_it(self):
        task = make_task(ordinary=True)
        choices = [
            *[
                tool_choice("ping", {}, f"ping-{index}")
                for index in range(5)
            ],
            tool_choice("read_plan", {}, "read"),
            complete_choice(),
            final_choice(),
        ]
        llm = ScriptedLLMClient(choices)
        harness = make_harness(llm, task=task)
        harness.create_plan("Ship", definitions([ONE_STEP]))

        await harness.run()

        self.assertTrue(
            all(
                not has_progress_reminder(request)
                for request in llm.requests[:5]
            )
        )
        self.assertTrue(has_progress_reminder(llm.requests[5]))
        self.assertFalse(has_progress_reminder(llm.requests[6]))
        self.assertFalse(has_progress_reminder(llm.requests[7]))

    async def test_failed_plan_tool_does_not_reset_reminder(self):
        task = make_task(ordinary=True)
        gateway = PlanAwareGateway(failed_plan_results=1)
        llm = ScriptedLLMClient(
            [
                *[
                    tool_choice("ping", {}, f"ping-{index}")
                    for index in range(5)
                ],
                tool_choice("read_plan", {}, "failed-read"),
                tool_choice("read_plan", {}, "successful-read"),
                complete_choice(),
                final_choice(),
            ]
        )
        harness = make_harness(llm, task=task, gateway=gateway)
        harness.create_plan("Ship", definitions([ONE_STEP]))

        await harness.run()

        self.assertTrue(has_progress_reminder(llm.requests[5]))
        self.assertTrue(has_progress_reminder(llm.requests[6]))
        self.assertFalse(has_progress_reminder(llm.requests[7]))

    async def test_every_successful_plan_tool_counts_in_mixed_results(self):
        for name in (
            "create_plan",
            "read_plan",
            "update_plan",
            "plan_step_complete",
        ):
            with self.subTest(name=name):
                results = [
                    result("ping", "ordinary"),
                    result(name, f"plan-{name}"),
                ]
                self.assertTrue(
                    PlanAndSolveHarness._has_successful_plan_tool_result(
                        results
                    )
                )
        self.assertFalse(
            PlanAndSolveHarness._has_successful_plan_tool_result(
                [result("read_plan", "failed", error=True)]
            )
        )

    async def test_gateway_exception_from_pushing_marks_task_failed(self):
        class RaisingGateway(PlanAwareGateway):
            async def execute_many(self, calls):
                self.batches.append(list(calls))
                raise RuntimeError("gateway failed")

        task = make_task()
        harness = make_harness(
            ScriptedLLMClient([create_choice()]),
            task=task,
            gateway=RaisingGateway(),
        )

        with self.assertRaisesRegex(RuntimeError, "gateway failed"):
            await harness.run()

        self.assertIs(task.state, TaskState.Failed)
        self.assertEqual(
            [message["role"] for message in task.messages],
            ["system", "user", "assistant"],
        )

    async def test_tool_call_limit_fails_before_executing_batch(self):
        task = make_task()
        gateway = PlanAwareGateway()
        harness = make_harness(
            ScriptedLLMClient([create_choice()]),
            task=task,
            gateway=gateway,
        )
        harness.MAX_TOOL_CALLS = 0

        with self.assertRaises(PlanAndSolveLimitError):
            await harness.run()

        self.assertEqual(gateway.batches, [])
        self.assertIs(task.state, TaskState.Failed)

    async def test_round_limit_and_model_error_fail_and_unbind(self):
        with self.subTest(case="round limit"):
            task = make_task(ordinary=True)
            harness = make_harness(
                ScriptedLLMClient(
                    [tool_choice("ping", {}, "only-round")]
                ),
                task=task,
            )
            harness.MAX_PLAN_ROUNDS = 1
            with self.assertRaises(PlanAndSolveLimitError):
                await harness.run()
            self.assertIs(task.state, TaskState.Failed)

        with self.subTest(case="model error"):
            task = make_task()
            harness = make_harness(
                ScriptedLLMClient(error=RuntimeError("model offline")),
                task=task,
            )
            with self.assertRaisesRegex(RuntimeError, "model offline"):
                await harness.run()
            self.assertIs(task.state, TaskState.Failed)

    async def test_rejects_second_run_without_rewriting_terminal_state(self):
        task = make_task()
        harness = make_harness(
            ScriptedLLMClient([final_choice("failed")]),
            task=task,
        )
        await harness.run()

        with self.assertRaisesRegex(RuntimeError, "new Task"):
            await harness.run()

        self.assertIs(task.state, TaskState.Failed)

    async def test_task_manager_interrupts_planning_model_wait(self):
        started = asyncio.Event()

        class BlockingLLM(ScriptedLLMClient):
            async def invoke(self, messages, *, tools=None, tool_choice=None):
                started.set()
                await asyncio.Event().wait()

        manager = TaskManager()
        task = make_task()
        harness = make_harness(BlockingLLM(), task=task)
        record = retain_test_harness(manager, harness)
        running = asyncio.create_task(harness.run())
        record.runner = running
        await started.wait()

        outcome = manager.interrupt_task(task.task_title)

        self.assertIs(outcome.outcome, InterruptTaskOutcome.Interrupted)
        with self.assertRaises(PlanAndSolveInterruptedError):
            await running
        record.runner = None
        self.assertIs(task.state, TaskState.Interrupted)
        self.assertEqual(
            [message["role"] for message in task.messages],
            ["system", "user"],
        )
        manager.shutdown()

    async def test_interrupts_pushing_without_appending_late_tool_results(self):
        gateway_started = asyncio.Event()

        class BlockingGateway(PlanAwareGateway):
            async def execute_many(self, calls):
                self.batches.append(list(calls))
                gateway_started.set()
                await asyncio.Event().wait()

        task = make_task()
        gateway = BlockingGateway()
        harness = make_harness(
            ScriptedLLMClient([create_choice()]),
            task=task,
            gateway=gateway,
        )
        running = asyncio.create_task(harness.run())
        await gateway_started.wait()

        self.assertIs(task.state, TaskState.Pushing)
        task.interrupt()
        running.cancel()
        with self.assertRaises(PlanAndSolveInterruptedError):
            await running
        self.assertIs(task.state, TaskState.Interrupted)
        self.assertEqual(
            [message["role"] for message in task.messages],
            ["system", "user", "assistant"],
        )

    async def test_unstarted_interrupted_task_never_initializes_context(self):
        task = make_task()
        task.interrupt()
        harness = make_harness(
            ScriptedLLMClient([final_choice("failed")]),
            task=task,
        )

        with self.assertRaises(PlanAndSolveInterruptedError):
            await harness.run()

        self.assertIs(task.state, TaskState.Interrupted)
        self.assertEqual(task.messages, ())

    def test_plan_specific_limits_are_frozen(self):
        self.assertEqual(PlanAndSolveHarness.MAX_PLAN_ROUNDS, 60)
        self.assertEqual(PlanAndSolveHarness.MAX_TOOL_CALLS, 100)
        self.assertEqual(
            PlanAndSolveHarness.PLAN_PROGRESS_GRACE_ROUNDS,
            5,
        )


if __name__ == "__main__":
    unittest.main()
