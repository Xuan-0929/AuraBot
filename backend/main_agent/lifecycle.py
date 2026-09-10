"""Serialize explicit Profile activation and preserve ownership of all active business resources.

Electron sends one saved/applied Soul and adaptor snapshot. Activation revokes the old transport generation
before changing resources; same-Profile application waits for cancelled invokes before freezing new tools.
Token issuance only checks the active snapshot. Failed retirement blocks every activation until the
remaining owners close; completed shutdown steps are never repeated. Dormant Profiles own no active handles.
"""

# SOUL remains complete; the bound includes the controlled name envelope above a 32 KiB document.
from __future__ import annotations

import asyncio
import inspect
import json
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .avatar import AvatarCapabilities


_PROFILE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


@dataclass(slots=True)
class ProfileResources:
    """All owners that exist only while one Profile is active."""

    profile_id: str
    runtime: Any
    speech: Any
    memory: Any
    closeables: tuple[Any, ...] = ()
    generation: int = 0
    revision: int = 0
    snapshot_key: str = ""
    speech_factory: Callable[[], Any] | None = None
    _shutdown_stage: int = 0

    async def start_speech(self) -> None:
        """Open speech lazily for an explicitly activated voice session."""

        if self.speech is not None:
            return
        if self.speech_factory is None:
            raise RuntimeError("speech_unavailable")
        speech = self.speech_factory()
        try:
            await speech.startup()
        except BaseException:
            await speech.shutdown()
            raise
        self.speech = speech

    async def stop_speech(self) -> None:
        """Close the one Speech owner before another session can start it."""

        speech = self.speech
        if speech is not None:
            await speech.shutdown()
            self.speech = None

    async def shutdown(self) -> None:
        """Close active work before storage and network adapters in deterministic ownership order."""

        if self._shutdown_stage == 0:
            await _close(self.runtime)
            self._shutdown_stage = 1
        if self._shutdown_stage == 1:
            await self.stop_speech()
            self._shutdown_stage = 2
        if self._shutdown_stage == 2:
            await _close(self.memory)
            self._shutdown_stage = 3
        while self._shutdown_stage - 3 < len(self.closeables):
            await _close(self.closeables[self._shutdown_stage - 3])
            self._shutdown_stage += 1


ProfileFactory = Callable[[str, Path, str], Awaitable[ProfileResources]]


