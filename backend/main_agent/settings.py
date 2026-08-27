"""解析 Main Agent 的局部设置并维护共享、受信任的工作区目录。

配置加载器保留原始 YAML 后，本模块把 app/main_agent 段变成不可变路径设置；
WorkspaceRegistry 只接受由配置声明的真实目录，TaskManager 用它将 workspace_id 解析为
内部 cwd，后续 Main Agent 的安全读取也复用这同一份注册表而不接收任意文件系统路径。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class WorkspaceRegistryError(ValueError):
    """Raised when a workspace declaration or lookup is unsafe or unknown."""


def _path(value: str, root: Path) -> Path:
    """Resolve a configured non-empty path relative to a trusted configuration root."""

    path = Path(value).expanduser()
    return (path if path.is_absolute() else root / path).resolve()


def _workspace_root(value: str | Path) -> Path:
    """Reject symlinks in every existing lexical component before canonicalizing a root."""

    path = Path(value).expanduser()
    candidate = path if path.is_absolute() else Path.cwd() / path
    current = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        if part == "..":
            current = current.parent
            continue
        current /= part
        if current.is_symlink():
            raise WorkspaceRegistryError("workspace root must not contain a symlink.")
    return candidate.resolve()


def _configured_path(mapping: Mapping[str, Any], key: str, default: str) -> str:
    """Return one explicit non-empty string path without coercing YAML scalar types."""

    value = mapping.get(key, default)
    if not isinstance(value, str) or not (path := value.strip()):
        raise ValueError(f"{key} must be a non-empty string path.")
    return path


class WorkspaceRegistry:
    """Immutable lookup of configured workspace identifiers to real directory roots."""

    def __init__(self, workspaces: Mapping[str, str | Path]) -> None:
        roots: dict[str, Path] = {}
        for workspace_id, raw_path in workspaces.items():
            identifier = self._validate_id(workspace_id)
            resolved = _workspace_root(raw_path)
            if not resolved.is_dir():
                raise WorkspaceRegistryError(
                    f"workspace {identifier!r} root is not an existing directory."
                )
            if identifier in roots:
                raise WorkspaceRegistryError(f"duplicate workspace id: {identifier!r}")
            roots[identifier] = resolved
        self._roots = roots

    @classmethod
    def empty(cls) -> "WorkspaceRegistry":
        """Return the explicit no-workspace registry used by isolated legacy tests."""

        return cls({})

    @classmethod
    def from_config(cls, config: Mapping[str, Any], *, config_root: Path) -> "WorkspaceRegistry":
        """Read app.workspaces list entries relative to the configuration file."""

        app = config.get("app", {})
        if not isinstance(app, Mapping):
            raise WorkspaceRegistryError("app must be a mapping.")
        raw_workspaces = app.get("workspaces", [])
        if not isinstance(raw_workspaces, list):
            raise WorkspaceRegistryError("app.workspaces must be a list.")
        roots: dict[str, Path] = {}
        for entry in raw_workspaces:
            if not isinstance(entry, Mapping):
                raise WorkspaceRegistryError("app.workspaces entries must be mappings.")
            workspace_id = entry.get("id")
            workspace_path = entry.get("path")
            if not isinstance(workspace_id, str) or not isinstance(workspace_path, str):
                raise WorkspaceRegistryError("workspace entries require string id and path.")
            if workspace_id in roots:
                raise WorkspaceRegistryError(f"duplicate workspace id: {workspace_id!r}")
            path = Path(workspace_path).expanduser()
            roots[workspace_id] = path if path.is_absolute() else config_root / path
        return cls(roots)

    @staticmethod
    def _validate_id(workspace_id: object) -> str:
        if not isinstance(workspace_id, str):
            raise WorkspaceRegistryError("workspace id is required.")
        normalized = workspace_id.strip()
        if (
            not normalized
            or len(normalized) > 128
            or "/" in normalized
            or "\\" in normalized
            or normalized in {".", ".."}
        ):
            raise WorkspaceRegistryError("workspace id is invalid.")
        return normalized

    def require(self, workspace_id: object) -> Path:
        """Return one registered root, rejecting unknown or path-shaped identifiers."""

        identifier = self._validate_id(workspace_id)
        try:
            return self._roots[identifier]
        except KeyError as exc:
            raise WorkspaceRegistryError(f"Unknown workspace: {identifier}") from exc


@dataclass(frozen=True, slots=True)
class MainAgentSettings:
    """Typed Main Agent paths shared by profile storage and workspace-aware services."""

    data_dir: Path
    profile_root: Path
    workspaces: WorkspaceRegistry

    @classmethod
    def from_config(
        cls, config: Mapping[str, Any], config_path: Path
    ) -> "MainAgentSettings":
        """Derive default profile storage and workspaces from retained Backend config."""

        root = config_path.resolve().parent
        app = config.get("app", {})
        main_agent = config.get("main_agent", {})
        if not isinstance(app, Mapping) or not isinstance(main_agent, Mapping):
            raise ValueError("app and main_agent must be mappings.")
        data_dir = _path(_configured_path(app, "data_dir", "./.aurabot-data"), root)
        profile_root = _path(
            _configured_path(main_agent, "profile_dir", "profiles"), data_dir
        )
        return cls(
            data_dir=data_dir,
            profile_root=profile_root,
            workspaces=WorkspaceRegistry.from_config(config, config_root=root),
        )


__all__ = ["MainAgentSettings", "WorkspaceRegistry", "WorkspaceRegistryError"]
