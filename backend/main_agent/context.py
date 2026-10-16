"""Persist one Profile's canonical Context, speech-delivery truth, and archive boundary.

The Runtime appends user, observation, think-note, Task, and acknowledged assistant facts here. Conversation
Task facts are upserted by stable identity/version in a dedicated latest-state table; conversation
turns are always reconstructed from canonical events; a singleton pending row stores only a stable sequence
boundary across retry/restart. A successful Memory result is committed atomically with its summary, boundary
cleanup, terminal Speech Ledger cleanup, pending deletion, and conditional idle-metadata removal.
Realtime may register an empty streaming clause before text arrives, then update it while nonterminal;
the complete renderer ACK remains the only path that promotes its final text into conversation history.
Pipeline also reads bounded delivery state, separating scheduled text from acknowledged history.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from threading import RLock
from typing import Any


class ClauseState(StrEnum):
    """Durable status of a short speech clause owned by the delivery ledger."""

    DRAFT = "draft"
    TTS_QUEUED = "tts_queued"
    PLAYING = "playing"
    DELIVERED = "delivered"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


_NONTERMINAL = frozenset({ClauseState.DRAFT, ClauseState.TTS_QUEUED, ClauseState.PLAYING})
_TRANSITIONS = {
    ClauseState.DRAFT: frozenset({ClauseState.TTS_QUEUED, ClauseState.INTERRUPTED, ClauseState.FAILED}),
    ClauseState.TTS_QUEUED: frozenset({ClauseState.PLAYING, ClauseState.INTERRUPTED, ClauseState.FAILED}),
    ClauseState.PLAYING: frozenset({ClauseState.INTERRUPTED, ClauseState.FAILED}),
}


@dataclass(frozen=True, slots=True)
class SpeechClause:
    """A delivery-ledger record; text becomes contextual only at DELIVERED."""

    clause_id: str
    text: str
    state: ClauseState
    sequence: int


@dataclass(frozen=True, slots=True)
class FoldedContext:
    """The latest prompt-relevant state folded deterministically from canonical event history."""

    conversation_turns: tuple[dict[str, Any], ...]
    observations: tuple[dict[str, Any], ...]
    think_note: str | None
    task_facts: tuple[dict[str, Any], ...]
    conversation_summaries: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class ConversationBatch:
    """One immutable, restart-stable slice of the unarchived conversation buffer."""

    source_id: str
    first_sequence: int
    through_sequence: int
    turns: tuple[dict[str, str], ...]


@dataclass(frozen=True, slots=True)
class PendingConversationArchive:
    """One persisted immutable canonical-event boundary being submitted to long-term Memory."""

    source_id: str
    first_sequence: int
    through_sequence: int


class ContextStore:
    """Small synchronous SQLite owner for one profile's canonical context and delivery ledger."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = RLock()
        with self._lock, self._connection:
            self._connection.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS context_events (
                  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                  event_type TEXT NOT NULL,
                  payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS context_task_facts (
                  id TEXT PRIMARY KEY,
                  version INTEGER,
                  payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS speech_clauses (
                  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                  clause_id TEXT NOT NULL UNIQUE,
                  text TEXT NOT NULL,
                  state TEXT NOT NULL CHECK(state IN (
                    'draft','tts_queued','playing','delivered','interrupted','failed'
                  ))
                );
                CREATE TABLE IF NOT EXISTS context_metadata (
                  key TEXT PRIMARY KEY,
                  value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pending_conversation_archive (
                  singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                  source_id TEXT NOT NULL,
                  first_sequence INTEGER NOT NULL,
                  through_sequence INTEGER NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_context_summary_source
                  ON context_events(json_extract(payload_json, '$.source_id'))
                  WHERE event_type = 'conversation_summary';
                """
            )

    def close(self) -> None:
        """Release the SQLite connection; later use is intentionally rejected by sqlite3."""

        with self._lock:
            self._connection.close()

    def append_event(self, event_type: str, payload: Mapping[str, Any]) -> int:
        """Append one JSON object fact and return its monotonic Context sequence."""

        if not isinstance(event_type, str) or not event_type:
            raise ValueError("event_type must be non-empty text")
        if event_type == "assistant_clause":
            raise ValueError("assistant_clause is reserved for complete renderer ACK")
        encoded = _json_object(payload)
        with self._transaction() as db:
            cursor = db.execute(
                "INSERT INTO context_events(event_type, payload_json) VALUES (?, ?)",
                (event_type, encoded),
            )
            return int(cursor.lastrowid)

    def upsert_task_fact(self, fact: Mapping[str, Any]) -> bool:
        """Persist only a changed/latest Task or proposal fact without folding conversation events."""

        identifier = fact["id"]
        version = fact.get("version")
        encoded = _json_object(fact)
        with self._transaction() as db:
            previous = db.execute(
                "SELECT version, payload_json FROM context_task_facts WHERE id = ?", (identifier,)
            ).fetchone()
            if previous is not None:
                if version is not None and previous["version"] is not None and previous["version"] >= version:
                    return False
                if previous["payload_json"] == encoded:
                    return False
            db.execute(
                "INSERT INTO context_task_facts(id, version, payload_json) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET version=excluded.version, payload_json=excluded.payload_json",
                (identifier, version, encoded),
            )
            return True

    def recover_interrupted_clauses(self) -> int:
        """Explicitly close clauses left nonterminal by a confirmed prior runtime shutdown."""

        with self._transaction() as db:
            cursor = db.execute(
                "UPDATE speech_clauses SET state = 'interrupted' WHERE state IN ('draft','tts_queued','playing')"
            )
            return cursor.rowcount

    def create_clause(self, clause_id: str, text: str) -> SpeechClause:
        """Register a not-yet-heard clause without adding any assistant context event."""

        if not isinstance(clause_id, str) or not clause_id or not isinstance(text, str) or not text:
            raise ValueError("clause id and text must be non-empty strings")
        with self._transaction() as db:
            cursor = db.execute(
                "INSERT INTO speech_clauses(clause_id, text, state) VALUES (?, ?, ?)",
                (clause_id, text, ClauseState.DRAFT.value),
            )
            return SpeechClause(clause_id, text, ClauseState.DRAFT, int(cursor.lastrowid))

    def get_clause(self, clause_id: str) -> SpeechClause:
        """Return one ledger entry by its renderer-stable identifier."""

        with self._lock:
            row = self._connection.execute(
                "SELECT sequence, clause_id, text, state FROM speech_clauses WHERE clause_id = ?", (clause_id,)
            ).fetchone()
        if row is None:
            raise KeyError(clause_id)
        return _clause_from_row(row)

    def create_streaming_clause(self, clause_id: str) -> None:
        if not isinstance(clause_id, str) or not clause_id:
            raise ValueError("clause id is required")
        with self._transaction() as db:
            db.execute("INSERT INTO speech_clauses(clause_id, text, state) VALUES (?, '', 'draft')", (clause_id,))

    def update_clause_text(self, clause_id: str, text: str) -> bool:
        if not isinstance(text, str) or len(text) > 8000:
            raise ValueError("streaming clause text is invalid")
        with self._transaction() as db:
            return bool(db.execute("UPDATE speech_clauses SET text = ? WHERE clause_id = ? AND state IN ('draft','tts_queued','playing')",
                                   (text, clause_id)).rowcount)

    def transition_clause(self, clause_id: str, state: ClauseState) -> SpeechClause:
        """Advance a clause through queue/play states or close it without creating context text."""

        target = ClauseState(state)
        with self._transaction() as db:
            row = _require_clause_row(db, clause_id)
            current = ClauseState(row["state"])
            if target not in _TRANSITIONS.get(current, frozenset()):
                raise ValueError(f"invalid clause transition: {current} -> {target}")
            db.execute("UPDATE speech_clauses SET state = ? WHERE clause_id = ?", (target.value, clause_id))
            return SpeechClause(clause_id, str(row["text"]), target, int(row["sequence"]))

    def ack_clause(self, clause_id: str, *, complete: bool, mode: str = "voice") -> bool:
        """Apply a renderer ACK; complete ACK is the sole atomic assistant-context write path."""

        with self._transaction() as db:
            row = _require_clause_row(db, clause_id)
            state = ClauseState(row["state"])
            if complete and state is ClauseState.DELIVERED:
                return True
            if state not in _NONTERMINAL:
                return False
            if complete:
                if state is not (ClauseState.DRAFT if mode == "text" else ClauseState.PLAYING):
                    raise ValueError("clause delivery state does not match ACK mode")
                db.execute("UPDATE speech_clauses SET state = 'delivered' WHERE clause_id = ?", (clause_id,))
                db.execute(
                    "INSERT INTO context_events(event_type, payload_json) VALUES ('assistant_clause', ?)",
                    (_json_object({"clause_id": clause_id, "text": row["text"]}),),
                )
                return True
            db.execute("UPDATE speech_clauses SET state = 'interrupted' WHERE clause_id = ?", (clause_id,))
            return False

    def fold(self) -> FoldedContext:
        """Fold prompt-relevant events using last-write-wins facts and stable identifier ordering."""

        with self._lock:
            rows = self._connection.execute(
                "SELECT sequence, event_type, payload_json FROM context_events ORDER BY sequence"
            ).fetchall()
            task_rows = self._connection.execute(
                "SELECT payload_json FROM context_task_facts ORDER BY id"
            ).fetchall()
        conversation: list[dict[str, Any]] = []
        segment_positions: dict[str, int] = {}
        observations: dict[str, tuple[int, dict[str, Any]]] = {}
        summaries: dict[str, tuple[int, dict[str, Any]]] = {}
        think_note: str | None = None
        for row in rows:
            payload = json.loads(row["payload_json"])
            event_type = row["event_type"]
            if event_type == "user_segment" and isinstance(payload.get("text"), str):
                segment_id = payload.get("segment_id")
                if not isinstance(segment_id, str):
                    continue
                turn = {"role": "user", "content": payload["text"], "segment_id": segment_id}
                position = segment_positions.get(segment_id)
                if position is None:
                    segment_positions[segment_id] = len(conversation)
                    conversation.append(turn)
                else:
                    conversation[position] = turn
            elif event_type == "assistant_clause" and isinstance(payload.get("text"), str):
                conversation.append({
                    "role": "assistant",
                    "content": payload["text"],
                    "clause_id": payload.get("clause_id", ""),
                })
            elif event_type == "conversation_summary":
                source_id = payload.get("source_id")
                content = payload.get("content")
                if isinstance(source_id, str) and isinstance(content, str):
                    summaries[source_id] = (int(row["sequence"]), {
                        "kind": "conversation_summary",
                        "source_id": source_id,
                        "content": content,
                    })
            elif event_type == "observation" and isinstance(payload.get("id"), str):
                previous = observations.get(payload["id"])
                merged = {**previous[1], **payload} if previous is not None else payload
                observations[payload["id"]] = (int(row["sequence"]), merged)
            elif event_type == "think_note" and isinstance(payload.get("text"), str):
                think_note = payload["text"]
        return FoldedContext(
            conversation_turns=tuple(conversation),
            observations=tuple(
                payload for _sequence, payload in sorted(observations.values(), key=lambda item: item[0])
            ),
            think_note=think_note,
            task_facts=tuple(json.loads(row["payload_json"]) for row in task_rows),
            conversation_summaries=tuple(
                payload for _sequence, payload in sorted(summaries.values(), key=lambda item: item[0])
            ),
        )

    def conversation_batch(self) -> ConversationBatch | None:
        """Return the current Memory-ready conversation with a precise event cleanup boundary."""

        with self._lock:
            rows = self._connection.execute(
                "SELECT sequence, event_type, payload_json FROM context_events "
                "WHERE event_type IN ('user_segment','assistant_clause') ORDER BY sequence"
            ).fetchall()
        return _conversation_batch_from_rows(rows)

    def freeze_conversation_archive(self) -> PendingConversationArchive | None:
        """Persist the current raw boundary once, returning an existing boundary unchanged."""

        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM pending_conversation_archive WHERE singleton = 1"
            ).fetchone()
            if row is not None:
                return _pending_archive_from_row(row)
            rows = db.execute(
                "SELECT sequence, event_type, payload_json FROM context_events "
                "WHERE event_type IN ('user_segment','assistant_clause') ORDER BY sequence"
            ).fetchall()
            batch = _conversation_batch_from_rows(rows)
            if batch is None:
                return None
            db.execute(
                "INSERT INTO pending_conversation_archive ("
                "singleton, source_id, first_sequence, through_sequence"
                ") VALUES (1, ?, ?, ?)",
                (batch.source_id, batch.first_sequence, batch.through_sequence),
            )
            return PendingConversationArchive(
                batch.source_id,
                batch.first_sequence,
                batch.through_sequence,
            )

    def pending_conversation_archive(self) -> PendingConversationArchive | None:
        """Load the frozen archive boundary without consulting later conversation events."""

        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM pending_conversation_archive WHERE singleton = 1"
            ).fetchone()
        return None if row is None else _pending_archive_from_row(row)

    def conversation_archive_turns(
        self, pending: PendingConversationArchive,
    ) -> tuple[dict[str, str], ...]:
        """Reconstruct and validate one pending boundary from canonical conversation events."""

        if not isinstance(pending, PendingConversationArchive):
            raise TypeError("pending must be a PendingConversationArchive")
        with self._lock:
            rows = self._connection.execute(
                "SELECT sequence, event_type, payload_json FROM context_events "
                "WHERE sequence BETWEEN ? AND ? "
                "AND event_type IN ('user_segment','assistant_clause') ORDER BY sequence",
                (pending.first_sequence, pending.through_sequence),
            ).fetchall()
        batch = _conversation_batch_from_rows(rows)
        if batch is None or (
            batch.source_id,
            batch.first_sequence,
            batch.through_sequence,
        ) != (
            pending.source_id,
            pending.first_sequence,
            pending.through_sequence,
        ):
            raise RuntimeError("pending conversation archive does not match canonical events")
        return batch.turns

    def commit_conversation_archive(
        self,
        batch: PendingConversationArchive,
        summary: str,
    ) -> None:
        """Atomically retain one pending summary, remove its raw boundary, and clear its marker."""

        if not isinstance(batch, PendingConversationArchive):
            raise TypeError("batch must be a PendingConversationArchive")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("conversation summary must be non-empty text")
        content = summary.strip()[:1_000]
        payload = _json_object({
            "kind": "conversation_summary",
            "source_id": batch.source_id,
            "content": content,
        })
        with self._transaction() as db:
            pending = db.execute(
                "SELECT * FROM pending_conversation_archive WHERE singleton = 1"
            ).fetchone()
            if pending is None:
                existing_summary = db.execute(
                    "SELECT 1 FROM context_events WHERE event_type = 'conversation_summary' "
                    "AND json_extract(payload_json, '$.source_id') = ? LIMIT 1",
                    (batch.source_id,),
                ).fetchone()
                if existing_summary is not None:
                    return
                raise RuntimeError("conversation archive is not pending")
            persisted = _pending_archive_from_row(pending)
            if persisted != batch:
                raise RuntimeError("conversation archive boundary does not match pending state")
            db.execute(
                "INSERT OR IGNORE INTO context_events(event_type, payload_json) "
                "VALUES ('conversation_summary', ?)",
                (payload,),
            )
            rows = db.execute(
                "SELECT payload_json FROM context_events "
                "WHERE sequence <= ? AND event_type = 'assistant_clause'",
                (batch.through_sequence,),
            ).fetchall()
            clause_ids = [
                item["clause_id"]
                for item in (json.loads(row["payload_json"]) for row in rows)
                if isinstance(item.get("clause_id"), str) and item["clause_id"]
            ]
            db.execute(
                "DELETE FROM context_events WHERE sequence <= ? "
                "AND event_type IN ('user_segment','assistant_clause')",
                (batch.through_sequence,),
            )
            if clause_ids:
                placeholders = ",".join("?" for _ in clause_ids)
                db.execute(
                    f"DELETE FROM speech_clauses WHERE clause_id IN ({placeholders}) "
                    "AND state IN ('delivered','interrupted','failed')",
                    tuple(clause_ids),
                )
            db.execute("DELETE FROM pending_conversation_archive WHERE singleton = 1")
            active = db.execute(
                "SELECT 1 FROM pending_conversation_archive WHERE singleton = 1 "
                "UNION ALL "
                "SELECT 1 FROM context_events "
                "WHERE event_type IN ('user_segment','assistant_clause') LIMIT 1"
            ).fetchone()
            if active is None:
                db.execute("DELETE FROM context_metadata WHERE key='last_user_activity'")

    def set_last_user_activity(self, timestamp: float) -> None:
        """Persist the wall-clock anchor used to resume the Profile's Memory idle deadline."""

        value = float(timestamp)
        if not math.isfinite(value) or value < 0:
            raise ValueError("last user activity must be a finite non-negative timestamp")
        with self._transaction() as db:
            db.execute(
                "INSERT INTO context_metadata(key,value) VALUES('last_user_activity',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (repr(value),),
            )

    def get_last_user_activity(self) -> float | None:
        """Load the persisted Memory idle anchor, if this Profile has an unarchived conversation."""

        with self._lock:
            row = self._connection.execute(
                "SELECT value FROM context_metadata WHERE key='last_user_activity'"
            ).fetchone()
        return None if row is None else float(row["value"])

    def clear_last_user_activity_if_idle(self) -> bool:
        """Atomically clear activity only when no raw or pending conversation exists."""

        with self._transaction() as db:
            active = db.execute(
                "SELECT 1 FROM pending_conversation_archive WHERE singleton = 1 "
                "UNION ALL "
                "SELECT 1 FROM context_events "
                "WHERE event_type IN ('user_segment','assistant_clause') LIMIT 1"
            ).fetchone()
            if active is not None:
                return False
            db.execute("DELETE FROM context_metadata WHERE key='last_user_activity'")
            return True

    def speech_delivery_state(self) -> dict[str, Any]:
        """Describe recent delivered and outstanding text without committing scheduled speech."""
        result: dict[str, Any] = {}
        with self._lock:
            for key, states, budget in (
                ("delivered", ("delivered",), 1000),
                ("scheduled", ("draft", "tts_queued", "playing"), 2000),
            ):
                placeholders = ",".join("?" for _ in states)
                rows = self._connection.execute(
                    f"SELECT text,state FROM speech_clauses WHERE state IN ({placeholders}) ORDER BY sequence DESC",
                    states,
                )
                items = []
                used = 0
                truncated = False
                for row in rows:
                    item = {"text": row["text"], "state": row["state"]}
                    size = len(json.dumps(item, ensure_ascii=False))
                    if used + size > budget:
                        truncated = True
                        break
                    items.append(item)
                    used += size
                result[key] = list(reversed(items))
                result[key + "_truncated"] = truncated
        return result

    def has_unsettled_clauses(self) -> bool:
        """Report whether physically audible assistant text can still change the conversation batch."""

        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM speech_clauses "
                "WHERE state IN ('draft','tts_queued','playing') LIMIT 1"
            ).fetchone()
        return row is not None

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
            except BaseException:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()


def _conversation_batch_from_rows(rows: list[sqlite3.Row]) -> ConversationBatch | None:
    if not rows:
        return None
    turns: list[dict[str, str]] = []
    segment_positions: dict[str, int] = {}
    for row in rows:
        payload = json.loads(row["payload_json"])
        if row["event_type"] == "user_segment":
            segment_id = payload.get("segment_id")
            text = payload.get("text")
            if not isinstance(segment_id, str) or not isinstance(text, str):
                continue
            turn = {"role": "user", "content": text}
            position = segment_positions.get(segment_id)
            if position is None:
                segment_positions[segment_id] = len(turns)
                turns.append(turn)
            else:
                turns[position] = turn
        else:
            text = payload.get("text")
            if isinstance(text, str):
                turns.append({"role": "assistant", "content": text})
    if not turns:
        return None
    first_sequence = int(rows[0]["sequence"])
    through_sequence = int(rows[-1]["sequence"])
    return ConversationBatch(
        source_id=f"main-agent-context:{first_sequence}:{through_sequence}",
        first_sequence=first_sequence,
        through_sequence=through_sequence,
        turns=tuple(turns),
    )


def _pending_archive_from_row(row: sqlite3.Row) -> PendingConversationArchive:
    return PendingConversationArchive(
        source_id=str(row["source_id"]),
        first_sequence=int(row["first_sequence"]),
        through_sequence=int(row["through_sequence"]),
    )


def _json_object(payload: Mapping[str, Any]) -> str:
    if not isinstance(payload, Mapping):
        raise ValueError("event payload must be a mapping")
    try:
        return json.dumps(
            _json_value(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("event payload must contain only JSON values and string keys") from exc


def _json_value(value: Any) -> Any:
    """Validate JSON boundary values before SQLite receives a canonical event payload."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON floats must be finite")
        return value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("JSON mapping keys must be strings")
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise ValueError("event payload contains a non-JSON value")


def _require_clause_row(db: sqlite3.Connection, clause_id: str) -> sqlite3.Row:
    row = db.execute(
        "SELECT sequence, clause_id, text, state FROM speech_clauses WHERE clause_id = ?", (clause_id,)
    ).fetchone()
    if row is None:
        raise KeyError(clause_id)
    return row


def _clause_from_row(row: sqlite3.Row) -> SpeechClause:
    return SpeechClause(str(row["clause_id"]), str(row["text"]), ClauseState(row["state"]), int(row["sequence"]))


__all__ = [
    "ClauseState", "ContextStore", "ConversationBatch", "FoldedContext",
    "PendingConversationArchive", "SpeechClause",
]
