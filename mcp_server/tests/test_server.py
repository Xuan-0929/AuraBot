from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import validate

from FileSystem.schemas import TOOL_DEFINITIONS as FILESYSTEM_DEFINITIONS
from Plan.schemas import TOOL_DEFINITIONS as PLAN_DEFINITIONS
from server import TOOL_DEFINITIONS, create_server


EXPECTED_NAMES = [
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


class RecordingPlanHandlers:
    def __init__(self, result=None):
        self.result = result or {
            "created": True,
            "current_step_id": "s1",
            "total_step_count": 1,
        }
        self.calls = []

    async def call(self, name, arguments, task_name):
        self.calls.append((name, arguments, task_name))
        return self.result


@pytest.mark.asyncio
async def test_lists_exact_aggregated_tool_set_and_schemas():
    server = create_server()
    listed = await server.list_tools()
    assert [tool.name for tool in listed] == EXPECTED_NAMES
    assert listed == list(TOOL_DEFINITIONS)
    assert tuple(listed[:8]) == FILESYSTEM_DEFINITIONS
    assert tuple(listed[8:]) == PLAN_DEFINITIONS
    for tool in listed:
        assert tool.description
        assert tool.inputSchema["additionalProperties"] is False
        assert tool.outputSchema is not None
        assert tool.outputSchema["additionalProperties"] is False


@pytest.mark.asyncio
async def test_filesystem_success_returns_text_and_structured_content(
    tmp_path: Path,
):
    path = tmp_path / "file.txt"
    path.write_text("hello", encoding="utf-8")
    server = create_server()

    result = await server.call_tool("read_file", {"path": str(path)})

    assert result.isError is False
    assert result.structuredContent["content"] == "hello"
    assert json.loads(result.content[0].text) == result.structuredContent
    schema = next(
        tool.outputSchema for tool in TOOL_DEFINITIONS if tool.name == "read_file"
    )
    validate(result.structuredContent, schema)


@pytest.mark.asyncio
async def test_errors_are_structured_and_argument_schema_is_enforced(
    tmp_path: Path,
):
    server = create_server()

    invalid = await server.call_tool(
        "read_file",
        {"path": str(tmp_path / "missing"), "extra": True},
    )
    assert invalid.isError is True
    assert invalid.structuredContent["error"]["code"] == "invalid_arguments"

    missing = await server.call_tool(
        "read_file",
        {"path": str(tmp_path / "missing")},
    )
    assert missing.isError is True
    assert missing.structuredContent["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_plan_requires_valid_metadata_before_calling_backend():
    handlers = RecordingPlanHandlers()
    server = create_server(plan_handlers=handlers)

    for metadata in (
        None,
        {},
        {"aurabot": {}},
        {"aurabot": {"task_name": ""}},
        {"aurabot": {"task_name": " padded "}},
        {"aurabot": {"task_name": 42}},
    ):
        server.get_context = lambda metadata=metadata: SimpleNamespace(
            request_context=SimpleNamespace(
                meta=(
                    None
                    if metadata is None
                    else SimpleNamespace(model_extra=metadata)
                )
            )
        )
        result = await server.call_tool(
            "create_plan",
            {
                "goal": "Ship",
                "steps": [
                    {
                        "id": "s1",
                        "title": "First",
                        "description": "Do first",
                        "expected_output": "Done",
                    }
                ],
            },
        )
        assert result.isError is True
        assert (
            result.structuredContent["error"]["code"]
            == "invalid_task_context"
        )
    assert handlers.calls == []


@pytest.mark.asyncio
async def test_plan_reads_metadata_and_returns_valid_minimal_receipt():
    handlers = RecordingPlanHandlers()
    server = create_server(plan_handlers=handlers)
    server.get_context = lambda: SimpleNamespace(
        request_context=SimpleNamespace(
            meta=SimpleNamespace(
                model_extra={
                    "aurabot": {"task_name": "Unique task"},
                    "trace": {"id": "r1"},
                }
            )
        )
    )
    arguments = {
        "goal": "Ship",
        "steps": [
            {
                "id": "s1",
                "title": "First",
                "description": "Do first",
                "expected_output": "Done",
            }
        ],
    }

    result = await server.call_tool("create_plan", arguments)

    assert result.isError is False
    assert handlers.calls == [("create_plan", arguments, "Unique task")]
    assert "task_name" not in str(result.structuredContent)


@pytest.mark.asyncio
async def test_invalid_plan_backend_result_is_protocol_error():
    handlers = RecordingPlanHandlers({"unexpected": True})
    server = create_server(plan_handlers=handlers)
    server.get_context = lambda: SimpleNamespace(
        request_context=SimpleNamespace(
            meta=SimpleNamespace(
                model_extra={"aurabot": {"task_name": "Task"}}
            )
        )
    )
    result = await server.call_tool("read_plan", {})
    assert result.isError is True
    assert (
        result.structuredContent["error"]["code"]
        == "backend_protocol_error"
    )


@pytest.mark.asyncio
async def test_cross_field_invalid_plan_snapshot_is_protocol_error():
    handlers = RecordingPlanHandlers(
        {
            "goal": "Ship",
            "steps": [
                {
                    "id": "s1",
                    "title": "First",
                    "description": "Do first",
                    "expected_output": "Done",
                    "status": "pending",
                    "completion_summary": None,
                }
            ],
            "current_step_id": None,
            "completed_step_count": 0,
            "total_step_count": 1,
            "is_complete": True,
        }
    )
    server = create_server(plan_handlers=handlers)
    server.get_context = lambda: SimpleNamespace(
        request_context=SimpleNamespace(
            meta=SimpleNamespace(
                model_extra={"aurabot": {"task_name": "Task"}}
            )
        )
    )

    result = await server.call_tool("read_plan", {})

    assert result.isError is True
    assert (
        result.structuredContent["error"]["code"]
        == "backend_protocol_error"
    )


@pytest.mark.asyncio
async def test_unknown_tool_is_error():
    result = await create_server().call_tool("unknown", {})
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "unknown_tool"
