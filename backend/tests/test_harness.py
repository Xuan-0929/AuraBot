import json
import unittest

from backend.agent.LLMClient import ChatCompletionChoice
from backend.agent.task.Task import Task, TaskType
from backend.agent.task.harness.Harness import Harness
from backend.agent.task.harness.adaptors.Adaptor import (
    Adaptor,
    AdaptorRouteError,
)
from backend.agent.task.harness.adaptors.AdaptorRegistry import (
    AdaptorRegistry,
)
from backend.agent.task.harness.adaptors.FileSystemAdaptor import (
    FileSystemAdaptor,
)
from backend.agent.task.harness.utils import build_common_prompt_values
from backend.agent.tools.Tool import Tool
from backend.agent.tools.ToolGroup import ToolGroup
from backend.agent.tools.core.ToolCallEntity import (
    FinalResponse,
    ResolvedToolCall,
    ToolCall,
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


class RecordingAdaptor(Adaptor):
    def __init__(self):
        self.entities = []

    def adapt(self, entity: ResolvedToolCall) -> ResolvedToolCall:
        self.entities.append(entity)
        return entity


class AppendingAdaptor(Adaptor):
    def __init__(self, value: str):
        self.value = value

    def adapt(self, entity: ResolvedToolCall) -> ResolvedToolCall:
        arguments = dict(entity.arguments)
        arguments["trace"] = [*arguments.get("trace", []), self.value]
        return ResolvedToolCall(
            tool=entity.tool,
            arguments=arguments,
            call_id=entity.call_id,
        )


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
        self.assertEqual(dict(harness.adaptors), {})

    def test_base_owns_injected_gateway_and_adaptor_registry(self):
        gateway = object()
        registry = AdaptorRegistry()
        harness = ConcreteHarness(
            task=make_task(),
            llm_client=FakeLLMClient(),
            tool_call_gateway=gateway,  # type: ignore[arg-type]
            adaptor_registry=registry,
        )

        self.assertIs(harness.tool_call_gateway, gateway)
        self.assertIs(harness.adaptor_registry, registry)

    def test_resolves_before_running_routed_adaptor(self):
        bash = Tool(
            "bash",
            "Run Bash",
            "http://localhost:6410/mcp",
            input_schema={"type": "object"},
            adaptor_names=("RecordingAdaptor",),
        )
        recorder = RecordingAdaptor()
        harness = ConcreteHarness(
            task=make_task(
                tool_groups=[ToolGroup("shell", [bash], "Shell tools")]
            ),
            llm_client=FakeLLMClient(),
            adaptor_registry=AdaptorRegistry(
                {"RecordingAdaptor": lambda task: recorder}
            ),
        )
        harness.build_openai_tools()

        resolved = harness.resolve_and_adapt_tool_calls(
            [ToolCall("bash", {"PATH": "."}, "call_1")]
        )

        self.assertIs(resolved[0].tool, bash)
        self.assertIs(recorder.entities[0], resolved[0])
        self.assertIsInstance(recorder.entities[0], ResolvedToolCall)

    def test_default_filesystem_adaptor_runs_after_resolver(self):
        bash = Tool(
            "bash",
            "Run Bash",
            "http://localhost:6410/mcp",
            input_schema={"type": "object"},
            adaptor_names=("FileSystemAdaptor",),
        )
        harness = ConcreteHarness(
            task=make_task(
                tool_groups=[ToolGroup("shell", [bash], "Shell tools")]
            ),
            llm_client=FakeLLMClient(),
        )
        harness.build_openai_tools()

        resolved = harness.resolve_and_adapt_tool_calls(
            [ToolCall("bash", {"PATH": "backend"}, "call_1")]
        )

        self.assertIs(resolved[0].tool, bash)
        self.assertEqual(resolved[0].arguments["PATH"], "/workspace/backend")
        self.assertIsInstance(
            harness.adaptors["FileSystemAdaptor"],
            FileSystemAdaptor,
        )

    def test_requires_tool_build_before_any_resolution_batch(self):
        ping = Tool(
            "ping",
            "Return pong",
            "http://localhost:6410/mcp",
            input_schema=EMPTY_INPUT_SCHEMA,
        )
        harness = ConcreteHarness(
            task=make_task(
                tool_groups=[ToolGroup("utilities", [ping], "Utilities")]
            ),
            llm_client=FakeLLMClient(),
        )

        with self.assertRaisesRegex(
            AdaptorRouteError,
            "build_openai_tools",
        ):
            harness.resolve_and_adapt_tool_calls(
                [ToolCall("ping", {}, "call_1")]
            )

    def test_mixed_batch_adapts_only_bound_tools(self):
        read = Tool(
            "read_file",
            "Read a file",
            "http://localhost:6410/mcp",
            input_schema={"type": "object"},
            adaptor_names=("FileSystemAdaptor",),
        )
        ping = Tool(
            "ping",
            "Return pong",
            "http://localhost:6410/mcp",
            input_schema=EMPTY_INPUT_SCHEMA,
        )
        harness = ConcreteHarness(
            task=make_task(
                tool_groups=[
                    ToolGroup("mixed", [read, ping], "Mixed tools")
                ]
            ),
            llm_client=FakeLLMClient(),
        )
        harness.build_openai_tools()

        resolved = harness.resolve_and_adapt_tool_calls(
            [
                ToolCall("read_file", {"path": "a.txt"}, "call_1"),
                ToolCall("ping", {}, "call_2"),
            ]
        )

        self.assertEqual(resolved[0].arguments["path"], "/workspace/a.txt")
        self.assertEqual(resolved[1].arguments, {})

    def test_applies_each_tools_adaptors_in_declared_order(self):
        tool = Tool(
            "trace",
            "Trace Adaptor order",
            "http://localhost:6410/mcp",
            input_schema={"type": "object"},
            adaptor_names=("FirstAdaptor", "SecondAdaptor"),
        )
        harness = ConcreteHarness(
            task=make_task(
                tool_groups=[ToolGroup("trace", [tool], "Trace tools")]
            ),
            llm_client=FakeLLMClient(),
            adaptor_registry=AdaptorRegistry(
                {
                    "FirstAdaptor": lambda task: AppendingAdaptor("first"),
                    "SecondAdaptor": lambda task: AppendingAdaptor("second"),
                }
            ),
        )
        harness.build_openai_tools()

        resolved = harness.resolve_and_adapt_tool_calls(
            [ToolCall("trace", {}, "call_1")]
        )

        self.assertEqual(
            resolved[0].arguments["trace"],
            ["first", "second"],
        )

    def test_build_rejects_unknown_or_invalid_adaptor_factory(self):
        tool = Tool(
            "ping",
            "Return pong",
            "http://localhost:6410/mcp",
            input_schema=EMPTY_INPUT_SCHEMA,
            adaptor_names=("UnknownAdaptor",),
        )
        task = make_task(
            tool_groups=[ToolGroup("utilities", [tool], "Utilities")]
        )

        with self.assertRaises(AdaptorRouteError):
            ConcreteHarness(
                task=task,
                llm_client=FakeLLMClient(),
            ).build_openai_tools()

        with self.assertRaisesRegex(TypeError, "must return Adaptor"):
            ConcreteHarness(
                task=task,
                llm_client=FakeLLMClient(),
                adaptor_registry=AdaptorRegistry(
                    {"UnknownAdaptor": lambda task: object()}
                ),
            ).build_openai_tools()

    def test_repeated_build_replaces_instead_of_accumulating_adaptors(self):
        calls = []
        read = Tool(
            "read_file",
            "Read a file",
            "http://localhost:6410/mcp",
            input_schema={"type": "object"},
            adaptor_names=("FileSystemAdaptor",),
        )
        harness = ConcreteHarness(
            task=make_task(
                tool_groups=[ToolGroup("files", [read], "File tools")]
            ),
            llm_client=FakeLLMClient(),
            adaptor_registry=AdaptorRegistry(
                {
                    "FileSystemAdaptor": lambda task: calls.append(task)
                    or FileSystemAdaptor(task.workdir)
                }
            ),
        )

        harness.build_openai_tools()
        first = harness.adaptors["FileSystemAdaptor"]
        harness.build_openai_tools()

        self.assertEqual(calls, [harness.task, harness.task])
        self.assertEqual(tuple(harness.adaptors), ("FileSystemAdaptor",))
        self.assertIsNot(
            harness.adaptors["FileSystemAdaptor"],
            first,
        )

    def test_multiple_tools_share_one_adaptor_instance(self):
        factory_calls = []
        tools = [
            Tool(
                name,
                f"{name} tool",
                "http://localhost:6410/mcp",
                input_schema={"type": "object"},
                adaptor_names=("FileSystemAdaptor",),
            )
            for name in ("read_file", "edit_file")
        ]
        harness = ConcreteHarness(
            task=make_task(
                tool_groups=[ToolGroup("files", tools, "File tools")]
            ),
            llm_client=FakeLLMClient(),
            adaptor_registry=AdaptorRegistry(
                {
                    "FileSystemAdaptor": lambda task: factory_calls.append(task)
                    or FileSystemAdaptor(task.workdir)
                }
            ),
        )

        harness.build_openai_tools()

        self.assertEqual(factory_calls, [harness.task])
        self.assertEqual(tuple(harness.adaptors), ("FileSystemAdaptor",))

    def test_failed_rebuild_preserves_previously_built_adaptors(self):
        read = Tool(
            "read_file",
            "Read a file",
            "http://localhost:6410/mcp",
            input_schema={"type": "object"},
            adaptor_names=("FileSystemAdaptor",),
        )
        task = make_task(
            tool_groups=[ToolGroup("files", [read], "File tools")]
        )
        harness = ConcreteHarness(
            task=task,
            llm_client=FakeLLMClient(),
        )
        harness.build_openai_tools()
        original = harness.adaptors["FileSystemAdaptor"]
        task.update_tool_groups(
            [
                ToolGroup(
                    "unknown",
                    [
                        Tool(
                            "unknown",
                            "Unknown tool",
                            "http://localhost:6410/mcp",
                            input_schema=EMPTY_INPUT_SCHEMA,
                            adaptor_names=("UnknownAdaptor",),
                        )
                    ],
                    "Unknown tools",
                )
            ]
        )

        with self.assertRaises(AdaptorRouteError):
            harness.build_openai_tools()

        self.assertIs(
            harness.adaptors["FileSystemAdaptor"],
            original,
        )

    def test_tool_group_update_requires_and_supports_route_rebuild(self):
        ping = Tool(
            "ping",
            "Return pong",
            "http://localhost:6410/mcp",
            input_schema=EMPTY_INPUT_SCHEMA,
        )
        read = Tool(
            "read_file",
            "Read a file",
            "http://localhost:6410/mcp",
            input_schema={"type": "object"},
            adaptor_names=("FileSystemAdaptor",),
        )
        task = make_task(
            tool_groups=[ToolGroup("utilities", [ping], "Utilities")]
        )
        harness = ConcreteHarness(
            task=task,
            llm_client=FakeLLMClient(),
        )
        initial_definitions = harness.build_openai_tools()

        task.update_tool_groups(
            [ToolGroup("filesystem", [read], "Filesystem tools")]
        )
        with self.assertRaisesRegex(
            AdaptorRouteError,
            "rebuild routes",
        ):
            harness.resolve_and_adapt_tool_calls(
                [ToolCall("read_file", {"path": "a.txt"}, "call_1")]
            )

        rebuilt_definitions = harness.build_openai_tools()
        resolved = harness.resolve_and_adapt_tool_calls(
            [ToolCall("read_file", {"path": "a.txt"}, "call_2")]
        )

        self.assertEqual(
            initial_definitions[0]["function"]["name"],
            "ping",
        )
        self.assertEqual(
            rebuilt_definitions[0]["function"]["name"],
            "read_file",
        )
        self.assertEqual(resolved[0].arguments["path"], "/workspace/a.txt")

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
        self.assertEqual(dict(harness.adaptors), {})

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
