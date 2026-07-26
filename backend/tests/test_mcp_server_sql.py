import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from backend.agent.integration.sql import utils
from backend.agent.integration.sql.mcp_server import MCPServerSQL
from backend.agent.integration.sql.tool_group import ToolGroupSQL


@contextmanager
def temporary_agent_database():
    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "agent_runtime_sql.sqlite"
        with patch.object(utils, "SQLITE_PATH", database_path):
            utils.initialize_schema()
            yield database_path


class MCPServerSQLTests(unittest.TestCase):
    def test_mcp_server_crud_returns_metadata_without_database_id(self):
        with temporary_agent_database():
            local_id = MCPServerSQL.insert_mcp_server_metadata(
                "local",
                "localhost:6410",
                "Local MCP server",
            )
            remote_id = MCPServerSQL.insert_mcp_server_metadata(
                "remote",
                "https://mcp.example.com/v1",
                "Remote MCP server",
            )
            self.assertIsInstance(local_id, int)
            self.assertIsInstance(remote_id, int)

            self.assertIsNone(
                MCPServerSQL.insert_mcp_server_metadata(
                    "local",
                    "https://duplicate-name.example.com",
                )
            )
            self.assertIsNone(
                MCPServerSQL.insert_mcp_server_metadata(
                    "duplicate-url",
                    "localhost:6410",
                )
            )
            self.assertIsNone(
                MCPServerSQL.insert_mcp_server_metadata(
                    "credentials",
                    "https://user:password@mcp.example.com",
                )
            )
            self.assertIsNone(
                MCPServerSQL.insert_mcp_server_metadata(
                    "query-token",
                    "https://mcp.example.com?token=secret",
                )
            )
            self.assertIsNone(
                MCPServerSQL.insert_mcp_server_metadata(
                    "query-api-key",
                    "https://mcp.example.com?api-key=secret",
                )
            )
            self.assertIsNone(
                MCPServerSQL.insert_mcp_server_metadata(
                    "invalid-port",
                    "https://mcp.example.com:not-a-port",
                )
            )
            self.assertIsNone(
                MCPServerSQL.insert_mcp_server_metadata(
                    "fragment",
                    "https://mcp.example.com#mcp-token",
                )
            )
            self.assertIsNone(
                MCPServerSQL.insert_mcp_server_metadata(
                    "",
                    "https://empty-name.example.com",
                )
            )

            self.assertEqual(
                MCPServerSQL.get_mcp_server("localhost:6410"),
                {
                    "name": "local",
                    "url": "localhost:6410",
                    "description": "Local MCP server",
                },
            )
            self.assertEqual(
                MCPServerSQL.get_mcp_server_by_id(remote_id)["name"],
                "remote",
            )
            self.assertEqual(
                MCPServerSQL.get_mcp_server_by_name("remote")["url"],
                "https://mcp.example.com/v1",
            )

            self.assertTrue(
                MCPServerSQL.update_mcp_server_metadata(
                    "https://mcp.example.com/v1",
                    new_name="external",
                    new_url="https://mcp.example.com/mcp",
                    description="Updated MCP server",
                )
            )
            self.assertIsNone(
                MCPServerSQL.get_mcp_server("https://mcp.example.com/v1")
            )
            self.assertEqual(
                MCPServerSQL.get_mcp_server_by_id(remote_id),
                {
                    "name": "external",
                    "url": "https://mcp.example.com/mcp",
                    "description": "Updated MCP server",
                },
            )
            self.assertTrue(
                MCPServerSQL.update_mcp_server_metadata(
                    "https://mcp.example.com/mcp"
                )
            )
            self.assertFalse(
                MCPServerSQL.update_mcp_server_metadata(
                    "https://missing.example.com",
                    new_name="missing",
                )
            )
            self.assertFalse(
                MCPServerSQL.update_mcp_server_metadata(
                    "https://mcp.example.com/mcp",
                    new_name="local",
                )
            )
            self.assertFalse(
                MCPServerSQL.update_mcp_server_metadata(
                    "https://mcp.example.com/mcp",
                    new_url="https://mcp.example.com?api_key=secret",
                )
            )

            servers, ids = MCPServerSQL.list_mcp_servers()
            self.assertEqual(
                [server["name"] for server in servers],
                ["local", "external"],
            )
            self.assertEqual(ids, [local_id, remote_id])

            self.assertTrue(
                MCPServerSQL.delete_mcp_server_metadata(
                    "https://mcp.example.com/mcp"
                )
            )
            self.assertFalse(
                MCPServerSQL.delete_mcp_server_metadata(
                    "https://mcp.example.com/mcp"
                )
            )

    def test_server_with_tool_group_cannot_be_deleted(self):
        with temporary_agent_database():
            MCPServerSQL.insert_mcp_server_metadata(
                "local",
                "localhost:6410",
                "Local MCP server",
            )
            ToolGroupSQL.insert_tool_group_metadata(
                "filesystem",
                "localhost:6410",
                "Filesystem tools",
            )

            self.assertFalse(
                MCPServerSQL.delete_mcp_server_metadata("localhost:6410")
            )
            self.assertIsNotNone(
                MCPServerSQL.get_mcp_server("localhost:6410")
            )

            self.assertTrue(
                ToolGroupSQL.delete_tool_group_metadata("filesystem")
            )
            self.assertTrue(
                MCPServerSQL.delete_mcp_server_metadata("localhost:6410")
            )


if __name__ == "__main__":
    unittest.main()
