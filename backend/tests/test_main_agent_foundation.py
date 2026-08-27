"""验证 Main Agent 档案、上下文账本和不可变 Prompt 投影的基础契约。

这些测试以真实临时目录和 SQLite 覆盖 ProfileStore 的受控文件所有权及 SOUL 并发语义，
再通过 ContextStore 验证 renderer ACK 才能把助手语言放入 Canonical Context。SnapshotProjector
只消费折叠后的事实，因此后续 scheduler、tool runtime 与语音模块可复用这些稳定边界。
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.main_agent.context import ClauseState, ContextStore
from backend.main_agent.profiles import (
    ProfileConflictError,
    ProfileStore,
    ProfileValidationError,
)
from backend.main_agent.snapshot import SnapshotBudget, SnapshotProjector


class ProfileStoreTests(unittest.TestCase):
    def test_create_read_update_and_recoverable_archive_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "profiles"
            store = ProfileStore(root)

            profile = store.create("aura-1", soul="初始人格")
            self.assertEqual(profile.profile_id, "aura-1")
            self.assertEqual(profile.soul.text, "初始人格")
            self.assertTrue((profile.path / "profile.yaml").is_file())
            self.assertTrue((profile.path / "SOUL.md").is_file())
            self.assertTrue((profile.path / "main-agent.sqlite3").is_file())
            self.assertTrue((profile.path / "memory.sqlite3").is_file())
            self.assertTrue((profile.path / "qdrant").is_dir())

            updated = store.update_soul("aura-1", "更新人格", profile.soul.etag)
            self.assertEqual(store.read("aura-1").soul, updated)
            archived = store.archive("aura-1")
            self.assertTrue(archived.is_dir())
            self.assertFalse((root / "aura-1").exists())
            restored = store.restore(archived)
            self.assertEqual(restored.profile_id, "aura-1")

    def test_rejects_path_like_ids_invalid_soul_and_etag_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ProfileStore(Path(directory))
            for profile_id in ("", "../escape", "a/b", ".hidden", "white space"):
                with self.subTest(profile_id=profile_id):
                    with self.assertRaises(ProfileValidationError):
                        store.create(profile_id)
            with self.assertRaises(ProfileValidationError):
                store.create("aura", soul="x" * (32 * 1024 + 1))

            profile = store.create("aura", soul="one")
            with self.assertRaises(ProfileConflictError):
                store.update_soul("aura", "two", "stale")
            with self.assertRaises(ProfileConflictError):
                store.create("aura")

    def test_detects_valid_external_edits_and_keeps_last_known_good_on_bad_edit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ProfileStore(Path(directory))
            original = store.create("aura", soul="one")
            soul_path = original.path / "SOUL.md"
            soul_path.write_text("external", encoding="utf-8")
            externally_changed = store.read("aura")
            self.assertEqual(externally_changed.soul.text, "external")
            self.assertNotEqual(externally_changed.soul.etag, original.soul.etag)

            soul_path.write_bytes(b"\xff\xfe")
            fallback = store.read("aura")
            self.assertEqual(fallback.soul, externally_changed.soul)
            with self.assertRaises(ProfileConflictError):
                store.update_soul("aura", "new", original.soul.etag)

    def test_update_conflicts_against_invalid_current_soul_without_overwriting_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ProfileStore(Path(directory))
            profile = store.create("aura", soul="safe")
            soul_path = profile.path / "SOUL.md"
            invalid = b"\xffexternal-edit"
            soul_path.write_bytes(invalid)

            with self.assertRaises(ProfileConflictError):
                store.update_soul("aura", "replacement", profile.soul.etag)
            self.assertEqual(soul_path.read_bytes(), invalid)

    def test_rejects_symlinked_profile_root_and_archive_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real_root = root / "real-profiles"
            real_root.mkdir()
            link_root = root / "profiles-link"
            link_root.symlink_to(real_root, target_is_directory=True)
            with self.assertRaises(ProfileValidationError):
                ProfileStore(link_root)

            store = ProfileStore(root / "profiles")
            store.create("aura")
            outside = root / "outside"
            outside.mkdir()
            (store.root / ".archive").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ProfileValidationError):
                store.archive("aura")
            self.assertTrue((store.root / "aura").is_dir())

    def test_archive_requires_owned_manifest_exact_contents_and_real_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ProfileStore(Path(directory) / "profiles")
            store.create("aura")
            archived = store.archive("aura")
            store.create("aura")
            with self.assertRaises(ProfileConflictError):
                store.restore(archived)

            store.archive("aura")
            destination = store.root / "aura"
            destination.symlink_to(Path(directory) / "elsewhere")
            with self.assertRaises(ProfileConflictError):
                store.restore(archived)
            destination.unlink()

            (archived / "unexpected.txt").write_text("tamper", encoding="utf-8")
            with self.assertRaises(ProfileValidationError):
                store.restore(archived)

            fabricated = store.root / ".archive" / "fabricated"
            fabricated.mkdir()
            (fabricated / "profile.yaml").write_text("id: fake\n", encoding="utf-8")
            (fabricated / "SOUL.md").write_text("x", encoding="utf-8")
            (fabricated / "main-agent.sqlite3").touch()
            (fabricated / "memory.sqlite3").touch()
            (fabricated / "qdrant").mkdir()
            with self.assertRaises(ProfileValidationError):
                store.restore(fabricated)

    def test_restore_rejects_invalid_archived_soul_without_publishing_or_consuming_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ProfileStore(Path(directory) / "profiles")
            invalid_contents = {
                "bad-utf8": b"\xff\xfe",
                "too-large": b"x" * (32 * 1024 + 1),
            }
            for profile_id, invalid_soul in invalid_contents.items():
                with self.subTest(profile_id=profile_id):
                    store.create(profile_id, soul="safe")
                    archived = store.archive(profile_id)
                    manifest = archived.parent / f".{archived.name}.archive.yaml"
                    (archived / "SOUL.md").write_bytes(invalid_soul)

                    with self.assertRaises(ProfileValidationError):
                        store.restore(archived)

                    self.assertTrue(archived.is_dir())
                    self.assertTrue(manifest.is_file())
                    self.assertFalse((store.root / profile_id).exists())


class ContextStoreTests(unittest.TestCase):
    def test_public_events_cannot_forge_delivered_assistant_language_or_non_json_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ContextStore(Path(directory) / "main-agent.sqlite3")
            with self.assertRaises(ValueError):
                store.append_event("assistant_clause", {"text": "forged"})
            with self.assertRaises(ValueError):
                store.append_event("transcript", {1: "bad key"})
            with self.assertRaises(ValueError):
                store.append_event("transcript", {"number": float("nan")})
            self.assertEqual(store.fold().delivered_clauses, ())

    def test_only_complete_ack_atomically_delivers_assistant_clause(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ContextStore(Path(directory) / "main-agent.sqlite3")
            clause = store.create_clause("clause-1", "我听见你了。")
            self.assertEqual(clause.state, ClauseState.DRAFT)
            store.transition_clause("clause-1", ClauseState.TTS_QUEUED)
            store.transition_clause("clause-1", ClauseState.PLAYING)
            self.assertFalse(store.ack_clause("clause-1", complete=False))
            self.assertEqual(store.fold().delivered_clauses, ())
            self.assertEqual(store.get_clause("clause-1").state, ClauseState.INTERRUPTED)

            store.create_clause("clause-2", "完整播放。")
            store.transition_clause("clause-2", ClauseState.TTS_QUEUED)
            store.transition_clause("clause-2", ClauseState.PLAYING)
            self.assertTrue(store.ack_clause("clause-2", complete=True))
            folded = store.fold()
            self.assertEqual(folded.delivered_clauses, ("完整播放。",))
            self.assertEqual(store.get_clause("clause-2").state, ClauseState.DELIVERED)
            store.close()

    def test_explicit_recovery_interrupts_nonterminal_clauses_without_constructor_side_effect(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "main-agent.sqlite3"
            store = ContextStore(path)
            for clause_id, target in (
                ("draft", None), ("queued", ClauseState.TTS_QUEUED), ("playing", ClauseState.PLAYING),
            ):
                store.create_clause(clause_id, "未播放")
                if target is ClauseState.TTS_QUEUED:
                    store.transition_clause(clause_id, target)
                if target is ClauseState.PLAYING:
                    store.transition_clause(clause_id, ClauseState.TTS_QUEUED)
                    store.transition_clause(clause_id, ClauseState.PLAYING)
            store.append_event("transcript", {"text": "第一版"})
            store.append_event("transcript", {"text": "最终输入"})
            store.append_event("observation", {"id": "search-1", "state": "running"})
            store.append_event("observation", {"id": "search-1", "state": "completed", "result": "资料"})
            store.append_event("think_note", {"text": "先查资料"})
            store.append_event("think_note", {"text": "资料已到"})
            store.append_event("task_fact", {"id": "task-1", "status": "pending"})
            store.append_event("task_fact", {"id": "task-1", "status": "running"})
            reopened = ContextStore(path)
            self.assertEqual(reopened.get_clause("draft").state, ClauseState.DRAFT)
            self.assertEqual(reopened.get_clause("queued").state, ClauseState.TTS_QUEUED)
            self.assertEqual(reopened.get_clause("playing").state, ClauseState.PLAYING)
            self.assertEqual(reopened.recover_interrupted_clauses(), 3)
            for clause_id in ("draft", "queued", "playing"):
                self.assertEqual(reopened.get_clause(clause_id).state, ClauseState.INTERRUPTED)
                self.assertEqual(store.get_clause(clause_id).state, ClauseState.INTERRUPTED)
            folded = reopened.fold()
            self.assertEqual(folded.transcript, "最终输入")
            self.assertEqual(folded.observations, ({"id": "search-1", "result": "资料", "state": "completed"},))
            self.assertEqual(folded.think_note, "资料已到")
            self.assertEqual(folded.task_facts, ({"id": "task-1", "status": "running"},))
            reopened.close()
            store.close()

    def test_ack_rolls_back_clause_transition_when_assistant_event_insert_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ContextStore(Path(directory) / "main-agent.sqlite3")
            store.create_clause("clause", "完整播放")
            store.transition_clause("clause", ClauseState.TTS_QUEUED)
            store.transition_clause("clause", ClauseState.PLAYING)
            store._connection.execute(
                """
                CREATE TRIGGER reject_assistant_event BEFORE INSERT ON context_events
                WHEN NEW.event_type = 'assistant_clause'
                BEGIN SELECT RAISE(ABORT, 'injected failure'); END;
                """
            )
            with self.assertRaises(sqlite3.IntegrityError):
                store.ack_clause("clause", complete=True)
            self.assertEqual(store.get_clause("clause").state, ClauseState.PLAYING)
            self.assertEqual(store.fold().delivered_clauses, ())


class SnapshotProjectorTests(unittest.TestCase):
    def test_rejects_total_budget_that_cannot_represent_a_json_object(self) -> None:
        with self.assertRaises(ValueError):
            SnapshotBudget(max_prompt_chars=1)

    def test_projection_is_immutable_deterministic_and_budgeted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ContextStore(Path(directory) / "main-agent.sqlite3")
            store.append_event("transcript", {"text": "用户问题"})
            store.append_event("observation", {"id": "b", "state": "completed", "result": "B"})
            store.append_event("observation", {"id": "a", "state": "running"})
            store.append_event("think_note", {"text": "思考"})
            store.append_event("task_fact", {"id": "task", "status": "running"})
            store.create_clause("one", "已听见")
            store.transition_clause("one", ClauseState.TTS_QUEUED)
            store.transition_clause("one", ClauseState.PLAYING)
            store.ack_clause("one", complete=True)

            projector = SnapshotProjector(
                SnapshotBudget(max_prompt_chars=360, soul_chars=32, transcript_chars=32,
                delivered_chars=32, observation_chars=128, memory_chars=32,
                               runtime_chars=32, tool_chars=32)
            )
            first = projector.project(
                store.fold(), soul="人格" * 30, memory_facts=[{"id": "m", "text": "记忆"}],
                runtime_state={"user_speaking": False}, allowed_tools=[{"name": "placeholder"}],
            )
            second = projector.project(
                store.fold(), soul="人格" * 30, memory_facts=[{"text": "记忆", "id": "m"}],
                runtime_state={"user_speaking": False}, allowed_tools=[{"name": "placeholder"}],
            )
            self.assertEqual(first.prompt, second.prompt)
            self.assertLessEqual(len(first.prompt), 360)
            self.assertEqual(first.transcript, "用户问题")
            self.assertEqual(first.delivered_clauses, ("已听见",))
            self.assertEqual(tuple(item["id"] for item in first.observations), ("a", "b"))
            with self.assertRaises(TypeError):
                first.runtime_state["user_speaking"] = True
            with self.assertRaises(AttributeError):
                first.delivered_clauses.append("no")

    def test_total_budget_keeps_required_control_sections_and_matches_snapshot_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ContextStore(Path(directory) / "main-agent.sqlite3")
            store.append_event("transcript", {"text": "用户输入" * 30})
            projector = SnapshotProjector(
                SnapshotBudget(max_prompt_chars=260, soul_chars=100, transcript_chars=100,
                               delivered_chars=100, observation_chars=100, memory_chars=100,
                               runtime_chars=100, tool_chars=100)
            )
            snapshot = projector.project(
                store.fold(), soul="人格" * 30, memory_facts=[{"text": "记忆" * 30}],
                runtime_state={"user_speaking": False}, allowed_tools=[{"name": "say_to_user"}],
            )
            prompt = json.loads(snapshot.prompt)
            self.assertLessEqual(len(snapshot.prompt), 260)
            self.assertIn("runtime_state", prompt)
            self.assertIn("allowed_tool_surface", prompt)
            self.assertEqual(prompt["transcript"], snapshot.transcript)
            self.assertEqual(prompt["soul"], snapshot.soul)
            self.assertEqual(prompt["runtime_state"], dict(snapshot.runtime_state))
            self.assertEqual(prompt["allowed_tool_surface"], [dict(item) for item in snapshot.allowed_tool_surface])
            with self.assertRaises(ValueError):
                SnapshotProjector(SnapshotBudget(max_prompt_chars=2)).project(
                    store.fold(), soul="", runtime_state={"ready": True}, allowed_tools=[]
                )

    def test_snapshot_rejects_non_json_values_and_non_string_mapping_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            projector = SnapshotProjector()
            context = ContextStore(Path(directory) / "main-agent.sqlite3").fold()
            with self.assertRaises(ValueError):
                projector.project(context, soul="x", runtime_state={1: "bad"})
            with self.assertRaises(ValueError):
                projector.project(context, soul="x", memory_facts=[{"n": float("nan")}])


if __name__ == "__main__":
    unittest.main()
