"""
验证 Backend native Plan ToolGroup 的名称、schema、说明与 adaptor 配置。

测试对 canonical 声明做真实 JSON Schema 校验，不执行 PlanningRuntime 或 MCP handler。
"""

import unittest

from jsonschema import Draft202012Validator

from backend.agent.tools.native.plan import (
    PLAN_CONTEXT_ADAPTOR,
    PLAN_TOOL_DEFINITIONS,
    PLAN_TOOL_NAMES,
    create_plan_tool_group,
)


class PlanToolGroupTests(unittest.TestCase):
    def test_builds_exact_canonical_group_without_task_context_in_schema(self):
        group = create_plan_tool_group("http://127.0.0.1:6410/mcp")

        self.assertEqual(tuple(tool.name for tool in group.tools), PLAN_TOOL_NAMES)
        for tool, definition in zip(group.tools, PLAN_TOOL_DEFINITIONS):
            self.assertEqual(tool.description, definition["description"])
            self.assertEqual(tool.input_schema, definition["inputSchema"])
            self.assertEqual(tool.output_schema, definition["outputSchema"])
            self.assertEqual(tool.adaptor_names, (PLAN_CONTEXT_ADAPTOR,))
            self.assertNotIn("task_name", str(tool.input_schema))
            self.assertNotIn("task_name", str(tool.output_schema))
            Draft202012Validator.check_schema(tool.input_schema)
            Draft202012Validator.check_schema(tool.output_schema)


if __name__ == "__main__":
    unittest.main()
