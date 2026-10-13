"""
验证 Memory 整段对话提炼、检索流程与持久化适配器的可观察行为。

测试使用临时 SQLite 和内存向量替身覆盖对话归档、提取与召回排序流程；
通过当前 Memory module 验证召回降级与预算，不保留旧短期记忆或兼容 façade 测试。
"""

import asyncio
import json
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path


from backend.memory.embedding import HashEmbeddingProvider, create_embedding_provider
from backend.memory.service import MemoryService
from backend.memory.stores.qdrant import QdrantLocalVectorStore
from backend.memory.stores.sqlite import SQLiteMemoryStore
from backend.memory.extractor import StructuredMemoryExtractor
from backend.memory.extraction.schemas import (
    parse_conversation_extraction_payload,
    parse_structured_extraction_payload,
)
from backend.memory.extraction.providers import OpenAICompatibleMemoryExtractionModel
from backend.tests.memory_fixtures import memory_snapshot
from backend.memory.retrieval.fusion import fuse_candidates
from backend.memory.retrieval.context import pack_context
from backend.memory.retrieval.query import QueryAnalyzer
from backend.memory.retrieval.ranking import hydrate_and_rank_memories
from backend.memory.retrieval.rerank import RuleBasedMemoryReranker
from backend.memory.retrieval.trace import build_recall_trace
from backend.memory.validators import validate_memory_candidate
from backend.memory.types import MemoryCandidate
from backend.main_agent.context import FoldedContext
from backend.main_agent.snapshot import SnapshotProjector


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


class FailingSearchVectorStore(InMemoryVectorStore):
    def search(self, vector, *, profile_id, limit, filters=None):
        raise RuntimeError("vector index unavailable")


class FailSecondUpsertVectorStore(InMemoryVectorStore):
    def __init__(self):
        super().__init__()
        self.attempts: list[str] = []
        self.fail_second = True

    def upsert(self, memory_id, vector, payload):
        self.attempts.append(memory_id)
        if self.fail_second and len(self.attempts) == 2:
            raise RuntimeError("second vector upsert failed")
        super().upsert(memory_id, vector, payload)


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


