import tempfile
import unittest
from pathlib import Path

from backend.agent.task.harness.adaptors.adaptor import (
    Adaptor,
    AdaptorRouteError,
)
from backend.agent.task.harness.adaptors.file_system import (
    FileSystemAdaptor,
    PathBuildError,
    PathOutOfSandboxError,
)
from backend.agent.tools.tool import Tool
from backend.agent.tools.core.model import ResolvedToolCall


def make_call(
    tool_name: str,
    arguments: dict,
    *,
    meta: dict | None = None,
) -> ResolvedToolCall:
    return ResolvedToolCall(
        tool=Tool(
            tool_name,
            f"{tool_name} tool",
            "http://localhost:6410/mcp",
            input_schema={"type": "object"},
            adaptor_names=("FileSystemAdaptor",),
        ),
        arguments=arguments,
        call_id="call_1",
        mcp_meta=meta or {},
    )


class FileSystemAdaptorTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name).resolve()
        self.current_directory = self.root / "workspace"
        self.current_directory.mkdir()
        self.adaptor = FileSystemAdaptor(str(self.current_directory))

    def test_is_an_adaptor_subclass(self):
        self.assertIsInstance(self.adaptor, Adaptor)

    def test_adapts_every_filesystem_tool_path_field(self):
        path_fields = {
            "list_directory": "path",
            "read_file": "path",
            "stat": "path",
            "glob": "root",
            "search_files": "root",
            "create_file": "url",
            "edit_file": "path",
            "bash": "PATH",
        }

        for tool_name, path_field in path_fields.items():
            with self.subTest(tool_name=tool_name):
                entity = make_call(
                    tool_name,
                    {path_field: "backend/tests", "preserved": True},
                )

                adapted = self.adaptor.adapt(entity)

                self.assertEqual(
                    adapted.arguments[path_field],
                    str(self.current_directory / "backend" / "tests"),
                )
                self.assertTrue(adapted.arguments["preserved"])
                self.assertIs(adapted.tool, entity.tool)
                self.assertEqual(adapted.call_id, entity.call_id)
                self.assertEqual(
                    entity.arguments[path_field],
                    "backend/tests",
                )

    def test_preserves_existing_mcp_metadata(self):
        entity = make_call(
            "read_file",
            {"path": "file.txt"},
            meta={"aurabot": {"task_name": "Task"}},
        )

        adapted = self.adaptor.adapt(entity)

        self.assertEqual(adapted.mcp_meta, entity.mcp_meta)

    def test_absolute_child_and_current_directory_are_allowed(self):
        child = self.current_directory / "backend"

        self.assertEqual(
            self.adaptor.build_path(str(child)),
            str(child),
        )
        self.assertEqual(
            self.adaptor.build_path(str(self.current_directory)),
            str(self.current_directory),
        )

    def test_absolute_and_relative_escapes_are_rejected(self):
        outside = self.root / "outside"

        for path in (str(outside), "../outside"):
            with self.subTest(path=path):
                with self.assertRaises(PathOutOfSandboxError) as raised:
                    self.adaptor.build_path(path)
                self.assertIsInstance(raised.exception, PathBuildError)
                self.assertEqual(
                    raised.exception.current_directory,
                    str(self.current_directory),
                )

    def test_symlink_escape_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.current_directory / "link"
        link.symlink_to(outside, target_is_directory=True)

        with self.assertRaises(PathOutOfSandboxError):
            self.adaptor.build_path("link/secret.txt")

    def test_missing_or_invalid_path_field_raises_path_build_error(self):
        invalid_calls = (
            make_call("read_file", {}),
            make_call("read_file", {"path": ""}),
            make_call("read_file", {"path": None}),
            make_call("read_file", {"path": 42}),
        )

        for entity in invalid_calls:
            with self.subTest(arguments=entity.arguments):
                with self.assertRaises(PathBuildError):
                    self.adaptor.adapt(entity)

    def test_create_file_accepts_local_file_urls_and_decodes_path(self):
        target = self.current_directory / "file with spaces.txt"

        urls = (
            target.as_uri(),
            target.as_uri().replace("file:///", "file://LOCALHOST/"),
        )
        for url in urls:
            with self.subTest(url=url):
                adapted = self.adaptor.adapt(
                    make_call("create_file", {"url": url})
                )
                self.assertEqual(adapted.arguments["url"], str(target))

    def test_create_file_rejects_nonlocal_or_decorated_urls(self):
        target_url = (self.current_directory / "file.txt").as_uri()
        invalid_urls = (
            "https://example.com/file.txt",
            "file://remote.example/file.txt",
            "file:relative.txt",
            f"{target_url}?version=1",
            f"{target_url}#fragment",
        )

        for url in invalid_urls:
            with self.subTest(url=url):
                with self.assertRaises(PathBuildError):
                    self.adaptor.adapt(
                        make_call("create_file", {"url": url})
                    )

    def test_unknown_tool_binding_is_a_route_error(self):
        with self.assertRaises(AdaptorRouteError):
            self.adaptor.adapt(make_call("unknown", {"path": "."}))

    def test_current_directory_must_be_absolute(self):
        adaptor = FileSystemAdaptor("relative/workspace")

        with self.assertRaises(PathBuildError):
            adaptor.build_path(".")

    def test_missing_current_directory_fails_when_adapting(self):
        adaptor = FileSystemAdaptor(None)

        with self.assertRaises(PathBuildError):
            adaptor.adapt(make_call("read_file", {"path": "."}))


if __name__ == "__main__":
    unittest.main()
