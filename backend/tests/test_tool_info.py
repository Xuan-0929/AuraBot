import unittest

from agent.tools.Tool import Tool
from agent.tools.ToolGroup import ToolGroup


class ToolDescriptionTests(unittest.TestCase):
    def test_tool_load_intro_returns_complete_xml_function_schema(self):
        tool = Tool("search & inspect", "Find <matches>")

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
            [Tool("read <file>", "Read & inspect a file")],
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
