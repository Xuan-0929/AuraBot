"""管理受信任 Profile 目录及 SOUL 的乐观并发读写。

本模块是 Main Agent 人格文件的唯一文件系统边界：它创建并验证 profile.yaml、SOUL.md、
main-agent.sqlite3、memory.sqlite3 和 qdrant/，以 SHA-256 ETag 防止覆盖外部编辑，并以原子
replace 保持 SOUL 完整。ContextStore 和 Memory 后续模块只消费这里返回的已验证目录；无效
外部 SOUL 会回退到进程内最后已知有效版本，避免一次手工坏编辑中断实时对话。
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from uuid import uuid4

import yaml

_PROFILE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_MAX_SOUL_BYTES = 32 * 1024
_PROFILE_CHILDREN = frozenset({"profile.yaml", "SOUL.md", "main-agent.sqlite3", "memory.sqlite3", "qdrant"})


class ProfileError(RuntimeError):
    """Base error for profile filesystem operations."""


class ProfileValidationError(ProfileError, ValueError):
    """Raised when a profile identifier, layout, YAML, or SOUL is unsafe."""


class ProfileConflictError(ProfileError):
    """Raised when creation or a SOUL ETag compare-and-swap conflicts."""


@dataclass(frozen=True, slots=True)
class SoulDocument:
    """Validated SOUL text with a byte-content ETag for conditional replacement."""

    text: str
    etag: str


@dataclass(frozen=True, slots=True)
class Profile:
    """One verified profile directory and its current last-known-good Soul."""

    profile_id: str
    path: Path
    soul: SoulDocument


class ProfileStore:
    """Own profile directories without accepting caller-controlled paths or partial writes."""

    def __init__(self, root: str | Path) -> None:
        self.root = _absolute_unresolved(root)
        _reject_symlink_components(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        _reject_symlink_components(self.root)
        if not self.root.is_dir() or self.root.is_symlink():
            raise ProfileValidationError("profile root must be a real directory")
        root_stat = self.root.lstat()
        self._root_identity = (root_stat.st_dev, root_stat.st_ino)
        self._lock = RLock()
        self._last_known_good: dict[str, SoulDocument] = {}

    def create(self, profile_id: str, *, soul: str = "") -> Profile:
        """Create all owned profile artifacts together, refusing duplicate identifiers."""

        identifier = self._validate_id(profile_id)
        document = self._validate_soul(soul)
        destination = self._profile_path(identifier)
        with self._lock:
            self._require_root()
            if _lexists(destination):
                raise ProfileConflictError(f"profile already exists: {identifier}")
            temporary = Path(tempfile.mkdtemp(prefix=f".{identifier}-", dir=self.root))
            try:
                self._write_layout(temporary, identifier, document.text)
                try:
                    os.replace(temporary, destination)
                except FileExistsError as exc:
                    raise ProfileConflictError(f"profile already exists: {identifier}") from exc
            except BaseException:
                if temporary.exists():
                    shutil.rmtree(temporary)
                raise
            profile = Profile(identifier, destination, document)
            self._last_known_good[identifier] = document
            _fsync_dir(self.root)
            return profile

    def read(self, profile_id: str) -> Profile:
        """Read a verified profile and refresh its cached Soul only after validation."""

        identifier = self._validate_id(profile_id)
        with self._lock:
            self._require_root()
            path = self._require_layout(identifier)
            document = self._read_soul_last_known_good(identifier, path / "SOUL.md")
            return Profile(identifier, path, document)

    def update_soul(self, profile_id: str, soul: str, if_match: str) -> SoulDocument:
        """Atomically replace SOUL only when the caller ETag matches current valid bytes."""

        identifier = self._validate_id(profile_id)
        replacement = self._validate_soul(soul)
        if not isinstance(if_match, str) or not if_match:
            raise ProfileConflictError("SOUL update requires an ETag")
        with self._lock:
            self._require_root()
            path = self._require_layout(identifier)
            try:
                current = self._validate_soul_bytes((path / "SOUL.md").read_bytes())
            except (OSError, ProfileValidationError) as exc:
                raise ProfileConflictError("current SOUL is invalid; refusing to overwrite external edit") from exc
            if current.etag != if_match:
                raise ProfileConflictError("SOUL was changed by another writer")
            self._atomic_write(path / "SOUL.md", replacement.text.encode("utf-8"))
            self._last_known_good[identifier] = replacement
            return replacement

    def archive(self, profile_id: str) -> Path:
        """Move one complete profile to an in-root archive that restore can recover."""

        identifier = self._validate_id(profile_id)
        with self._lock:
            self._require_root()
            source = self._require_layout(identifier)
            archive_root = self._require_archive_root()
            destination = archive_root / f"{identifier}-{uuid4().hex}"
            manifest = self._archive_manifest(destination)
            self._atomic_write(
                manifest,
                yaml.safe_dump(
                    {"archive_name": destination.name, "profile_id": identifier}, sort_keys=True
                ).encode("utf-8"),
            )
            try:
                os.replace(source, destination)
            except BaseException:
                manifest.unlink(missing_ok=True)
                raise
            self._last_known_good.pop(identifier, None)
            _fsync_dir(archive_root)
            _fsync_dir(self.root)
            return destination

    def restore(self, archive_path: str | Path) -> Profile:
        """Restore an archive made by archive() if its original identifier remains free."""

        with self._lock:
            self._require_root()
            archive_root = self._require_archive_root()
            candidate = _absolute_unresolved(archive_path)
            _reject_symlink_components(candidate)
            if candidate.parent != archive_root or not candidate.is_dir() or candidate.is_symlink():
                raise ProfileValidationError("archive path is not a managed profile archive")
            manifest = self._archive_manifest(candidate)
            profile_id = self._archive_profile_id(manifest, candidate)
            destination = self._profile_path(profile_id)
            if _lexists(destination):
                raise ProfileConflictError(f"profile already exists: {profile_id}")
            self._require_layout_at(candidate, profile_id)
            soul = self._validate_soul_bytes((candidate / "SOUL.md").read_bytes())
            os.replace(candidate, destination)
            manifest.unlink()
            _fsync_dir(archive_root)
            _fsync_dir(self.root)
            self._last_known_good[profile_id] = soul
            return Profile(profile_id, destination, soul)

    def _write_layout(self, path: Path, profile_id: str, soul: str) -> None:
        """Populate the fixed profile ownership layout inside a private temporary directory."""

        self._atomic_write(
            path / "profile.yaml",
            yaml.safe_dump({"id": profile_id}, sort_keys=True, allow_unicode=True).encode("utf-8"),
        )
        self._atomic_write(path / "SOUL.md", soul.encode("utf-8"))
        for filename in ("main-agent.sqlite3", "memory.sqlite3"):
            file_path = path / filename
            descriptor = os.open(file_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        (path / "qdrant").mkdir(mode=0o700)
        _fsync_dir(path)

    def _require_layout(self, profile_id: str) -> Path:
        return self._require_layout_at(self._profile_path(profile_id), profile_id)

    def _profile_path(self, profile_id: str) -> Path:
        """Build the sole direct child path after identifier validation rules have run."""

        return self.root / profile_id

    def _require_root(self) -> None:
        """Revalidate root components and inode identity before every mutation boundary."""

        _reject_symlink_components(self.root)
        try:
            root_stat = self.root.lstat()
        except OSError as exc:
            raise ProfileValidationError("profile root is unavailable") from exc
        if not stat.S_ISDIR(root_stat.st_mode) or (root_stat.st_dev, root_stat.st_ino) != self._root_identity:
            raise ProfileValidationError("profile root identity changed")

    def _require_archive_root(self) -> Path:
        """Return the owned real archive child without ever following a substituted symlink."""

        archive_root = self.root / ".archive"
        if _lexists(archive_root):
            if archive_root.is_symlink() or not archive_root.is_dir():
                raise ProfileValidationError("profile archive must be a real directory")
        else:
            archive_root.mkdir(mode=0o700)
        _reject_symlink_components(archive_root)
        _fsync_dir(self.root)
        return archive_root

    @staticmethod
    def _archive_manifest(candidate: Path) -> Path:
        return candidate.parent / f".{candidate.name}.archive.yaml"

    def _archive_profile_id(self, manifest: Path, candidate: Path) -> str:
        """Authenticate a managed archive sidecar before its content can be restored."""

        if not manifest.is_file() or manifest.is_symlink():
            raise ProfileValidationError("archive is not owned by ProfileStore")
        try:
            loaded = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise ProfileValidationError(f"invalid archive manifest: {exc}") from exc
        if not isinstance(loaded, dict) or set(loaded) != {"archive_name", "profile_id"}:
            raise ProfileValidationError("archive manifest is invalid")
        if loaded["archive_name"] != candidate.name:
            raise ProfileValidationError("archive manifest does not name candidate")
        return self._validate_id(loaded["profile_id"])

    def _require_layout_at(self, path: Path, profile_id: str) -> Path:
        """Validate that every required artifact is owned regular storage, never a link."""

        if not path.is_dir() or path.is_symlink():
            raise ProfileValidationError(f"profile does not exist: {profile_id}")
        if {item.name for item in path.iterdir()} != _PROFILE_CHILDREN:
            raise ProfileValidationError("profile contains unexpected or missing owned artifacts")
        if self._profile_id_from_yaml(path) != profile_id:
            raise ProfileValidationError("profile.yaml id does not match directory")
        for filename in ("profile.yaml", "SOUL.md", "main-agent.sqlite3", "memory.sqlite3"):
            item = path / filename
            if not item.is_file() or item.is_symlink():
                raise ProfileValidationError(f"profile owns no safe {filename}")
        qdrant = path / "qdrant"
        if not qdrant.is_dir() or qdrant.is_symlink():
            raise ProfileValidationError("profile owns no safe qdrant directory")
        return path

    def _profile_id_from_yaml(self, path: Path) -> str:
        try:
            loaded = yaml.safe_load((path / "profile.yaml").read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise ProfileValidationError(f"invalid profile.yaml: {exc}") from exc
        if not isinstance(loaded, dict) or set(loaded) != {"id"}:
            raise ProfileValidationError("profile.yaml must contain only id")
        return self._validate_id(loaded["id"])

    def _read_soul_last_known_good(self, profile_id: str, path: Path) -> SoulDocument:
        try:
            document = self._validate_soul_bytes(path.read_bytes())
        except (OSError, ProfileValidationError):
            cached = self._last_known_good.get(profile_id)
            if cached is not None:
                return cached
            raise
        self._last_known_good[profile_id] = document
        return document

    @staticmethod
    def _validate_id(profile_id: object) -> str:
        if not isinstance(profile_id, str) or not _PROFILE_ID.fullmatch(profile_id):
            raise ProfileValidationError("profile id is invalid")
        return profile_id

    @staticmethod
    def _validate_soul(soul: object) -> SoulDocument:
        if not isinstance(soul, str):
            raise ProfileValidationError("SOUL must be text")
        return ProfileStore._validate_soul_bytes(soul.encode("utf-8"))

    @staticmethod
    def _validate_soul_bytes(content: bytes) -> SoulDocument:
        if len(content) > _MAX_SOUL_BYTES:
            raise ProfileValidationError("SOUL exceeds 32 KiB UTF-8 limit")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProfileValidationError("SOUL must be valid UTF-8") from exc
        return SoulDocument(text=text, etag=hashlib.sha256(content).hexdigest())

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        """Commit one file by fsyncing a same-directory temporary file then replacing it."""

        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
            _fsync_dir(path.parent)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise


def _absolute_unresolved(path: str | Path) -> Path:
    """Make a lexical absolute path without resolving (and thereby hiding) symlinks."""

    return Path(os.path.abspath(Path(path).expanduser()))


def _reject_symlink_components(path: Path) -> None:
    """Inspect every existing lexical component with lstat before it is trusted for mutation."""

    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ProfileValidationError(f"profile path cannot be inspected: {exc}") from exc
        if stat.S_ISLNK(metadata.st_mode):
            # macOS exposes its system temp hierarchy through /var -> /private/var.
            if current == Path("/var") and Path("/private/var").is_dir():
                continue
            raise ProfileValidationError("profile paths must not contain symlinks")


def _lexists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _fsync_dir(path: Path) -> None:
    """Durably publish a completed directory entry after a replace, archive, or restore rename."""

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "Profile",
    "ProfileConflictError",
    "ProfileError",
    "ProfileStore",
    "ProfileValidationError",
    "SoulDocument",
]
