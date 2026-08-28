"""Persist, recall, and durably forget one active Profile's memories with one local worker.

``ProfileMemory`` owns exactly one ``memory.sqlite3`` plus optional embedding/Qdrant adapters. Stable
segments are durably upserted into one pending episode before returning; a replaceable five-minute
timer seals that episode into the same job table used for extraction retries and deferred vector
deletes. Durable episode/memory tombstones fence extraction and indexing across races and restarts. One
same-loop worker processes only the active Profile; recall fresh-reads SQLite and treats vectors as optional.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from backend.memory.extractor import MemoryExtractor
from backend.memory.types import EmbeddingProvider, MemoryCandidate, VectorStore
from backend.memory.validators import validate_memory_candidate


_SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_episode (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    generation INTEGER NOT NULL,
    segments_json TEXT NOT NULL,
    last_confirmed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_jobs (
    job_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('extract', 'vector_delete')),
    episode_id TEXT,
    payload_json TEXT NOT NULL,
    result_json TEXT,
    state TEXT NOT NULL CHECK (state IN ('pending', 'running', 'indexing', 'completed', 'failed')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at REAL NOT NULL,
    last_error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_items (
    memory_id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    importance REAL NOT NULL,
    confidence REAL NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS memory_items_fts USING fts5(memory_id UNINDEXED, content);
CREATE TABLE IF NOT EXISTS forgotten_episodes (
    episode_id TEXT PRIMARY KEY,
    forgotten_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS forgotten_memories (
    memory_id TEXT PRIMARY KEY,
    forgotten_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS memory_jobs_ready ON memory_jobs(state, next_attempt_at);
CREATE INDEX IF NOT EXISTS memory_items_episode ON memory_items(episode_id);
"""


