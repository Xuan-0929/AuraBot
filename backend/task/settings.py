"""Resolve immutable Task settings from the Backend section of Electron JSON configuration.

Settings derive durable SQLite/session/Pi-home paths and trusted workspaces, validate pinned Pi installation
on demand, and forward configured model identity to the subprocess adapter. Missing Pi does not prevent
Backend startup; TaskManager exposes a disabled capability until the next configured restart."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from ..main_agent.settings import WorkspaceRegistry

EXPECTED_PI_VERSION = "0.84.2"


class TaskSettingsError(RuntimeError):
    """Raised when the Pi Runtime configuration cannot be used safely."""


def _resolve_path(value: str, root: Path) -> Path:
    """把配置中的路径解析为规范化绝对路径。

    用户目录写法会先展开；相对路径以配置文件所在目录 ``root`` 为基准，而不是以
    Backend 进程的当前工作目录为基准。返回值经过 ``resolve()``，供后续运行时路径
    派生和安装完整性检查使用。本函数只计算路径，不要求目标已经存在。
    """

    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


@dataclass(frozen=True, slots=True)
class TaskSettings:
    """Store、Manager 与 process 共享的完整运行参数。"""

    database_path: Path
    session_root: Path
    pi_home: Path
    node_executable: str
    pi_cli_path: Path
    plugin_entry_path: Path
    system_prompt_path: Path
    default_timeout_seconds: int
    startup_timeout_seconds: int = 15
    rpc_timeout_seconds: int = 10
    abort_grace_seconds: int = 10
    terminate_grace_seconds: int = 5
    shutdown_timeout_seconds: int = 30
    max_jsonl_line_bytes: int = 8 * 1024 * 1024
    protocol_queue_size: int = 512
    output_chunk_bytes: int = 16 * 1024
    workspace_registry: WorkspaceRegistry = field(default_factory=WorkspaceRegistry.empty)
    model_name: str = ""

    @classmethod
    def from_mapping(
        cls,
        config: Mapping[str, Any],
        *,
        config_root: Path,
    ) -> "TaskSettings":
        """解析 task 配置；安装文件验证留给 ``validate_runtime()``。"""

        raw = config.get("task", {})
        if not isinstance(raw, Mapping):
            raise TaskSettingsError("Configuration must contain a task mapping.")
        if "concurrency_limit" in raw:
            raise TaskSettingsError(
                "task.concurrency_limit is no longer configurable; remove it."
            )

        raw_default_timeout = raw.get("default_timeout_seconds", 1800)
        if isinstance(raw_default_timeout, bool):
            raise TaskSettingsError(
                "task.default_timeout_seconds must be between 1 and 86400."
            )
        default_timeout_seconds = int(raw_default_timeout)
        if not 1 <= default_timeout_seconds <= 86_400:
            raise TaskSettingsError(
                "task.default_timeout_seconds must be between 1 and 86400."
            )

        user_data = _resolve_path(
            str(raw.get("user_data_dir", config.get("app", {}).get("data_dir", "./.aurabot-data"))), config_root
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
            model_name=str(raw.get("model", {}).get("model_name", "")),
            database_path=task_root / "task-runtime.sqlite3",
            session_root=task_root / "sessions",
            pi_home=user_data / "pi-agent",
            node_executable=node_executable,
            pi_cli_path=(
                runtime
                / "node_modules/@earendil-works/pi-coding-agent/dist/cli.js"
            ).resolve(),
            plugin_entry_path=(installed_package / "aurabot-task/index.ts").resolve(),
            system_prompt_path=(installed_package / "prompts/task-system.md").resolve(),
            default_timeout_seconds=default_timeout_seconds,
            workspace_registry=WorkspaceRegistry.from_config(config, config_root=config_root),
        )

    @classmethod
    def from_config(cls, config: Mapping[str, Any], config_path: Path) -> "TaskSettings":
        """以 YAML 文件目录为相对路径根解析设置。"""

        return cls.from_mapping(config, config_root=config_path.resolve().parent)

    def validate_runtime(self) -> None:
        """验证 Node、runtime 文件和精确 pinned Pi 版本。"""

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
        """返回受 UUID 约束的 Task session 目录，不创建它。"""

        canonical_id = UUID(str(task_id))
        return self.session_root / str(canonical_id)


__all__ = ["EXPECTED_PI_VERSION", "TaskSettings", "TaskSettingsError"]
