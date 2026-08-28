"""Check the ShortTerm compatibility facade at policy, summary, delivery, ownership, and validation edges.

The facade tests intentionally delegate SQLite transaction details to the store suites and retain only the
observable buffering contract, fresh snapshots, one delivery flow, close ownership, and constructor guards.
"""

from pathlib import Path
import tempfile
import unittest

from backend.memory.short_term.buffer import ShortTermMemoryBuffer
from backend.memory.short_term.models import BufferStats, SegmentState, ShortTermMessage, SummaryRequest
from backend.memory.short_term.policy import ShortTermPolicy
from backend.memory.stores.short_term_sqlite import SQLiteShortTermStore


class ShortTermPolicyAndModelsTests(unittest.TestCase):
    def test_policy_validation_and_thresholds(self) -> None:
        for field in ("max_breakpoints", "max_chars"):
            for value in (0, -1, True, 1.5):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError): ShortTermPolicy(**{field: value})
        policy = ShortTermPolicy(max_breakpoints=2, max_chars=10)
        self.assertFalse(policy.should_prepare(BufferStats(1, 1, 9)))
        self.assertTrue(policy.should_prepare(BufferStats(1, 2, 1)))
        self.assertTrue(policy.should_prepare(BufferStats(1, 0, 10)))

    def test_summary_request_copies_messages_and_is_frozen(self) -> None:
        messages = [ShortTermMessage("m1", 1, "user", "hello", None, "2026-01-01T00:00:00Z")]
        request = SummaryRequest(
            "s1", "p1", "chat", "b1", None, None, messages,
            SegmentState.PENDING, 0, "2026-01-01T00:00:00Z",
        )
        messages.clear()
        self.assertEqual(len(request.messages), 1)
        with self.assertRaises(Exception): request.state = SegmentState.FAILED
        with self.assertRaises(TypeError):
            SummaryRequest("s", "p", "chat", "b", None, None, ("bad",), SegmentState.PENDING, 0, "now")


class ShortTermMemoryBufferTests(unittest.TestCase):
    PROFILE = "profile-1"
    SOURCE = "chat"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.resources = []

    def tearDown(self) -> None:
        for resource in self.resources: resource.close()
        self.temp.cleanup()

    def _store(self):
        store = SQLiteShortTermStore(Path(self.temp.name) / "memory.sqlite3")
        self.resources.append(store)
        return store

    def _buffer(self, **options):
        options.setdefault("profile_id", self.PROFILE)
        options.setdefault("source_type", self.SOURCE)
        buffer = ShortTermMemoryBuffer(**options)
        self.resources.append(buffer)
        return buffer

    def test_breakpoint_and_character_thresholds_keep_active_text(self) -> None:
        breakpoint_buffer = self._buffer(store=self._store(), max_breakpoints=2)
        breakpoint_buffer.add(role="user", content="first")
        self.assertIsNone(breakpoint_buffer.mark_breakpoint(topic="one"))
        breakpoint_buffer.add(role="user", content="second")
        prepared = breakpoint_buffer.mark_breakpoint(topic="two")
        self.assertEqual((prepared["topic"], prepared["state"]), ("one", "pending"))
        self.assertEqual([message.content for message in breakpoint_buffer.messages], ["second"])

        char_buffer = self._buffer(max_breakpoints=10, max_chars=8)
        char_buffer.add(role="user", content="first")
        char_buffer.mark_breakpoint(topic="one")
        prepared = char_buffer.add(role="user", content="long second")
        self.assertEqual(prepared["topic"], "one")
        self.assertEqual([message.content for message in char_buffer.messages], ["long second"])

    def test_unbroken_message_and_returned_lists_remain_owned_by_buffer(self) -> None:
        buffer = self._buffer(max_breakpoints=10, max_chars=5)
        self.assertIsNone(buffer.add(role="user", content="long active message"))
        self.assertEqual([message.content for message in buffer.messages], ["long active message"])

        snapshots = self._buffer(max_breakpoints=10, max_chars=100)
        snapshots.add(role="user", content="active")
        snapshots.mark_breakpoint(topic="active")
        messages, breakpoints = snapshots.messages, snapshots.breakpoints
        messages.clear(); breakpoints.clear()
        self.assertEqual([message.content for message in snapshots.messages], ["active"])
        self.assertEqual([item.topic for item in snapshots.breakpoints], ["active"])

    def test_facade_completes_retry_ack_recover_and_requeue_flow(self) -> None:
        buffer = self._buffer(store=self._store(), max_breakpoints=1, max_summary_attempts=1)
        prepared = buffer.add(role="user", content="coffee")
        self.assertIsNone(prepared)
        prepared = buffer.mark_breakpoint(topic="coffee")
        claimed = buffer.claim_next_summary(
            owner="worker", lease_seconds=10, now="2026-07-25T00:00:00+00:00",
        )
        self.assertEqual(claimed.segment_id, prepared["segment_id"])
        self.assertEqual(buffer.recover_expired_summaries(now="2026-07-25T00:01:00+00:00"), 1)
        claimed = buffer.claim_next_summary(
            owner="worker", lease_seconds=60, now="2026-07-25T00:02:00+00:00",
        )
        failed = buffer.release_summary(
            segment_id=claimed.segment_id, owner="worker", error="failed",
            now="2026-07-25T00:02:01+00:00",
        )
        self.assertEqual(failed.state, SegmentState.FAILED)
        buffer.requeue_failed_summary(segment_id=failed.segment_id)
        claimed = buffer.claim_next_summary(
            owner="worker", lease_seconds=60, now="2026-07-25T00:03:00+00:00",
        )
        completed = buffer.acknowledge_summary(
            segment_id=claimed.segment_id, owner="worker", outcome="remembered",
            episode_id="episode", memory_ids=["memory"], reason=None,
            now="2026-07-25T00:03:01+00:00",
        )
        self.assertEqual(completed.state, SegmentState.COMPLETED)

    def test_close_is_idempotent_and_closes_only_owned_store(self) -> None:
        owned = self._buffer()
        owned_store = owned._store
        owned.close(); owned.close()
        with self.assertRaisesRegex(RuntimeError, "Buffer is closed"):
            _ = owned.messages
        with self.assertRaisesRegex(RuntimeError, "Store is closed"):
            owned_store.list_messages(self.PROFILE, self.SOURCE)

        injected_store = self._store()
        injected = self._buffer(store=injected_store)
        injected.close()
        self.assertEqual(injected_store.list_messages(self.PROFILE, self.SOURCE), [])

    def test_constructor_and_mutator_validation_is_stable(self) -> None:
        store = self._store()
        cases = (
            lambda: ShortTermMemoryBuffer(profile_id="", source_type=self.SOURCE),
            lambda: ShortTermMemoryBuffer(profile_id=self.PROFILE, source_type=""),
            lambda: ShortTermMemoryBuffer(profile_id=self.PROFILE, source_type=self.SOURCE, store=store,
                                          persistence_path=Path(self.temp.name) / "x.sqlite3"),
            lambda: ShortTermMemoryBuffer(profile_id=self.PROFILE, source_type=self.SOURCE,
                                          max_summary_attempts=0),
        )
        for operation in cases:
            with self.subTest(operation=operation):
                with self.assertRaises(ValueError): operation()
        buffer = self._buffer(store=store)
        for operation in (
            lambda: buffer.add(role="", content="text"),
            lambda: buffer.add(role="user", content=""),
            lambda: buffer.mark_breakpoint(topic=3),
        ):
            with self.subTest(operation=operation):
                with self.assertRaises((TypeError, ValueError)): operation()
