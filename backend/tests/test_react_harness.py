import asyncio
import tempfile
import unittest
from pathlib import Path
from string import Formatter

from backend.agent.LLMClient import ChatCompletionChoice
from backend.agent.skills.Skill import Skill
from backend.agent.skills.SkillGroup import SkillGroup
from backend.agent.task.Task import Task, TaskState, TaskType
from backend.agent.task.harness.Harness import Harness
from backend.agent.task.harness.ReActHarness import (
    ReActHarness,
    ReActInterruptedError,
    ReActLimitError,
)
from backend.agent.task.harness.prompts import system_prompt_ReAct
from backend.agent.tools.Tool import Tool
from backend.agent.tools.ToolGroup import ToolGroup
from backend.agent.tools.core.ToolCallEntity import ToolExecutionResult


EMPTY_INPUT_SCHEMA = {"type": "object", "properties": {}}


class FakeLLMClient:
    def __init__(
        self,
        choices: list[ChatCompletionChoice] | None = None,
        error: Exception | None = None,
    ):
        self.choices = list(choices or [])
        self.error = error
        self.requests: list[dict] = []
        self.message_list_ids: list[int] = []

    async def invoke(self, messages, *, tools=None, tool_choice=None):
        self.message_list_ids.append(id(messages))
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


class FakeToolCallGateway:
    def __init__(
        self,
        results: list[list[ToolExecutionResult]] | None = None,
    ):
        self.results = list(results or [])
        self.batches = []

    async def execute_many(self, calls):
        self.batches.append(list(calls))
        return self.results.pop(0)


class RecordingTask(Task):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.state_history = [self.state]

    def compare_and_set_state(self, expected, target) -> bool:
        previous_state = self.state
        transitioned = super().compare_and_set_state(expected, target)
        if self.state is not previous_state:
            self.state_history.append(self.state)
        return transitioned


def final_choice(content: str = "Task completed.") -> ChatCompletionChoice:
    return ChatCompletionChoice(
        message={"role": "assistant", "content": content},
        finish_reason="stop",
    )


def tool_choice(
    *,
    name: str = "read_file",
    arguments: str = '{"path":"example.py"}',
    call_id: str = "call_1",
) -> ChatCompletionChoice:
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
                        "arguments": arguments,
                    },
                }
            ],
        },
        finish_reason="tool_calls",
    )


def make_task(**overrides) -> RecordingTask:
    values = {
        "task_id": 1,
        "task_title": "Fix example",
        "parent_session_id": "main-session",
        "task_type": TaskType.ReAct,
        "task_prompt": "Fix example.py and verify it.",
        "workdir": "/workspace",
    }
    values.update(overrides)
    return RecordingTask(**values)


class ReActPromptTemplateTests(unittest.TestCase):
    def test_template_declares_only_common_task_placeholders(self):
        placeholders = {
            field_name
            for _, field_name, _, _ in Formatter().parse(system_prompt_ReAct)
            if field_name is not None
        }

        self.assertEqual(
            placeholders,
            {"workdir", "constraints", "skill_groups", "tool_groups"},
        )


class ReActHarnessTests(unittest.TestCase):
    def test_system_prompt_contains_react_scope_and_group_summaries(self):
        with tempfile.TemporaryDirectory() as directory:
            skill_path = Path(directory) / "SKILL.md"
            skill_path.write_text(
                "---\n"
                "name: summarize\n"
                "description: Summarize source material.\n"
                "---\n"
                "Skill instructions.\n",
                encoding="utf-8",
            )
            task = Task(
                task_id=1,
                task_title="Research <report>",
                parent_session_id="main-session",
                task_type=TaskType.ReAct,
                task_prompt="Prepare an evidence-based report.",
                workdir="C:/workspace & data",
                constraints="Do not modify source files.",
                skill_groups=[
                    SkillGroup(
                        "research",
                        [Skill("summarize", path=skill_path)],
                        "Research skills",
                    ),
                    SkillGroup("writing", [], "Writing skills"),
                ],
                tool_groups=[
                    ToolGroup(
                        "filesystem",
                        [
                            Tool(
                                "read_file",
                                "Read one file",
                                "http://localhost:6410/mcp",
                                input_schema=EMPTY_INPUT_SCHEMA,
                            )
                        ],
                        "File tools",
                    )
                ],
            )
            harness = ReActHarness(task=task, llm_client=FakeLLMClient())

            prompt = harness.build_system_prompt()

        self.assertIn("no more than 20 ReAct rounds", prompt)
        self.assertIn("Working directory:\nC:/workspace & data", prompt)
        self.assertIn("Constraints:\nDo not modify source files.", prompt)
        self.assertIn("<name>research</name>", prompt)
        self.assertIn("<name>writing</name>", prompt)
        self.assertIn("<name>summarize</name>", prompt)
        self.assertIn(
            "- filesystem: File tools (tools: read_file)",
            prompt,
        )
        self.assertNotIn("<parameters>", prompt)
        self.assertNotIn("Create a finite phase plan", prompt)
        self.assertEqual(harness.build_user_prompt(), task.task_prompt)

    def test_system_prompt_marks_optional_scope_and_groups_as_missing(self):
        task = Task(
            task_id=2,
            task_title="Answer",
            parent_session_id="main-session",
            task_type=TaskType.ReAct,
            task_prompt="Answer the question.",
        )
        harness = ReActHarness(task=task, llm_client=FakeLLMClient())

        prompt = harness.build_system_prompt()

        self.assertIn("Working directory:\nNot specified.", prompt)
        self.assertIn("Constraints:\nNot specified.", prompt)
        self.assertEqual(prompt.count("None provided."), 2)

    def test_inherits_all_common_tool_and_prompt_methods(self):
        common_methods = {
            "build_openai_tools",
            "parse_completion",
            "build_tool_result_messages",
            "build_user_prompt",
        }

        self.assertTrue(common_methods.isdisjoint(ReActHarness.__dict__))
        self.assertIn("build_system_prompt", ReActHarness.__dict__)
        self.assertNotIn("__init__", ReActHarness.__dict__)
        self.assertIs(ReActHarness.__init__, Harness.__init__)


