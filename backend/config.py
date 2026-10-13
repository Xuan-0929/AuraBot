"""Read one immutable Electron-owned JSON configuration snapshot for Backend composition.

Legacy version 1 and Profile-owned version 2 snapshots share the Backend section; persona is never a
Backend configuration source. Version/revision stay process metadata. Missing files
produce safe blank defaults. JSON strings stay literal, and saves never hot reload a running process."""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from pathlib import Path
from threading import RLock
from typing import Any

import json

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = Path.home() / "Library/Application Support/AuraBot/desktop-v2/settings.json"
CONFIG_PATH_ENV = "AURABOT_CONFIG_PATH"
RETAINED_TOP_LEVEL_KEYS = frozenset(
    {"app", "dev", "memory", "ports", "chat_model", "gateway_model", "main_agent", "task"}
)


class ConfigError(RuntimeError):
    """Raised when the runtime configuration cannot be initialized."""


class ConfigNotInitializedError(ConfigError):
    """Raised when configuration is accessed before process initialization."""


class Config(Mapping[str, Any]):
    """An in-memory view of one JSON configuration file.

    The module-level ``config`` instance is initialized once by ``backend.main``.
    Mapping access and top-level attribute access are both supported::

        config["ports"]["backend_gateway"]
        config.ports["backend_gateway"]
    """

    def __init__(self) -> None:
        self._data: dict[str, Any] | None = None
        self._path: Path | None = None
        self._lock = RLock()
        self.revision = 0

    @property
    def path(self) -> Path:
        if self._path is None:
            raise ConfigNotInitializedError("AuraBot configuration has not been initialized.")
        return self._path

    def initialize(self, config_path: str | Path | None = None) -> Config:
        """Load the JSON file once and return this shared configuration object."""

        resolved_path = resolve_config_path(config_path)

        with self._lock:
            if self._data is not None:
                if self._path != resolved_path:
                    raise ConfigError(
                        "AuraBot configuration is already initialized from "
                        f"{self._path}; refusing to reinitialize it from {resolved_path}."
                    )
                return self

            try:
                loaded = json.loads(resolved_path.read_text(encoding="utf-8")) if resolved_path.is_file() else {"version": 2, "revision": 0, "backend": {}}
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ConfigError("Invalid AuraBot JSON configuration") from exc
            if not isinstance(loaded, dict) or type(loaded.get("version")) is not int or loaded["version"] not in {1, 2}:
                raise ConfigError("AuraBot configuration must be a version 1 or 2 object")
            revision = loaded.get("revision", 0)
            backend = loaded.get("backend", {})
            if type(revision) is not int or revision < 0 or not isinstance(backend, dict):
                raise ConfigError("Invalid configuration revision or backend section")
            self.revision = revision
            self._data = {key: value for key, value in backend.items() if key in RETAINED_TOP_LEVEL_KEYS}
            self._path = resolved_path
            return self

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


config = Config()


def initialize_config(config_path: str | Path | None = None) -> Config:
    """Initialize and return the process-wide configuration singleton."""

    return config.initialize(config_path)


__all__ = [
    "CONFIG_PATH_ENV",
    "DEFAULT_CONFIG_PATH",
    "Config",
    "ConfigError",
    "ConfigNotInitializedError",
    "config",
    "initialize_config",
    "resolve_config_path",
]
