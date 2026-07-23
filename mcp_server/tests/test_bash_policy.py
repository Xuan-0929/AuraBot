from __future__ import annotations

import pytest

from FileSystem.bash_policy import validate_statement
from FileSystem.errors import ToolFailure


@pytest.mark.parametrize(
    "statement",
    [
        "rm -rf target",
        "rm -fr target",
        "rm -R -f target",
        "rm --recursive --force target",
        "/sbin/mkfs.ext4 /dev/disk9",
        "fdisk /dev/disk9",
        "dd if=/dev/zero of=/dev/disk9",
        "shutdown -h now",
        ":(){ :|:& };:",
        "kill -KILL -1",
        "chmod -R /",
    ],
)
def test_blocks_direct_destructive_commands(statement: str):
    with pytest.raises(ToolFailure) as raised:
        validate_statement(statement)
    assert raised.value.code == "blocked_command"


@pytest.mark.parametrize(
    "statement",
    [
        "rm target",
        "rm -r target",
        "echo 'rm -rf /'",
        "dd if=/dev/zero of=/dev/null count=1",
        "chmod -R ./build",
        "printf ok | sed s/o/a/",
    ],
)
def test_allows_nonblocked_commands(statement: str):
    validate_statement(statement)


def test_rejects_unparseable_statement():
    with pytest.raises(ToolFailure) as raised:
        validate_statement("if then")
    assert raised.value.code == "unsafe_statement"
