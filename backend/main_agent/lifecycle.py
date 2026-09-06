"""Serialize AuraBot's one active Main Agent Profile and all Profile-owned resources.

Electron supplies the effective Soul when requesting a realtime capability. ``ActiveProfileManager``
validates the Profile identity, creates only its runtime-data directory, increments a generation for
every replaceable voice session, and keeps an existing same-Profile Runtime alive while updating its
next-invoke Soul. Switching first clears the old Hub session, then closes Runtime, Speech, Memory,
Context/provider/tool resources before composing the new Profile. Memory is the existing Profile-scoped
``MemoryService``; lifecycle exposes no second browse/job/retry surface and dormant Profiles own nothing.
ProfileResources alone stores the lazy Speech owner; RealtimeHub reads that owner and requests its
start/stop operations without maintaining a second resource reference.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
    speech_factory: Callable[[], Any] | None = None

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

        speech, self.speech = self.speech, None
        if speech is not None:
            await speech.shutdown()

    async def shutdown(self) -> None:
        """Close active work before storage and network adapters in deterministic ownership order."""

        await _close(self.runtime)
        await self.stop_speech()
        await _close(self.memory)
        for owner in self.closeables:
            await _close(owner)


ProfileFactory = Callable[[str, Path, str], Awaitable[ProfileResources]]


class ActiveProfileManager:
    """Own at most one ``ProfileResources`` and serialize Soul refresh or Profile replacement."""

    def __init__(self, profile_root: Path, hub: Any, factory: ProfileFactory) -> None:
        self.profile_root = Path(profile_root).resolve()
        self.hub = hub
        self.factory = factory
        self._lock = asyncio.Lock()
        self._active: ProfileResources | None = None
        self._next_generation = 0

    @property
    def active_profile_id(self) -> str | None:
        return self._active.profile_id if self._active is not None else None

    @property
    def runtime(self) -> Any | None:
        return self._active.runtime if self._active is not None else None

    @property
    def memory(self) -> Any | None:
        return self._active.memory if self._active is not None else None

    async def activate(self, profile_id: str, soul: str) -> int:
        """Activate or refresh one Electron-owned Profile and replace its voice generation."""

        identifier = self.validate_profile_id(profile_id)
        effective_soul = str(soul).strip()
        if not effective_soul or len(effective_soul) > 32 * 1024:
            raise ValueError("soul must be non-empty and at most 32 KiB")
        async with self._lock:
            self._next_generation += 1
            generation = self._next_generation
            current = self._active
            if current is not None and current.profile_id == identifier:
                await current.runtime.update_soul(effective_soul)
                current.generation = generation
                await self.hub.set_active(
                    identifier, generation, current
                )
                return generation
            if current is not None:
                await self.hub.clear_active(current.profile_id, current.generation)
                self._active = None
                await current.shutdown()
            path = self.profile_path(identifier)
            path.mkdir(parents=True, exist_ok=True)
            resources: ProfileResources | None = None
            try:
                resources = await self.factory(identifier, path, effective_soul)
                if resources.profile_id != identifier:
                    raise RuntimeError("Profile factory returned a mismatched identity")
                resources.generation = generation
                await self.hub.set_active(
                    identifier, generation, resources
                )
            except BaseException:
                if resources is not None:
                    await resources.shutdown()
                raise
            self._active = resources
            return generation

    async def shutdown(self) -> None:
        """Fence realtime admission and close the sole active Profile, if one exists."""

        async with self._lock:
            current = self._active
            if current is None:
                return
            await self.hub.clear_active(current.profile_id, current.generation)
            self._active = None
            await current.shutdown()

    def profile_path(self, profile_id: str) -> Path:
        identifier = self.validate_profile_id(profile_id)
        candidate = (self.profile_root / identifier).resolve()
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
