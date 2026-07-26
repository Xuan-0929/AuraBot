from __future__ import annotations

import sys
from pathlib import Path

from jsonschema import Draft202012Validator, validate

from Plan.schemas import TOOL_DEFINITIONS


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agent.tools.native.plan import PLAN_TOOL_DEFINITIONS


STEP = {
    "id": "s1",
    "title": "Build",
    "description": "Build the runtime",
    "expected_output": "Runtime built",
}
CONTRACT_EXAMPLES = {
    "create_plan": (
        {"goal": "Ship", "steps": [STEP]},
        {
            "created": True,
            "current_step_id": "s1",
            "total_step_count": 1,
        },
    ),
    "read_plan": (
        {},
        {
            "goal": "Ship",
            "steps": [
                {
                    **STEP,
                    "status": "pending",
                    "completion_summary": None,
                }
            ],
            "current_step_id": "s1",
            "completed_step_count": 0,
            "total_step_count": 1,
            "is_complete": False,
        },
    ),
    "update_plan": (
        {"goal": "Ship safely", "steps": [STEP]},
        {
            "updated": True,
            "current_step_id": "s1",
            "total_step_count": 1,
        },
    ),
    "plan_step_complete": (
        {"step_id": "s1", "completion_summary": "Verified"},
        {
            "completed": True,
            "completed_step_id": "s1",
            "next_step_id": None,
            "is_complete": True,
        },
    ),
}


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


def test_both_schema_copies_accept_the_same_canonical_contract_examples():
    mcp_by_name = {tool.name: tool for tool in TOOL_DEFINITIONS}
    backend_by_name = {
        definition["name"]: definition
        for definition in PLAN_TOOL_DEFINITIONS
    }
    assert set(mcp_by_name) == set(CONTRACT_EXAMPLES)
    assert set(backend_by_name) == set(CONTRACT_EXAMPLES)

    for name, (request, response) in CONTRACT_EXAMPLES.items():
        validate(request, mcp_by_name[name].inputSchema)
        validate(response, mcp_by_name[name].outputSchema)
        validate(request, backend_by_name[name]["inputSchema"])
        validate(response, backend_by_name[name]["outputSchema"])


def test_all_schemas_are_draft_2020_12_valid_and_hide_task_context():
    for tool in TOOL_DEFINITIONS:
        Draft202012Validator.check_schema(tool.inputSchema)
        Draft202012Validator.check_schema(tool.outputSchema)
        assert "task_name" not in str(tool.inputSchema)
        assert "task_name" not in str(tool.outputSchema)
