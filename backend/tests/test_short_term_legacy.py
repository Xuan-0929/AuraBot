from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import tempfile
from threading import Barrier, Event, Lock, Thread
import unittest
from unittest.mock import patch

from backend.memory.short_term.buffer import ShortTermMemoryBuffer
from backend.memory.stores.short_term_sqlite import SQLiteShortTermStore


class LegacyShortTermJsonMigrationTests(unittest.TestCase):
    PROFILE_ID = "profile-1"
    SOURCE_TYPE = "chat"

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.legacy_path = Path(self.tmpdir.name) / "short-term.json"
        self.sidecar_path = Path(f"{self.legacy_path}.sqlite3")
        self.buffers = []
        self.stores = []

    def tearDown(self):
        for buffer in self.buffers:
            buffer.close()
        for store in self.stores:
            store.close()
        self.tmpdir.cleanup()

    def _snapshot(self, *, messages=None, breakpoints=None):
        return {
            "profile_id": self.PROFILE_ID,
            "source_type": self.SOURCE_TYPE,
            "messages": (
                [
                    {
                        "role": "user",
                        "content": "第一条",
                        "source_id": "legacy-message-1",
                        "created_at": "2026-07-24T10:00:00+08:00",
                    },
                    {
                        "role": "assistant",
                        "content": "第二条",
                        "source_id": None,
                        "created_at": "2026-07-24T02:00:01Z",
                    },
                ]
                if messages is None
                else messages
            ),
            "breakpoints": (
                [
                    {
                        "id": "breakpoint-legacy-1",
                        "message_index": 2,
                        "topic": "legacy-topic",
                        "reason": "topic_changed",
                        "created_at": "2026-07-24T10:00:02+08:00",
                    }
                ]
                if breakpoints is None
                else breakpoints
            ),
        }

    def _write_snapshot(self, snapshot):
        self.legacy_path.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _buffer(self, **kwargs):
        kwargs.setdefault("profile_id", self.PROFILE_ID)
        kwargs.setdefault("source_type", self.SOURCE_TYPE)
        kwargs.setdefault("persistence_path", self.legacy_path)
        buffer = ShortTermMemoryBuffer(**kwargs)
        self.buffers.append(buffer)
        return buffer

    def _store(self, path=None):
        store = SQLiteShortTermStore(path or self.sidecar_path)
        self.stores.append(store)
        return store

    def _marker_value(self, *, profile_id=None, source_type=None):
        profile_id = profile_id or self.PROFILE_ID
        source_type = source_type or self.SOURCE_TYPE
        canonical_scope = json.dumps(
            [profile_id, source_type],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        marker_key = "legacy_json_imported:" + hashlib.sha256(
            canonical_scope.encode("utf-8")
        ).hexdigest()
        with closing(sqlite3.connect(self.sidecar_path)) as connection:
            row = connection.execute(
                """
                select value
                from short_term_schema_info
                where key = ?
                """,
                (marker_key,),
            ).fetchone()
        return None if row is None else row[0]

    def _state_counts(self):
        with closing(sqlite3.connect(self.sidecar_path)) as connection:
            return tuple(
                connection.execute(
                    f"select count(*) from {table}"
                ).fetchone()[0]
                for table in (
                    "short_term_messages",
                    "short_term_breakpoints",
                    "short_term_segments",
                )
            )

    def _symlink_or_skip(self, target, link):
        if not hasattr(os, "symlink"):
            self.skipTest("symlink creation is not supported")
        try:
            os.symlink(target, link)
        except (NotImplementedError, OSError) as error:
            self.skipTest(f"symlink creation is unavailable: {error}")

    def _hardlink_or_skip(self, target, link):
        if not hasattr(os, "link"):
            self.skipTest("hardlink creation is not supported")
        try:
            os.link(target, link)
        except (NotImplementedError, OSError) as error:
            self.skipTest(f"hardlink creation is unavailable: {error}")

    def _assert_legacy_bytes_fail_atomically(self, legacy_bytes, error_pattern):
        if self.sidecar_path.exists():
            self.sidecar_path.unlink()
        self.legacy_path.write_bytes(legacy_bytes)
        original_bytes = self.legacy_path.read_bytes()

        with self.assertRaisesRegex(ValueError, error_pattern):
            self._buffer()

        self.assertEqual(self.legacy_path.read_bytes(), original_bytes)
        self.assertEqual(self._state_counts(), (0, 0, 0))
        self.assertIsNone(self._marker_value())

    def test_existing_legacy_snapshot_imports_without_modifying_json(self):
        self._write_snapshot(self._snapshot())
        original_bytes = self.legacy_path.read_bytes()

        buffer = self._buffer()

        self.assertEqual(
            [
                (
                    message.role,
                    message.content,
                    message.source_id,
                    message.created_at,
                )
                for message in buffer.messages
            ],
            [
                (
                    "user",
                    "第一条",
                    "legacy-message-1",
                    "2026-07-24T10:00:00+08:00",
                ),
                (
                    "assistant",
                    "第二条",
                    None,
                    "2026-07-24T02:00:01Z",
                ),
            ],
        )
        self.assertEqual(len(buffer.breakpoints), 1)
        breakpoint = buffer.breakpoints[0]
        self.assertEqual(breakpoint.id, "breakpoint-legacy-1")
        self.assertEqual(breakpoint.message_index, 2)
        self.assertEqual(
            breakpoint.message_sequence,
            buffer.messages[1].sequence,
        )
        self.assertEqual(breakpoint.topic, "legacy-topic")
        self.assertEqual(breakpoint.reason, "topic_changed")
        self.assertEqual(
            breakpoint.created_at,
            "2026-07-24T10:00:02+08:00",
        )
        self.assertTrue(self.sidecar_path.exists())
        self.assertEqual(self.legacy_path.read_bytes(), original_bytes)

    def test_repeated_reopen_is_restart_safe_and_does_not_duplicate(self):
        self._write_snapshot(self._snapshot())

        for _ in range(3):
            buffer = self._buffer()
            self.assertEqual(len(buffer.messages), 2)
            self.assertEqual(len(buffer.breakpoints), 1)
            buffer.close()

        self.assertEqual(self._state_counts(), (2, 1, 0))

    def test_empty_snapshot_writes_marker_and_is_not_reimported(self):
        self._write_snapshot(self._snapshot(messages=[], breakpoints=[]))
        first = self._buffer()
        first.close()
        self.assertIsNotNone(self._marker_value())

        self._write_snapshot(self._snapshot())
        reopened = self._buffer()

        self.assertEqual(reopened.messages, [])
        self.assertEqual(reopened.breakpoints, [])
        self.assertEqual(self._state_counts(), (0, 0, 0))

    def test_concurrent_buffer_constructors_import_once(self):
        self._write_snapshot(self._snapshot())
        start = Barrier(3)
        results = []
        errors = []
        result_lock = Lock()

        def construct_buffer():
            start.wait()
            try:
                buffer = ShortTermMemoryBuffer(
                    profile_id=self.PROFILE_ID,
                    source_type=self.SOURCE_TYPE,
                    persistence_path=self.legacy_path,
                )
                result = (len(buffer.messages), len(buffer.breakpoints))
                buffer.close()
                with result_lock:
                    results.append(result)
            except BaseException as error:
                with result_lock:
                    errors.append(error)

        threads = [Thread(target=construct_buffer) for _ in range(2)]
        for thread in threads:
            thread.start()
        start.wait()
        for thread in threads:
            thread.join(timeout=5)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(sorted(results), [(2, 1), (2, 1)])
        self.assertEqual(self._state_counts(), (2, 1, 0))
        self.assertIsNotNone(self._marker_value())

    def test_marker_identity_isolates_collision_and_unicode_streams(self):
        streams = [
            ("a:b", "c", "collision-left"),
            ("a", "b:c", "collision-right"),
            ("用户::a/b|c", "来源:聊天/🚀|[]", "unicode-delimiters"),
        ]
        legacy_paths = []
        store = self._store()

        for index, (profile_id, source_type, content) in enumerate(streams):
            legacy_path = Path(self.tmpdir.name) / f"legacy-{index}.json"
            legacy_path.write_text(
                json.dumps(
                    {
                        "profile_id": profile_id,
                        "source_type": source_type,
                        "messages": [
                            {
                                "role": "user",
                                "content": content,
                                "source_id": None,
                                "created_at": "2026-07-24T10:00:00+08:00",
                            }
                        ],
                        "breakpoints": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            legacy_paths.append(legacy_path)

            result = store.import_legacy_snapshot(
                profile_id=profile_id,
                source_type=source_type,
                legacy_path=legacy_path,
            )

            self.assertTrue(result.imported)
            self.assertEqual(result.reason, "imported")
            self.assertEqual(
                [
                    message.content
                    for message in store.list_messages(
                        profile_id,
                        source_type,
                    )
                ],
                [content],
            )

        store.close()
        reopened = self._store()
        for legacy_path, (profile_id, source_type, content) in zip(
            legacy_paths,
            streams,
            strict=True,
        ):
            legacy_path.write_text("not JSON", encoding="utf-8")

            result = reopened.import_legacy_snapshot(
                profile_id=profile_id,
                source_type=source_type,
                legacy_path=legacy_path,
            )

            self.assertFalse(result.imported)
            self.assertEqual(result.reason, "already_imported")
            self.assertEqual(
                [
                    message.content
                    for message in reopened.list_messages(
                        profile_id,
                        source_type,
                    )
                ],
                [content],
            )
            marker_value = self._marker_value(
                profile_id=profile_id,
                source_type=source_type,
            )
            self.assertEqual(
                json.loads(marker_value),
                {
                    "legacy_filename": legacy_path.name,
                    "profile_id": profile_id,
                    "status": "imported",
                    "source_type": source_type,
                },
            )

    def test_invalid_top_level_snapshots_fail_without_partial_import(self):
        invalid_cases = {
            "malformed JSON": b"{",
            "non-object root": json.dumps([]).encode(),
            "missing profile": json.dumps(
                {
                    "source_type": self.SOURCE_TYPE,
                    "messages": [],
                    "breakpoints": [],
                }
            ).encode(),
            "wrong profile": json.dumps(
                self._snapshot() | {"profile_id": "profile-2"}
            ).encode(),
            "missing source": json.dumps(
                {
                    "profile_id": self.PROFILE_ID,
                    "messages": [],
                    "breakpoints": [],
                }
            ).encode(),
            "wrong source": json.dumps(
                self._snapshot() | {"source_type": "scheduler"}
            ).encode(),
            "non-list messages": json.dumps(
                self._snapshot(messages={})
            ).encode(),
            "non-list breakpoints": json.dumps(
                self._snapshot(breakpoints={})
            ).encode(),
        }

        for name, legacy_bytes in invalid_cases.items():
            with self.subTest(case=name):
                if self.sidecar_path.exists():
                    self.sidecar_path.unlink()
                self.legacy_path.write_bytes(legacy_bytes)

                with self.assertRaisesRegex(ValueError, "legacy"):
                    self._buffer()

                if self.sidecar_path.exists():
                    self.assertEqual(self._state_counts(), (0, 0, 0))
                    self.assertIsNone(self._marker_value())

    def test_invalid_message_records_fail_atomically(self):
        valid_message = self._snapshot()["messages"][0]
        invalid_messages = {
            "non-object": ["not-an-object"],
            "missing role": [
                {
                    key: value
                    for key, value in valid_message.items()
                    if key != "role"
                }
            ],
            "invalid content": [valid_message | {"content": 3}],
            "invalid source id": [valid_message | {"source_id": 3}],
            "naive timestamp": [
                valid_message | {"created_at": "2026-07-24T10:00:00"}
            ],
        }

        for name, messages in invalid_messages.items():
            with self.subTest(case=name):
                if self.sidecar_path.exists():
                    self.sidecar_path.unlink()
                self._write_snapshot(
                    self._snapshot(messages=messages, breakpoints=[])
                )

                with self.assertRaisesRegex(ValueError, "legacy message"):
                    self._buffer()

                self.assertEqual(self._state_counts(), (0, 0, 0))
                self.assertIsNone(self._marker_value())

    def test_invalid_breakpoint_records_fail_atomically(self):
        valid_breakpoint = self._snapshot()["breakpoints"][0]
        invalid_breakpoints = {
            "non-object": ["not-an-object"],
            "missing id": [
                {
                    key: value
                    for key, value in valid_breakpoint.items()
                    if key != "id"
                }
            ],
            "blank id": [valid_breakpoint | {"id": " "}],
            "invalid index": [
                valid_breakpoint | {"message_index": True}
            ],
            "out-of-range index": [
                valid_breakpoint | {"message_index": 3}
            ],
            "invalid topic": [valid_breakpoint | {"topic": 3}],
            "invalid reason": [valid_breakpoint | {"reason": 3}],
            "naive timestamp": [
                valid_breakpoint
                | {"created_at": "2026-07-24T10:00:02"}
            ],
            "duplicate ids": [
                valid_breakpoint | {"message_index": 1},
                valid_breakpoint,
            ],
            "duplicate indexes": [
                valid_breakpoint | {"id": "breakpoint-1"},
                valid_breakpoint | {"id": "breakpoint-2"},
            ],
            "out-of-order indexes": [
                valid_breakpoint,
                valid_breakpoint
                | {"id": "breakpoint-2", "message_index": 1},
            ],
        }

        for name, breakpoints in invalid_breakpoints.items():
            with self.subTest(case=name):
                if self.sidecar_path.exists():
                    self.sidecar_path.unlink()
                self._write_snapshot(
                    self._snapshot(breakpoints=breakpoints)
                )

                with self.assertRaisesRegex(ValueError, "legacy breakpoint"):
                    self._buffer()

                self.assertEqual(self._state_counts(), (0, 0, 0))
                self.assertIsNone(self._marker_value())

    def test_unknown_legacy_object_keys_fail_atomically(self):
        snapshot = self._snapshot()
        invalid_snapshots = {
            "top-level": snapshot | {"unexpected": True},
            "message": snapshot
            | {
                "messages": [
                    snapshot["messages"][0] | {"unexpected": True}
                ],
                "breakpoints": [],
            },
            "breakpoint": snapshot
            | {
                "breakpoints": [
                    snapshot["breakpoints"][0] | {"unexpected": True}
                ]
            },
        }

        for level, invalid_snapshot in invalid_snapshots.items():
            with self.subTest(level=level):
                self._assert_legacy_bytes_fail_atomically(
                    json.dumps(
                        invalid_snapshot,
                        ensure_ascii=False,
                    ).encode("utf-8"),
                    "unknown keys",
                )

    def test_duplicate_legacy_object_keys_fail_atomically(self):
        duplicate_snapshots = {
            "top-level": (
                b'{"profile_id":"profile-1","profile_id":"profile-1",'
                b'"source_type":"chat","messages":[],"breakpoints":[]}'
            ),
            "message": (
                b'{"profile_id":"profile-1","source_type":"chat",'
                b'"messages":[{"role":"user","content":"first",'
                b'"content":"second","source_id":null,'
                b'"created_at":"2026-07-24T10:00:00+08:00"}],'
                b'"breakpoints":[]}'
            ),
            "breakpoint": (
                b'{"profile_id":"profile-1","source_type":"chat",'
                b'"messages":[{"role":"user","content":"first",'
                b'"source_id":null,'
                b'"created_at":"2026-07-24T10:00:00+08:00"}],'
                b'"breakpoints":[{"id":"breakpoint-1",'
                b'"id":"breakpoint-2","message_index":1,'
                b'"topic":null,"reason":null,'
                b'"created_at":"2026-07-24T10:00:01+08:00"}]}'
            ),
        }

        for level, legacy_bytes in duplicate_snapshots.items():
            with self.subTest(level=level):
                self._assert_legacy_bytes_fail_atomically(
                    legacy_bytes,
                    "duplicate key",
                )

    def test_constructor_closes_owned_store_when_migration_fails(self):
        self._write_snapshot(self._snapshot(messages=["invalid"]))
        created_stores = []

        class TrackingStore(SQLiteShortTermStore):
            def __init__(self, path):
                super().__init__(path)
                created_stores.append(self)

        with patch(
            "backend.memory.short_term.buffer.SQLiteShortTermStore",
            TrackingStore,
        ):
            with self.assertRaisesRegex(ValueError, "legacy message"):
                ShortTermMemoryBuffer(
                    profile_id=self.PROFILE_ID,
                    source_type=self.SOURCE_TYPE,
                    persistence_path=self.legacy_path,
                )

        self.assertEqual(len(created_stores), 1)
        with self.assertRaisesRegex(
            RuntimeError,
            "^SQLiteShortTermStore is closed$",
        ):
            created_stores[0].list_messages(
                self.PROFILE_ID,
                self.SOURCE_TYPE,
            )

    def test_existing_stream_state_wins_without_reading_legacy_json(self):
        store = self._store()
        store.append_message(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            role="user",
            content="SQLite state",
            source_id=None,
        )
        store.close()
        self.legacy_path.write_bytes(b"{")

        buffer = self._buffer()

        self.assertEqual(
            [message.content for message in buffer.messages],
            ["SQLite state"],
        )
        self.assertEqual(
            json.loads(self._marker_value()),
            {
                "legacy_filename": self.legacy_path.name,
                "profile_id": self.PROFILE_ID,
                "status": "existing_state_won",
                "source_type": self.SOURCE_TYPE,
            },
        )

    def test_existing_state_marker_prevents_legacy_resurrection_after_cleanup(self):
        self._write_snapshot(self._snapshot())
        original_bytes = self.legacy_path.read_bytes()
        store = self._store()
        store.append_message(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            role="user",
            content="durable SQLite state",
            source_id=None,
        )
        store.append_message(
            profile_id="other-profile",
            source_type="other-source",
            role="user",
            content="other stream",
            source_id=None,
        )
        store.close()

        first = self._buffer()
        self.assertEqual(
            [message.content for message in first.messages],
            ["durable SQLite state"],
        )
        first.close()
        self.assertEqual(
            json.loads(self._marker_value())["status"],
            "existing_state_won",
        )

        with closing(sqlite3.connect(self.sidecar_path)) as connection:
            for table in (
                "short_term_segments",
                "short_term_breakpoints",
                "short_term_messages",
            ):
                connection.execute(
                    f"""
                    delete from {table}
                    where profile_id = ? and source_type = ?
                    """,
                    (self.PROFILE_ID, self.SOURCE_TYPE),
                )
            connection.commit()

        reopened = self._buffer()

        self.assertEqual(reopened.messages, [])
        self.assertEqual(reopened.breakpoints, [])
        other_store = self._store()
        self.assertEqual(
            [
                message.content
                for message in other_store.list_messages(
                    "other-profile",
                    "other-source",
                )
            ],
            ["other stream"],
        )
        self.assertEqual(self.legacy_path.read_bytes(), original_bytes)

    def test_absent_legacy_with_existing_state_writes_terminal_marker(self):
        self.assertFalse(self.legacy_path.exists())
        store = self._store()
        store.append_message(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            role="user",
            content="SQLite state while legacy is absent",
            source_id=None,
        )
        store.close()

        first = self._buffer()

        self.assertEqual(
            [message.content for message in first.messages],
            ["SQLite state while legacy is absent"],
        )
        first.close()
        self.assertEqual(
            json.loads(self._marker_value())["status"],
            "existing_state_won",
        )

        with closing(sqlite3.connect(self.sidecar_path)) as connection:
            connection.execute(
                """
                delete from short_term_messages
                where profile_id = ? and source_type = ?
                """,
                (self.PROFILE_ID, self.SOURCE_TYPE),
            )
            connection.commit()
        self._write_snapshot(self._snapshot())

        reopened = self._buffer()

        self.assertEqual(reopened.messages, [])
        self.assertEqual(reopened.breakpoints, [])

    def test_absent_legacy_without_state_can_import_when_file_appears(self):
        self.assertFalse(self.legacy_path.exists())

        absent = self._buffer()

        self.assertFalse(absent._legacy_import_result.imported)
        self.assertEqual(
            absent._legacy_import_result.reason,
            "legacy_absent",
        )
        absent.close()
        self.assertTrue(self.sidecar_path.exists())
        self.assertEqual(self._state_counts(), (0, 0, 0))
        self.assertIsNone(self._marker_value())

        self._write_snapshot(self._snapshot())
        imported = self._buffer()
        imported.close()
        reopened = self._buffer()

        self.assertEqual(len(reopened.messages), 2)
        self.assertEqual(len(reopened.breakpoints), 1)
        self.assertEqual(self._state_counts(), (2, 1, 0))
        self.assertEqual(
            json.loads(self._marker_value())["status"],
            "imported",
        )

    def test_concurrent_absent_restored_boundary_imports_once(self):
        from backend.memory.stores import short_term_sqlite as store_module

        first_store = self._store()
        second_store = self._store()
        real_loader = store_module._load_legacy_snapshot
        first_read_started = Event()
        release_absent_read = Event()
        loader_lock = Lock()
        loader_calls = 0
        results = []
        errors = []
        result_lock = Lock()

        def controlled_loader(path, *, profile_id, source_type):
            nonlocal loader_calls
            with loader_lock:
                loader_calls += 1
                call_number = loader_calls
            if call_number == 1:
                first_read_started.set()
                if not release_absent_read.wait(timeout=3):
                    raise AssertionError("test did not release absent read")
                raise FileNotFoundError(path)
            return real_loader(
                path,
                profile_id=profile_id,
                source_type=source_type,
            )

        def import_snapshot(store):
            try:
                result = store.import_legacy_snapshot(
                    profile_id=self.PROFILE_ID,
                    source_type=self.SOURCE_TYPE,
                    legacy_path=self.legacy_path,
                )
                with result_lock:
                    results.append(result)
            except BaseException as error:
                with result_lock:
                    errors.append(error)

        with patch.object(
            store_module,
            "_load_legacy_snapshot",
            controlled_loader,
        ):
            first_thread = Thread(
                target=import_snapshot,
                args=(first_store,),
            )
            second_thread = Thread(
                target=import_snapshot,
                args=(second_store,),
            )
            first_thread.start()
            self.assertTrue(first_read_started.wait(timeout=1))
            self._write_snapshot(self._snapshot())
            second_thread.start()
            release_absent_read.set()
            first_thread.join(timeout=5)
            second_thread.join(timeout=5)

        self.assertFalse(first_thread.is_alive())
        self.assertFalse(second_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(
            sorted(result.reason for result in results),
            ["imported", "legacy_absent"],
        )
        self.assertEqual(
            sum(result.imported for result in results),
            1,
        )
        self.assertEqual(self._state_counts(), (2, 1, 0))

    def test_other_stream_state_does_not_block_exact_stream_import(self):
        store = self._store()
        store.append_message(
            profile_id="profile-2",
            source_type=self.SOURCE_TYPE,
            role="user",
            content="other stream",
            source_id=None,
        )
        store.close()
        self._write_snapshot(self._snapshot())

        buffer = self._buffer()

        self.assertEqual(
            [message.content for message in buffer.messages],
            ["第一条", "第二条"],
        )
        reopened = self._store()
        self.assertEqual(
            [
                message.content
                for message in reopened.list_messages(
                    "profile-2",
                    self.SOURCE_TYPE,
                )
            ],
            ["other stream"],
        )

    def test_legacy_changes_after_import_do_not_overwrite_sqlite_state(self):
        self._write_snapshot(self._snapshot())
        first = self._buffer()
        first.add(role="user", content="SQLite only", source_id=None)
        first.close()
        self._write_snapshot(
            self._snapshot(
                messages=[
                    self._snapshot()["messages"][0]
                    | {"content": "changed legacy"}
                ],
                breakpoints=[],
            )
        )

        reopened = self._buffer()

        self.assertEqual(
            [message.content for message in reopened.messages],
            ["第一条", "第二条", "SQLite only"],
        )

    def test_sqlite_path_is_used_directly_and_case_insensitive_json_gets_sidecar(self):
        direct_path = Path(self.tmpdir.name) / "direct.sqlite3"
        direct = self._buffer(persistence_path=direct_path)
        direct.add(role="user", content="direct", source_id=None)
        direct.close()
        self.assertTrue(direct_path.exists())
        self.assertFalse(Path(f"{direct_path}.sqlite3").exists())

        uppercase_json = Path(self.tmpdir.name) / "legacy.JSON"
        uppercase = self._buffer(persistence_path=uppercase_json)
        uppercase.close()
        self.assertFalse(uppercase_json.exists())
        self.assertTrue(Path(f"{uppercase_json}.sqlite3").exists())

    def test_dangling_symlink_sidecar_is_rejected_without_touching_legacy(self):
        self._write_snapshot(self._snapshot())
        original_bytes = self.legacy_path.read_bytes()
        missing_target = Path(self.tmpdir.name) / "missing.sqlite3"
        self._symlink_or_skip(missing_target, self.sidecar_path)

        with self.assertRaisesRegex(ValueError, "sidecar.*symlink"):
            self._buffer()

        self.assertTrue(self.sidecar_path.is_symlink())
        self.assertFalse(missing_target.exists())
        self.assertEqual(self.legacy_path.read_bytes(), original_bytes)

    def test_symlink_sidecar_to_existing_legacy_is_rejected(self):
        self._write_snapshot(self._snapshot())
        original_bytes = self.legacy_path.read_bytes()
        self._symlink_or_skip(self.legacy_path, self.sidecar_path)

        with self.assertRaisesRegex(ValueError, "sidecar.*symlink"):
            self._buffer()

        self.assertTrue(self.sidecar_path.is_symlink())
        self.assertEqual(self.legacy_path.read_bytes(), original_bytes)

    def test_hardlink_sidecar_aliasing_legacy_is_rejected(self):
        self._write_snapshot(self._snapshot())
        original_bytes = self.legacy_path.read_bytes()
        self._hardlink_or_skip(self.legacy_path, self.sidecar_path)

        with self.assertRaisesRegex(ValueError, "sidecar.*legacy"):
            self._buffer()

        self.assertTrue(os.path.samefile(self.legacy_path, self.sidecar_path))
        self.assertEqual(self.legacy_path.read_bytes(), original_bytes)

    def test_directory_sidecar_is_rejected_without_touching_legacy(self):
        self._write_snapshot(self._snapshot())
        original_bytes = self.legacy_path.read_bytes()
        self.sidecar_path.mkdir()

        with self.assertRaisesRegex(ValueError, "sidecar.*regular file"):
            self._buffer()

        self.assertTrue(self.sidecar_path.is_dir())
        self.assertEqual(self.legacy_path.read_bytes(), original_bytes)

    @unittest.skipUnless(
        hasattr(socket, "AF_UNIX"),
        "Unix-domain sockets are unavailable",
    )
    def test_nonregular_socket_sidecar_is_rejected(self):
        self._write_snapshot(self._snapshot())
        original_bytes = self.legacy_path.read_bytes()
        unix_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(unix_socket.close)
        try:
            unix_socket.bind(str(self.sidecar_path))
        except OSError as error:
            self.skipTest(f"Unix-domain socket binding is unavailable: {error}")

        with self.assertRaisesRegex(ValueError, "sidecar.*regular file"):
            self._buffer()

        self.assertEqual(self.legacy_path.read_bytes(), original_bytes)
