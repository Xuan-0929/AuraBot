from contextlib import closing
from dataclasses import FrozenInstanceError
import gc
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import stat
import tempfile
from threading import Barrier, BrokenBarrierError, Event, Lock, Thread
from typing import get_type_hints
import unittest
from unittest.mock import Mock, patch
import weakref

from backend.memory.short_term.buffer import ShortTermMemoryBuffer
from backend.memory.short_term.models import (
    BufferStats,
    SegmentState,
    ShortTermBreakpoint,
    ShortTermMessage,
    SummaryRequest,
)
from backend.memory.short_term.errors import ShortTermStateError
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

    def _durable_short_term_state(self):
        with closing(sqlite3.connect(self.path)) as connection:
            messages = connection.execute(
                """
                select
                  id, sequence, profile_id, source_type,
                  role, content, source_id, created_at
                from short_term_messages
                order by sequence
                """
            ).fetchall()
            breakpoints = connection.execute(
                """
                select
                  id, profile_id, source_type, message_sequence,
                  topic, reason, created_at
                from short_term_breakpoints
                order by id
                """
            ).fetchall()
            segments = connection.execute(
                """
                select
                  id, profile_id, source_type, breakpoint_id,
                  state, payload_json, result_json, attempt_count,
                  last_error, lease_owner, lease_expires_at,
                  created_at, updated_at, completed_at
                from short_term_segments
                order by id
                """
            ).fetchall()
        return messages, breakpoints, segments

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

    def test_new_direct_database_is_private_under_permissive_umask(self):
        self.store.close()
        path = Path(self.tmpdir.name) / "private.sqlite3"
        previous_umask = os.umask(0o022)
        store = None
        try:
            store = SQLiteShortTermStore(path)
        finally:
            os.umask(previous_umask)
        try:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        finally:
            if store is not None:
                store.close()

    def test_existing_direct_database_is_tightened_without_truncating_data(self):
        self.store.append_message(
            profile_id="profile-1",
            source_type="chat",
            role="user",
            content="existing bytes",
            source_id=None,
        )
        self.store.close()
        os.chmod(self.path, 0o644)
        before = self.path.read_bytes()

        self.store = SQLiteShortTermStore(self.path)
        self.store.close()

        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        after = self.path.read_bytes()
        self.assertEqual(after[:16], before[:16])
        self.assertEqual(len(after), len(before))
        with closing(sqlite3.connect(self.path)) as connection:
            columns = {
                row[1]
                for row in connection.execute(
                    "pragma table_info(short_term_messages)"
                )
            }
            count = connection.execute(
                "select count(*) from short_term_messages"
            ).fetchone()[0]
        self.assertIn("source_type", columns)
        self.assertEqual(count, 1)

    def test_direct_database_symlink_is_rejected_without_touching_target(self):
        self.store.close()
        target = Path(self.tmpdir.name) / "target.sqlite3"
        target.write_bytes(b"not a database")
        link = Path(self.tmpdir.name) / "linked.sqlite3"
        if not hasattr(os, "symlink"):
            self.skipTest("symlink creation is not supported")
        try:
            os.symlink(target, link)
        except (NotImplementedError, OSError) as error:
            self.skipTest(f"symlink creation is unavailable: {error}")

        with self.assertRaisesRegex(ValueError, "symlink"):
            SQLiteShortTermStore(link)

        self.assertEqual(target.read_bytes(), b"not a database")
        self.assertTrue(link.is_symlink())

    def test_concurrent_direct_database_constructors_share_private_file(self):
        self.store.close()
        path = Path(self.tmpdir.name) / "concurrent.sqlite3"
        start = Barrier(3)
        stores = []
        errors = []
        result_lock = Lock()
        previous_umask = os.umask(0o022)

        def construct():
            try:
                start.wait(timeout=5)
                store = SQLiteShortTermStore(path)
                with result_lock:
                    stores.append(store)
            except BaseException as error:
                with result_lock:
                    errors.append(error)

        threads = [Thread(target=construct) for _ in range(2)]
        try:
            for thread in threads:
                thread.start()
            start.wait(timeout=5)
            for thread in threads:
                thread.join(timeout=5)
        finally:
            os.umask(previous_umask)
            for store in stores:
                store.close()

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(stores), 2)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

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

    def test_prepare_oldest_segment_keeps_newer_topic_active(self):
        coffee_messages = [
            self.store.append_message(
                profile_id="profile-1",
                source_type="chat",
                role="user",
                content="想喝咖啡",
                source_id="chat-1",
            ),
            self.store.append_message(
                profile_id="profile-1",
                source_type="chat",
                role="assistant",
                content="可以试试拿铁",
                source_id="chat-2",
            ),
        ]
        coffee_breakpoint = self.store.add_breakpoint(
            profile_id="profile-1",
            source_type="chat",
            topic="coffee",
            reason="topic_changed",
        )
        project_messages = [
            self.store.append_message(
                profile_id="profile-1",
                source_type="chat",
                role="user",
                content="项目进度怎么样",
                source_id="chat-3",
            ),
            self.store.append_message(
                profile_id="profile-1",
                source_type="chat",
                role="assistant",
                content="正在实现短期记忆",
                source_id="chat-4",
            ),
        ]
        project_breakpoint = self.store.add_breakpoint(
            profile_id="profile-1",
            source_type="chat",
            topic="project",
            reason="topic_changed",
        )

        request = self.store.prepare_oldest_segment(
            profile_id="profile-1",
            source_type="chat",
        )

        self.assertIsNotNone(request)
        self.assertEqual(request.state, SegmentState.PENDING)
        self.assertEqual(request.topic, "coffee")
        self.assertEqual(request.breakpoint_id, coffee_breakpoint.id)
        self.assertEqual(request.messages, tuple(coffee_messages))
        self.assertEqual(
            self.store.list_messages("profile-1", "chat"),
            project_messages,
        )
        remaining_breakpoints = self.store.list_breakpoints("profile-1", "chat")
        self.assertEqual([item.id for item in remaining_breakpoints], [project_breakpoint.id])
        self.assertEqual(
            [item.message_index for item in remaining_breakpoints],
            [len(project_messages)],
        )

    def test_prepare_requires_a_complete_breakpoint(self):
        message = self.store.append_message(
            profile_id="profile-1",
            source_type="chat",
            role="user",
            content="还在当前话题",
            source_id=None,
        )

        request = self.store.prepare_oldest_segment(
            profile_id="profile-1",
            source_type="chat",
        )

        self.assertIsNone(request)
        self.assertEqual(self.store.list_messages("profile-1", "chat"), [message])

    def test_same_breakpoint_never_creates_duplicate_segments(self):
        self.store.append_message(
            profile_id="profile-1",
            source_type="chat",
            role="user",
            content="单段消息",
            source_id=None,
        )
        breakpoint = self.store.add_breakpoint(
            profile_id="profile-1",
            source_type="chat",
            topic="single",
            reason=None,
        )

        first = self.store.prepare_oldest_segment(
            profile_id="profile-1",
            source_type="chat",
        )
        second = self.store.prepare_oldest_segment(
            profile_id="profile-1",
            source_type="chat",
        )

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        with closing(sqlite3.connect(self.path)) as connection:
            count = connection.execute(
                """
                select count(*)
                from short_term_segments
                where breakpoint_id = ?
                """,
                (breakpoint.id,),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_prepare_oldest_segment_isolates_exact_profile_and_source(self):
        profile_one_chat = self.store.append_message(
            profile_id="profile-1",
            source_type="chat",
            role="user",
            content="profile-1 chat",
            source_id=None,
        )
        profile_one_chat_breakpoint = self.store.add_breakpoint(
            profile_id="profile-1",
            source_type="chat",
            topic="target",
            reason=None,
        )
        profile_two_chat = self.store.append_message(
            profile_id="profile-2",
            source_type="chat",
            role="user",
            content="profile-2 chat",
            source_id=None,
        )
        profile_two_chat_breakpoint = self.store.add_breakpoint(
            profile_id="profile-2",
            source_type="chat",
            topic="other-profile",
            reason=None,
        )
        profile_one_scheduler = self.store.append_message(
            profile_id="profile-1",
            source_type="scheduler",
            role="system",
            content="profile-1 scheduler",
            source_id="job-1",
        )
        profile_one_scheduler_breakpoint = self.store.add_breakpoint(
            profile_id="profile-1",
            source_type="scheduler",
            topic="other-source",
            reason=None,
        )

        request = self.store.prepare_oldest_segment(
            profile_id="profile-1",
            source_type="chat",
        )

        self.assertEqual(request.messages, (profile_one_chat,))
        self.assertEqual(request.breakpoint_id, profile_one_chat_breakpoint.id)
        self.assertEqual(self.store.list_messages("profile-1", "chat"), [])
        self.assertEqual(self.store.list_breakpoints("profile-1", "chat"), [])
        self.assertEqual(
            self.store.list_messages("profile-2", "chat"),
            [profile_two_chat],
        )
        self.assertEqual(
            self.store.list_breakpoints("profile-2", "chat"),
            [profile_two_chat_breakpoint],
        )
        self.assertEqual(
            self.store.list_messages("profile-1", "scheduler"),
            [profile_one_scheduler],
        )
        self.assertEqual(
            self.store.list_breakpoints("profile-1", "scheduler"),
            [profile_one_scheduler_breakpoint],
        )

    def test_prepare_preserves_payload_and_pending_row_durably(self):
        messages = [
            self.store.append_message(
                profile_id="profile-1",
                source_type="chat",
                role="user",
                content="保留原文",
                source_id=None,
            ),
            self.store.append_message(
                profile_id="profile-1",
                source_type="chat",
                role="assistant",
                content="with source",
                source_id="source-2",
            ),
        ]
        breakpoint = self.store.add_breakpoint(
            profile_id="profile-1",
            source_type="chat",
            topic=None,
            reason=None,
        )

        request = self.store.prepare_oldest_segment(
            profile_id="profile-1",
            source_type="chat",
        )

        self.assertTrue(request.segment_id.startswith("segment_"))
        self.assertEqual(request.profile_id, "profile-1")
        self.assertEqual(request.source_type, "chat")
        self.assertEqual(request.breakpoint_id, breakpoint.id)
        self.assertIsNone(request.topic)
        self.assertIsNone(request.reason)
        self.assertEqual(request.messages, tuple(messages))
        self.assertEqual(request.state, SegmentState.PENDING)
        self.assertEqual(request.attempt_count, 0)
        self.assertIsNone(request.lease_expires_at)

        with closing(sqlite3.connect(self.path)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                select *
                from short_term_segments
                where id = ?
                """,
                (request.segment_id,),
            ).fetchone()
        payload_json = row["payload_json"]
        payload = json.loads(payload_json)

        self.assertIn("保留原文", payload_json)
        self.assertEqual(
            set(payload),
            {
                "segment_id",
                "profile_id",
                "source_type",
                "breakpoint_id",
                "topic",
                "reason",
                "messages",
                "content",
                "message_count",
                "state",
                "attempt_count",
                "created_at",
                "lease_expires_at",
                "handoff",
            },
        )
        self.assertIsInstance(payload["messages"], list)
        self.assertEqual(len(payload["messages"]), 2)
        self.assertEqual(
            set(payload["messages"][0]),
            {"id", "sequence", "role", "content", "source_id", "created_at"},
        )
        self.assertEqual(
            payload["messages"],
            [
                {
                    "id": messages[0].id,
                    "sequence": messages[0].sequence,
                    "role": "user",
                    "content": "保留原文",
                    "source_id": None,
                    "created_at": messages[0].created_at,
                },
                {
                    "id": messages[1].id,
                    "sequence": messages[1].sequence,
                    "role": "assistant",
                    "content": "with source",
                    "source_id": "source-2",
                    "created_at": messages[1].created_at,
                },
            ],
        )
        self.assertEqual(payload["segment_id"], request.segment_id)
        self.assertEqual(payload["profile_id"], "profile-1")
        self.assertEqual(payload["source_type"], "chat")
        self.assertEqual(payload["breakpoint_id"], breakpoint.id)
        self.assertIsNone(payload["topic"])
        self.assertIsNone(payload["reason"])
        self.assertEqual(
            payload["content"],
            "user: 保留原文\nassistant: with source",
        )
        self.assertEqual(payload["message_count"], 2)
        self.assertEqual(payload["state"], "pending")
        self.assertEqual(payload["attempt_count"], 0)
        self.assertEqual(payload["created_at"], request.created_at)
        self.assertIsNone(payload["lease_expires_at"])
        self.assertEqual(payload["handoff"], "chat_agent_summary_required")

        reconstructed = SummaryRequest(
            segment_id=payload["segment_id"],
            profile_id=payload["profile_id"],
            source_type=payload["source_type"],
            breakpoint_id=payload["breakpoint_id"],
            topic=payload["topic"],
            reason=payload["reason"],
            messages=tuple(
                ShortTermMessage(**message) for message in payload["messages"]
            ),
            state=SegmentState(payload["state"]),
            attempt_count=payload["attempt_count"],
            created_at=payload["created_at"],
            lease_expires_at=payload["lease_expires_at"],
        )
        self.assertEqual(reconstructed, request)
        self.assertEqual(row["profile_id"], "profile-1")
        self.assertEqual(row["source_type"], "chat")
        self.assertEqual(row["breakpoint_id"], breakpoint.id)
        self.assertEqual(row["state"], "pending")
        self.assertEqual(row["result_json"], "{}")
        self.assertEqual(row["attempt_count"], 0)
        self.assertIsNone(row["last_error"])
        self.assertIsNone(row["lease_owner"])
        self.assertIsNone(row["lease_expires_at"])
        self.assertEqual(row["created_at"], request.created_at)
        self.assertEqual(row["updated_at"], request.created_at)
        self.assertIsNone(row["completed_at"])

        self.store.append_message(
            profile_id="profile-1",
            source_type="chat",
            role="user",
            content="新的活动消息",
            source_id=None,
        )
        with closing(sqlite3.connect(self.path)) as connection:
            stored_payload = connection.execute(
                """
                select payload_json
                from short_term_segments
                where id = ?
                """,
                (request.segment_id,),
            ).fetchone()[0]
        self.assertEqual(stored_payload, payload_json)
        self.assertNotIn("新的活动消息", stored_payload)

    def test_prepare_rejects_persisted_breakpoint_without_messages(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                """
                insert into short_term_breakpoints (
                  id, profile_id, source_type, message_sequence,
                  topic, reason, created_at
                )
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "orphan-breakpoint",
                    "profile-1",
                    "chat",
                    999,
                    "orphan",
                    None,
                    "2026-07-25T00:00:00+00:00",
                ),
            )

        with self.assertRaisesRegex(RuntimeError, "no matching messages"):
            self.store.prepare_oldest_segment(
                profile_id="profile-1",
                source_type="chat",
            )

        self.assertEqual(
            [item.id for item in self.store.list_breakpoints("profile-1", "chat")],
            ["orphan-breakpoint"],
        )

    def test_prepare_rolls_back_when_segment_breakpoint_is_duplicate(self):
        message = self.store.append_message(
            profile_id="profile-1",
            source_type="chat",
            role="user",
            content="必须保留",
            source_id=None,
        )
        breakpoint = self.store.add_breakpoint(
            profile_id="profile-1",
            source_type="chat",
            topic="rollback",
            reason="test",
        )
        with closing(sqlite3.connect(self.path)) as connection, connection:
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
                    "conflicting-segment",
                    "profile-1",
                    "chat",
                    breakpoint.id,
                    "pending",
                    json.dumps({"valid": True}),
                    "{}",
                    0,
                    "2026-07-25T00:00:00+00:00",
                    "2026-07-25T00:00:00+00:00",
                ),
            )

        with self.assertRaises(sqlite3.IntegrityError):
            self.store.prepare_oldest_segment(
                profile_id="profile-1",
                source_type="chat",
            )

        self.assertEqual(self.store.list_messages("profile-1", "chat"), [message])
        self.assertEqual(
            self.store.list_breakpoints("profile-1", "chat"),
            [breakpoint],
        )
        with closing(sqlite3.connect(self.path)) as connection:
            segment_count = connection.execute(
                """
                select count(*)
                from short_term_segments
                where breakpoint_id = ?
                """,
                (breakpoint.id,),
            ).fetchone()[0]
        self.assertEqual(segment_count, 1)

    def test_prepare_rejects_future_breakpoint_boundary_without_changes(self):
        self.store.append_message(
            profile_id="profile-1",
            source_type="chat",
            role="user",
            content="must remain active",
            source_id=None,
        )
        breakpoint = self.store.add_breakpoint(
            profile_id="profile-1",
            source_type="chat",
            topic="future-boundary",
            reason=None,
        )
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                """
                update short_term_breakpoints
                set message_sequence = ?
                where id = ?
                """,
                (999, breakpoint.id),
            )
        before = self._durable_short_term_state()

        with self.assertRaisesRegex(
            RuntimeError,
            "inconsistent breakpoint boundary",
        ):
            self.store.prepare_oldest_segment(
                profile_id="profile-1",
                source_type="chat",
            )

        after = self._durable_short_term_state()
        self.assertEqual(after, before)
        self.assertEqual(len(after[0]), 1)
        self.assertEqual(len(after[1]), 1)
        self.assertEqual(len(after[2]), 0)

    def test_prepare_rejects_cross_stream_breakpoint_boundary_without_changes(self):
        self.store.append_message(
            profile_id="profile-1",
            source_type="chat",
            role="user",
            content="target stream",
            source_id=None,
        )
        breakpoint = self.store.add_breakpoint(
            profile_id="profile-1",
            source_type="chat",
            topic="cross-stream-boundary",
            reason=None,
        )
        other_stream_message = self.store.append_message(
            profile_id="profile-2",
            source_type="chat",
            role="user",
            content="other stream",
            source_id=None,
        )
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                """
                update short_term_breakpoints
                set message_sequence = ?
                where id = ?
                """,
                (other_stream_message.sequence, breakpoint.id),
            )
        before = self._durable_short_term_state()

        with self.assertRaisesRegex(
            RuntimeError,
            "inconsistent breakpoint boundary",
        ):
            self.store.prepare_oldest_segment(
                profile_id="profile-1",
                source_type="chat",
            )

        after = self._durable_short_term_state()
        self.assertEqual(after, before)
        self.assertEqual(len(after[0]), 2)
        self.assertEqual(len(after[1]), 1)
        self.assertEqual(len(after[2]), 0)

    def test_two_stores_prepare_one_segment_exactly_once(self):
        message = self.store.append_message(
            profile_id="profile-1",
            source_type="chat",
            role="user",
            content="prepare once",
            source_id=None,
        )
        breakpoint = self.store.add_breakpoint(
            profile_id="profile-1",
            source_type="chat",
            topic="concurrent",
            reason=None,
        )
        competing_store = SQLiteShortTermStore(self.path)
        self.addCleanup(competing_store.close)
        barrier = Barrier(3)
        result_lock = Lock()
        results = []
        errors = []

        def prepare(store):
            try:
                barrier.wait(timeout=5)
                result = store.prepare_oldest_segment(
                    profile_id="profile-1",
                    source_type="chat",
                )
                with result_lock:
                    results.append(result)
            except Exception as error:
                with result_lock:
                    errors.append(error)

        threads = [
            Thread(target=prepare, args=(store,))
            for store in (self.store, competing_store)
        ]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=5)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        prepared = [result for result in results if result is not None]
        self.assertEqual(len(prepared), 1)
        self.assertEqual(prepared[0].messages, (message,))
        self.assertEqual(prepared[0].breakpoint_id, breakpoint.id)
        self.assertEqual(sum(result is None for result in results), 1)

        durable_messages, durable_breakpoints, durable_segments = (
            self._durable_short_term_state()
        )
        self.assertEqual(durable_messages, [])
        self.assertEqual(durable_breakpoints, [])
        self.assertEqual(len(durable_segments), 1)
        self.assertEqual(durable_segments[0][3], breakpoint.id)

    def test_message_delete_failure_rolls_back_prepared_segment(self):
        self.store.append_message(
            profile_id="profile-1",
            source_type="chat",
            role="user",
            content="message delete rollback",
            source_id=None,
        )
        self.store.add_breakpoint(
            profile_id="profile-1",
            source_type="chat",
            topic="rollback",
            reason=None,
        )
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                """
                create trigger abort_short_term_message_delete
                before delete on short_term_messages
                begin
                  select raise(abort, 'message delete blocked');
                end
                """
            )
        before = self._durable_short_term_state()

        with self.assertRaisesRegex(
            sqlite3.IntegrityError,
            "message delete blocked",
        ):
            self.store.prepare_oldest_segment(
                profile_id="profile-1",
                source_type="chat",
            )

        after = self._durable_short_term_state()
        self.assertEqual(after, before)
        self.assertEqual(len(after[0]), 1)
        self.assertEqual(len(after[1]), 1)
        self.assertEqual(len(after[2]), 0)

    def test_breakpoint_delete_failure_restores_messages_and_segment(self):
        self.store.append_message(
            profile_id="profile-1",
            source_type="chat",
            role="user",
            content="breakpoint delete rollback",
            source_id=None,
        )
        self.store.add_breakpoint(
            profile_id="profile-1",
            source_type="chat",
            topic="rollback",
            reason=None,
        )
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                """
                create trigger abort_short_term_breakpoint_delete
                before delete on short_term_breakpoints
                begin
                  select raise(abort, 'breakpoint delete blocked');
                end
                """
            )
        before = self._durable_short_term_state()

        with self.assertRaisesRegex(
            sqlite3.IntegrityError,
            "breakpoint delete blocked",
        ):
            self.store.prepare_oldest_segment(
                profile_id="profile-1",
                source_type="chat",
            )

        after = self._durable_short_term_state()
        self.assertEqual(after, before)
        self.assertEqual(len(after[0]), 1)
        self.assertEqual(len(after[1]), 1)
        self.assertEqual(len(after[2]), 0)

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

    def test_fresh_schema_requires_complete_processing_leases(self):
        cases = (
            ("null-owner", None, "2026-07-25T00:01:00+00:00"),
            ("blank-owner", " ", "2026-07-25T00:01:00+00:00"),
            ("null-expiry", "worker", None),
        )

        for label, lease_owner, lease_expires_at in cases:
            with self.subTest(label=label):
                with closing(sqlite3.connect(self.path)) as connection, connection:
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            """
                            insert into short_term_segments (
                              id, profile_id, source_type, breakpoint_id, state,
                              payload_json, lease_owner, lease_expires_at,
                              created_at, updated_at
                            )
                            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                f"segment-{label}",
                                "profile-1",
                                "chat",
                                f"breakpoint-{label}",
                                "processing",
                                "{}",
                                lease_owner,
                                lease_expires_at,
                                "2026-07-25T00:00:00+00:00",
                                "2026-07-25T00:00:00+00:00",
                            ),
                        )

    def test_v1_schema_rejects_missing_processing_lease_check(self):
        self._rewrite_table_schema(
            table_name="short_term_segments",
            old=(
                "check (\n"
                "        state != 'processing'\n"
                "        or (\n"
                "          lease_owner is not null\n"
                "          and length(trim(lease_owner)) > 0\n"
                "          and lease_expires_at is not null\n"
                "        )\n"
                "      )"
            ),
            new="check (1)",
        )

        self._assert_reopen_rejected("effective CHECK.*processing lease")

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


class SQLiteShortTermDeliveryTests(unittest.TestCase):
    PROFILE_ID = "profile-1"
    SOURCE_TYPE = "chat"
    NOW = "2026-07-25T00:00:00+00:00"

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "delivery.sqlite3"
        self.store = SQLiteShortTermStore(self.path)

    def tearDown(self):
        self.store.close()
        self.tmpdir.cleanup()

    def _prepare_segment(
        self,
        *,
        profile_id=PROFILE_ID,
        source_type=SOURCE_TYPE,
        topic="coffee",
        reason="topic_changed",
        content="想喝咖啡",
    ):
        message = self.store.append_message(
            profile_id=profile_id,
            source_type=source_type,
            role="user",
            content=content,
            source_id="message-source",
        )
        self.store.add_breakpoint(
            profile_id=profile_id,
            source_type=source_type,
            topic=topic,
            reason=reason,
        )
        request = self.store.prepare_oldest_segment(
            profile_id=profile_id,
            source_type=source_type,
        )
        self.assertIsNotNone(request)
        return request, message

    def _segment_row(self, segment_id):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                select *
                from short_term_segments
                where id = ?
                """,
                (segment_id,),
            ).fetchone()
        return None if row is None else dict(row)

    def _update_segment(self, segment_id, **values):
        assignments = ", ".join(f"{column} = ?" for column in values)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                f"update short_term_segments set {assignments} where id = ?",
                (*values.values(), segment_id),
            )

    def _corrupt_segment(self, segment_id, **values):
        assignments = ", ".join(f"{column} = ?" for column in values)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("pragma ignore_check_constraints = on")
            connection.execute(
                f"update short_term_segments set {assignments} where id = ?",
                (*values.values(), segment_id),
            )

    def _payload(self, segment_id):
        return json.loads(self._segment_row(segment_id)["payload_json"])

    def _assert_cross_source_error_is_opaque(self, operation, *, state):
        before = self._segment_row(operation["segment_id"])

        with self.assertRaises(ShortTermStateError) as raised:
            operation["call"]()

        message = str(raised.exception)
        self.assertNotIn("source-only secret", message)
        self.assertNotIn(state, message)
        self.assertEqual(self._segment_row(operation["segment_id"]), before)

    def test_acknowledge_requires_exact_source_and_correct_source_succeeds(self):
        prepared, _ = self._prepare_segment(content="source-only secret")
        self._prepare_segment(
            source_type="scheduler",
            content="other source",
        )
        self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )

        self._assert_cross_source_error_is_opaque(
            {
                "segment_id": prepared.segment_id,
                "call": lambda: self.store.acknowledge_segment(
                    profile_id=self.PROFILE_ID,
                    source_type="scheduler",
                    segment_id=prepared.segment_id,
                    owner="main-agent",
                    outcome="remembered",
                    episode_id=None,
                    memory_ids=[],
                    reason=None,
                    now="2026-07-25T00:00:30+00:00",
                ),
            },
            state="processing",
        )

        completed = self.store.acknowledge_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=prepared.segment_id,
            owner="main-agent",
            outcome="remembered",
            episode_id=None,
            memory_ids=[],
            reason=None,
            now="2026-07-25T00:00:30+00:00",
        )
        self.assertEqual(completed.state, SegmentState.COMPLETED)

    def test_release_requires_exact_source_and_correct_source_succeeds(self):
        prepared, _ = self._prepare_segment(content="source-only secret")
        self._prepare_segment(
            source_type="scheduler",
            content="other source",
        )
        self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )

        self._assert_cross_source_error_is_opaque(
            {
                "segment_id": prepared.segment_id,
                "call": lambda: self.store.release_segment(
                    profile_id=self.PROFILE_ID,
                    source_type="scheduler",
                    segment_id=prepared.segment_id,
                    owner="main-agent",
                    error="temporary",
                    max_attempts=2,
                    now="2026-07-25T00:00:30+00:00",
                ),
            },
            state="processing",
        )

        released = self.store.release_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=prepared.segment_id,
            owner="main-agent",
            error="temporary",
            max_attempts=2,
            now="2026-07-25T00:00:30+00:00",
        )
        self.assertEqual(released.state, SegmentState.PENDING)

    def test_requeue_requires_exact_source_and_correct_source_succeeds(self):
        prepared, _ = self._prepare_segment(content="source-only secret")
        self._prepare_segment(
            source_type="scheduler",
            content="other source",
        )
        self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )
        self.store.release_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=prepared.segment_id,
            owner="main-agent",
            error="permanent",
            max_attempts=1,
            now="2026-07-25T00:00:30+00:00",
        )

        self._assert_cross_source_error_is_opaque(
            {
                "segment_id": prepared.segment_id,
                "call": lambda: self.store.requeue_failed_segment(
                    profile_id=self.PROFILE_ID,
                    source_type="scheduler",
                    segment_id=prepared.segment_id,
                ),
            },
            state="failed",
        )

        requeued = self.store.requeue_failed_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=prepared.segment_id,
        )
        self.assertEqual(requeued.state, SegmentState.PENDING)

    def test_acknowledge_and_release_require_live_lease_at_exact_instant(self):
        cases = (
            ("before", "2026-07-25T08:00:59.999999+08:00", True),
            ("equal", "2026-07-24T20:01:00-04:00", False),
            ("after", "2026-07-25T09:01:00+08:00", False),
        )
        for operation in ("acknowledge", "release"):
            for label, now, succeeds in cases:
                with self.subTest(operation=operation, timing=label):
                    profile_id = f"profile-{operation}-{label}"
                    prepared, _ = self._prepare_segment(profile_id=profile_id)
                    self.store.claim_next_segment(
                        profile_id=profile_id,
                        source_type=self.SOURCE_TYPE,
                        owner="stale-owner",
                        lease_seconds=60,
                        now=self.NOW,
                    )
                    before = self._segment_row(prepared.segment_id)

                    if operation == "acknowledge":
                        transition = lambda: self.store.acknowledge_segment(
                            profile_id=profile_id,
                            source_type=self.SOURCE_TYPE,
                            segment_id=prepared.segment_id,
                            owner="stale-owner",
                            outcome="remembered",
                            episode_id=None,
                            memory_ids=[],
                            reason=None,
                            now=now,
                        )
                    else:
                        transition = lambda: self.store.release_segment(
                            profile_id=profile_id,
                            source_type=self.SOURCE_TYPE,
                            segment_id=prepared.segment_id,
                            owner="stale-owner",
                            error="failed",
                            max_attempts=2,
                            now=now,
                        )

                    if succeeds:
                        result = transition()
                        self.assertIn(
                            result.state,
                            (SegmentState.COMPLETED, SegmentState.PENDING),
                        )
                        continue

                    with self.assertRaisesRegex(
                        ShortTermStateError,
                        "lease",
                    ):
                        transition()
                    self.assertEqual(
                        self._segment_row(prepared.segment_id),
                        before,
                    )
                    self.store.close()
                    self.store = SQLiteShortTermStore(self.path)
                    self.assertEqual(
                        self._segment_row(prepared.segment_id),
                        before,
                    )

    def test_completed_acknowledge_is_idempotent_after_original_lease_expiry(self):
        prepared, _ = self._prepare_segment()
        self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )
        first = self.store.acknowledge_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=prepared.segment_id,
            owner="main-agent",
            outcome="remembered",
            episode_id="episode-1",
            memory_ids=["memory-1"],
            reason=None,
            now="2026-07-25T00:00:30+00:00",
        )
        before = self._segment_row(prepared.segment_id)
        self.store.close()
        self.store = SQLiteShortTermStore(self.path)

        repeated = self.store.acknowledge_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=prepared.segment_id,
            owner="retry-owner",
            outcome="discarded",
            episode_id=None,
            memory_ids=[],
            reason="late",
            now="2026-07-25T00:02:00+00:00",
        )

        self.assertEqual(repeated, first)
        self.assertEqual(self._segment_row(prepared.segment_id), before)

    def test_claim_and_owner_scoped_acknowledge_are_idempotent(self):
        prepared, _ = self._prepare_segment()

        claimed = self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )

        self.assertEqual(claimed.state, SegmentState.PROCESSING)
        self.assertEqual(claimed.attempt_count, 1)
        self.assertEqual(
            claimed.lease_expires_at,
            "2026-07-25T00:01:00+00:00",
        )
        claimed_row = self._segment_row(prepared.segment_id)
        self.assertEqual(claimed_row["lease_owner"], "main-agent")

        completed = self.store.acknowledge_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=prepared.segment_id,
            owner="main-agent",
            outcome="remembered",
            episode_id="episode-1",
            memory_ids=["memory-1", "memory-2"],
            reason=None,
            now=self.NOW,
        )
        first_row = self._segment_row(prepared.segment_id)

        self.assertEqual(completed.state, SegmentState.COMPLETED)
        self.assertEqual(
            json.loads(first_row["result_json"]),
            {
                "episode_id": "episode-1",
                "memory_ids": ["memory-1", "memory-2"],
                "outcome": "remembered",
                "reason": None,
            },
        )
        self.assertIsNotNone(first_row["completed_at"])
        self.assertIsNone(first_row["lease_owner"])
        self.assertIsNone(first_row["lease_expires_at"])
        self.assertIsNone(first_row["last_error"])

        repeated = self.store.acknowledge_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=prepared.segment_id,
            owner="different-retry-owner",
            outcome="discarded",
            episode_id=None,
            memory_ids=[],
            reason="late response",
            now=self.NOW,
        )
        repeated_row = self._segment_row(prepared.segment_id)

        self.assertEqual(repeated, completed)
        self.assertEqual(repeated_row["result_json"], first_row["result_json"])
        self.assertEqual(repeated_row["completed_at"], first_row["completed_at"])
        self.assertIsNone(
            self.store.claim_next_segment(
                profile_id=self.PROFILE_ID,
                source_type=self.SOURCE_TYPE,
                owner="main-agent",
                lease_seconds=60,
                now="2026-07-25T00:02:00+00:00",
            )
        )

    def test_release_retries_then_marks_segment_failed(self):
        prepared, _ = self._prepare_segment()
        first_claim = self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )

        pending = self.store.release_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=prepared.segment_id,
            owner="main-agent",
            error="temporary failure",
            max_attempts=2,
            now=self.NOW,
        )

        self.assertEqual(pending.state, SegmentState.PENDING)
        self.assertEqual(pending.attempt_count, first_claim.attempt_count)
        pending_row = self._segment_row(prepared.segment_id)
        self.assertEqual(pending_row["last_error"], "temporary failure")
        self.assertIsNone(pending_row["lease_owner"])
        self.assertIsNone(pending_row["lease_expires_at"])

        second_claim = self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now="2026-07-25T00:02:00+00:00",
        )
        self.assertEqual(
            self._segment_row(prepared.segment_id)["last_error"],
            "temporary failure",
        )
        failed = self.store.release_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=prepared.segment_id,
            owner="main-agent",
            error="permanent failure",
            max_attempts=2,
            now=self.NOW,
        )

        self.assertEqual(second_claim.attempt_count, 2)
        self.assertEqual(failed.state, SegmentState.FAILED)
        self.assertEqual(failed.attempt_count, 2)
        failed_row = self._segment_row(prepared.segment_id)
        self.assertEqual(failed_row["last_error"], "permanent failure")
        self.assertIsNone(failed_row["lease_owner"])
        self.assertIsNone(failed_row["lease_expires_at"])

    def test_expired_processing_lease_can_be_claimed_by_another_owner(self):
        prepared, _ = self._prepare_segment()
        first = self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="dead-worker",
            lease_seconds=10,
            now=self.NOW,
        )

        reclaimed = self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now="2026-07-25T00:01:00+00:00",
        )

        self.assertEqual(first.segment_id, prepared.segment_id)
        self.assertEqual(reclaimed.segment_id, prepared.segment_id)
        self.assertEqual(reclaimed.state, SegmentState.PROCESSING)
        self.assertEqual(reclaimed.attempt_count, 2)
        self.assertEqual(
            reclaimed.lease_expires_at,
            "2026-07-25T00:02:00+00:00",
        )
        row = self._segment_row(prepared.segment_id)
        self.assertEqual(row["lease_owner"], "main-agent")

    def test_claim_recovery_preserves_full_microsecond_precision(self):
        prepared, _ = self._prepare_segment()
        self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="original-owner",
            lease_seconds=60,
            now=self.NOW,
        )
        self._update_segment(
            prepared.segment_id,
            lease_expires_at="2026-07-25T00:01:00.123456+00:00",
        )
        before = self._segment_row(prepared.segment_id)

        early_claim = self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="early-owner",
            lease_seconds=60,
            now="2026-07-25T00:01:00.123455+00:00",
        )

        self.assertIsNone(early_claim)
        self.assertEqual(self._segment_row(prepared.segment_id), before)

        exact_claim = self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="exact-owner",
            lease_seconds=60,
            now="2026-07-25T00:01:00.123456+00:00",
        )

        self.assertEqual(exact_claim.segment_id, prepared.segment_id)
        self.assertEqual(exact_claim.attempt_count, 2)
        exact_row = self._segment_row(prepared.segment_id)
        self.assertEqual(exact_row["lease_owner"], "exact-owner")
        self.assertEqual(exact_row["attempt_count"], 2)

    def test_recover_expired_lease_compares_equivalent_offset_instants(self):
        prepared, _ = self._prepare_segment()
        self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="offset-owner",
            lease_seconds=60,
            now=self.NOW,
        )
        self._update_segment(
            prepared.segment_id,
            lease_expires_at="2026-07-25T08:01:00.123456+08:00",
        )

        recovered = self.store.recover_expired_leases(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            now="2026-07-24T20:01:00.123456-04:00",
        )

        self.assertEqual(recovered, 1)
        row = self._segment_row(prepared.segment_id)
        self.assertEqual(row["state"], "pending")
        self.assertEqual(row["attempt_count"], 1)
        self.assertIsNone(row["lease_owner"])
        self.assertIsNone(row["lease_expires_at"])

    def test_invalid_persisted_lease_rolls_back_all_recovery_paths(self):
        corrupt_leases = (
            ("null-owner", {"lease_owner": None}, "lease_owner"),
            ("blank-owner", {"lease_owner": " "}, "lease_owner"),
            ("null-expiry", {"lease_expires_at": None}, "lease_expires_at"),
            (
                "malformed-expiry",
                {"lease_expires_at": "not-a-timestamp"},
                "lease_expires_at",
            ),
            (
                "naive-expiry",
                {"lease_expires_at": "2026-07-25T00:00:30.000000"},
                "lease_expires_at",
            ),
        )
        for operation in ("explicit", "claim"):
            for label, corruption, error_field in corrupt_leases:
                with self.subTest(operation=operation, timestamp=label):
                    profile_id = f"profile-{operation}-{label}"
                    valid, _ = self._prepare_segment(
                        profile_id=profile_id,
                        content="valid expired lease",
                    )
                    invalid, _ = self._prepare_segment(
                        profile_id=profile_id,
                        content="invalid lease",
                    )
                    for owner in ("valid-owner", "invalid-owner"):
                        self.store.claim_next_segment(
                            profile_id=profile_id,
                            source_type=self.SOURCE_TYPE,
                            owner=owner,
                            lease_seconds=60,
                            now=self.NOW,
                        )
                    self._update_segment(
                        valid.segment_id,
                        lease_expires_at="2026-07-25T00:00:30+00:00",
                    )
                    self._corrupt_segment(
                        invalid.segment_id,
                        **corruption,
                    )
                    before = {
                        valid.segment_id: self._segment_row(valid.segment_id),
                        invalid.segment_id: self._segment_row(invalid.segment_id),
                    }

                    with self.assertRaisesRegex(
                        RuntimeError,
                        error_field,
                    ):
                        if operation == "explicit":
                            self.store.recover_expired_leases(
                                profile_id=profile_id,
                                source_type=self.SOURCE_TYPE,
                                now="2026-07-25T00:01:00+00:00",
                            )
                        else:
                            self.store.claim_next_segment(
                                profile_id=profile_id,
                                source_type=self.SOURCE_TYPE,
                                owner="replacement-owner",
                                lease_seconds=60,
                                now="2026-07-25T00:01:00+00:00",
                            )

                    after = {
                        valid.segment_id: self._segment_row(valid.segment_id),
                        invalid.segment_id: self._segment_row(invalid.segment_id),
                    }
                    self.assertEqual(after, before)

    def test_recovery_and_claim_race_preserves_one_processing_claim(self):
        prepared, _ = self._prepare_segment()
        self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="expired-owner",
            lease_seconds=10,
            now=self.NOW,
        )
        competing_store = SQLiteShortTermStore(self.path)
        self.addCleanup(competing_store.close)
        barrier = Barrier(2)
        result_lock = Lock()
        results = {}
        errors = []

        def recover():
            try:
                barrier.wait()
                recovered = self.store.recover_expired_leases(
                    profile_id=self.PROFILE_ID,
                    source_type=self.SOURCE_TYPE,
                    now="2026-07-25T00:01:00+00:00",
                )
                with result_lock:
                    results["recovered"] = recovered
            except BaseException as error:
                with result_lock:
                    errors.append(error)

        def claim():
            try:
                barrier.wait()
                claimed = competing_store.claim_next_segment(
                    profile_id=self.PROFILE_ID,
                    source_type=self.SOURCE_TYPE,
                    owner="replacement-owner",
                    lease_seconds=60,
                    now="2026-07-25T00:01:00+00:00",
                )
                with result_lock:
                    results["claimed"] = claimed
            except BaseException as error:
                with result_lock:
                    errors.append(error)

        threads = [Thread(target=recover), Thread(target=claim)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertIn(results["recovered"], (0, 1))
        self.assertEqual(results["claimed"].segment_id, prepared.segment_id)
        self.assertEqual(results["claimed"].attempt_count, 2)
        row = self._segment_row(prepared.segment_id)
        self.assertEqual(row["state"], "processing")
        self.assertEqual(row["lease_owner"], "replacement-owner")
        self.assertEqual(row["attempt_count"], 2)

    def test_recover_expired_leases_only_changes_exact_stream(self):
        expired, _ = self._prepare_segment(content="expired")
        future, _ = self._prepare_segment(content="future")
        other_source, _ = self._prepare_segment(
            source_type="scheduler",
            content="other source",
        )
        for request, owner in (
            (expired, "expired-owner"),
            (future, "future-owner"),
        ):
            claimed = self.store.claim_next_segment(
                profile_id=request.profile_id,
                source_type=request.source_type,
                owner=owner,
                lease_seconds=60,
                now=self.NOW,
            )
            self.assertEqual(claimed.segment_id, request.segment_id)
        self.store.claim_next_segment(
            profile_id=other_source.profile_id,
            source_type=other_source.source_type,
            owner="other-owner",
            lease_seconds=10,
            now=self.NOW,
        )
        self._update_segment(
            expired.segment_id,
            lease_expires_at="2026-07-25T00:00:30+00:00",
        )
        self._update_segment(
            future.segment_id,
            lease_expires_at="2026-07-25T00:02:00+00:00",
        )

        recovered = self.store.recover_expired_leases(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            now="2026-07-25T00:01:00+00:00",
        )

        self.assertEqual(recovered, 1)
        expired_row = self._segment_row(expired.segment_id)
        self.assertEqual(expired_row["state"], "pending")
        self.assertEqual(expired_row["attempt_count"], 1)
        self.assertIsNone(expired_row["lease_owner"])
        self.assertIsNone(expired_row["lease_expires_at"])
        self.assertEqual(self._segment_row(future.segment_id)["state"], "processing")
        self.assertEqual(
            self._segment_row(other_source.segment_id)["state"],
            "processing",
        )

    def test_owner_mismatch_does_not_change_durable_segment(self):
        prepared, _ = self._prepare_segment()
        self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )

        for operation in ("ack", "release"):
            with self.subTest(operation=operation):
                before = self._segment_row(prepared.segment_id)
                with self.assertRaises(ShortTermStateError):
                    if operation == "ack":
                        self.store.acknowledge_segment(
                            profile_id=self.PROFILE_ID,
                            source_type=self.SOURCE_TYPE,
                            segment_id=prepared.segment_id,
                            owner="wrong-owner",
                            outcome="remembered",
                            episode_id=None,
                            memory_ids=[],
                            reason=None,
                            now=self.NOW,
                        )
                    else:
                        self.store.release_segment(
                            profile_id=self.PROFILE_ID,
                            source_type=self.SOURCE_TYPE,
                            segment_id=prepared.segment_id,
                            owner="wrong-owner",
                            error="failed",
                            max_attempts=2,
                            now=self.NOW,
                        )
                self.assertEqual(self._segment_row(prepared.segment_id), before)

    def test_cross_profile_and_missing_segment_access_are_rejected(self):
        prepared, _ = self._prepare_segment()
        self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )

        for profile_id, segment_id in (
            ("profile-2", prepared.segment_id),
            (self.PROFILE_ID, "missing-segment"),
        ):
            for operation in ("ack", "release", "requeue"):
                with self.subTest(
                    profile_id=profile_id,
                    segment_id=segment_id,
                    operation=operation,
                ):
                    before = self._segment_row(prepared.segment_id)
                    with self.assertRaises(ShortTermStateError):
                        if operation == "ack":
                            self.store.acknowledge_segment(
                                profile_id=profile_id,
                                source_type=self.SOURCE_TYPE,
                                segment_id=segment_id,
                                owner="main-agent",
                                outcome="remembered",
                                episode_id=None,
                                memory_ids=[],
                                reason=None,
                                now=self.NOW,
                            )
                        elif operation == "release":
                            self.store.release_segment(
                                profile_id=profile_id,
                                source_type=self.SOURCE_TYPE,
                                segment_id=segment_id,
                                owner="main-agent",
                                error="failed",
                                max_attempts=2,
                                now=self.NOW,
                            )
                        else:
                            self.store.requeue_failed_segment(
                                profile_id=profile_id,
                                source_type=self.SOURCE_TYPE,
                                segment_id=segment_id,
                            )
                    self.assertEqual(self._segment_row(prepared.segment_id), before)

    def test_acknowledge_and_release_reject_nonprocessing_states(self):
        failed, _ = self._prepare_segment(content="failed")
        self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )
        self.store.release_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=failed.segment_id,
            owner="main-agent",
            error="failed",
            max_attempts=1,
            now=self.NOW,
        )
        pending, _ = self._prepare_segment(content="pending")

        for request in (pending, failed):
            for operation in ("ack", "release"):
                with self.subTest(state=request.state, operation=operation):
                    before = self._segment_row(request.segment_id)
                    with self.assertRaises(ShortTermStateError):
                        if operation == "ack":
                            self.store.acknowledge_segment(
                                profile_id=self.PROFILE_ID,
                                source_type=self.SOURCE_TYPE,
                                segment_id=request.segment_id,
                                owner="main-agent",
                                outcome="remembered",
                                episode_id=None,
                                memory_ids=[],
                                reason=None,
                                now=self.NOW,
                            )
                        else:
                            self.store.release_segment(
                                profile_id=self.PROFILE_ID,
                                source_type=self.SOURCE_TYPE,
                                segment_id=request.segment_id,
                                owner="main-agent",
                                error="failed",
                                max_attempts=1,
                                now=self.NOW,
                            )
                    self.assertEqual(self._segment_row(request.segment_id), before)

    def test_delivery_arguments_are_validated(self):
        prepared, _ = self._prepare_segment()

        claim_cases = (
            {"profile_id": "", "source_type": "chat", "owner": "owner", "lease_seconds": 1},
            {"profile_id": "profile", "source_type": " ", "owner": "owner", "lease_seconds": 1},
            {"profile_id": "profile", "source_type": "chat", "owner": "", "lease_seconds": 1},
            {"profile_id": "profile", "source_type": "chat", "owner": "owner", "lease_seconds": 0},
            {"profile_id": "profile", "source_type": "chat", "owner": "owner", "lease_seconds": -1},
            {"profile_id": "profile", "source_type": "chat", "owner": "owner", "lease_seconds": True},
        )
        for arguments in claim_cases:
            with self.subTest(claim=arguments):
                with self.assertRaises((TypeError, ValueError)):
                    self.store.claim_next_segment(**arguments, now=self.NOW)

        for invalid_now in ("not-a-time", "2026-07-25T00:00:00"):
            with self.subTest(now=invalid_now):
                with self.assertRaises(ValueError):
                    self.store.claim_next_segment(
                        profile_id=self.PROFILE_ID,
                        source_type=self.SOURCE_TYPE,
                        owner="owner",
                        lease_seconds=1,
                        now=invalid_now,
                    )

        ack_cases = (
            {"profile_id": "", "segment_id": prepared.segment_id, "owner": "owner", "outcome": "ok", "memory_ids": []},
            {"profile_id": self.PROFILE_ID, "segment_id": "", "owner": "owner", "outcome": "ok", "memory_ids": []},
            {"profile_id": self.PROFILE_ID, "segment_id": prepared.segment_id, "owner": " ", "outcome": "ok", "memory_ids": []},
            {"profile_id": self.PROFILE_ID, "segment_id": prepared.segment_id, "owner": "owner", "outcome": "", "memory_ids": []},
            {"profile_id": self.PROFILE_ID, "segment_id": prepared.segment_id, "owner": "owner", "outcome": "ok", "memory_ids": ("not", "list")},
            {"profile_id": self.PROFILE_ID, "segment_id": prepared.segment_id, "owner": "owner", "outcome": "ok", "memory_ids": ["valid", 1]},
        )
        for arguments in ack_cases:
            with self.subTest(ack=arguments):
                with self.assertRaises((TypeError, ValueError)):
                    self.store.acknowledge_segment(
                        **arguments,
                        source_type=self.SOURCE_TYPE,
                        episode_id=None,
                        reason=None,
                        now=self.NOW,
                    )

        release_cases = (
            {"owner": "", "error": "failed", "max_attempts": 1},
            {"owner": "owner", "error": " ", "max_attempts": 1},
            {"owner": "owner", "error": "failed", "max_attempts": 0},
            {"owner": "owner", "error": "failed", "max_attempts": True},
        )
        for arguments in release_cases:
            with self.subTest(release=arguments):
                with self.assertRaises((TypeError, ValueError)):
                    self.store.release_segment(
                        profile_id=self.PROFILE_ID,
                        source_type=self.SOURCE_TYPE,
                        segment_id=prepared.segment_id,
                        **arguments,
                        now=self.NOW,
                    )

    def test_only_failed_segments_can_be_requeued_cleanly(self):
        failed, _ = self._prepare_segment(content="failed")
        self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )
        self.store.release_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=failed.segment_id,
            owner="main-agent",
            error="failed permanently",
            max_attempts=1,
            now=self.NOW,
        )

        processing, _ = self._prepare_segment(content="processing")
        processing_claim = self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )
        self.assertEqual(processing_claim.segment_id, processing.segment_id)

        completed, _ = self._prepare_segment(content="completed")
        completed_claim = self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )
        self.assertEqual(completed_claim.segment_id, completed.segment_id)
        self.store.acknowledge_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=completed.segment_id,
            owner="main-agent",
            outcome="remembered",
            episode_id=None,
            memory_ids=[],
            reason=None,
            now=self.NOW,
        )

        pending, _ = self._prepare_segment(content="pending")

        requeued = self.store.requeue_failed_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=failed.segment_id,
        )

        self.assertEqual(requeued.state, SegmentState.PENDING)
        self.assertEqual(requeued.attempt_count, 0)
        row = self._segment_row(failed.segment_id)
        self.assertIsNone(row["last_error"])
        self.assertIsNone(row["lease_owner"])
        self.assertIsNone(row["lease_expires_at"])
        self.assertIsNone(row["completed_at"])
        self.assertEqual(row["result_json"], "{}")

        for segment_id in (
            pending.segment_id,
            processing.segment_id,
            completed.segment_id,
        ):
            with self.subTest(segment_id=segment_id):
                before = self._segment_row(segment_id)
                with self.assertRaises(ShortTermStateError):
                    self.store.requeue_failed_segment(
                        profile_id=self.PROFILE_ID,
                        source_type=self.SOURCE_TYPE,
                        segment_id=segment_id,
                    )
                self.assertEqual(self._segment_row(segment_id), before)

    def test_claims_pending_segments_oldest_first(self):
        first, _ = self._prepare_segment(content="first")
        second, _ = self._prepare_segment(content="second")
        first_payload = self._payload(first.segment_id)
        first_payload["created_at"] = "2026-07-25T00:00:00+00:00"
        self._update_segment(
            first.segment_id,
            payload_json=json.dumps(first_payload),
            created_at="2026-07-25T00:00:00+00:00",
        )
        second_payload = self._payload(second.segment_id)
        second_payload["created_at"] = "2026-07-25T00:00:01+00:00"
        self._update_segment(
            second.segment_id,
            payload_json=json.dumps(second_payload),
            created_at="2026-07-25T00:00:01+00:00",
        )

        first_claim = self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="worker-1",
            lease_seconds=60,
            now=self.NOW,
        )
        second_claim = self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="worker-2",
            lease_seconds=60,
            now=self.NOW,
        )

        self.assertEqual(
            [first_claim.segment_id, second_claim.segment_id],
            [first.segment_id, second.segment_id],
        )

    def test_concurrent_claim_returns_segment_to_exactly_one_owner(self):
        prepared, _ = self._prepare_segment()
        competing_store = SQLiteShortTermStore(self.path)
        self.addCleanup(competing_store.close)
        barrier = Barrier(2)
        result_lock = Lock()
        results = []
        errors = []

        def claim(store, owner):
            try:
                barrier.wait()
                result = store.claim_next_segment(
                    profile_id=self.PROFILE_ID,
                    source_type=self.SOURCE_TYPE,
                    owner=owner,
                    lease_seconds=60,
                    now=self.NOW,
                )
                with result_lock:
                    results.append((owner, result))
            except BaseException as error:
                with result_lock:
                    errors.append(error)

        threads = [
            Thread(target=claim, args=(self.store, "worker-1")),
            Thread(target=claim, args=(competing_store, "worker-2")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        claimed = [(owner, result) for owner, result in results if result is not None]
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0][1].segment_id, prepared.segment_id)
        self.assertEqual(self._segment_row(prepared.segment_id)["lease_owner"], claimed[0][0])

    def test_transitions_reconstruct_immutable_payload_and_row_state(self):
        prepared, message = self._prepare_segment(
            topic="exact topic",
            reason="exact reason",
            content="exact message",
        )

        claimed = self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )
        released = self.store.release_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=prepared.segment_id,
            owner="main-agent",
            error="retry",
            max_attempts=2,
            now=self.NOW,
        )
        self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now="2026-07-25T00:02:00+00:00",
        )
        completed = self.store.acknowledge_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=prepared.segment_id,
            owner="main-agent",
            outcome="remembered",
            episode_id=None,
            memory_ids=[],
            reason=None,
            now=self.NOW,
        )

        for request, state, attempts, lease in (
            (
                claimed,
                SegmentState.PROCESSING,
                1,
                "2026-07-25T00:01:00+00:00",
            ),
            (released, SegmentState.PENDING, 1, None),
            (completed, SegmentState.COMPLETED, 2, None),
        ):
            with self.subTest(state=state):
                self.assertEqual(request.topic, "exact topic")
                self.assertEqual(request.reason, "exact reason")
                self.assertEqual(request.messages, (message,))
                self.assertEqual(request.created_at, prepared.created_at)
                self.assertEqual(request.state, state)
                self.assertEqual(request.attempt_count, attempts)
                self.assertEqual(request.lease_expires_at, lease)

    def test_corrupt_payload_does_not_mutate_pending_segment(self):
        prepared, _ = self._prepare_segment()
        self._update_segment(
            prepared.segment_id,
            payload_json=json.dumps({"segment_id": prepared.segment_id}),
        )
        before = self._segment_row(prepared.segment_id)

        with self.assertRaisesRegex(RuntimeError, "payload"):
            self.store.claim_next_segment(
                profile_id=self.PROFILE_ID,
                source_type=self.SOURCE_TYPE,
                owner="main-agent",
                lease_seconds=60,
                now=self.NOW,
            )

        self.assertEqual(self._segment_row(prepared.segment_id), before)

    def test_claim_rejects_semantically_corrupt_payloads_without_mutation(self):
        cases = (
            ("empty-messages", lambda payload: payload.update(messages=[])),
            (
                "mismatched-created-at",
                lambda payload: payload.update(
                    created_at="2026-07-25T00:00:01+00:00"
                ),
            ),
            (
                "malformed-created-at",
                lambda payload: payload.update(created_at="not-a-timestamp"),
            ),
            (
                "naive-message-created-at",
                lambda payload: payload["messages"][0].update(
                    created_at="2026-07-25T00:00:00"
                ),
            ),
            (
                "duplicate-message-id",
                lambda payload: payload["messages"].append(
                    {
                        **payload["messages"][0],
                        "sequence": payload["messages"][0]["sequence"] + 1,
                    }
                ),
            ),
            (
                "duplicate-sequence",
                lambda payload: payload["messages"].append(
                    {
                        **payload["messages"][0],
                        "id": "different-message",
                    }
                ),
            ),
            (
                "out-of-order-sequence",
                lambda payload: payload["messages"].append(
                    {
                        **payload["messages"][0],
                        "id": "different-message",
                        "sequence": payload["messages"][0]["sequence"] - 1,
                    }
                ),
            ),
            (
                "invalid-payload-state",
                lambda payload: payload.update(state=1),
            ),
            (
                "invalid-payload-attempt",
                lambda payload: payload.update(attempt_count=True),
            ),
        )

        for label, corrupt in cases:
            with self.subTest(label=label):
                profile_id = f"profile-payload-{label}"
                prepared, _ = self._prepare_segment(profile_id=profile_id)
                payload = self._payload(prepared.segment_id)
                corrupt(payload)
                self._update_segment(
                    prepared.segment_id,
                    payload_json=json.dumps(payload),
                )
                before = self._segment_row(prepared.segment_id)

                with self.assertRaisesRegex(RuntimeError, "payload"):
                    self.store.claim_next_segment(
                        profile_id=profile_id,
                        source_type=self.SOURCE_TYPE,
                        owner="main-agent",
                        lease_seconds=60,
                        now=self.NOW,
                    )

                self.assertEqual(self._segment_row(prepared.segment_id), before)

    def test_claim_rejects_payload_identity_mismatches_without_mutation(self):
        identity_fields = (
            "segment_id",
            "profile_id",
            "source_type",
            "breakpoint_id",
        )

        for field_name in identity_fields:
            with self.subTest(field=field_name):
                profile_id = f"profile-identity-{field_name}"
                prepared, _ = self._prepare_segment(profile_id=profile_id)
                payload = self._payload(prepared.segment_id)
                payload[field_name] = f"wrong-{field_name}"
                self._update_segment(
                    prepared.segment_id,
                    payload_json=json.dumps(payload),
                )
                before = self._segment_row(prepared.segment_id)

                with self.assertRaisesRegex(RuntimeError, field_name):
                    self.store.claim_next_segment(
                        profile_id=profile_id,
                        source_type=self.SOURCE_TYPE,
                        owner="main-agent",
                        lease_seconds=60,
                        now=self.NOW,
                    )

                self.assertEqual(self._segment_row(prepared.segment_id), before)

    def test_equivalent_payload_and_row_created_at_instants_are_accepted(self):
        prepared, _ = self._prepare_segment()
        payload = self._payload(prepared.segment_id)
        payload["created_at"] = "2026-07-25T08:00:00+08:00"
        self._update_segment(
            prepared.segment_id,
            payload_json=json.dumps(payload),
            created_at="2026-07-25T00:00:00+00:00",
        )

        claimed = self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )

        self.assertEqual(claimed.segment_id, prepared.segment_id)
        self.assertEqual(claimed.created_at, "2026-07-25T00:00:00+00:00")

    def test_corrupt_payload_blocks_every_delivery_transition(self):
        operations = ("ack", "release", "recover", "requeue")

        for operation in operations:
            with self.subTest(operation=operation):
                profile_id = f"profile-transition-{operation}"
                prepared, _ = self._prepare_segment(profile_id=profile_id)
                self.store.claim_next_segment(
                    profile_id=profile_id,
                    source_type=self.SOURCE_TYPE,
                    owner="main-agent",
                    lease_seconds=60,
                    now=self.NOW,
                )
                if operation == "requeue":
                    self.store.release_segment(
                        profile_id=profile_id,
                        source_type=self.SOURCE_TYPE,
                        segment_id=prepared.segment_id,
                        owner="main-agent",
                        error="failed",
                        max_attempts=1,
                        now=self.NOW,
                    )
                elif operation == "recover":
                    self._update_segment(
                        prepared.segment_id,
                        lease_expires_at="2026-07-25T00:00:30+00:00",
                    )
                payload = self._payload(prepared.segment_id)
                payload["messages"] = []
                self._update_segment(
                    prepared.segment_id,
                    payload_json=json.dumps(payload),
                )
                before = self._segment_row(prepared.segment_id)

                with self.assertRaisesRegex(RuntimeError, "payload"):
                    if operation == "ack":
                        self.store.acknowledge_segment(
                            profile_id=profile_id,
                            source_type=self.SOURCE_TYPE,
                            segment_id=prepared.segment_id,
                            owner="main-agent",
                            outcome="remembered",
                            episode_id=None,
                            memory_ids=[],
                            reason=None,
                            now=self.NOW,
                        )
                    elif operation == "release":
                        self.store.release_segment(
                            profile_id=profile_id,
                            source_type=self.SOURCE_TYPE,
                            segment_id=prepared.segment_id,
                            owner="main-agent",
                            error="retry",
                            max_attempts=2,
                            now=self.NOW,
                        )
                    elif operation == "recover":
                        self.store.recover_expired_leases(
                            profile_id=profile_id,
                            source_type=self.SOURCE_TYPE,
                            now="2026-07-25T00:01:00+00:00",
                        )
                    else:
                        self.store.requeue_failed_segment(
                            profile_id=profile_id,
                            source_type=self.SOURCE_TYPE,
                            segment_id=prepared.segment_id,
                        )

                self.assertEqual(self._segment_row(prepared.segment_id), before)

    def test_completed_result_is_validated_after_close_and_reopen(self):
        invalid_results = (
            ("null", "null"),
            ("array", "[]"),
            ("missing", '{"outcome":"remembered"}'),
            (
                "wrong-types",
                json.dumps(
                    {
                        "outcome": 1,
                        "episode_id": [],
                        "memory_ids": ["valid", 2],
                        "reason": {},
                    }
                ),
            ),
        )

        for label, result_json in invalid_results:
            with self.subTest(label=label):
                profile_id = f"profile-result-{label}"
                prepared, _ = self._prepare_segment(profile_id=profile_id)
                self.store.claim_next_segment(
                    profile_id=profile_id,
                    source_type=self.SOURCE_TYPE,
                    owner="main-agent",
                    lease_seconds=60,
                    now=self.NOW,
                )
                self.store.acknowledge_segment(
                    profile_id=profile_id,
                    source_type=self.SOURCE_TYPE,
                    segment_id=prepared.segment_id,
                    owner="main-agent",
                    outcome="remembered",
                    episode_id=None,
                    memory_ids=[],
                    reason=None,
                    now=self.NOW,
                )
                self._update_segment(
                    prepared.segment_id,
                    result_json=result_json,
                )
                self.store.close()
                self.store = SQLiteShortTermStore(self.path)
                before = self._segment_row(prepared.segment_id)

                with self.assertRaisesRegex(RuntimeError, "result"):
                    self.store.acknowledge_segment(
                        profile_id=profile_id,
                        source_type=self.SOURCE_TYPE,
                        segment_id=prepared.segment_id,
                        owner="retry-owner",
                        outcome="discarded",
                        episode_id=None,
                        memory_ids=[],
                        reason="retry",
                        now=self.NOW,
                    )

                self.assertEqual(self._segment_row(prepared.segment_id), before)

    def test_repeated_ack_preserves_durable_result_after_reopen(self):
        prepared, _ = self._prepare_segment()
        self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )
        first = self.store.acknowledge_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=prepared.segment_id,
            owner="main-agent",
            outcome="remembered",
            episode_id="episode-1",
            memory_ids=["memory-1"],
            reason=None,
            now=self.NOW,
        )
        before = self._segment_row(prepared.segment_id)
        self.store.close()
        self.store = SQLiteShortTermStore(self.path)

        repeated = self.store.acknowledge_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=prepared.segment_id,
            owner="retry-owner",
            outcome="discarded",
            episode_id=None,
            memory_ids=[],
            reason="late",
            now=self.NOW,
        )

        self.assertEqual(repeated, first)
        self.assertEqual(self._segment_row(prepared.segment_id), before)

    def test_claim_lease_overflow_is_stable_and_does_not_mutate(self):
        cases = (
            ("huge-seconds", self.NOW, 10**100),
            ("max-datetime", "9999-12-31T23:59:59+00:00", 1),
        )

        for label, now, lease_seconds in cases:
            with self.subTest(label=label):
                profile_id = f"profile-overflow-{label}"
                prepared, _ = self._prepare_segment(profile_id=profile_id)
                before = self._segment_row(prepared.segment_id)

                with self.assertRaisesRegex(ValueError, "lease"):
                    self.store.claim_next_segment(
                        profile_id=profile_id,
                        source_type=self.SOURCE_TYPE,
                        owner="main-agent",
                        lease_seconds=lease_seconds,
                        now=now,
                    )

                self.assertEqual(self._segment_row(prepared.segment_id), before)

    def test_payload_reconstruction_accepts_empty_message_content(self):
        prepared, message = self._prepare_segment(content="")

        claimed = self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )

        self.assertEqual(claimed.segment_id, prepared.segment_id)
        self.assertEqual(claimed.messages, (message,))

    def test_claim_update_failure_rolls_back_transaction(self):
        prepared, _ = self._prepare_segment()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                """
                create trigger abort_segment_claim
                before update on short_term_segments
                when new.state = 'processing'
                begin
                  select raise(abort, 'claim update failed');
                end
                """
            )
        before = self._segment_row(prepared.segment_id)

        with self.assertRaises(sqlite3.IntegrityError):
            self.store.claim_next_segment(
                profile_id=self.PROFILE_ID,
                source_type=self.SOURCE_TYPE,
                owner="main-agent",
                lease_seconds=60,
                now=self.NOW,
            )

        self.assertEqual(self._segment_row(prepared.segment_id), before)

    def test_acknowledge_update_failure_rolls_back_transaction(self):
        prepared, _ = self._prepare_segment()
        self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                """
                create trigger abort_segment_acknowledge
                before update on short_term_segments
                when new.state = 'completed'
                begin
                  select raise(abort, 'acknowledge update failed');
                end
                """
            )
        before = self._segment_row(prepared.segment_id)

        with self.assertRaises(sqlite3.IntegrityError):
            self.store.acknowledge_segment(
                profile_id=self.PROFILE_ID,
                source_type=self.SOURCE_TYPE,
                segment_id=prepared.segment_id,
                owner="main-agent",
                outcome="remembered",
                episode_id="episode-1",
                memory_ids=["memory-1"],
                reason=None,
                now=self.NOW,
            )

        self.assertEqual(self._segment_row(prepared.segment_id), before)

    def test_release_update_failure_is_durably_rolled_back(self):
        prepared, _ = self._prepare_segment()
        self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                """
                create trigger abort_segment_release
                before update on short_term_segments
                when old.state = 'processing' and new.state != 'processing'
                begin
                  select raise(abort, 'release update failed');
                end
                """
            )
        before = self._segment_row(prepared.segment_id)

        with self.assertRaisesRegex(
            sqlite3.IntegrityError,
            "release update failed",
        ):
            self.store.release_segment(
                profile_id=self.PROFILE_ID,
                source_type=self.SOURCE_TYPE,
                segment_id=prepared.segment_id,
                owner="main-agent",
                error="retry",
                max_attempts=2,
                now=self.NOW,
            )

        self.store.close()
        self.store = SQLiteShortTermStore(self.path)
        self.assertEqual(self._segment_row(prepared.segment_id), before)

    def test_requeue_update_failure_is_durably_rolled_back(self):
        prepared, _ = self._prepare_segment()
        self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )
        self.store.release_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=prepared.segment_id,
            owner="main-agent",
            error="failed",
            max_attempts=1,
            now=self.NOW,
        )
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                """
                create trigger abort_segment_requeue
                before update on short_term_segments
                when old.state = 'failed' and new.state = 'pending'
                begin
                  select raise(abort, 'requeue update failed');
                end
                """
            )
        before = self._segment_row(prepared.segment_id)

        with self.assertRaisesRegex(
            sqlite3.IntegrityError,
            "requeue update failed",
        ):
            self.store.requeue_failed_segment(
                profile_id=self.PROFILE_ID,
                source_type=self.SOURCE_TYPE,
                segment_id=prepared.segment_id,
            )

        self.store.close()
        self.store = SQLiteShortTermStore(self.path)
        self.assertEqual(self._segment_row(prepared.segment_id), before)


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


