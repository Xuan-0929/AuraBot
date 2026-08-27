"""Persist long-term Memory truth plus Task-9 extraction and vector-delivery work.

``SQLiteMemoryStore`` is the authoritative per-profile ``memory.sqlite3`` boundary.  Existing episode,
item/entity/relation/FTS APIs remain compatible; Task 9 adds durable pending episode assembly, atomic
episode/job sealing, leased extraction jobs, and an idempotent Qdrant outbox in the same database. Every
claim/recovery transition uses
``BEGIN IMMEDIATE`` under the store lock so restart and competing worker instances cannot duplicate
ownership, while owner/CAS renewal protects slow external stages and SQLite active state remains the
immediate recall/forget authority. Schema discovery is fail-closed: declared versions and shapes are
validated before DDL, and each supported version migrates in its own rollback-safe transaction.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from ..types import EntityCandidate, MemoryCandidate


SCHEMA_VERSION = 4
ACTIVE_MEMORY_STATES = {"active", "archived", "stale", "superseded"}


class MemoryLeaseLostError(RuntimeError):
    """Signal that durable work is no longer owned without classifying SQLite failure as retryable."""


class SQLiteMemoryStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        try:
            self._migrate()
        except BaseException:
            self._connection.close()
            raise

    def close(self) -> None:
        self._connection.close()

    def _get_connection(self) -> sqlite3.Connection:
        return self._connection

    def schema_version(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "select value from memory_schema_info where key = 'schema_version'"
            ).fetchone()

        if row is None:
            return 0

        try:
            return int(row["value"])
        except (TypeError, ValueError):
            return 0

    def table_columns(self, table_name: str) -> list[str]:
        with self._lock:
            rows = self._connection.execute(f"pragma table_info({table_name})").fetchall()

        return [row["name"] for row in rows]

    def create_episode(
        self,
        *,
        profile_id: str,
        source_type: str,
        source_id: str | None,
        role: str | None,
        content: str,
        metadata: dict[str, Any],
    ) -> str:
        episode_id = f"episode_{uuid4().hex}"
        now = now_iso()

        with self._lock, self._connection:
            self._connection.execute(
                """
                insert into memory_episodes (
                  id, profile_id, source_type, source_id, role, content, metadata_json, created_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    episode_id,
                    profile_id,
                    source_type,
                    source_id,
                    role,
                    content,
                    dumps(metadata),
                    now,
                ),
            )

        return episode_id

    def seal_episode_job(
        self,
        *,
        profile_id: str,
        segments: Sequence[Mapping[str, Any]],
        now: float,
    ) -> dict[str, str]:
        """Atomically persist one immutable episode payload and its extraction job."""

        frozen_segments = _frozen_segments(segments)
        with self._lock:
            connection = self._get_connection()
            with connection:
                connection.execute("begin immediate")
                return _insert_episode_job(
                    connection, profile_id=profile_id, segments=frozen_segments, now=now
                )

    def append_pending_episode(
        self,
        *,
        profile_id: str,
        segment: Mapping[str, Any],
        confirmed_at: float,
    ) -> dict[str, Any]:
        """Atomically append/update one confirmed segment and advance the durable idle generation."""

        frozen = _frozen_segments((segment,))[0]
        with self._lock:
            connection = self._get_connection()
            with connection:
                connection.execute("begin immediate")
                row = connection.execute(
                    "select * from memory_pending_episodes where profile_id = ?",
                    (profile_id,),
                ).fetchone()
                segments = [] if row is None else loads(row["segments_json"], [])
                if not isinstance(segments, list):
                    raise RuntimeError("pending Memory episode payload is malformed")
                by_id = {} if row is None else {
                    item["segment_id"]: item for item in _frozen_segments(segments)
                }
                if by_id.get(frozen["segment_id"]) == frozen:
                    return _pending_episode_dict(row)
                by_id[frozen["segment_id"]] = frozen
                generation = 1 if row is None else int(row["generation"]) + 1
                connection.execute(
                    """
                    insert into memory_pending_episodes (
                      profile_id, generation, last_confirmed_at, segments_json, updated_at
                    ) values (?, ?, ?, ?, ?)
                    on conflict(profile_id) do update set
                      generation = excluded.generation,
                      last_confirmed_at = excluded.last_confirmed_at,
                      segments_json = excluded.segments_json,
                      updated_at = excluded.updated_at
                    """,
                    (
                        profile_id, generation, float(confirmed_at),
                        dumps(list(by_id.values())), now_iso(),
                    ),
                )
                current = connection.execute(
                    "select * from memory_pending_episodes where profile_id = ?",
                    (profile_id,),
                ).fetchone()
        return _pending_episode_dict(current)

    def load_pending_episode(self, *, profile_id: str) -> dict[str, Any] | None:
        """Return one immutable pending episode snapshot for startup recovery."""

        with self._lock:
            row = self._get_connection().execute(
                "select * from memory_pending_episodes where profile_id = ?",
                (profile_id,),
            ).fetchone()
        return None if row is None else _pending_episode_dict(row)

    def seal_pending_episode_job(
        self, *, profile_id: str, generation: int, now: float
    ) -> dict[str, str] | None:
        """Atomically seal only the still-current pending generation and clear its durable buffer."""

        with self._lock:
            connection = self._get_connection()
            with connection:
                connection.execute("begin immediate")
                row = connection.execute(
                    """
                    select * from memory_pending_episodes
                    where profile_id = ? and generation = ?
                    """,
                    (profile_id, int(generation)),
                ).fetchone()
                if row is None:
                    return None
                result = _insert_episode_job(
                    connection,
                    profile_id=profile_id,
                    segments=_frozen_segments(loads(row["segments_json"], [])),
                    now=now,
                )
                connection.execute(
                    """
                    delete from memory_pending_episodes
                    where profile_id = ? and generation = ?
                    """,
                    (profile_id, int(generation)),
                )
                return result

    def browse_extraction_jobs(
        self, *, profile_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._get_connection().execute(
                """
                select * from memory_extraction_jobs
                where profile_id = ? order by created_at desc, id desc limit ?
                """,
                (profile_id, max(1, min(int(limit), 200))),
            ).fetchall()
        return [_job_row_to_dict(row) for row in rows]

    def get_extraction_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._get_connection().execute(
                "select * from memory_extraction_jobs where id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return _job_row_to_dict(row)

    def claim_extraction_job(
        self, *, owner: str, lease_seconds: float, now: float
    ) -> dict[str, Any] | None:
        if not owner or lease_seconds <= 0:
            raise ValueError("claim requires owner and positive lease")
        with self._lock:
            connection = self._get_connection()
            with connection:
                connection.execute("begin immediate")
                connection.execute(
                    """
                    update memory_extraction_jobs
                    set state = 'pending', lease_owner = null, lease_expires_at = null,
                        next_attempt_at = min(next_attempt_at, ?), updated_at = ?
                    where state = 'processing' and lease_expires_at <= ?
                    """,
                    (float(now), now_iso(), float(now)),
                )
                row = connection.execute(
                    """
                    select * from memory_extraction_jobs
                    where state = 'pending' and next_attempt_at <= ?
                    order by next_attempt_at asc, created_at asc, id asc limit 1
                    """,
                    (float(now),),
                ).fetchone()
                if row is None:
                    return None
                cursor = connection.execute(
                    """
                    update memory_extraction_jobs
                    set state = 'processing', attempt_count = attempt_count + 1,
                        lease_owner = ?, lease_expires_at = ?, updated_at = ?
                    where id = ? and state = 'pending'
                    """,
                    (owner, float(now) + lease_seconds, now_iso(), row["id"]),
                )
                if cursor.rowcount != 1:
                    return None
                claimed = connection.execute(
                    "select * from memory_extraction_jobs where id = ?", (row["id"],)
                ).fetchone()
        return _job_row_to_dict(claimed)

    def complete_extraction_job(
        self,
        *,
        job_id: str,
        owner: str,
        candidates: Sequence[MemoryCandidate],
        now: float,
    ) -> dict[str, Any]:
        """Commit validated items, idempotent upsert outbox, and job completion together."""

        with self._lock:
            connection = self._get_connection()
            with connection:
                connection.execute("begin immediate")
                row = connection.execute(
                    "select * from memory_extraction_jobs where id = ?", (job_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(job_id)
                if row["state"] == "completed":
                    return _job_row_to_dict(row)
                if row["state"] != "processing" or row["lease_owner"] != owner:
                    raise MemoryLeaseLostError("Memory extraction job lease is not owned")
                if row["lease_expires_at"] <= float(now):
                    raise MemoryLeaseLostError("Memory extraction job lease expired")
                payload = loads(row["payload_json"], {})
                source_ids = payload.get("source_segment_ids", [])
                memory_ids: list[str] = []
                for index, candidate in enumerate(candidates):
                    memory_id = f"memory_{uuid5(NAMESPACE_URL, f'{job_id}:{index}').hex}"
                    memory_ids.append(memory_id)
                    metadata = {
                        **candidate.metadata,
                        "entities": candidate.entities,
                        "source_segment_ids": list(source_ids),
                    }
                    timestamp = now_iso()
                    connection.execute(
                        """
                        insert or ignore into memory_items (
                          id, profile_id, episode_id, memory_type, state, content,
                          importance, confidence, valid_from, valid_to, metadata_json,
                          forget_reason, created_at, updated_at
                        ) values (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, null, ?, ?)
                        """,
                        (
                            memory_id, row["profile_id"], row["episode_id"], candidate.memory_type,
                            candidate.content, candidate.importance, candidate.confidence,
                            candidate.valid_from, candidate.valid_to, dumps(metadata), timestamp, timestamp,
                        ),
                    )
                    connection.execute(
                        """
                        insert into memory_items_fts(memory_id, content)
                        select ?, ? where not exists (
                          select 1 from memory_items_fts where memory_id = ?
                        )
                        """,
                        (memory_id, candidate.content, memory_id),
                    )
                    outbox_payload = {
                        "content": candidate.content,
                        "entities": candidate.entities,
                        "episode_id": row["episode_id"],
                        "memory_type": candidate.memory_type,
                        "importance": candidate.importance,
                        "confidence": candidate.confidence,
                        "valid_from": candidate.valid_from,
                        "valid_to": candidate.valid_to,
                        "source_segment_ids": source_ids,
                    }
                    connection.execute(
                        """
                        insert or ignore into memory_vector_outbox (
                          id, profile_id, memory_id, operation, state, payload_json,
                          attempt_count, next_attempt_at, last_error, lease_owner,
                          lease_expires_at, created_at, updated_at, completed_at
                        ) values (?, ?, ?, 'upsert', 'pending', ?, 0, ?, null, null, null, ?, ?, null)
                        """,
                        (
                            f"outbox_upsert_{memory_id}", row["profile_id"], memory_id,
                            dumps(outbox_payload), float(now), timestamp, timestamp,
                        ),
                    )
                completed_at = now_iso()
                connection.execute(
                    """
                    update memory_extraction_jobs
                    set state = 'completed', result_json = ?, last_error = null,
                        lease_owner = null, lease_expires_at = null,
                        updated_at = ?, completed_at = ?
                    where id = ? and state = 'processing' and lease_owner = ?
                    """,
                    (dumps({"memory_ids": memory_ids}), completed_at, completed_at, job_id, owner),
                )
                completed = connection.execute(
                    "select * from memory_extraction_jobs where id = ?", (job_id,)
                ).fetchone()
        return _job_row_to_dict(completed)

    def release_extraction_job(
        self, *, job_id: str, owner: str, error: str, now: float
    ) -> dict[str, Any]:
        return self._release_work(
            table="memory_extraction_jobs", work_id=job_id, owner=owner,
            error=error, now=now,
        )

    def renew_extraction_lease(
        self, *, job_id: str, owner: str, lease_seconds: float, now: float
    ) -> bool:
        return self._renew_work_lease(
            table="memory_extraction_jobs", work_id=job_id, owner=owner,
            lease_seconds=lease_seconds, now=now,
        )

    def browse_outbox(self, *, profile_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._get_connection().execute(
                """
                select * from memory_vector_outbox
                where profile_id = ? order by created_at asc, id asc limit ?
                """,
                (profile_id, max(1, min(int(limit), 200))),
            ).fetchall()
        return [_outbox_row_to_dict(row) for row in rows]

    def claim_outbox(
        self, *, owner: str, lease_seconds: float, now: float
    ) -> dict[str, Any] | None:
        if not owner or lease_seconds <= 0:
            raise ValueError("claim requires owner and positive lease")
        with self._lock:
            connection = self._get_connection()
            with connection:
                connection.execute("begin immediate")
                connection.execute(
                    """
                    update memory_vector_outbox
                    set state = 'pending', lease_owner = null, lease_expires_at = null,
                        next_attempt_at = min(next_attempt_at, ?), updated_at = ?
                    where state = 'processing' and lease_expires_at <= ?
                    """,
                    (float(now), now_iso(), float(now)),
                )
                row = connection.execute(
                    """
                    select * from memory_vector_outbox
                    where state = 'pending' and next_attempt_at <= ?
                    order by next_attempt_at asc, created_at asc, id asc limit 1
                    """,
                    (float(now),),
                ).fetchone()
                if row is None:
                    return None
                cursor = connection.execute(
                    """
                    update memory_vector_outbox
                    set state = 'processing', attempt_count = attempt_count + 1,
                        lease_owner = ?, lease_expires_at = ?, updated_at = ?
                    where id = ? and state = 'pending'
                    """,
                    (owner, float(now) + lease_seconds, now_iso(), row["id"]),
                )
                if cursor.rowcount != 1:
                    return None
                claimed = connection.execute(
                    "select * from memory_vector_outbox where id = ?", (row["id"],)
                ).fetchone()
        return _outbox_row_to_dict(claimed)

    def complete_outbox(self, *, outbox_id: str, owner: str, now: float) -> dict[str, Any]:
        with self._lock:
            connection = self._get_connection()
            with connection:
                connection.execute("begin immediate")
                cursor = connection.execute(
                    """
                    update memory_vector_outbox
                    set state = 'completed', last_error = null, lease_owner = null,
                        lease_expires_at = null, updated_at = ?, completed_at = ?
                    where id = ? and state = 'processing' and lease_owner = ?
                      and lease_expires_at > ?
                    """,
                    (now_iso(), now_iso(), outbox_id, owner, float(now)),
                )
                if cursor.rowcount != 1:
                    raise MemoryLeaseLostError("Memory outbox lease is not owned or expired")
                row = connection.execute(
                    "select * from memory_vector_outbox where id = ?", (outbox_id,)
                ).fetchone()
        return _outbox_row_to_dict(row)

    def release_outbox(
        self, *, outbox_id: str, owner: str, error: str, now: float
    ) -> dict[str, Any]:
        return self._release_work(
            table="memory_vector_outbox", work_id=outbox_id, owner=owner,
            error=error, now=now,
        )

    def renew_outbox_lease(
        self, *, outbox_id: str, owner: str, lease_seconds: float, now: float
    ) -> bool:
        return self._renew_work_lease(
            table="memory_vector_outbox", work_id=outbox_id, owner=owner,
            lease_seconds=lease_seconds, now=now,
        )

    def retry_failed(
        self, *, profile_id: str, job_id: str, now: float
    ) -> dict[str, Any]:
        for table, converter in (
            ("memory_extraction_jobs", _job_row_to_dict),
            ("memory_vector_outbox", _outbox_row_to_dict),
        ):
            with self._lock:
                connection = self._get_connection()
                with connection:
                    connection.execute("begin immediate")
                    cursor = connection.execute(
                        f"""
                        update {table}
                        set state = 'pending', attempt_count = 0, next_attempt_at = ?,
                            last_error = null, lease_owner = null, lease_expires_at = null,
                            updated_at = ?, completed_at = null
                        where id = ? and profile_id = ? and state = 'failed'
                        """,
                        (float(now), now_iso(), job_id, profile_id),
                    )
                    if cursor.rowcount == 1:
                        row = connection.execute(
                            f"select * from {table} where id = ?", (job_id,)
                        ).fetchone()
                        return converter(row)
        raise KeyError(job_id)

    def next_work_at(self) -> float | None:
        with self._lock:
            row = self._get_connection().execute(
                """
                select min(next_attempt_at) as next_at from (
                  select next_attempt_at from memory_extraction_jobs where state = 'pending'
                  union all
                  select next_attempt_at from memory_vector_outbox where state = 'pending'
                )
                """
            ).fetchone()
        return None if row is None or row["next_at"] is None else float(row["next_at"])

    def _release_work(
        self, *, table: str, work_id: str, owner: str, error: str, now: float
    ) -> dict[str, Any]:
        converter = _job_row_to_dict if table == "memory_extraction_jobs" else _outbox_row_to_dict
        with self._lock:
            connection = self._get_connection()
            with connection:
                connection.execute("begin immediate")
                row = connection.execute(f"select * from {table} where id = ?", (work_id,)).fetchone()
                if row is None:
                    raise KeyError(work_id)
                if (
                    row["state"] != "processing"
                    or row["lease_owner"] != owner
                    or row["lease_expires_at"] <= float(now)
                ):
                    raise MemoryLeaseLostError("Memory work lease is not owned or expired")
                attempt = int(row["attempt_count"])
                delays = (5.0, 30.0, 300.0)
                state = "pending" if attempt <= len(delays) else "failed"
                next_at = float(now) + delays[attempt - 1] if state == "pending" else float(now)
                connection.execute(
                    f"""
                    update {table}
                    set state = ?, next_attempt_at = ?, last_error = ?,
                        lease_owner = null, lease_expires_at = null, updated_at = ?
                    where id = ? and state = 'processing' and lease_owner = ?
                    """,
                    (state, next_at, str(error)[:1_000], now_iso(), work_id, owner),
                )
                updated = connection.execute(f"select * from {table} where id = ?", (work_id,)).fetchone()
        return converter(updated)

    def _renew_work_lease(
        self,
        *,
        table: str,
        work_id: str,
        owner: str,
        lease_seconds: float,
        now: float,
    ) -> bool:
        if table not in {"memory_extraction_jobs", "memory_vector_outbox"}:
            raise ValueError("unsupported Memory lease table")
        if not owner or lease_seconds <= 0:
            raise ValueError("lease renewal requires owner and positive lease")
        with self._lock, self._get_connection():
            cursor = self._get_connection().execute(
                f"""
                update {table}
                set lease_expires_at = ?, updated_at = ?
                where id = ? and state = 'processing' and lease_owner = ?
                  and lease_expires_at > ?
                """,
                (float(now) + lease_seconds, now_iso(), work_id, owner, float(now)),
            )
        return cursor.rowcount == 1

    def create_memory(
        self,
        *,
        profile_id: str,
        episode_id: str,
        candidate: MemoryCandidate,
    ) -> str:
        memory_id = f"memory_{uuid4().hex}"
        now = now_iso()

        with self._lock, self._connection:
            self._connection.execute(
                """
                insert into memory_items (
                  id, profile_id, episode_id, memory_type, state, content,
                  importance, confidence, valid_from, valid_to, metadata_json,
                  forget_reason, created_at, updated_at
                )
                values (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, null, ?, ?)
                """,
                (
                    memory_id,
                    profile_id,
                    episode_id,
                    candidate.memory_type,
                    candidate.content,
                    candidate.importance,
                    candidate.confidence,
                    candidate.valid_from,
                    candidate.valid_to,
                    dumps(
                        {
                            **candidate.metadata,
                            "entities": candidate.entities,
                        }
                    ),
                    now,
                    now,
                ),
            )
            self._connection.execute(
                "insert into memory_items_fts(memory_id, content) values (?, ?)",
                (memory_id, candidate.content),
            )

        return memory_id

    def upsert_entity(
        self,
        *,
        profile_id: str,
        candidate: EntityCandidate,
    ) -> str:
        now = now_iso()

        with self._lock, self._connection:
            existing = self._connection.execute(
                """
                select id, aliases_json
                from memory_entities
                where profile_id = ? and name = ? and state = 'active'
                limit 1
                """,
                (profile_id, candidate.name),
            ).fetchone()

            if existing:
                aliases = sorted(set(loads(existing["aliases_json"], []) + candidate.aliases))
                self._connection.execute(
                    """
                    update memory_entities
                    set aliases_json = ?, summary = coalesce(nullif(?, ''), summary), updated_at = ?
                    where id = ?
                    """,
                    (dumps(aliases), candidate.summary, now, existing["id"]),
                )
                return existing["id"]

            entity_id = f"entity_{uuid4().hex}"
            self._connection.execute(
                """
                insert into memory_entities (
                  id, profile_id, name, entity_type, aliases_json, summary, state, created_at, updated_at
                )
                values (?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    entity_id,
                    profile_id,
                    candidate.name,
                    candidate.entity_type,
                    dumps(candidate.aliases),
                    candidate.summary,
                    now,
                    now,
                ),
            )
            return entity_id

    def create_relation(
        self,
        *,
        profile_id: str,
        source_id: str,
        target_id: str,
        relation_type: str,
        weight: float,
        confidence: float,
        episode_id: str,
        valid_from: str | None = None,
        valid_to: str | None = None,
    ) -> str:
        relation_id = f"relation_{uuid4().hex}"
        now = now_iso()

        with self._lock, self._connection:
            existing = self._connection.execute(
                """
                select id
                from memory_relations
                where profile_id = ?
                  and source_id = ?
                  and target_id = ?
                  and relation_type = ?
                  and state = 'active'
                limit 1
                """,
                (profile_id, source_id, target_id, relation_type),
            ).fetchone()

            if existing:
                return existing["id"]

            self._connection.execute(
                """
                insert into memory_relations (
                  id, profile_id, source_id, target_id, relation_type, state,
                  weight, confidence, valid_from, valid_to, episode_id, created_at, updated_at
                )
                values (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    relation_id,
                    profile_id,
                    source_id,
                    target_id,
                    relation_type,
                    weight,
                    confidence,
                    valid_from,
                    valid_to,
                    episode_id,
                    now,
                    now,
                ),
            )

        return relation_id

    def get_memory(self, memory_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                "select * from memory_items where id = ?",
                (memory_id,),
            ).fetchone()

        if row is None:
            raise KeyError(memory_id)

        return memory_row_to_dict(row)

    def get_active_memories(self, memory_ids: list[str], profile_id: str) -> list[dict[str, Any]]:
        if not memory_ids:
            return []

        placeholders = ",".join("?" for _ in memory_ids)

        with self._lock:
            rows = self._connection.execute(
                f"""
                select *
                from memory_items
                where profile_id = ?
                  and state = 'active'
                  and id in ({placeholders})
                """,
                (profile_id, *memory_ids),
            ).fetchall()

        by_id = {row["id"]: memory_row_to_dict(row) for row in rows}
        return [by_id[memory_id] for memory_id in memory_ids if memory_id in by_id]

    def get_episodes(self, *, profile_id: str, episode_ids: list[str]) -> list[dict[str, Any]]:
        if not episode_ids:
            return []

        placeholders = ",".join("?" for _ in episode_ids)

        with self._lock:
            rows = self._connection.execute(
                f"""
                select *
                from memory_episodes
                where profile_id = ?
                  and id in ({placeholders})
                """,
                (profile_id, *episode_ids),
            ).fetchall()

        by_id = {row["id"]: episode_row_to_dict(row) for row in rows}
        return [by_id[episode_id] for episode_id in episode_ids if episode_id in by_id]

    def list_active_memories(self, profile_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                select *
                from memory_items
                where profile_id = ? and state = 'active'
                order by updated_at desc
                """,
                (profile_id,),
            ).fetchall()

        return [memory_row_to_dict(row) for row in rows]

    def search_fts(self, *, profile_id: str, query: str, limit: int) -> list[dict[str, Any]]:
        tokens = _lexical_terms(query)
        match_query = " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"*' for token in tokens)
        like_terms = tokens or [query]
        clauses = " or ".join("m.content like ?" for _ in like_terms)
        params = [profile_id, *(f"%{term}%" for term in like_terms)]

        with self._lock:
            rows = []
            if match_query:
                rows = self._connection.execute(
                    """
                    select m.*, bm25(memory_items_fts) as fts_score
                    from memory_items_fts
                    join memory_items m on m.id = memory_items_fts.memory_id
                    where memory_items_fts match ? and m.profile_id = ? and m.state = 'active'
                    order by fts_score asc, m.importance desc, m.updated_at desc
                    limit ?
                    """,
                    (match_query, profile_id, limit),
                ).fetchall()
            if not rows:
                # Unicode FTS tokenization may keep short CJK phrases in a larger token.  The
                # active-only LIKE fallback preserves prior behavior without replacing FTS as the
                # primary lexical path.
                rows = self._connection.execute(
                    f"""
                    select m.*, 0.55 as fts_score
                    from memory_items m
                    where m.profile_id = ? and m.state = 'active' and ({clauses})
                    order by m.importance desc, m.updated_at desc limit ?
                    """,
                    (*params, limit),
                ).fetchall()

        return [
            {
                "memory_id": row["id"],
                "score": row["fts_score"],
                "source": "fts",
            }
            for row in rows
        ]

    def search_entities(
        self,
        *,
        profile_id: str,
        names: list[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        if not names:
            return []

        memories = self.list_active_memories(profile_id)
        matches = []

        for memory in memories:
            metadata_entities = memory["metadata"].get("entities", [])
            content = memory["content"]

            if any(name in content or name in metadata_entities for name in names):
                matches.append(
                    {
                        "memory_id": memory["memory_id"],
                        "score": 0.72 + memory["importance"] * 0.2,
                        "source": "entity",
                    }
                )

        return matches[:limit]

    def list_relations_for_memories(
        self,
        *,
        profile_id: str,
        memory_ids: list[str],
        limit: int = 16,
    ) -> list[dict[str, Any]]:
        if not memory_ids:
            return []

        placeholders = ",".join("?" for _ in memory_ids)

        with self._lock:
            rows = self._connection.execute(
                f"""
                select *
                from memory_relations
                where profile_id = ?
                  and state = 'active'
                  and (source_id in ({placeholders}) or target_id in ({placeholders}))
                order by weight desc, confidence desc
                limit ?
                """,
                (profile_id, *memory_ids, *memory_ids, limit),
            ).fetchall()

        return [relation_row_to_dict(row) for row in rows]

    def get_due_memories(self, *, profile_id: str, now_iso_value: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                select *
                from memory_items
                where profile_id = ?
                  and state = 'active'
                  and memory_type in ('follow_up', 'event')
                  and valid_from is not null
                  and valid_from <= ?
                order by valid_from asc, importance desc
                """,
                (profile_id, now_iso_value),
            ).fetchall()

        return [memory_row_to_dict(row) for row in rows]

    def archive_memories(
        self,
        *,
        profile_id: str,
        memory_ids: list[str],
        reason: str | None,
    ) -> int:
        if not memory_ids:
            return 0

        placeholders = ",".join("?" for _ in memory_ids)
        now = now_iso()

        with self._lock, self._connection:
            cursor = self._connection.execute(
                f"""
                update memory_items
                set state = 'archived',
                    forget_reason = ?,
                    updated_at = ?
                where profile_id = ?
                  and state != 'archived'
                  and id in ({placeholders})
                """,
                (reason, now, profile_id, *memory_ids),
            )
            self._connection.executemany(
                "delete from memory_items_fts where memory_id = ?",
                [(memory_id,) for memory_id in memory_ids],
            )

        return cursor.rowcount

    def forget_with_outbox(
        self,
        *,
        profile_id: str,
        memory_ids: Sequence[str],
        episode_ids: Sequence[str],
        reason: str | None,
        now: float,
    ) -> dict[str, Any]:
        """Archive SQLite truth and enqueue idempotent vector deletes in one transaction."""

        if not memory_ids and not episode_ids:
            raise ValueError("forget requires memory_ids or episode_ids")
        requested = [str(value) for value in memory_ids if value]
        with self._lock:
            connection = self._get_connection()
            with connection:
                connection.execute("begin immediate")
                if episode_ids:
                    placeholders = ",".join("?" for _ in episode_ids)
                    rows = connection.execute(
                        f"""
                        select id from memory_items
                        where profile_id = ? and episode_id in ({placeholders})
                        """,
                        (profile_id, *episode_ids),
                    ).fetchall()
                    requested.extend(str(row["id"]) for row in rows)
                requested = list(dict.fromkeys(requested))
                if not requested:
                    return {"forgotten_count": 0, "memory_ids": []}
                placeholders = ",".join("?" for _ in requested)
                actual_rows = connection.execute(
                    f"""
                    select id from memory_items
                    where profile_id = ? and id in ({placeholders})
                    """,
                    (profile_id, *requested),
                ).fetchall()
                actual_ids = [str(row["id"]) for row in actual_rows]
                if not actual_ids:
                    return {"forgotten_count": 0, "memory_ids": []}
                actual_placeholders = ",".join("?" for _ in actual_ids)
                timestamp = now_iso()
                cursor = connection.execute(
                    f"""
                    update memory_items
                    set state = 'archived', forget_reason = ?, updated_at = ?
                    where profile_id = ? and state != 'archived'
                      and id in ({actual_placeholders})
                    """,
                    (reason, timestamp, profile_id, *actual_ids),
                )
                connection.execute(
                    f"""
                    update memory_vector_outbox
                    set state = 'completed', last_error = 'superseded_by_forget',
                        lease_owner = null, lease_expires_at = null,
                        updated_at = ?, completed_at = ?
                    where profile_id = ? and operation = 'upsert'
                      and state != 'completed' and memory_id in ({actual_placeholders})
                    """,
                    (timestamp, timestamp, profile_id, *actual_ids),
                )
                connection.executemany(
                    "delete from memory_items_fts where memory_id = ?",
                    ((memory_id,) for memory_id in actual_ids),
                )
                connection.executemany(
                    """
                    insert into memory_vector_outbox (
                      id, profile_id, memory_id, operation, state, payload_json,
                      attempt_count, next_attempt_at, last_error, lease_owner,
                      lease_expires_at, created_at, updated_at, completed_at
                    ) values (?, ?, ?, 'delete', 'pending', '{}', 0, ?, null, null, null, ?, ?, null)
                    on conflict(id) do update set
                      state = 'pending', last_error = null, lease_owner = null,
                      lease_expires_at = null, completed_at = null,
                      next_attempt_at = min(memory_vector_outbox.next_attempt_at, excluded.next_attempt_at),
                      updated_at = excluded.updated_at
                    """,
                    (
                        (f"outbox_delete_{memory_id}", profile_id, memory_id, float(now), timestamp, timestamp)
                        for memory_id in actual_ids
                    ),
                )
        return {"forgotten_count": cursor.rowcount, "memory_ids": actual_ids}

    def memory_ids_for_episodes(self, *, profile_id: str, episode_ids: list[str]) -> list[str]:
        if not episode_ids:
            return []

        placeholders = ",".join("?" for _ in episode_ids)

        with self._lock:
            rows = self._connection.execute(
                f"""
                select id
                from memory_items
                where profile_id = ?
                  and state = 'active'
                  and episode_id in ({placeholders})
                """,
                (profile_id, *episode_ids),
            ).fetchall()

        return [row["id"] for row in rows]

    def mark_memories_stale(self, *, profile_id: str, memory_ids: list[str]) -> int:
        if not memory_ids:
            return 0

        placeholders = ",".join("?" for _ in memory_ids)
        now = now_iso()

        with self._lock, self._connection:
            cursor = self._connection.execute(
                f"""
                update memory_items
                set state = 'stale',
                    updated_at = ?
                where profile_id = ? and id in ({placeholders})
                """,
                (now, profile_id, *memory_ids),
            )
            self._connection.executemany(
                "delete from memory_items_fts where memory_id = ?",
                [(memory_id,) for memory_id in memory_ids],
            )

        return cursor.rowcount

    def mark_memories_superseded(self, *, profile_id: str, memory_ids: list[str]) -> int:
        if not memory_ids:
            return 0

        placeholders = ",".join("?" for _ in memory_ids)
        now = now_iso()

        with self._lock, self._connection:
            cursor = self._connection.execute(
                f"""
                update memory_items
                set state = 'superseded',
                    updated_at = ?
                where profile_id = ? and id in ({placeholders})
                """,
                (now, profile_id, *memory_ids),
            )
            self._connection.executemany(
                "delete from memory_items_fts where memory_id = ?",
                [(memory_id,) for memory_id in memory_ids],
            )

        return cursor.rowcount

    def write_audit_log(
        self,
        *,
        profile_id: str,
        action: str,
        input_summary: str,
        result_json: dict[str, Any],
        error: str | None = None,
    ) -> str:
        audit_id = f"audit_{uuid4().hex}"

        with self._lock, self._connection:
            self._connection.execute(
                """
                insert into memory_audit_logs (
                  id, profile_id, action, input_summary, result_json, error, created_at
                )
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audit_id,
                    profile_id,
                    action,
                    input_summary[:500],
                    dumps(result_json),
                    error,
                    now_iso(),
                ),
            )

        return audit_id

    def debug_snapshot(self, profile_id: str) -> dict[str, Any]:
        with self._lock:
            episodes = self._connection.execute(
                "select * from memory_episodes where profile_id = ? order by created_at desc limit 50",
                (profile_id,),
            ).fetchall()
            memories = self._connection.execute(
                "select * from memory_items where profile_id = ? order by updated_at desc limit 100",
                (profile_id,),
            ).fetchall()
            entities = self._connection.execute(
                "select * from memory_entities where profile_id = ? order by updated_at desc limit 100",
                (profile_id,),
            ).fetchall()
            relations = self._connection.execute(
                "select * from memory_relations where profile_id = ? order by updated_at desc limit 100",
                (profile_id,),
            ).fetchall()
            audit_logs = self._connection.execute(
                "select * from memory_audit_logs where profile_id = ? order by created_at desc limit 50",
                (profile_id,),
            ).fetchall()

        return {
            "episodes": [episode_row_to_dict(row) for row in episodes],
            "memories": [memory_row_to_dict(row) for row in memories],
            "entities": [entity_row_to_dict(row) for row in entities],
            "relations": [relation_row_to_dict(row) for row in relations],
            "audit_logs": [audit_row_to_dict(row) for row in audit_logs],
        }

    def health(self) -> dict[str, Any]:
        with self._lock:
            memories = self._connection.execute("select count(*) from memory_items").fetchone()[0]
            episodes = self._connection.execute("select count(*) from memory_episodes").fetchone()[0]
            entities = self._connection.execute("select count(*) from memory_entities").fetchone()[0]
            relations = self._connection.execute("select count(*) from memory_relations").fetchone()[0]

        return {
            "backend": "sqlite",
            "path": str(self.path),
            "schema_version": self.schema_version(),
            "episodes": episodes,
            "memories": memories,
            "entities": entities,
            "relations": relations,
        }

    def _migrate(self) -> None:
        with self._lock:
            version = self._read_declared_schema_version()
            self._validate_version_shape(version)
            for target in range(version + 1, SCHEMA_VERSION + 1):
                self._connection.execute("begin immediate")
                try:
                    getattr(self, f"_migrate_to_{target}")()
                    self._validate_version_shape(target)
                    self._connection.execute(
                        """
                        insert into memory_schema_info(key, value, updated_at)
                        values ('schema_version', ?, ?)
                        on conflict(key) do update set
                          value = excluded.value, updated_at = excluded.updated_at
                        """,
                        (str(target), now_iso()),
                    )
                    self._connection.commit()
                except BaseException:
                    self._connection.rollback()
                    raise

    def _read_declared_schema_version(self) -> int:
        if not self._table_exists("memory_schema_info"):
            return 0
        required = {"key", "value", "updated_at"}
        if not required <= set(self.table_columns("memory_schema_info")):
            raise RuntimeError("Memory schema version table is malformed")
        rows = self._connection.execute(
            "select value from memory_schema_info where key = 'schema_version'"
        ).fetchall()
        if len(rows) != 1 or not re.fullmatch(r"0|[1-9][0-9]*", str(rows[0]["value"])):
            raise RuntimeError("Memory schema version is malformed")
        version = int(rows[0]["value"])
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"Memory schema version {version} is newer than supported {SCHEMA_VERSION}"
            )
        return version

    def _migrate_to_1(self) -> None:
        for statement in (
            """
            create table if not exists memory_schema_info (
              key text primary key, value text not null, updated_at text not null
            )
            """,
            """
            create table if not exists memory_episodes (
              id text primary key, profile_id text not null, source_type text not null,
              source_id text, role text, content text not null,
              metadata_json text not null default '{}', created_at text not null
            )
            """,
            """
            create table if not exists memory_items (
              id text primary key, profile_id text not null,
              episode_id text not null references memory_episodes(id) on delete cascade,
              memory_type text not null,
              state text not null check (state in ('active', 'archived', 'stale', 'superseded')),
              content text not null,
              importance real not null check (importance >= 0 and importance <= 1),
              confidence real not null default 0.7 check (confidence >= 0 and confidence <= 1),
              valid_from text, valid_to text, metadata_json text not null default '{}',
              forget_reason text, created_at text not null, updated_at text not null
            )
            """,
        ):
            self._connection.execute(statement)

    def _migrate_to_2(self) -> None:
        self._ensure_legacy_episode_columns()
        self._ensure_memory_items_shape()
        for statement in (
                """
                create index if not exists idx_memory_episodes_profile_created
                  on memory_episodes(profile_id, created_at)
                """,
                """
                create index if not exists idx_memory_items_profile_state_type
                  on memory_items(profile_id, state, memory_type)
                """,
                """
                create table if not exists memory_entities (
                  id text primary key,
                  profile_id text not null,
                  name text not null,
                  entity_type text not null,
                  aliases_json text not null default '[]',
                  summary text not null default '',
                  state text not null check (state in ('active', 'archived')) default 'active',
                  created_at text not null,
                  updated_at text not null,
                  unique(profile_id, name)
                )
                """,
                """
                create table if not exists memory_relations (
                  id text primary key,
                  profile_id text not null,
                  source_id text not null,
                  target_id text not null,
                  relation_type text not null,
                  state text not null check (state in ('active', 'archived', 'stale')) default 'active',
                  weight real not null check (weight >= 0 and weight <= 1),
                  confidence real not null check (confidence >= 0 and confidence <= 1),
                  valid_from text,
                  valid_to text,
                  episode_id text references memory_episodes(id) on delete set null,
                  created_at text not null,
                  updated_at text not null,
                  unique(profile_id, source_id, target_id, relation_type)
                )
                """,
                """
                create table if not exists memory_audit_logs (
                  id text primary key,
                  profile_id text not null,
                  action text not null,
                  input_summary text not null,
                  result_json text not null default '{}',
                  error text,
                  created_at text not null
                )
                """,
                """
                create virtual table if not exists memory_items_fts
                  using fts5(memory_id unindexed, content)
                """,
        ):
            self._connection.execute(statement)
        self._connection.execute("delete from memory_items_fts")
        self._connection.execute(
            """
            insert into memory_items_fts(memory_id, content)
            select id, content from memory_items where state = 'active'
            """
        )

    def _migrate_to_3(self) -> None:
        for statement in (
            """
                create table if not exists memory_extraction_jobs (
                  id text primary key,
                  profile_id text not null,
                  episode_id text not null references memory_episodes(id) on delete cascade,
                  state text not null check (state in ('pending', 'processing', 'completed', 'failed')),
                  payload_json text not null,
                  result_json text not null default '{}',
                  attempt_count integer not null default 0 check (attempt_count >= 0),
                  next_attempt_at real not null,
                  last_error text,
                  lease_owner text,
                  lease_expires_at real,
                  created_at text not null,
                  updated_at text not null,
                  completed_at text,
                  check (
                    (state = 'processing' and lease_owner is not null and lease_expires_at is not null)
                    or (state != 'processing' and lease_owner is null and lease_expires_at is null)
                  )
                )
            """,
            """
                create index if not exists idx_memory_extraction_claim
                  on memory_extraction_jobs(state, next_attempt_at, created_at)
            """,
            """
                create index if not exists idx_memory_extraction_lease
                  on memory_extraction_jobs(state, lease_expires_at)
            """,
            """
                create table if not exists memory_vector_outbox (
                  id text primary key,
                  profile_id text not null,
                  memory_id text not null,
                  operation text not null check (operation in ('upsert', 'delete')),
                  state text not null check (state in ('pending', 'processing', 'completed', 'failed')),
                  payload_json text not null default '{}',
                  attempt_count integer not null default 0 check (attempt_count >= 0),
                  next_attempt_at real not null,
                  last_error text,
                  lease_owner text,
                  lease_expires_at real,
                  created_at text not null,
                  updated_at text not null,
                  completed_at text,
                  check (
                    (state = 'processing' and lease_owner is not null and lease_expires_at is not null)
                    or (state != 'processing' and lease_owner is null and lease_expires_at is null)
                  )
                )
            """,
            """
                create index if not exists idx_memory_outbox_claim
                  on memory_vector_outbox(state, next_attempt_at, created_at)
            """,
            """
                create index if not exists idx_memory_outbox_lease
                  on memory_vector_outbox(state, lease_expires_at)
            """,
        ):
            self._connection.execute(statement)

    def _migrate_to_4(self) -> None:
        self._connection.execute(
            """
            create table memory_pending_episodes (
              profile_id text primary key,
              generation integer not null check (generation >= 1),
              last_confirmed_at real not null,
              segments_json text not null,
              updated_at text not null
            )
            """
        )

    def _validate_version_shape(self, version: int) -> None:
        if version == 0:
            return
        required: dict[str, set[str]] = {
            "memory_schema_info": {"key", "value", "updated_at"},
            "memory_episodes": {"id", "profile_id", "content", "created_at"},
            "memory_items": {
                "id", "profile_id", "episode_id", "memory_type", "state", "content",
                "importance", "metadata_json", "created_at", "updated_at",
            },
        }
        if version >= 2:
            required.update({
                "memory_entities": {"id", "profile_id", "name", "state"},
                "memory_relations": {"id", "profile_id", "source_id", "target_id", "state"},
                "memory_audit_logs": {"id", "profile_id", "action", "created_at"},
                "memory_items_fts": {"memory_id", "content"},
            })
            required["memory_episodes"].add("role")
            required["memory_items"].update(
                {"confidence", "valid_from", "valid_to", "forget_reason"}
            )
        if version >= 3:
            work_columns = {
                "id", "profile_id", "state", "attempt_count", "next_attempt_at",
                "lease_owner", "lease_expires_at", "created_at", "updated_at",
            }
            required["memory_extraction_jobs"] = work_columns | {"episode_id", "payload_json"}
            required["memory_vector_outbox"] = work_columns | {
                "memory_id", "operation", "payload_json",
            }
        if version >= 4:
            required["memory_pending_episodes"] = {
                "profile_id", "generation", "last_confirmed_at", "segments_json",
                "updated_at",
            }
        for table, columns in required.items():
            if not self._table_exists(table) or not columns <= set(self.table_columns(table)):
                raise RuntimeError(f"Memory schema version {version} has incompatible {table}")

    def _ensure_legacy_episode_columns(self) -> None:
        if not self._table_exists("memory_episodes"):
            return

        if "role" not in self.table_columns("memory_episodes"):
            self._connection.execute("alter table memory_episodes add column role text")

    def _ensure_memory_items_shape(self) -> None:
        if not self._table_exists("memory_items"):
            return

        columns = set(self.table_columns("memory_items"))
        required = {
            "id",
            "profile_id",
            "episode_id",
            "memory_type",
            "state",
            "content",
            "importance",
            "confidence",
            "valid_from",
            "valid_to",
            "metadata_json",
            "forget_reason",
            "created_at",
            "updated_at",
        }
        create_sql = self._table_create_sql("memory_items")

        if required <= columns and all(state in create_sql for state in ACTIVE_MEMORY_STATES):
            return

        legacy_table = "memory_items_legacy_migration"
        self._connection.execute("drop table if exists memory_items_fts")
        self._connection.execute(f"alter table memory_items rename to {legacy_table}")
        self._connection.execute(
            """
            create table memory_items (
              id text primary key,
              profile_id text not null,
              episode_id text not null references memory_episodes(id) on delete cascade,
              memory_type text not null,
              state text not null check (state in ('active', 'archived', 'stale', 'superseded')),
              content text not null,
              importance real not null check (importance >= 0 and importance <= 1),
              confidence real not null default 0.7 check (confidence >= 0 and confidence <= 1),
              valid_from text,
              valid_to text,
              metadata_json text not null default '{}',
              forget_reason text,
              created_at text not null,
              updated_at text not null
            )
            """
        )

        old_columns = set(self.table_columns(legacy_table))
        expression_by_column = {
            "id": "id",
            "profile_id": "profile_id",
            "episode_id": "episode_id",
            "memory_type": "memory_type",
            "state": "case when state in ('active', 'archived', 'stale', 'superseded') then state else 'active' end",
            "content": "content",
            "importance": "min(max(coalesce(importance, 0.5), 0), 1)",
            "confidence": "min(max(coalesce(confidence, 0.7), 0), 1)" if "confidence" in old_columns else "0.7",
            "valid_from": "valid_from" if "valid_from" in old_columns else "null",
            "valid_to": "valid_to" if "valid_to" in old_columns else "null",
            "metadata_json": "coalesce(metadata_json, '{}')" if "metadata_json" in old_columns else "'{}'",
            "forget_reason": "forget_reason" if "forget_reason" in old_columns else "null",
            "created_at": "coalesce(created_at, '')",
            "updated_at": "coalesce(updated_at, created_at, '')",
        }
        ordered_columns = list(expression_by_column)
        expressions = ", ".join(expression_by_column[column] for column in ordered_columns)

        self._connection.execute(
            f"""
            insert into memory_items ({", ".join(ordered_columns)})
            select {expressions}
            from {legacy_table}
            """
        )
        self._connection.execute(f"drop table {legacy_table}")

    def _table_exists(self, table_name: str) -> bool:
        row = self._connection.execute(
            "select 1 from sqlite_master where type in ('table', 'virtual table') and name = ?",
            (table_name,),
        ).fetchone()
        return row is not None

    def _table_create_sql(self, table_name: str) -> str:
        row = self._connection.execute(
            "select sql from sqlite_master where type = 'table' and name = ?",
            (table_name,),
        ).fetchone()
        return row["sql"] if row and row["sql"] else ""


def _lexical_terms(query: str) -> list[str]:
    """Bound mixed-language FTS/LIKE terms without adding a tokenizer dependency."""

    normalized = re.sub(r"[，。！？、；,.!?;]+", " ", str(query)).strip()
    words = [word for word in normalized.split() if word]
    terms = list(words)
    for run in re.findall(r"[\u3400-\u9fff]{2,}", normalized):
        for width in (4, 3, 2):
            terms.extend(run[index:index + width] for index in range(len(run) - width + 1))
    return list(dict.fromkeys(terms))[:24]


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def loads(value: str | None, default: Any) -> Any:
    if not value:
        return default

    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _frozen_segments(segments: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Validate and copy the only fields allowed in a durable pending/sealed episode."""

    result: list[dict[str, str]] = []
    for segment in segments:
        if not isinstance(segment, Mapping):
            raise RuntimeError("pending Memory episode payload is malformed")
        segment_id = str(segment.get("segment_id", "")).strip()
        content = str(segment.get("content", "")).strip()
        if not segment_id or not content:
            raise ValueError("sealed Memory segments require segment_id and content")
        result.append({"segment_id": segment_id, "content": content})
    if not result:
        raise ValueError("cannot seal an empty Memory episode")
    return result


def _pending_episode_dict(row: sqlite3.Row) -> dict[str, Any]:
    segments = _frozen_segments(loads(row["segments_json"], []))
    return {
        "profile_id": row["profile_id"],
        "generation": int(row["generation"]),
        "last_confirmed_at": float(row["last_confirmed_at"]),
        "segments": tuple(segments),
    }


def _insert_episode_job(
    connection: sqlite3.Connection,
    *,
    profile_id: str,
    segments: Sequence[Mapping[str, Any]],
    now: float,
) -> dict[str, str]:
    """Insert one immutable episode and extraction job inside the caller's transaction."""

    frozen = _frozen_segments(segments)
    episode_id = f"episode_{uuid4().hex}"
    job_id = f"memory_job_{uuid4().hex}"
    created_at = now_iso()
    source_ids = [item["segment_id"] for item in frozen]
    payload = {
        "episode_id": episode_id,
        "segments": frozen,
        "source_segment_ids": source_ids,
    }
    connection.execute(
        """
        insert into memory_episodes (
          id, profile_id, source_type, source_id, role, content, metadata_json, created_at
        ) values (?, ?, 'main_agent_episode', ?, 'user', ?, ?, ?)
        """,
        (
            episode_id, profile_id, frozen[0]["segment_id"],
            "\n".join(item["content"] for item in frozen),
            dumps({"source_segment_ids": source_ids}), created_at,
        ),
    )
    connection.execute(
        """
        insert into memory_extraction_jobs (
          id, profile_id, episode_id, state, payload_json, result_json,
          attempt_count, next_attempt_at, last_error, lease_owner,
          lease_expires_at, created_at, updated_at, completed_at
        ) values (?, ?, ?, 'pending', ?, '{}', 0, ?, null, null, null, ?, ?, null)
        """,
        (
            job_id, profile_id, episode_id, dumps(payload), float(now),
            created_at, created_at,
        ),
    )
    return {"episode_id": episode_id, "job_id": job_id}


def episode_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "episode_id": row["id"],
        "profile_id": row["profile_id"],
        "source_type": row["source_type"],
        "source_id": row["source_id"],
        "role": row["role"],
        "content": row["content"],
        "metadata": loads(row["metadata_json"], {}),
        "created_at": row["created_at"],
    }


def memory_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "memory_id": row["id"],
        "profile_id": row["profile_id"],
        "episode_id": row["episode_id"],
        "memory_type": row["memory_type"],
        "state": row["state"],
        "content": row["content"],
        "importance": row["importance"],
        "confidence": row["confidence"],
        "valid_from": row["valid_from"],
        "valid_to": row["valid_to"],
        "metadata": loads(row["metadata_json"], {}),
        "forget_reason": row["forget_reason"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def entity_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "entity_id": row["id"],
        "profile_id": row["profile_id"],
        "name": row["name"],
        "entity_type": row["entity_type"],
        "aliases": loads(row["aliases_json"], []),
        "summary": row["summary"],
        "state": row["state"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def relation_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "relation_id": row["id"],
        "profile_id": row["profile_id"],
        "source_id": row["source_id"],
        "target_id": row["target_id"],
        "relation_type": row["relation_type"],
        "state": row["state"],
        "weight": row["weight"],
        "confidence": row["confidence"],
        "valid_from": row["valid_from"],
        "valid_to": row["valid_to"],
        "episode_id": row["episode_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def audit_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "audit_id": row["id"],
        "profile_id": row["profile_id"],
        "action": row["action"],
        "input_summary": row["input_summary"],
        "result": loads(row["result_json"], {}),
        "error": row["error"],
        "created_at": row["created_at"],
    }


def _job_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "job_id": row["id"],
        "profile_id": row["profile_id"],
        "episode_id": row["episode_id"],
        "state": row["state"],
        "payload": loads(row["payload_json"], {}),
        "result": loads(row["result_json"], {}),
        "attempt_count": row["attempt_count"],
        "next_attempt_at": row["next_attempt_at"],
        "last_error": row["last_error"],
        "lease_owner": row["lease_owner"],
        "lease_expires_at": row["lease_expires_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
    }


def _outbox_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "outbox_id": row["id"],
        "profile_id": row["profile_id"],
        "memory_id": row["memory_id"],
        "operation": row["operation"],
        "state": row["state"],
        "payload": loads(row["payload_json"], {}),
        "attempt_count": row["attempt_count"],
        "next_attempt_at": row["next_attempt_at"],
        "last_error": row["last_error"],
        "lease_owner": row["lease_owner"],
        "lease_expires_at": row["lease_expires_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
    }
