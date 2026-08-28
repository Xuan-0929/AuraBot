"""Protect the public SQLite ShortTerm store contract with representative durable scenarios.

These tests cover persistence, stream isolation, segmentation, concurrent single-claim behavior, rollback,
file identity, and schema rejection without duplicating every internal SQL probe implemented by the store.
"""

from contextlib import closing
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
from threading import Barrier, Lock, Thread
import unittest

from backend.memory.stores.short_term_sqlite import SQLiteShortTermStore


class SQLiteShortTermStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "memory.sqlite3"
        self.store = SQLiteShortTermStore(self.path)

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def _append(self, content, *, profile="profile-1", source="chat"):
        return self.store.append_message(
            profile_id=profile, source_type=source, role="user", content=content, source_id=None,
        )

    def _prepare(self, contents=("one",), *, profile="profile-1", source="chat"):
        messages = [self._append(content, profile=profile, source=source) for content in contents]
        breakpoint = self.store.add_breakpoint(
            profile_id=profile, source_type=source, topic="topic", reason="changed",
        )
        request = self.store.prepare_oldest_segment(profile_id=profile, source_type=source)
        self.assertIsNotNone(request)
        return request, messages, breakpoint

    def _durable_counts(self):
        with closing(sqlite3.connect(self.path)) as connection:
            return tuple(connection.execute(f"select count(*) from {table}").fetchone()[0] for table in (
                "short_term_messages", "short_term_breakpoints", "short_term_segments",
            ))

    def test_messages_breakpoints_and_sequence_survive_reopen(self) -> None:
        first = self._append("first")
        self.store.add_breakpoint(
            profile_id="profile-1", source_type="chat", topic="one", reason="changed",
        )
        self.store.close()
        self.store = SQLiteShortTermStore(self.path)
        second = self._append("second")
        self.assertEqual(
            [message.content for message in self.store.list_messages("profile-1", "chat")],
            ["first", "second"],
        )
        self.assertGreater(second.sequence, first.sequence)
        self.assertEqual(len(self.store.list_breakpoints("profile-1", "chat")), 1)

    def test_profile_and_source_streams_are_isolated(self) -> None:
        for profile, source, content in (
            ("profile-a", "chat", "a-chat"),
            ("profile-a", "scheduler", "a-scheduler"),
            ("profile-b", "chat", "b-chat"),
        ):
            self._append(content, profile=profile, source=source)
        for profile, source, expected in (
            ("profile-a", "chat", "a-chat"),
            ("profile-a", "scheduler", "a-scheduler"),
            ("profile-b", "chat", "b-chat"),
        ):
            with self.subTest(profile=profile, source=source):
                messages = self.store.list_messages(profile, source)
                self.assertEqual([message.content for message in messages], [expected])

    def test_prepare_moves_only_the_oldest_complete_segment_durably(self) -> None:
        self._append("first")
        self.store.add_breakpoint(
            profile_id="profile-1", source_type="chat", topic="first", reason="changed",
        )
        self._append("second")
        self.store.add_breakpoint(
            profile_id="profile-1", source_type="chat", topic="second", reason="changed",
        )
        self._append("still active")
        prepared = self.store.prepare_oldest_segment(profile_id="profile-1", source_type="chat")
        self.assertEqual([message.content for message in prepared.messages], ["first"])
        self.assertEqual(
            [message.content for message in self.store.list_messages("profile-1", "chat")],
            ["second", "still active"],
        )
        self.store.close()
        self.store = SQLiteShortTermStore(self.path)
        self.assertEqual(self._durable_counts(), (2, 1, 1))

    def test_duplicate_prepare_and_incomplete_boundary_do_not_create_segments(self) -> None:
        self._append("active")
        self.assertIsNone(self.store.prepare_oldest_segment(profile_id="profile-1", source_type="chat"))
        prepared, _, _ = self._prepare(("complete",), profile="profile-2")
        self.assertIsNotNone(prepared)
        self.assertIsNone(self.store.prepare_oldest_segment(profile_id="profile-2", source_type="chat"))

    def test_two_connections_prepare_one_segment_exactly_once(self) -> None:
        self._append("shared")
        self.store.add_breakpoint(
            profile_id="profile-1", source_type="chat", topic=None, reason=None,
        )
        other = SQLiteShortTermStore(self.path)
        self.addCleanup(other.close)
        barrier, lock = Barrier(2), Lock()
        results, errors = [], []

        def prepare(store) -> None:
            try:
                barrier.wait()
                result = store.prepare_oldest_segment(profile_id="profile-1", source_type="chat")
                with lock: results.append(result)
            except BaseException as error:
                with lock: errors.append(error)

        threads = [Thread(target=prepare, args=(store,)) for store in (self.store, other)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(5)
        self.assertFalse(errors)
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(self._durable_counts(), (0, 0, 1))

    def test_prepare_rolls_back_as_one_transaction(self) -> None:
        self._append("rollback")
        self.store.add_breakpoint(
            profile_id="profile-1", source_type="chat", topic=None, reason=None,
        )
        before = self._durable_counts()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                "create trigger abort_message_delete before delete on short_term_messages "
                "begin select raise(abort, 'delete failed'); end"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.prepare_oldest_segment(profile_id="profile-1", source_type="chat")
        self.assertEqual(self._durable_counts(), before)

    def test_schema_corruption_is_rejected_without_rewriting_the_database(self) -> None:
        mutations = {
            "future version": lambda connection: connection.execute(
                "update short_term_schema_info set value = '99' where key = 'schema_version'"
            ),
            "missing index": lambda connection: connection.execute(
                "drop index idx_short_term_segments_profile_source_state_lease"
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                path = Path(self.temp.name) / f"{label.replace(' ', '-')}.sqlite3"
                store = SQLiteShortTermStore(path)
                store.close()
                with closing(sqlite3.connect(path)) as connection, connection:
                    mutate(connection)
                with self.assertRaises(RuntimeError):
                    SQLiteShortTermStore(path)

    def test_database_file_is_private_and_symlinks_are_rejected(self) -> None:
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        target = Path(self.temp.name) / "target.sqlite3"
        target.write_bytes(b"do not touch")
        link = Path(self.temp.name) / "link.sqlite3"
        try:
            os.symlink(target, link)
        except (NotImplementedError, OSError) as error:
            self.skipTest(f"symlink unavailable: {error}")
        with self.assertRaises(ValueError):
            SQLiteShortTermStore(link)
        self.assertEqual(target.read_bytes(), b"do not touch")