class ShortTermMemoryBufferTests(unittest.TestCase):
    PROFILE_ID = "profile-1"
    SOURCE_TYPE = "chat"

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.stores = []
        self.buffers = []

    def tearDown(self):
        for buffer in self.buffers:
            buffer.close()
        for store in self.stores:
            store.close()
        self.tmpdir.cleanup()

    def _store(self, filename="memory.sqlite3"):
        store = SQLiteShortTermStore(Path(self.tmpdir.name) / filename)
        self.stores.append(store)
        return store

    def _buffer(self, **kwargs):
        kwargs.setdefault("profile_id", self.PROFILE_ID)
        kwargs.setdefault("source_type", self.SOURCE_TYPE)
        buffer = ShortTermMemoryBuffer(**kwargs)
        self.buffers.append(buffer)
        return buffer

    def _prepare_segment(self, buffer):
        buffer.add(role="user", content="咖啡", source_id="m1")
        return buffer.mark_breakpoint(topic="coffee", reason="topic_changed")

    def test_breakpoint_threshold_prepares_oldest_pending_segment(self):
        store = self._store()
        service = Mock()
        buffer = self._buffer(
            max_breakpoints=2,
            store=store,
            service=service,
            max_messages=1,
        )
        buffer.add(role="user", content="咖啡", source_id="m1")

        self.assertIsNone(
            buffer.mark_breakpoint(topic="coffee", reason="topic_changed")
        )
        buffer.add_message(role="user", content="项目", source_id="m2")
        prepared = buffer.mark_breakpoint(
            topic="project",
            reason="topic_changed",
        )

        self.assertIsInstance(prepared, dict)
        self.assertEqual(prepared["state"], "pending")
        self.assertEqual(prepared["topic"], "coffee")
        self.assertEqual(
            [message.content for message in buffer.messages],
            ["项目"],
        )
        self.assertEqual(
            [breakpoint.topic for breakpoint in buffer.breakpoints],
            ["project"],
        )
        self.assertEqual(service.mock_calls, [])

    def test_context_limit_prepares_only_oldest_complete_segment(self):
        buffer = self._buffer(max_breakpoints=10, max_chars=8)
        buffer.add(role="user", content="咖啡", source_id="m1")
        buffer.mark_breakpoint(topic="coffee", reason="topic_changed")

        prepared = buffer.add(
            role="user",
            content="项目内容很长",
            source_id="m2",
        )

        self.assertEqual(prepared["topic"], "coffee")
        self.assertEqual(
            [message.content for message in buffer.messages],
            ["项目内容很长"],
        )
        self.assertEqual(buffer.breakpoints, [])

    def test_context_limit_keeps_unbroken_current_message_intact(self):
        buffer = self._buffer(max_breakpoints=10, max_chars=5)

        prepared = buffer.add(
            role="user",
            content="超过上下文限制",
            source_id="m1",
        )

        self.assertIsNone(prepared)
        self.assertEqual(
            [message.content for message in buffer.messages],
            ["超过上下文限制"],
        )

    def test_legacy_mutators_return_dictionary_or_none(self):
        buffer = self._buffer(max_breakpoints=10, max_chars=100)

        self.assertIsNone(
            buffer.add(role="user", content="first", source_id="m1")
        )
        prepared = buffer.mark_breakpoint(topic="first", reason=None)
        self.assertIsNone(prepared)
        prepared = buffer.pop_oldest_segment_for_summary()

        self.assertIsInstance(prepared, dict)
        self.assertEqual(prepared["state"], "pending")
        self.assertIsNone(
            buffer.add_message(
                role="user",
                content="second",
                source_id="m2",
            )
        )

    def test_messages_and_breakpoints_are_fresh_read_only_snapshots(self):
        store = self._store()
        buffer = self._buffer(store=store)
        buffer.add(role="user", content="咖啡", source_id="m1")
        buffer.mark_breakpoint(topic="coffee", reason="topic_changed")

        messages = buffer.messages
        breakpoints = buffer.breakpoints
        messages.clear()
        breakpoints.clear()

        self.assertEqual([message.content for message in buffer.messages], ["咖啡"])
        self.assertEqual(
            [breakpoint.topic for breakpoint in buffer.breakpoints],
            ["coffee"],
        )
        self.assertIsNot(buffer.messages, buffer.messages)
        self.assertIsNot(buffer.breakpoints, buffer.breakpoints)

    def test_delivery_methods_delegate_exact_buffer_scope_and_arguments(self):
        store = self._store()
        wrapped = Mock(wraps=store, spec=store)
        buffer = self._buffer(
            store=wrapped,
            max_breakpoints=1,
            max_summary_attempts=4,
        )
        prepared = self._prepare_segment(buffer)
        claimed = buffer.claim_next_summary(
            owner="main-agent",
            lease_seconds=60,
            now="2026-07-25T00:00:00+00:00",
        )
        released = buffer.release_summary(
            segment_id=claimed.segment_id,
            owner="main-agent",
            error="temporary failure",
            now="2026-07-25T00:00:30+00:00",
        )
        claimed_again = buffer.claim_next_summary(
            owner="main-agent",
            lease_seconds=30,
            now="2026-07-25T00:01:00+00:00",
        )
        completed = buffer.acknowledge_summary(
            segment_id=claimed_again.segment_id,
            owner="main-agent",
            outcome="remembered",
            episode_id="episode-1",
            memory_ids=["memory-1"],
            reason=None,
            now="2026-07-25T00:01:15+00:00",
        )

        self.assertIsInstance(claimed, SummaryRequest)
        self.assertEqual(released.state, SegmentState.PENDING)
        self.assertEqual(completed.state, SegmentState.COMPLETED)
        wrapped.claim_next_segment.assert_any_call(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now="2026-07-25T00:00:00+00:00",
        )
        wrapped.release_segment.assert_called_once_with(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=prepared["segment_id"],
            owner="main-agent",
            error="temporary failure",
            max_attempts=4,
            now="2026-07-25T00:00:30+00:00",
        )
        wrapped.acknowledge_segment.assert_called_once_with(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=prepared["segment_id"],
            owner="main-agent",
            outcome="remembered",
            episode_id="episode-1",
            memory_ids=["memory-1"],
            reason=None,
            now="2026-07-25T00:01:15+00:00",
        )

    def test_release_summary_accepts_valid_per_call_attempt_override(self):
        store = self._store()
        wrapped = Mock(wraps=store, spec=store)
        buffer = self._buffer(
            store=wrapped,
            max_breakpoints=1,
            max_summary_attempts=4,
        )
        prepared = self._prepare_segment(buffer)
        buffer.claim_next_summary(owner="main-agent", lease_seconds=60)

        released = buffer.release_summary(
            segment_id=prepared["segment_id"],
            owner="main-agent",
            error="permanent failure",
            max_attempts=1,
        )

        self.assertEqual(released.state, SegmentState.FAILED)
        wrapped.release_segment.assert_called_once_with(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=prepared["segment_id"],
            owner="main-agent",
            error="permanent failure",
            max_attempts=1,
            now=None,
        )

    def test_recover_and_requeue_delegate_and_preserve_store_behavior(self):
        store = self._store()
        wrapped = Mock(wraps=store, spec=store)
        buffer = self._buffer(
            store=wrapped,
            max_breakpoints=1,
            max_summary_attempts=1,
        )
        prepared = self._prepare_segment(buffer)
        buffer.claim_next_summary(
            owner="expired-worker",
            lease_seconds=10,
            now="2026-07-25T00:00:00+00:00",
        )

        recovered = buffer.recover_expired_summaries(
            now="2026-07-25T00:01:00+00:00"
        )
        claimed = buffer.claim_next_summary(
            owner="main-agent",
            lease_seconds=60,
            now="2026-07-25T00:02:00+00:00",
        )
        failed = buffer.release_summary(
            segment_id=claimed.segment_id,
            owner="main-agent",
            error="failed",
            now="2026-07-25T00:02:30+00:00",
        )
        requeued = buffer.requeue_failed_summary(
            segment_id=failed.segment_id
        )

        self.assertEqual(recovered, 1)
        self.assertEqual(requeued.state, SegmentState.PENDING)
        wrapped.recover_expired_leases.assert_called_once_with(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            now="2026-07-25T00:01:00+00:00",
        )
        wrapped.requeue_failed_segment.assert_called_once_with(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=prepared["segment_id"],
        )

    def test_default_owned_store_closes_idempotently(self):
        buffer = self._buffer()
        buffer.add(role="user", content="咖啡", source_id=None)
        owned_store = buffer._store

        buffer.close()
        buffer.close()

        with self.assertRaisesRegex(
            RuntimeError,
            "^ShortTermMemoryBuffer is closed$",
        ):
            _ = buffer.messages
        with self.assertRaisesRegex(
            RuntimeError,
            "^SQLiteShortTermStore is closed$",
        ):
            owned_store.list_messages(self.PROFILE_ID, self.SOURCE_TYPE)

    def test_context_manager_closes_only_owned_store(self):
        with ShortTermMemoryBuffer(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
        ) as owned:
            owned_store = owned._store
            owned.add(role="user", content="owned", source_id=None)

        with self.assertRaisesRegex(
            RuntimeError,
            "^SQLiteShortTermStore is closed$",
        ):
            owned_store.list_messages(self.PROFILE_ID, self.SOURCE_TYPE)

        injected_store = self._store("injected.sqlite3")
        with ShortTermMemoryBuffer(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            store=injected_store,
        ) as injected:
            injected.add(role="user", content="injected", source_id=None)

        self.assertEqual(
            [
                message.content
                for message in injected_store.list_messages(
                    self.PROFILE_ID,
                    self.SOURCE_TYPE,
                )
            ],
            ["injected"],
        )

    def test_gc_finalizer_closes_owned_but_not_injected_store(self):
        owned_store = Mock()
        with patch(
            "backend.memory.short_term.buffer.SQLiteShortTermStore",
            return_value=owned_store,
        ):
            owned = ShortTermMemoryBuffer(
                profile_id=self.PROFILE_ID,
                source_type=self.SOURCE_TYPE,
            )
        owned_ref = weakref.ref(owned)
        del owned
        gc.collect()

        self.assertIsNone(owned_ref())
        owned_store.close.assert_called_once_with()

        injected_store = Mock()
        injected = ShortTermMemoryBuffer(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            store=injected_store,
        )
        injected_ref = weakref.ref(injected)
        del injected
        gc.collect()

        self.assertIsNone(injected_ref())
        injected_store.close.assert_not_called()

    def test_all_public_operations_fail_after_injected_buffer_close(self):
        store = self._store()
        buffer = self._buffer(store=store)
        buffer.add(role="user", content="咖啡", source_id=None)

        buffer.close()

        operations = {
            "messages": lambda: buffer.messages,
            "breakpoints": lambda: buffer.breakpoints,
            "add": lambda: buffer.add(
                role="user",
                content="new",
                source_id=None,
            ),
            "add_message": lambda: buffer.add_message(
                role="user",
                content="new",
                source_id=None,
            ),
            "mark_breakpoint": lambda: buffer.mark_breakpoint(
                topic="coffee",
                reason=None,
            ),
            "pop_oldest_segment_for_summary": (
                buffer.pop_oldest_segment_for_summary
            ),
            "claim_next_summary": lambda: buffer.claim_next_summary(
                owner="main-agent",
                lease_seconds=60,
            ),
            "acknowledge_summary": lambda: buffer.acknowledge_summary(
                segment_id="segment-1",
                owner="main-agent",
                outcome="remembered",
                episode_id=None,
                memory_ids=[],
                reason=None,
            ),
            "release_summary": lambda: buffer.release_summary(
                segment_id="segment-1",
                owner="main-agent",
                error="failed",
            ),
            "recover_expired_summaries": (
                buffer.recover_expired_summaries
            ),
            "requeue_failed_summary": lambda: (
                buffer.requeue_failed_summary(segment_id="segment-1")
            ),
        }
        for name, operation in operations.items():
            with self.subTest(operation=name):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "^ShortTermMemoryBuffer is closed$",
                ):
                    operation()

        store.append_message(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            role="user",
            content="caller still owns store",
            source_id=None,
        )
        self.assertEqual(
            [message.content for message in store.list_messages(
                self.PROFILE_ID,
                self.SOURCE_TYPE,
            )],
            ["咖啡", "caller still owns store"],
        )

    def test_concurrent_threshold_crossing_matches_serial_ordering(self):
        store = self._store()
        for content, topic in (("first", "first"), ("second", "second")):
            store.append_message(
                profile_id=self.PROFILE_ID,
                source_type=self.SOURCE_TYPE,
                role="user",
                content=content,
                source_id=None,
            )
            store.add_breakpoint(
                profile_id=self.PROFILE_ID,
                source_type=self.SOURCE_TYPE,
                topic=topic,
                reason="topic_changed",
            )

        append_barrier = Barrier(2)
        stats_barrier = Barrier(2)

        class ThresholdRaceStore:
            def __getattr__(self, name):
                return getattr(store, name)

            def append_message(self, **kwargs):
                message = store.append_message(**kwargs)
                try:
                    append_barrier.wait(timeout=1)
                except BrokenBarrierError:
                    pass
                return message

            def get_stats(self, profile_id, source_type):
                stats = store.get_stats(profile_id, source_type)
                try:
                    stats_barrier.wait(timeout=1)
                except BrokenBarrierError:
                    pass
                return stats

        buffer = self._buffer(
            store=ThresholdRaceStore(),
            max_breakpoints=2,
            max_chars=1000,
        )
        start = Barrier(3)
        results = []
        errors = []

        def add_message(content):
            start.wait()
            try:
                results.append(
                    buffer.add(
                        role="user",
                        content=content,
                        source_id=None,
                    )
                )
            except BaseException as error:
                errors.append(error)

        threads = [
            Thread(target=add_message, args=(content,))
            for content in ("concurrent-a", "concurrent-b")
        ]
        for thread in threads:
            thread.start()
        start.wait()
        for thread in threads:
            thread.join(timeout=5)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        prepared = [result for result in results if result is not None]
        self.assertEqual(len(prepared), 1)
        self.assertEqual(prepared[0]["topic"], "first")
        self.assertEqual(
            [breakpoint.topic for breakpoint in buffer.breakpoints],
            ["second"],
        )

    def test_close_waits_for_started_add_before_closing_facade(self):
        store = self._store()
        append_started = Event()
        allow_append = Event()

        class BlockingAppendStore:
            def __getattr__(self, name):
                return getattr(store, name)

            def append_message(self, **kwargs):
                append_started.set()
                if not allow_append.wait(timeout=3):
                    raise AssertionError("test did not release blocked append")
                return store.append_message(**kwargs)

        buffer = self._buffer(store=BlockingAppendStore())
        add_results = []
        errors = []
        close_started = Event()
        close_done = Event()

        def add_message():
            try:
                add_results.append(
                    buffer.add(
                        role="user",
                        content="in flight",
                        source_id=None,
                    )
                )
            except BaseException as error:
                errors.append(error)

        def close_buffer():
            close_started.set()
            try:
                buffer.close()
            except BaseException as error:
                errors.append(error)
            finally:
                close_done.set()

        add_thread = Thread(target=add_message)
        close_thread = Thread(target=close_buffer)
        add_thread.start()
        self.assertTrue(append_started.wait(timeout=1))
        close_thread.start()
        self.assertTrue(close_started.wait(timeout=1))
        close_returned_while_add_blocked = close_done.wait(timeout=0.2)
        allow_append.set()
        add_thread.join(timeout=3)
        close_thread.join(timeout=3)

        self.assertFalse(add_thread.is_alive())
        self.assertFalse(close_thread.is_alive())
        self.assertFalse(close_returned_while_add_blocked)
        self.assertEqual(errors, [])
        self.assertEqual(add_results, [None])
        with self.assertRaisesRegex(
            RuntimeError,
            "^ShortTermMemoryBuffer is closed$",
        ):
            _ = buffer.messages

        store.append_message(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            role="user",
            content="caller use",
            source_id=None,
        )
        self.assertEqual(
            [
                message.content
                for message in store.list_messages(
                    self.PROFILE_ID,
                    self.SOURCE_TYPE,
                )
            ],
            ["in flight", "caller use"],
        )

    def test_json_persistence_path_uses_sqlite_sidecar(self):
        path = Path(self.tmpdir.name) / "new-memory.json"
        previous_umask = os.umask(0o022)
        try:
            buffer = self._buffer(persistence_path=path)
            buffer.add(role="user", content="持久化", source_id=None)
            buffer.close()
        finally:
            os.umask(previous_umask)

        sidecar = Path(f"{path}.sqlite3")
        self.assertFalse(path.exists())
        self.assertTrue(sidecar.exists())
        self.assertEqual(stat.S_IMODE(sidecar.stat().st_mode), 0o600)
        self.assertEqual(sidecar.read_bytes()[:16], b"SQLite format 3\x00")
        reopened = SQLiteShortTermStore(sidecar)
        self.stores.append(reopened)
        self.assertEqual(
            [message.content for message in reopened.list_messages(
                self.PROFILE_ID,
                self.SOURCE_TYPE,
            )],
            ["持久化"],
        )

    def test_store_and_persistence_path_are_mutually_exclusive(self):
        store = self._store()

        with self.assertRaisesRegex(
            ValueError,
            "^store and persistence_path cannot both be provided$",
        ):
            self._buffer(
                store=store,
                persistence_path=Path(self.tmpdir.name) / "other.sqlite3",
            )

    def test_constructor_requires_valid_scope_and_positive_exact_integers(self):
        for field in ("profile_id", "source_type"):
            for value, error in (
                (None, TypeError),
                (1, TypeError),
                ("", ValueError),
                ("  ", ValueError),
            ):
                with self.subTest(field=field, value=value):
                    kwargs = {
                        "profile_id": self.PROFILE_ID,
                        "source_type": self.SOURCE_TYPE,
                        field: value,
                    }
                    with self.assertRaises(error):
                        ShortTermMemoryBuffer(**kwargs)

        for field in (
            "max_breakpoints",
            "max_chars",
            "max_summary_attempts",
        ):
            for value in (0, -1, True, False, 0.5, 1.5):
                with self.subTest(field=field, value=value):
                    kwargs = {
                        "profile_id": self.PROFILE_ID,
                        "source_type": self.SOURCE_TYPE,
                        field: value,
                    }
                    with self.assertRaisesRegex(
                        ValueError,
                        rf"^{field} must be a positive integer$",
                    ):
                        ShortTermMemoryBuffer(**kwargs)

    def test_add_message_validates_role_and_content_like_store_identifiers(self):
        buffer = self._buffer()

        for field in ("role", "content"):
            for value, error in (
                (None, TypeError),
                (1, TypeError),
                ("", ValueError),
                ("  ", ValueError),
            ):
                with self.subTest(field=field, value=value):
                    kwargs = {"role": "user", "content": "hello", field: value}
                    with self.assertRaises(error):
                        buffer.add_message(**kwargs)

        self.assertEqual(buffer.messages, [])

    def test_duplicate_or_empty_breakpoint_does_not_prepare(self):
        buffer = self._buffer(max_breakpoints=2)

        self.assertIsNone(buffer.mark_breakpoint(topic="empty", reason=None))
        buffer.add(role="user", content="咖啡", source_id=None)
        self.assertIsNone(
            buffer.mark_breakpoint(topic="coffee", reason=None)
        )
        self.assertIsNone(
            buffer.mark_breakpoint(topic="duplicate", reason=None)
        )
        self.assertEqual(
            [breakpoint.topic for breakpoint in buffer.breakpoints],
            ["coffee"],
        )


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