class ReActHarnessRunTests(unittest.IsolatedAsyncioTestCase):
    async def test_runs_to_completion_and_keeps_context_on_task(self):
        task = make_task()
        original_messages = task.messages
        llm_client = FakeLLMClient([final_choice("All checks passed.")])
        harness = ReActHarness(
            task=task,
            llm_client=llm_client,  # type: ignore[arg-type]
            tool_call_gateway=FakeToolCallGateway(),  # type: ignore[arg-type]
        )

        result = await harness.run()

        self.assertEqual(result, "All checks passed.")
        self.assertEqual(original_messages, ())
        self.assertEqual(
            [message["role"] for message in task.messages],
            ["system", "user", "assistant"],
        )
        self.assertEqual(task.messages[1]["content"], task.task_prompt)
        self.assertEqual(
            task.state_history,
            [
                TaskState.Initializing,
                TaskState.Thinking,
                TaskState.Pushing,
                TaskState.Completed,
            ],
        )
        self.assertIsNone(llm_client.requests[0]["tools"])
        self.assertIsNone(llm_client.requests[0]["tool_choice"])

    async def test_observes_tool_result_then_asks_model_again(self):
        with tempfile.TemporaryDirectory() as directory:
            read_file = Tool(
                "read_file",
                "Read one file",
                "http://127.0.0.1:6410/mcp",
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
                adaptor_names=("FileSystemAdaptor",),
            )
            task = make_task(
                workdir=directory,
                tool_groups=[
                    ToolGroup("FileSystem", [read_file], "File tools")
                ],
            )
            llm_client = FakeLLMClient(
                [tool_choice(), final_choice("File inspected and fixed.")]
            )
            gateway = FakeToolCallGateway(
                [
                    [
                        ToolExecutionResult(
                            tool_name="read_file",
                            call_id="call_1",
                            content=[
                                {"type": "text", "text": "def answer(): ..."}
                            ],
                            structured_content={
                                "content": "def answer(): ..."
                            },
                            is_error=False,
                        )
                    ]
                ]
            )
            harness = ReActHarness(
                task=task,
                llm_client=llm_client,  # type: ignore[arg-type]
                tool_call_gateway=gateway,  # type: ignore[arg-type]
            )

            result = await harness.run()

        self.assertEqual(result, "File inspected and fixed.")
        self.assertEqual(len(gateway.batches), 1)
        self.assertEqual(
            gateway.batches[0][0].arguments["path"],
            str((Path(directory) / "example.py").resolve()),
        )
        self.assertEqual(
            [message["role"] for message in task.messages],
            ["system", "user", "assistant", "tool", "assistant"],
        )
        self.assertEqual(
            task.state_history,
            [
                TaskState.Initializing,
                TaskState.Thinking,
                TaskState.Pushing,
                TaskState.Thinking,
                TaskState.Pushing,
                TaskState.Completed,
            ],
        )
        self.assertEqual(
            llm_client.requests[1]["messages"][-1]["tool_call_id"],
            "call_1",
        )
        self.assertIsNotNone(llm_client.requests[0]["tools"])
        self.assertEqual(llm_client.requests[0]["tool_choice"], "auto")
        self.assertEqual(task.state, TaskState.Completed)

    async def test_marks_task_failed_and_reraises_model_error(self):
        task = make_task()
        harness = ReActHarness(
            task=task,
            llm_client=FakeLLMClient(  # type: ignore[arg-type]
                error=RuntimeError("model offline")
            ),
            tool_call_gateway=FakeToolCallGateway(),  # type: ignore[arg-type]
        )

        with self.assertRaisesRegex(RuntimeError, "model offline"):
            await harness.run()

        self.assertEqual(
            task.state_history,
            [
                TaskState.Initializing,
                TaskState.Thinking,
                TaskState.Failed,
            ],
        )

    async def test_marks_task_failed_when_tool_call_limit_is_exceeded(self):
        read_file = Tool(
            "read_file",
            "Read one file",
            "http://127.0.0.1:6410/mcp",
            input_schema={"type": "object"},
        )
        task = make_task(
            tool_groups=[ToolGroup("FileSystem", [read_file], "File tools")]
        )
        gateway = FakeToolCallGateway()
        harness = ReActHarness(
            task=task,
            llm_client=FakeLLMClient([tool_choice()]),  # type: ignore[arg-type]
            tool_call_gateway=gateway,  # type: ignore[arg-type]
        )
        harness.MAX_TOOL_CALLS = 0

        with self.assertRaisesRegex(ReActLimitError, "tool-call limit"):
            await harness.run()

        self.assertEqual(task.state, TaskState.Failed)
        self.assertEqual(gateway.batches, [])

    async def test_rejects_second_run_without_rewriting_terminal_state(self):
        task = make_task()
        harness = ReActHarness(
            task=task,
            llm_client=FakeLLMClient([final_choice()]),  # type: ignore[arg-type]
            tool_call_gateway=FakeToolCallGateway(),  # type: ignore[arg-type]
        )
        await harness.run()

        with self.assertRaisesRegex(RuntimeError, "new Task"):
            await harness.run()

        self.assertEqual(task.state, TaskState.Completed)

    async def test_discards_a_model_response_after_external_interrupt(self):
        model_started = asyncio.Event()
        release_model = asyncio.Event()

        class BlockingLLMClient(FakeLLMClient):
            async def invoke(self, messages, *, tools=None, tool_choice=None):
                model_started.set()
                await release_model.wait()
                return final_choice("Late response")

        task = make_task()
        harness = ReActHarness(
            task=task,
            llm_client=BlockingLLMClient(),  # type: ignore[arg-type]
            tool_call_gateway=FakeToolCallGateway(),  # type: ignore[arg-type]
        )
        run = asyncio.create_task(harness.run())
        await model_started.wait()

        self.assertIs(task.state, TaskState.Thinking)
        task.interrupt()
        release_model.set()

        with self.assertRaises(ReActInterruptedError):
            await run

        self.assertIs(task.state, TaskState.Interrupted)
        self.assertEqual(
            [message["role"] for message in task.messages],
            ["system", "user"],
        )

    async def test_unstarted_interrupted_task_never_initializes_context(self):
        task = make_task()
        task.interrupt()
        harness = ReActHarness(
            task=task,
            llm_client=FakeLLMClient([final_choice()]),  # type: ignore[arg-type]
            tool_call_gateway=FakeToolCallGateway(),  # type: ignore[arg-type]
        )

        with self.assertRaises(ReActInterruptedError):
            await harness.run()

        self.assertIs(task.state, TaskState.Interrupted)
        self.assertEqual(task.messages, ())

    async def test_cancels_an_in_flight_tool_batch_when_interrupted(self):
        gateway_started = asyncio.Event()

        class BlockingToolCallGateway(FakeToolCallGateway):
            async def execute_many(self, calls):
                self.batches.append(list(calls))
                gateway_started.set()
                await asyncio.Event().wait()

        read_file = Tool(
            "read_file",
            "Read one file",
            "http://127.0.0.1:6410/mcp",
            input_schema={"type": "object"},
        )
        task = make_task(
            tool_groups=[
                ToolGroup("FileSystem", [read_file], "File tools")
            ]
        )
        gateway = BlockingToolCallGateway()
        harness = ReActHarness(
            task=task,
            llm_client=FakeLLMClient([tool_choice()]),  # type: ignore[arg-type]
            tool_call_gateway=gateway,  # type: ignore[arg-type]
        )
        run = asyncio.create_task(harness.run())
        await gateway_started.wait()

        self.assertIs(task.state, TaskState.Pushing)
        task.interrupt()
        run.cancel()

        with self.assertRaises(ReActInterruptedError):
            await run

        self.assertIs(task.state, TaskState.Interrupted)
        self.assertEqual(
            [message["role"] for message in task.messages],
            ["system", "user", "assistant"],
        )


if __name__ == "__main__":
    unittest.main()
