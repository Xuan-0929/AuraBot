"""
通过真实 HTTP 端点冒烟验证 MCP Server 与 Backend ToolCallGateway 的集成。

脚本在临时目录创建并编辑文件，从 MCP 响应和本地文件系统两侧核对结果，最后清理
所有测试产物；它不使用进程内 ASGI transport，也不修改服务端持久业务数据。
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx


MCP_SERVER_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = MCP_SERVER_ROOT.parent
MAIN_PATH = MCP_SERVER_ROOT / "main.py"
MCP_PROTOCOL_VERSION = "2025-11-25"


class RawMCPClient:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.next_id = 1

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        response = await self.client.post("", json=payload)
        response.raise_for_status()
        body = response.json()
        if body.get("id") != request_id:
            raise AssertionError(f"Unexpected JSON-RPC response id: {body!r}")
        if "error" in body:
            raise AssertionError(f"JSON-RPC error: {body['error']!r}")
        return body["result"]

    async def notify(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        response = await self.client.post("", json=payload)
        if response.status_code not in {200, 202, 204}:
            response.raise_for_status()

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        meta: dict[str, Any] | None = None,
        expect_error: bool = False,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "name": name,
            "arguments": arguments,
        }
        if meta is not None:
            params["_meta"] = meta
        result = await self.request(
            "tools/call",
            params,
        )
        if result.get("isError") and not expect_error:
            raise AssertionError(f"{name} returned Tool error: {result!r}")
        if expect_error and not result.get("isError"):
            raise AssertionError(f"{name} unexpectedly succeeded: {result!r}")
        structured = result.get("structuredContent")
        if not isinstance(structured, dict):
            raise AssertionError(f"{name} omitted structuredContent")
        return structured


def reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def wait_until_ready(raw: RawMCPClient) -> dict[str, Any]:
    last_error: Exception | None = None
    for _ in range(80):
        try:
            return await raw.request(
                "initialize",
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "aurabot-http-smoke-test",
                        "version": "1.0",
                    },
                },
            )
        except (httpx.HTTPError, AssertionError, json.JSONDecodeError) as exc:
            last_error = exc
            await asyncio.sleep(0.1)
    raise RuntimeError("MCP server did not become ready.") from last_error


async def verify_gateway(
    *,
    server_url: str,
    target: Path,
    read_input_schema: dict[str, Any],
) -> None:
    sys.path.insert(0, str(PROJECT_ROOT))
    from backend.agent.integration.mcp.client import MCPClient
    from backend.agent.task.harness.adaptors.file_system import (
        FileSystemAdaptor,
    )
    from backend.agent.tools.tool import Tool
    from backend.agent.tools.core.model import ResolvedToolCall
    from backend.agent.tools.core.gateway import ToolCallGateway

    tool = Tool(
        name="read_file",
        description="Read UTF-8 file",
        server_url=server_url,
        input_schema=read_input_schema,
        adaptor_names=("FileSystemAdaptor",),
    )
    unresolved = ResolvedToolCall(
        tool=tool,
        arguments={"path": target.name},
        call_id="smoke_gateway_read",
    )
    adapted = FileSystemAdaptor(str(target.parent)).adapt(unresolved)
    proxy_keys = {
        key
        for key in os.environ
        if key.casefold()
        in {
            "all_proxy",
            "http_proxy",
            "https_proxy",
        }
    }
    saved_proxies = {key: os.environ.pop(key) for key in proxy_keys}
    try:
        result = await ToolCallGateway(
            MCPClient(timeout_seconds=5),
        ).execute(adapted)
    finally:
        os.environ.update(saved_proxies)

    assert result.is_error is False, result
    assert result.call_id == "smoke_gateway_read"
    assert result.structured_content is not None
    assert result.structured_content["content"] == "beta\nhello 世界\n"


async def run_smoke_test() -> None:
    port = reserve_port()
    endpoint = f"http://127.0.0.1:{port}/mcp"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(MAIN_PATH),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--path",
        "/mcp",
        "--log-level",
        "ERROR",
        cwd=MCP_SERVER_ROOT,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )

    removed_path: Path | None = None
    try:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        }
        async with httpx.AsyncClient(
            base_url=endpoint,
            headers=headers,
            timeout=5,
            trust_env=False,
            follow_redirects=True,
        ) as client:
            raw = RawMCPClient(client)
            initialized = await wait_until_ready(raw)
            assert initialized["protocolVersion"] == MCP_PROTOCOL_VERSION
            await raw.notify("notifications/initialized")

            listed = await raw.request("tools/list")
            tools = listed["tools"]
            assert [tool["name"] for tool in tools] == [
                "list_directory",
                "read_file",
                "stat",
                "glob",
                "search_files",
                "create_file",
                "edit_file",
                "bash",
                "create_plan",
                "read_plan",
                "update_plan",
                "plan_step_complete",
            ]
            invalid_context = await raw.call_tool(
                "read_plan",
                {},
                expect_error=True,
            )
            assert invalid_context["error"]["code"] == "invalid_task_context"
            read_schema = next(
                tool["inputSchema"]
                for tool in tools
                if tool["name"] == "read_file"
            )

            with tempfile.TemporaryDirectory(
                prefix="aurabot-mcp-smoke-"
            ) as directory:
                root = Path(directory).resolve()
                removed_path = root
                (root / ".git").mkdir()
                (root / ".github").mkdir()
                target = root / "created.txt"

                created = await raw.call_tool(
                    "create_file",
                    {"url": target.as_uri()},
                )
                assert created["created"] is True
                assert target.exists()

                first_edit = await raw.call_tool(
                    "edit_file",
                    {
                        "path": str(target),
                        "edits": [
                            {
                                "old_text": "",
                                "new_text": "alpha\nhello 世界\n",
                            }
                        ],
                    },
                )
                assert first_edit["changed"] is True
                assert target.read_text(encoding="utf-8") == "alpha\nhello 世界\n"

                first_read = await raw.call_tool(
                    "read_file",
                    {"path": str(target)},
                )
                second_edit = await raw.call_tool(
                    "edit_file",
                    {
                        "path": str(target),
                        "expected_sha256": first_read["sha256"],
                        "edits": [
                            {"old_text": "alpha", "new_text": "beta"}
                        ],
                    },
                )
                assert second_edit["changed"] is True
                assert target.read_text(encoding="utf-8") == "beta\nhello 世界\n"

                second_read = await raw.call_tool(
                    "read_file",
                    {"path": str(target)},
                )
                assert second_read["content"] == "beta\nhello 世界\n"

                metadata = await raw.call_tool(
                    "stat",
                    {"path": str(target)},
                )
                assert metadata["kind"] == "file"

                directory_result = await raw.call_tool(
                    "list_directory",
                    {"path": str(root)},
                )
                names = [entry["name"] for entry in directory_result["entries"]]
                assert ".git" not in names
                assert ".github" in names
                assert "created.txt" in names

                globbed = await raw.call_tool(
                    "glob",
                    {"root": str(root), "pattern": "**/*.txt"},
                )
                assert [item["path"] for item in globbed["matches"]] == [
                    str(target)
                ]

                searched = await raw.call_tool(
                    "search_files",
                    {"root": str(root), "query": "世界", "glob": "**/*.txt"},
                )
                assert searched["total_matches"] == 1
                assert searched["matches"][0]["line"] == 2

                bash_result = await raw.call_tool(
                    "bash",
                    {"PATH": str(root), "statement": "pwd; printf smoke"},
                )
                assert bash_result["cwd"] == str(root)
                assert bash_result["stdout"].endswith("\nsmoke")
                assert bash_result["exit_code"] == 0

                await verify_gateway(
                    server_url=endpoint,
                    target=target,
                    read_input_schema=read_schema,
                )
    finally:
        if process.returncode is None:
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except TimeoutError:
            process.kill()
            await process.wait()

    if removed_path is None:
        raise AssertionError("Smoke test did not create its temporary workspace.")
    assert not removed_path.exists(), f"Temporary workspace was not removed: {removed_path}"
    print(
        "PASS: raw HTTP MCP and ToolCallGateway created, edited, read, "
        "verified all 12 native tools, rejected missing Plan metadata, and "
        "cleaned the temporary file."
    )


if __name__ == "__main__":
    asyncio.run(run_smoke_test())