class ActiveProfileManager:
    """Own at most one ``ProfileResources`` and serialize Soul refresh or Profile replacement."""

    def __init__(self, profile_root: Path, hub: Any, factory: ProfileFactory, *, generation_seed: int | None = None) -> None:
        self.profile_root = Path(profile_root).resolve()
        self.hub = hub
        self.factory = factory
        self._lock = asyncio.Lock()
        self._active: ProfileResources | None = None
        # Random process seed prevents late receipts from a previous Backend matching after restart.
        self._next_generation = secrets.randbits(48) if generation_seed is None else generation_seed
        self._retiring: dict[str, ProfileResources] = {}

    @property
    def active_profile_id(self) -> str | None:
        return self._active.profile_id if self._active is not None else None

    @property
    def active_revision(self) -> int:
        return self._active.revision if self._active is not None else 0

    @property
    def runtime(self) -> Any | None:
        return self._active.runtime if self._active is not None else None

    @property
    def memory(self) -> Any | None:
        return self._active.memory if self._active is not None else None

    async def activate(self, profile_id: str, soul: str, *, revision: int = 0,
                       adaptor: dict[str, Any] | None = None, model_version: str = "", force: bool = False) -> int:
        """Activate or refresh one Electron-owned Profile and replace its voice generation."""

        identifier = self.validate_profile_id(profile_id)
        effective_soul = str(soul).strip()
        if not effective_soul or len(effective_soul.encode("utf-8")) > 34 * 1024:
            raise ValueError("soul_envelope_invalid")
        if type(revision) is not int or revision < 0:
            raise ValueError("invalid_revision")
        if model_version and (not isinstance(model_version, str) or not _PROFILE_ID.fullmatch(model_version)):
            raise ValueError("invalid_model_version")
        live2d_root = self.profile_path(identifier) / "live2d"
        if model_version:
            live2d_root = live2d_root / "packages" / model_version
        relative = live2d_root.relative_to(self.profile_path(identifier))
        component = self.profile_path(identifier)
        for part in relative.parts:
            component /= part
            if component.is_symlink():
                raise ValueError("model_package_symlink")
        effective_adaptor = adaptor or {"schema_version": 1, "revision": max(revision, 1), "model": None, "entries": {}, "states": {}}
        capabilities = AvatarCapabilities.compile(effective_adaptor, profile_id=identifier, revision=revision,
            live2d_root=live2d_root)
        snapshot_key = json.dumps([effective_soul, revision, adaptor or {}, model_version], sort_keys=True, ensure_ascii=True)
        if self._lock.locked():
            raise RuntimeError("activation_in_progress")
        async with self._lock:
            await self._finish_retiring()
            current = self._active
            if current is not None and current.profile_id == identifier and revision > 0 and current.snapshot_key == snapshot_key and self.hub.active_profile_id == identifier and self.hub.active_generation == current.generation:
                return current.generation
            if current is not None and not force and getattr(current.runtime, "restart_busy", lambda: False)():
                raise RuntimeError("profile_busy")
            self._next_generation += 1
            generation = self._next_generation
            current = self._active
            if current is not None and current.profile_id == identifier:
                await self.hub.clear_active(current.profile_id, current.generation)
                disconnect = getattr(current.runtime, "renderer_disconnected", None)
                if disconnect is not None:
                    await disconnect()
                wait_idle = getattr(current.runtime, "wait_until_idle", None)
                if wait_idle is not None:
                    await wait_idle()
                await current.runtime.update_soul(effective_soul)
                if hasattr(current.runtime, "tool_policy"):
                    current.runtime.tool_policy.set_avatar_capabilities(capabilities)
                current.generation = generation
                current.runtime.generation = generation
                current.revision = revision
                current.snapshot_key = snapshot_key
                await self.hub.set_active(
                    identifier, generation, current
                )
                return generation
            if current is not None:
                await self.hub.clear_active(current.profile_id, current.generation)
                self._active = None
                self._retiring[current.profile_id] = current
                await self._finish_retiring()
            path = self.profile_path(identifier)
            path.mkdir(parents=True, exist_ok=True)
            resources: ProfileResources | None = None
            try:
                resources = await self.factory(identifier, path, effective_soul)
                if resources.profile_id != identifier:
                    raise RuntimeError("Profile factory returned a mismatched identity")
                resources.generation = generation
                resources.runtime.generation = generation
                resources.revision = revision
                resources.snapshot_key = snapshot_key
                if hasattr(resources.runtime, "tool_policy"):
                    resources.runtime.tool_policy.set_avatar_capabilities(capabilities)
                await self.hub.set_active(
                    identifier, generation, resources
                )
            except BaseException:
                if resources is not None:
                    self._retiring[resources.profile_id] = resources
                    await self.hub.clear_active(resources.profile_id, resources.generation)
                    await self._finish_retiring()
                raise
            self._active = resources
            return generation

    async def renew_session(self, profile_id: str, revision: int) -> int:
        """Replace only the transport generation; token issuance never edits a Profile snapshot."""
        if self._lock.locked():
            raise RuntimeError("activation_in_progress")
        async with self._lock:
            current = self._active
            if current is None or current.profile_id != profile_id or current.revision != revision:
                raise RuntimeError("profile_snapshot_not_active")
            await self.hub.clear_active(current.profile_id, current.generation)
            await current.runtime.renderer_disconnected()
            await current.runtime.wait_until_idle()
            self._next_generation += 1
            generation = self._next_generation
            current.generation = generation
            current.runtime.generation = generation
            await self.hub.set_active(profile_id, generation, current)
            return generation

    async def _finish_retiring(self) -> None:
        """Complete all older owners before admitting any new Profile; failed owners remain reachable."""
        for identifier, retiring in tuple(self._retiring.items()):
            try:
                await retiring.shutdown()
            except Exception:
                raise RuntimeError("profile_cleanup_incomplete") from None
            self._retiring.pop(identifier, None)

    async def shutdown(self) -> None:
        """Fence realtime admission and close the sole active Profile, if one exists."""

        async with self._lock:
            await self._finish_retiring()
            current = self._active
            if current is None:
                return
            await self.hub.clear_active(current.profile_id, current.generation)
            self._active = None
            self._retiring[current.profile_id] = current
            await self._finish_retiring()

    def profile_path(self, profile_id: str) -> Path:
        identifier = self.validate_profile_id(profile_id)
        lexical = self.profile_root / identifier
        if lexical.is_symlink():
            raise ValueError("profile_directory_symlink")
        candidate = lexical.resolve()
        if candidate.parent != self.profile_root:
            raise ValueError("profile_id escapes the Profile root")
        return candidate

    @staticmethod
    def validate_profile_id(profile_id: object) -> str:
        if not isinstance(profile_id, str) or _PROFILE_ID.fullmatch(profile_id) is None:
            raise ValueError("profile_id is invalid")
        return profile_id


async def _close(owner: Any) -> None:
    for name in ("shutdown", "aclose", "close"):
        method = getattr(owner, name, None)
        if callable(method):
            result = method()
            if inspect.isawaitable(result):
                await result
            return


__all__ = ["ActiveProfileManager", "ProfileResources"]
