"""加载 Backend 进程级 YAML 配置并隔离已经删除的旧执行字段。

Backend 导入时只初始化一次本模块；端口、保留模型角色与新的 Pi-native ``task`` 段
通过此边界进入 ``backend.main`` 和保留服务。旧 Task/MCP 段在环境变量展开前即被丢弃，
因此历史配置不会让当前进程重新依赖已删除的 runtime secret。
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Mapping
from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"
CONFIG_PATH_ENV = "AURABOT_CONFIG_PATH"
RETAINED_TOP_LEVEL_KEYS = frozenset(
    {"ports", "chat_model", "gateway_model", "task"}
)
ENV_REFERENCE_PATTERN = re.compile(
    r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}"
)


class ConfigError(RuntimeError):
    """Raised when the runtime configuration cannot be initialized."""


class ConfigNotInitializedError(ConfigError):
    """Raised when configuration is accessed before process initialization."""


class Config(Mapping[str, Any]):
    """An in-memory view of one YAML configuration file.

    The module-level ``config`` instance is initialized once by ``backend.main``.
    Mapping access and top-level attribute access are both supported::

        config["ports"]["backend_gateway"]
        config.ports["backend_gateway"]
    """

    def __init__(self) -> None:
        self._data: dict[str, Any] | None = None
        self._path: Path | None = None
        self._lock = RLock()

    @property
    def initialized(self) -> bool:
        return self._data is not None

    @property
    def path(self) -> Path:
        if self._path is None:
            raise ConfigNotInitializedError("AuraBot configuration has not been initialized.")
        return self._path

    def initialize(self, config_path: str | Path | None = None) -> Config:
        """Load the YAML file once and return this shared configuration object."""

        resolved_path = resolve_config_path(config_path)

        with self._lock:
            if self._data is not None:
                if self._path != resolved_path:
                    raise ConfigError(
                        "AuraBot configuration is already initialized from "
                        f"{self._path}; refusing to reinitialize it from {resolved_path}."
                    )
                return self

            if not resolved_path.is_file():
                raise ConfigError(
                    f"AuraBot config file not found: {resolved_path}. "
                    "Create config.yaml from config.example.yaml before starting the backend."
                )

            try:
                loaded = yaml.safe_load(resolved_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, yaml.YAMLError) as exc:
                raise ConfigError(
                    f"Failed to load AuraBot configuration from {resolved_path}: {exc}"
                ) from exc

            if loaded is None:
                loaded = {}
            if not isinstance(loaded, dict):
                raise ConfigError(
                    f"AuraBot configuration root must be a mapping, got {type(loaded).__name__}."
                )
            if not all(isinstance(key, str) for key in loaded):
                raise ConfigError("AuraBot configuration top-level keys must all be strings.")

            retained = {
                key: value for key, value in loaded.items() if key in RETAINED_TOP_LEVEL_KEYS
            }
            self._data = _expand_environment_references(retained)
            self._path = resolved_path
            return self

    def as_dict(self) -> dict[str, Any]:
        """Return a defensive copy suitable for serialization or inspection."""

        return deepcopy(self._require_data())

    def __getitem__(self, key: str) -> Any:
        return self._require_data()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._require_data())

    def __len__(self) -> int:
        return len(self._require_data())

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __repr__(self) -> str:
        if self._path is None:
            return "Config(initialized=False)"
        return f"Config(initialized=True, path={self._path!s})"

    def _require_data(self) -> dict[str, Any]:
        if self._data is None:
            raise ConfigNotInitializedError("AuraBot configuration has not been initialized.")
        return self._data


def resolve_config_path(config_path: str | Path | None = None) -> Path:
    """Resolve an explicit or environment-provided path against the project root."""

    raw_path: str | Path = config_path or os.environ.get(CONFIG_PATH_ENV) or DEFAULT_CONFIG_PATH
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _expand_environment_references(value: Any, location: str = "config") -> Any:
    """Recursively expand ${VAR} and ${VAR:-fallback} string references."""

    if isinstance(value, dict):
        return {
            key: _expand_environment_references(item, f"{location}.{key}")
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _expand_environment_references(item, f"{location}[{index}]")
            for index, item in enumerate(value)
        ]
    if not isinstance(value, str):
        return value

    def replace_reference(match: re.Match[str]) -> str:
        variable_name = match.group(1)
        fallback = match.group(2)

        if variable_name in os.environ:
            return os.environ[variable_name]
        if fallback is not None:
            return fallback

        raise ConfigError(
            f"Required environment variable {variable_name} referenced by "
            f"{location} is not set."
        )

    return ENV_REFERENCE_PATTERN.sub(replace_reference, value)


config = Config()


def initialize_config(config_path: str | Path | None = None) -> Config:
    """Initialize and return the process-wide configuration singleton."""

    return config.initialize(config_path)


def get_config() -> Config:
    """Return the initialized process-wide configuration singleton."""

    if not config.initialized:
        raise ConfigNotInitializedError("AuraBot configuration has not been initialized.")
    return config


__all__ = [
    "CONFIG_PATH_ENV",
    "DEFAULT_CONFIG_PATH",
    "Config",
    "ConfigError",
    "ConfigNotInitializedError",
    "config",
    "get_config",
    "initialize_config",
    "resolve_config_path",
]
