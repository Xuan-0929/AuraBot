from dataclasses import FrozenInstanceError
from pathlib import Path
import tempfile
import unittest

from backend.memory.short_term.models import (
    BufferStats,
    SegmentState,
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
