from __future__ import annotations

import hashlib
import os
import re
import stat as stat_module
from pathlib import Path

import pytest

from FileSystem import tools
from FileSystem.errors import ToolFailure


def test_list_directory_filters_only_dot_git(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".github").mkdir()
    (tmp_path / ".gitignore").write_text("", encoding="utf-8")
    (tmp_path / "plain.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "link").symlink_to(tmp_path / "plain.txt")

    result = tools.list_directory({"path": str(tmp_path)})

    assert [entry["name"] for entry in result["entries"]] == [
        ".github",
        ".gitignore",
        "link",
        "plain.txt",
    ]
    assert [entry["kind"] for entry in result["entries"]] == [
        "directory",
        "file",
        "symlink",
        "file",
    ]
    assert result["total_entries"] == 4
    assert result["truncated"] is False


def test_list_directory_truncates_after_filtered_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(tools, "DIRECTORY_LIMIT", 2)
    for name in ("c", "a", "b"):
        (tmp_path / name).write_text(name, encoding="utf-8")

    result = tools.list_directory({"path": str(tmp_path)})

    assert [entry["name"] for entry in result["entries"]] == ["a", "b"]
    assert result["total_entries"] == 3
    assert result["truncated"] is True


def test_read_file_preserves_lines_and_hash(tmp_path: Path):
    path = tmp_path / "unicode.txt"
    data = "第一行\nsecond\nthird\n".encode()
    path.write_bytes(data)

    result = tools.read_file({"path": str(path), "offset": 1, "limit": 1})

    assert result["content"] == "second\n"
    assert result["returned_lines"] == 1
    assert result["total_lines"] == 3
    assert result["size_bytes"] == len(data)
    assert result["sha256"] == hashlib.sha256(data).hexdigest()
    assert result["truncated"] is True


def test_read_file_empty_and_past_end(tmp_path: Path):
    path = tmp_path / "empty.txt"
    path.touch()
    result = tools.read_file({"path": str(path), "offset": 4})
    assert result["content"] == ""
    assert result["returned_lines"] == result["total_lines"] == 0
    assert result["truncated"] is False


@pytest.mark.parametrize("data", [b"\x00binary", b"\xff\xfe"])
def test_read_file_rejects_binary_and_invalid_utf8(tmp_path: Path, data: bytes):
    path = tmp_path / "bad.bin"
    path.write_bytes(data)
    with pytest.raises(ToolFailure):
        tools.read_file({"path": str(path)})


def test_stat_describes_symlink_itself(tmp_path: Path):
    target = tmp_path / "target.txt"
    target.write_text("data", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to("target.txt")

    result = tools.stat({"path": str(link)})

    assert result["path"] == str(link)
    assert result["kind"] == "symlink"
    assert result["size_bytes"] is None
    assert result["symlink_target"] == "target.txt"
    assert re.fullmatch(r".+T.+(?:Z|[+-]\d\d:\d\d)", result["modified_at"])


def test_glob_supports_double_star_hidden_and_no_symlink_recursion(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "tracked.py").write_text("", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "hidden.py").write_text("", encoding="utf-8")
    (tmp_path / "linked").symlink_to(outside, target_is_directory=True)

    result = tools.glob({"root": str(tmp_path), "pattern": "**/*.py"})
    relative = [
        Path(match["path"]).relative_to(tmp_path).as_posix()
        for match in result["matches"]
    ]

    assert ".git/tracked.py" in relative
    assert "src/main.py" in relative
    assert "linked/hidden.py" not in relative


@pytest.mark.parametrize("pattern", ["/etc/*", "../*.py", r"src\*.py"])
def test_glob_rejects_non_relative_patterns(tmp_path: Path, pattern: str):
    with pytest.raises(ToolFailure) as raised:
        tools.glob({"root": str(tmp_path), "pattern": pattern})
    assert raised.value.code == "invalid_glob"


def test_search_files_reports_unicode_columns_and_skips_binary(tmp_path: Path):
    (tmp_path / "code.py").write_text("甲乙 hello\nhello hello\n", encoding="utf-8")
    (tmp_path / "skip.bin").write_bytes(b"\x00hello")

    result = tools.search_files(
        {"root": str(tmp_path), "query": "hello", "glob": "**/*.py"}
    )

    assert [(item["line"], item["column"]) for item in result["matches"]] == [
        (1, 4),
        (2, 1),
        (2, 7),
    ]
    assert result["total_matches"] == 3
    assert result["glob"] == "**/*.py"


def test_search_files_invalid_regex(tmp_path: Path):
    with pytest.raises(ToolFailure) as raised:
        tools.search_files({"root": str(tmp_path), "query": "["})
    assert raised.value.code == "invalid_regex"


def test_create_file_is_exclusive_and_accepts_file_url(tmp_path: Path):
    path = tmp_path / "with space.txt"
    result = tools.create_file({"url": path.as_uri()})
    assert result["path"] == str(path)
    assert path.read_bytes() == b""
    assert result["sha256"] == hashlib.sha256(b"").hexdigest()
    with pytest.raises(ToolFailure) as raised:
        tools.create_file({"url": str(path)})
    assert raised.value.code == "already_exists"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/file.txt",
        "file://remote.example/file.txt",
        "file:///tmp/file.txt?x=1",
        "file:///tmp/file.txt#fragment",
    ],
)
def test_create_file_rejects_nonlocal_urls(url: str):
    with pytest.raises(ToolFailure):
        tools.create_file({"url": url})


