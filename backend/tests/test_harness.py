import json
import unittest

from backend.agent.LLMClient import ChatCompletionChoice
from backend.agent.task.Task import Task, TaskType
from backend.agent.task.harness.Harness import Harness
from backend.agent.task.harness.utils import build_common_prompt_values
from backend.agent.tools.Tool import Tool
from backend.agent.tools.ToolGroup import ToolGroup
from backend.agent.tools.core.ToolCallEntity import (
    FinalResponse,
    ToolCallBatch,
    ToolExecutionResult,
)


EMPTY_INPUT_SCHEMA = {"type": "object", "properties": {}}


class FakeLLMClient:
    pass


class ConcreteHarness(Harness):
    def build_system_prompt(self) -> str:
        return (
            "workdir={workdir}\nconstraints={constraints}\n"
            "skills={skill_groups}\ntools={tool_groups}"
        ).format(**build_common_prompt_values(self.task))

    async def run(self) -> object:
        return "done"


def make_task(*, tool_groups=None) -> Task:
    return Task(
        task_id=1,
        task_title="Test",
        parent_session_id="main-session",
        task_type=TaskType.ReAct,
        task_prompt="Do the work.",
        workdir="/workspace",
        constraints="Read only.",
        tool_groups=tool_groups,
    )


class HarnessTests(unittest.TestCase):
    def test_owns_task_and_llm_without_protocol_instance_state(self):
        task = make_task()
        llm_client = FakeLLMClient()

        harness = ConcreteHarness(
            task=task,
            llm_client=llm_client,
        )

        self.assertIs(harness.task, task)
        self.assertIs(harness.llm_client, llm_client)
        self.assertNotIn("_tool_protocol", harness.__dict__)

    def test_builds_system_and_user_prompts_via_base_contract(self):
        harness = ConcreteHarness(
            task=make_task(),
            llm_client=FakeLLMClient(),
        )

        system_prompt = harness.build_system_prompt()

        self.assertIn("workdir=/workspace", system_prompt)
        self.assertIn("constraints=Read only.", system_prompt)
        self.assertIn("skills=None provided.", system_prompt)
        self.assertIn("tools=None provided.", system_prompt)
        self.assertEqual(harness.build_user_prompt(), "Do the work.")

    def test_builds_openai_tools_from_task_authorization(self):
        tool = Tool(
            "ping",
            "Return pong",
            "http://localhost:6410/mcp",
            input_schema=EMPTY_INPUT_SCHEMA,
        )
        harness = ConcreteHarness(
            task=make_task(
                tool_groups=[ToolGroup("utilities", [tool], "Utilities")]
            ),
            llm_client=FakeLLMClient(),
        )

        definitions = harness.build_openai_tools()

        self.assertEqual(definitions[0]["type"], "function")
        self.assertEqual(definitions[0]["function"]["name"], "ping")
        self.assertEqual(
            definitions[0]["function"]["parameters"],
            EMPTY_INPUT_SCHEMA,
        )

    def test_parses_native_completion_for_every_harness(self):
        harness = ConcreteHarness(
            task=make_task(),
            llm_client=FakeLLMClient(),
        )

        final = harness.parse_completion(
            ChatCompletionChoice(
                message={"role": "assistant", "content": "done"},
                finish_reason="stop",
            )
        )
        calls = harness.parse_completion(
            ChatCompletionChoice(
                message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_provider_40",
                            "type": "function",
                            "function": {
                                "name": "ping",
                                "arguments": "{}",
                            },
                        }
                    ],
                },
                finish_reason="tool_calls",
            )
        )

        self.assertEqual(final, FinalResponse("done"))
        self.assertIsInstance(calls, ToolCallBatch)
        self.assertEqual(calls.calls[0].call_id, "call_provider_40")

    def test_builds_ordered_role_tool_messages_for_every_harness(self):
        harness = ConcreteHarness(
            task=make_task(),
            llm_client=FakeLLMClient(),
        )

        messages = harness.build_tool_result_messages(
            [
                ToolExecutionResult(
                    tool_name="ping",
                    call_id="call_provider_40",
                    content=[{"type": "text", "text": "pong"}],
                    structured_content=None,
                    is_error=False,
                )
            ]
        )

        self.assertEqual(messages[0]["role"], "tool")
        self.assertEqual(messages[0]["tool_call_id"], "call_provider_40")
        self.assertEqual(json.loads(messages[0]["content"])["name"], "ping")


if __name__ == "__main__":
    unittest.main()
