"""Verify the single-active-Profile lifecycle and its token-generation switching contract.

The tests use resource-level fakes at the composition seam while exercising real directory validation,
serialized replacement, Soul updates, teardown order, and active-only Memory delegation. They do not
recreate the deleted Backend ProfileStore or personality files.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.main_agent.context import ContextStore
from backend.main_agent.lifecycle import ActiveProfileManager, ProfileResources


class Hub:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.active_profile_id = None
        self.active_generation = 0

    async def set_active(self, profile_id, generation, runtime, speech):
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
    async def browse(self, *, limit):
        return {"profileId": self.profile_id, "limit": limit}

    async def forget(self, **kwargs):
        return {"forgottenMemoryIds": list(kwargs["memory_ids"])}

    async def retry(self, job_id):
        return {"jobId": job_id, "state": "pending"}


class ActiveProfileManagerTests(unittest.IsolatedAsyncioTestCase):
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
            manager = ActiveProfileManager(Path(directory), hub, build)
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
            manager = ActiveProfileManager(root, hub, build)
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

    async def test_invalid_or_inactive_profile_never_opens_a_directory(self) -> None:
        calls: list[str] = []
        hub = Hub(calls)

        async def build(profile_id, path, soul):
            return ProfileResources(
                profile_id, Runtime(profile_id, soul, calls),
                Owner("speech", profile_id, calls), Memory("memory", profile_id, calls),
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = ActiveProfileManager(root, hub, build)
            with self.assertRaises(ValueError):
                await manager.activate("../escape", "Soul")
            await manager.activate("profile_a", "Soul")
            with self.assertRaises(RuntimeError):
                await manager.browse_memory("profile_b", 10)
            self.assertFalse((root.parent / "escape").exists())
            await manager.shutdown()

    async def test_profile_a_and_b_reopen_independent_context_and_soul(self) -> None:
        calls: list[str] = []
        hub = Hub(calls)

        async def build(profile_id, path, soul):
            context = ContextStore(path / "context.sqlite3")
            runtime = Runtime(profile_id, soul, calls)
            runtime.context = context
            return ProfileResources(
                profile_id, runtime, Owner("speech", profile_id, calls),
                Memory("memory", profile_id, calls), closeables=(context,),
            )

        with tempfile.TemporaryDirectory() as directory:
            manager = ActiveProfileManager(Path(directory), hub, build)
            await manager.activate("profile_a", "Soul A")
            manager.runtime.context.append_event("user_segment", {
                "segment_id": "a-1", "input_id": "a", "text": "only A",
            })
            await manager.activate("profile_b", "Soul B")
            manager.runtime.context.append_event("user_segment", {
                "segment_id": "b-1", "input_id": "b", "text": "only B",
            })
            await manager.activate("profile_a", "Soul A restored")

            self.assertEqual(manager.runtime.soul, "Soul A restored")
            self.assertEqual(
                [turn["content"] for turn in manager.runtime.context.fold().conversation_turns],
                ["only A"],
            )
            self.assertEqual(manager.runtime.profile_id, "profile_a")
            await manager.shutdown()


if __name__ == "__main__":
    unittest.main()