class LegacyEmptyExtractor:
    def extract(self, **_kwargs):
        return [], []


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
            "summary": "用户喜欢红茶，并已得到确认。",
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
        self.assertEqual(first["summary"], "用户喜欢红茶，并已得到确认。")
        self.assertEqual(self.model.contents, ["user: 我喜欢红茶\nassistant: 我记住了。"])
        snapshot = memory_snapshot(self.store, "profile-a")
        self.assertEqual(len(snapshot["episodes"]), 1)
        self.assertEqual(snapshot["episodes"][0]["source_type"], "main_agent_conversation")
        self.assertEqual(snapshot["episodes"][0]["source_id"], "context:1:2")
        self.assertEqual([item["content"] for item in snapshot["memories"]], ["用户喜欢红茶"])

    async def test_structured_conversation_extraction_returns_summary_from_same_payload(self):
        self.model.release.set()
        extractor = StructuredMemoryExtractor(self.model)

        result = await extractor.extract_conversation_async(
            content="user: 我喜欢红茶",
            metadata={"turn_count": 1},
        )

        self.assertEqual(result.summary, "用户喜欢红茶，并已得到确认。")
        self.assertEqual([item.content for item in result.memories], ["用户喜欢红茶"])
        self.assertEqual([item.name for item in result.entities], ["红茶"])
        self.assertEqual(self.model.contents, ["user: 我喜欢红茶"])

    async def test_zero_memory_conversation_persists_model_summary_without_reextracting(self):
        self.model.payload = {
            "memories": [],
            "entities": [],
            "summary": "用户正在比较方案 A 和方案 B，并倾向继续使用方案 B。",
        }
        self.model.release.set()
        turns = ({"role": "user", "content": "那就按照第二个继续"},)

        first = await self.service.remember_conversation(
            profile_id="profile-a", turns=turns, source_id="context:1:1",
        )
        repeated = await self.service.remember_conversation(
            profile_id="profile-a", turns=turns, source_id="context:1:1",
        )

        self.assertEqual(first, repeated)
        self.assertEqual(
            first["summary"],
            "用户正在比较方案 A 和方案 B，并倾向继续使用方案 B。",
        )
        self.assertEqual(first["memory_ids"], [])
        self.assertEqual(len(self.model.contents), 1)
        snapshot = memory_snapshot(self.store, "profile-a")
        self.assertEqual(len(snapshot["episodes"]), 1)
        self.assertEqual(snapshot["episodes"][0]["summary"], first["summary"])
        self.assertEqual(snapshot["memories"], [])

    async def test_legacy_extractor_gets_deterministic_nonempty_bounded_summary(self):
        service = MemoryService(
            metadata_store=self.store,
            vector_store=self.vector_store,
            embedding_provider=HashEmbeddingProvider(dimension=32),
            extractor=LegacyEmptyExtractor(),
        )
        turns = (
            {"role": "user", "content": "较早内容" + "x" * 1_100},
            {"role": "assistant", "content": "最新决定是继续方案 B"},
        )

        first = await service.remember_conversation(
            profile_id="profile-a", turns=turns, source_id="context:1:2",
        )
        repeated = await service.remember_conversation(
            profile_id="profile-a", turns=turns, source_id="context:1:2",
        )

        self.assertEqual(first, repeated)
        self.assertTrue(first["summary"])
        self.assertLessEqual(len(first["summary"]), 1_000)
        self.assertIn("最新决定是继续方案 B", first["summary"])
        self.assertEqual(first["memory_ids"], [])

    async def test_conversation_sqlite_failure_rolls_back_every_fact_before_vector_indexing(self):
        self.model.payload = {
            "memories": [
                {
                    "content": "用户喜欢红茶",
                    "memory_type": "preference",
                    "importance": 0.8,
                    "confidence": 0.9,
                    "entities": ["红茶"],
                },
                {
                    "content": "用户常在下午喝红茶",
                    "memory_type": "semantic",
                    "importance": 0.7,
                    "confidence": 0.8,
                    "entities": ["红茶"],
                },
            ],
            "entities": [{"name": "红茶"}],
        }
        self.model.release.set()
        connection = sqlite3.connect(self.store.path)
        connection.executescript(
            """
            create trigger fail_second_conversation_memory
            before insert on memory_items
            when (select count(*) from memory_items) >= 1
            begin
              select raise(abort, 'second memory insert failed');
            end;
            """
        )
        connection.close()
        turns = ({"role": "user", "content": "我下午喜欢喝红茶"},)

        with self.assertRaises(sqlite3.IntegrityError):
            await self.service.remember_conversation(
                profile_id="profile-a", turns=turns, source_id="context:1:1",
            )

        failed = memory_snapshot(self.store, "profile-a")
        self.assertEqual(failed["episodes"], [])
        self.assertEqual(failed["memories"], [])
        self.assertEqual(failed["entities"], [])
        self.assertEqual(failed["relations"], [])
        self.assertEqual(self.vector_store.points, {})

        connection = sqlite3.connect(self.store.path)
        connection.execute("drop trigger fail_second_conversation_memory")
        connection.close()
        result = await self.service.remember_conversation(
            profile_id="profile-a", turns=turns, source_id="context:1:1",
        )

        completed = memory_snapshot(self.store, "profile-a")
        self.assertEqual(len(completed["episodes"]), 1)
        self.assertEqual(len(completed["memories"]), 2)
        self.assertEqual(len(result["memory_ids"]), 2)

    async def test_conversation_retry_repairs_all_vectors_without_reextracting(self):
        self.model.payload = {
            "memories": [
                {
                    "content": "用户喜欢红茶",
                    "memory_type": "preference",
                    "importance": 0.8,
                    "confidence": 0.9,
                    "entities": ["红茶"],
                },
                {
                    "content": "用户常在下午喝红茶",
                    "memory_type": "semantic",
                    "importance": 0.7,
                    "confidence": 0.8,
                    "entities": ["红茶"],
                },
            ],
            "entities": [{"name": "红茶"}],
        }
        self.model.release.set()
        vectors = FailSecondUpsertVectorStore()
        service = MemoryService(
            metadata_store=self.store,
            vector_store=vectors,
            embedding_provider=HashEmbeddingProvider(dimension=32),
            extractor=StructuredMemoryExtractor(self.model),
        )
        turns = ({"role": "user", "content": "我下午喜欢喝红茶"},)

        with self.assertRaisesRegex(RuntimeError, "second vector upsert failed"):
            await service.remember_conversation(
                profile_id="profile-a", turns=turns, source_id="context:1:1",
            )

        sqlite_truth = memory_snapshot(self.store, "profile-a")
        self.assertEqual(len(sqlite_truth["episodes"]), 1)
        self.assertEqual(len(sqlite_truth["memories"]), 2)
        self.assertEqual(len(vectors.points), 1)
        vectors.fail_second = False
        result = await service.remember_conversation(
            profile_id="profile-a", turns=turns, source_id="context:1:1",
        )

        self.assertEqual(len(self.model.contents), 1)
        self.assertEqual(len(result["memory_ids"]), 2)
        self.assertEqual(set(vectors.points), set(result["memory_ids"]))
        self.assertEqual(len(vectors.attempts), 4)
        completed = memory_snapshot(self.store, "profile-a")
        self.assertEqual(len(completed["episodes"]), 1)
        self.assertEqual(len(completed["memories"]), 2)

    async def test_existing_incomplete_episode_is_rebuilt_instead_of_reported_success(self):
        incomplete_episode = "incomplete-episode"
        with sqlite3.connect(self.store.path) as connection:
            connection.execute("""insert into memory_episodes
                (id, profile_id, source_type, source_id, content, metadata_json, created_at)
                values (?, 'profile-a', 'main_agent_conversation', 'context:1:1',
                        'user: 我喜欢红茶', '{"turn_count":1}', '2026-01-01')""", (incomplete_episode,))
        self.model.release.set()

        result = await self.service.remember_conversation(
            profile_id="profile-a",
            turns=({"role": "user", "content": "我喜欢红茶"},),
            source_id="context:1:1",
        )

        self.assertEqual(result["episode_id"], incomplete_episode)
        self.assertEqual(result["summary"], "用户喜欢红茶，并已得到确认。")
        self.assertEqual(len(result["memory_ids"]), 1)
        self.assertEqual(len(self.model.contents), 1)
        snapshot = memory_snapshot(self.store, "profile-a")
        self.assertEqual(len(snapshot["episodes"]), 1)
        self.assertEqual(len(snapshot["memories"]), 1)

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

        snapshot = memory_snapshot(self.store, "profile-a")
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

    async def test_recall_context_async_returns_only_bounded_public_memory_fields(self):
        self.model.release.set()
        self.model.payload["memories"][0]["metadata"] = {"private_blob": "secret-" + "x" * 5000}
        remembered = await self.service.remember_conversation(
            profile_id="profile-a", turns=({"role": "user", "content": "用户喜欢红茶"},),
            source_id="red-tea",
        )

        result = await self.service.recall_context_async(
            profile_id="profile-a",
            query="红茶",
            limit=6,
            max_chars=80,
        )

        self.assertEqual(set(result), {"text", "memory_ids", "char_count", "max_chars"})
        self.assertIn("用户喜欢红茶", result["text"])
        self.assertIn(remembered["memory_id"], result["memory_ids"])
        self.assertLessEqual(result["char_count"], 80)
        self.assertNotIn("private_blob", result["text"])
        self.assertNotIn("secret-", result["text"])

    async def test_recall_uses_the_context_already_packed_by_retrieval(self):
        # A successful recall must return the same bounded record without a second pack pass.
        packed = pack_context([], max_chars=80)
        with patch.object(self.service.retriever, "recall", return_value={
            "memories": [], "trace": {}, "packed_context": packed,
        }):
            result = await self.service.recall_context_async(
                profile_id="profile-a", query="query", max_chars=80,
            )
        self.assertIs(result, packed)

    async def test_consecutive_recall_timeouts_own_only_one_sync_operation_until_shutdown(self):
        started = threading.Event()
        release = threading.Event()
        calls = 0

        def blocking_recall(**_kwargs):
            nonlocal calls
            calls += 1
            started.set()
            release.wait(2)
            return {"memories": []}

        self.service.recall = blocking_recall
        first = asyncio.create_task(self.service.recall_context_async(
            profile_id="profile-a", query="first", max_chars=200,
        ))
        self.assertTrue(await asyncio.to_thread(started.wait, 0.5))
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(first, 0.01)

        second = await asyncio.wait_for(self.service.recall_context_async(
            profile_id="profile-a", query="second", max_chars=200,
        ), 0.05)
        third = await asyncio.wait_for(self.service.recall_context_async(
            profile_id="profile-a", query="third", max_chars=200,
        ), 0.05)
        self.assertEqual(second["text"], "")
        self.assertEqual(third["text"], "")
        self.assertEqual(calls, 1)

        shutdown = asyncio.create_task(self.service.shutdown())
        await asyncio.sleep(0)
        self.assertFalse(shutdown.done())
        release.set()
        await asyncio.wait_for(shutdown, 1)
        self.assertEqual(calls, 1)


