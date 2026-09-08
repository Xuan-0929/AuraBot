"""Migrate legacy Profile-owned Memory storage without changing Context or extraction semantics.

The active Profile factory calls this only before opening Memory handles. SQLite's backup API includes WAL;
a Qdrant lock excludes a live vector owner during copying. An atomic directory rename commits the verified
copy with its marker. Originals remain preserved and are never used again after the marker exists.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from contextlib import ExitStack
from pathlib import Path


def prepare_memory_directory(profile: Path) -> Path:
    target = profile / "memory"
    marker = target / "legacy-migration.json"
    old_sqlite, old_vectors = profile / "memory.sqlite3", profile / "qdrant"
    for source in (old_sqlite, old_vectors, target, target / "memory.sqlite3", target / "qdrant", marker):
        if source.is_symlink():
            raise ValueError("memory_migration_symlink")
    if marker.is_file() or not (old_sqlite.exists() or old_vectors.exists()):
        target.mkdir(parents=True, exist_ok=True)
        return target
    if target.exists() and any(target.iterdir()):
        raise RuntimeError("memory_migration_ambiguous_existing_data")
    with ExitStack() as stack:
        if old_vectors.exists():
            lock_path = old_vectors / ".lock"
            if lock_path.exists():
                if lock_path.is_symlink():
                    raise ValueError("memory_migration_symlink")
                import portalocker  # Same portable lock implementation and mode as qdrant-client.
                lock = stack.enter_context(lock_path.open("r+"))
                try:
                    portalocker.lock(lock, portalocker.LockFlags.EXCLUSIVE | portalocker.LockFlags.NON_BLOCKING)
                    stack.callback(portalocker.unlock, lock)
                except portalocker.exceptions.LockException:
                    raise RuntimeError("memory_vector_store_in_use") from None
            if any(path.is_symlink() for path in old_vectors.rglob("*")):
                raise ValueError("memory_migration_symlink")
        staging = Path(tempfile.mkdtemp(prefix=".memory-migration-", dir=profile))
        try:
            if old_sqlite.exists():
                with sqlite3.connect(old_sqlite.as_uri() + "?mode=ro", uri=True) as source, sqlite3.connect(staging / "memory.sqlite3") as destination:
                    source.backup(destination)
                    if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise RuntimeError("memory_migration_verification_failed")
            if old_vectors.exists():
                shutil.copytree(old_vectors, staging / "qdrant")
            (staging / "legacy-migration.json").write_text(json.dumps({"version": 1,
                "sqlite": old_sqlite.exists(), "qdrant": old_vectors.exists(), "originals_preserved": True}), encoding="utf-8")
            if target.exists():
                target.rmdir()  # Only the previously verified empty directory may be removed.
            staging.rename(target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)  # This transaction's disposable copy, never user originals.
    return target
