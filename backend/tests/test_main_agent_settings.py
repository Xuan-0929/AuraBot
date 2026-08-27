"""验证 Main Agent 配置与共享工作区注册表的安全边界。

测试从完整 Backend 配置构造 typed MainAgentSettings 和 WorkspaceRegistry，覆盖默认目录、
未知 workspace、路径伪装和 symlink 根目录拒绝；TaskManager 与后续 Main Agent runtime 都通过
同一注册表取得受信任的工作目录，而不接收客户端路径。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.main_agent.settings import (
    MainAgentSettings,
    WorkspaceRegistry,
    WorkspaceRegistryError,
)


class MainAgentSettingsTests(unittest.TestCase):
    def test_parses_workspace_and_profile_defaults_from_complete_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "project"
            workspace.mkdir()

            settings = MainAgentSettings.from_config(
                {
                    "app": {
                        "data_dir": "./data",
                        "workspaces": [{"id": "project", "path": "./project"}],
                    },
                    "main_agent": {},
                    "memory": {},
                    "task": {},
                    "dev": {},
                },
                config_path=root / "config.yaml",
            )

        self.assertEqual(settings.data_dir, (root / "data").resolve())
        self.assertEqual(settings.profile_root, (root / "data/profiles").resolve())
        self.assertEqual(settings.workspaces.require("project"), workspace.resolve())

    def test_registry_rejects_unknown_and_path_like_workspace_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "project"
            workspace.mkdir()
            registry = WorkspaceRegistry({"project": workspace.resolve()})

            for workspace_id in (None, "missing", "../project", "/tmp/project"):
                with self.subTest(workspace_id=workspace_id):
                    with self.assertRaisesRegex(WorkspaceRegistryError, "workspace"):
                        registry.require(workspace_id)

    def test_registry_rejects_symlinked_workspace_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            link = root / "project"
            link.symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(WorkspaceRegistryError, "symlink"):
                WorkspaceRegistry({"project": link})

    def test_registry_and_config_reject_symlinked_workspace_ancestor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside_project = root / "outside/project"
            outside_project.mkdir(parents=True)
            link = root / "link"
            link.symlink_to(root / "outside", target_is_directory=True)

            with self.assertRaisesRegex(WorkspaceRegistryError, "symlink"):
                WorkspaceRegistry({"project": root.resolve() / "link/project"})
            with self.assertRaisesRegex(WorkspaceRegistryError, "symlink"):
                WorkspaceRegistry.from_config(
                    {"app": {"workspaces": [{"id": "project", "path": "./link/project"}]}},
                    config_root=root.resolve(),
                )

    def test_settings_reject_non_string_or_empty_data_and_profile_paths(self):
        invalid_values = (None, "", [], {})
        for section, key in (("app", "data_dir"), ("main_agent", "profile_dir")):
            for value in invalid_values:
                with self.subTest(section=section, value=value):
                    config = {"app": {}, "main_agent": {}}
                    config[section][key] = value
                    with self.assertRaisesRegex(ValueError, key):
                        MainAgentSettings.from_config(config, Path("/tmp/config.yaml"))


if __name__ == "__main__":
    unittest.main()
