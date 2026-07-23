from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import validate

from FileSystem.schemas import TOOL_DEFINITIONS
from FileSystem.server import create_server


EXPECTED_NAMES = [
    "list_directory",
    "read_file",
    "stat",
    "glob",
    "search_files",
    "create_file",
    "edit_file",
    "bash",
]


@pytest.mark.asyncio
async def test_lists_exact_documented_tool_set_and_schemas():
    server = create_server()
    listed = await server.list_tools()
    assert [tool.name for tool in listed] == EXPECTED_NAMES
    assert listed == list(TOOL_DEFINITIONS)
    for tool in listed:
        assert tool.description
        assert tool.inputSchema["additionalProperties"] is False
        assert tool.outputSchema is not None
        assert tool.outputSchema["additionalProperties"] is False


@pytest.mark.asyncio
async def test_success_returns_text_and_structured_content(tmp_path: Path):
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
async def test_errors_are_structured_and_argument_schema_is_enforced(tmp_path: Path):
    server = create_server()

    invalid = await server.call_tool(
        "read_file",
        {"path": str(tmp_path / "missing"), "extra": True},
    )
    assert invalid.isError is True
    assert invalid.structuredContent["error"]["code"] == "invalid_arguments"
    assert invalid.content[0].text

    missing = await server.call_tool(
        "read_file",
        {"path": str(tmp_path / "missing")},
    )
    assert missing.isError is True
    assert missing.structuredContent["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_unknown_tool_is_error():
    result = await create_server().call_tool("unknown", {})
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "unknown_tool"
