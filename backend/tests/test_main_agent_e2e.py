"""Close the silent Main Agent path through real Context, Memory recall, tools, and delivery.

This end-to-end test keeps only provider and Renderer as fakes: confirmed desktop text enters the
real ``MainAgentRuntime``, automatic ``MemoryBridge`` recall projects SQLite truth into the frozen
Snapshot, a normal ``say_to_user`` provider tool crosses the real harness, and Renderer ACK commits
the clause to canonical Context.  Voice, search→CONTINUE, and Task proposal→fact companion E2Es live
beside their richer realtime/runtime/Task fixtures and are listed in the Task-9 verification report.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.main_agent import ContextStore, SnapshotProjector
from backend.main_agent.memory import MemoryBridge
from backend.main_agent.runtime import MainAgentRuntime
from backend.main_agent.tool_runtime import ToolSurfacePolicy
from backend.memory.stores.sqlite import SQLiteMemoryStore
from backend.memory.types import MemoryCandidate


def tool_delta(index: int, call_id: str, name: str, arguments: str) -> dict:
    return {"choices": [{"delta": {"tool_calls": [{
        "index": index,
        "id": call_id,
        "function": {"name": name, "arguments": arguments},
    }]}}]}


class SilentMainAgentE2ETests(unittest.IsolatedAsyncioTestCase):
    async def test_confirmed_text_recall_snapshot_provider_and_renderer_ack(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory_store = SQLiteMemoryStore(root / "memory.sqlite3")
            context_store = ContextStore(root / "main-agent.sqlite3")
            episode_id = memory_store.create_episode(
                profile_id="aura",
                source_type="prior_conversation",
                source_id="older",
                role="user",
                content="以前聊过咖啡",
                metadata={},
            )
            memory_id = memory_store.create_memory(
                profile_id="aura",
                episode_id=episode_id,
                candidate=MemoryCandidate(content="用户喜欢低酸咖啡豆"),
            )
            bridge = MemoryBridge(profile_id="aura", store=memory_store)
            requests = []

            def provider(request):
                requests.append(request)
                return [tool_delta(0, "say", "say_to_user", '{"text":"记得，选低酸的。"}')]

            async def renderer(_clause):
                return True

            runtime = MainAgentRuntime(
                context_store=context_store,
                snapshot_projector=SnapshotProjector(),
                tool_policy=ToolSurfacePolicy(),
                provider=provider,
                renderer=renderer,
                executors={},
                soul="test",
                memory_bridge=bridge,
            )
            try:
                await runtime.startup()
                await runtime.submit_text("今天想买咖啡豆")
                await runtime.wait_until_idle()

                self.assertEqual(len(requests), 1)
                self.assertEqual(requests[0].snapshot.memory_facts[0]["id"], memory_id)
                self.assertEqual(context_store.fold().transcript, "今天想买咖啡豆")
                self.assertEqual(context_store.fold().delivered_clauses, ("记得，选低酸的。",))
            finally:
                await runtime.shutdown()
                await bridge.shutdown()
                context_store.close()
                memory_store.close()


if __name__ == "__main__":
    unittest.main()
