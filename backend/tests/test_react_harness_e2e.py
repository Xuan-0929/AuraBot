from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path

from mcp.types import Tool as MCPTool

from backend.agent.LLMClient import ChatCompletionChoice, LLMClient
from backend.agent.integration.mcp import MCPClient, MCPClientError
from backend.agent.task.Task import Task, TaskState, TaskType
from backend.agent.task.harness.ReActHarness import ReActHarness
from backend.agent.tools.Tool import Tool
from backend.agent.tools.ToolGroup import ToolGroup
from backend.config import Config


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MCP_SERVER_ROOT = PROJECT_ROOT / "mcp_server"
DEFAULT_MCP_PYTHON = MCP_SERVER_ROOT / ".venv" / "bin" / "python"
CONFIGURED_MCP_PYTHON = os.environ.get("AURABOT_MCP_PYTHON")
MCP_RUNTIME_AVAILABLE = (
    Path(CONFIGURED_MCP_PYTHON).is_file()
    if CONFIGURED_MCP_PYTHON
    else DEFAULT_MCP_PYTHON.is_file()
    or importlib.util.find_spec("bashlex") is not None
)
RUN_LIVE_MODEL_TEST = os.environ.get("AURABOT_RUN_LIVE_REACT_TEST") == "1"

BROKEN_SOURCE = """\
def add(left: int, right: int) -> int:
    \"\"\"Return the arithmetic sum of two integers.\"\"\"
    return left - right
"""
FIXED_SOURCE = BROKEN_SOURCE.replace(
    "    return left - right\n",
    "    return left + right\n",
)
TEST_SOURCE = """\
import unittest

from calculator import add


class CalculatorTests(unittest.TestCase):
    def test_adds_two_integers(self):
        self.assertEqual(add(7, 5), 12)


if __name__ == "__main__":
    unittest.main()
"""


def reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class RunningMCPServer:
    """Own one real mcp_server/main.py subprocess for an integration test."""

    def __init__(self):
        self.port = reserve_port()
        self.server_url = f"http://127.0.0.1:{self.port}/mcp"
        self.process: asyncio.subprocess.Process | None = None

    async def start(self) -> list[MCPTool]:
        python_path = (
            Path(CONFIGURED_MCP_PYTHON)
            if CONFIGURED_MCP_PYTHON
            else DEFAULT_MCP_PYTHON
        )
        if not python_path.is_file():
            python_path = Path(sys.executable)

        self.process = await asyncio.create_subprocess_exec(
            str(python_path),
            str(MCP_SERVER_ROOT / "main.py"),
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--path",
            "/mcp",
            "--log-level",
            "ERROR",
            cwd=MCP_SERVER_ROOT,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        client = MCPClient(timeout_seconds=2)
        last_error: Exception | None = None
        for _ in range(80):
            if self.process.returncode is not None:
                break
            try:
                return await client.list_tools(self.server_url)
            except (MCPClientError, ExceptionGroup) as exc:
                last_error = exc
                await asyncio.sleep(0.1)

        raise RuntimeError("MCP server did not become ready") from last_error

    async def close(self) -> None:
        if self.process is None or self.process.returncode is not None:
            return
        self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), timeout=5)
        except TimeoutError:
            self.process.kill()
            await self.process.wait()


def build_filesystem_group(
    server_url: str,
    definitions: list[MCPTool],
) -> ToolGroup:
    tools = [
        Tool(
            name=definition.name,
            description=definition.description or definition.name,
            server_url=server_url,
            input_schema=definition.inputSchema,
            output_schema=definition.outputSchema,
            adaptor_names=("FileSystemAdaptor",),
        )
        for definition in definitions
    ]
    return ToolGroup(
        "FileSystem",
        tools,
        "Explore, edit, and verify files in the Task working directory.",
    )


def build_programming_task(
    workspace: Path,
    filesystem_group: ToolGroup,
) -> Task:
    return Task(
        task_id=1,
        task_title="Fix integer addition",
        parent_session_id="react-e2e",
        task_type=TaskType.ReAct,
        task_prompt=(
            "Fix calculator.py so add(left, right) returns the arithmetic sum. "
            "Inspect the existing files, make the smallest correct edit, and "
            "run `python3 -m unittest -v` to verify the change."
        ),
        workdir=str(workspace),
        constraints="Only modify calculator.py.",
        tool_groups=[filesystem_group],
        timeout_ms=180_000,
    )


