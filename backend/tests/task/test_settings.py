"""
验证 Task 配置到不可变运行参数的派生和 pinned Pi 校验。

测试使用临时目录构造真实 Node/Pi/Package 路径，确保 Python 只启动配置载体中的绝对
CLI，并把所有 Task 数据和 Pi Home 固定在 AuraBot user data 下。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.task.settings import EXPECTED_PI_VERSION, TaskSettings, TaskSettingsError


class TaskSettingsTests(unittest.TestCase):
    def make_runtime(self, root: Path, version: str = EXPECTED_PI_VERSION) -> tuple[Path, Path]:
        runtime = root / "pi-runtime"
        cli = runtime / "node_modules/@earendil-works/pi-coding-agent/dist/cli.js"
        plugin = runtime / "node_modules/@scpz24/pi-aurabot-task/aurabot-task/index.ts"
        prompt = runtime / "node_modules/@scpz24/pi-aurabot-task/prompts/task-system.md"
        cli.parent.mkdir(parents=True)
        plugin.parent.mkdir(parents=True)
        prompt.parent.mkdir(parents=True)
        cli.write_text("// fake cli\n", encoding="utf-8")
        plugin.write_text("// fake extension\n", encoding="utf-8")
        prompt.write_text("prompt\n", encoding="utf-8")
        node = root / "node"
        node.write_text(f"#!/bin/sh\nprintf '{version}\\n'\n", encoding="utf-8")
        node.chmod(0o755)
        return runtime, node

    def test_derives_isolated_paths_and_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime, node = self.make_runtime(root)
            settings = TaskSettings.from_mapping(
                {
                    "task": {
                        "user_data_dir": "./user-data",
                        "pi_runtime_dir": str(runtime),
                        "node_executable": str(node),
                        "default_timeout_seconds": 900,
                    }
                },
                config_root=root,
            )

            resolved_root = root.resolve()
            self.assertEqual(
                settings.database_path,
                resolved_root / "user-data/task/task-runtime.sqlite3",
            )
            self.assertEqual(settings.session_root, resolved_root / "user-data/task/sessions")
            self.assertEqual(settings.pi_home, resolved_root / "user-data/pi-agent")
            self.assertEqual(settings.default_timeout_seconds, 900)
            self.assertEqual(settings.startup_timeout_seconds, 15)
            self.assertEqual(settings.max_jsonl_line_bytes, 8 * 1024 * 1024)
            self.assertTrue(settings.pi_cli_path.is_absolute())
            self.assertTrue(settings.plugin_entry_path.is_absolute())
            self.assertTrue(settings.system_prompt_path.is_absolute())
            self.assertFalse(hasattr(settings, "concurrency_limit"))
            self.assertFalse(hasattr(settings, "waiting_process_grace_seconds"))
            self.assertFalse(hasattr(settings, "stderr_tail_bytes"))

    def test_validates_exact_pinned_cli_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime, node = self.make_runtime(root)
            settings = TaskSettings.from_mapping(
                {
                    "task": {
                        "user_data_dir": str(root / "data"),
                        "pi_runtime_dir": str(runtime),
                        "node_executable": str(node),
                    }
                },
                config_root=root,
            )

            settings.validate_runtime()

            wrong_runtime, wrong_node = self.make_runtime(root / "wrong", "0.85.0")
            wrong = TaskSettings.from_mapping(
                {
                    "task": {
                        "user_data_dir": str(root / "wrong-data"),
                        "pi_runtime_dir": str(wrong_runtime),
                        "node_executable": str(wrong_node),
                    }
                },
                config_root=root,
            )
            with self.assertRaisesRegex(TaskSettingsError, "0.84.2"):
                wrong.validate_runtime()

    def test_rejects_removed_concurrency_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(TaskSettingsError, "no longer configurable"):
                TaskSettings.from_mapping(
                    {
                        "task": {
                            "concurrency_limit": 2,
                        }
                    },
                    config_root=root,
                )

    def test_rejects_missing_runtime_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            node = root / "node"
            node.write_text("#!/bin/sh\nprintf '0.84.2\\n'\n", encoding="utf-8")
            node.chmod(0o755)
            settings = TaskSettings.from_mapping(
                {
                    "task": {
                        "user_data_dir": str(root / "data"),
                        "pi_runtime_dir": str(root / "missing"),
                        "node_executable": str(node),
                    }
                },
                config_root=root,
            )
            with self.assertRaisesRegex(TaskSettingsError, "Pi CLI"):
                settings.validate_runtime()

    def test_rejects_default_timeout_outside_public_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for timeout in (0, 86_401):
                with self.subTest(timeout=timeout):
                    with self.assertRaisesRegex(TaskSettingsError, "default_timeout_seconds"):
                        TaskSettings.from_mapping(
                            {"task": {"default_timeout_seconds": timeout}},
                            config_root=root,
                        )


if __name__ == "__main__":
    unittest.main()
