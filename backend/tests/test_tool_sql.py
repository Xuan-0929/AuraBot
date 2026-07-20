import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from unittest.mock import patch

from backend.agent.integration.sql import utils
from backend.agent.integration.sql.MCPServerSQL import MCPServerSQL
from backend.agent.integration.sql.ToolGroupSQL import ToolGroupSQL
from backend.agent.integration.sql.ToolSQL import ToolSQL
from backend.agent.tools.Tool import Tool
from backend.agent.tools.ToolGroup import ToolGroup


@contextmanager
def temporary_agent_database():
    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "agent_runtime_sql.sqlite"
        with patch.object(utils, "SQLITE_PATH", database_path):
            utils.initialize_schema()
            MCPServerSQL.insert_mcp_server_metadata(
                "local",
                "localhost:6410",
                "Local MCP server",
            )
            MCPServerSQL.insert_mcp_server_metadata(
                "remote",
                "https://mcp.example.com",
                "Remote MCP server",
            )
            yield database_path


class ToolGroupSQLTests(unittest.TestCase):
    def test_tool_group_crud_returns_runtime_objects(self):
        with temporary_agent_database():
            self.assertIsNone(
                ToolGroupSQL.insert_tool_group_metadata(
                    "missing-server",
                    "https://missing.example.com",
                    "Unavailable server",
                )
            )

            group_id = ToolGroupSQL.insert_tool_group_metadata(
                "filesystem",
                "localhost:6410",
                "Filesystem tools",
            )
            self.assertIsInstance(group_id, int)
            self.assertIsNone(
                ToolGroupSQL.insert_tool_group_metadata(
                    "filesystem",
                    "https://mcp.example.com",
                    "Duplicate global name",
                )
            )
            self.assertIsNone(
                ToolGroupSQL.insert_tool_group_metadata(
                    "empty-description",
                    "localhost:6410",
                    "",
                )
            )

            group = ToolGroupSQL.get_tool_group("filesystem")
            self.assertIsInstance(group, ToolGroup)
            self.assertEqual(group.name, "filesystem")
            self.assertEqual(group.description, "Filesystem tools")
            self.assertEqual(group.tools, [])

            self.assertTrue(
                ToolGroupSQL.update_tool_group_metadata(
                    "filesystem",
                    new_name="files",
                    description="File tools",
                )
            )
            self.assertFalse(
                ToolGroupSQL.update_tool_group_metadata(
                    "files",
                    description="",
                )
            )
            self.assertIsNone(ToolGroupSQL.get_tool_group("filesystem"))
            self.assertEqual(
                ToolGroupSQL.get_tool_group_by_id(
                    group_id,
                    include_tools=False,
                ).name,
                "files",
            )

            groups, ids = ToolGroupSQL.list_tool_groups(include_tools=False)
            self.assertEqual([item.name for item in groups], ["files"])
            self.assertEqual(ids, [group_id])

            local_groups, local_ids = ToolGroupSQL.list_tool_groups_by_server(
                "localhost:6410",
                include_tools=False,
            )
            self.assertEqual([item.name for item in local_groups], ["files"])
            self.assertEqual(local_ids, [group_id])
            self.assertEqual(
                ToolGroupSQL.list_tool_groups_by_server(
                    "https://missing.example.com",
                    include_tools=False,
                ),
                ([], []),
            )

            self.assertTrue(ToolGroupSQL.delete_tool_group_metadata("files"))
            self.assertFalse(ToolGroupSQL.delete_tool_group_metadata("files"))

    def test_native_tool_group_cannot_be_deleted(self):
        with temporary_agent_database():
            ToolGroupSQL.insert_tool_group_metadata(
                "native",
                "localhost:6410",
                "Native tools",
                is_native=True,
            )

            with self.assertWarns(RuntimeWarning):
                self.assertFalse(
                    ToolGroupSQL.delete_tool_group_metadata("native")
                )
            self.assertIsNotNone(
                ToolGroupSQL.get_tool_group("native", include_tools=False)
            )


