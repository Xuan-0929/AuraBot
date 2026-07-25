from __future__ import annotations

import sys
from pathlib import Path

from jsonschema import Draft202012Validator

from Plan.schemas import TOOL_DEFINITIONS


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agent.tools.native.PlanToolGroup import PLAN_TOOL_DEFINITIONS


def test_backend_and_mcp_plan_protocols_match_field_for_field():
    mcp_definitions = tuple(
        {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.inputSchema,
            "outputSchema": tool.outputSchema,
        }
        for tool in TOOL_DEFINITIONS
    )
    assert mcp_definitions == PLAN_TOOL_DEFINITIONS


def test_all_schemas_are_draft_2020_12_valid_and_hide_task_context():
    for tool in TOOL_DEFINITIONS:
        Draft202012Validator.check_schema(tool.inputSchema)
        Draft202012Validator.check_schema(tool.outputSchema)
        assert "task_name" not in str(tool.inputSchema)
        assert "task_name" not in str(tool.outputSchema)
