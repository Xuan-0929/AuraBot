"""Provide causal Observation state plus bounded provider-neutral Web and workspace read executors.

Runtime owns one ObservationLedger for request reuse and terminal persistence. WebTools shares a small
opaque source map across ``web_search`` and ``web_read`` and delegates public-page extraction to the selected search adapter. Producer content fits Runtime's JSON scalar boundary; WorkspaceReader also enforces no-follow,
secret, binary, file-size, and paging boundaries beneath configured roots.
"""

from __future__ import annotations

import asyncio
import codecs
import errno
import hashlib
import json
import os
import stat
import urllib.parse
from collections import OrderedDict
from collections.abc import Mapping
from typing import Any

from .web_provider import ObservationExecutorError, SearchProvider
from .settings import WorkspaceRegistry, WorkspaceRegistryError
from .tool_runtime import ToolCall


_MAX_CONTEXT_BYTES = 16 * 1024
_MAX_WORKSPACE_FILE_BYTES = 1024 * 1024
_MAX_WORKSPACE_ITEMS = 200
_MAX_WORKSPACE_SCAN = 2_000
_MAX_WORKSPACE_LINES = 200
_MAX_START_LINE = 10_000
_OBSERVATION_HISTORY_LIMIT = 256
_SECRET_NAMES = {".git", ".ssh", ".gnupg", ".aws", ".config", "credentials", "secrets"}
_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore")


class ObservationSecurityError(ValueError):
    """Stable secret-free trust-boundary rejection."""


def _copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError("Observation facts must be JSON-safe") from exc


