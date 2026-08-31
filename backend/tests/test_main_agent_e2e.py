"""Close Main Agent through real Context, MemoryService, tools, idle archive, and restart.

Only external model and delivery are controlled. Tests cover recall and physical ACK end to end, then prove
a persisted minimal pending boundary reuses Memory source-id idempotency after restart without re-extraction
before Context atomically commits its summary and cleanup.
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
        "summary": "用户在讨论咖啡豆，并偏好低酸选择。",
    }

    def __init__(self):
        self.async_calls = 0

    def extract_memory_payload(self, **_kwargs):
        return self.payload

    async def extract_memory_payload_async(self, **_kwargs):
        self.async_calls += 1
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
            prior = memory.remember(
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

                self.assertEqual(
                    requests[0].snapshot.memory_facts[0],
                    {
                        "text": "用户喜欢低酸咖啡豆",
                        "memory_ids": prior["memory_ids"],
                    },
                )
                self.assertEqual(context.fold().conversation_turns, ())
                self.assertTrue(any(
                    episode["source_type"] == "main_agent_conversation"
                    for episode in memory.debug("profile_a")["episodes"]
                ))
            finally:
                await runtime.shutdown()
                memory.close()
                context.close()

    async def test_restart_finishes_context_archive_from_persisted_episode_without_reextracting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context_path = root / "context.sqlite3"
            memory_path = root / "memory.sqlite3"
            qdrant_path = root / "qdrant"
            model = ExtractionModel()
            context = ContextStore(context_path)
            context.append_event("user_segment", {
                "segment_id": "persisted", "input_id": "old", "text": "我想选低酸咖啡豆",
            })
            context.set_last_user_activity(10.0)
            batch = context.freeze_conversation_archive()
            memory = MemoryService(
                metadata_store=SQLiteMemoryStore(memory_path),
                vector_store=QdrantLocalVectorStore(path=qdrant_path, vector_size=32),
                embedding_provider=HashEmbeddingProvider(dimension=32),
                extractor=StructuredMemoryExtractor(model),
            )
            first = await memory.remember_conversation(
                profile_id="profile_a",
                turns=context.conversation_archive_turns(batch),
                source_id=batch.source_id,
            )
            memory.close()
            context.close()

            reopened_context = ContextStore(context_path)
            self.assertEqual(reopened_context.pending_conversation_archive(), batch)
            reopened_memory = MemoryService(
                metadata_store=SQLiteMemoryStore(memory_path),
                vector_store=QdrantLocalVectorStore(path=qdrant_path, vector_size=32),
                embedding_provider=HashEmbeddingProvider(dimension=32),
                extractor=StructuredMemoryExtractor(model),
            )

            async def provider(_request):
                if False:
                    yield {}

            async def renderer(_clause):
                return None

            runtime = MainAgentRuntime(
                context_store=reopened_context,
                snapshot_projector=SnapshotProjector(),
                tool_policy=ToolSurfacePolicy(),
                provider=provider,
                renderer=renderer,
                executors={},
                soul="test",
                memory_service=reopened_memory,
                owner_profile_id="profile_a",
                memory_idle_seconds=300,
                wall_clock=lambda: 400.0,
            )
            try:
                await runtime.startup()
                async with asyncio.timeout(1):
                    while reopened_context.conversation_batch() is not None:
                        await asyncio.sleep(0.001)

                self.assertEqual(model.async_calls, 1)
                self.assertEqual(
                    reopened_context.fold().conversation_summaries,
                    ({
                        "kind": "conversation_summary",
                        "source_id": batch.source_id,
                        "content": first["summary"],
                    },),
                )
            finally:
                await runtime.shutdown()
                reopened_memory.close()
                reopened_context.close()
