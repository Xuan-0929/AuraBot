"""Close the silent Main Agent path through real Context, Profile Memory, tools, and physical ACK.

Only model and speech delivery are faked: prior profile memory is extracted into the focused store, a
confirmed user turn recalls it into a real Snapshot, ``say_to_user`` crosses the Harness, and the clause
enters canonical conversation only after explicit queued/playing/complete Renderer transitions.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.main_agent import ContextStore, SnapshotProjector
from backend.main_agent.memory import ProfileMemory
from backend.main_agent.runtime import MainAgentRuntime
from backend.main_agent.tool_runtime import ToolSurfacePolicy
from backend.memory.types import MemoryCandidate


def tool_delta(call_id: str, name: str, arguments: str) -> dict:
    return {"choices": [{"delta": {"tool_calls": [{
        "index": 0, "id": call_id, "function": {"name": name, "arguments": arguments},
    }]}}]}


class Extractor:
    def extract(self, *, content, metadata, memory_type=None, importance=0.5):
        return ([MemoryCandidate(content="用户喜欢低酸咖啡豆", metadata=metadata)], [])


class SilentMainAgentE2ETests(unittest.IsolatedAsyncioTestCase):
    async def test_recall_snapshot_provider_say_and_physical_ack(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = ContextStore(root / "context.sqlite3")
            memory = ProfileMemory(
                profile_id="profile_a", path=root / "memory.sqlite3", extractor=Extractor(),
                idle_seconds=0.01,
            )
            await memory.startup()
            await memory.record_confirmed_input(
                segment_id="prior", content="以前聊过咖啡", input_id="old", revision=1,
            )
            await memory.wait_until_idle()
            requests = []
            offers = []

            async def provider(request):
                requests.append(request)
                yield tool_delta("say", "say_to_user", '{"text":"记得，选低酸的。"}')

            async def renderer(clause):
                offers.append(clause)

            runtime = MainAgentRuntime(
                context_store=context,
                snapshot_projector=SnapshotProjector(),
                tool_policy=ToolSurfacePolicy(),
                provider=provider,
                renderer=renderer,
                executors={},
                soul="test",
                memory_bridge=memory,
            )
            try:
                await runtime.submit_text("咖啡豆")
                await runtime.wait_until_idle()
                clause = offers[0]
                await runtime.renderer_tts_queued(clause.clause_id)
                await runtime.renderer_playing(clause.clause_id)
                await runtime.renderer_ack(clause.clause_id, complete=True)

                self.assertEqual(requests[0].snapshot.memory_facts[0]["content"], "用户喜欢低酸咖啡豆")
                self.assertEqual(
                    [(turn["role"], turn["content"]) for turn in context.fold().conversation_turns],
                    [("user", "咖啡豆"), ("assistant", "记得，选低酸的。")],
                )
            finally:
                await runtime.shutdown()
                await memory.shutdown()
                context.close()