class ObservationLedger:
    """Keep one latest fact per accepted request and dedupe identical calls within an ActiveInput."""

    def __init__(self) -> None:
        self._latest: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._dedupe: dict[tuple[str, str, str], str] = {}

    def start(
        self,
        observation_id: str,
        *,
        input_id: str,
        revision: int,
        kind: str,
        arguments: Mapping[str, Any],
    ) -> tuple[str, bool]:
        copied = _copy(dict(arguments))
        canonical = json.dumps(copied, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        key = (input_id, kind, canonical)
        if key in self._dedupe:
            return self._dedupe[key], False
        if observation_id in self._latest:
            raise ValueError("Observation identity is already in use")
        self._trim()
        if len(self._latest) >= _OBSERVATION_HISTORY_LIMIT:
            raise ValueError("Observation running history limit reached")
        fact = {
            "id": observation_id,
            "kind": kind,
            "request": copied,
            "status": "running",
            "origin_input_id": input_id,
            "origin_revision": revision,
        }
        self._latest[observation_id] = fact
        self._dedupe[key] = observation_id
        return observation_id, True

    def settle(self, observation_id: str, status: str, payload: Any) -> tuple[dict[str, Any] | None, bool]:
        if status not in {"completed", "failed"}:
            raise ValueError("invalid Observation terminal status")
        current = self._latest.get(observation_id)
        if current is None:
            return None, False
        if current["status"] != "running":
            return _copy(current), False
        terminal = {"result": _copy(payload)} if status == "completed" else {
            "error_type": payload[:128] if isinstance(payload, str) and payload.isidentifier() else "ExecutorError"
        }
        fact = {**current, "status": status, **terminal}
        self._latest[observation_id] = fact
        self._latest.move_to_end(observation_id)
        self._trim()
        return _copy(fact), True

    def terminal_fact(self, observation_id: str) -> dict[str, Any] | None:
        fact = self._latest.get(observation_id)
        return _copy(fact) if fact and fact["status"] != "running" else None

    def fold(self) -> tuple[dict[str, Any], ...]:
        return tuple(_copy(value) for value in self._latest.values())

    def _trim(self) -> None:
        while len(self._latest) >= _OBSERVATION_HISTORY_LIMIT:
            terminal = next((key for key, fact in self._latest.items() if fact["status"] != "running"), None)
            if terminal is None:
                return
            fact = self._latest.pop(terminal)
            canonical = json.dumps(fact["request"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            self._dedupe.pop((fact["origin_input_id"], fact["kind"], canonical), None)


class WebTools:
    """Keep bounded source references and tool results independent of the provider."""

    def __init__(self, provider: SearchProvider, *, max_sources: int = 256) -> None:
        self.provider = provider
        self.max_sources = max_sources
        self._sources: OrderedDict[str, str] = OrderedDict()

    async def search(self, call: ToolCall) -> dict[str, Any]:
        query = call.arguments["query"]
        results = []
        for item in (await self.provider.search(query))[:8]:
            try:
                source_id = self._register(item.url)
            except ObservationSecurityError:
                continue
            results.append({
                "source_id": source_id,
                "title": item.title[:500],
                "snippet": item.snippet[:4_000],
            })
        return {"query": query, "results": results}

    async def read(self, call: ToolCall) -> dict[str, Any]:
        source_id = call.arguments["source_id"]
        try:
            url = self._sources[source_id]
        except (KeyError, TypeError) as exc:
            raise ObservationSecurityError("source_id is unknown") from exc
        self._sources.move_to_end(source_id)
        return {"source_id": source_id, "content": _truncate(await self.provider.read(url))}

    def _register(self, raw: Any) -> str:
        if not isinstance(raw, str):
            raise ObservationSecurityError("web source URL is invalid")
        parsed = urllib.parse.urlsplit(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ObservationSecurityError("web source URL is invalid")
        normalized = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))
        source_id = "source-" + hashlib.sha256(normalized.encode()).hexdigest()[:20]
        self._sources[source_id] = normalized
        self._sources.move_to_end(source_id)
        while len(self._sources) > self.max_sources:
            self._sources.popitem(last=False)
        return source_id

    async def aclose(self) -> None:
        await self.provider.aclose()


def _truncate(text: str) -> str:
    encoded = text.encode("utf-8")[:_MAX_CONTEXT_BYTES]
    return encoded.decode("utf-8", "ignore")


def _relative_parts(raw_path: object, *, allow_root: bool) -> tuple[str, ...]:
    if not isinstance(raw_path, str):
        raise ObservationSecurityError("workspace path must be text")
    if "\0" in raw_path or "\\" in raw_path or raw_path.startswith("/"):
        raise ObservationSecurityError("workspace path is invalid")
    parts = tuple(part for part in raw_path.split("/") if part not in {"", "."})
    if (not allow_root and not parts) or any(part == ".." or _secret_name(part) for part in parts):
        raise ObservationSecurityError("workspace path is unavailable or unsafe")
    return parts


def _secret_name(name: str) -> bool:
    lowered = name.casefold()
    return (
        lowered in _SECRET_NAMES
        or lowered == ".env"
        or lowered.startswith(".env.")
        or lowered.endswith(_SECRET_SUFFIXES)
        or "credential" in lowered
        or "secret" in lowered
        or "service-account" in lowered
        or "private-key" in lowered
    )


def _binary(data: bytes) -> bool:
    if b"\0" in data:
        return True
    try:
        codecs.getincrementaldecoder("utf-8")("strict").decode(data, final=False)
    except UnicodeDecodeError:
        return True
    controls = sum(byte < 32 and byte not in {9, 10, 13} for byte in data)
    return bool(data) and controls / len(data) > 0.02


class WorkspaceReader:
    """Perform bounded no-follow list/read operations beneath configured workspace roots."""

    def __init__(self, registry: WorkspaceRegistry) -> None:
        self._registry = registry

    def list(self, workspace_id: object, path: object = "") -> dict[str, Any]:
        descriptor = self._open(workspace_id, _relative_parts(path, allow_root=True), directory=True)
        try:
            items = []
            with os.scandir(descriptor) as entries:
                for scanned, entry in enumerate(entries, 1):
                    if scanned > _MAX_WORKSPACE_SCAN:
                        raise ObservationSecurityError("workspace directory exceeds scan limit")
                    if _secret_name(entry.name):
                        continue
                    try:
                        info = os.stat(entry.name, dir_fd=descriptor, follow_symlinks=False)
                    except OSError:
                        continue
                    if stat.S_ISDIR(info.st_mode):
                        items.append({"name": entry.name, "type": "directory", "size": 0})
                    elif stat.S_ISREG(info.st_mode) and not self._binary_entry(descriptor, entry.name):
                        items.append({"name": entry.name, "type": "file", "size": info.st_size})
            items.sort(key=lambda item: item["name"])
            return {
                "workspace_id": workspace_id,
                "path": str(path),
                "items": items[:_MAX_WORKSPACE_ITEMS],
                "truncated": len(items) > _MAX_WORKSPACE_ITEMS,
            }
        finally:
            os.close(descriptor)

    def read(
        self,
        workspace_id: object,
        path: object,
        *,
        start_line: int = 1,
        max_lines: int = _MAX_WORKSPACE_LINES,
    ) -> dict[str, Any]:
        if type(start_line) is not int or not 1 <= start_line <= _MAX_START_LINE:
            raise ValueError("start_line must be between 1 and 10000")
        if type(max_lines) is not int or not 1 <= max_lines <= _MAX_WORKSPACE_LINES:
            raise ValueError("max_lines must be between 1 and 200")
        descriptor = self._open(workspace_id, _relative_parts(path, allow_root=False), directory=False)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_WORKSPACE_FILE_BYTES:
                raise ObservationSecurityError("workspace file is unavailable or too large")
            with os.fdopen(os.dup(descriptor), "rb") as stream:
                sample = stream.read(8192)
                if _binary(sample):
                    raise ObservationSecurityError("workspace binary files are not readable")
                stream.seek(0)
                current = 1
                while current < start_line and stream.readline(_MAX_CONTEXT_BYTES + 1):
                    current += 1
                output = bytearray()
                lines = 0
                truncated = False
                while lines < max_lines:
                    raw = stream.readline(_MAX_CONTEXT_BYTES + 1)
                    if not raw:
                        break
                    if len(raw) > _MAX_CONTEXT_BYTES or _binary(raw) or len(output) + len(raw) > _MAX_CONTEXT_BYTES:
                        truncated = True
                        break
                    output.extend(raw)
                    current += 1
                    lines += 1
                if not truncated:
                    truncated = bool(stream.peek(1))
            return {
                "workspace_id": workspace_id,
                "path": str(path),
                "start_line": start_line,
                "content": output.decode("utf-8", "ignore"),
                "next_start_line": current if truncated else None,
            }
        finally:
            os.close(descriptor)

    def _open(self, workspace_id: object, parts: tuple[str, ...], *, directory: bool) -> int:
        try:
            root = self._registry.require(workspace_id)
        except WorkspaceRegistryError as exc:
            raise ObservationSecurityError("workspace is not registered") from exc
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
        directory_flag = getattr(os, "O_DIRECTORY", 0)
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        descriptor = -1
        try:
            descriptor = os.open(root, flags | directory_flag | no_follow)
            for index, part in enumerate(parts):
                child_flags = flags | no_follow
                if index < len(parts) - 1 or directory:
                    child_flags |= directory_flag
                child = os.open(part, child_flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            return descriptor
        except OSError as exc:
            if descriptor >= 0:
                os.close(descriptor)
            if exc.errno in {errno.ELOOP, errno.ENOTDIR, errno.ENOENT, errno.EACCES, errno.EPERM}:
                raise ObservationSecurityError("workspace path is unavailable or unsafe") from exc
            raise ObservationSecurityError("workspace path could not be opened safely") from exc

    @staticmethod
    def _binary_entry(directory: int, name: str) -> bool:
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
                dir_fd=directory,
            )
            try:
                return not stat.S_ISREG(os.fstat(descriptor).st_mode) or _binary(os.read(descriptor, 8192))
            finally:
                os.close(descriptor)
        except OSError:
            return True


class WorkspaceListExecutor:
    def __init__(self, reader: WorkspaceReader) -> None:
        self.reader = reader

    async def __call__(self, call: ToolCall) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.reader.list, call.arguments.get("workspace_id"), call.arguments.get("path", ""),
        )


class WorkspaceReadExecutor:
    def __init__(self, reader: WorkspaceReader) -> None:
        self.reader = reader

    async def __call__(self, call: ToolCall) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.reader.read,
            call.arguments.get("workspace_id"),
            call.arguments.get("path"),
            start_line=call.arguments.get("start_line", 1),
            max_lines=call.arguments.get("max_lines", _MAX_WORKSPACE_LINES),
        )


__all__ = [
    "ObservationExecutorError", "ObservationLedger", "ObservationSecurityError", "WebTools",
    "WorkspaceListExecutor", "WorkspaceReadExecutor", "WorkspaceReader",
]
