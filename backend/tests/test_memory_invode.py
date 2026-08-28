"""
验证 Memory 服务、整段对话提炼、HTTP 路由、检索组件与持久化适配器的可观察行为。

测试使用临时 SQLite 和内存向量替身覆盖 remember/recall/forget、提取与排序流程；
短期记忆缓冲区由独立测试模块负责，避免在本文件重复验证同一契约。
"""

import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.memory.invode import (
    HashEmbeddingProvider,
    MemoryService,
    QdrantLocalVectorStore,
    SQLiteMemoryStore,
    StructuredMemoryExtractor,
    create_embedding_provider,
    create_default_memory_service,
    create_memory_router,
)
from backend.memory.extraction.schemas import parse_structured_extraction_payload
from backend.memory.extraction.providers import OpenAICompatibleMemoryExtractionModel
from backend.memory.consolidation.strategies import (
    ConflictingPreferenceStrategy,
    DuplicateMemoryStrategy,
)
from backend.memory.retrieval.fusion import fuse_candidates
from backend.memory.retrieval.context import pack_context
from backend.memory.retrieval.query import QueryAnalyzer
from backend.memory.retrieval.ranking import hydrate_and_rank_memories
from backend.memory.retrieval.rerank import RuleBasedMemoryReranker
from backend.memory.retrieval.relations import expand_relation_candidates
from backend.memory.retrieval.seeds import EntitySeedRetriever, FtsSeedRetriever, VectorSeedRetriever
from backend.memory.retrieval.trace import build_recall_trace
from backend.memory.validators import validate_memory_candidate
from backend.memory.types import MemoryCandidate


class InMemoryVectorStore:
    def __init__(self):
        self.points = {}

    def upsert(self, memory_id, vector, payload):
        self.points[memory_id] = {"vector": vector, "payload": payload}

    def search(self, vector, *, profile_id, limit, filters=None):
        filters = filters or {}
        matches = []

        for memory_id, point in self.points.items():
            payload = point["payload"]

            if payload["profile_id"] != profile_id:
                continue

            if any(payload.get(key) != value for key, value in filters.items()):
                continue

            score = sum(left * right for left, right in zip(vector, point["vector"]))
            matches.append({"memory_id": memory_id, "score": score})

        return sorted(matches, key=lambda item: item["score"], reverse=True)[:limit]

    def delete(self, memory_ids):
        for memory_id in memory_ids:
            self.points.pop(memory_id, None)

    def health(self):
        return {"backend": "in-memory", "points": len(self.points)}


class FakeExtractionModel:
    def __init__(self, payload):
        self.payload = payload

    def extract_memory_payload(self, *, content, metadata, memory_type=None, importance=0.5):
        return self.payload


class AsyncExtractionModel(FakeExtractionModel):
    def __init__(self, payload):
        super().__init__(payload)
        self.contents = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def extract_memory_payload_async(
        self, *, content, metadata, memory_type=None, importance=0.5,
    ):
        self.contents.append(content)
        self.started.set()
        await self.release.wait()
        return self.payload


class BlockingAsyncHttpClient:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.closed = asyncio.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        self.closed.set()

    async def post(self, *_args, **_kwargs):
        self.started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


class ReverseMemoryReranker:
    def rerank(self, *, query, memories, analysis, limit):
        return list(reversed(memories))[:limit]


class MemoryConversationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.store = SQLiteMemoryStore(Path(self.tmpdir.name) / "memory.sqlite3")
        self.vector_store = InMemoryVectorStore()
        self.model = AsyncExtractionModel({
            "memories": [{
                "content": "用户喜欢红茶",
                "memory_type": "preference",
                "importance": 0.8,
                "confidence": 0.9,
                "entities": ["红茶"],
            }],
            "entities": [{"name": "红茶"}],
        })
        self.service = MemoryService(
            metadata_store=self.store,
            vector_store=self.vector_store,
            embedding_provider=HashEmbeddingProvider(dimension=32),
            extractor=StructuredMemoryExtractor(self.model),
        )

    async def asyncTearDown(self):
        self.store.close()
        self.tmpdir.cleanup()

    async def test_conversation_is_persisted_once_with_its_stable_source(self):
        self.model.release.set()
        turns = (
            {"role": "user", "content": "我喜欢红茶"},
            {"role": "assistant", "content": "我记住了。"},
        )

        first = await self.service.remember_conversation(
            profile_id="profile-a", turns=turns, source_id="context:1:2",
        )
        repeated = await self.service.remember_conversation(
            profile_id="profile-a", turns=turns, source_id="context:1:2",
        )

        self.assertEqual(first, repeated)
        self.assertEqual(self.model.contents, ["user: 我喜欢红茶\nassistant: 我记住了。"])
        snapshot = self.store.debug_snapshot("profile-a")
        self.assertEqual(len(snapshot["episodes"]), 1)
        self.assertEqual(snapshot["episodes"][0]["source_type"], "main_agent_conversation")
        self.assertEqual(snapshot["episodes"][0]["source_id"], "context:1:2")
        self.assertEqual([item["content"] for item in snapshot["memories"]], ["用户喜欢红茶"])

    async def test_cancelled_conversation_extraction_writes_nothing(self):
        task = asyncio.create_task(self.service.remember_conversation(
            profile_id="profile-a",
            turns=({"role": "user", "content": "先不要保存"},),
            source_id="context:1:1",
        ))
        await self.model.started.wait()

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        snapshot = self.store.debug_snapshot("profile-a")
        self.assertEqual(snapshot["episodes"], [])
        self.assertEqual(snapshot["memories"], [])
        self.assertEqual(self.vector_store.points, {})

    async def test_conversation_rejects_invalid_turns_before_extraction(self):
        with self.assertRaises(ValueError):
            await self.service.remember_conversation(
                profile_id="profile-a",
                turns=({"role": "tool", "content": "not a conversation turn"},),
                source_id="context:1:1",
            )
        self.assertEqual(self.model.contents, [])

    async def test_openai_async_request_propagates_cancellation_and_closes_client(self):
        client = BlockingAsyncHttpClient()
        model = OpenAICompatibleMemoryExtractionModel(
            base_url="https://memory.invalid/v1",
            model_name="memory-model",
            api_key="test-key",
            timeout_seconds=30,
            async_client_factory=lambda **_kwargs: client,
        )
        task = asyncio.create_task(model.extract_memory_payload_async(
            content="user: 取消这次提炼",
            metadata={},
        ))
        await client.started.wait()

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertTrue(client.cancelled.is_set())
        self.assertTrue(client.closed.is_set())


class MemoryInvodeTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        sqlite_path = Path(self.tmpdir.name) / "memory.sqlite3"
        self.store = SQLiteMemoryStore(sqlite_path)
        self.vector_store = InMemoryVectorStore()
        self.service = MemoryService(
            metadata_store=self.store,
            vector_store=self.vector_store,
            embedding_provider=HashEmbeddingProvider(dimension=32),
        )

    def tearDown(self):
        self.store.close()
        self.tmpdir.cleanup()

    def test_remember_persists_episode_metadata_and_vector_payload(self):
        result = self.service.remember(
            profile_id="user-1",
            content="用户喜欢热拿铁，工作日上午经常喝。",
            source_type="chat",
            source_id="message-1",
            memory_type="preference",
            importance=0.8,
            metadata={"channel": "desktop"},
        )

        stored = self.store.get_memory(result["memory_id"])

        self.assertEqual(stored["profile_id"], "user-1")
        self.assertEqual(stored["episode_id"], result["episode_id"])
        self.assertEqual(stored["memory_type"], "preference")
        self.assertEqual(stored["metadata"]["channel"], "desktop")
        self.assertIn(result["memory_id"], self.vector_store.points)

    def test_recall_uses_vector_candidates_and_hydrates_from_sqlite(self):
        first = self.service.remember(
            profile_id="user-1",
            content="用户喜欢热拿铁。",
            source_type="chat",
            source_id="message-1",
            memory_type="preference",
        )
        second = self.service.remember(
            profile_id="user-1",
            content="用户明天要提交项目周报。",
            source_type="chat",
            source_id="message-2",
            memory_type="event",
        )

        result = self.service.recall(
            profile_id="user-1",
            query="拿铁",
            limit=5,
            filters={"memory_type": "preference"},
        )

        self.assertEqual([item["memory_id"] for item in result["memories"]], [first["memory_id"]])
        self.assertNotIn(second["memory_id"], [item["memory_id"] for item in result["memories"]])
        self.assertEqual(result["memories"][0]["content"], "用户喜欢热拿铁。")

    def test_forget_archives_sqlite_rows_and_removes_vectors(self):
        remembered = self.service.remember(
            profile_id="user-1",
            content="用户讨厌过甜的咖啡。",
            source_type="chat",
            source_id="message-1",
            memory_type="preference",
        )

        result = self.service.forget(
            profile_id="user-1",
            memory_ids=[remembered["memory_id"]],
            reason="user requested deletion",
        )

        self.assertEqual(result["forgotten_count"], 1)
        self.assertEqual(self.store.get_memory(remembered["memory_id"])["state"], "archived")
        self.assertNotIn(remembered["memory_id"], self.vector_store.points)

    def test_sqlite_store_migrates_phase_zero_schema_without_losing_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sqlite_path = Path(tmpdir) / "legacy.sqlite3"
            connection = sqlite3.connect(sqlite_path)
            connection.executescript(
                """
                create table memory_episodes (
                  id text primary key,
                  profile_id text not null,
                  source_type text not null,
                  source_id text,
                  content text not null,
                  metadata_json text not null default '{}',
                  created_at text not null
                );
                create table memory_items (
                  id text primary key,
                  profile_id text not null,
                  episode_id text not null,
                  memory_type text not null,
                  state text not null check (state in ('active', 'archived')),
                  content text not null,
                  importance real not null,
                  metadata_json text not null default '{}',
                  created_at text not null,
                  updated_at text not null
                );
                insert into memory_episodes (
                  id, profile_id, source_type, source_id, content, metadata_json, created_at
                ) values (
                  'episode_legacy', 'user-1', 'chat', 'm1', '旧对话', '{}', '2026-01-01T00:00:00+00:00'
                );
                insert into memory_items (
                  id, profile_id, episode_id, memory_type, state, content,
                  importance, metadata_json, created_at, updated_at
                ) values (
                  'memory_legacy', 'user-1', 'episode_legacy', 'preference', 'active',
                  '用户喜欢热拿铁', 0.7, '{"entities":["热拿铁"]}',
                  '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'
                );
                """
            )
            connection.close()

            migrated = SQLiteMemoryStore(sqlite_path)
            try:
                self.assertGreaterEqual(migrated.schema_version(), 2)
                self.assertIn("role", migrated.table_columns("memory_episodes"))
                self.assertIn("confidence", migrated.table_columns("memory_items"))
                self.assertIn("forget_reason", migrated.table_columns("memory_items"))

                memory = migrated.get_memory("memory_legacy")
                self.assertEqual(memory["content"], "用户喜欢热拿铁")
                self.assertEqual(memory["confidence"], 0.7)
                self.assertEqual(memory["state"], "active")
            finally:
                migrated.close()

    def test_remember_extracts_multiple_memories_entities_relations_and_audit(self):
        result = self.service.remember(
            profile_id="user-1",
            content="我喜欢热拿铁，明天提醒我提交项目周报。",
            source_type="chat",
            source_id="message-1",
            role="user",
            metadata={"channel": "desktop"},
        )

        self.assertGreaterEqual(len(result["memory_ids"]), 2)
        self.assertEqual(result["memory_id"], result["memory_ids"][0])

        debug = self.service.debug("user-1")
        self.assertEqual(len(debug["episodes"]), 1)
        self.assertGreaterEqual(len(debug["memories"]), 2)
        self.assertTrue(any(item["memory_type"] == "preference" for item in debug["memories"]))
        self.assertTrue(any(item["memory_type"] == "follow_up" for item in debug["memories"]))
        self.assertTrue(any(entity["name"] == "热拿铁" for entity in debug["entities"]))
        self.assertTrue(any(log["action"] == "remember" for log in debug["audit_logs"]))

    def test_recall_combines_vector_fts_entity_relation_and_trace_sources(self):
        self.service.remember(
            profile_id="user-1",
            content="用户喜欢热拿铁。",
            source_type="chat",
            source_id="message-1",
            memory_type="preference",
            metadata={"entities": ["热拿铁"]},
        )
        self.service.remember(
            profile_id="user-1",
            content="用户明天要去咖啡店和阿杰讨论项目。",
            source_type="chat",
            source_id="message-2",
            memory_type="event",
            metadata={"entities": ["咖啡店", "阿杰", "项目"]},
        )

        result = self.service.recall(
            profile_id="user-1",
            query="阿杰项目那件事和咖啡有关吗？",
            limit=5,
            context_hints={"entities": ["阿杰"]},
        )

        contents = [memory["content"] for memory in result["memories"]]
        self.assertTrue(any("阿杰" in content for content in contents))
        self.assertTrue(any("热拿铁" in content for content in contents))
        self.assertIn("trace", result)
        self.assertTrue({"vector", "fts", "entity"} & set(result["trace"]["sources_by_memory"].values()))
        self.assertTrue(result["trace"]["relation_expansions"])

    def test_forget_by_episode_archives_derived_memories_and_removes_vectors(self):
        remembered = self.service.remember(
            profile_id="user-1",
            content="用户喜欢冷萃咖啡，也喜欢晚上写代码。",
            source_type="chat",
            source_id="message-1",
        )

        result = self.service.forget(
            profile_id="user-1",
            episode_ids=[remembered["episode_id"]],
            reason="delete source conversation",
        )

        self.assertEqual(result["forgotten_count"], len(remembered["memory_ids"]))
        self.assertTrue(all(memory_id not in self.vector_store.points for memory_id in remembered["memory_ids"]))
        self.assertFalse(
            self.service.recall(profile_id="user-1", query="冷萃咖啡", limit=5)["memories"]
        )

    def test_due_returns_active_time_based_memories(self):
        due = self.service.remember(
            profile_id="user-1",
            content="提醒用户提交周报",
            source_type="chat",
            source_id="message-1",
            memory_type="follow_up",
            metadata={"valid_from": "2026-06-10T09:00:00+00:00"},
        )
        self.service.remember(
            profile_id="user-1",
            content="提醒用户下个月整理照片",
            source_type="chat",
            source_id="message-2",
            memory_type="follow_up",
            metadata={"valid_from": "2026-07-10T09:00:00+00:00"},
        )

        result = self.service.due("user-1", now_iso="2026-06-11T09:00:00+00:00")

        self.assertEqual([item["memory_id"] for item in result["memories"]], due["memory_ids"])

    def test_router_exposes_memory_workflow_health_and_validation(self):
        app = FastAPI()
        app.include_router(create_memory_router(self.service))
        client = TestClient(app)

        remember_response = client.post(
            "/memory/remember",
            json={
                "profile_id": "user-1",
                "content": "我喜欢黑咖啡，明天提醒我买咖啡豆。",
                "source_type": "chat",
                "source_id": "message-1",
                "role": "user",
            },
        )
        self.assertEqual(remember_response.status_code, 200)
        episode_id = remember_response.json()["episode_id"]

        recall_response = client.post(
            "/memory/recall",
            json={
                "profile_id": "user-1",
                "query": "咖啡豆提醒",
                "limit": 5,
                "context_hints": {"entities": ["咖啡豆"]},
            },
        )
        self.assertEqual(recall_response.status_code, 200)
        self.assertIn("trace", recall_response.json())
        self.assertTrue(recall_response.json()["source_episodes"])

        improve_response = client.post("/memory/improve", json={"profile_id": "user-1"})
        self.assertEqual(improve_response.status_code, 200)
        self.assertIn("stale_count", improve_response.json())

        due_response = client.get("/memory/due", params={"profile_id": "user-1", "now_iso": "2100-01-01T00:00:00+00:00"})
        self.assertEqual(due_response.status_code, 200)
        self.assertTrue(due_response.json()["memories"])

        debug_response = client.get("/memory/debug/user-1")
        self.assertEqual(debug_response.status_code, 200)
        self.assertTrue(debug_response.json()["audit_logs"])

        health_response = client.get("/memory/health")
        self.assertEqual(health_response.status_code, 200)
        self.assertEqual(health_response.json()["sqlite"]["backend"], "sqlite")
        self.assertGreaterEqual(
            health_response.json()["sqlite"]["schema_version"],
            2,
        )

        forget_response = client.post(
            "/memory/forget",
            json={"profile_id": "user-1", "episode_ids": [episode_id], "reason": "cleanup"},
        )
        self.assertEqual(forget_response.status_code, 200)
        self.assertGreaterEqual(forget_response.json()["forgotten_count"], 1)

        invalid_forget_response = client.post("/memory/forget", json={"profile_id": "user-1"})
        self.assertEqual(invalid_forget_response.status_code, 400)

    def test_improve_marks_older_conflicting_preference_superseded(self):
        liked = self.service.remember(
            profile_id="user-1",
            content="我喜欢热拿铁。",
            source_type="chat",
            source_id="message-1",
        )
        disliked = self.service.remember(
            profile_id="user-1",
            content="我不喜欢热拿铁。",
            source_type="chat",
            source_id="message-2",
        )

        result = self.service.improve("user-1")

        self.assertEqual(result["superseded_count"], 1)
        self.assertEqual(self.store.get_memory(liked["memory_id"])["state"], "superseded")
        self.assertEqual(self.store.get_memory(disliked["memory_id"])["state"], "active")
        self.assertNotIn(liked["memory_id"], self.vector_store.points)

        recall = self.service.recall(profile_id="user-1", query="热拿铁", limit=5)
        recalled_ids = [memory["memory_id"] for memory in recall["memories"]]
        self.assertIn(disliked["memory_id"], recalled_ids)
        self.assertNotIn(liked["memory_id"], recalled_ids)

    def test_structured_extractor_persists_model_candidates_entities_and_trace_metadata(self):
        service = MemoryService(
            metadata_store=self.store,
            vector_store=self.vector_store,
            embedding_provider=HashEmbeddingProvider(dimension=32),
            extractor=StructuredMemoryExtractor(
                FakeExtractionModel(
                    {
                        "memories": [
                            {
                                "content": "用户喜欢低酸咖啡豆",
                                "memory_type": "preference",
                                "importance": 0.88,
                                "confidence": 0.91,
                                "entities": ["低酸咖啡豆"],
                                "metadata": {"preference_polarity": "positive"},
                            }
                        ],
                        "entities": [
                            {
                                "name": "低酸咖啡豆",
                                "entity_type": "product",
                                "aliases": ["低酸豆"],
                                "summary": "用户偏好的咖啡豆类型",
                            }
                        ],
                    }
                )
            ),
        )

        result = service.remember(
            profile_id="user-1",
            content="我最近在找低酸咖啡豆。",
            source_type="chat",
            source_id="message-structured",
        )

        memory = self.store.get_memory(result["memory_id"])
        debug = self.service.debug("user-1")

        self.assertEqual(memory["content"], "用户喜欢低酸咖啡豆")
        self.assertEqual(memory["metadata"]["preference_polarity"], "positive")
        self.assertEqual(memory["metadata"]["extraction_source"], "structured")
        self.assertTrue(any(entity["name"] == "低酸咖啡豆" for entity in debug["entities"]))

    def test_structured_payload_parser_merges_metadata_and_dedupes_entities(self):
        candidates, entities = parse_structured_extraction_payload(
            {
                "memories": [
                    {
                        "content": "用户喜欢浅烘咖啡",
                        "memory_type": "preference",
                        "entities": ["浅烘咖啡", "浅烘咖啡"],
                    }
                ],
                "entities": [{"name": "浅烘咖啡"}, {"name": "咖啡器具"}],
            },
            base_metadata={"channel": "desktop"},
            default_importance=0.6,
        )

        self.assertEqual(candidates[0].metadata["channel"], "desktop")
        self.assertEqual(candidates[0].metadata["extraction_source"], "structured")
        self.assertEqual(candidates[0].entities, ["浅烘咖啡"])
        self.assertEqual([entity.name for entity in entities], ["浅烘咖啡", "咖啡器具"])

    def test_memory_candidate_rejects_blank_content(self):
        with self.assertRaises(ValueError):
            validate_memory_candidate(
                MemoryCandidate(
                    content=" ",
                    memory_type="preference",
                    importance=0.5,
                    confidence=0.7,
                )
            )

    def test_structured_extractor_rejects_invalid_model_output_and_writes_audit_error(self):
        service = MemoryService(
            metadata_store=self.store,
            vector_store=self.vector_store,
            embedding_provider=HashEmbeddingProvider(dimension=32),
            extractor=StructuredMemoryExtractor(
                FakeExtractionModel(
                    {
                        "memories": [
                            {
                                "content": "",
                                "memory_type": "preference",
                                "importance": 1.2,
                            }
                        ],
                        "entities": [{"name": ""}],
                    }
                )
            ),
        )

        with self.assertRaises(ValueError):
            service.remember(
                profile_id="user-1",
                content="坏输出不要入库",
                source_type="chat",
                source_id="message-invalid",
            )

        debug = self.service.debug("user-1")
        self.assertEqual(debug["memories"], [])
        self.assertTrue(
            any(log["action"] == "remember" and log["error"] for log in debug["audit_logs"])
        )

    def test_improve_marks_near_duplicate_memory_text_stale(self):
        first = self.service.remember(
            profile_id="user-1",
            content="用户喜欢热拿铁。",
            source_type="chat",
            source_id="message-1",
            memory_type="preference",
            metadata={"entities": ["热拿铁"], "preference_polarity": "positive"},
        )
        second = self.service.remember(
            profile_id="user-1",
            content="用户喜欢热拿铁",
            source_type="chat",
            source_id="message-2",
            memory_type="preference",
            metadata={"entities": ["热拿铁"], "preference_polarity": "positive"},
        )

        result = self.service.improve("user-1")

        self.assertEqual(result["stale_count"], 1)
        self.assertEqual(self.store.get_memory(first["memory_id"])["state"], "active")
        self.assertEqual(self.store.get_memory(second["memory_id"])["state"], "stale")

    def test_consolidation_strategies_select_duplicate_and_conflicting_ids(self):
        memories = [
            {
                "memory_id": "old-positive",
                "memory_type": "preference",
                "content": "用户喜欢热拿铁。",
                "metadata": {"entities": ["热拿铁"], "preference_polarity": "positive"},
                "created_at": "2026-01-01T00:00:00+00:00",
            },
            {
                "memory_id": "new-positive",
                "memory_type": "preference",
                "content": "用户喜欢热拿铁",
                "metadata": {"entities": ["热拿铁"], "preference_polarity": "positive"},
                "created_at": "2026-01-02T00:00:00+00:00",
            },
            {
                "memory_id": "new-negative",
                "memory_type": "preference",
                "content": "用户不喜欢热拿铁",
                "metadata": {"entities": ["热拿铁"], "preference_polarity": "negative"},
                "created_at": "2026-01-03T00:00:00+00:00",
            },
        ]

        duplicate_result = DuplicateMemoryStrategy().select(memories)
        conflict_result = ConflictingPreferenceStrategy().select(memories)

        self.assertEqual(duplicate_result.stale_ids, ["new-positive"])
        self.assertEqual(conflict_result.superseded_ids, ["old-positive", "new-positive"])

    def test_seed_retrievers_fuse_and_expand_related_candidates(self):
        self.service.remember(
            profile_id="user-1",
            content="用户喜欢热拿铁。",
            source_type="chat",
            source_id="message-1",
            memory_type="preference",
            metadata={"entities": ["热拿铁"]},
        )
        self.service.remember(
            profile_id="user-1",
            content="用户明天要去咖啡店和阿杰讨论项目。",
            source_type="chat",
            source_id="message-2",
            memory_type="event",
            metadata={"entities": ["咖啡店", "阿杰", "项目"]},
        )

        vector_seeds = VectorSeedRetriever(
            vector_store=self.vector_store,
            embedding_provider=HashEmbeddingProvider(dimension=32),
        ).retrieve(profile_id="user-1", query="热拿铁", limit=5, filters={})
        fts_seeds = FtsSeedRetriever(self.store).retrieve(
            profile_id="user-1",
            query="热拿铁",
            limit=5,
        )
        entity_seeds = EntitySeedRetriever(self.store).retrieve(
            profile_id="user-1",
            entity_names=["阿杰"],
            limit=5,
        )
        fused = fuse_candidates([*vector_seeds, *fts_seeds, *entity_seeds])
        relation_candidates, relations = expand_relation_candidates(
            store=self.store,
            profile_id="user-1",
            seed_ids=[item["memory_id"] for item in fused],
            existing_ids={item["memory_id"] for item in fused},
        )

        self.assertTrue(any(item["source"] == "vector" for item in vector_seeds))
        self.assertTrue(any(item["source"] == "fts" for item in fts_seeds))
        self.assertTrue(any(item["source"] == "entity" for item in entity_seeds))
        self.assertEqual(fused, sorted(fused, key=lambda item: item["score"], reverse=True))
        self.assertTrue(relations)
        self.assertIsInstance(relation_candidates, list)

    def test_ranking_and_trace_preserve_candidate_sources(self):
        remembered = self.service.remember(
            profile_id="user-1",
            content="用户喜欢热拿铁。",
            source_type="chat",
            source_id="message-1",
            memory_type="preference",
        )
        candidates = [
            {"memory_id": remembered["memory_id"], "score": 0.8, "source": "vector"},
            {"memory_id": remembered["memory_id"], "score": 0.6, "source": "fts"},
        ]
        fused = fuse_candidates(candidates)

        ranked = hydrate_and_rank_memories(
            store=self.store,
            profile_id="user-1",
            fused_candidates=fused,
            limit=3,
        )
        trace = build_recall_trace(
            candidates=candidates,
            relation_candidates=[],
            relations=[],
            pre_rerank_memories=ranked,
            ranked_memories=ranked,
        )

        self.assertEqual(ranked[0]["memory_id"], remembered["memory_id"])
        self.assertEqual(ranked[0]["recall_source"], "fts")
        self.assertEqual(trace["vector_candidates"], [remembered["memory_id"]])
        self.assertEqual(trace["fts_candidates"], [remembered["memory_id"]])
        self.assertEqual(trace["sources_by_memory"], {remembered["memory_id"]: "fts"})
        self.assertEqual(trace["pre_rerank"][0]["memory_id"], remembered["memory_id"])
        self.assertEqual(trace["post_rerank"][0]["memory_id"], remembered["memory_id"])

    def test_query_analysis_and_context_packing_honor_hints_and_budget(self):
        analysis = QueryAnalyzer(default_now_iso="2026-06-11T00:00:00+00:00").analyze(
            query="今天阿杰项目有什么提醒？",
            context_hints={"entities": ["咖啡店"]},
        )
        packed = pack_context(
            [
                {"memory_id": "m1", "content": "用户今天要和阿杰讨论项目。"},
                {"memory_id": "m2", "content": "用户喜欢热拿铁。"},
            ],
            max_chars=18,
        )

        self.assertEqual(analysis["time"]["valid_at"], "2026-06-11T00:00:00+00:00")
        self.assertEqual(set(analysis["entities"]), {"咖啡店", "阿杰", "项目"})
        self.assertEqual(packed["memory_ids"], ["m1"])
        self.assertLessEqual(packed["char_count"], 18)

    def test_recall_returns_query_analysis_and_packed_context(self):
        self.service.remember(
            profile_id="user-1",
            content="用户今天要和阿杰讨论项目。",
            source_type="chat",
            source_id="message-1",
            memory_type="event",
            metadata={
                "entities": ["阿杰", "项目"],
                "valid_from": "2026-06-11T00:00:00+00:00",
                "valid_to": "2026-06-12T00:00:00+00:00",
            },
        )

        result = self.service.recall(
            profile_id="user-1",
            query="今天阿杰项目有什么？",
            limit=5,
            context_hints={
                "now_iso": "2026-06-11T00:00:00+00:00",
                "context_budget_chars": 64,
            },
        )

        self.assertEqual(result["analysis"]["time"]["valid_at"], "2026-06-11T00:00:00+00:00")
        self.assertIn("阿杰", result["analysis"]["entities"])
        self.assertIn(result["memories"][0]["memory_id"], result["packed_context"]["memory_ids"])
        self.assertLessEqual(result["packed_context"]["char_count"], 64)

    def test_rule_based_reranker_boosts_entity_and_time_matches(self):
        reranked = RuleBasedMemoryReranker().rerank(
            query="今天阿杰项目有什么？",
            analysis={
                "entities": ["阿杰", "项目"],
                "time": {"valid_at": "2026-06-11T00:00:00+00:00"},
            },
            limit=2,
            memories=[
                {
                    "memory_id": "preference",
                    "content": "用户喜欢热拿铁。",
                    "score": 0.9,
                    "recall_source": "vector",
                    "valid_from": None,
                    "valid_to": None,
                },
                {
                    "memory_id": "event",
                    "content": "用户今天要和阿杰讨论项目。",
                    "score": 0.6,
                    "recall_source": "entity",
                    "valid_from": "2026-06-11T00:00:00+00:00",
                    "valid_to": "2026-06-12T00:00:00+00:00",
                },
            ],
        )

        self.assertEqual([memory["memory_id"] for memory in reranked], ["event", "preference"])
        self.assertGreater(reranked[0]["rerank_score"], reranked[1]["rerank_score"])

    def test_recall_trace_exposes_rerank_score_changes(self):
        self.service.remember(
            profile_id="user-1",
            content="用户喜欢热拿铁。",
            source_type="chat",
            source_id="message-1",
            memory_type="preference",
        )
        self.service.remember(
            profile_id="user-1",
            content="用户今天要和阿杰讨论项目。",
            source_type="chat",
            source_id="message-2",
            memory_type="event",
            metadata={
                "entities": ["阿杰", "项目"],
                "valid_from": "2026-06-11T00:00:00+00:00",
            },
        )

        result = self.service.recall(
            profile_id="user-1",
            query="今天阿杰项目有什么？",
            limit=2,
            context_hints={"now_iso": "2026-06-11T00:00:00+00:00"},
        )

        self.assertIn("pre_rerank", result["trace"])
        self.assertIn("post_rerank", result["trace"])
        self.assertTrue(all("score" in item for item in result["trace"]["pre_rerank"]))
        self.assertTrue(all("rerank_score" in item for item in result["trace"]["post_rerank"]))
        self.assertEqual(
            [item["memory_id"] for item in result["trace"]["post_rerank"]],
            [memory["memory_id"] for memory in result["memories"]],
        )

    def test_retriever_accepts_custom_reranker_without_changing_seed_components(self):
        self.service.remember(
            profile_id="user-1",
            content="用户喜欢热拿铁。",
            source_type="chat",
            source_id="message-1",
            memory_type="preference",
        )
        self.service.remember(
            profile_id="user-1",
            content="用户喜欢冷萃咖啡。",
            source_type="chat",
            source_id="message-2",
            memory_type="preference",
        )
        baseline = self.service.recall(profile_id="user-1", query="咖啡", limit=2)
        baseline_ids = [memory["memory_id"] for memory in baseline["memories"]]
        self.service.retriever.reranker = ReverseMemoryReranker()

        result = self.service.recall(profile_id="user-1", query="咖啡", limit=2)

        self.assertEqual(
            [memory["memory_id"] for memory in result["memories"]],
            list(reversed(baseline_ids)),
        )
        self.assertTrue(all("rerank_score" not in memory for memory in result["memories"]))

    def test_recall_fixture_cases(self):
        fixture_path = Path(__file__).parent / "fixtures" / "memory" / "recall_cases.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

        for case in fixture["cases"]:
            with self.subTest(case=case["id"]):
                service = MemoryService(
                    metadata_store=self.store,
                    vector_store=self.vector_store,
                    embedding_provider=HashEmbeddingProvider(dimension=32),
                )
                memory_ids_to_forget = []

                for index, memory in enumerate(case["memories"]):
                    remembered = service.remember(
                        profile_id=case["id"],
                        content=memory["content"],
                        source_type="fixture",
                        source_id=f"{case['id']}-{index}",
                        memory_type=memory["memory_type"],
                        metadata=memory.get("metadata", {}),
                    )

                    if memory.get("forget"):
                        memory_ids_to_forget.extend(remembered["memory_ids"])

                if memory_ids_to_forget:
                    service.forget(
                        profile_id=case["id"],
                        memory_ids=memory_ids_to_forget,
                        reason="fixture forget",
                    )

                result = service.recall(
                    profile_id=case["id"],
                    query=case["query"],
                    context_hints=case.get("context_hints", {}),
                )
                recalled_text = "\n".join(memory["content"] for memory in result["memories"])

                for expected in case["expected_contains"]:
                    self.assertIn(expected, recalled_text)

                if not case["expected_contains"]:
                    self.assertEqual(result["memories"], [])


