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
    """
    Fully derived process settings shared by Store, RPC and Supervisor.

    可以认为是一个单例的全局Task共有配置对象。
    """

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
        """从 Backend 配置映射构造完整、不可变的 Task Runtime 设置。

        本方法读取顶层 ``task`` 配置，校验并发数和默认超时，并以 ``config_root``
        为相对路径基准派生 Task SQLite、Pi session root、独立 Pi Home、项目锁定的
        Pi CLI、AuraBot Extension 入口及 system prompt 路径。它只解析和派生配置，
        不访问文件系统验证安装内容；文件与版本检查由 ``validate_runtime()`` 负责。

        配置结构缺失或数值无效时抛出 ``TaskSettingsError``。
        """

        raw = config.get("task")
        if not isinstance(raw, Mapping):
            raise TaskSettingsError("Configuration must contain a task mapping.")

        concurrency_limit = int(raw.get("concurrency_limit", 2))
        raw_default_timeout = raw.get("default_timeout_seconds", 1800)
        if isinstance(raw_default_timeout, bool):
            raise TaskSettingsError(
                "task.default_timeout_seconds must be between 1 and 86400."
            )
        default_timeout_seconds = int(raw_default_timeout)
        if concurrency_limit < 1:
            raise TaskSettingsError("task.concurrency_limit must be at least 1.")
        if not 1 <= default_timeout_seconds <= 86_400:
            raise TaskSettingsError(
                "task.default_timeout_seconds must be between 1 and 86400."
            )

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
        """以 YAML 配置文件所在目录为基准构造 Task Runtime 设置。

        ``config_path`` 只用于确定相对路径的根目录；实际字段解析统一委托给
        ``from_mapping()``，从而避免调用方因当前工作目录不同而得到不同的安装路径。
        返回值尚未执行 Node、Pi、Extension 或 Prompt 的存在性与版本验证。
        """

        return cls.from_mapping(config, config_root=config_path.resolve().parent)

    def validate_runtime(self) -> None:
        """验证当前设置指向的 Node、Pi CLI 与 AuraBot Pi 资源可用于启动 Task。

        Node 为命令名时通过 ``PATH`` 定位，为显式路径时直接检查对应文件；随后确认
        项目锁定的 Pi CLI、AuraBot Extension 入口和 Task system prompt 均存在。最后
        运行 ``Pi --version``，要求其成功退出并精确报告 ``EXPECTED_PI_VERSION``。

        本方法只执行启动前安装完整性检查，不创建 Pi Home、session 或子进程。任何
        文件缺失、Node 不可用、版本命令失败、超时或版本不匹配都会抛出
        ``TaskSettingsError``。
        """

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
        """返回指定 Task 在 ``session_root`` 下的规范 session 目录。

        输入会重新解析为 UUID，避免任意字符串或路径片段进入文件系统路径；返回目录
        始终是 ``session_root/<canonical-task-uuid>`` 的直接子目录。本方法只计算路径，
        不创建目录；目录创建和生命周期分别由 Pi Process Supervisor 与清理流程负责。
        """

        canonical_id = UUID(str(task_id))
        return self.session_root / str(canonical_id)


__all__ = ["EXPECTED_PI_VERSION", "TaskSettings", "TaskSettingsError"]
