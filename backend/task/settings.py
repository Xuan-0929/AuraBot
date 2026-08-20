"""
把 Backend YAML 的新 task 段解析成不可变 Pi Runtime 设置。

本模块派生独立 Task SQLite、session root、Pi Home、项目 pinned CLI 与 AuraBot Pi
Package 路径，并在 Backend 启动前验证文件和精确版本；它从不发现全局 pi 命令。
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

EXPECTED_PI_VERSION = "0.84.2"


class TaskSettingsError(RuntimeError):
    """Raised when the Pi Runtime configuration cannot be used safely."""


def _resolve_path(value: str, root: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


@dataclass(frozen=True, slots=True)
class TaskSettings:
    """Fully derived process settings shared by Store, RPC and Supervisor."""

    user_data_dir: Path
    database_path: Path
    session_root: Path
    pi_home: Path
    pi_runtime_dir: Path
    node_executable: str
    pi_cli_path: Path
    plugin_entry_path: Path
    system_prompt_path: Path
    concurrency_limit: int
    default_timeout_seconds: int
    startup_timeout_seconds: int = 15
    rpc_timeout_seconds: int = 10
    abort_grace_seconds: int = 10
    terminate_grace_seconds: int = 5
    shutdown_timeout_seconds: int = 30
    waiting_process_grace_seconds: int = 60
    max_jsonl_line_bytes: int = 8 * 1024 * 1024
    protocol_queue_size: int = 512
    stderr_tail_bytes: int = 64 * 1024
    output_chunk_bytes: int = 16 * 1024

    @classmethod
    def from_mapping(
        cls,
        config: Mapping[str, Any],
        *,
        config_root: Path,
    ) -> "TaskSettings":
        """Parse the new task section without accepting legacy aliases."""

        raw = config.get("task")
        if not isinstance(raw, Mapping):
            raise TaskSettingsError("Configuration must contain a task mapping.")

        concurrency_limit = int(raw.get("concurrency_limit", 2))
        default_timeout_seconds = int(raw.get("default_timeout_seconds", 1800))
        if concurrency_limit < 1:
            raise TaskSettingsError("task.concurrency_limit must be at least 1.")
        if default_timeout_seconds < 1:
            raise TaskSettingsError("task.default_timeout_seconds must be positive.")

        user_data = _resolve_path(
            str(raw.get("user_data_dir", "./.aurabot-data")), config_root
        )
        runtime = _resolve_path(
            str(raw.get("pi_runtime_dir", "./pi-runtime")), config_root
        )
        node_value = str(raw.get("node_executable", "node"))
        node_path = Path(node_value).expanduser()
        node_executable = (
            str(_resolve_path(node_value, config_root))
            if node_path.is_absolute() or node_path.parent != Path(".")
            else node_value
        )
        installed_package = runtime / "node_modules/@scpz24/pi-aurabot-task"
        task_root = user_data / "task"

        return cls(
            user_data_dir=user_data,
            database_path=task_root / "task-runtime.sqlite3",
            session_root=task_root / "sessions",
            pi_home=user_data / "pi-agent",
            pi_runtime_dir=runtime,
            node_executable=node_executable,
            pi_cli_path=(
                runtime
                / "node_modules/@earendil-works/pi-coding-agent/dist/cli.js"
            ).resolve(),
            plugin_entry_path=(installed_package / "aurabot-task/index.ts").resolve(),
            system_prompt_path=(installed_package / "prompts/task-system.md").resolve(),
            concurrency_limit=concurrency_limit,
            default_timeout_seconds=default_timeout_seconds,
        )

    @classmethod
    def from_config(cls, config: Mapping[str, Any], config_path: Path) -> "TaskSettings":
        """Build settings relative to the YAML file directory."""

        return cls.from_mapping(config, config_root=config_path.resolve().parent)

    def validate_runtime(self) -> None:
        """Validate pinned files and require the CLI to report exactly 0.84.2."""

        node = (
            shutil.which(self.node_executable)
            if Path(self.node_executable).parent == Path(".")
            else self.node_executable
        )
        if not node or not Path(node).is_file():
            raise TaskSettingsError(f"Node executable not found: {self.node_executable}")
        for label, path in (
            ("Pi CLI", self.pi_cli_path),
            ("AuraBot Pi extension", self.plugin_entry_path),
            ("AuraBot Task system prompt", self.system_prompt_path),
        ):
            if not path.is_file():
                raise TaskSettingsError(f"{label} not found: {path}")

        try:
            completed = subprocess.run(
                [str(node), str(self.pi_cli_path), "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.startup_timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise TaskSettingsError(f"Failed to execute pinned Pi CLI: {exc}") from exc
        reported = completed.stdout.strip()
        if completed.returncode != 0 or reported != EXPECTED_PI_VERSION:
            raise TaskSettingsError(
                f"Pinned Pi CLI must report {EXPECTED_PI_VERSION}; got {reported or 'no version'}."
            )

    def session_directory(self, task_id: UUID) -> Path:
        """Return the canonical direct child session directory for a UUID Task."""

        canonical_id = UUID(str(task_id))
        return self.session_root / str(canonical_id)


__all__ = ["EXPECTED_PI_VERSION", "TaskSettings", "TaskSettingsError"]