class ProfileMemory:
    """Own durable segment sealing, extraction jobs, recall, and forget for one active Profile."""

    def __init__(
        self,
        *,
        profile_id: str,
        path: str | Path,
        extractor: MemoryExtractor,
        vector_store: VectorStore | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        vector_wait_seconds: float = 0.3,
        max_items: int = 6,
        max_chars: int = 2_400,
        idle_seconds: float = 300.0,
        retry_delays: Sequence[float] = (5.0, 30.0, 300.0),
        clock: Any = time.time,
    ) -> None:
        if not profile_id or not str(profile_id).strip():
            raise ValueError("profile_id must be non-empty text")
        if (vector_store is None) != (embedding_provider is None):
            raise ValueError("vector_store and embedding_provider must be configured together")
        if idle_seconds <= 0 or vector_wait_seconds < 0 or max_items < 1 or max_chars < 1:
            raise ValueError("Memory bounds must be positive")
        if not retry_delays or any(delay < 0 for delay in retry_delays):
            raise ValueError("retry_delays must be non-negative")
        self.profile_id = str(profile_id).strip()
        self.path = Path(path)
        self.extractor = extractor
        self.vector_store = vector_store
        self.embedding_provider = embedding_provider
        self.vector_wait_seconds = float(vector_wait_seconds)
        self.max_items = int(max_items)
        self.max_chars = int(max_chars)
        self.idle_seconds = float(idle_seconds)
        self.retry_delays = tuple(float(value) for value in retry_delays)
        self.clock = clock
        self._db: sqlite3.Connection | None = None
        self._state_lock = asyncio.Lock()
        self._vector_semaphore = asyncio.Semaphore(1)
        self._wake = asyncio.Event()
        self._seal_task: asyncio.Task[None] | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._vector_task: asyncio.Task[list[str]] | None = None
        self._processing = False
        self._closed = False

    async def startup(self) -> None:
        """Open this Profile only, recover interrupted work, and restore its durable idle deadline."""

        async with self._state_lock:
            if self._db is not None:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(self.path, check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            self._db.executescript(_SCHEMA)
            now = float(self.clock())
            self._db.execute(
                "UPDATE memory_jobs SET state='pending', next_attempt_at=?, updated_at=? "
                "WHERE state IN ('running','indexing')",
                (now, now),
            )
            self._db.commit()
            pending = self._db.execute(
                "SELECT generation, last_confirmed_at FROM pending_episode WHERE singleton=1"
            ).fetchone()
            if pending is not None:
                remaining = max(0.0, self.idle_seconds - (now - float(pending["last_confirmed_at"])))
                self._schedule_seal_locked(int(pending["generation"]), remaining)
            self._worker_task = asyncio.create_task(
                self._worker(), name=f"profile-memory-{self.profile_id}"
            )
            self._wake.set()

    async def record_confirmed_input(
        self,
        *,
        segment_id: str,
        content: str | None = None,
        text: str | None = None,
        input_id: str | None = None,
        revision: int | None = None,
    ) -> None:
        """Atomically replace one stable segment and reset the active Profile's idle timer."""

        identifier = str(segment_id).strip()
        value = str(content if content is not None else text or "").strip()
        if not identifier or not value:
            raise ValueError("confirmed Memory input requires segment_id and content")
        now = float(self.clock())
        async with self._state_lock:
            db = self._require_db()
            row = db.execute(
                "SELECT generation, segments_json FROM pending_episode WHERE singleton=1"
            ).fetchone()
            segments = json.loads(row["segments_json"]) if row is not None else []
            generation = int(row["generation"]) + 1 if row is not None else 1
            replacement = {
                "segment_id": identifier,
                "content": value,
                "input_id": input_id,
                "revision": revision,
            }
            for index, segment in enumerate(segments):
                if segment["segment_id"] == identifier:
                    segments[index] = replacement
                    break
            else:
                segments.append(replacement)
            db.execute(
                "INSERT INTO pending_episode(singleton,generation,segments_json,last_confirmed_at) "
                "VALUES(1,?,?,?) ON CONFLICT(singleton) DO UPDATE SET generation=excluded.generation, "
                "segments_json=excluded.segments_json,last_confirmed_at=excluded.last_confirmed_at",
                (generation, _json(segments), now),
            )
            db.commit()
            self._schedule_seal_locked(generation, self.idle_seconds)

    async def recall(
        self,
        *,
        query: str,
        revision: int,
        visible_source_segment_ids: Sequence[str] = (),
        visible_source_texts: Sequence[str] = (),
        **_identity: Any,
    ) -> tuple[dict[str, Any], ...]:
        """Fresh-read lexical truth and optionally merge a vector result arriving within 300 ms."""

        normalized = str(query).strip()
        if not normalized or self._db is None:
            return ()
        lexical_task = asyncio.create_task(asyncio.to_thread(self._lexical_ids, normalized))
        vector_task = (
            asyncio.create_task(self._vector_ids(normalized))
            if self.vector_store is not None else None
        )
        if vector_task is not None:
            self._vector_task = vector_task
            vector_task.add_done_callback(self._vector_done)
        lexical = await lexical_task
        vector: list[str] = []
        if vector_task is not None:
            try:
                vector = await asyncio.wait_for(
                    asyncio.shield(vector_task), timeout=self.vector_wait_seconds
                )
            except Exception:
                vector = []
        identifiers = list(dict.fromkeys((*lexical, *vector)))
        rows = await asyncio.to_thread(self._read_items, identifiers)
        hidden_ids = {str(value) for value in visible_source_segment_ids}
        hidden_text = {str(value).strip() for value in visible_source_texts if str(value).strip()}
        packed: list[dict[str, Any]] = []
        used = 0
        for row in rows:
            metadata = json.loads(row["metadata_json"])
            if hidden_ids.intersection(metadata.get("source_segment_ids", [])):
                continue
            if row["content"].strip() in hidden_text:
                continue
            cost = len(row["content"])
            if used + cost > self.max_chars:
                continue
            packed.append({
                "memory_id": row["memory_id"],
                "type": row["memory_type"],
                "content": row["content"],
                "metadata": metadata,
            })
            used += cost
            if len(packed) >= self.max_items:
                break
        return tuple(packed)

    async def browse(self, *, limit: int = 50) -> dict[str, Any]:
        """Return bounded active items and public job state for the current Profile UI."""

        bounded = max(1, min(int(limit), 100))
        db = self._require_db()
        items = db.execute(
            "SELECT * FROM memory_items ORDER BY updated_at DESC LIMIT ?", (bounded,)
        ).fetchall()
        jobs = db.execute(
            "SELECT job_id,kind,episode_id,state,attempt_count,last_error,created_at,updated_at "
            "FROM memory_jobs ORDER BY updated_at DESC LIMIT ?",
            (bounded,),
        ).fetchall()
        return {
            "profileId": self.profile_id,
            "items": [{
                "memoryId": row["memory_id"], "episodeId": row["episode_id"],
                "type": row["memory_type"], "content": row["content"],
                "createdAt": row["created_at"], "updatedAt": row["updated_at"],
            } for row in items],
            "jobs": [_public_job(row) for row in jobs],
        }

    async def forget(
        self,
        *,
        memory_ids: Sequence[str] = (),
        episode_ids: Sequence[str] = (),
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Persist revocation and delete current SQLite/vector truth for episodes or memory IDs."""

        del reason
        db = self._require_db()
        requested = list(dict.fromkeys(str(value) for value in memory_ids if str(value)))
        episodes = list(dict.fromkeys(str(value) for value in episode_ids if str(value)))
        now = float(self.clock())
        db.execute("BEGIN IMMEDIATE")
        try:
            db.executemany(
                "INSERT OR IGNORE INTO forgotten_episodes(episode_id,forgotten_at) VALUES(?,?)",
                ((episode_id, now) for episode_id in episodes),
            )
            identifiers = list(requested)
            if episodes:
                placeholders = ",".join("?" for _ in episodes)
                identifiers.extend(row[0] for row in db.execute(
                    f"SELECT memory_id FROM memory_items WHERE episode_id IN ({placeholders})", episodes
                ))
                db.execute(
                    f"UPDATE memory_jobs SET state='completed',last_error=NULL,updated_at=? "
                    f"WHERE kind='extract' AND episode_id IN ({placeholders}) AND state!='completed'",
                    (now, *episodes),
                )
            identifiers = list(dict.fromkeys(identifiers))
            db.executemany(
                "INSERT OR IGNORE INTO forgotten_memories(memory_id,forgotten_at) VALUES(?,?)",
                ((memory_id, now) for memory_id in identifiers),
            )
            if identifiers:
                placeholders = ",".join("?" for _ in identifiers)
                db.execute(f"DELETE FROM memory_items WHERE memory_id IN ({placeholders})", identifiers)
                db.execute(f"DELETE FROM memory_items_fts WHERE memory_id IN ({placeholders})", identifiers)
            db.commit()
        except BaseException:
            db.rollback()
            raise
        if identifiers and self.vector_store is not None:
            try:
                async with self._vector_semaphore:
                    await asyncio.to_thread(self.vector_store.delete, identifiers)
            except Exception as exc:
                self._insert_job("vector_delete", None, {"memory_ids": identifiers}, str(exc))
                self._wake.set()
        return {"forgottenCount": len(identifiers), "forgottenMemoryIds": identifiers}

    async def retry(self, job_id: str) -> dict[str, Any]:
        """Move one failed job back to pending without inventing a second retry mechanism."""

        db = self._require_db()
        now = float(self.clock())
        cursor = db.execute(
            "UPDATE memory_jobs SET state='pending',attempt_count=0,next_attempt_at=?,last_error=NULL,"
            "updated_at=? WHERE job_id=? AND state='failed'",
            (now, now, str(job_id)),
        )
        db.commit()
        if cursor.rowcount != 1:
            raise KeyError(job_id)
        self._wake.set()
        row = db.execute("SELECT * FROM memory_jobs WHERE job_id=?", (str(job_id),)).fetchone()
        return _public_job(row)

    async def local_status(self) -> dict[str, Any]:
        """Return local lifecycle facts without probing paid or network providers."""

        db = self._require_db()
        pending = db.execute("SELECT COUNT(*) FROM pending_episode").fetchone()[0]
        jobs = db.execute(
            "SELECT COUNT(*) FROM memory_jobs WHERE state IN ('pending','running','indexing')"
        ).fetchone()[0]
        return {"closed": self._closed, "pendingEpisode": bool(pending), "pendingJobs": jobs}

    async def wait_until_idle(self) -> None:
        """Test/support boundary: wait until the current seal and all retryable jobs settle."""

        while True:
            if self._worker_task is not None and self._worker_task.done():
                error = self._worker_task.exception()
                if error is not None:
                    raise RuntimeError("Profile Memory worker stopped") from error
            seal = self._seal_task
            if seal is not None and not seal.done():
                await asyncio.shield(seal)
                continue
            db = self._require_db()
            row = db.execute(
                "SELECT MIN(next_attempt_at) AS due, COUNT(*) AS count FROM memory_jobs "
                "WHERE state IN ('pending','running','indexing')"
            ).fetchone()
            if not row["count"] and not self._processing:
                return
            delay = max(0.001, min(0.05, float(row["due"] or self.clock()) - float(self.clock())))
            self._wake.set()
            await asyncio.sleep(delay)

    async def shutdown(self) -> None:
        """Stop active-only timer/worker and close adapters without deleting durable pending rows."""

        if self._closed:
            return
        self._closed = True
        tasks = [task for task in (self._seal_task, self._worker_task) if task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._vector_task is not None:
            await asyncio.gather(self._vector_task, return_exceptions=True)
        if self.vector_store is not None:
            close = getattr(self.vector_store, "close", None)
            if callable(close):
                await asyncio.to_thread(close)
        async with self._state_lock:
            if self._db is not None:
                self._db.close()
                self._db = None

    def _schedule_seal_locked(self, generation: int, delay: float) -> None:
        if self._seal_task is not None:
            self._seal_task.cancel()
        self._seal_task = asyncio.create_task(
            self._seal_after(generation, delay), name=f"memory-seal-{self.profile_id}"
        )

    async def _seal_after(self, generation: int, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
            async with self._state_lock:
                db = self._require_db()
                row = db.execute(
                    "SELECT generation,segments_json FROM pending_episode WHERE singleton=1"
                ).fetchone()
                if row is None or int(row["generation"]) != generation:
                    return
                segments = json.loads(row["segments_json"])
                episode_id = f"episode-{uuid.uuid4().hex}"
                now = float(self.clock())
                payload = {"segments": segments}
                db.execute("BEGIN IMMEDIATE")
                db.execute("DELETE FROM pending_episode WHERE singleton=1 AND generation=?", (generation,))
                db.execute(
                    "INSERT INTO memory_jobs VALUES(?,?,?,?,?,'pending',0,?,?,?,?)",
                    (f"job-{uuid.uuid4().hex}", "extract", episode_id, _json(payload), None,
                     now, None, now, now),
                )
                db.commit()
            self._wake.set()
        except asyncio.CancelledError:
            raise

    async def _worker(self) -> None:
        while True:
            job = self._claim_due_job()
            if job is None:
                self._wake.clear()
                due = self._next_due()
                try:
                    if due is None:
                        await self._wake.wait()
                    else:
                        await asyncio.wait_for(
                            self._wake.wait(), timeout=max(0.001, due - float(self.clock()))
                        )
                except TimeoutError:
                    pass
                continue
            self._processing = True
            try:
                if job["kind"] == "extract":
                    await self._process_extract(job)
                else:
                    await self._process_vector_delete(job)
            except asyncio.CancelledError:
                self._requeue_interrupted(job["job_id"])
                raise
            except Exception as exc:
                self._fail_or_retry(job, exc)
            finally:
                self._processing = False

    def _claim_due_job(self) -> sqlite3.Row | None:
        db = self._require_db()
        now = float(self.clock())
        row = db.execute(
            "SELECT * FROM memory_jobs WHERE state='pending' AND next_attempt_at<=? "
            "ORDER BY created_at LIMIT 1", (now,),
        ).fetchone()
        if row is None:
            return None
        db.execute(
            "UPDATE memory_jobs SET state='running',attempt_count=attempt_count+1,updated_at=? "
            "WHERE job_id=?", (now, row["job_id"]),
        )
        db.commit()
        return db.execute("SELECT * FROM memory_jobs WHERE job_id=?", (row["job_id"],)).fetchone()

    async def _process_extract(self, job: sqlite3.Row) -> None:
        if self._episode_forgotten(job["episode_id"]):
            self._complete_job(job["job_id"])
            return
        payload = json.loads(job["payload_json"])
        result = json.loads(job["result_json"]) if job["result_json"] else None
        if result is None:
            segments = payload["segments"]
            metadata = {
                "episode_id": job["episode_id"],
                "source_segment_ids": [item["segment_id"] for item in segments],
                "source_segments": segments,
            }
            content = "\n".join(item["content"] for item in segments)
            candidates, _entities = await asyncio.to_thread(
                self.extractor.extract, content=content, metadata=metadata,
                memory_type=None, importance=0.5,
            )
            if self._episode_forgotten(job["episode_id"]):
                self._complete_job(job["job_id"])
                return
            result = {"memories": [_candidate(candidate) for candidate in candidates]}
            db = self._require_db()
            now = float(self.clock())
            db.execute(
                "UPDATE memory_jobs SET result_json=?,state='indexing',updated_at=? WHERE job_id=?",
                (_json(result), now, job["job_id"]),
            )
            db.commit()
        await self._index_result(job["episode_id"], result)
        self._complete_job(job["job_id"])

    async def _index_result(self, episode_id: str, result: Mapping[str, Any]) -> None:
        if self._episode_forgotten(episode_id):
            return
        for index, raw in enumerate(result["memories"]):
            candidate = validate_memory_candidate(MemoryCandidate(**raw))
            memory_id = "memory-" + hashlib.sha256(
                f"{episode_id}\0{index}\0{candidate.content}".encode("utf-8")
            ).hexdigest()[:32]
            now = float(self.clock())
            metadata = dict(candidate.metadata)
            db = self._require_db()
            db.execute("BEGIN IMMEDIATE")
            try:
                if self._forgotten(db, episode_id, memory_id):
                    db.commit()
                    continue
                db.execute(
                    "INSERT INTO memory_items VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(memory_id) DO UPDATE "
                    "SET memory_type=excluded.memory_type,content=excluded.content,metadata_json=excluded.metadata_json,"
                    "importance=excluded.importance,confidence=excluded.confidence,updated_at=excluded.updated_at",
                    (memory_id, episode_id, candidate.memory_type, candidate.content, _json(metadata),
                     candidate.importance, candidate.confidence, now, now),
                )
                db.execute("DELETE FROM memory_items_fts WHERE memory_id=?", (memory_id,))
                db.execute(
                    "INSERT INTO memory_items_fts(memory_id,content) VALUES(?,?)", (memory_id, candidate.content)
                )
                db.commit()
            except BaseException:
                db.rollback()
                raise
            if self.vector_store is not None and self.embedding_provider is not None:
                vector = await asyncio.to_thread(self.embedding_provider.embed, candidate.content)
                async with self._vector_semaphore:
                    if self._forgotten(self._require_db(), episode_id, memory_id):
                        continue
                    await asyncio.to_thread(
                        self.vector_store.upsert, memory_id, vector,
                        {"memory_id": memory_id, "profile_id": self.profile_id, "episode_id": episode_id,
                         "content": candidate.content, "memory_type": candidate.memory_type},
                    )

    async def _process_vector_delete(self, job: sqlite3.Row) -> None:
        if self.vector_store is not None:
            async with self._vector_semaphore:
                await asyncio.to_thread(
                    self.vector_store.delete, json.loads(job["payload_json"])["memory_ids"]
                )
        self._complete_job(job["job_id"])

    def _complete_job(self, job_id: str) -> None:
        db = self._require_db()
        now = float(self.clock())
        db.execute(
            "UPDATE memory_jobs SET state='completed',last_error=NULL,updated_at=? WHERE job_id=?",
            (now, job_id),
        )
        db.commit()

    def _fail_or_retry(self, job: sqlite3.Row, error: Exception) -> None:
        db = self._require_db()
        attempt = int(job["attempt_count"])
        now = float(self.clock())
        if attempt <= len(self.retry_delays):
            state = "pending"
            next_at = now + self.retry_delays[attempt - 1]
        else:
            state = "failed"
            next_at = now
        db.execute(
            "UPDATE memory_jobs SET state=?,next_attempt_at=?,last_error=?,updated_at=? "
            "WHERE job_id=? AND state IN ('running','indexing')",
            (state, next_at, str(error)[:1000], now, job["job_id"]),
        )
        db.commit()

    def _requeue_interrupted(self, job_id: str) -> None:
        if self._db is None:
            return
        now = float(self.clock())
        self._db.execute(
            "UPDATE memory_jobs SET state='pending',next_attempt_at=?,updated_at=? "
            "WHERE job_id=? AND state IN ('running','indexing')",
            (now, now, job_id),
        )
        self._db.commit()

    def _insert_job(self, kind: str, episode_id: str | None, payload: Mapping[str, Any], error: str) -> None:
        db = self._require_db()
        now = float(self.clock())
        db.execute(
            "INSERT INTO memory_jobs VALUES(?,?,?,?,?,'pending',0,?,?,?,?)",
            (f"job-{uuid.uuid4().hex}", kind, episode_id, _json(payload), None,
             now, error[:1000], now, now),
        )
        db.commit()

    def _lexical_ids(self, query: str) -> list[str]:
        with sqlite3.connect(self.path) as db:
            try:
                rows = db.execute(
                    "SELECT memory_id FROM memory_items_fts WHERE memory_items_fts MATCH ? LIMIT ?",
                    ('"' + query.replace('"', '""') + '"', self.max_items * 3),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            if not rows:
                rows = db.execute(
                    "SELECT memory_id FROM memory_items WHERE content LIKE ? ORDER BY updated_at DESC LIMIT ?",
                    (f"%{query}%", self.max_items * 3),
                ).fetchall()
            return [row[0] for row in rows]

    async def _vector_ids(self, query: str) -> list[str]:
        assert self.vector_store is not None and self.embedding_provider is not None
        async with self._vector_semaphore:
            vector = await asyncio.to_thread(self.embedding_provider.embed, query)
            rows = await asyncio.to_thread(
                self.vector_store.search, vector, profile_id=self.profile_id,
                limit=self.max_items * 3, filters=None,
            )
        return [str(row.get("memory_id", row.get("id", ""))) for row in rows if row.get("memory_id", row.get("id"))]

    def _vector_done(self, task: asyncio.Task[list[str]]) -> None:
        if self._vector_task is task:
            self._vector_task = None
        _consume_task_result(task)

    def _read_items(self, identifiers: Sequence[str]) -> list[sqlite3.Row]:
        if not identifiers:
            return []
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in identifiers)
        try:
            rows = db.execute(
                f"SELECT * FROM memory_items WHERE memory_id IN ({placeholders})", tuple(identifiers)
            ).fetchall()
        finally:
            db.close()
        by_id = {row["memory_id"]: row for row in rows}
        return [by_id[value] for value in identifiers if value in by_id]

    def _next_due(self) -> float | None:
        row = self._require_db().execute(
            "SELECT MIN(next_attempt_at) FROM memory_jobs WHERE state='pending'"
        ).fetchone()
        return float(row[0]) if row[0] is not None else None

    def _require_db(self) -> sqlite3.Connection:
        if self._db is None:
            raise RuntimeError("Profile Memory is not active")
        return self._db

    def _episode_forgotten(self, episode_id: str) -> bool:
        return self._forgotten(self._require_db(), episode_id)

    @staticmethod
    def _forgotten(db: sqlite3.Connection, episode_id: str, memory_id: str | None = None) -> bool:
        if db.execute(
            "SELECT 1 FROM forgotten_episodes WHERE episode_id=?", (episode_id,)
        ).fetchone() is not None:
            return True
        return memory_id is not None and db.execute(
            "SELECT 1 FROM forgotten_memories WHERE memory_id=?", (memory_id,)
        ).fetchone() is not None


def _candidate(candidate: MemoryCandidate) -> dict[str, Any]:
    validated = validate_memory_candidate(candidate)
    return {
        "content": validated.content, "memory_type": validated.memory_type,
        "importance": validated.importance, "confidence": validated.confidence,
        "valid_from": validated.valid_from, "valid_to": validated.valid_to,
        "entities": list(validated.entities), "metadata": dict(validated.metadata),
    }


def _public_job(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "jobId": row["job_id"], "kind": row["kind"], "episodeId": row["episode_id"],
        "state": row["state"], "attemptCount": row["attempt_count"],
        "lastError": row["last_error"], "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _consume_task_result(task: asyncio.Task[Any]) -> None:
    if not task.cancelled():
        try:
            task.exception()
        except BaseException:
            pass


__all__ = ["ProfileMemory"]