def test_edit_file_initializes_empty_file_and_uses_hash(tmp_path: Path):
    path = tmp_path / "edit.txt"
    path.touch(mode=0o640)
    empty_hash = hashlib.sha256(b"").hexdigest()

    result = tools.edit_file(
        {
            "path": str(path),
            "expected_sha256": empty_hash,
            "edits": [{"old_text": "", "new_text": "hello hello\n"}],
        }
    )

    assert path.read_text(encoding="utf-8") == "hello hello\n"
    assert result["changed"] is True
    assert result["replacement_count"] == 1
    assert stat_module.S_IMODE(path.stat().st_mode) == 0o640


def test_edit_file_replace_all_and_transactional_failure(tmp_path: Path):
    path = tmp_path / "edit.txt"
    path.write_text("one one two", encoding="utf-8")

    result = tools.edit_file(
        {
            "path": str(path),
            "edits": [
                {"old_text": "one", "new_text": "1", "replace_all": True},
                {"old_text": "two", "new_text": "2"},
            ],
        }
    )
    assert path.read_text(encoding="utf-8") == "1 1 2"
    assert result["replacement_count"] == 3

    before = path.read_bytes()
    with pytest.raises(ToolFailure):
        tools.edit_file(
            {
                "path": str(path),
                "edits": [
                    {"old_text": "1", "new_text": "x"},
                    {"old_text": "missing", "new_text": "y"},
                ],
            }
        )
    assert path.read_bytes() == before


def test_edit_file_hash_conflict_and_symlink_rejection(tmp_path: Path):
    path = tmp_path / "edit.txt"
    path.write_text("hello", encoding="utf-8")
    with pytest.raises(ToolFailure) as raised:
        tools.edit_file(
            {
                "path": str(path),
                "expected_sha256": "0" * 64,
                "edits": [{"old_text": "hello", "new_text": "bye"}],
            }
        )
    assert raised.value.code == "hash_conflict"

    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(ToolFailure) as raised:
        tools.edit_file(
            {
                "path": str(link),
                "edits": [{"old_text": "hello", "new_text": "bye"}],
            }
        )
    assert raised.value.code == "not_file"


@pytest.mark.asyncio
async def test_bash_cwd_stdout_stderr_and_nonzero(tmp_path: Path):
    result = await tools.bash(
        {
            "PATH": str(tmp_path),
            "statement": "pwd; printf out; printf err >&2; exit 7",
        }
    )
    assert result["cwd"] == str(tmp_path.resolve())
    assert result["stdout"].endswith("\nout")
    assert result["stderr"] == "err"
    assert result["exit_code"] == 7
    assert result["timed_out"] is False


@pytest.mark.asyncio
async def test_bash_timeout_and_truncation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(tools, "BASH_TIMEOUT_SECONDS", 0.05)
    timed = await tools.bash({"PATH": str(tmp_path), "statement": "sleep 2"})
    assert timed["timed_out"] is True
    assert timed["exit_code"] is not None

    monkeypatch.setattr(tools, "BASH_STREAM_LIMIT_BYTES", 8)
    truncated = await tools.bash(
        {"PATH": str(tmp_path), "statement": "printf 123456789012"}
    )
    assert truncated["stdout"] == "12345678"
    assert truncated["truncated"] is True
