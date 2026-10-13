"""解析 Main Agent 的模型、语音、Web 与路径设置（兼容旧 Tavily 密钥字段）并维护受信任工作区目录。

配置加载器保留原始 YAML 后，本模块把 app/main_agent/chat_model 段变成不可变路径设置；
WorkspaceRegistry 只接受由配置声明的真实目录，TaskManager 用它将 workspace_id 解析为
内部 cwd。聊天与语音设置只记录 Provider 地址、密钥、模型名及本地模型/许可证路径，
真正的网络连接、可选 SDK 和 ONNX 资产由 composition/provider 模块按需检查加载，因此
关闭、缺少密钥或未安装语音能力时 Backend 仍能正常启动。语音模式默认沿用 Pipeline；
Realtime 设置独立保存 SeedDuplex 凭据和音色，不依赖流水线的密钥或本地推理资产。
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

    @property
    def ids(self) -> tuple[str, ...]:
        """Return configured public identifiers without exposing mutable path storage."""

        return tuple(self._roots)

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
class RealtimeVoiceSettings:
    """SeedDuplex connection settings; an empty voice/key is allowed until voice activation."""

    provider: str = "seeduplex"
    api_key: str = ""
    model: str = "1.2.6.1"
    voice: str = ""

    @classmethod
    def from_config(cls, raw: Mapping[str, Any]) -> "RealtimeVoiceSettings":
        if not isinstance(raw, Mapping):
            raise ValueError("main_agent.speech.realtime must be a mapping.")
        values = {}
        for name, default in (("provider", "seeduplex"), ("api_key", ""), ("model", "1.2.6.1"), ("voice", "")):
            value = raw.get(name, default)
            if not isinstance(value, str):
                raise ValueError(f"main_agent.speech.realtime.{name} must be text.")
            values[name] = value.strip()
        if values["provider"] != "seeduplex" or values["model"] != "1.2.6.1":
            raise ValueError("main_agent.speech.realtime provider/model is unsupported.")
        return cls(**values)


@dataclass(frozen=True, slots=True)
class SpeechSettings:
    """Configured provider model identities and explicitly checked local model/license assets."""

    enabled: bool
    api_key: str
    stt_model_name: str
    tts_model_name: str
    tts_voice: str
    silero_model_path: Path
    silero_license_path: Path
    smart_turn_model_path: Path
    smart_turn_license_path: Path
    mode: str = "pipeline"
    realtime: RealtimeVoiceSettings = RealtimeVoiceSettings()

    @classmethod
    def from_config(cls, raw: Mapping[str, Any], *, config_root: Path) -> "SpeechSettings":
        if not isinstance(raw, Mapping):
            raise ValueError("main_agent.speech must be a mapping.")

        def text(key: str, default: str) -> str:
            value = raw.get(key, default)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"main_agent.speech.{key} must be non-empty text.")
            return value.strip()

        enabled = raw.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ValueError("main_agent.speech.enabled must be a boolean.")

        mode = raw.get("mode", "pipeline")
        if mode not in ("pipeline", "realtime"):
            raise ValueError("main_agent.speech.mode must be pipeline or realtime.")

        return cls(
            enabled=enabled,
            mode=mode,
            realtime=RealtimeVoiceSettings.from_config(raw.get("realtime", {})),
            api_key=str(raw.get("api_key", "")).strip(),
            stt_model_name=text("stt_model_name", "fun-asr-realtime"),
            tts_model_name=text("tts_model_name", "cosyvoice-v3-flash"),
            tts_voice=text("tts_voice", "longanyang"),
            silero_model_path=_path(text("silero_model_path", "assets/models/silero-vad.onnx"), config_root),
            silero_license_path=_path(text("silero_license_path", "assets/licenses/silero-vad.txt"), config_root),
            smart_turn_model_path=_path(text("smart_turn_model_path", "assets/models/smart-turn-v3.2.onnx"), config_root),
            smart_turn_license_path=_path(text("smart_turn_license_path", "assets/licenses/smart-turn-v3.2.txt"), config_root),
        )


@dataclass(frozen=True, slots=True)
class ChatModelSettings:
    """Validated OpenAI-compatible endpoint identity with a bounded invoke deadline."""

    base_url: str
    model_name: str
    api_key: str
    timeout_seconds: float = 60.0

    @classmethod
    def from_config(cls, raw: Mapping[str, Any]) -> "ChatModelSettings":
        if not isinstance(raw, Mapping):
            raise ValueError("chat_model must be a mapping.")

        def text(key: str, default: str = "") -> str:
            value = raw.get(key, default)
            if not isinstance(value, str):
                raise ValueError(f"chat_model.{key} must be text.")
            return value.strip()

        base_url = text("base_url")
        model_name = text("model_name")
        api_key = text("api_key")
        raw_timeout = raw.get("timeout_seconds", 60.0)
        if isinstance(raw_timeout, bool) or not isinstance(raw_timeout, (int, float)):
            raise ValueError("chat_model.timeout_seconds must be a number.")
        timeout = float(raw_timeout)
        if not 0 < timeout <= 600:
            raise ValueError("chat_model.timeout_seconds must be between 0 and 600.")
        return cls(base_url.rstrip("/"), model_name, api_key, timeout)


@dataclass(frozen=True, slots=True)
class WebSettings:
    """Optional public-web credentials; an empty key means no web capability."""

    api_key: str = ""
    provider: str = "tavily"

    @classmethod
    def from_config(cls, raw: Mapping[str, Any]) -> "WebSettings":
        if not isinstance(raw, Mapping):
            raise ValueError("main_agent.web must be a mapping.")
        provider = raw.get("provider", "tavily")
        if provider != "tavily":
            raise ValueError("main_agent.web.provider is unsupported.")
        value = raw.get("api_key", raw.get("tavily_api_key", ""))
        if not isinstance(value, str):
            raise ValueError("main_agent.web.api_key must be text.")
        return cls(api_key=value.strip(), provider=provider)


@dataclass(frozen=True, slots=True)
class MainAgentSettings:
    """Typed Main Agent paths shared by profile storage and workspace-aware services."""

    data_dir: Path
    profile_root: Path
    workspaces: WorkspaceRegistry
    chat_model: ChatModelSettings
    speech: SpeechSettings
    web: WebSettings

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
            chat_model=ChatModelSettings.from_config(config.get("chat_model", {})),
            speech=SpeechSettings.from_config(
                main_agent.get("speech", {}), config_root=root
            ),
            web=WebSettings.from_config(main_agent.get("web", {})),
        )


__all__ = [
    "ChatModelSettings", "MainAgentSettings", "SpeechSettings", "WebSettings", "WorkspaceRegistry",
    "WorkspaceRegistryError",
]
