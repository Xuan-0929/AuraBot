"""Implement AuraBot's native FileSystem tools."""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import os
import re
import shutil
import signal
import stat as stat_module
import tempfile
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .bash_policy import validate_statement
from .errors import ToolFailure


DIRECTORY_LIMIT = 2000
READ_LINE_LIMIT = 2000
GLOB_LIMIT = 2000
SEARCH_LIMIT = 1000
BASH_TIMEOUT_SECONDS = 20.0
BASH_STREAM_LIMIT_BYTES = 1024 * 1024
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def normalize_path(value: str) -> Path:
    """Make a path absolute without expanding shell syntax or following links."""

    if not isinstance(value, str) or not value:
        raise ToolFailure("invalid_path", "Path must be a non-empty string.")
    try:
        absolute = Path(os.path.abspath(value))
        if absolute == absolute.parent:
            return absolute
        # Resolve intermediate links (for example macOS /var -> /private/var)
        # while preserving a final symlink so stat can describe the link itself.
        return Path(os.path.realpath(absolute.parent)) / absolute.name
    except (OSError, ValueError) as exc:
        raise ToolFailure("invalid_path", "Path could not be normalized.") from exc


def object_kind(mode: int) -> str:
    if stat_module.S_ISREG(mode):
        return "file"
    if stat_module.S_ISDIR(mode):
        return "directory"
    if stat_module.S_ISLNK(mode):
        return "symlink"
    return "other"


def _lstat(path: Path) -> os.stat_result:
    try:
        return path.lstat()
    except FileNotFoundError as exc:
        raise ToolFailure("not_found", "File system object does not exist.") from exc
    except OSError as exc:
        raise ToolFailure("access_failed", "File system object could not be accessed.") from exc


def _require_directory(path: Path) -> os.stat_result:
    info = _lstat(path)
    if not stat_module.S_ISDIR(info.st_mode):
        raise ToolFailure("not_directory", "Path is not a directory.")
    return info


def _require_regular_file(path: Path) -> os.stat_result:
    info = _lstat(path)
    if not stat_module.S_ISREG(info.st_mode):
        raise ToolFailure("not_file", "Path is not a regular file.")
    return info


