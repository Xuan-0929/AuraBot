"""Persist one Profile's conversation buffer, observations, tasks, and renderer-delivery truth.

The runtime records stable utterance identities, observations, think notes and Task facts as append-only
events. Conversation batches expose only final user segments and physically delivered assistant clauses to
Memory; a successful Memory write clears exactly that batch boundary while preserving unrelated facts.
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


@dataclass(frozen=True, slots=True)
class ConversationBatch:
    """One immutable, restart-stable slice of the unarchived conversation buffer."""

    source_id: str
    first_sequence: int
    through_sequence: int
    turns: tuple[dict[str, str], ...]


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

    def ack_clause(self, clause_id: str, *, complete: bool) -> bool:
        """Apply a renderer ACK; complete ACK is the sole atomic assistant-context write path."""

        with self._transaction() as db:
            row = _require_clause_row(db, clause_id)
            state = ClauseState(row["state"])
            if complete and state is ClauseState.DELIVERED:
                return True
            if state not in _NONTERMINAL:
                return False
            if complete:
                if state is not ClauseState.PLAYING:
                    raise ValueError("only a playing clause can receive complete ACK")
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
        conversation: list[dict[str, Any]] = []
        segment_positions: dict[str, int] = {}
        observations: dict[str, tuple[int, dict[str, Any]]] = {}
        task_facts: dict[str, dict[str, Any]] = {}
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
            elif event_type == "observation" and isinstance(payload.get("id"), str):
                previous = observations.get(payload["id"])
                merged = {**previous[1], **payload} if previous is not None else payload
                observations[payload["id"]] = (int(row["sequence"]), merged)
            elif event_type == "think_note" and isinstance(payload.get("text"), str):
                think_note = payload["text"]
            elif event_type == "task_fact" and isinstance(payload.get("id"), str):
                task_facts[payload["id"]] = payload
        return FoldedContext(
            conversation_turns=tuple(conversation),
            observations=tuple(
                payload for _sequence, payload in sorted(observations.values(), key=lambda item: item[0])
            ),
            think_note=think_note,
            task_facts=tuple(task_facts[key] for key in sorted(task_facts)),
        )

    def conversation_batch(self) -> ConversationBatch | None:
        """Return the current Memory-ready conversation with a precise event cleanup boundary."""

        with self._lock:
            rows = self._connection.execute(
                "SELECT sequence, event_type, payload_json FROM context_events "
                "WHERE event_type IN ('user_segment','assistant_clause') ORDER BY sequence"
            ).fetchall()
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

    def clear_conversation_through(self, through_sequence: int) -> None:
        """Atomically discard one successful Memory batch and its terminal speech-ledger rows."""

        if isinstance(through_sequence, bool) or not isinstance(through_sequence, int):
            raise TypeError("through_sequence must be an integer")
        with self._transaction() as db:
            rows = db.execute(
                "SELECT payload_json FROM context_events "
                "WHERE sequence <= ? AND event_type = 'assistant_clause'",
                (through_sequence,),
            ).fetchall()
            clause_ids = [
                payload["clause_id"]
                for payload in (json.loads(row["payload_json"]) for row in rows)
                if isinstance(payload.get("clause_id"), str) and payload["clause_id"]
            ]
            db.execute(
                "DELETE FROM context_events WHERE sequence <= ? "
                "AND event_type IN ('user_segment','assistant_clause')",
                (through_sequence,),
            )
            if clause_ids:
                placeholders = ",".join("?" for _ in clause_ids)
                db.execute(
                    f"DELETE FROM speech_clauses WHERE clause_id IN ({placeholders}) "
                    "AND state IN ('delivered','interrupted','failed')",
                    tuple(clause_ids),
                )

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

    def clear_last_user_activity(self) -> None:
        """Remove the idle anchor after the corresponding conversation batch is archived."""

        with self._transaction() as db:
            db.execute("DELETE FROM context_metadata WHERE key='last_user_activity'")

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


__all__ = ["ClauseState", "ContextStore", "ConversationBatch", "FoldedContext", "SpeechClause"]
