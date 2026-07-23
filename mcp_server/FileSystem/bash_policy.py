"""Best-effort checks for directly visible destructive Bash commands.

This module is deliberately not a security sandbox.  Harness authorization is the
security boundary; these checks merely prevent common accidental destructive calls.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from typing import Any

import bashlex

from .errors import ToolFailure


_ALWAYS_BLOCKED = {
    "fdisk",
    "sfdisk",
    "cfdisk",
    "parted",
    "shutdown",
    "reboot",
    "halt",
    "poweroff",
}
_PERMISSION_COMMANDS = {"chmod", "chown", "chgrp"}
_FORK_BOMB = re.compile(r":\(\)\{:\|:&\};:")


def validate_statement(statement: str) -> None:
    """Reject a small set of directly represented destructive commands."""

    compact = re.sub(r"\s+", "", statement)
    if _FORK_BOMB.search(compact):
        _blocked()

    try:
        trees = bashlex.parse(statement)
    except (ValueError, bashlex.errors.ParsingError) as exc:
        raise ToolFailure(
            "unsafe_statement",
            "Bash statement could not be inspected safely.",
        ) from exc

    for node in _walk_nodes(trees):
        if getattr(node, "kind", None) != "command":
            continue
        words = [
            part.word
            for part in getattr(node, "parts", ())
            if getattr(part, "kind", None) == "word"
        ]
        _validate_command(words)


def _walk_nodes(value: Any) -> Iterator[Any]:
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_nodes(item)
        return
    if not hasattr(value, "kind"):
        return

    yield value
    for attribute, child in vars(value).items():
        if attribute in {"kind", "pos", "word", "value"}:
            continue
        if isinstance(child, (list, tuple)) or hasattr(child, "kind"):
            yield from _walk_nodes(child)


def _validate_command(words: list[str]) -> None:
    if not words:
        return
    command = words[0]
    if not _is_literal(command):
        return
    name = os.path.basename(command)
    args = words[1:]

    if name == "rm" and _rm_is_recursive_and_forced(args):
        _blocked()
    if name == "mkfs" or name.startswith("mkfs."):
        _blocked()
    if name in _ALWAYS_BLOCKED:
        _blocked()
    if name == "dd" and _dd_writes_device(args):
        _blocked()
    if name == "kill" and _kill_all_with_sigkill(args):
        _blocked()
    if name in _PERMISSION_COMMANDS and _recursive_root_change(args):
        _blocked()


def _is_literal(word: str) -> bool:
    return not any(marker in word for marker in ("$", "`"))


def _rm_is_recursive_and_forced(args: list[str]) -> bool:
    recursive = False
    forced = False
    for argument in args:
        if argument == "--":
            break
        if argument == "--recursive":
            recursive = True
        elif argument == "--force":
            forced = True
        elif argument.startswith("-") and not argument.startswith("--"):
            flags = argument[1:]
            recursive = recursive or "r" in flags or "R" in flags
            forced = forced or "f" in flags
    return recursive and forced


def _dd_writes_device(args: list[str]) -> bool:
    for argument in args:
        if not argument.startswith("of="):
            continue
        target = argument[3:]
        if target.startswith("/dev/") and target != "/dev/null":
            return True
    return False


def _kill_all_with_sigkill(args: list[str]) -> bool:
    signal_is_kill = any(
        argument in {"-9", "-KILL", "-SIGKILL", "--signal=9", "--signal=KILL"}
        for argument in args
    )
    return signal_is_kill and "-1" in args


def _recursive_root_change(args: list[str]) -> bool:
    recursive = False
    operands: list[str] = []
    options_done = False
    for argument in args:
        if argument == "--":
            options_done = True
            continue
        if not options_done and argument.startswith("-"):
            if argument == "--recursive" or (
                not argument.startswith("--") and "R" in argument[1:]
            ):
                recursive = True
            continue
        operands.append(argument)
    return recursive and any(operand.rstrip("/") == "" for operand in operands)


def _blocked() -> None:
    raise ToolFailure(
        "blocked_command",
        "Bash statement contains a blocked destructive command.",
    )


__all__ = ["validate_statement"]