def _read_text_bytes(path: Path) -> tuple[bytes, str]:
    _require_regular_file(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ToolFailure("read_failed", "File could not be read.") from exc
    if _looks_binary(data):
        raise ToolFailure("binary_file", "File is binary and cannot be read as text.")
    try:
        return data, data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ToolFailure("invalid_utf8", "File is not valid UTF-8 text.") from exc


def _looks_binary(data: bytes) -> bool:
    if b"\x00" in data:
        return True
    if not data:
        return False
    sample = data[:8192]
    suspicious = sum(
        byte < 32 and byte not in {8, 9, 10, 12, 13}
        for byte in sample
    )
    return suspicious / len(sample) > 0.30


def list_directory(arguments: dict[str, Any]) -> dict[str, Any]:
    path = normalize_path(arguments["path"])
    _require_directory(path)
    try:
        children = [
            child
            for child in path.iterdir()
            if child.name != ".git"
        ]
    except OSError as exc:
        raise ToolFailure("list_failed", "Directory could not be listed.") from exc

    entries: list[dict[str, Any]] = []
    for child in sorted(children, key=lambda item: str(item)):
        try:
            info = child.lstat()
        except OSError as exc:
            raise ToolFailure("stat_failed", "Directory entry could not be inspected.") from exc
        kind = object_kind(info.st_mode)
        entries.append(
            {
                "name": child.name,
                "path": str(normalize_path(str(child))),
                "kind": kind,
                "size_bytes": info.st_size if kind == "file" else None,
            }
        )

    total = len(entries)
    return {
        "path": str(path),
        "entries": entries[:DIRECTORY_LIMIT],
        "total_entries": total,
        "truncated": total > DIRECTORY_LIMIT,
    }


def read_file(arguments: dict[str, Any]) -> dict[str, Any]:
    path = normalize_path(arguments["path"])
    offset = arguments.get("offset", 0)
    limit = arguments.get("limit", READ_LINE_LIMIT)
    data, text = _read_text_bytes(path)
    lines = text.splitlines(keepends=True)
    selected = lines[offset : offset + limit]
    return {
        "path": str(path),
        "content": "".join(selected),
        "offset": offset,
        "limit": limit,
        "returned_lines": len(selected),
        "total_lines": len(lines),
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "truncated": offset + len(selected) < len(lines),
    }


def stat(arguments: dict[str, Any]) -> dict[str, Any]:
    path = normalize_path(arguments["path"])
    info = _lstat(path)
    kind = object_kind(info.st_mode)
    try:
        readonly = not os.access(path, os.W_OK, follow_symlinks=False)
    except (NotImplementedError, TypeError):
        readonly = not os.access(path, os.W_OK)

    try:
        target = os.readlink(path) if kind == "symlink" else None
    except OSError as exc:
        raise ToolFailure("readlink_failed", "Symbolic link target could not be read.") from exc

    return {
        "path": str(path),
        "name": path.name,
        "kind": kind,
        "size_bytes": info.st_size if kind == "file" else None,
        "modified_at": datetime.fromtimestamp(
            info.st_mtime,
            tz=timezone.utc,
        ).isoformat().replace("+00:00", "Z"),
        "readonly": readonly,
        "symlink_target": target,
    }


def validate_glob_pattern(pattern: str) -> tuple[str, ...]:
    if pattern.startswith("/") or "\\" in pattern:
        raise ToolFailure("invalid_glob", "Glob pattern must be relative and use '/'.")
    parts = tuple(pattern.split("/"))
    if any(part == ".." for part in parts):
        raise ToolFailure("invalid_glob", "Glob pattern must not contain '..'.")
    return parts


def _glob_matches(relative_path: str, pattern: str) -> bool:
    pattern_parts = validate_glob_pattern(pattern)
    path_parts = tuple(relative_path.split("/"))

    def match(path_index: int, pattern_index: int) -> bool:
        if pattern_index == len(pattern_parts):
            return path_index == len(path_parts)
        token = pattern_parts[pattern_index]
        if token == "**":
            return match(path_index, pattern_index + 1) or (
                path_index < len(path_parts)
                and match(path_index + 1, pattern_index)
            )
        return (
            path_index < len(path_parts)
            and fnmatch.fnmatchcase(path_parts[path_index], token)
            and match(path_index + 1, pattern_index + 1)
        )

    return match(0, 0)


def _walk(root: Path) -> Iterator[tuple[Path, str, str]]:
    """Yield every descendant as (absolute path, relative posix path, kind)."""

    try:
        with os.scandir(root) as scanner:
            entries = sorted(scanner, key=lambda entry: entry.name)
    except OSError as exc:
        raise ToolFailure("list_failed", "Directory could not be traversed.") from exc

    for entry in entries:
        path = Path(entry.path)
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError as exc:
            raise ToolFailure("stat_failed", "Directory entry could not be inspected.") from exc
        kind = object_kind(info.st_mode)
        relative = path.relative_to(root).as_posix()
        yield path, relative, kind
        if kind == "directory":
            for child_path, child_relative, child_kind in _walk(path):
                yield (
                    child_path,
                    f"{relative}/{child_relative}",
                    child_kind,
                )


def glob(arguments: dict[str, Any]) -> dict[str, Any]:
    root = normalize_path(arguments["root"])
    pattern = arguments["pattern"]
    _require_directory(root)
    validate_glob_pattern(pattern)
    matches = [
        {"path": str(normalize_path(str(path))), "kind": kind}
        for path, relative, kind in _walk(root)
        if _glob_matches(relative, pattern)
    ]
    matches.sort(key=lambda item: item["path"])
    total = len(matches)
    return {
        "root": str(root),
        "pattern": pattern,
        "matches": matches[:GLOB_LIMIT],
        "total_matches": total,
        "truncated": total > GLOB_LIMIT,
    }


def search_files(arguments: dict[str, Any]) -> dict[str, Any]:
    root = normalize_path(arguments["root"])
    query = arguments["query"]
    glob_pattern = arguments.get("glob")
    _require_directory(root)
    if glob_pattern is not None:
        validate_glob_pattern(glob_pattern)
    try:
        expression = re.compile(query)
    except re.error as exc:
        raise ToolFailure("invalid_regex", "Search query is not a valid regular expression.") from exc

    results: list[dict[str, Any]] = []
    total = 0
    for path, relative, kind in _walk(root):
        if kind != "file":
            continue
        if glob_pattern is not None and not _glob_matches(relative, glob_pattern):
            continue
        try:
            data = path.read_bytes()
            if _looks_binary(data):
                continue
            text = data.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for found in expression.finditer(line):
                total += 1
                if len(results) < SEARCH_LIMIT:
                    results.append(
                        {
                            "path": str(normalize_path(str(path))),
                            "line": line_number,
                            "column": found.start() + 1,
                            "match": found.group(0),
                            "text": line,
                        }
                    )
    results.sort(key=lambda item: (item["path"], item["line"], item["column"]))
    return {
        "root": str(root),
        "query": query,
        "glob": glob_pattern,
        "matches": results,
        "total_matches": total,
        "truncated": total > SEARCH_LIMIT,
    }


def _create_path(value: str) -> Path:
    parsed = urlsplit(value)
    if not parsed.scheme:
        return normalize_path(value)
    if parsed.scheme.casefold() != "file":
        raise ToolFailure("invalid_url", "Only local file URLs are supported.")
    if parsed.netloc.casefold() not in {"", "localhost"}:
        raise ToolFailure("invalid_url", "File URL must refer to the local host.")
    if parsed.query or parsed.fragment:
        raise ToolFailure("invalid_url", "File URL must not contain query or fragment.")
    if not parsed.path.startswith("/"):
        raise ToolFailure("invalid_url", "File URL path must be absolute.")
    return normalize_path(unquote(parsed.path))


def create_file(arguments: dict[str, Any]) -> dict[str, Any]:
    path = _create_path(arguments["url"])
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
    except FileExistsError as exc:
        raise ToolFailure("already_exists", "Target already exists.") from exc
    except FileNotFoundError as exc:
        raise ToolFailure("parent_not_found", "Parent directory does not exist.") from exc
    except OSError as exc:
        raise ToolFailure("create_failed", "File could not be created.") from exc
    else:
        os.close(descriptor)
    return {
        "path": str(path),
        "created": True,
        "size_bytes": 0,
        "sha256": EMPTY_SHA256,
    }


def edit_file(arguments: dict[str, Any]) -> dict[str, Any]:
    path = normalize_path(arguments["path"])
    info = _require_regular_file(path)
    data, text = _read_text_bytes(path)
    previous_hash = hashlib.sha256(data).hexdigest()
    expected_hash = arguments.get("expected_sha256")
    if expected_hash is not None and expected_hash != previous_hash:
        raise ToolFailure("hash_conflict", "File changed since it was last read.")

    edits = arguments["edits"]
    current = text
    replacement_count = 0
    for index, edit in enumerate(edits):
        old_text = edit["old_text"]
        new_text = edit["new_text"]
        replace_all = edit.get("replace_all", False)
        if old_text == "":
            if len(edits) != 1 or index != 0 or current != "":
                raise ToolFailure(
                    "invalid_empty_edit",
                    "Empty old_text is only valid for one edit on an empty file.",
                )
            current = new_text
            replacement_count += 1
            continue

        occurrences = current.count(old_text)
        if replace_all:
            if occurrences == 0:
                raise ToolFailure("text_not_found", "Edit text was not found.")
            current = current.replace(old_text, new_text)
            replacement_count += occurrences
        else:
            if occurrences != 1:
                raise ToolFailure(
                    "ambiguous_edit",
                    "Edit text must occur exactly once.",
                )
            current = current.replace(old_text, new_text, 1)
            replacement_count += 1

    changed = current != text
    new_data = current.encode("utf-8")
    new_hash = hashlib.sha256(new_data).hexdigest()
    if changed:
        _atomic_write(path, new_data, stat_module.S_IMODE(info.st_mode))
    return {
        "path": str(path),
        "changed": changed,
        "edit_count": len(edits),
        "replacement_count": replacement_count,
        "previous_sha256": previous_hash,
        "sha256": new_hash,
        "size_bytes": len(new_data),
    }


def _atomic_write(path: Path, data: bytes, mode: int) -> None:
    descriptor: int | None = None
    temporary_name: str | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
        os.replace(temporary_name, path)
        temporary_name = None
    except OSError as exc:
        raise ToolFailure("write_failed", "File could not be replaced atomically.") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


async def bash(arguments: dict[str, Any]) -> dict[str, Any]:
    statement = arguments["statement"]
    cwd = normalize_path(arguments["PATH"])
    _require_directory(cwd)
    validate_statement(statement)
    bash_path = shutil.which("bash")
    if bash_path is None:
        raise ToolFailure("bash_unavailable", "Bash executable is unavailable.")

    started = time.monotonic()
    try:
        process = await asyncio.create_subprocess_exec(
            bash_path,
            "--noprofile",
            "--norc",
            "-c",
            statement,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        raise ToolFailure("bash_start_failed", "Bash process could not be started.") from exc

    assert process.stdout is not None
    assert process.stderr is not None
    stdout_task = asyncio.create_task(_read_bounded(process.stdout))
    stderr_task = asyncio.create_task(_read_bounded(process.stderr))
    timed_out = False
    try:
        await asyncio.wait_for(process.wait(), timeout=BASH_TIMEOUT_SECONDS)
    except TimeoutError:
        timed_out = True
        await _terminate_process_group(process)

    stdout_data, stdout_truncated = await stdout_task
    stderr_data, stderr_truncated = await stderr_task
    duration_ms = max(0, round((time.monotonic() - started) * 1000))
    return {
        "cwd": str(cwd),
        "stdout": stdout_data.decode("utf-8", errors="replace"),
        "stderr": stderr_data.decode("utf-8", errors="replace"),
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "truncated": stdout_truncated or stderr_truncated,
        "duration_ms": duration_ms,
    }


async def _read_bounded(
    stream: asyncio.StreamReader,
) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    retained = 0
    truncated = False
    while True:
        chunk = await stream.read(64 * 1024)
        if not chunk:
            break
        remaining = BASH_STREAM_LIMIT_BYTES - retained
        if remaining > 0:
            kept = chunk[:remaining]
            chunks.append(kept)
            retained += len(kept)
        if len(chunk) > max(remaining, 0):
            truncated = True
    return b"".join(chunks), truncated


async def _terminate_process_group(process: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=0.5)
    except TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()


TOOL_HANDLERS = {
    "list_directory": list_directory,
    "read_file": read_file,
    "stat": stat,
    "glob": glob,
    "search_files": search_files,
    "create_file": create_file,
    "edit_file": edit_file,
    "bash": bash,
}

__all__ = [
    "BASH_STREAM_LIMIT_BYTES",
    "BASH_TIMEOUT_SECONDS",
    "TOOL_HANDLERS",
    "bash",
    "create_file",
    "edit_file",
    "glob",
    "list_directory",
    "normalize_path",
    "read_file",
    "search_files",
    "stat",
]
