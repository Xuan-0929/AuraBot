"""Workspace context checks also ensure declared paths reach the model without filesystem scanning.
Protect canonical Context folding, minimal archive boundaries, and prompt projection.

The tests verify stable user segments, Renderer-ACK-only assistant language, current pending archive boundaries,
canonical boundary reconstruction, atomic archive cleanup, Observation ordering, and bounded JSON snapshots.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.main_agent.context import ClauseState, ContextStore, FoldedContext
from backend.main_agent.snapshot import SnapshotBudget, SnapshotProjector


class ContextStoreTests(unittest.TestCase):
    def test_idle_activity_is_cleared_only_when_transaction_sees_no_pending_or_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ContextStore(Path(directory) / "context.sqlite3")
            store.set_last_user_activity(10.0)
            self.assertTrue(store.clear_last_user_activity_if_idle())
            self.assertIsNone(store.get_last_user_activity())

            store.append_event("user_segment", {
                "segment_id": "new", "input_id": "new-input", "text": "新输入",
            })
            store.set_last_user_activity(20.0)
            self.assertFalse(store.clear_last_user_activity_if_idle())
            self.assertEqual(store.get_last_user_activity(), 20.0)
            store.close()

    def test_frozen_archive_keeps_source_turns_and_boundary_after_later_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ContextStore(Path(directory) / "context.sqlite3")
            store.append_event(
                "user_segment", {"segment_id": "old", "input_id": "input-old", "text": "旧问题"}
            )

            pending = store.freeze_conversation_archive()
            store.append_event(
                "user_segment", {"segment_id": "new", "input_id": "input-new", "text": "新问题"}
            )

            self.assertEqual(pending.source_id, "main-agent-context:1:1")
            self.assertEqual(pending.through_sequence, 1)
            self.assertEqual(store.conversation_archive_turns(pending), ({"role": "user", "content": "旧问题"},))
            self.assertEqual(store.pending_conversation_archive(), pending)
            self.assertEqual(store.conversation_batch().source_id, "main-agent-context:1:2")
            store.close()

    def test_pending_archive_survives_context_store_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "context.sqlite3"
            store = ContextStore(path)
            store.append_event(
                "user_segment", {"segment_id": "old", "input_id": "input-old", "text": "冻结内容"}
            )
            frozen = store.freeze_conversation_archive()
            store.close()

            reopened = ContextStore(path)
            self.assertEqual(reopened.pending_conversation_archive(), frozen)
            self.assertEqual(
                reopened.conversation_archive_turns(frozen),
                ({"role": "user", "content": "冻结内容"},),
            )
            reopened.close()

    def test_archive_commit_atomically_deletes_pending_marker_with_raw_turns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ContextStore(Path(directory) / "context.sqlite3")
            store.append_event(
                "user_segment", {"segment_id": "old", "input_id": "input-old", "text": "归档我"}
            )
            pending = store.freeze_conversation_archive()

            store.commit_conversation_archive(pending, "旧对话摘要")

            self.assertIsNone(store.pending_conversation_archive())
            self.assertIsNone(store.conversation_batch())
            self.assertEqual(store.fold().conversation_summaries[0]["source_id"], pending.source_id)
            store.close()

    def test_archive_failure_preserves_pending_marker_raw_turns_and_no_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "context.sqlite3"
            store = ContextStore(path)
            store.append_event(
                "user_segment", {"segment_id": "old", "input_id": "input-old", "text": "不要半提交"}
            )
            pending = store.freeze_conversation_archive()
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                create trigger fail_pending_archive_delete
                before delete on context_events
                when old.event_type = 'user_segment'
                begin
                  select raise(abort, 'pending archive delete failed');
                end;
                """
            )
            connection.close()

            with self.assertRaises(sqlite3.IntegrityError):
                store.commit_conversation_archive(pending, "不能留下的摘要")

            self.assertEqual(store.pending_conversation_archive(), pending)
            self.assertEqual(
                store.conversation_batch().turns,
                ({"role": "user", "content": "不要半提交"},),
            )
            self.assertEqual(store.fold().conversation_summaries, ())
            store.close()

    def test_last_user_activity_survives_reopen_and_clears_only_when_idle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "context.sqlite3"
            store = ContextStore(path)
            store.set_last_user_activity(123.5)
            store.close()

            reopened = ContextStore(path)
            self.assertEqual(reopened.get_last_user_activity(), 123.5)
            self.assertTrue(reopened.clear_last_user_activity_if_idle())
            self.assertIsNone(reopened.get_last_user_activity())
            reopened.close()

    def test_archive_commit_keeps_one_summary_and_preserves_later_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ContextStore(Path(directory) / "context.sqlite3")
            store.append_event(
                "user_segment", {"segment_id": "old", "input_id": "i1", "text": "比较 A 和 B"}
            )
            batch = store.freeze_conversation_archive()
            store.append_event(
                "user_segment", {"segment_id": "new", "input_id": "i2", "text": "继续第二个"}
            )
            summary = "用户正在比较方案 A 和方案 B，并倾向继续使用方案 B。"

            self.assertEqual(
                store.conversation_archive_turns(batch),
                ({"role": "user", "content": "比较 A 和 B"},),
            )
            store.commit_conversation_archive(batch, summary)
            store.commit_conversation_archive(batch, summary)

            folded = store.fold()
            self.assertEqual(folded.conversation_summaries, ({
                "kind": "conversation_summary",
                "source_id": batch.source_id,
                "content": summary,
            },))
            self.assertEqual(
                folded.conversation_turns,
                ({"role": "user", "content": "继续第二个", "segment_id": "new"},),
            )
            self.assertEqual(
                store.conversation_batch().turns,
                ({"role": "user", "content": "继续第二个"},),
            )
            store.close()

    def test_archive_commit_rolls_back_summary_when_raw_delete_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "context.sqlite3"
            store = ContextStore(path)
            store.append_event(
                "user_segment", {"segment_id": "old", "input_id": "i1", "text": "保留原文"}
            )
            batch = store.freeze_conversation_archive()
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                create trigger fail_archive_raw_delete
                before delete on context_events
                when old.event_type = 'user_segment'
                begin
                  select raise(abort, 'raw delete failed');
                end;
                """
            )
            connection.close()

            with self.assertRaises(sqlite3.IntegrityError):
                store.commit_conversation_archive(batch, "这条摘要必须回滚")

            self.assertEqual(
                store.conversation_batch().turns,
                ({"role": "user", "content": "保留原文"},),
            )
            self.assertEqual(store.pending_conversation_archive(), batch)
            self.assertEqual(store.fold().conversation_summaries, ())
            store.close()

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
    def test_user_workspace_locations_enter_model_context_without_directory_scanning(self) -> None:
        directories = {"default": "/user-selected/install", "home": "/Users/example"}
        projector = SnapshotProjector(workspaces=directories)
        directories["default"] = "/later-change"
        snapshot = projector.project(FoldedContext((), (), None, ()), soul="Aura")
        context = json.loads(snapshot.prompt)["user_workspaces"]
        self.assertEqual(context["directories"], [
            {"id": "default", "path": "/user-selected/install"},
            {"id": "home", "path": "/Users/example"},
        ])
        self.assertIn("not Pi filesystem access boundaries", context["purpose"])


    def test_section_budget_truncates_text_without_removing_any_protected_segment(self) -> None:
        turns = tuple({
            "role": "user",
            "content": f"segment-{index}-" + "x" * 120,
            "segment_id": f"active-{index}",
        } for index in range(6))
        context = FoldedContext(turns, (), None, ())

        snapshot = SnapshotProjector(SnapshotBudget(conversation_chars=100)).project(
            context,
            soul="Aura",
            runtime_state={"mode": "respond"},
            protected_segment_ids=tuple(f"active-{index}" for index in range(6)),
        )

        self.assertEqual(
            [turn["segment_id"] for turn in snapshot.conversation_turns],
            [f"active-{index}" for index in range(6)],
        )
        self.assertTrue(snapshot.conversation_turns[-1]["content"])
        self.assertLessEqual(len(snapshot.prompt), 12_000)

    def test_total_budget_truncates_text_without_removing_any_protected_segment(self) -> None:
        turns = tuple({
            "role": "user",
            "content": f"segment-{index}-" + "x" * 180,
            "segment_id": f"active-{index}",
        } for index in range(6))
        context = FoldedContext(turns, (), None, ())

        snapshot = SnapshotProjector(SnapshotBudget(
            max_prompt_chars=520,
            conversation_chars=6_000,
        )).project(
            context,
            soul="Aura",
            runtime_state={"mode": "respond"},
            protected_segment_ids=tuple(f"active-{index}" for index in range(6)),
        )

        self.assertEqual(
            [turn["segment_id"] for turn in snapshot.conversation_turns],
            [f"active-{index}" for index in range(6)],
        )
        self.assertTrue(snapshot.conversation_turns[-1]["content"])
        self.assertLessEqual(len(snapshot.prompt), 520)

    def test_total_budget_evicts_old_context_before_all_active_input_segments(self) -> None:
        context = FoldedContext(
            conversation_turns=(
                {"role": "user", "content": "很早以前的问题" + "o" * 200, "segment_id": "old"},
                {"role": "user", "content": "帮我看看明天上海的天气", "segment_id": "active-1"},
                {"role": "user", "content": "顺便看看要不要带伞", "segment_id": "active-2"},
            ),
            observations=({"id": "o1", "status": "completed", "result": "x" * 300},),
            think_note="旧思考" * 80,
            task_facts=({"task_id": "t1", "description": "旧任务" * 80},),
            conversation_summaries=({
                "kind": "conversation_summary", "source_id": "summary-old", "content": "旧摘要" * 120,
            },),
        )
        projector = SnapshotProjector(SnapshotBudget(max_prompt_chars=700))

        snapshot = projector.project(
            context,
            soul="Aura",
            memory_facts=({"text": "旧记忆" * 100, "memory_ids": ["m1"]},),
            runtime_state={"mode": "respond"},
            allowed_tools=({"type": "function", "function": {"name": "say_to_user"}},),
            protected_segment_ids=("active-1", "active-2"),
        )

        contents = [turn["content"] for turn in snapshot.conversation_turns]
        self.assertIn("帮我看看明天上海的天气", contents)
        self.assertIn("顺便看看要不要带伞", contents)
        self.assertNotIn(context.conversation_turns[0]["content"], contents)
        self.assertLessEqual(len(snapshot.prompt), 700)

    def test_oversized_active_input_is_deterministically_truncated_but_not_removed(self) -> None:
        context = FoldedContext(
            conversation_turns=({
                "role": "user", "content": "开头-" + "很长的问题" * 500 + "-结尾", "segment_id": "active",
            },),
            observations=(),
            think_note=None,
            task_facts=(),
        )
        projector = SnapshotProjector(SnapshotBudget(
            max_prompt_chars=500,
            conversation_chars=20,
        ))

        first = projector.project(
            context,
            soul="Aura",
            runtime_state={"mode": "respond"},
            protected_segment_ids=("active",),
        )
        second = projector.project(
            context,
            soul="Aura",
            runtime_state={"mode": "respond"},
            protected_segment_ids=("active",),
        )

        self.assertEqual(first.conversation_turns, second.conversation_turns)
        self.assertEqual(len(first.conversation_turns), 1)
        self.assertTrue(first.conversation_turns[0]["content"])
        self.assertLess(len(first.conversation_turns[0]["content"]), len(context.conversation_turns[0]["content"]))
        self.assertLessEqual(len(first.prompt), 500)

    def test_old_conversation_can_be_removed_while_short_active_input_stays_complete(self) -> None:
        context = FoldedContext(
            conversation_turns=(
                {"role": "assistant", "content": "旧回答" * 150, "clause_id": "old"},
                {"role": "user", "content": "当前短问题", "segment_id": "active"},
            ),
            observations=(),
            think_note=None,
            task_facts=(),
        )

        snapshot = SnapshotProjector(SnapshotBudget(max_prompt_chars=350)).project(
            context,
            soul="Aura",
            runtime_state={"mode": "respond"},
            protected_segment_ids=("active",),
        )

        self.assertEqual(
            snapshot.conversation_turns,
            ({"role": "user", "content": "当前短问题", "segment_id": "active"},),
        )
        self.assertLessEqual(len(snapshot.prompt), 350)

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

    def test_summary_has_separate_bounded_section_and_oversized_memory_does_not_hide_later_fact(self):
        summaries = tuple({
            "kind": "conversation_summary",
            "source_id": f"source-{index}",
            "content": f"summary-{index}-" + "s" * 900,
        } for index in range(3))
        context = FoldedContext(
            conversation_turns=({"role": "user", "content": "继续第二个", "segment_id": "new"},),
            observations=(),
            think_note=None,
            task_facts=(),
            conversation_summaries=summaries,
        )

        snapshot = SnapshotProjector().project(
            context,
            soul="Aura",
            memory_facts=(
                {"text": "x" * 3_000, "memory_ids": ["oversized"]},
                {"text": "用户喜欢红茶", "memory_ids": ["m1"]},
            ),
        )

        prompt = json.loads(snapshot.prompt)
        self.assertEqual(prompt["conversation_summaries"][-1]["source_id"], "source-2")
        self.assertLessEqual(
            sum(len(json.dumps(item, ensure_ascii=False)) for item in prompt["conversation_summaries"]),
            2_000,
        )
        self.assertEqual(snapshot.memory_facts, ({"text": "用户喜欢红茶", "memory_ids": ["m1"]},))
        self.assertLessEqual(len(snapshot.prompt), 12_000)
