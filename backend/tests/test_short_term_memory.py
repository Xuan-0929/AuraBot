import unittest

from backend.memory.short_term.models import (
    BufferStats,
    SegmentState,
    ShortTermMessage,
    SummaryRequest,
)
from backend.memory.short_term.policy import ShortTermPolicy


class ShortTermPolicyTests(unittest.TestCase):
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
