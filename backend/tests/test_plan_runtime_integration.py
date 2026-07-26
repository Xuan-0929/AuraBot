"""验证 Plan Harness 经原生 MCP 到应用服务的集成契约；用进程内 ASGI 与脚本化 LLM 夹具串联相邻层，
不负责真实网络监听、外部 MCP 进程或模型质量。"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from jsonschema import validate
from mcp.types import CallToolResult, TextContent

from backend.agent.llm_client import ChatCompletionChoice
from backend.agent.task.model import Task, TaskState, TaskType
from backend.agent.task.manager import TaskManager
from backend.agent.task.harness.factory import HarnessFactory
from backend.agent.tools.core.model import ToolCall
from backend.agent.tools.core.gateway import ToolCallGateway
from backend.app.gateway.dependencies import get_plan_tool_service
from backend.app.gateway.errors import application_error_handler
from backend.app.gateway.plan_tool_routes import router
from backend.app.services.errors import ApplicationError
from backend.app.services.plan_tool_service import PlanToolApplicationService
from backend.tests.task_runtime_helpers import retain_test_harness


MCP_SERVER_ROOT = Path(__file__).resolve().parents[2] / "mcp_server"
if str(MCP_SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(MCP_SERVER_ROOT))

from Plan.schemas import TOOL_BY_NAME
from Plan.tools import PlanToolHandlers


class FakeLLMClient:
    pass


class ScriptedPlanLLMClient:
    def __init__(self, choices):
        self.choices = list(choices)
        self.requests = []

    async def invoke(self, messages, *, tools=None, tool_choice=None):
        self.requests.append(
            {
                "messages": [dict(message) for message in messages],
                "tools": tools,
                "tool_choice": tool_choice,
            }
        )
        return self.choices.pop(0)


def tool_choice(name, arguments, call_id):
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


def completed_choice():
    return ChatCompletionChoice(
        message={
            "role": "assistant",
            "content": (
                "Status: completed\n"
                "Summary: Runtime shipped\n"
                "Artifacts: none\n"
                "Verification: Plan completed through native MCP\n"
                "Blocker: none"
            ),
        },
        finish_reason="stop",
    )


class InProcessNativePlanClient:
    """Compose the real Plan proxy with backend ASGI without a TCP listener."""

    def __init__(self, handlers):
        self.handlers = handlers
        self.requests = []

    async def call_tool(
        self,
        server_url,
        tool_name,
        arguments,
        *,
        meta=None,
    ):
        self.requests.append((server_url, tool_name, arguments, meta))
        tool = TOOL_BY_NAME[tool_name]
        validate(arguments, tool.inputSchema)
        task_name = meta["aurabot"]["task_name"]
        value = await self.handlers.call(tool_name, arguments, task_name)
        validate(value, tool.outputSchema)
        return CallToolResult(
            content=[TextContent(type="text", text="ok")],
            structuredContent=value,
            isError=False,
        )


class PlanRuntimeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager = TaskManager()
        application = FastAPI()
        application.add_exception_handler(
            ApplicationError,
            application_error_handler,
        )
        application.include_router(router)
        application.dependency_overrides[get_plan_tool_service] = (
            lambda: PlanToolApplicationService(self.manager)
        )
        backend_transport = httpx.ASGITransport(app=application)
        handlers = PlanToolHandlers(6401, transport=backend_transport)
        self.mcp_client = InProcessNativePlanClient(handlers)

    async def asyncTearDown(self):
        self.manager.shutdown()

    async def test_factory_to_adaptor_to_mcp_proxy_to_harness_plan(self):
        task = Task(
            1,
            "Integrated Plan Task",
            "session",
            TaskType.PlanAndSolve,
            "Ship the runtime",
        )
        factory = HarnessFactory(
            task_llm_client=FakeLLMClient(),
            tool_call_gateway_factory=lambda task: ToolCallGateway(
                self.mcp_client
            ),
        )
        with patch(
            "backend.config.get_config",
            return_value={
                "local_mcp_server": "http://127.0.0.1:6410/mcp"
            },
        ):
            harness = factory.create(task)
        retain_test_harness(self.manager, harness)

        openai_tools = harness.build_openai_tools()
        self.assertEqual(
            [definition["function"]["name"] for definition in openai_tools],
            [
                "create_plan",
                "read_plan",
                "update_plan",
                "plan_step_complete",
            ],
        )
        self.assertNotIn("task_name", str(openai_tools))

        async def execute(name, arguments, call_id):
            resolved = harness.resolve_and_adapt_tool_calls(
                [ToolCall(name, arguments, call_id)]
            )[0]
            self.assertEqual(
                resolved.mcp_meta,
                {"aurabot": {"task_name": "Integrated Plan Task"}},
            )
            result = await harness.tool_call_gateway.execute(resolved)
            self.assertFalse(result.is_error, result)
            return result.structured_content

        steps = [
            {
                "id": "s1",
                "title": "Build",
                "description": "Build runtime",
                "expected_output": "Runtime built",
            },
            {
                "id": "s2",
                "title": "Verify",
                "description": "Verify runtime",
                "expected_output": "Tests pass",
            },
        ]
        created = await execute(
            "create_plan",
            {"goal": "Ship", "steps": steps},
            "create",
        )
        self.assertEqual(created["current_step_id"], "s1")
        first = await execute(
            "plan_step_complete",
            {"step_id": "s1", "completion_summary": "Runtime built"},
            "complete-1",
        )
        self.assertEqual(first["next_step_id"], "s2")
        updated = await execute(
            "update_plan",
            {
                "goal": "Ship safely",
                "steps": [
                    steps[0],
                    {**steps[1], "title": "Verify thoroughly"},
                ],
            },
            "update",
        )
        self.assertEqual(updated["current_step_id"], "s2")
        second = await execute(
            "plan_step_complete",
            {"step_id": "s2", "completion_summary": "All tests pass"},
            "complete-2",
        )
        self.assertTrue(second["is_complete"])
        snapshot = await execute("read_plan", {}, "read")
        self.assertTrue(snapshot["is_complete"])
        self.assertEqual(snapshot["completed_step_count"], 2)
        self.assertTrue(
            all(
                request[3]
                == {"aurabot": {"task_name": "Integrated Plan Task"}}
                for request in self.mcp_client.requests
            )
        )

    async def test_run_drives_plan_tools_through_mcp_and_completes(self):
        steps = [
            {
                "id": "s1",
                "title": "Build",
                "description": "Build runtime",
                "expected_output": "Runtime built",
            }
        ]
        llm_client = ScriptedPlanLLMClient(
            [
                tool_choice(
                    "create_plan",
                    {"goal": "Ship", "steps": steps},
                    "create",
                ),
                tool_choice(
                    "plan_step_complete",
                    {
                        "step_id": "s1",
                        "completion_summary": "Runtime built and verified",
                    },
                    "complete",
                ),
                completed_choice(),
            ]
        )
        task = Task(
            2,
            "Run Integrated Plan Task",
            "session",
            TaskType.PlanAndSolve,
            "Ship the runtime",
        )
        factory = HarnessFactory(
            task_llm_client=llm_client,
            tool_call_gateway_factory=lambda task: ToolCallGateway(
                self.mcp_client
            ),
        )
        with patch(
            "backend.config.get_config",
            return_value={
                "local_mcp_server": "http://127.0.0.1:6410/mcp"
            },
        ):
            harness = factory.create(task)

        retain_test_harness(self.manager, harness)
        response = await harness.run()

        self.assertTrue(response.startswith("Status: completed"))
        self.assertIs(task.state, TaskState.Completed)
        self.assertTrue(harness.plan.is_complete)
        self.assertEqual(
            [request[1] for request in self.mcp_client.requests],
            ["create_plan", "plan_step_complete"],
        )
        self.assertTrue(
            all(
                request[3]
                == {"aurabot": {"task_name": "Run Integrated Plan Task"}}
                for request in self.mcp_client.requests
            )
        )
        self.assertEqual(len(llm_client.requests), 3)


if __name__ == "__main__":
    unittest.main()