class QdrantLocalVectorStoreTests(unittest.TestCase):
    def test_qdrant_local_store_filters_by_profile_and_memory_type_then_deletes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = QdrantLocalVectorStore(
                path=Path(tmpdir) / "qdrant",
                collection_name="test_memory_items",
                vector_size=3,
            )
            try:
                store.upsert(
                    "memory-a",
                    [1.0, 0.0, 0.0],
                    {
                        "memory_id": "memory-a",
                        "profile_id": "user-1",
                        "state": "active",
                        "memory_type": "preference",
                    },
                )
                store.upsert(
                    "memory-b",
                    [1.0, 0.0, 0.0],
                    {
                        "memory_id": "memory-b",
                        "profile_id": "user-2",
                        "state": "active",
                        "memory_type": "preference",
                    },
                )

                result = store.search(
                    [1.0, 0.0, 0.0],
                    profile_id="user-1",
                    limit=5,
                    filters={"memory_type": "preference"},
                )

                self.assertEqual([item["memory_id"] for item in result], ["memory-a"])

                store.delete(["memory-a"])
                self.assertEqual(
                    store.search([1.0, 0.0, 0.0], profile_id="user-1", limit=5),
                    [],
                )
            finally:
                store.close()

    def test_qdrant_local_store_filters_active_type_and_time_windows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = QdrantLocalVectorStore(
                path=Path(tmpdir) / "qdrant",
                collection_name="test_memory_time_filters",
                vector_size=3,
            )
            try:
                for memory_id, payload in {
                    "memory-active-current": {
                        "profile_id": "user-1",
                        "state": "active",
                        "memory_type": "event",
                        "valid_from": "2026-06-01T00:00:00+00:00",
                        "valid_to": "2026-06-20T00:00:00+00:00",
                    },
                    "memory-active-future": {
                        "profile_id": "user-1",
                        "state": "active",
                        "memory_type": "event",
                        "valid_from": "2026-07-01T00:00:00+00:00",
                        "valid_to": None,
                    },
                    "memory-archived": {
                        "profile_id": "user-1",
                        "state": "archived",
                        "memory_type": "event",
                        "valid_from": "2026-06-01T00:00:00+00:00",
                        "valid_to": None,
                    },
                }.items():
                    store.upsert(
                        memory_id,
                        [1.0, 0.0, 0.0],
                        {"memory_id": memory_id, **payload},
                    )

                result = store.search(
                    [1.0, 0.0, 0.0],
                    profile_id="user-1",
                    limit=5,
                    filters={
                        "memory_type": "event",
                        "valid_at": "2026-06-11T00:00:00+00:00",
                    },
                )

                self.assertEqual([item["memory_id"] for item in result], ["memory-active-current"])
            finally:
                store.close()


class MemoryFactoryTests(unittest.TestCase):
    def test_embedding_provider_factory_defaults_to_hash_for_offline_mode(self):
        provider = create_embedding_provider({"provider": "hash", "dimension": 16})

        self.assertIsInstance(provider, HashEmbeddingProvider)
        self.assertEqual(provider.dimension, 16)

    def test_embedding_provider_factory_rejects_unknown_production_provider(self):
        with self.assertRaises(ValueError):
            create_embedding_provider({"provider": "unknown"})

    def test_default_memory_service_exposes_index_health(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = create_default_memory_service(
                base_path=Path(tmpdir) / "memory",
                embedding_config={"provider": "hash", "dimension": 16},
            )
            try:
                health = service.health()
            finally:
                service.close()

            self.assertEqual(health["embedding"]["provider"], "HashEmbeddingProvider")
            self.assertEqual(health["embedding"]["dimension"], 16)
            self.assertEqual(health["qdrant"]["collection"], "aurabot_memory_items")
