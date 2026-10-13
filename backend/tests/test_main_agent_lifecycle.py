"""Verify single-active-Profile resource isolation and token-generation switching.

Focused lifecycle tests use resource fakes for ordering; the A→B→A regression composes real ContextStore,
SQLiteMemoryStore, MemoryService, and MainAgentRuntime around an in-memory derived vector index. No test
recreates deleted Backend Profile, personality, or Main-Agent-specific Memory management surfaces.
"""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from backend.main_agent.context import ContextStore
from backend.main_agent.lifecycle import ActiveProfileManager, ProfileResources
from backend.main_agent.runtime import MainAgentRuntime
from backend.main_agent.snapshot import SnapshotProjector
from backend.main_agent.tool_runtime import ToolSurfacePolicy
from backend.memory.embedding import HashEmbeddingProvider
from backend.memory.service import MemoryService
from backend.memory.stores.sqlite import SQLiteMemoryStore


class Hub:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.active_profile_id = None
        self.active_generation = 0

    async def set_active(self, profile_id, generation, resources):
        self.calls.append(f"hub:set:{profile_id}:{generation}")
        self.active_profile_id = profile_id
        self.active_generation = generation

    async def clear_active(self, profile_id, generation):
        self.calls.append(f"hub:clear:{profile_id}:{generation}")
        if self.active_profile_id == profile_id and self.active_generation == generation:
            self.active_profile_id = None


class Runtime:
    def __init__(self, profile_id: str, soul: str, calls: list[str]) -> None:
        self.profile_id = profile_id
        self.soul = soul
        self.calls = calls

    async def update_soul(self, soul: str) -> None:
        self.calls.append(f"soul:{self.profile_id}:{soul}")
        self.soul = soul

    async def shutdown(self) -> None:
        self.calls.append(f"runtime:stop:{self.profile_id}")


class Owner:
    def __init__(self, name: str, profile_id: str, calls: list[str]) -> None:
        self.name = name
        self.profile_id = profile_id
        self.calls = calls

    async def shutdown(self) -> None:
        self.calls.append(f"{self.name}:stop:{self.profile_id}")


class Memory(Owner):
    """Profile-owned closeable matching the existing MemoryService lifecycle only."""


class ResourceVectorStore:
    """Lightweight derived index whose close state makes Profile teardown observable."""

    def __init__(self) -> None:
        self.points = {}
        self.closed = False

    def upsert(self, memory_id, vector, payload):
        self.points[memory_id] = {"vector": vector, "payload": payload}

    def search(self, vector, *, profile_id, limit, filters=None):
        return []

    def close(self):
        self.closed = True


class ActiveProfileManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_profile_switch_waits_for_inflight_memory_recall_before_closing_resources(self) -> None:
        calls: list[str] = []
        hub = Hub(calls)
        memories: dict[str, MemoryService] = {}
        vectors: dict[str, ResourceVectorStore] = {}
        started = threading.Event()
        release = threading.Event()

        async def build(profile_id, path, soul):
            vector = ResourceVectorStore()
            memory = MemoryService(
                metadata_store=SQLiteMemoryStore(path / "memory.sqlite3"),
                vector_store=vector,
                embedding_provider=HashEmbeddingProvider(dimension=16),
            )
            memories[profile_id] = memory
            vectors[profile_id] = vector
            return ProfileResources(
                profile_id,
                Runtime(profile_id, soul, calls),
                Owner("speech", profile_id, calls),
                memory,
            )

        with tempfile.TemporaryDirectory() as directory:
            manager = ActiveProfileManager(Path(directory), hub, build, generation_seed=0)
            await manager.activate("profile_a", "Soul A")
            memory_a = memories["profile_a"]
            original_recall = memory_a.recall

            def blocking_recall(**kwargs):
                started.set()
                release.wait(2)
                return original_recall(**kwargs)

            memory_a.recall = blocking_recall
            recall = asyncio.create_task(memory_a.recall_context_async(
                profile_id="profile_a", query="红茶", max_chars=200,
            ))
            self.assertTrue(await asyncio.to_thread(started.wait, 0.5))

            switching = asyncio.create_task(manager.activate("profile_b", "Soul B"))
            await asyncio.sleep(0)
            self.assertFalse(switching.done())
            self.assertFalse(vectors["profile_a"].closed)
            release.set()
            await asyncio.wait_for(switching, 1)
            await asyncio.wait_for(recall, 1)

            self.assertTrue(vectors["profile_a"].closed)
            self.assertEqual(manager.active_profile_id, "profile_b")
            self.assertFalse(vectors["profile_b"].closed)
            await manager.shutdown()

    async def test_same_profile_updates_soul_and_generation_without_rebuilding_runtime(self) -> None:
        calls: list[str] = []
        built: list[str] = []
        hub = Hub(calls)

        async def build(profile_id, path, soul):
            built.append(profile_id)
            return ProfileResources(
                profile_id, Runtime(profile_id, soul, calls),
                Owner("speech", profile_id, calls), Memory("memory", profile_id, calls),
            )

        with tempfile.TemporaryDirectory() as directory:
            manager = ActiveProfileManager(Path(directory), hub, build, generation_seed=0)
            first = await manager.activate("profile_a", "Soul A")
            second = await manager.activate("profile_a", "Soul A2")

            self.assertEqual((first, second), (1, 2))
            self.assertEqual(built, ["profile_a"])
            self.assertEqual(manager.runtime.soul, "Soul A2")
            self.assertEqual(hub.active_generation, 2)
            await manager.shutdown()
    async def test_profile_switch_closes_old_voice_before_owned_resources(self) -> None:
        calls: list[str] = []
        hub = Hub(calls)

        async def build(profile_id, path, soul):
            path.mkdir(parents=True, exist_ok=True)
            (path / "context.sqlite3").touch()
            return ProfileResources(
                profile_id, Runtime(profile_id, soul, calls),
                Owner("speech", profile_id, calls), Memory("memory", profile_id, calls),
                closeables=(Owner("context", profile_id, calls),),
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = ActiveProfileManager(root, hub, build, generation_seed=0)
            await manager.activate("profile_a", "Soul A")
            await manager.activate("profile_b", "Soul B")

            clear = calls.index("hub:clear:profile_a:1")
            runtime = calls.index("runtime:stop:profile_a")
            speech = calls.index("speech:stop:profile_a")
            memory = calls.index("memory:stop:profile_a")
            self.assertLess(clear, runtime)
            self.assertLess(runtime, speech)
            self.assertLess(speech, memory)
            self.assertEqual(manager.active_profile_id, "profile_b")
            self.assertFalse((root / "profile_a" / "profile.yaml").exists())
            self.assertFalse((root / "profile_a" / "SOUL.md").exists())
            await manager.shutdown()

    async def test_invalid_profile_never_opens_a_directory(self) -> None:
        calls: list[str] = []
        hub = Hub(calls)

        async def build(profile_id, path, soul):
            return ProfileResources(
                profile_id, Runtime(profile_id, soul, calls),
                Owner("speech", profile_id, calls), Memory("memory", profile_id, calls),
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = ActiveProfileManager(root, hub, build, generation_seed=0)
            with self.assertRaises(ValueError):
                await manager.activate("../escape", "Soul")
            await manager.activate("profile_a", "Soul")
            self.assertFalse((root.parent / "escape").exists())
            await manager.shutdown()

    async def test_real_profile_resources_keep_context_memory_vector_and_soul_isolated(self) -> None:
        calls: list[str] = []
        hub = Hub(calls)
        builds: dict[str, list[ProfileResources]] = {}
        contexts: dict[str, list[ContextStore]] = {}
        vectors: dict[str, list[ResourceVectorStore]] = {}

        async def provider(_request):
            if False:
                yield {}

        async def renderer(_clause):
            return None

        async def build(profile_id, path, soul):
            context = ContextStore(path / "context.sqlite3")
            vector = ResourceVectorStore()
            memory = MemoryService(
                metadata_store=SQLiteMemoryStore(path / "memory.sqlite3"),
                vector_store=vector,
                embedding_provider=HashEmbeddingProvider(dimension=16),
            )
            runtime = MainAgentRuntime(
                context_store=context,
                snapshot_projector=SnapshotProjector(),
                tool_policy=ToolSurfacePolicy(),
                provider=provider,
                renderer=renderer,
                executors={},
                soul=soul,
                memory_service=memory,
                owner_profile_id=profile_id,
                memory_idle_seconds=3_600,
            )
            await runtime.startup()
            resources = ProfileResources(
                profile_id,
                runtime,
                Owner("speech", profile_id, calls),
                memory,
                closeables=(context,),
            )
            builds.setdefault(profile_id, []).append(resources)
            contexts.setdefault(profile_id, []).append(context)
            vectors.setdefault(profile_id, []).append(vector)
            return resources

        with tempfile.TemporaryDirectory() as directory:
            manager = ActiveProfileManager(Path(directory), hub, build, generation_seed=0)
            await manager.activate("profile_a", "Soul A")
            first_a = builds["profile_a"][0]
            first_a_context = contexts["profile_a"][0]
            first_a_context.append_event("user_segment", {
                "segment_id": "a-1", "input_id": "a", "text": "only A",
            })
            await first_a.memory.remember_conversation(
                profile_id="profile_a",
                turns=({"role": "user", "content": "用户喜欢红茶"},),
                source_id="a-memory",
            )
            await manager.activate("profile_a", "Soul A refreshed")
            self.assertIs(manager.runtime, first_a.runtime)
            self.assertEqual(len(builds["profile_a"]), 1)
            self.assertEqual(manager.runtime.soul, "Soul A refreshed")

            await manager.activate("profile_b", "Soul B")
            self.assertTrue(vectors["profile_a"][0].closed)
            with self.assertRaises(sqlite3.ProgrammingError):
                first_a_context.fold()
            with self.assertRaises(sqlite3.ProgrammingError):
                first_a.memory.metadata_store.list_active_memories("profile_a")
            current_b = builds["profile_b"][0]
            current_b_context = contexts["profile_b"][0]
            self.assertEqual(current_b.runtime.soul, "Soul B")
            self.assertEqual(current_b_context.fold().conversation_turns, ())
            self.assertEqual(
                current_b.memory.recall(profile_id="profile_b", query="红茶")["memories"],
                [],
            )
            current_b_context.append_event("user_segment", {
                "segment_id": "b-1", "input_id": "b", "text": "only B",
            })
            await current_b.memory.remember_conversation(
                profile_id="profile_b",
                turns=({"role": "user", "content": "用户喜欢咖啡"},),
                source_id="b-memory",
            )

            await manager.activate("profile_a", "Soul A restored")
            reopened_a = builds["profile_a"][1]
            reopened_a_context = contexts["profile_a"][1]
            self.assertEqual(manager.active_profile_id, "profile_a")
            self.assertEqual(reopened_a.runtime.soul, "Soul A restored")
            self.assertEqual(
                [turn["content"] for turn in reopened_a_context.fold().conversation_turns],
                ["only A"],
            )
            self.assertEqual(
                [item["content"] for item in reopened_a.memory.recall(
                    profile_id="profile_a", query="红茶"
                )["memories"]],
                ["用户喜欢红茶"],
            )
            self.assertEqual(
                reopened_a.memory.recall(profile_id="profile_a", query="咖啡")["memories"],
                [],
            )
            await manager.shutdown()

    async def test_failed_retirement_blocks_every_target_until_resource_cleanup_succeeds(self) -> None:
        calls: list[str] = []
        builds: list[str] = []
        allow_close = False
        hub = Hub(calls)

        class FailingMemory(Memory):
            async def shutdown(self) -> None:
                if not allow_close:
                    raise OSError("archive storage still closing")
                await super().shutdown()

        async def build(profile_id, path, soul):
            builds.append(profile_id)
            (path / "preserved-context.txt").write_text("unarchived private " + profile_id)
            return ProfileResources(profile_id, Runtime(profile_id, soul, calls),
                Owner("speech", profile_id, calls),
                FailingMemory("memory", profile_id, calls) if profile_id == "a" else Memory("memory", profile_id, calls),
                closeables=(Owner("context", profile_id, calls),))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = ActiveProfileManager(root, hub, build, generation_seed=0)
            await manager.activate("a", "Soul A")
            with self.assertRaises(Exception):
                await manager.activate("b", "Soul B")
            self.assertIsNone(manager.active_profile_id)
            self.assertIsNone(hub.active_profile_id)
            self.assertEqual(builds, ["a"])
            for target in ("b", "c", "a"):
                with self.assertRaisesRegex(RuntimeError, "profile_cleanup_incomplete"):
                    await manager.activate(target, "Soul target", force=True)
                self.assertIsNone(manager.active_profile_id)
                self.assertIsNone(hub.active_profile_id)
                self.assertEqual(builds, ["a"])
            self.assertEqual((root / "a" / "preserved-context.txt").read_text(), "unarchived private a")
            allow_close = True
            await manager.activate("b", "Soul B")
            self.assertEqual(manager.active_profile_id, "b")
            self.assertEqual(builds, ["a", "b"])
            self.assertEqual(manager._retiring, {})
            # Successfully closed owners are not invoked again when a later owner needs a retry.
            self.assertEqual(calls.count("runtime:stop:a"), 1)
            self.assertEqual(calls.count("speech:stop:a"), 1)
            self.assertEqual(calls.count("memory:stop:a"), 1)
            self.assertLess(calls.index("context:stop:a"), calls.index("hub:set:b:3"))
            self.assertEqual((root / "a" / "preserved-context.txt").read_text(), "unarchived private a")
            await manager.shutdown()