class MemoryInvodeTests(unittest.IsolatedAsyncioTestCase):
    def test_context_packer_rejects_budget_smaller_than_fixed_json_envelope(self):
        with self.assertRaisesRegex(ValueError, "fixed JSON envelope"):
            pack_context([], max_chars=20)

    def test_context_packer_skips_unfit_id_and_uses_later_memory_that_fits(self):
        packed = pack_context(
            [
                {"memory_id": "too-long-" + "x" * 100, "content": "first"},
                {"memory_id": "m2", "content": "ok"},
            ],
            max_chars=40,
        )
        serialized = json.dumps(
            {"text": packed["text"], "memory_ids": packed["memory_ids"]},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )

        self.assertEqual(packed["text"], "ok")
        self.assertEqual(packed["memory_ids"], ["m2"])
        self.assertEqual(packed["char_count"], len(serialized))
        self.assertLessEqual(packed["char_count"], 40)

    def test_empty_usable_context_reports_actual_json_envelope_size(self):
        packed = pack_context([], max_chars=30)
        self.assertEqual(packed["char_count"], 27)

    def test_conversation_extraction_rejects_oversized_model_batches_before_persistence(self):
        cases = (
            ({
                "memories": [
                    {"content": f"memory-{index}", "memory_type": "semantic"}
                    for index in range(17)
                ],
                "entities": [],
                "summary": "有限摘要",
            }, "at most 16 memories"),
            ({
                "memories": [],
                "entities": [{"name": f"entity-{index}"} for index in range(33)],
                "summary": "有限摘要",
            }, "at most 32 entities"),
            ({
                "memories": [{"content": "x" * 2_001, "memory_type": "semantic"}],
                "entities": [],
                "summary": "有限摘要",
            }, "content exceeds 2000"),
            ({
                "memories": [{
                    "content": "有限内容", "memory_type": "semantic",
                    "metadata": {"blob": "x" * 16_385},
                }],
                "entities": [],
                "summary": "有限摘要",
            }, "metadata exceeds 16384"),
        )

        for oversized, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    parse_conversation_extraction_payload(
                        oversized,
                        base_metadata={"turn_count": 1},
                        default_importance=0.5,
                    )

    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        sqlite_path = Path(self.tmpdir.name) / "memory.sqlite3"
        self.store = SQLiteMemoryStore(sqlite_path)
        self.vector_store = InMemoryVectorStore()
        self.model = FakeExtractionModel({})
        self.service = MemoryService(
            metadata_store=self.store,
            vector_store=self.vector_store,
            embedding_provider=HashEmbeddingProvider(dimension=32),
            extractor=StructuredMemoryExtractor(self.model),
        )

    async def _archive(self, *, profile_id, content, source_id, memory_type="semantic", metadata=None, service=None):
        metadata = metadata or {}
        entities = metadata.get("entities", [])
        self.model.payload = {
            "memories": [{"content": content, "memory_type": memory_type, "metadata": metadata,
                          "entities": entities, "valid_from": metadata.get("valid_from"),
                          "valid_to": metadata.get("valid_to")}],
            "entities": [{"name": name} for name in entities],
        }
        return await (service or self.service).remember_conversation(
            profile_id=profile_id, source_id=source_id, turns=({"role": "user", "content": content},),
        )

    async def asyncTearDown(self):
        self.store.close()
        self.tmpdir.cleanup()

    async def test_recall_uses_vector_candidates_and_hydrates_from_sqlite(self):
        first = await self._archive(
            profile_id='user-1',
            content='用户喜欢热拿铁。',
            source_id='message-1',
            memory_type='preference',
        )
        second = await self._archive(
            profile_id='user-1',
            content='用户明天要提交项目周报。',
            source_id='message-2',
            memory_type='event',
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

    async def test_recall_falls_back_to_fts_when_vector_search_fails(self):
        vector_store = FailingSearchVectorStore()
        service = MemoryService(
            metadata_store=self.store,
            vector_store=vector_store,
            embedding_provider=HashEmbeddingProvider(dimension=32),
            extractor=StructuredMemoryExtractor(self.model),
        )
        remembered = await self._archive(
            profile_id='user-1',
            content='用户喜欢红茶',
            source_id='red-tea',
            memory_type='preference',
            service=service,
        )

        result = service.recall(profile_id="user-1", query="红茶", limit=5)

        self.assertEqual([item["memory_id"] for item in result["memories"]], [remembered["memory_id"]])
        self.assertEqual(result["memories"][0]["content"], "用户喜欢红茶")
        self.assertEqual(result["trace"]["failed_branches"], ["vector"])

    async def test_recall_uses_entity_seed_when_vector_is_empty(self):
        remembered = await self._archive(
            profile_id='user-1',
            content='用户喜欢红茶',
            source_id='red-tea',
            memory_type='preference',
            metadata={'entities': ['红茶']},
        )
        self.vector_store.points.clear()

        result = self.service.recall(
            profile_id="user-1",
            query="饮品偏好",
            limit=5,
            context_hints={"entities": ["红茶"]},
        )

        self.assertEqual([item["memory_id"] for item in result["memories"]], [remembered["memory_id"]])
        self.assertEqual(result["trace"]["entity_candidates"], [remembered["memory_id"]])

    def test_recall_returns_legal_empty_result_when_all_seed_branches_fail(self):
        service = MemoryService(
            metadata_store=self.store,
            vector_store=FailingSearchVectorStore(),
            embedding_provider=HashEmbeddingProvider(dimension=32),
            extractor=StructuredMemoryExtractor(self.model),
        )

        def fail_branch(**_kwargs):
            raise sqlite3.OperationalError("seed unavailable")

        self.store.search_fts = fail_branch
        self.store.search_entities = fail_branch

        result = service.recall(profile_id="user-1", query="anything", limit=5)

        self.assertEqual(result["memories"], [])
        self.assertEqual(result["packed_context"]["text"], "")
        self.assertEqual(result["trace"]["failed_branches"], ["vector", "fts", "entity"])

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

                memory = migrated.get_active_memories(["memory_legacy"], "user-1")[0]
                self.assertEqual(memory["content"], "用户喜欢热拿铁")
                self.assertEqual(memory["confidence"], 0.7)
                self.assertEqual(memory["state"], "active")
            finally:
                migrated.close()

    async def test_recall_combines_vector_fts_entity_relation_and_trace_sources(self):
        await self._archive(
            profile_id='user-1',
            content='用户喜欢热拿铁。',
            source_id='message-1',
            memory_type='preference',
            metadata={'entities': ['热拿铁']},
        )
        await self._archive(
            profile_id='user-1',
            content='用户明天要去咖啡店和阿杰讨论项目。',
            source_id='message-2',
            memory_type='event',
            metadata={'entities': ['咖啡店', '阿杰', '项目']},
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

    async def test_structured_extractor_persists_model_candidates_entities_and_trace_metadata(self):
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

        result = await service.remember_conversation(
            profile_id='user-1',
            turns=({'role': 'user', 'content': '我最近在找低酸咖啡豆。'},),
            source_id='message-structured',
        )

        memory = self.store.get_active_memories([result["memory_id"]], "user-1")[0]
        debug = memory_snapshot(self.store, "user-1")

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

    async def test_structured_extractor_rejects_invalid_model_output_before_persistence(self):
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
            await service.remember_conversation(
                profile_id='user-1',
                turns=({'role': 'user', 'content': '坏输出不要入库'},),
                source_id='message-invalid',
            )

        debug = memory_snapshot(self.store, "user-1")
        self.assertEqual(debug["memories"], [])
        self.assertEqual(debug["episodes"], [])
        self.assertEqual(debug["audit_logs"], [])

    async def test_ranking_and_trace_preserve_candidate_sources(self):
        remembered = await self._archive(
            profile_id='user-1',
            content='用户喜欢热拿铁。',
            source_id='message-1',
            memory_type='preference',
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
            max_chars=50,
        )

        self.assertEqual(analysis["time"]["valid_at"], "2026-06-11T00:00:00+00:00")
        self.assertEqual(set(analysis["entities"]), {"咖啡店", "阿杰", "项目"})
        self.assertEqual(packed["memory_ids"], ["m1"])
        self.assertLessEqual(packed["char_count"], 50)

    def test_packed_context_budget_covers_final_json_record_and_survives_snapshot(self):
        packed = pack_context(
            [
                {"memory_id": "memory-one-" + "1" * 30, "content": "最高排名记忆。" + "甲" * 1_180},
                {"memory_id": "memory-two-" + "2" * 30, "content": "第二条记忆。" + "乙" * 1_180},
                {"memory_id": "memory-three-" + "3" * 30, "content": "第三条记忆。"},
            ],
            max_chars=2_400,
        )
        public_record = {"text": packed["text"], "memory_ids": packed["memory_ids"]}
        serialized = json.dumps(
            public_record, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )

        self.assertTrue(public_record["text"])
        self.assertTrue(public_record["memory_ids"])
        self.assertEqual(packed["char_count"], len(serialized))
        self.assertLessEqual(len(serialized), 2_400)
        snapshot = SnapshotProjector().project(
            FoldedContext((), (), None, ()),
            soul="Aura",
            memory_facts=(public_record,),
        )
        self.assertEqual(snapshot.memory_facts, (public_record,))
        self.assertLessEqual(len(snapshot.prompt), 12_000)

    async def test_recall_returns_query_analysis_and_packed_context(self):
        await self._archive(
            profile_id='user-1',
            content='用户今天要和阿杰讨论项目。',
            source_id='message-1',
            memory_type='event',
            metadata={'entities': ['阿杰', '项目'], 'valid_from': '2026-06-11T00:00:00+00:00', 'valid_to': '2026-06-12T00:00:00+00:00'},
        )

        result = self.service.recall(
            profile_id="user-1",
            query="今天阿杰项目有什么？",
            limit=5,
            context_hints={
                "now_iso": "2026-06-11T00:00:00+00:00",
                "context_budget_chars": 120,
            },
        )

        self.assertEqual(result["analysis"]["time"]["valid_at"], "2026-06-11T00:00:00+00:00")
        self.assertIn("阿杰", result["analysis"]["entities"])
        self.assertIn(result["memories"][0]["memory_id"], result["packed_context"]["memory_ids"])
        self.assertLessEqual(result["packed_context"]["char_count"], 120)

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

    async def test_recall_trace_exposes_rerank_score_changes(self):
        await self._archive(profile_id='user-1', content='用户喜欢热拿铁。', source_id='message-1', memory_type='preference')
        await self._archive(
            profile_id='user-1',
            content='用户今天要和阿杰讨论项目。',
            source_id='message-2',
            memory_type='event',
            metadata={'entities': ['阿杰', '项目'], 'valid_from': '2026-06-11T00:00:00+00:00'},
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

    async def test_retriever_accepts_custom_reranker_without_changing_seed_components(self):
        await self._archive(profile_id='user-1', content='用户喜欢热拿铁。', source_id='message-1', memory_type='preference')
        await self._archive(profile_id='user-1', content='用户喜欢冷萃咖啡。', source_id='message-2', memory_type='preference')
        baseline = self.service.recall(profile_id="user-1", query="咖啡", limit=2)
        baseline_ids = [memory["memory_id"] for memory in baseline["memories"]]
        self.service.retriever.reranker = ReverseMemoryReranker()

        result = self.service.recall(profile_id="user-1", query="咖啡", limit=2)

        self.assertEqual(
            [memory["memory_id"] for memory in result["memories"]],
            list(reversed(baseline_ids)),
        )
        self.assertTrue(all("rerank_score" not in memory for memory in result["memories"]))

    async def test_recall_fixture_cases(self):
        fixture_path = Path(__file__).parent / "fixtures" / "memory" / "recall_cases.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

        for case in fixture["cases"]:
            with self.subTest(case=case["id"]):
                service = MemoryService(
                    metadata_store=self.store,
                    vector_store=self.vector_store,
                    embedding_provider=HashEmbeddingProvider(dimension=32),
                    extractor=StructuredMemoryExtractor(self.model),
                )

                for index, memory in enumerate(case["memories"]):
                    await self._archive(
                        profile_id=case['id'],
                        content=memory['content'],
                        source_id=f"{case['id']}-{index}",
                        memory_type=memory['memory_type'],
                        metadata=memory.get('metadata', {}),
                        service=service,
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
    def test_qdrant_local_store_filters_by_profile_and_memory_type(self):
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
