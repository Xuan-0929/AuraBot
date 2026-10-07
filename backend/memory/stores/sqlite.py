"""Persist one Profile's long-term Memory metadata, episodes, relations, audit log, and FTS index.

``MemoryService`` is the sole writer of this store. Main Agent conversation buffering remains in
``ContextStore``. Read-only benchmark connections neither migrate nor create files; this module intentionally contains no pending-conversation table, background job,
lease, worker, retry queue, or vector outbox.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..types import EntityCandidate, MemoryCandidate


SCHEMA_VERSION = 3
ACTIVE_MEMORY_STATES = {"active", "archived", "stale", "superseded"}


class SQLiteMemoryStore:
    def __init__(self, path: str | Path, *, read_only: bool = False):
        self.path = Path(path)
        if read_only:
            self._connection = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, check_same_thread=False)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        if not read_only:
            self._migrate()

    def close(self) -> None:
        self._connection.close()

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

    def persist_conversation(
        self,
        *,
        profile_id: str,
        source_type: str,
        source_id: str,
        episode_id: str,
        content: str,
        summary: str,
        metadata: dict[str, Any],
        memories: Sequence[tuple[str, MemoryCandidate]],
        entities: Sequence[EntityCandidate],
        relations: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Atomically persist one stable conversation and every SQLite-owned derived fact."""

        now = now_iso()
        with self._lock:
            self._connection.execute("begin immediate")
            try:
                existing = self._connection.execute(
                    """
                    select * from memory_episodes
                    where profile_id = ? and source_type = ? and source_id = ?
                    limit 1
                    """,
                    (profile_id, source_type, source_id),
                ).fetchone()
                if existing is not None and existing["summary"]:
                    rows = self._connection.execute(
                        """
                        select id from memory_items
                        where profile_id = ? and episode_id = ? and state = 'active'
                        order by rowid
                        """,
                        (profile_id, existing["id"]),
                    ).fetchall()
                    result = {
                        "episode_id": existing["id"],
                        "memory_ids": [row["id"] for row in rows],
                        "summary": existing["summary"],
                    }
                else:
                    if existing is None:
                        self._connection.execute(
                            """
                            insert into memory_episodes (
                              id, profile_id, source_type, source_id, role, content,
                              summary, metadata_json, created_at
                            ) values (?, ?, ?, ?, null, ?, ?, ?, ?)
                            """,
                            (
                                episode_id, profile_id, source_type, source_id, content,
                                summary, dumps(metadata), now,
                            ),
                        )
                    else:
                        episode_id = existing["id"]
                        partial_rows = self._connection.execute(
                            "select id from memory_items where episode_id = ?",
                            (episode_id,),
                        ).fetchall()
                        partial_ids = [row["id"] for row in partial_rows]
                        if partial_ids:
                            placeholders = ",".join("?" for _ in partial_ids)
                            self._connection.execute(
                                f"delete from memory_relations where episode_id = ? "
                                f"or source_id in ({placeholders}) or target_id in ({placeholders})",
                                (episode_id, *partial_ids, *partial_ids),
                            )
                            self._connection.executemany(
                                "delete from memory_items_fts where memory_id = ?",
                                [(memory_id,) for memory_id in partial_ids],
                            )
                            self._connection.execute(
                                f"delete from memory_items where id in ({placeholders})",
                                tuple(partial_ids),
                            )
                        self._connection.execute(
                            """
                            update memory_episodes
                            set content = ?, summary = ?, metadata_json = ?
                            where id = ?
                            """,
                            (content, summary, dumps(metadata), episode_id),
                        )
                    for entity in entities:
                        row = self._connection.execute(
                            """
                            select id, aliases_json from memory_entities
                            where profile_id = ? and name = ? and state = 'active'
                            limit 1
                            """,
                            (profile_id, entity.name),
                        ).fetchone()
                        if row is not None:
                            aliases = sorted(set(loads(row["aliases_json"], []) + entity.aliases))
                            self._connection.execute(
                                """
                                update memory_entities
                                set aliases_json = ?, summary = coalesce(nullif(?, ''), summary),
                                    updated_at = ?
                                where id = ?
                                """,
                                (dumps(aliases), entity.summary, now, row["id"]),
                            )
                        else:
                            self._connection.execute(
                                """
                                insert into memory_entities (
                                  id, profile_id, name, entity_type, aliases_json, summary,
                                  state, created_at, updated_at
                                ) values (?, ?, ?, ?, ?, ?, 'active', ?, ?)
                                """,
                                (
                                    f"entity_{uuid4().hex}", profile_id, entity.name,
                                    entity.entity_type, dumps(entity.aliases), entity.summary, now, now,
                                ),
                            )
                    for memory_id, candidate in memories:
                        self._connection.execute(
                            """
                            insert into memory_items (
                              id, profile_id, episode_id, memory_type, state, content,
                              importance, confidence, valid_from, valid_to, metadata_json,
                              forget_reason, created_at, updated_at
                            ) values (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, null, ?, ?)
                            """,
                            (
                                memory_id, profile_id, episode_id, candidate.memory_type,
                                candidate.content, candidate.importance, candidate.confidence,
                                candidate.valid_from, candidate.valid_to,
                                dumps({**candidate.metadata, "entities": candidate.entities}),
                                now, now,
                            ),
                        )
                        self._connection.execute(
                            "insert into memory_items_fts(memory_id, content) values (?, ?)",
                            (memory_id, candidate.content),
                        )
                    for relation in relations:
                        self._connection.execute(
                            """
                            insert or ignore into memory_relations (
                              id, profile_id, source_id, target_id, relation_type, state,
                              weight, confidence, valid_from, valid_to, episode_id,
                              created_at, updated_at
                            ) values (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                relation["relation_id"], profile_id, relation["source_id"],
                                relation["target_id"], relation["relation_type"],
                                relation["weight"], relation["confidence"],
                                relation.get("valid_from"), relation.get("valid_to"),
                                episode_id, now, now,
                            ),
                        )
                    result = {
                        "episode_id": episode_id,
                        "memory_ids": [memory_id for memory_id, _candidate in memories],
                        "summary": summary,
                    }
            except BaseException:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()
                return result

    def entity_ids_by_names(self, *, profile_id: str, names: Sequence[str]) -> dict[str, str]:
        """Resolve active entity identifiers for vector payload reconstruction."""

        unique_names = list(dict.fromkeys(name for name in names if name))
        if not unique_names:
            return {}
        placeholders = ",".join("?" for _ in unique_names)
        with self._lock:
            rows = self._connection.execute(
                f"""
                select id, name from memory_entities
                where profile_id = ? and state = 'active' and name in ({placeholders})
                """,
                (profile_id, *unique_names),
            ).fetchall()
        return {row["name"]: row["id"] for row in rows}

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

    def get_episode_by_source(
        self, *, profile_id: str, source_type: str, source_id: str,
    ) -> dict[str, Any] | None:
        """Return the existing episode for one idempotent conversation-ingestion source."""

        with self._lock:
            row = self._connection.execute(
                """
                select * from memory_episodes
                where profile_id = ? and source_type = ? and source_id = ?
                order by created_at asc, id asc limit 1
                """,
                (profile_id, source_type, source_id),
            ).fetchone()
        return None if row is None else episode_row_to_dict(row)

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
        tokens = [token for token in query.replace("，", " ").replace("。", " ").split() if token]
        like_terms = tokens or [query]
        clauses = " or ".join("m.content like ?" for _ in like_terms)
        params = [profile_id, *(f"%{term}%" for term in like_terms)]

        with self._lock:
            rows = self._connection.execute(
                f"""
                select m.*, 0.55 as fts_score
                from memory_items m
                where m.profile_id = ?
                  and m.state = 'active'
                  and ({clauses})
                order by m.importance desc, m.updated_at desc
                limit ?
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
                order by rowid
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
        with self._lock, self._connection:
            self._connection.execute(
                """
                create table if not exists memory_schema_info (
                  key text primary key,
                  value text not null,
                  updated_at text not null
                )
                """
            )
            self._ensure_legacy_episode_columns()
            self._ensure_memory_items_shape()
            self._connection.executescript(
                """
                create table if not exists memory_episodes (
                  id text primary key,
                  profile_id text not null,
                  source_type text not null,
                  source_id text,
                  role text,
                  content text not null,
                  summary text not null default '',
                  metadata_json text not null default '{}',
                  created_at text not null
                );

                create index if not exists idx_memory_episodes_profile_created
                  on memory_episodes(profile_id, created_at);

                create unique index if not exists idx_memory_episodes_stable_source
                  on memory_episodes(profile_id, source_type, source_id)
                  where source_id is not null
                    and source_type = 'main_agent_conversation';

                create table if not exists memory_items (
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
                );

                create index if not exists idx_memory_items_profile_state_type
                  on memory_items(profile_id, state, memory_type);

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
                );

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
                );

                create table if not exists memory_audit_logs (
                  id text primary key,
                  profile_id text not null,
                  action text not null,
                  input_summary text not null,
                  result_json text not null default '{}',
                  error text,
                  created_at text not null
                );

                create virtual table if not exists memory_items_fts
                  using fts5(memory_id unindexed, content);
                """
            )
            self._connection.execute("delete from memory_items_fts")
            self._connection.execute(
                """
                insert into memory_items_fts(memory_id, content)
                select id, content from memory_items where state = 'active'
                """
            )
            self._connection.execute(
                """
                insert into memory_schema_info(key, value, updated_at)
                values ('schema_version', ?, ?)
                on conflict(key) do update set value = excluded.value, updated_at = excluded.updated_at
                """,
                (str(SCHEMA_VERSION), now_iso()),
            )

    def _ensure_legacy_episode_columns(self) -> None:
        if not self._table_exists("memory_episodes"):
            return

        if "role" not in self.table_columns("memory_episodes"):
            self._connection.execute("alter table memory_episodes add column role text")
        if "summary" not in self.table_columns("memory_episodes"):
            self._connection.execute(
                "alter table memory_episodes add column summary text not null default ''"
            )

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
        self._connection.executescript(
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
            );
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


def episode_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "episode_id": row["id"],
        "profile_id": row["profile_id"],
        "source_type": row["source_type"],
        "source_id": row["source_id"],
        "role": row["role"],
        "content": row["content"],
        "summary": row["summary"],
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