class ToolSQLTests(unittest.TestCase):
    def test_tool_crud_enforces_server_group_ownership(self):
        with temporary_agent_database() as database_path:
            alpha_id = ToolGroupSQL.insert_tool_group_metadata(
                "alpha",
                "localhost:6410",
                "Alpha tools",
            )
            beta_id = ToolGroupSQL.insert_tool_group_metadata(
                "beta",
                "localhost:6410",
                "Beta tools",
            )
            ToolGroupSQL.insert_tool_group_metadata(
                "remote",
                "https://mcp.example.com",
                "Remote tools",
            )
            self.assertIsInstance(alpha_id, int)
            self.assertIsInstance(beta_id, int)

            input_schema = {
                "type": "object",
                "properties": {"query": {"type": "string"}},
            }
            output_schema = {
                "type": "object",
                "properties": {"result": {"type": "string"}},
            }

            local_tool_id = ToolSQL.insert_tool_metadata(
                "search",
                "Search locally",
                input_schema,
                output_schema,
                "alpha",
                "localhost:6410",
            )
            remote_tool_id = ToolSQL.insert_tool_metadata(
                "search",
                "Search remotely",
                input_schema,
                None,
                "remote",
                "https://mcp.example.com",
            )
            self.assertIsInstance(local_tool_id, int)
            self.assertIsInstance(remote_tool_id, int)
            self.assertNotEqual(local_tool_id, remote_tool_id)

            self.assertIsNone(
                ToolSQL.insert_tool_metadata(
                    "search",
                    "Duplicate on one server",
                    input_schema,
                    None,
                    "beta",
                    "localhost:6410",
                )
            )
            self.assertIsNone(
                ToolSQL.insert_tool_metadata(
                    "wrong-server",
                    "Wrong ownership",
                    input_schema,
                    None,
                    "remote",
                    "localhost:6410",
                )
            )
            self.assertIsNone(
                ToolSQL.insert_tool_metadata(
                    "invalid-schema",
                    "Invalid schema",
                    [],
                    None,
                    "alpha",
                    "localhost:6410",
                )
            )

            local_tool = ToolSQL.get_tool("localhost:6410", "search")
            remote_tool = ToolSQL.get_tool_by_id(remote_tool_id)
            self.assertIsInstance(local_tool, Tool)
            self.assertEqual(local_tool.name, "search")
            self.assertEqual(local_tool.description, "Search locally")
            self.assertEqual(local_tool.server_url, "localhost:6410")
            self.assertEqual(local_tool.input_schema, input_schema)
            self.assertEqual(local_tool.output_schema, output_schema)
            self.assertEqual(remote_tool.server_url, "https://mcp.example.com")
            self.assertEqual(remote_tool.input_schema, input_schema)
            self.assertIsNone(remote_tool.output_schema)

            alpha_tools, alpha_tool_ids = ToolSQL.list_tools("alpha")
            self.assertEqual([tool.name for tool in alpha_tools], ["search"])
            self.assertEqual(alpha_tool_ids, [local_tool_id])
            self.assertEqual(alpha_tools[0].input_schema, input_schema)
            self.assertEqual(alpha_tools[0].output_schema, output_schema)

            named_tools, named_tool_ids = ToolSQL.list_tools_by_name("search")
            self.assertEqual(
                [tool.server_url for tool in named_tools],
                ["localhost:6410", "https://mcp.example.com"],
            )
            self.assertEqual(named_tool_ids, [local_tool_id, remote_tool_id])
            self.assertEqual(
                [tool.input_schema for tool in named_tools],
                [input_schema, input_schema],
            )
            self.assertEqual(
                [tool.output_schema for tool in named_tools],
                [output_schema, None],
            )

            local_tools, local_tool_ids = ToolSQL.list_tools_by_server(
                "localhost:6410"
            )
            self.assertEqual([tool.name for tool in local_tools], ["search"])
            self.assertEqual(local_tool_ids, [local_tool_id])
            self.assertEqual(local_tools[0].input_schema, input_schema)
            self.assertEqual(local_tools[0].output_schema, output_schema)

            with patch.object(
                utils,
                "create_connection",
                wraps=utils.create_connection,
            ) as create_connection:
                alpha = ToolGroupSQL.get_tool_group("alpha")

            self.assertEqual(create_connection.call_count, 1)
            self.assertEqual([tool.name for tool in alpha.tools], ["search"])
            self.assertEqual(alpha.tools[0].input_schema, input_schema)
            self.assertEqual(alpha.tools[0].output_schema, output_schema)

            alpha_by_id = ToolGroupSQL.get_tool_group_by_id(alpha_id)
            self.assertEqual(alpha_by_id.tools[0].input_schema, input_schema)
            self.assertEqual(alpha_by_id.tools[0].output_schema, output_schema)

            local_groups, _ = ToolGroupSQL.list_tool_groups_by_server(
                "localhost:6410"
            )
            self.assertEqual(
                [group.name for group in local_groups],
                ["alpha", "beta"],
            )
            self.assertEqual(
                local_groups[0].tools[0].input_schema,
                input_schema,
            )
            self.assertEqual(
                local_groups[0].tools[0].output_schema,
                output_schema,
            )

            with patch.object(
                utils,
                "create_connection",
                wraps=utils.create_connection,
            ) as create_connection:
                groups, _ = ToolGroupSQL.list_tool_groups()

            self.assertEqual(create_connection.call_count, 1)
            self.assertEqual(
                [group.name for group in groups],
                ["alpha", "beta", "remote"],
            )
            self.assertEqual(
                [[tool.name for tool in group.tools] for group in groups],
                [["search"], [], ["search"]],
            )
            self.assertEqual(groups[0].tools[0].input_schema, input_schema)
            self.assertEqual(groups[0].tools[0].output_schema, output_schema)
            self.assertEqual(groups[2].tools[0].input_schema, input_schema)
            self.assertIsNone(groups[2].tools[0].output_schema)
            self.assertFalse(
                ToolGroupSQL.delete_tool_group_metadata("alpha")
            )

            self.assertTrue(
                ToolSQL.update_tool_metadata(
                    "localhost:6410",
                    "search",
                    description="Updated search",
                )
            )
            with closing(sqlite3.connect(database_path)) as connection:
                preserved_output = connection.execute(
                    "SELECT output_schema_json FROM tool WHERE id = ?",
                    (local_tool_id,),
                ).fetchone()[0]
            self.assertEqual(json.loads(preserved_output), output_schema)

            updated_input_schema = {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            }
            self.assertTrue(
                ToolSQL.update_tool_metadata(
                    "localhost:6410",
                    "search",
                    new_name="lookup",
                    input_schema=updated_input_schema,
                    output_schema=None,
                    new_group_name="beta",
                )
            )
            self.assertFalse(
                ToolSQL.update_tool_metadata(
                    "localhost:6410",
                    "lookup",
                    new_group_name="remote",
                )
            )

            with closing(sqlite3.connect(database_path)) as connection:
                row = connection.execute(
                    """
                    SELECT
                        tool.input_schema_json,
                        tool.output_schema_json,
                        tool.group_id
                    FROM tool
                    WHERE tool.id = ?
                    """,
                    (local_tool_id,),
                ).fetchone()
            self.assertEqual(json.loads(row[0]), updated_input_schema)
            self.assertIsNone(row[1])
            self.assertEqual(row[2], beta_id)

            self.assertIsNone(ToolSQL.get_tool("localhost:6410", "search"))
            updated_tool = ToolSQL.get_tool("localhost:6410", "lookup")
            self.assertEqual(updated_tool.description, "Updated search")
            self.assertEqual(updated_tool.input_schema, updated_input_schema)
            self.assertIsNone(updated_tool.output_schema)
            self.assertTrue(ToolGroupSQL.delete_tool_group_metadata("alpha"))

            self.assertTrue(
                ToolSQL.delete_tool_metadata("localhost:6410", "lookup")
            )
            self.assertFalse(
                ToolSQL.delete_tool_metadata("localhost:6410", "lookup")
            )
            self.assertTrue(ToolGroupSQL.delete_tool_group_metadata("beta"))


if __name__ == "__main__":
    unittest.main()