class ScriptedLLMClient:
    """Drive a deterministic ReAct trace while all tool I/O stays real."""

    def __init__(self):
        self.choices = [
            self._tool_choice(
                "call_read",
                "read_file",
                {"path": "calculator.py"},
            ),
            self._tool_choice(
                "call_edit",
                "edit_file",
                {
                    "path": "calculator.py",
                    "edits": [
                        {
                            "old_text": BROKEN_SOURCE,
                            "new_text": FIXED_SOURCE,
                        }
                    ],
                },
            ),
            self._tool_choice(
                "call_test",
                "bash",
                {
                    "statement": "python3 -m unittest -v",
                    "PATH": ".",
                },
            ),
            ChatCompletionChoice(
                message={
                    "role": "assistant",
                    "content": (
                        "Status: completed\n"
                        "Summary: Fixed integer addition.\n"
                        "Artifacts: calculator.py\n"
                        "Verification: python3 -m unittest -v passed.\n"
                        "Blocker: none"
                    ),
                },
                finish_reason="stop",
            ),
        ]

    async def invoke(self, messages, *, tools=None, tool_choice=None):
        if not tools or tool_choice != "auto":
            raise AssertionError("ReActHarness did not expose FileSystem tools")
        return self.choices.pop(0)

    @staticmethod
    def _tool_choice(
        call_id: str,
        name: str,
        arguments: dict,
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
                            "arguments": json.dumps(arguments),
                        },
                    }
                ],
            },
            finish_reason="tool_calls",
        )


class ReActHarnessMCPTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._old_no_proxy = os.environ.get("NO_PROXY")
        self._old_no_proxy_lower = os.environ.get("no_proxy")
        os.environ["NO_PROXY"] = "127.0.0.1,localhost"
        os.environ["no_proxy"] = "127.0.0.1,localhost"
        self.addCleanup(
            self._restore_environment,
            "NO_PROXY",
            self._old_no_proxy,
        )
        self.addCleanup(
            self._restore_environment,
            "no_proxy",
            self._old_no_proxy_lower,
        )

        self.server = RunningMCPServer()
        self.addAsyncCleanup(self.server.close)
        self.tool_definitions = await self.server.start()
        self.temporary_directory = tempfile.TemporaryDirectory(
            prefix="aurabot-react-e2e-"
        )
        self.addCleanup(self.temporary_directory.cleanup)
        self.workspace = Path(self.temporary_directory.name).resolve()
        (self.workspace / "calculator.py").write_text(
            BROKEN_SOURCE,
            encoding="utf-8",
        )
        (self.workspace / "test_calculator.py").write_text(
            TEST_SOURCE,
            encoding="utf-8",
        )
        self.filesystem_group = build_filesystem_group(
            self.server.server_url,
            self.tool_definitions,
        )

    @staticmethod
    def _restore_environment(name: str, value: str | None) -> None:
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


@unittest.skipUnless(
    MCP_RUNTIME_AVAILABLE,
    "install mcp_server dependencies or set AURABOT_MCP_PYTHON",
)
class ReActHarnessMCPIntegrationTests(ReActHarnessMCPTestCase):
    async def test_task_harness_and_real_mcp_complete_programming_task(self):
        task = build_programming_task(self.workspace, self.filesystem_group)
        harness = ReActHarness(
            task=task,
            llm_client=ScriptedLLMClient(),  # type: ignore[arg-type]
        )

        result = await harness.run()

        self.assertEqual(task.state, TaskState.Completed)
        self.assertIn("Status: completed", result)
        self.assertEqual(
            (self.workspace / "calculator.py").read_text(encoding="utf-8"),
            FIXED_SOURCE,
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
                "tool",
                "assistant",
            ],
        )
        bash_observation = json.loads(task.messages[-2]["content"])
        self.assertFalse(bash_observation["is_error"])
        self.assertEqual(
            bash_observation["structured_content"]["exit_code"],
            0,
        )


@unittest.skipUnless(
    MCP_RUNTIME_AVAILABLE and RUN_LIVE_MODEL_TEST,
    "install the MCP runtime and set AURABOT_RUN_LIVE_REACT_TEST=1",
)
class ReActHarnessLiveModelTests(ReActHarnessMCPTestCase):
    async def test_configured_model_solves_programming_task_via_mcp(self):
        runtime_config = Config().initialize(PROJECT_ROOT / "config.yaml")
        model_config = runtime_config.task_model
        llm_client = LLMClient(
            base_url=model_config["base_url"],
            api_key=model_config["api_key"],
            model=model_config["model_name"],
            timeout=90,
        )
        task = build_programming_task(self.workspace, self.filesystem_group)
        harness = ReActHarness(task=task, llm_client=llm_client)

        try:
            result = await harness.run()
        finally:
            await llm_client.aclose()

        self.assertEqual(task.state, TaskState.Completed)
        self.assertIn("Status: completed", result)
        self.assertEqual(
            (self.workspace / "calculator.py").read_text(encoding="utf-8"),
            FIXED_SOURCE,
        )
        self.assertTrue(
            any(message["role"] == "tool" for message in task.messages)
        )


if __name__ == "__main__":
    unittest.main()
