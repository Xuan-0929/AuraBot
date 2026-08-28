"""Close Main Agent through real Context, existing MemoryService, tools, idle archive, and ACK.

Only external model and speech delivery are controlled: prior Profile memory is recalled into a real
Snapshot, ``say_to_user`` crosses the Harness, physical ACK enters Context, then idle conversation
ingestion returns to MemoryService and clears the submitted Context buffer.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from backend.main_agent import ContextStore, SnapshotProjector
from backend.main_agent.runtime import MainAgentRuntime
from backend.main_agent.tool_runtime import ToolSurfacePolicy
from backend.memory.embedding import HashEmbeddingProvider
from backend.memory.extractor import StructuredMemoryExtractor
from backend.memory.service import MemoryService
from backend.memory.stores.qdrant import QdrantLocalVectorStore
from backend.memory.stores.sqlite import SQLiteMemoryStore


def tool_delta(call_id: str, name: str, arguments: str) -> dict:
    return {"choices": [{"delta": {"tool_calls": [{
        "index": 0, "id": call_id, "function": {"name": name, "arguments": arguments},
    }]}}]}


class ExtractionModel:
    payload = {
        "memories": [{
            "content": "用户喜欢低酸咖啡豆",
            "memory_type": "preference",
            "importance": 0.8,
            "confidence": 0.9,
            "entities": ["低酸咖啡豆"],
        }],
        "entities": [{"name": "低酸咖啡豆"}],
    }

    def extract_memory_payload(self, **_kwargs):
        return self.payload

    async def extract_memory_payload_async(self, **_kwargs):
        return self.payload


class SilentMainAgentE2ETests(unittest.IsolatedAsyncioTestCase):
    async def test_recall_snapshot_provider_say_and_physical_ack(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = ContextStore(root / "context.sqlite3")
            embedding = HashEmbeddingProvider(dimension=32)
            memory = MemoryService(
                metadata_store=SQLiteMemoryStore(root / "memory.sqlite3"),
                vector_store=QdrantLocalVectorStore(path=root / "qdrant", vector_size=32),
                embedding_provider=embedding,
                extractor=StructuredMemoryExtractor(ExtractionModel()),
            )
            memory.remember(
                profile_id="profile_a", content="以前聊过咖啡", source_type="chat",
                source_id="prior",
            )
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
                memory_service=memory,
                owner_profile_id="profile_a",
                memory_idle_seconds=0.01,
            )
            try:
                await runtime.submit_text("咖啡豆")
                await runtime.wait_until_idle()
                clause = offers[0]
                await runtime.renderer_tts_queued(clause.clause_id)
                await runtime.renderer_playing(clause.clause_id)
                await runtime.renderer_ack(clause.clause_id, complete=True)
                async with asyncio.timeout(1):
                    while context.conversation_batch() is not None:
                        await asyncio.sleep(0.001)

                self.assertEqual(requests[0].snapshot.memory_facts[0]["content"], "用户喜欢低酸咖啡豆")
                self.assertEqual(context.fold().conversation_turns, ())
                self.assertTrue(any(
                    episode["source_type"] == "main_agent_conversation"
                    for episode in memory.debug("profile_a")["episodes"]
                ))
            finally:
                await runtime.shutdown()
                memory.close()
                context.close()