class ShortTermCompatibilityTests(unittest.TestCase):
    def test_short_term_package_exports_domain_surface(self):
        from backend.memory import short_term

        self.assertEqual(
            short_term.__all__,
            [
                "BufferStats",
                "SegmentState",
                "ShortTermBreakpoint",
                "ShortTermMemoryBuffer",
                "ShortTermMessage",
                "ShortTermPolicy",
                "ShortTermStateError",
                "ShortTermStore",
                "SummaryRequest",
                "now_iso",
            ],
        )

    def test_now_iso_remains_importable_from_short_term_package(self):
        from backend.memory.short_term import now_iso
        from backend.memory.short_term.buffer import now_iso as buffer_now_iso

        self.assertIs(now_iso, buffer_now_iso)

    def test_memory_package_and_facade_export_short_term_contract(self):
        from backend.memory import (
            SegmentState as PublicSegmentState,
            ShortTermMemoryBuffer as PublicShortTermMemoryBuffer,
            ShortTermPolicy as PublicShortTermPolicy,
            ShortTermStateError as PublicShortTermStateError,
            SQLiteShortTermStore as PublicSQLiteShortTermStore,
            SummaryRequest as PublicSummaryRequest,
        )
        from backend.memory.invode import (
            SQLiteShortTermStore as FacadeShortTermStore,
        )

        self.assertIs(PublicSegmentState, SegmentState)
        self.assertIs(PublicShortTermMemoryBuffer, ShortTermMemoryBuffer)
        self.assertIs(PublicShortTermPolicy, ShortTermPolicy)
        self.assertIs(PublicShortTermStateError, ShortTermStateError)
        self.assertIs(PublicSQLiteShortTermStore, SQLiteShortTermStore)
        self.assertIs(PublicSummaryRequest, SummaryRequest)
        self.assertIs(FacadeShortTermStore, SQLiteShortTermStore)
