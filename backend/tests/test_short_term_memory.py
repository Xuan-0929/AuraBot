from dataclasses import FrozenInstanceError
import gc
import os
from pathlib import Path
import stat
import tempfile
from threading import Barrier, BrokenBarrierError, Event, Thread
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
