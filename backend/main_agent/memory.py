"""Bridge durable Memory into Main Agent snapshots without exposing a model tool.

``MemoryBridge`` owns the latency-sensitive recall boundary: SQLite lexical candidates are always
available first, the optional embedding/Qdrant branch is shared by revision and awaited for at most
the configured hard cap, and every result is re-hydrated through SQLite active truth.  A separate,
bounded owned-task registry prevents abandoned vector work from outliving cache eviction or shutdown.
The bridge filters memories that overlap visible source segments and returns bounded plain facts for
``SnapshotProjector``. Confirmed segments and their idle deadline are persisted before acknowledgement,
then startup resumes or atomically seals the pending episode/job in the same ``memory.sqlite3``. The
process-global event-driven worker shares that lifecycle; owner/CAS heartbeats fence slow extraction,
embedding, and Qdrant delivery across the durable lease boundary.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
import time
import uuid
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from backend.memory.extractor import MemoryExtractor, RuleBasedMemoryExtractor
from backend.memory.stores.sqlite import MemoryLeaseLostError, SQLiteMemoryStore
from backend.memory.types import EmbeddingProvider, VectorStore
from backend.memory.validators import validate_memory_candidate


logger = logging.getLogger(__name__)

@dataclass(frozen=True, slots=True)
class _WorkerProfile:
    store: SQLiteMemoryStore
    extractor: MemoryExtractor
    vector_store: VectorStore
    embedding_provider: EmbeddingProvider


class _LeaseLost(RuntimeError):
    """Internal control result: durable work is no longer owned by this worker."""


class MemoryBridge:
    """Provide bounded automatic recall for one profile's existing Memory stores."""

    def __init__(
        self,
        *,
        profile_id: str,
        store: SQLiteMemoryStore,
        vector_store: VectorStore | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        vector_wait_seconds: float = 0.3,
        cache_size: int = 8,
        vector_task_limit: int | None = None,
        max_items: int = 6,
        max_chars: int = 2_400,
        idle_seconds: float = 300.0,
        idle_sleep: Any = asyncio.sleep,
        worker: "MemoryWorker | None" = None,
        extractor: MemoryExtractor | None = None,
        metrics: Any | None = None,
        clock: Any | None = None,
    ) -> None:
        if not isinstance(profile_id, str) or not profile_id:
            raise ValueError("profile_id must be non-empty text")
        if vector_wait_seconds < 0:
            raise ValueError("vector_wait_seconds must be non-negative")
        if cache_size < 1 or max_items < 1 or max_chars < 1:
            raise ValueError("Memory recall bounds must be positive")
        if vector_task_limit is not None and vector_task_limit < 1:
            raise ValueError("vector task limit must be positive")
        if idle_seconds <= 0 or not callable(idle_sleep):
            raise ValueError("Memory idle seal requires positive seconds and a sleep function")
        if (vector_store is None) != (embedding_provider is None):
            raise ValueError("vector store and embedding provider must be configured together")
        self.profile_id = profile_id
        self.store = store
        self.vector_store = vector_store
        self.embedding_provider = embedding_provider
        self.vector_wait_seconds = vector_wait_seconds
        self.cache_size = cache_size
        self.vector_task_limit = vector_task_limit or cache_size
        self.max_items = max_items
        self.max_chars = max_chars
        self.idle_seconds = float(idle_seconds)
        self._idle_sleep = idle_sleep
        self.worker = worker
        self.clock = clock or getattr(worker, "clock", time.time)
        self.extractor = extractor or RuleBasedMemoryExtractor()
        self.metrics = metrics
        self._lexical_cache: OrderedDict[tuple[int, str], tuple[dict[str, Any], ...]] = OrderedDict()
        self._vector_tasks: OrderedDict[tuple[int, str], asyncio.Task[tuple[dict[str, Any], ...]]] = OrderedDict()
        self._owned_vector_tasks: set[asyncio.Task[tuple[dict[str, Any], ...]]] = set()
        self._vector_waiters: dict[asyncio.Task[tuple[dict[str, Any], ...]], int] = {}
        self._episode_lock = asyncio.Lock()
        self._startup_lock = asyncio.Lock()
        self._segments: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._seal_generation = 0
        self._seal_tasks: set[asyncio.Task[None]] = set()
        self._seal_task: asyncio.Task[None] | None = None
        self._closed = False
        self._started = False

    @property
    def vector_tasks(self) -> tuple[asyncio.Task[tuple[dict[str, Any], ...]], ...]:
        """Return immutable task diagnostics without exposing the mutable cache."""

        return tuple(self._owned_vector_tasks)

    @property
    def cache_entries(self) -> int:
        return max(len(self._lexical_cache), len(self._vector_tasks))

    async def recall(
        self,
        *,
        query: str,
        revision: int,
        visible_source_segment_ids: Sequence[str] = (),
        visible_source_texts: Sequence[str] = (),
        **_identity: Any,
    ) -> tuple[dict[str, Any], ...]:
        """Return lexical-first active facts, degrading on any vector delay or failure."""

        started_at = time.monotonic()
        if self._closed:
            return ()
        normalized_query = str(query).strip()
        if not normalized_query:
            return ()
        key = (int(revision), normalized_query)
        vector_started_at = time.monotonic()
        vector_task: asyncio.Task[tuple[dict[str, Any], ...]] | None = None
        if self.vector_store is not None and self.embedding_provider is not None:
            vector_task = self._vector_tasks.get(key)
            if vector_task is None:
                if len(self._owned_vector_tasks) < self.vector_task_limit:
                    vector_task = asyncio.create_task(
                        self._vector_recall(normalized_query),
                        name=f"memory-vector-recall-{self.profile_id}-{revision}",
                    )
                    self._owned_vector_tasks.add(vector_task)
                    self._vector_waiters[vector_task] = 0
                    vector_task.add_done_callback(self._vector_task_done)
                    self._remember(self._vector_tasks, key, vector_task)
                else:
                    self._increment("memory.recall.vector_saturated")
            else:
                self._vector_tasks.move_to_end(key)
            if vector_task is not None:
                self._vector_waiters[vector_task] = self._vector_waiters.get(vector_task, 0) + 1
        try:
            lexical = self._lexical_cache.get(key)
            if lexical is None:
                lexical = tuple(await asyncio.to_thread(self._lexical_recall, normalized_query))
                self._remember(self._lexical_cache, key, lexical)
            else:
                self._lexical_cache.move_to_end(key)
        except BaseException:
            if vector_task is not None:
                self._release_vector_waiter(vector_task)
            raise
        self._increment("memory.recall.lexical")

        vector: tuple[dict[str, Any], ...] = ()
        if vector_task is not None:
            try:
                if vector_task.done():
                    vector = vector_task.result()
                else:
                    remaining = max(
                        0.0,
                        self.vector_wait_seconds - (time.monotonic() - vector_started_at),
                    )
                    vector = await asyncio.wait_for(asyncio.shield(vector_task), timeout=remaining)
            except TimeoutError:
                self._increment("memory.recall.vector_timeout")
                self._increment("memory.recall.degraded")
                vector = ()
            except Exception:
                self._increment("memory.recall.vector_failure")
                self._increment("memory.recall.degraded")
                vector = ()
            finally:
                self._release_vector_waiter(vector_task)

        candidate_ids = list(dict.fromkeys(
            str(item.get("memory_id", item.get("id", "")))
            for item in (*lexical, *vector)
            if item.get("memory_id", item.get("id"))
        ))
        # Cache is only an acceleration hint. A fresh SQLite active read is the immediate
        # authority, so forget takes effect even for the same query/revision cache key.
        active = await asyncio.to_thread(
            self.store.get_active_memories, candidate_ids, self.profile_id
        )
        result = self._filter_and_pack(
            active,
            visible_source_segment_ids=visible_source_segment_ids,
            visible_source_texts=visible_source_texts,
        )
        self._observe("memory.recall.latency", time.monotonic() - started_at)
        return result

    async def browse(self, *, limit: int = 50) -> dict[str, Any]:
        """Return bounded public Memory/job summaries without payload or lease internals."""

        bounded = max(1, min(int(limit), 100))
        memories, extraction_jobs, outbox_jobs = await asyncio.gather(
            asyncio.to_thread(self.store.list_active_memories, self.profile_id),
            asyncio.to_thread(
                self.store.browse_extraction_jobs,
                profile_id=self.profile_id,
                limit=bounded,
            ),
            asyncio.to_thread(
                self.store.browse_outbox,
                profile_id=self.profile_id,
                limit=bounded,
            ),
        )
        jobs = sorted(
            (*extraction_jobs, *outbox_jobs),
            key=lambda item: str(item.get("updated_at", "")),
            reverse=True,
        )
        return {
            "items": [
                {
                    "memoryId": item["memory_id"],
                    "episodeId": item["episode_id"],
                    "type": item["memory_type"],
                    "content": item["content"],
                    "createdAt": item["created_at"],
                    "updatedAt": item["updated_at"],
                }
                for item in memories[:bounded]
            ],
            "jobs": [_public_job(job) for job in jobs[:bounded]],
        }

    async def local_status(self) -> dict[str, Any]:
        """Read only SQLite/lifecycle facts; never probe embeddings or Qdrant."""

        schema_version = await asyncio.to_thread(self.store.schema_version)
        async with self._episode_lock:
            buffered = len(self._segments)
            seal_pending = bool(self._seal_task is not None and not self._seal_task.done())
        return {
            "closed": self._closed,
            "sqliteSchemaVersion": schema_version,
            "bufferedSegments": buffered,
            "sealPending": seal_pending,
        }

    async def forget(
        self,
        *,
        memory_ids: Sequence[str] = (),
        episode_ids: Sequence[str] = (),
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Commit immediate SQLite forget truth and durable vector-delete recovery."""

        result = await asyncio.to_thread(
            self.store.forget_with_outbox,
            profile_id=self.profile_id,
            memory_ids=tuple(memory_ids),
            episode_ids=tuple(episode_ids),
            reason=reason,
            now=float(self.clock()),
        )
        self._lexical_cache.clear()
        if self.worker is not None:
            self.worker.notify()
        return {
            "forgottenCount": result["forgotten_count"],
            "memoryIds": result["memory_ids"],
        }

    async def retry(self, job_id: str) -> dict[str, Any]:
        """Requeue one failed extraction/outbox item without exposing its durable payload."""

        result = await asyncio.to_thread(
            self.store.retry_failed,
            profile_id=self.profile_id,
            job_id=str(job_id),
            now=float(self.clock()),
        )
        if self.worker is not None:
            self.worker.notify()
        return _public_job(result)

    async def startup(self) -> None:
        """Recover the durable idle deadline, then register this profile with the global worker."""

        async with self._startup_lock:
            if self._started:
                return
            async with self._episode_lock:
                pending = await asyncio.to_thread(
                    self.store.load_pending_episode, profile_id=self.profile_id
                )
                if pending is not None:
                    self._segments = OrderedDict(
                        (item["segment_id"], dict(item))
                        for item in pending["segments"]
                    )
                    self._seal_generation = pending["generation"]
                    remaining = max(
                        0.0,
                        self.idle_seconds
                        - (float(self.clock()) - pending["last_confirmed_at"]),
                    )
                    self._schedule_seal_locked(self._seal_generation, remaining)
            if self.worker is not None:
                if self.vector_store is None or self.embedding_provider is None:
                    raise RuntimeError("Memory worker requires vector and embedding adapters")
                self.worker.register(
                    profile_id=self.profile_id,
                    store=self.store,
                    extractor=self.extractor,
                    vector_store=self.vector_store,
                    embedding_provider=self.embedding_provider,
                )
                await self.worker.startup()
            self._started = True

    async def record_confirmed_input(
        self,
        *,
        segment_id: str,
        content: str,
        input_id: str | None = None,
        revision: int | None = None,
    ) -> None:
        """Append one confirmed user segment exactly once and reset its idle generation."""

        if self._closed:
            return
        normalized_id = str(segment_id).strip()
        normalized_content = str(content).strip()
        if not normalized_id or not normalized_content:
            raise ValueError("confirmed Memory input requires segment_id and content")
        async with self._episode_lock:
            current = self._segments.get(normalized_id)
            if current is not None and current["content"] == normalized_content:
                return
            pending = await asyncio.to_thread(
                self.store.append_pending_episode,
                profile_id=self.profile_id,
                segment={"segment_id": normalized_id, "content": normalized_content},
                confirmed_at=float(self.clock()),
            )
            self._segments = OrderedDict(
                (item["segment_id"], dict(item)) for item in pending["segments"]
            )
            self._seal_generation = pending["generation"]
            self._schedule_seal_locked(self._seal_generation, self.idle_seconds)

    def _schedule_seal_locked(self, generation: int, delay: float) -> None:
        """Replace the owned timer while the episode lock protects its durable generation."""

        previous = self._seal_task
        if previous is not None and not previous.done():
            previous.cancel()
        task = asyncio.create_task(
            self._seal_after_idle(generation, delay),
            name=f"memory-idle-seal-{self.profile_id}-{generation}",
        )
        self._seal_task = task
        self._seal_tasks.add(task)
        task.add_done_callback(self._seal_tasks.discard)

    async def wait_for_seals(self) -> None:
        """Wait for currently owned seal timers, including cancelled-generation cleanup."""

        while self._seal_tasks:
            await asyncio.gather(
                *(asyncio.shield(task) for task in tuple(self._seal_tasks)),
                return_exceptions=True,
            )

    async def _seal_after_idle(self, generation: int, delay: float) -> None:
        try:
            await self._idle_sleep(delay)
        except asyncio.CancelledError:
            raise
        async with self._episode_lock:
            if self._closed or generation != self._seal_generation or not self._segments:
                return
            result = await asyncio.to_thread(
                self.store.seal_pending_episode_job,
                profile_id=self.profile_id,
                generation=generation,
                now=float(self.clock()),
            )
            if result is None:
                return
            self._increment("memory.seal.completed")
            self._segments.clear()
            if self._seal_task is asyncio.current_task():
                self._seal_task = None
            if self.worker is not None:
                self.worker.notify()

    async def shutdown(self) -> None:
        """Cancel unfinished optional vector work owned by this bridge."""

        if self._closed:
            return
        self._closed = True
        seal_tasks = tuple(task for task in self._seal_tasks if not task.done())
        for task in seal_tasks:
            task.cancel()
        tasks = tuple(task for task in self._owned_vector_tasks if not task.done())
        for task in tasks:
            task.cancel()
        owned = (*seal_tasks, *tasks)
        if owned:
            await asyncio.gather(*owned, return_exceptions=True)
        self._owned_vector_tasks.clear()
        self._vector_waiters.clear()
        self._vector_tasks.clear()

    def _lexical_recall(self, query: str) -> list[dict[str, Any]]:
        seeds = self.store.search_fts(
            profile_id=self.profile_id, query=query, limit=max(self.max_items * 2, self.max_items)
        )
        return self.store.get_active_memories(
            [str(seed["memory_id"]) for seed in seeds], self.profile_id
        )

    async def _vector_recall(self, query: str) -> tuple[dict[str, Any], ...]:
        assert self.vector_store is not None and self.embedding_provider is not None
        vector = await _external_call(self.embedding_provider.embed, query)
        seeds = await _external_call(
            self.vector_store.search,
            vector,
            profile_id=self.profile_id,
            limit=max(self.max_items * 2, self.max_items),
            filters=None,
        )
        memory_ids = [
            str(seed["memory_id"])
            for seed in seeds
            if isinstance(seed, Mapping) and seed.get("memory_id")
        ]
        active = await asyncio.to_thread(
            self.store.get_active_memories, memory_ids, self.profile_id
        )
        return tuple(active)

    def _filter_and_pack(
        self,
        memories: Sequence[Mapping[str, Any]],
        *,
        visible_source_segment_ids: Sequence[str],
        visible_source_texts: Sequence[str],
    ) -> tuple[dict[str, Any], ...]:
        visible_ids = {str(value) for value in visible_source_segment_ids if value}
        visible_text = "\n".join(str(value) for value in visible_source_texts if value)
        seen_ids: set[str] = set()
        seen_content: set[str] = set()
        result: list[dict[str, Any]] = []
        remaining = self.max_chars
        for memory in memories:
            memory_id = str(memory.get("memory_id", memory.get("id", "")))
            content = str(memory.get("content", "")).strip()
            if not memory_id or not content or memory_id in seen_ids:
                continue
            normalized = _normalized_content(content)
            if not normalized or normalized in seen_content:
                continue
            metadata = memory.get("metadata", {})
            if not isinstance(metadata, Mapping):
                metadata = {}
            source_ids = tuple(
                str(value) for value in metadata.get("source_segment_ids", ()) if value
            )
            if visible_ids.intersection(source_ids) or _overlaps_visible(content, visible_text):
                continue
            clipped = content[:remaining]
            if not clipped:
                break
            result.append({
                "id": memory_id,
                "content": clipped,
                "type": str(memory.get("memory_type", "semantic")),
                "sourceSegmentIds": list(source_ids),
            })
            seen_ids.add(memory_id)
            seen_content.add(normalized)
            remaining -= len(clipped)
            if len(result) >= self.max_items or remaining <= 0:
                break
        return tuple(result)

    def _remember(self, cache: OrderedDict, key: tuple[int, str], value: Any) -> None:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > self.cache_size:
            _old_key, old_value = cache.popitem(last=False)
            if isinstance(old_value, asyncio.Task) and not old_value.done():
                if self._vector_waiters.get(old_value, 0) == 0:
                    old_value.cancel()

    def _release_vector_waiter(
        self, task: asyncio.Task[tuple[dict[str, Any], ...]]
    ) -> None:
        waiters = self._vector_waiters.get(task, 0)
        if waiters <= 1:
            self._vector_waiters.pop(task, None)
            if task not in self._vector_tasks.values() and not task.done():
                task.cancel()
        else:
            self._vector_waiters[task] = waiters - 1

    def _vector_task_done(
        self, task: asyncio.Task[tuple[dict[str, Any], ...]]
    ) -> None:
        self._owned_vector_tasks.discard(task)
        self._vector_waiters.pop(task, None)
        _consume_task_result(task)

    def _increment(self, name: str) -> None:
        increment = getattr(self.metrics, "increment", None)
        if callable(increment):
            increment(name)

    def _observe(self, name: str, seconds: float) -> None:
        observe = getattr(self.metrics, "observe", None)
        if callable(observe):
            observe(name, seconds)


class MemoryWorker:
    """One event-driven process owner for every registered profile's durable Memory work."""

    def __init__(
        self,
        *,
        clock: Any = time.time,
        sleep: Any = asyncio.sleep,
        lease_seconds: float = 30.0,
        heartbeat_seconds: float | None = None,
        heartbeat_sleep: Any = asyncio.sleep,
        metrics: Any | None = None,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("Memory worker lease must be positive")
        heartbeat = lease_seconds / 3 if heartbeat_seconds is None else heartbeat_seconds
        if heartbeat <= 0 or heartbeat >= lease_seconds or not callable(heartbeat_sleep):
            raise ValueError("Memory heartbeat must be callable and shorter than its lease")
        self.clock = clock
        self.sleep = sleep
        self.lease_seconds = float(lease_seconds)
        self.heartbeat_seconds = float(heartbeat)
        self.heartbeat_sleep = heartbeat_sleep
        self.metrics = metrics
        self.owner = f"memory-worker-{uuid.uuid4().hex}"
        self._profiles: OrderedDict[str, _WorkerProfile] = OrderedDict()
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._closing = False

    @property
    def live_task(self) -> asyncio.Task[None] | None:
        task = self._task
        return task if task is not None and not task.done() else None

    def register(
        self,
        *,
        profile_id: str,
        store: SQLiteMemoryStore,
        extractor: MemoryExtractor,
        vector_store: VectorStore,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        registration = _WorkerProfile(store, extractor, vector_store, embedding_provider)
        existing = self._profiles.get(profile_id)
        if existing is not None and existing != registration:
            raise ValueError(f"Memory profile already registered: {profile_id}")
        self._profiles[profile_id] = registration
        self._wake.set()

    async def startup(self) -> None:
        if self._closing:
            raise RuntimeError("Memory worker is closed")
        if self.live_task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="memory-global-worker")
        self._increment("memory.worker.startup")
        logger.info("memory_worker_started", extra={"profile_count": len(self._profiles)})
        self._wake.set()

    def notify(self) -> None:
        if not self._closing:
            self._wake.set()

    async def shutdown(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._wake.set()
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._increment("memory.worker.shutdown")
        logger.info("memory_worker_stopped")

    async def drain_ready(self) -> int:
        """Process all currently due jobs/outbox entries, stopping before future retry times."""

        processed = 0
        while not self._closing:
            claimed = False
            for profile_id, profile in tuple(self._profiles.items()):
                now = float(self.clock())
                job = await asyncio.to_thread(
                    profile.store.claim_extraction_job,
                    owner=self.owner,
                    lease_seconds=self.lease_seconds,
                    now=now,
                )
                if job is not None:
                    lease_lost = await self._process_extraction(profile_id, profile, job)
                    processed += 1
                    if lease_lost:
                        return processed
                    claimed = True
                    break
                outbox = await asyncio.to_thread(
                    profile.store.claim_outbox,
                    owner=self.owner,
                    lease_seconds=self.lease_seconds,
                    now=now,
                )
                if outbox is not None:
                    lease_lost = await self._process_outbox(profile_id, profile, outbox)
                    processed += 1
                    if lease_lost:
                        return processed
                    claimed = True
                    break
            if not claimed:
                return processed
        return processed

    async def _process_extraction(
        self, profile_id: str, profile: _WorkerProfile, job: Mapping[str, Any]
    ) -> bool:
        try:
            payload = job["payload"]
            segments = payload.get("segments", [])
            content = "\n".join(str(item.get("content", "")) for item in segments)
            candidates, _entities = await self._run_with_lease_heartbeat(
                lambda: _external_call(
                    profile.extractor.extract,
                    content=content,
                    metadata={
                        "source_segment_ids": list(payload.get("source_segment_ids", [])),
                        "episode_id": job["episode_id"],
                    },
                ),
                lambda: profile.store.renew_extraction_lease(
                    job_id=job["job_id"], owner=self.owner,
                    lease_seconds=self.lease_seconds, now=float(self.clock()),
                ),
            )
            validated = tuple(validate_memory_candidate(candidate) for candidate in candidates)
            await asyncio.to_thread(
                profile.store.complete_extraction_job,
                job_id=job["job_id"], owner=self.owner,
                candidates=validated, now=float(self.clock()),
            )
            self._increment("memory.job.completed")
            return False
        except asyncio.CancelledError:
            raise
        except (_LeaseLost, MemoryLeaseLostError):
            self._increment("memory.job.lease_lost")
            logger.warning("memory_extraction_lease_lost")
            return True
        except Exception as exc:
            try:
                released = await asyncio.to_thread(
                    profile.store.release_extraction_job,
                    job_id=job["job_id"], owner=self.owner,
                    error=type(exc).__name__, now=float(self.clock()),
                )
            except MemoryLeaseLostError:
                self._increment("memory.job.lease_lost")
                logger.warning("memory_extraction_lease_lost")
                return True
            self._increment(
                "memory.job.failed" if released["state"] == "failed" else "memory.job.retry"
            )
            logger.warning(
                "memory_extraction_released",
                extra={"state": released["state"], "attempt_count": released["attempt_count"]},
            )
            return False

    async def _process_outbox(
        self, profile_id: str, profile: _WorkerProfile, outbox: Mapping[str, Any]
    ) -> bool:
        try:
            memory_id = str(outbox["memory_id"])
            async def deliver() -> None:
                if outbox["operation"] == "delete":
                    await _external_call(profile.vector_store.delete, [memory_id])
                    return
                payload = dict(outbox["payload"])
                vector_text = " ".join(
                    [str(payload.get("content", "")), *map(str, payload.get("entities", []))]
                )
                vector = await _external_call(profile.embedding_provider.embed, vector_text)
                active = await asyncio.to_thread(
                    profile.store.get_active_memories, [memory_id], profile_id
                )
                if active:
                    await _external_call(
                        profile.vector_store.upsert,
                        memory_id,
                        vector,
                        {
                            **payload,
                            "memory_id": memory_id,
                            "profile_id": profile_id,
                            "state": "active",
                        },
                    )

            await self._run_with_lease_heartbeat(
                deliver,
                lambda: profile.store.renew_outbox_lease(
                    outbox_id=outbox["outbox_id"], owner=self.owner,
                    lease_seconds=self.lease_seconds, now=float(self.clock()),
                ),
            )
            await asyncio.to_thread(
                profile.store.complete_outbox,
                outbox_id=outbox["outbox_id"], owner=self.owner, now=float(self.clock()),
            )
            self._increment("memory.outbox.completed")
            return False
        except asyncio.CancelledError:
            raise
        except (_LeaseLost, MemoryLeaseLostError):
            self._increment("memory.outbox.lease_lost")
            logger.warning("memory_outbox_lease_lost")
            return True
        except Exception as exc:
            try:
                released = await asyncio.to_thread(
                    profile.store.release_outbox,
                    outbox_id=outbox["outbox_id"], owner=self.owner,
                    error=type(exc).__name__, now=float(self.clock()),
                )
            except MemoryLeaseLostError:
                self._increment("memory.outbox.lease_lost")
                logger.warning("memory_outbox_lease_lost")
                return True
            self._increment(
                "memory.outbox.failed"
                if released["state"] == "failed"
                else "memory.outbox.retry"
            )
            logger.warning(
                "memory_outbox_released",
                extra={"state": released["state"], "attempt_count": released["attempt_count"]},
            )
            return False

    async def _run_with_lease_heartbeat(self, operation: Any, renew: Any) -> Any:
        external = asyncio.create_task(operation(), name="memory-leased-external-call")
        lease_lost = False

        async def heartbeat() -> None:
            nonlocal lease_lost
            while not external.done():
                await self.heartbeat_sleep(self.heartbeat_seconds)
                if external.done():
                    return
                if not await asyncio.to_thread(renew):
                    lease_lost = True
                    return

        heartbeat_task = asyncio.create_task(heartbeat(), name="memory-lease-heartbeat")
        try:
            try:
                result = await external
            except asyncio.CancelledError:
                raise
            except Exception:
                if lease_lost:
                    raise _LeaseLost from None
                raise
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
        if lease_lost or not await asyncio.to_thread(renew):
            raise _LeaseLost
        return result

    async def _run(self) -> None:
        while True:
            self._wake.clear()
            await self.drain_ready()
            if self._closing:
                return
            next_at = min(
                (
                    next_at
                    for profile in self._profiles.values()
                    if (next_at := profile.store.next_work_at()) is not None
                ),
                default=None,
            )
            if self._wake.is_set():
                continue
            if next_at is None:
                await self._wake.wait()
                continue
            delay = max(0.0, next_at - float(self.clock()))
            wake = asyncio.create_task(self._wake.wait())
            deadline = asyncio.create_task(self.sleep(delay))
            done, pending = await asyncio.wait(
                {wake, deadline}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)

    def _increment(self, name: str) -> None:
        increment = getattr(self.metrics, "increment", None)
        if callable(increment):
            increment(name)


def _consume_task_result(task: asyncio.Task[Any]) -> None:
    if task.cancelled():
        return
    try:
        task.exception()
    except BaseException:
        pass


def _public_job(job: Mapping[str, Any]) -> dict[str, Any]:
    job_id = job.get("job_id", job.get("outbox_id"))
    return {
        "jobId": str(job_id),
        "kind": (
            "extraction"
            if "job_id" in job
            else "vectorDelete" if job.get("operation") == "delete" else "vectorUpsert"
        ),
        "state": str(job["state"]),
        "attemptCount": int(job["attempt_count"]),
        "lastError": job.get("last_error"),
        "updatedAt": job.get("updated_at"),
    }


async def _external_call(function: Any, *args: Any, **kwargs: Any) -> Any:
    """Use the loop's shared executor only for synchronous embedding/vector adapters."""

    if inspect.iscoroutinefunction(function):
        return await function(*args, **kwargs)
    result = await asyncio.to_thread(function, *args, **kwargs)
    return await result if inspect.isawaitable(result) else result


def _normalized_content(content: str) -> str:
    return re.sub(r"\s+", "", content).casefold()


def _overlaps_visible(content: str, visible: str) -> bool:
    normalized_content = _normalized_content(content)
    normalized_visible = _normalized_content(visible)
    return bool(
        normalized_content
        and normalized_visible
        and (normalized_content in normalized_visible or normalized_visible in normalized_content)
    )


__all__ = ["MemoryBridge", "MemoryWorker"]
