"""Protect durable conversation, causal Observation folding, and bounded prompt projection.

The tests use public SQLite and Snapshot APIs. They verify stable user segments, Renderer-ACK-only assistant
language, last-event Observation order, and a JSON-native prompt that preserves recent facts under budget.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.main_agent.context import ClauseState, ContextStore, FoldedContext
from backend.main_agent.snapshot import SnapshotBudget, SnapshotProjector


class ContextStoreTests(unittest.TestCase):
    def test_segments_update_in_place_and_complete_ack_preserves_conversation_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ContextStore(Path(directory) / "context.sqlite3")
            store.append_event("user_segment", {"segment_id": "s1", "input_id": "i1", "text": "你"})
            store.append_event("user_segment", {"segment_id": "s1", "input_id": "i1", "text": "你好"})
            store.create_clause("c1", "我在。")
            store.transition_clause("c1", ClauseState.TTS_QUEUED)
            store.transition_clause("c1", ClauseState.PLAYING)
            self.assertTrue(store.ack_clause("c1", complete=True))
            store.append_event("user_segment", {"segment_id": "s2", "input_id": "i1", "text": "继续"})

            self.assertEqual(
                store.fold().conversation_turns,
                (
                    {"role": "user", "content": "你好", "segment_id": "s1"},
                    {"role": "assistant", "content": "我在。", "clause_id": "c1"},
                    {"role": "user", "content": "继续", "segment_id": "s2"},
                ),
            )
            store.close()

    def test_partial_failed_and_unplayed_clauses_never_enter_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ContextStore(Path(directory) / "context.sqlite3")
            store.create_clause("partial", "没听完")
            store.transition_clause("partial", ClauseState.TTS_QUEUED)
            store.transition_clause("partial", ClauseState.PLAYING)
            self.assertFalse(store.ack_clause("partial", complete=False))
            store.create_clause("failed", "没播放")
            store.transition_clause("failed", ClauseState.FAILED)
            self.assertEqual(store.fold().conversation_turns, ())
            store.close()

    def test_complete_ack_requires_playing_and_is_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ContextStore(Path(directory) / "context.sqlite3")
            store.create_clause("c1", "hello")
            with self.assertRaises(ValueError):
                store.ack_clause("c1", complete=True)
            self.assertEqual(store.get_clause("c1").state, ClauseState.DRAFT)
            self.assertEqual(store.fold().conversation_turns, ())
            store.close()

    def test_observations_keep_only_latest_state_in_last_event_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ContextStore(Path(directory) / "context.sqlite3")
            store.append_event("observation", {"id": "a", "kind": "web_search", "status": "running"})
            store.append_event("observation", {"id": "z", "kind": "web_search", "status": "completed"})
            store.append_event("observation", {"id": "a", "kind": "web_search", "status": "completed"})

            observations = store.fold().observations

            self.assertEqual([item["id"] for item in observations], ["z", "a"])
            self.assertEqual([item["status"] for item in observations], ["completed", "completed"])
            store.close()


class SnapshotProjectorTests(unittest.TestCase):
    def test_snapshot_has_one_conversation_representation_and_independent_json_payload(self) -> None:
        context = FoldedContext(
            conversation_turns=({"role": "user", "content": "hello", "segment_id": "s1"},),
            observations=({"id": "o1", "status": "completed", "result": {"ok": True}},),
            think_note=None,
            task_facts=(),
        )
        source_runtime = {"mode": "respond", "nested": {"grant": "g1"}}
        snapshot = SnapshotProjector().project(
            context,
            soul="Aura",
            runtime_state=source_runtime,
            allowed_tools=({"type": "function", "function": {"name": "say_to_user"}},),
        )

        self.assertFalse(hasattr(snapshot, "transcript"))
        self.assertFalse(hasattr(snapshot, "delivered_clauses"))
        self.assertEqual(snapshot.conversation_turns[0]["content"], "hello")
        source_runtime["nested"]["grant"] = "changed"
        self.assertEqual(snapshot.runtime_state["nested"]["grant"], "g1")
        self.assertNotIn("Aura", snapshot.prompt)
        self.assertLessEqual(len(snapshot.prompt), 12_000)
        self.assertEqual(json.loads(snapshot.prompt)["conversation"][0]["content"], "hello")

    def test_budget_keeps_runtime_and_tools_then_drops_oldest_variable_facts(self) -> None:
        context = FoldedContext(
            conversation_turns=tuple(
                {"role": "user", "content": f"turn-{index}-" + "x" * 120, "segment_id": str(index)}
                for index in range(20)
            ),
            observations=(),
            think_note=None,
            task_facts=(),
        )
        snapshot = SnapshotProjector(SnapshotBudget(max_prompt_chars=900)).project(
            context,
            soul="Aura",
            runtime_state={"mode": "respond"},
            allowed_tools=({"type": "function", "function": {"name": "say_to_user"}},),
        )
        prompt = json.loads(snapshot.prompt)
        self.assertEqual(prompt["runtime_state"], {"mode": "respond"})
        self.assertEqual(prompt["allowed_tool_surface"][0]["function"]["name"], "say_to_user")
        self.assertEqual(prompt["conversation"][-1]["segment_id"], "19")
        self.assertLessEqual(len(snapshot.prompt), 900)

    def test_oversized_old_observation_does_not_hide_small_latest_observation(self) -> None:
        context = FoldedContext(
            conversation_turns=(),
            observations=(
                {"id": "old", "kind": "web_read", "status": "completed", "result": {"content": "x" * 5000}},
                {"id": "latest", "kind": "web_search", "status": "completed", "result": {"ok": True}},
            ),
            think_note=None,
            task_facts=(),
        )

        snapshot = SnapshotProjector().project(context, soul="Aura")

        self.assertEqual([item["id"] for item in snapshot.observations], ["latest"])
        self.assertLessEqual(len(snapshot.prompt), 12_000)
