"""
验证 Tool 领域模型的 schema 约束、文件构造与说明序列化。

测试以临时 fixture 读取真实定义，不覆盖 ToolGroup manager、resolver 或工具执行。
"""

import tempfile
import unittest
from json import JSONDecodeError
from pathlib import Path
from xml.etree import ElementTree
from unittest.mock import patch

from backend.agent.tools.tool import Tool
from backend.agent.tools.tool_group import ToolGroup
from backend.agent.tools.utils.formatter import (
    format_tool_group_intro,
    parse_optional_schema_json,
    parse_schema_json,
    serialize_schema,
)
from backend.config import Config


EMPTY_INPUT_SCHEMA = {"type": "object", "properties": {}}


class ToolFormatterTests(unittest.TestCase):
    def test_schema_json_helpers_round_trip_compact_unicode_json(self):
        schema = {"type": "object", "title": "中文"}

        serialized = serialize_schema(schema)

        self.assertEqual(serialized, '{"type":"object","title":"中文"}')
        self.assertEqual(parse_schema_json(serialized), schema)
        self.assertIsNone(parse_optional_schema_json(None))

    def test_schema_json_helpers_reject_non_object_json(self):
        with self.assertRaises(TypeError):
            serialize_schema([])
        with self.assertRaises(ValueError):
            parse_schema_json("[]")
        with self.assertRaises(JSONDecodeError):
            parse_schema_json("not-json")

    def test_empty_tool_group_intro_has_valid_tools_container(self):
        self.assertEqual(
            format_tool_group_intro(
                name="empty & safe",
                description="No <tools>",
                tool_intros=[],
            ),
            "<tool_group>\n"
            "<name>empty &amp; safe</name>\n"
            "<description>No &lt;tools&gt;</description>\n"
            "<tools>\n"
            "</tools>\n"
            "</tool_group>",
        )


class ToolDescriptionTests(unittest.TestCase):
    def test_tool_uses_configured_local_server_when_url_is_omitted(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            config_path.write_text(
                'local_mcp_server: "localhost:7654"\n',
                encoding="utf-8",
            )
            runtime_config = Config().initialize(config_path)

            with patch("backend.config.config", runtime_config):
                tool = Tool(
                    "search",
                    "Search locally",
                    input_schema=EMPTY_INPUT_SCHEMA,
                )

        self.assertEqual(tool.server_url, "localhost:7654")

    def test_explicit_server_url_does_not_require_initialized_config(self):
        with patch("backend.config.config", Config()):
            tool = Tool(
                "search",
                "Search remotely",
                server_url="https://mcp.example.com",
                input_schema=EMPTY_INPUT_SCHEMA,
            )

        self.assertEqual(tool.server_url, "https://mcp.example.com")

    def test_tool_requires_object_schemas(self):
        with self.assertRaises(ValueError):
            Tool("search", "Search locally", server_url="localhost:6410")
        with self.assertRaises(TypeError):
            Tool(
                "search",
                "Search locally",
                server_url="localhost:6410",
                input_schema=[],
            )
        with self.assertRaises(TypeError):
            Tool(
                "search",
                "Search locally",
                server_url="localhost:6410",
                input_schema=EMPTY_INPUT_SCHEMA,
                output_schema=[],
            )

    def test_tool_carries_ordered_immutable_adaptor_names(self):
        adaptor_names = ["FirstAdaptor", "SecondAdaptor"]
        tool = Tool(
            "read_file",
            "Read a file",
            server_url="localhost:6410",
            input_schema=EMPTY_INPUT_SCHEMA,
            adaptor_names=adaptor_names,
        )
        adaptor_names.append("LaterAdaptor")

        self.assertEqual(
            tool.adaptor_names,
            ("FirstAdaptor", "SecondAdaptor"),
        )
        self.assertIsInstance(tool.adaptor_names, tuple)

    def test_tool_rejects_invalid_adaptor_descriptions(self):
        with self.assertRaises(ValueError):
            Tool(
                "read_file",
                "Read a file",
                server_url="localhost:6410",
                input_schema=EMPTY_INPUT_SCHEMA,
                adaptor_names=(" FileSystemAdaptor",),
            )
        with self.assertRaises(ValueError):
            Tool(
                "read_file",
                "Read a file",
                server_url="localhost:6410",
                input_schema=EMPTY_INPUT_SCHEMA,
                adaptor_names=("SameAdaptor", "SameAdaptor"),
            )
        with self.assertRaises(TypeError):
            Tool(
                "read_file",
                "Read a file",
                server_url="localhost:6410",
                input_schema=EMPTY_INPUT_SCHEMA,
                adaptor_names="FileSystemAdaptor",
            )

    def test_tool_load_intro_returns_complete_xml_function_schema(self):
        input_schema = {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Find <term> & 中文",
                }
            },
            "required": ["query"],
        }
        output_schema = {
            "type": "object",
            "properties": {"matches": {"type": "array"}},
        }
        tool = Tool(
            "search & inspect",
            "Find <matches>",
            server_url="localhost:6410",
            input_schema=input_schema,
            output_schema=output_schema,
        )

        intro = tool.load_intro()
        function = ElementTree.fromstring(intro).find("function")

        self.assertIsNotNone(function)
        self.assertEqual(function.findtext("name"), "search & inspect")
        self.assertEqual(function.findtext("description"), "Find <matches>")
        self.assertEqual(
            parse_schema_json(function.findtext("parameters")),
            input_schema,
        )
        self.assertEqual(
            parse_schema_json(function.findtext("returns")),
            output_schema,
        )
        self.assertIn("Find &lt;term&gt; &amp; 中文", intro)
        self.assertNotIn("Input query for the tool.", intro)

    def test_tool_intro_omits_returns_without_output_schema(self):
        tool = Tool(
            "search",
            "Search locally",
            server_url="localhost:6410",
            input_schema=EMPTY_INPUT_SCHEMA,
        )

        function = ElementTree.fromstring(tool.load_intro()).find("function")

        self.assertIsNotNone(function)
        self.assertIsNone(function.find("returns"))

    def test_tool_group_lists_prompt_ready_tool_descriptions(self):
        group = ToolGroup(
            "filesystem & shell",
            [
                Tool(
                    "read <file>",
                    "Read & inspect a file",
                    server_url="localhost:6410",
                    input_schema={
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                )
            ],
            "Local <tools>",
        )

        description = group.list_tools_descriptions()

        self.assertEqual(
            description,
            "<tool_group>\n"
            "<name>filesystem &amp; shell</name>\n"
            "<description>Local &lt;tools&gt;</description>\n"
            "<tools>\n"
            f"{group.tools[0].load_intro()}\n"
            "</tools>\n"
            "</tool_group>",
        )


if __name__ == "__main__":
    unittest.main()
