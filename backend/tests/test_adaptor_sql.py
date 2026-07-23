import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.agent.integration.sql import utils
from backend.agent.integration.sql.AdaptorSQL import AdaptorSQL
from backend.agent.integration.sql.MCPServerSQL import MCPServerSQL
from backend.agent.integration.sql.ToolGroupSQL import ToolGroupSQL
from backend.agent.integration.sql.ToolSQL import ToolSQL


class AdaptorSQLTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = (
            Path(self.temporary_directory.name) / "agent.sqlite"
        )
        self.sqlite_path_patch = patch.object(
            utils,
            "SQLITE_PATH",
            self.database_path,
        )
        self.sqlite_path_patch.start()
        self.addCleanup(self.sqlite_path_patch.stop)
        utils.initialize_schema()

    def test_crud_returns_metadata_without_database_id(self):
        filesystem_id = AdaptorSQL.insert_adaptor_metadata(
            "FileSystemAdaptor",
            "Normalize Task filesystem paths",
        )
        web_id = AdaptorSQL.insert_adaptor_metadata("WebAdaptor")

        self.assertIsInstance(filesystem_id, int)
        self.assertIsInstance(web_id, int)
        self.assertIsNone(
            AdaptorSQL.insert_adaptor_metadata("FileSystemAdaptor")
        )
        for invalid_name in ("", " leading", "trailing "):
            with self.subTest(invalid_name=invalid_name):
                self.assertIsNone(
                    AdaptorSQL.insert_adaptor_metadata(invalid_name)
                )

        self.assertEqual(
            AdaptorSQL.get_adaptor("FileSystemAdaptor"),
            {
                "name": "FileSystemAdaptor",
                "description": "Normalize Task filesystem paths",
            },
        )
        self.assertEqual(
            AdaptorSQL.get_adaptor_by_id(web_id),
            {"name": "WebAdaptor", "description": None},
        )

        self.assertTrue(
            AdaptorSQL.update_adaptor_metadata(
                "WebAdaptor",
                new_name="BrowserAdaptor",
                description="Normalize browser calls",
            )
        )
        self.assertFalse(
            AdaptorSQL.update_adaptor_metadata(
                "BrowserAdaptor",
                new_name="FileSystemAdaptor",
            )
        )
        self.assertFalse(
            AdaptorSQL.update_adaptor_metadata(
                "BrowserAdaptor",
                new_name=" invalid",
            )
        )

        adaptors, ids = AdaptorSQL.list_adaptors()
        self.assertEqual(
            [adaptor["name"] for adaptor in adaptors],
            ["FileSystemAdaptor", "BrowserAdaptor"],
        )
        self.assertEqual(ids, [filesystem_id, web_id])

        self.assertTrue(
            AdaptorSQL.delete_adaptor_metadata("BrowserAdaptor")
        )
        self.assertFalse(
            AdaptorSQL.delete_adaptor_metadata("BrowserAdaptor")
        )

    def test_referenced_adaptor_cannot_be_deleted(self):
        MCPServerSQL.insert_mcp_server_metadata(
            "local",
            "localhost:6410",
        )
        ToolGroupSQL.insert_tool_group_metadata(
            "filesystem",
            "localhost:6410",
            "Filesystem tools",
        )
        AdaptorSQL.insert_adaptor_metadata("FileSystemAdaptor")
        ToolSQL.insert_tool_metadata(
            "read_file",
            "Read a file",
            {"type": "object"},
            None,
            "filesystem",
            "localhost:6410",
            adaptor_name="FileSystemAdaptor",
        )

        self.assertFalse(
            AdaptorSQL.delete_adaptor_metadata("FileSystemAdaptor")
        )


if __name__ == "__main__":
    unittest.main()
