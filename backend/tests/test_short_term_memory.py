from contextlib import closing
from dataclasses import FrozenInstanceError
from pathlib import Path
import sqlite3
import tempfile
from typing import get_type_hints
import unittest

from backend.memory.short_term.models import (
    BufferStats,
    SegmentState,
    ShortTermBreakpoint,
    ShortTermMessage,
    SummaryRequest,
)
from backend.memory.short_term.policy import ShortTermPolicy
from backend.memory.stores.short_term_sqlite import SQLiteShortTermStore


class SQLiteShortTermStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "memory.sqlite3"
        self.store = SQLiteShortTermStore(self.path)

    def tearDown(self):
        self.store.close()
        self.tmpdir.cleanup()

    def test_messages_and_breakpoints_survive_close_and_reopen(self):
        self.store.append_message(
            profile_id="profile-1",
            source_type="chat",
            role="user",
            content="第一段话题",
            source_id="source-1",
        )
        self.store.add_breakpoint(
            profile_id="profile-1",
            source_type="chat",
            topic="first",
            reason="topic_changed",
        )
        self.store.close()

        self.store = SQLiteShortTermStore(self.path)

        messages = self.store.list_messages("profile-1", "chat")
        breakpoints = self.store.list_breakpoints("profile-1", "chat")
        self.assertEqual(len(messages), 1)
        self.assertEqual(len(breakpoints), 1)
        self.assertEqual(breakpoints[0].topic, "first")

    def test_profiles_are_isolated(self):
        for profile_id in ("profile-1", "profile-2"):
            self.store.append_message(
                profile_id=profile_id,
                source_type="chat",
                role="user",
                content=profile_id,
                source_id=None,
            )

        messages = self.store.list_messages("profile-1", "chat")

        self.assertEqual([message.content for message in messages], ["profile-1"])

    def test_sources_are_isolated(self):
        for source_type in ("chat", "scheduler"):
            self.store.append_message(
                profile_id="profile-1",
                source_type=source_type,
                role="user",
                content=source_type,
                source_id=None,
            )

        chat_messages = self.store.list_messages("profile-1", "chat")
        scheduler_messages = self.store.list_messages("profile-1", "scheduler")

        self.assertEqual([message.content for message in chat_messages], ["chat"])
        self.assertEqual(
            [message.content for message in scheduler_messages],
            ["scheduler"],
        )

    def test_breakpoint_requires_a_new_latest_message(self):
        breakpoint_args = {
            "profile_id": "profile-1",
            "source_type": "chat",
            "topic": "coffee",
            "reason": "topic_changed",
        }
        self.assertIsNone(self.store.add_breakpoint(**breakpoint_args))

        self.store.append_message(
            profile_id="profile-1",
            source_type="chat",
            role="user",
            content="咖啡",
            source_id=None,
        )
        first = self.store.add_breakpoint(**breakpoint_args)
        duplicate = self.store.add_breakpoint(**breakpoint_args)

        self.assertIsNotNone(first)
        self.assertIsNone(duplicate)

        self.store.append_message(
            profile_id="profile-1",
            source_type="chat",
            role="assistant",
            content="拿铁",
            source_id=None,
        )
        second = self.store.add_breakpoint(**breakpoint_args)

        self.assertIsNotNone(second)
        self.assertNotEqual(first.id, second.id)

    def test_stats_count_messages_breakpoints_and_content_characters(self):
        for content in ("abc", "喜欢拿铁"):
            self.store.append_message(
                profile_id="profile-1",
                source_type="chat",
                role="user",
                content=content,
                source_id=None,
            )
        self.store.add_breakpoint(
            profile_id="profile-1",
            source_type="chat",
            topic="coffee",
            reason="topic_changed",
        )

        stats = self.store.get_stats("profile-1", "chat")

        self.assertEqual(stats.message_count, 2)
        self.assertEqual(stats.breakpoint_count, 1)
        self.assertEqual(stats.char_count, len("abc") + len("喜欢拿铁"))

    def test_close_is_idempotent_and_closed_operations_fail_clearly(self):
        self.store.close()
        self.store.close()

        with self.assertRaisesRegex(RuntimeError, "SQLiteShortTermStore is closed"):
            self.store.list_messages("profile-1", "chat")

    def test_memory_database_supports_messages_and_breakpoints(self):
        store = SQLiteShortTermStore(":memory:")
        self.addCleanup(store.close)

        message = store.append_message(
            profile_id="profile-1",
            source_type="chat",
            role="user",
            content="只在内存里",
            source_id=None,
        )
        breakpoint = store.add_breakpoint(
            profile_id="profile-1",
            source_type="chat",
            topic=None,
            reason=None,
        )

        self.assertEqual(store.list_messages("profile-1", "chat"), [message])
        self.assertEqual(store.list_breakpoints("profile-1", "chat"), [breakpoint])

    def test_interleaved_streams_keep_global_sequence_and_local_message_index(self):
        first = self.store.append_message(
            profile_id="profile-1",
            source_type="chat",
            role="user",
            content="p1-chat-1",
            source_id=None,
        )
        second = self.store.append_message(
            profile_id="profile-2",
            source_type="chat",
            role="user",
            content="p2-chat-1",
            source_id=None,
        )
        profile_two_breakpoint = self.store.add_breakpoint(
            profile_id="profile-2",
            source_type="chat",
            topic="profile-two",
            reason=None,
        )
        third = self.store.append_message(
            profile_id="profile-1",
            source_type="scheduler",
            role="system",
            content="p1-scheduler-1",
            source_id=None,
        )
        scheduler_breakpoint = self.store.add_breakpoint(
            profile_id="profile-1",
            source_type="scheduler",
            topic="scheduled",
            reason=None,
        )
        fourth = self.store.append_message(
            profile_id="profile-1",
            source_type="chat",
            role="assistant",
            content="p1-chat-2",
            source_id=None,
        )
        profile_one_breakpoint = self.store.add_breakpoint(
            profile_id="profile-1",
            source_type="chat",
            topic="profile-one",
            reason=None,
        )

        self.assertEqual(
            [first.sequence, second.sequence, third.sequence, fourth.sequence],
            [1, 2, 3, 4],
        )
        self.assertEqual(profile_two_breakpoint.message_index, 1)
        self.assertEqual(scheduler_breakpoint.message_index, 1)
        self.assertEqual(profile_one_breakpoint.message_index, 2)

    def test_two_stores_cannot_add_the_same_latest_breakpoint(self):
        self.store.append_message(
            profile_id="profile-1",
            source_type="chat",
            role="user",
            content="共享消息",
            source_id=None,
        )
        competing_store = SQLiteShortTermStore(self.path)
        self.addCleanup(competing_store.close)
        breakpoint_args = {
            "profile_id": "profile-1",
            "source_type": "chat",
            "topic": "shared",
            "reason": "topic_changed",
        }

        first = self.store.add_breakpoint(**breakpoint_args)
        duplicate = competing_store.add_breakpoint(**breakpoint_args)

        self.assertIsNotNone(first)
        self.assertIsNone(duplicate)
        self.assertEqual(len(self.store.list_breakpoints("profile-1", "chat")), 1)

    def test_rejects_unsupported_schema_version_without_rewriting_it(self):
        self._replace_schema_version("99")

        reopened = None
        try:
            with self.assertRaisesRegex(RuntimeError, "Unsupported.*99"):
                reopened = SQLiteShortTermStore(self.path)
        finally:
            if reopened is not None:
                reopened.close()

        self.assertEqual(self._read_schema_version(), "99")

    def test_rejects_malformed_schema_version_without_rewriting_it(self):
        self._replace_schema_version("not-an-integer")

        reopened = None
        try:
            with self.assertRaisesRegex(ValueError, "Invalid.*not-an-integer"):
                reopened = SQLiteShortTermStore(self.path)
        finally:
            if reopened is not None:
                reopened.close()

        self.assertEqual(self._read_schema_version(), "not-an-integer")

    def test_incompatible_partial_schema_rolls_back_new_schema_objects(self):
        self.store.close()
        self.path.unlink()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                """
                create table short_term_messages (
                  sequence integer primary key autoincrement
                )
                """
            )

        reopened = None
        try:
            with self.assertRaisesRegex(RuntimeError, "short_term_messages"):
                reopened = SQLiteShortTermStore(self.path)
        finally:
            if reopened is not None:
                reopened.close()

        with closing(sqlite3.connect(self.path)) as connection, connection:
            tables = {
                row[0]
                for row in connection.execute(
                    """
                    select name
                    from sqlite_master
                    where type = 'table' and name like 'short_term_%'
                    """
                )
            }
        self.assertEqual(tables, {"short_term_messages"})

    def test_v1_schema_rejects_nullable_critical_column(self):
        self._rewrite_table_schema(
            table_name="short_term_segments",
            old="profile_id text not null",
            new="profile_id text",
        )

        self._assert_reopen_rejected("short_term_segments.*profile_id")

    def test_v1_schema_rejects_wrong_critical_default(self):
        self._rewrite_table_schema(
            table_name="short_term_segments",
            old="attempt_count integer not null default 0",
            new="attempt_count integer not null default 1",
        )

        self._assert_reopen_rejected("short_term_segments.*attempt_count")

    def test_v1_schema_rejects_missing_required_unique_structure(self):
        self._rewrite_table_schema(
            table_name="short_term_breakpoints",
            old="unique(profile_id, source_type, message_sequence)",
            new=(
                "check ("
                "'unique(profile_id,source_type,message_sequence)' is not null"
                ")"
            ),
        )

        self._assert_reopen_rejected("short_term_breakpoints.*unique")

    def test_v1_schema_rejects_spoofed_state_check(self):
        self._rewrite_table_schema(
            table_name="short_term_segments",
            old=(
                "check (\n"
                "        state in ('pending', 'processing', 'completed', 'failed')\n"
                "      )"
            ),
            new=(
                "check (1) "
                "/* check(state in ('pending', 'processing', 'completed', 'failed')) */"
            ),
        )

        self._assert_reopen_rejected("effective CHECK.*state")

    def test_v1_schema_rejects_overly_restrictive_state_check(self):
        self._rewrite_table_schema(
            table_name="short_term_segments",
            old=(
                "check (\n"
                "        state in ('pending', 'processing', 'completed', 'failed')\n"
                "      )"
            ),
            new="check (state = 'pending')",
        )

        self._assert_reopen_rejected("overly restrictive.*state")

    def test_v1_schema_rejects_reusable_message_sequence(self):
        self._rewrite_table_schema(
            table_name="short_term_messages",
            old="sequence integer primary key autoincrement",
            new="sequence integer primary key",
        )

        self._assert_reopen_rejected("AUTOINCREMENT|monotonic")

    def test_fresh_schema_rejects_null_text_primary_keys(self):
        cases = (
            (
                "schema-info",
                """
                insert into short_term_schema_info(key, value, updated_at)
                values (?, ?, ?)
                """,
                (None, "value", "2026-07-25T00:00:00+00:00"),
            ),
            (
                "breakpoint",
                """
                insert into short_term_breakpoints (
                  id, profile_id, source_type, message_sequence, created_at
                )
                values (?, ?, ?, ?, ?)
                """,
                (
                    None,
                    "profile-1",
                    "chat",
                    1,
                    "2026-07-25T00:00:00+00:00",
                ),
            ),
            (
                "segment",
                """
                insert into short_term_segments (
                  id, profile_id, source_type, breakpoint_id, state,
                  payload_json, created_at, updated_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    None,
                    "profile-1",
                    "chat",
                    "breakpoint-null-id",
                    "pending",
                    "{}",
                    "2026-07-25T00:00:00+00:00",
                    "2026-07-25T00:00:00+00:00",
                ),
            ),
        )

        for name, statement, params in cases:
            with self.subTest(name=name):
                with closing(sqlite3.connect(self.path)) as connection, connection:
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(statement, params)

    def test_v1_schema_rejects_nullable_text_primary_key(self):
        self._rewrite_table_schema(
            table_name="short_term_breakpoints",
            old=("id text not null primary key", "id text primary key"),
            new="id text primary key",
        )

        self._assert_reopen_rejected("short_term_breakpoints.id")

    def test_v1_schema_rejects_missing_lease_recovery_index(self):
        self.store.close()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                "drop index idx_short_term_segments_profile_source_state_lease"
            )

        self._assert_reopen_rejected(
            "idx_short_term_segments_profile_source_state_lease"
        )

    def test_message_sequence_is_not_reused_after_delete(self):
        first = self.store.append_message(
            profile_id="profile-1",
            source_type="chat",
            role="user",
            content="first",
            source_id=None,
        )
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                "delete from short_term_messages where id = ?",
                (first.id,),
            )

        second = self.store.append_message(
            profile_id="profile-1",
            source_type="chat",
            role="user",
            content="second",
            source_id=None,
        )

        self.assertGreater(second.sequence, first.sequence)

    def test_schema_validation_probes_leave_no_rows_or_sequence_state(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            segment_count = connection.execute(
                "select count(*) from short_term_segments"
            ).fetchone()[0]
            validation_message_count = connection.execute(
                """
                select count(*)
                from short_term_messages
                where profile_id = ?
                """,
                ("schema-validation",),
            ).fetchone()[0]
            sequence_row = connection.execute(
                """
                select seq
                from sqlite_sequence
                where name = ?
                """,
                ("short_term_messages",),
            ).fetchone()

        first_real_message = self.store.append_message(
            profile_id="profile-1",
            source_type="chat",
            role="user",
            content="first real message",
            source_id=None,
        )

        self.assertEqual(segment_count, 0)
        self.assertEqual(validation_message_count, 0)
        self.assertIsNone(sequence_row)
        self.assertEqual(first_real_message.sequence, 1)

    def test_segment_rows_enforce_json_and_nonnegative_attempts(self):
        cases = (
            ("invalid-payload", "not-json", "{}", 0),
            ("invalid-result", "{}", "not-json", 0),
            ("negative-attempts", "{}", "{}", -1),
        )

        for segment_id, payload_json, result_json, attempt_count in cases:
            with self.subTest(segment_id=segment_id):
                with closing(sqlite3.connect(self.path)) as connection, connection:
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            """
                            insert into short_term_segments (
                              id, profile_id, source_type, breakpoint_id, state,
                              payload_json, result_json, attempt_count,
                              created_at, updated_at
                            )
                            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                segment_id,
                                "profile-1",
                                "chat",
                                f"breakpoint-{segment_id}",
                                "pending",
                                payload_json,
                                result_json,
                                attempt_count,
                                "2026-07-25T00:00:00+00:00",
                                "2026-07-25T00:00:00+00:00",
                            ),
                        )

    def test_schema_has_expired_lease_recovery_index(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            columns = [
                row[2]
                for row in connection.execute(
                    "pragma index_info(idx_short_term_segments_profile_source_state_lease)"
                )
            ]

        self.assertEqual(
            columns,
            ["profile_id", "source_type", "state", "lease_expires_at"],
        )

    def _replace_schema_version(self, version):
        self.store.close()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                """
                update short_term_schema_info
                set value = ?
                where key = 'schema_version'
                """,
                (version,),
            )

    def _read_schema_version(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            row = connection.execute(
                """
                select value
                from short_term_schema_info
                where key = 'schema_version'
                """
            ).fetchone()
        return row[0]

    def _rewrite_table_schema(self, *, table_name, old, new):
        index_statements = {
            "short_term_messages": (
                """
                create index idx_short_term_messages_profile_source_sequence
                on short_term_messages(profile_id, source_type, sequence)
                """,
            ),
            "short_term_breakpoints": (
                """
                create index idx_short_term_breakpoints_profile_source_sequence
                on short_term_breakpoints(profile_id, source_type, message_sequence)
                """,
            ),
            "short_term_segments": (
                """
                create index idx_short_term_segments_profile_source_state_created
                on short_term_segments(profile_id, source_type, state, created_at)
                """,
                """
                create index idx_short_term_segments_profile_source_state_lease
                on short_term_segments(
                  profile_id, source_type, state, lease_expires_at
                )
                """,
            ),
        }
        self.store.close()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            row = connection.execute(
                """
                select sql
                from sqlite_master
                where type = 'table' and name = ?
                """,
                (table_name,),
            ).fetchone()
            self.assertIsNotNone(row)
            old_candidates = (old,) if isinstance(old, str) else old
            matched = next(
                (candidate for candidate in old_candidates if candidate in row[0]),
                None,
            )
            self.assertIsNotNone(matched)
            rewritten_sql = row[0].replace(matched, new, 1)

            connection.execute(f"drop table {table_name}")
            connection.execute(rewritten_sql)
            for statement in index_statements[table_name]:
                connection.execute(statement)

    def _assert_reopen_rejected(self, pattern):
        reopened = None
        try:
            with self.assertRaisesRegex(RuntimeError, pattern):
                reopened = SQLiteShortTermStore(self.path)
        finally:
            if reopened is not None:
                reopened.close()


class ShortTermModelAnnotationTests(unittest.TestCase):
    def test_optional_storage_fields_are_annotated_as_nullable(self):
        message_hints = get_type_hints(ShortTermMessage)
        breakpoint_hints = get_type_hints(ShortTermBreakpoint)
        summary_hints = get_type_hints(SummaryRequest)

        self.assertEqual(message_hints["source_id"], str | None)
        self.assertEqual(breakpoint_hints["topic"], str | None)
        self.assertEqual(breakpoint_hints["reason"], str | None)
        self.assertEqual(summary_hints["topic"], str | None)
        self.assertEqual(summary_hints["reason"], str | None)


class ShortTermPolicyTests(unittest.TestCase):
    def test_policy_requires_positive_integer_thresholds(self):
        invalid_values = (0, -1, True, False, 0.5, 1.5)

        for field in ("max_breakpoints", "max_chars"):
            for value in invalid_values:
                with self.subTest(field=field, value=value):
                    kwargs = {"max_breakpoints": 1, "max_chars": 1, field: value}
                    with self.assertRaisesRegex(
                        ValueError,
                        rf"^{field} must be a positive integer$",
                    ):
                        ShortTermPolicy(**kwargs)

        policy = ShortTermPolicy(max_breakpoints=1, max_chars=2)
        self.assertEqual(policy.max_breakpoints, 1)
        self.assertEqual(policy.max_chars, 2)

    def test_policy_triggers_on_breakpoint_or_context_limit(self):
        policy = ShortTermPolicy(max_breakpoints=2, max_chars=20)

        self.assertTrue(
            policy.should_prepare(BufferStats(message_count=3, breakpoint_count=2, char_count=10))
        )
        self.assertTrue(
            policy.should_prepare(BufferStats(message_count=3, breakpoint_count=1, char_count=20))
        )
        self.assertFalse(
            policy.should_prepare(BufferStats(message_count=3, breakpoint_count=1, char_count=19))
        )

    def test_summary_request_serializes_immutable_messages(self):
        request = SummaryRequest(
            segment_id="segment-1",
            profile_id="profile-1",
            source_type="chat",
            breakpoint_id="breakpoint-1",
            topic="coffee",
            reason="topic_changed",
            messages=(
                ShortTermMessage(
                    id="message-1",
                    sequence=1,
                    role="user",
                    content="喜欢拿铁",
                    source_id="source-1",
                    created_at="2026-07-25T00:00:00+00:00",
                ),
            ),
            state=SegmentState.PENDING,
            attempt_count=0,
            created_at="2026-07-25T00:00:01+00:00",
        )

        payload = request.to_dict()

        self.assertEqual(payload["segment_id"], "segment-1")
        self.assertEqual(payload["handoff"], "chat_agent_summary_required")
        self.assertIn("喜欢拿铁", payload["content"])


class SummaryRequestTests(unittest.TestCase):
    def _message(self):
        return ShortTermMessage(
            id="message-1",
            sequence=1,
            role="user",
            content="喜欢拿铁",
            source_id="source-1",
            created_at="2026-07-25T00:00:00+00:00",
        )

    def _request(self, messages):
        return SummaryRequest(
            segment_id="segment-1",
            profile_id="profile-1",
            source_type="chat",
            breakpoint_id="breakpoint-1",
            topic="coffee",
            reason="topic_changed",
            messages=messages,
            state=SegmentState.PENDING,
            attempt_count=0,
            created_at="2026-07-25T00:00:01+00:00",
        )

    def test_messages_are_copied_to_an_immutable_tuple(self):
        source_messages = [self._message()]

        request = self._request(source_messages)
        source_messages.clear()

        self.assertIsInstance(request.messages, tuple)
        self.assertEqual(request.messages, (self._message(),))

    def test_request_fields_cannot_be_reassigned(self):
        request = self._request([self._message()])

        with self.assertRaises(FrozenInstanceError):
            request.messages = ()

    def test_messages_reject_invalid_members(self):
        with self.assertRaisesRegex(
            TypeError,
            "^messages must contain only ShortTermMessage instances$",
        ):
            self._request([self._message(), "not-a-message"])


class ShortTermCompatibilityTests(unittest.TestCase):
    def test_now_iso_remains_importable_from_short_term_package(self):
        from backend.memory.short_term import now_iso
        from backend.memory.short_term.buffer import now_iso as buffer_now_iso

        self.assertIs(now_iso, buffer_now_iso)
