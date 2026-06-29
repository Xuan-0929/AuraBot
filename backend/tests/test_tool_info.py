import json
import unittest

from agent.tools.Tool import Tool
from agent.tools.ToolGroup import ToolGroup


class ToolInfoTests(unittest.TestCase):
    def test_tool_get_info_returns_openai_compatible_function_schema(self):
        tool = Tool("search", "Search the workspace")

        info = json.loads(tool.get_info())

        self.assertEqual(
            info,
            {
                "type": "function",
                "function": {
                    "name": "search",
                    "description": "Search the workspace",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Input query for the tool.",
                            }
                        },
                        "required": ["query"],
                    },
                },
            },
        )

    def test_tool_group_get_info_returns_openai_compatible_tools_array(self):
        group = ToolGroup(
            "filesystem",
            [
                Tool("read_file", "Read a file"),
                Tool("write_file", "Write a file"),
            ],
        )

        info = json.loads(group.get_info())

        self.assertEqual([tool["function"]["name"] for tool in info], ["read_file", "write_file"])
        self.assertEqual([tool["type"] for tool in info], ["function", "function"])


if __name__ == "__main__":
    unittest.main()
