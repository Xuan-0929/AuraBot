"""Verify one-time legacy JSON import, atomic rejection, restart behavior, and sidecar safety.

The suite treats migration as a public compatibility boundary and table-drives malformed inputs instead of
duplicating the same sidecar reset and zero-state assertions for every JSON field variation.
"""

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from threading import Barrier, Lock, Thread
import unittest

from backend.memory.short_term.buffer import ShortTermMemoryBuffer
from backend.memory.stores.short_term_sqlite import SQLiteShortTermStore


class LegacyShortTermJsonMigrationTests(unittest.TestCase):
    PROFILE = "profile-1"
    SOURCE = "chat"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.legacy = Path(self.temp.name) / "short-term.json"
        self.sidecar = Path(f"{self.legacy}.sqlite3")
        self.resources = []

    def tearDown(self) -> None:
        for resource in self.resources:
            resource.close()
        self.temp.cleanup()

    def _snapshot(self, **changes):
        snapshot = {
            "profile_id": self.PROFILE,
            "source_type": self.SOURCE,
            "messages": [{
                "role": "user", "content": "first", "source_id": "legacy-1",
                "created_at": "2026-07-24T10:00:00+08:00",
            }],
            "breakpoints": [{
                "id": "breakpoint-1", "message_index": 1, "topic": "legacy",
                "reason": "changed", "created_at": "2026-07-24T10:00:01+08:00",
            }],
        }
        snapshot.update(changes)
        return snapshot

    def _write(self, value) -> bytes:
        self.legacy.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return self.legacy.read_bytes()

    def _buffer(self):
        buffer = ShortTermMemoryBuffer(
            profile_id=self.PROFILE, source_type=self.SOURCE, persistence_path=self.legacy,
        )
        self.resources.append(buffer)
        return buffer

    def _counts(self):
        if not self.sidecar.exists(): return (0, 0, 0)
        with closing(sqlite3.connect(self.sidecar)) as connection:
            return tuple(connection.execute(f"select count(*) from {table}").fetchone()[0] for table in (
                "short_term_messages", "short_term_breakpoints", "short_term_segments",
            ))

    def _assert_invalid(self, raw: bytes) -> None:
        if self.sidecar.exists(): self.sidecar.unlink()
        self.legacy.write_bytes(raw)
        original = self.legacy.read_bytes()
        with self.assertRaises(ValueError): self._buffer()
        self.assertEqual(self.legacy.read_bytes(), original)
        self.assertEqual(self._counts(), (0, 0, 0))

    def test_import_preserves_json_and_is_restart_safe(self) -> None:
        original = self._write(self._snapshot())
        first = self._buffer()
        self.assertEqual([message.content for message in first.messages], ["first"])
        self.assertEqual(first.breakpoints[0].message_sequence, first.messages[0].sequence)
        first.close()
        reopened = self._buffer()
        self.assertEqual([message.content for message in reopened.messages], ["first"])
        self.assertEqual(self._counts(), (1, 1, 0))
        self.assertEqual(self.legacy.read_bytes(), original)

    def test_existing_sqlite_state_wins_and_prevents_resurrection(self) -> None:
        store = SQLiteShortTermStore(self.sidecar)
        store.append_message(
            profile_id=self.PROFILE, source_type=self.SOURCE, role="user",
            content="SQLite truth", source_id=None,
        )
        store.close()
        self._write(self._snapshot())
        first = self._buffer()
        self.assertEqual([message.content for message in first.messages], ["SQLite truth"])
        first.close()
        with closing(sqlite3.connect(self.sidecar)) as connection, connection:
            connection.execute(
                "delete from short_term_messages where profile_id = ? and source_type = ?",
                (self.PROFILE, self.SOURCE),
            )
        self.assertEqual(self._buffer().messages, [])

    def test_invalid_snapshots_fail_atomically(self) -> None:
        valid = self._snapshot()
        cases = {
            "malformed": b"{",
            "wrong root": b"[]",
            "wrong profile": json.dumps(valid | {"profile_id": "other"}).encode(),
            "bad message": json.dumps(valid | {"messages": ["invalid"]}).encode(),
            "bad breakpoint": json.dumps(valid | {"breakpoints": [{
                **valid["breakpoints"][0], "message_index": 2,
            }]}).encode(),
            "unknown key": json.dumps(valid | {"unknown": True}).encode(),
            "duplicate key": (
                b'{"profile_id":"profile-1","profile_id":"profile-1",'
                b'"source_type":"chat","messages":[],"breakpoints":[]}'
            ),
        }
        for label, raw in cases.items():
            with self.subTest(label=label): self._assert_invalid(raw)

    def test_concurrent_constructors_import_once(self) -> None:
        self._write(self._snapshot())
        barrier, lock = Barrier(2), Lock()
        buffers, errors = [], []

        def construct() -> None:
            try:
                barrier.wait()
                buffer = ShortTermMemoryBuffer(
                    profile_id=self.PROFILE, source_type=self.SOURCE, persistence_path=self.legacy,
                )
                with lock: buffers.append(buffer)
            except BaseException as error:
                with lock: errors.append(error)

        threads = [Thread(target=construct) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(5)
        self.resources.extend(buffers)
        self.assertFalse(errors)
        self.assertEqual(self._counts(), (1, 1, 0))

    def test_unsafe_sidecar_shapes_are_rejected_without_touching_legacy(self) -> None:
        original = self._write(self._snapshot())
        target = Path(self.temp.name) / "target"
        target.write_bytes(b"target")
        try:
            os.symlink(target, self.sidecar)
        except (NotImplementedError, OSError) as error:
            self.skipTest(f"symlink unavailable: {error}")
        with self.assertRaises(ValueError): self._buffer()
        self.assertEqual(target.read_bytes(), b"target")
        self.assertEqual(self.legacy.read_bytes(), original)
