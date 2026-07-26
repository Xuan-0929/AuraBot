import unittest

from backend.agent.task.model import Task, TaskType
from backend.agent.task.harness.adaptors.adaptor import Adaptor
from backend.agent.task.harness.adaptors.registry import (
    AdaptorRegistry,
)
from backend.agent.task.harness.tool_execution_pipeline import (
    ToolExecutionPipeline,
)
from backend.agent.tools.tool import Tool
from backend.agent.tools.tool_group import ToolGroup
from backend.agent.tools.core.model import (
    ResolvedToolCall,
    ToolCall,
    ToolExecutionResult,
)


class _ContextAdaptor(Adaptor):
    def adapt(self, entity: ResolvedToolCall) -> ResolvedToolCall:
        return ResolvedToolCall(
            tool=entity.tool,
            arguments={**entity.arguments, "task_name": "Task"},
            call_id=entity.call_id,
        )


class _Gateway:
    def __init__(self):
        self.calls = ()

    async def execute_many(self, calls):
        self.calls = tuple(calls)
        return [
            ToolExecutionResult(
                tool_name=call.tool.name,
                call_id=call.call_id,
                content=[{"type": "text", "text": "pong"}],
                structured_content={"value": "pong"},
                is_error=False,
            )
            for call in calls
        ]


class ToolExecutionPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_builds_routes_executes_adapted_calls_and_encodes_results(self):
        tool = Tool(
            "ping",
            "Return pong",
            "http://localhost:6410/mcp",
            input_schema={"type": "object", "properties": {}},
            adaptor_names=("ContextAdaptor",),
        )
        task = Task(
            1,
            "Task",
            "session",
            TaskType.ReAct,
            "Ping",
            tool_groups=[ToolGroup("utilities", [tool], "Utilities")],
        )
        gateway = _Gateway()
        pipeline = ToolExecutionPipeline(
            task=task,
            tool_call_gateway=gateway,
            adaptor_registry=AdaptorRegistry(
                {"ContextAdaptor": lambda task: _ContextAdaptor()}
            ),
        )

        definitions = pipeline.build_openai_tools()
        results = await pipeline.execute_calls(
            [ToolCall("ping", {}, "call-1")]
        )
        messages = pipeline.build_tool_result_messages(results)

        self.assertEqual(definitions[0]["function"]["name"], "ping")
        self.assertEqual(
            gateway.calls[0].arguments,
            {"task_name": "Task"},
        )
        self.assertEqual(messages[0]["tool_call_id"], "call-1")


if __name__ == "__main__":
    unittest.main()
