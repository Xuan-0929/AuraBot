import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.tools.Tool import Tool
from agent.tools.ToolGroup import ToolGroup
from backend.config import Config


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
                tool = Tool("search", "Search locally")

        self.assertEqual(tool.server_url, "localhost:7654")

    def test_explicit_server_url_does_not_require_initialized_config(self):
        with patch("backend.config.config", Config()):
            tool = Tool(
                "search",
                "Search remotely",
                server_url="https://mcp.example.com",
            )

        self.assertEqual(tool.server_url, "https://mcp.example.com")

    def test_tool_load_intro_returns_complete_xml_function_schema(self):
        tool = Tool(
            "search & inspect",
            "Find <matches>",
            server_url="localhost:6410",
        )

        self.assertEqual(
            tool.load_intro(),
            "<tool>\n"
            "<type>function</type>\n"
            "<function>\n"
            "<name>search &amp; inspect</name>\n"
            "<description>Find &lt;matches&gt;</description>\n"
            "<parameters>\n"
            "<type>object</type>\n"
            "<properties>\n"
            "<query>\n"
            "<type>string</type>\n"
            "<description>Input query for the tool.</description>\n"
            "</query>\n"
            "</properties>\n"
            "<required>\n"
            "<field>query</field>\n"
            "</required>\n"
            "</parameters>\n"
            "</function>\n"
            "</tool>",
        )

    def test_tool_group_lists_prompt_ready_tool_descriptions(self):
        group = ToolGroup(
            "filesystem & shell",
            [
                Tool(
                    "read <file>",
                    "Read & inspect a file",
                    server_url="localhost:6410",
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
