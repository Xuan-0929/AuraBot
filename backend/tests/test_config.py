"""
验证 Backend YAML 配置的加载、路径解析与环境变量展开。

测试直接使用 backend.config 的公开 API 和临时文件，覆盖 health 与新 Pi Task Runtime
所需配置；它不要求已删除的 Task model、MCP 或 runtime 字段。
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.config import (
    CONFIG_PATH_ENV,
    Config,
    ConfigError,
    ConfigNotInitializedError,
    PROJECT_ROOT,
    resolve_config_path,
)


class ConfigTests(unittest.TestCase):
    def test_loads_only_retained_top_level_sections(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            config_path.write_text(
                """
ports:
  frontend: 6400
  backend_gateway: 6401
features:
  enabled: true
  names:
    - memory
    - tasks
optional_value: null
task:
  user_data_dir: "./data"
""".strip(),
                encoding="utf-8",
            )

            runtime_config = Config().initialize(config_path)

        self.assertEqual(runtime_config["ports"]["backend_gateway"], 6401)
        self.assertEqual(runtime_config.ports["frontend"], 6400)
        self.assertNotIn("features", runtime_config)
        self.assertNotIn("optional_value", runtime_config)
        self.assertEqual(runtime_config.task["user_data_dir"], "./data")
        self.assertEqual(runtime_config.path, config_path.resolve())

    def test_retains_main_agent_workspace_memory_and_development_sections(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            config_path.write_text(
                """
app:
  data_dir: ./data
main_agent:
  profile_dir: profiles
memory:
  enabled: true
task:
  default_timeout_seconds: 60
dev:
  test_mode: true
ignored:
  value: false
""".strip(),
                encoding="utf-8",
            )
            runtime_config = Config().initialize(config_path)

        self.assertEqual(
            set(runtime_config), {"app", "main_agent", "memory", "task", "dev"}
        )

    def test_initialization_is_idempotent_and_keeps_in_memory_values(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            config_path.write_text("ports:\n  backend_gateway: 6401\n", encoding="utf-8")
            runtime_config = Config()

            first = runtime_config.initialize(config_path)
            config_path.write_text("ports:\n  backend_gateway: 7401\n", encoding="utf-8")
            second = runtime_config.initialize(config_path)

        self.assertIs(first, second)
        self.assertEqual(runtime_config["ports"]["backend_gateway"], 6401)

    def test_rejects_reinitialization_from_another_file(self):
        with tempfile.TemporaryDirectory() as directory:
            first_path = Path(directory) / "first.yaml"
            second_path = Path(directory) / "second.yaml"
            first_path.write_text("value: first\n", encoding="utf-8")
            second_path.write_text("value: second\n", encoding="utf-8")
            runtime_config = Config().initialize(first_path)

            with self.assertRaisesRegex(ConfigError, "already initialized"):
                runtime_config.initialize(second_path)

    def test_reports_missing_and_invalid_configuration(self):
        runtime_config = Config()
        with self.assertRaises(ConfigNotInitializedError):
            _ = runtime_config["missing"]

        with tempfile.TemporaryDirectory() as directory:
            missing_path = Path(directory) / "missing.yaml"
            with self.assertRaisesRegex(ConfigError, "config file not found"):
                runtime_config.initialize(missing_path)

            invalid_path = Path(directory) / "invalid.yaml"
            invalid_path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "root must be a mapping"):
                Config().initialize(invalid_path)

    def test_environment_path_is_resolved_against_project_root(self):
        with patch.dict(os.environ, {CONFIG_PATH_ENV: "custom/config.yaml"}):
            self.assertEqual(
                resolve_config_path(),
                (PROJECT_ROOT / "custom/config.yaml").resolve(),
            )

    def test_expands_environment_references_recursively(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            config_path.write_text(
                """
chat_model:
  api_key: "${AURABOT_TEST_API_KEY}"
  base_url: "https://${AURABOT_TEST_API_HOST:-example.invalid}/v1"
gateway_model:
  api_key: "Bearer ${AURABOT_TEST_API_KEY}"
""".strip(),
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {"AURABOT_TEST_API_KEY": "secret:#value"},
                clear=False,
            ):
                os.environ.pop("AURABOT_TEST_API_HOST", None)
                runtime_config = Config().initialize(config_path)

        self.assertEqual(runtime_config.chat_model["api_key"], "secret:#value")
        self.assertEqual(
            runtime_config.chat_model["base_url"],
            "https://example.invalid/v1",
        )
        self.assertEqual(
            runtime_config.gateway_model["api_key"],
            "Bearer secret:#value",
        )

    def test_missing_required_environment_variable_prevents_initialization(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            config_path.write_text(
                'chat_model:\n  api_key: "${AURABOT_REQUIRED_TEST_API_KEY}"\n',
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("AURABOT_REQUIRED_TEST_API_KEY", None)
                with self.assertRaisesRegex(
                    ConfigError,
                    "AURABOT_REQUIRED_TEST_API_KEY",
                ):
                    runtime_config = Config()
                    runtime_config.initialize(config_path)

        self.assertFalse(runtime_config.initialized)

    def test_ignores_unresolved_environment_references_in_legacy_sections(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            config_path.write_text(
                """
ports:
  backend_gateway: 6401
task_model:
  api_key: "${AURABOT_TASK_MODEL_API_KEY}"
local_mcp_server:
  url: "${AURABOT_REMOVED_MCP_URL}"
task_runtime:
  retention_seconds: 600
""".strip(),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("AURABOT_TASK_MODEL_API_KEY", None)
                os.environ.pop("AURABOT_REMOVED_MCP_URL", None)
                runtime_config = Config().initialize(config_path)

        self.assertEqual(runtime_config.as_dict(), {"ports": {"backend_gateway": 6401}})

    def test_project_example_reads_retained_model_api_keys_from_environment(self):
        environment = {
            "AURABOT_CHAT_MODEL_API_KEY": "chat-secret",
            "AURABOT_GATEWAY_MODEL_API_KEY": "gateway-secret",
        }

        with patch.dict(os.environ, environment, clear=False):
            runtime_config = Config().initialize(PROJECT_ROOT / "config.example.yaml")

        self.assertEqual(runtime_config.chat_model["api_key"], "chat-secret")
        self.assertEqual(runtime_config.gateway_model["api_key"], "gateway-secret")
        self.assertNotIn("task_model", runtime_config)
        self.assertEqual(runtime_config.task["default_timeout_seconds"], 1800)


if __name__ == "__main__":
    unittest.main()
