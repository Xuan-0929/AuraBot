import json
import tempfile
import unittest
from pathlib import Path
from string import Formatter

from backend.agent.skills.Skill import Skill
from backend.agent.skills.SkillGroup import SkillGroup
from backend.agent.task.Task import Task, TaskType
from backend.agent.task.harness.ReActHarness import ReActHarness
from backend.agent.task.harness.prompts import system_prompt_ReAct
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


class ReActPromptTemplateTests(unittest.TestCase):
    def test_template_declares_every_dynamic_task_placeholder(self):
        placeholders = {
            field_name
            for _, field_name, _, _ in Formatter().parse(system_prompt_ReAct)
            if field_name is not None
        }

        self.assertEqual(
            placeholders,
            {
                "workdir",
                "constraints",
                "skill_groups",
                "tool_groups",
                "tool_protocol",
            },
        )


class ReActHarnessPromptTests(unittest.IsolatedAsyncioTestCase):
    async def test_system_prompt_contains_task_scope_and_all_capability_groups(self):
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
                                "localhost:6410",
                                input_schema=EMPTY_INPUT_SCHEMA,
                            )
                        ],
                        "File tools",
                    ),
                    ToolGroup(
                        "search",
                        [
                            Tool(
                                "web_search",
                                "Search the web",
                                "localhost:6410",
                                input_schema=EMPTY_INPUT_SCHEMA,
                            )
                        ],
                        "Search tools",
                    ),
                ],
            )
            harness = ReActHarness(task=task, llm_client=FakeLLMClient())

            prompt = await harness._create_system_prompt()
            user_prompt = await harness._create_user_prompt()

        self.assertIn("no more than 20 ReAct rounds", prompt)
        self.assertIn("Working directory:\nC:/workspace & data", prompt)
        self.assertIn("Constraints:\nDo not modify source files.", prompt)
        self.assertIn("<name>research</name>", prompt)
        self.assertIn("<name>writing</name>", prompt)
        self.assertIn("<name>summarize</name>", prompt)
        self.assertIn("<name>filesystem</name>", prompt)
        self.assertIn("<name>search</name>", prompt)
        self.assertIn("<name>read_file</name>", prompt)
        self.assertIn("<name>web_search</name>", prompt)
        self.assertIn('{"type":"tool_calls","calls":', prompt)
        self.assertIn('{"type":"final","content":', prompt)
        self.assertIn("Never create or return a tool-call ID", prompt)
        self.assertIn("Do not emit native tool_calls", prompt)
        self.assertIn("inside the `content`", prompt)
        self.assertIn("one or more authorized actions", prompt)
        self.assertNotIn("Research &lt;report&gt;", prompt)
        self.assertNotIn("Prepare an evidence-based report.", prompt)
        self.assertNotIn("<task_context>", prompt)
        self.assertNotIn("<workdir>", prompt)
        self.assertNotIn("{workdir}", prompt)
        self.assertNotIn("{skill_groups}", prompt)
        self.assertEqual(user_prompt, "Prepare an evidence-based report.")

    async def test_system_prompt_marks_optional_scope_and_groups_as_missing(self):
        task = Task(
            task_id=2,
            task_title="Answer",
            parent_session_id="main-session",
            task_type=TaskType.ReAct,
            task_prompt="Answer the question.",
        )
        harness = ReActHarness(task=task, llm_client=FakeLLMClient())

        prompt = await harness._create_system_prompt()

        self.assertIn("Working directory:\nNot specified.", prompt)
        self.assertIn("Constraints:\nNot specified.", prompt)
        self.assertEqual(prompt.count("None provided."), 2)

    async def test_parses_assistant_text_protocol_without_executing_tools(self):
        task = Task(
            task_id=3,
            task_title="Answer",
            parent_session_id="main-session",
            task_type=TaskType.ReAct,
            task_prompt="Answer the question.",
        )
        harness = ReActHarness(task=task, llm_client=FakeLLMClient())

        final = harness.parse_assistant_message(
            {
                "role": "assistant",
                "content": '{"type":"final","content":"done"}',
            }
        )
        calls = harness.parse_assistant_message(
            {
                "role": "assistant",
                "content": (
                    '{"type":"tool_calls","calls":['
                    '{"name":"ping","arguments":{}}]}'
                ),
            }
        )

        self.assertEqual(final, FinalResponse("done"))
        self.assertIsInstance(calls, ToolCallBatch)
        self.assertEqual(calls.calls[0].name, "ping")
        self.assertRegex(calls.calls[0].call_id, r"^call_\d+$")

    async def test_builds_user_role_tool_observation_message(self):
        task = Task(
            task_id=4,
            task_title="Answer",
            parent_session_id="main-session",
            task_type=TaskType.ReAct,
            task_prompt="Answer the question.",
        )
        harness = ReActHarness(task=task, llm_client=FakeLLMClient())

        message = harness.build_tool_observation_message(
            [
                ToolExecutionResult(
                    tool_name="ping",
                    call_id="call_40",
                    content=[{"type": "text", "text": "pong"}],
                    structured_content=None,
                    is_error=False,
                )
            ]
        )

        self.assertEqual(message["role"], "user")
        self.assertEqual(
            json.loads(message["content"]),
            {
                "type": "tool_observation",
                "results": [
                    {
                        "id": "call_40",
                        "name": "ping",
                        "is_error": False,
                        "content": [{"type": "text", "text": "pong"}],
                        "structured_content": None,
                        "error_code": None,
                    }
                ],
            },
        )

    async def test_rejects_native_tool_call_message(self):
        task = Task(
            task_id=5,
            task_title="Answer",
            parent_session_id="main-session",
            task_type=TaskType.ReAct,
            task_prompt="Answer the question.",
        )
        harness = ReActHarness(task=task, llm_client=FakeLLMClient())

        with self.assertRaisesRegex(ValueError, "native tool_calls"):
            harness.parse_assistant_message(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {"name": "ping", "arguments": "{}"},
                        }
                    ],
                }
            )

        with self.assertRaisesRegex(ValueError, "native tool_calls"):
            harness.parse_assistant_message(
                {
                    "role": "assistant",
                    "content": '{"type":"final","content":"done"}',
                    "tool_calls": [],
                }
            )


if __name__ == "__main__":
    unittest.main()
