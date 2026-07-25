# Reliable Short-Term Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the destructive JSON short-term buffer with a SQLite-backed, crash-recoverable segment queue that chat-agent can claim, acknowledge, release, and retry without memory loss.

**Architecture:** Keep `ShortTermMemoryBuffer` as the compatibility-facing orchestration object, move domain values and threshold decisions into focused modules, and put all persistence and state transitions in `SQLiteShortTermStore`. Segment creation is transactional; delivery uses owner-scoped leases and idempotent acknowledgement. The memory module only hands segments to chat-agent and never summarizes or writes long-term memory itself.

**Tech Stack:** Python 3.11+, standard-library `sqlite3`, dataclasses, `unittest`, existing AuraBot memory package, `uv`.

---

## File Map

**Create**

- `backend/memory/short_term/__init__.py`: compatibility exports for short-term memory.
- `backend/memory/short_term/buffer.py`: starts as the mechanically moved compatibility buffer, then becomes the SQLite-backed orchestration layer.
- `backend/memory/short_term/models.py`: immutable messages, breakpoints, segment requests, states, and buffer statistics.
- `backend/memory/short_term/errors.py`: short-term domain and state-transition errors.
- `backend/memory/short_term/policy.py`: threshold policy only.
- `backend/memory/short_term/store.py`: storage protocol consumed by the buffer.
- `backend/memory/stores/short_term_sqlite.py`: SQLite schema, transactions, leases, retries, and legacy import.
- `backend/tests/test_short_term_memory.py`: focused unit and integration coverage for stage 1.

**Delete**

- `backend/memory/short_term.py`: replaced by the package while preserving `backend.memory.short_term` imports.

**Modify**

- `backend/memory/__init__.py`: export the new public short-term types and store.
- `backend/memory/invode.py`: preserve compatibility façade exports.
- `backend/tests/test_memory_invode.py`: keep existing behavior tests compatible with the new package.

## Public Contract

```python
buffer = ShortTermMemoryBuffer(
    profile_id="profile-1",
    source_type="chat",
    store=short_term_store,
    max_breakpoints=4,
    max_chars=3000,
)

buffer.add(role="user", content="第一段消息", source_id="message-1")
buffer.mark_breakpoint(topic="coffee", reason="topic_changed")

request = buffer.claim_next_summary(
    owner="main-agent",
    lease_seconds=60,
)

buffer.acknowledge_summary(
    segment_id=request.segment_id,
    owner="main-agent",
    outcome="remembered",
    episode_id="episode-1",
    memory_ids=["memory-1"],
)
```

Failure path:

```python
buffer.release_summary(
    segment_id=request.segment_id,
    owner="main-agent",
    error="summary model timed out",
)
```

Memory must not call a summarizer or `MemoryService.remember()` in any of these methods.

### Task 1: Compatibility Package, Domain Models, and Threshold Policy

**Files:**

- Create: `backend/memory/short_term/__init__.py`
- Create by moving: `backend/memory/short_term/buffer.py`
- Create: `backend/memory/short_term/models.py`
- Create: `backend/memory/short_term/errors.py`
- Create: `backend/memory/short_term/policy.py`
- Delete by moving: `backend/memory/short_term.py`
- Test: `backend/tests/test_short_term_memory.py`

- [ ] **Step 1: Write failing model and policy tests**

Add:

```python
from backend.memory.short_term.models import (
    BufferStats,
    SegmentState,
    ShortTermMessage,
    SummaryRequest,
)
from backend.memory.short_term.policy import ShortTermPolicy


class ShortTermPolicyTests(unittest.TestCase):
    def test_policy_triggers_on_breakpoint_or_context_limit(self):
        policy = ShortTermPolicy(max_breakpoints=2, max_chars=20)

        self.assertTrue(
            policy.should_prepare(BufferStats(message_count=3, breakpoint_count=2, char_count=10))
        )
        self.assertTrue(
            policy.should_prepare(BufferStats(message_count=3, breakpoint_count=1, char_count=20))
        )
        self.assertFalse(
            policy.should_prepare(BufferStats(message_count=3, breakpoint_count=1, char_count=19))
        )

    def test_summary_request_serializes_immutable_messages(self):
        request = SummaryRequest(
            segment_id="segment-1",
            profile_id="profile-1",
            source_type="chat",
            breakpoint_id="breakpoint-1",
            topic="coffee",
            reason="topic_changed",
            messages=(
                ShortTermMessage(
                    id="message-1",
                    sequence=1,
                    role="user",
                    content="喜欢拿铁",
                    source_id="source-1",
                    created_at="2026-07-25T00:00:00+00:00",
                ),
            ),
            state=SegmentState.PENDING,
            attempt_count=0,
            created_at="2026-07-25T00:00:01+00:00",
        )

        payload = request.to_dict()

        self.assertEqual(payload["segment_id"], "segment-1")
        self.assertEqual(payload["handoff"], "chat_agent_summary_required")
        self.assertIn("喜欢拿铁", payload["content"])
```

- [ ] **Step 2: Run the focused tests and verify import failure**

Run:

```bash
uv run --project backend python -m unittest \
  backend.tests.test_short_term_memory.ShortTermPolicyTests
```

Expected: `ModuleNotFoundError` for the new `backend.memory.short_term` modules.

- [ ] **Step 3: Mechanically migrate the compatibility module to a package**

Use an `apply_patch` move from `backend/memory/short_term.py` to
`backend/memory/short_term/buffer.py` without changing its implementation. Add
`backend/memory/short_term/__init__.py`:

```python
from .buffer import (
    ShortTermBreakpoint,
    ShortTermMemoryBuffer,
    ShortTermMessage,
)

__all__ = [
    "ShortTermBreakpoint",
    "ShortTermMemoryBuffer",
    "ShortTermMessage",
]
```

This preserves existing `from backend.memory.short_term import ShortTermMemoryBuffer`
imports before changing buffer behavior.

- [ ] **Step 4: Implement immutable domain values**

Define:

```python
class SegmentState(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class ShortTermMessage:
    id: str
    sequence: int
    role: str
    content: str
    source_id: str | None
    created_at: str


@dataclass(frozen=True)
class ShortTermBreakpoint:
    id: str
    message_sequence: int
    message_index: int
    topic: str | None
    reason: str | None
    created_at: str


@dataclass(frozen=True)
class BufferStats:
    message_count: int
    breakpoint_count: int
    char_count: int


@dataclass(frozen=True)
class SummaryRequest:
    segment_id: str
    profile_id: str
    source_type: str
    breakpoint_id: str
    topic: str | None
    reason: str | None
    messages: tuple[ShortTermMessage, ...]
    state: SegmentState
    attempt_count: int
    created_at: str
    lease_expires_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "profile_id": self.profile_id,
            "source_type": self.source_type,
            "breakpoint_id": self.breakpoint_id,
            "topic": self.topic,
            "reason": self.reason,
            "message_count": len(self.messages),
            "messages": [asdict(message) for message in self.messages],
            "content": "\n".join(
                f"{message.role}: {message.content}" for message in self.messages
            ),
            "state": self.state.value,
            "attempt_count": self.attempt_count,
            "created_at": self.created_at,
            "lease_expires_at": self.lease_expires_at,
            "handoff": "chat_agent_summary_required",
        }
```

Add `ShortTermStateError(RuntimeError)` and a frozen `ShortTermPolicy` whose
`should_prepare()` returns true when either configured threshold is reached.
Reject non-positive limits in `__post_init__`.

- [ ] **Step 5: Run focused and compatibility tests**

Run:

```bash
uv run --project backend python -m unittest \
  backend.tests.test_short_term_memory.ShortTermPolicyTests \
  backend.tests.test_memory_invode
```

Expected: the 2 new tests and all existing memory tests pass.

- [ ] **Step 6: Commit the package and domain layer**

```bash
git add \
  backend/memory/short_term.py \
  backend/memory/short_term/__init__.py \
  backend/memory/short_term/buffer.py \
  backend/memory/short_term/models.py \
  backend/memory/short_term/errors.py \
  backend/memory/short_term/policy.py \
  backend/tests/test_short_term_memory.py
git commit -m "构建短期记忆领域模型"
```

### Task 2: SQLite Messages and Breakpoints

**Files:**

- Create: `backend/memory/short_term/store.py`
- Create: `backend/memory/stores/short_term_sqlite.py`
- Modify: `backend/tests/test_short_term_memory.py`

- [ ] **Step 1: Write failing persistence and isolation tests**

Add a temporary database fixture and tests:

```python
class SQLiteShortTermStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "memory.sqlite3"
        self.store = SQLiteShortTermStore(self.path)

    def tearDown(self):
        self.store.close()
        self.tmpdir.cleanup()

    def test_messages_and_breakpoints_survive_reopen(self):
        self.store.append_message(
            profile_id="profile-1",
            source_type="chat",
            role="user",
            content="第一段话题",
            source_id="source-1",
        )
        breakpoint = self.store.add_breakpoint(
            profile_id="profile-1",
            source_type="chat",
            topic="first",
            reason="topic_changed",
        )
        self.assertIsNotNone(breakpoint)
        self.store.close()

        self.store = SQLiteShortTermStore(self.path)

        self.assertEqual(len(self.store.list_messages("profile-1", "chat")), 1)
        self.assertEqual(len(self.store.list_breakpoints("profile-1", "chat")), 1)

    def test_profiles_are_isolated(self):
        for profile_id in ("profile-1", "profile-2"):
            self.store.append_message(
                profile_id=profile_id,
                source_type="chat",
                role="user",
                content=profile_id,
                source_id=None,
            )

        profile_one = self.store.list_messages("profile-1", "chat")

        self.assertEqual([message.content for message in profile_one], ["profile-1"])
```

- [ ] **Step 2: Run tests and verify the missing store**

Run:

```bash
uv run --project backend python -m unittest \
  backend.tests.test_short_term_memory.SQLiteShortTermStoreTests
```

Expected: import failure for `SQLiteShortTermStore`.

- [ ] **Step 3: Define the store protocol**

In `short_term/store.py`, define a `Protocol` with these exact operations:

```python
from __future__ import annotations

from typing import Any, Protocol

from .models import (
    BufferStats,
    ShortTermBreakpoint,
    ShortTermMessage,
    SummaryRequest,
)


class ShortTermStore(Protocol):
    def close(self) -> None:
        raise NotImplementedError

    def append_message(
        self,
        *,
        profile_id: str,
        source_type: str,
        role: str,
        content: str,
        source_id: str | None,
    ) -> ShortTermMessage:
        raise NotImplementedError

    def add_breakpoint(
        self,
        *,
        profile_id: str,
        source_type: str,
        topic: str | None,
        reason: str | None,
    ) -> ShortTermBreakpoint | None:
        raise NotImplementedError

    def list_messages(
        self,
        profile_id: str,
        source_type: str,
    ) -> list[ShortTermMessage]:
        raise NotImplementedError

    def list_breakpoints(
        self,
        profile_id: str,
        source_type: str,
    ) -> list[ShortTermBreakpoint]:
        raise NotImplementedError

    def get_stats(self, profile_id: str, source_type: str) -> BufferStats:
        raise NotImplementedError

    def prepare_oldest_segment(
        self,
        *,
        profile_id: str,
        source_type: str,
    ) -> SummaryRequest | None:
        raise NotImplementedError

    def claim_next_segment(
        self,
        *,
        profile_id: str,
        source_type: str,
        owner: str,
        lease_seconds: int,
        now: str | None = None,
    ) -> SummaryRequest | None:
        raise NotImplementedError

    def acknowledge_segment(
        self,
        *,
        profile_id: str,
        segment_id: str,
        owner: str,
        outcome: str,
        episode_id: str | None,
        memory_ids: list[str],
        reason: str | None,
    ) -> SummaryRequest:
        raise NotImplementedError

    def release_segment(
        self,
        *,
        profile_id: str,
        segment_id: str,
        owner: str,
        error: str,
        max_attempts: int,
    ) -> SummaryRequest:
        raise NotImplementedError

    def recover_expired_leases(
        self,
        *,
        profile_id: str,
        source_type: str,
        now: str | None = None,
    ) -> int:
        raise NotImplementedError

    def requeue_failed_segment(
        self,
        *,
        profile_id: str,
        segment_id: str,
    ) -> SummaryRequest:
        raise NotImplementedError
```

Use keyword-only arguments for mutating methods.

- [ ] **Step 4: Implement the schema and basic persistence**

Create schema version 1:

```sql
create table if not exists short_term_messages (
  sequence integer primary key autoincrement,
  id text not null unique,
  profile_id text not null,
  source_type text not null,
  role text not null,
  content text not null,
  source_id text,
  created_at text not null
);

create table if not exists short_term_breakpoints (
  id text primary key,
  profile_id text not null,
  source_type text not null,
  message_sequence integer not null,
  topic text,
  reason text,
  created_at text not null,
  unique(profile_id, source_type, message_sequence)
);

create table if not exists short_term_segments (
  id text primary key,
  profile_id text not null,
  source_type text not null,
  breakpoint_id text not null unique,
  state text not null check (state in ('pending', 'processing', 'completed', 'failed')),
  payload_json text not null,
  result_json text not null default '{}',
  attempt_count integer not null default 0,
  last_error text,
  lease_owner text,
  lease_expires_at text,
  created_at text not null,
  updated_at text not null,
  completed_at text
);
```

Add profile/source/state indexes. Use `threading.RLock`, `check_same_thread=False`,
row factories, UTC ISO timestamps, and parameterized SQL. Duplicate breakpoints at
the same latest message sequence return `None`.

- [ ] **Step 5: Run store and policy tests**

Run:

```bash
uv run --project backend python -m unittest backend.tests.test_short_term_memory
```

Expected: all current short-term tests pass.

- [ ] **Step 6: Commit basic storage**

```bash
git add \
  backend/memory/short_term/store.py \
  backend/memory/stores/short_term_sqlite.py \
  backend/tests/test_short_term_memory.py
git commit -m "持久化短期消息和断点"
```

### Task 3: Atomic Segment Preparation

**Files:**

- Modify: `backend/memory/stores/short_term_sqlite.py`
- Modify: `backend/tests/test_short_term_memory.py`

- [ ] **Step 1: Write failing atomic cut tests**

Add:

```python
def test_prepare_oldest_segment_keeps_newer_topic_active(self):
    self._append_topic("profile-1", ["咖啡一", "咖啡二"], topic="coffee")
    self._append_topic("profile-1", ["项目一"], topic="project")

    segment = self.store.prepare_oldest_segment(
        profile_id="profile-1",
        source_type="chat",
    )

    self.assertEqual([message.content for message in segment.messages], ["咖啡一", "咖啡二"])
    self.assertEqual(
        [message.content for message in self.store.list_messages("profile-1", "chat")],
        ["项目一"],
    )
    self.assertEqual(
        [breakpoint.topic for breakpoint in self.store.list_breakpoints("profile-1", "chat")],
        ["project"],
    )

def test_prepare_requires_a_complete_breakpoint(self):
    self.store.append_message(
        profile_id="profile-1",
        source_type="chat",
        role="user",
        content="还没有结束的话题",
        source_id=None,
    )

    self.assertIsNone(
        self.store.prepare_oldest_segment(profile_id="profile-1", source_type="chat")
    )

def test_same_breakpoint_never_creates_duplicate_segments(self):
    self._append_topic("profile-1", ["咖啡"], topic="coffee")

    first = self.store.prepare_oldest_segment(
        profile_id="profile-1", source_type="chat"
    )
    second = self.store.prepare_oldest_segment(
        profile_id="profile-1", source_type="chat"
    )

    self.assertIsNotNone(first)
    self.assertIsNone(second)
```

- [ ] **Step 2: Run the tests and verify failure**

Run:

```bash
uv run --project backend python -m unittest \
  backend.tests.test_short_term_memory.SQLiteShortTermStoreTests
```

Expected: failures because `prepare_oldest_segment()` is not implemented.

- [ ] **Step 3: Implement one-transaction segment preparation**

Inside one locked SQLite transaction:

1. Select the oldest breakpoint for the requested profile/source.
2. Select all messages for the same profile/source with `sequence <= message_sequence`.
3. Return `None` if either record set is empty.
4. Serialize the immutable message snapshot into `payload_json`.
5. Insert one `pending` segment keyed by a generated `segment_<uuid>` and unique breakpoint ID.
6. Delete consumed messages and all breakpoints through that message sequence.
7. Return the newly stored `SummaryRequest`.

Do not update long-term memory and do not call Qdrant.

- [ ] **Step 4: Run focused tests**

Run the command from Step 2.

Expected: all `SQLiteShortTermStoreTests` pass.

- [ ] **Step 5: Commit atomic preparation**

```bash
git add \
  backend/memory/stores/short_term_sqlite.py \
  backend/tests/test_short_term_memory.py
git commit -m "原子生成待总结记忆片段"
```

### Task 4: Claim, Acknowledge, Release, and Lease Recovery

**Files:**

- Modify: `backend/memory/stores/short_term_sqlite.py`
- Modify: `backend/memory/short_term/errors.py`
- Modify: `backend/tests/test_short_term_memory.py`

- [ ] **Step 1: Write failing delivery-state tests**

Add tests for:

```python
def test_claim_is_owner_scoped_and_acknowledge_is_idempotent(self):
    segment = self._prepare_segment()
    claimed = self.store.claim_next_segment(
        profile_id="profile-1",
        source_type="chat",
        owner="main-agent",
        lease_seconds=60,
        now="2026-07-25T00:00:00+00:00",
    )
    self.assertEqual(claimed.state, SegmentState.PROCESSING)
    self.assertEqual(claimed.attempt_count, 1)

    completed = self.store.acknowledge_segment(
        profile_id="profile-1",
        segment_id=segment.segment_id,
        owner="main-agent",
        outcome="remembered",
        episode_id="episode-1",
        memory_ids=["memory-1"],
        reason=None,
    )
    repeated = self.store.acknowledge_segment(
        profile_id="profile-1",
        segment_id=segment.segment_id,
        owner="main-agent",
        outcome="remembered",
        episode_id="episode-1",
        memory_ids=["memory-1"],
        reason=None,
    )
    self.assertEqual(completed.state, SegmentState.COMPLETED)
    self.assertEqual(repeated.state, SegmentState.COMPLETED)

def test_release_retries_then_fails_at_limit(self):
    segment = self._prepare_segment()

    for expected_attempt in (1, 2):
        claimed = self._claim(segment, owner="main-agent")
        released = self.store.release_segment(
            profile_id="profile-1",
            segment_id=segment.segment_id,
            owner="main-agent",
            error="model timeout",
            max_attempts=2,
        )
        self.assertEqual(claimed.attempt_count, expected_attempt)

    self.assertEqual(released.state, SegmentState.FAILED)

def test_expired_processing_lease_can_be_claimed_again(self):
    segment = self._prepare_segment()
    self.store.claim_next_segment(
        profile_id="profile-1",
        source_type="chat",
        owner="dead-worker",
        lease_seconds=10,
        now="2026-07-25T00:00:00+00:00",
    )

    claimed = self.store.claim_next_segment(
        profile_id="profile-1",
        source_type="chat",
        owner="main-agent",
        lease_seconds=60,
        now="2026-07-25T00:01:00+00:00",
    )

    self.assertEqual(claimed.segment_id, segment.segment_id)
    self.assertEqual(claimed.attempt_count, 2)
```

Also test owner mismatch, cross-profile access rejection, completed segments not being
claimed, and explicit `requeue_failed_segment()` resetting attempts to zero.

- [ ] **Step 2: Run focused state tests and verify failure**

Run:

```bash
uv run --project backend python -m unittest \
  backend.tests.test_short_term_memory.ShortTermDeliveryTests
```

Expected: failures for missing transition methods.

- [ ] **Step 3: Implement state transitions**

Implement these rules:

- `claim_next_segment()` first moves expired `processing` rows for the same
  profile/source back to `pending`, then atomically claims the oldest pending row.
- Claim sets `processing`, owner, lease expiry, and increments `attempt_count`.
- `acknowledge_segment()` accepts only the current owner while processing; if already
  completed, it returns the stored row without changing result data.
- Acknowledge writes `result_json`, clears lease fields, and sets completion time.
- `release_segment()` validates owner, records the error, clears lease fields, and
  selects `pending` or `failed` based on `max_attempts`.
- `requeue_failed_segment()` only accepts `failed`, resets attempts and error, and
  returns the row to `pending`.
- Invalid state, owner, profile, and missing segment conditions raise
  `ShortTermStateError`.

- [ ] **Step 4: Run all short-term tests**

Run:

```bash
uv run --project backend python -m unittest backend.tests.test_short_term_memory
```

Expected: all short-term tests pass.

- [ ] **Step 5: Commit reliable delivery**

```bash
git add \
  backend/memory/stores/short_term_sqlite.py \
  backend/memory/short_term/errors.py \
  backend/tests/test_short_term_memory.py
git commit -m "实现短期片段可靠交付"
```

### Task 5: SQLite-Backed Compatibility Buffer

**Files:**

- Modify: `backend/memory/short_term/buffer.py`
- Modify: `backend/memory/short_term/__init__.py`
- Modify: `backend/tests/test_short_term_memory.py`
- Modify: `backend/tests/test_memory_invode.py`

- [ ] **Step 1: Write failing buffer compatibility tests**

Add:

```python
class ShortTermMemoryBufferTests(unittest.TestCase):
    def test_threshold_prepares_pending_segment_without_long_term_write(self):
        buffer = self._buffer(max_breakpoints=2)
        buffer.add(role="user", content="咖啡", source_id="m1")
        self.assertIsNone(buffer.mark_breakpoint(topic="coffee", reason="topic_changed"))
        buffer.add(role="user", content="项目", source_id="m2")

        prepared = buffer.mark_breakpoint(topic="project", reason="topic_changed")

        self.assertEqual(prepared["state"], "pending")
        self.assertEqual(prepared["topic"], "coffee")
        self.assertEqual([message.content for message in buffer.messages], ["项目"])

    def test_context_limit_does_not_split_without_breakpoint(self):
        buffer = self._buffer(max_chars=5)

        prepared = buffer.add(role="user", content="超过上下文限制", source_id="m1")

        self.assertIsNone(prepared)
        self.assertEqual(len(buffer.messages), 1)

    def test_buffer_delegates_claim_ack_release(self):
        buffer = self._prepared_buffer()
        claimed = buffer.claim_next_summary(owner="main-agent", lease_seconds=60)
        released = buffer.release_summary(
            segment_id=claimed.segment_id,
            owner="main-agent",
            error="temporary failure",
        )

        self.assertEqual(released.state, SegmentState.PENDING)
```

Keep the existing assertions in `test_memory_invode.py` that `add()`,
`mark_breakpoint()`, `messages`, `breakpoints`, and dictionary segment payloads work.

- [ ] **Step 2: Run old and new tests and verify migration failure**

Run:

```bash
uv run --project backend python -m unittest \
  backend.tests.test_short_term_memory \
  backend.tests.test_memory_invode
```

Expected: failures until the compatibility buffer delegates to the SQLite store.

- [ ] **Step 3: Implement the buffer orchestration**

`ShortTermMemoryBuffer` must:

- Accept `profile_id`, `source_type`, thresholds, and an optional `ShortTermStore`.
- Use an in-memory `SQLiteShortTermStore(":memory:")` when no store/path is supplied.
- Expose `messages` and `breakpoints` as read-only snapshots from the store.
- Append messages and breakpoints, then ask `ShortTermPolicy` whether to prepare.
- Return `SummaryRequest.to_dict()` from legacy `add()`, `add_message()`,
  `mark_breakpoint()`, and `pop_oldest_segment_for_summary()`.
- Expose typed `claim_next_summary()`, `acknowledge_summary()`,
  `release_summary()`, and `requeue_failed_summary()`.
- Track whether it created the store; `close()` closes only an owned store and leaves an
  injected store under caller ownership.
- Keep deprecated `service` and `max_messages` constructor arguments accepted but
  unused, preventing old callers from breaking.
- Never import or call `MemoryService`.

- [ ] **Step 4: Switch package exports to the new domain values**

Replace `short_term/__init__.py` with:

```python
from .buffer import ShortTermMemoryBuffer
from .errors import ShortTermStateError
from .models import (
    BufferStats,
    SegmentState,
    ShortTermBreakpoint,
    ShortTermMessage,
    SummaryRequest,
)
from .policy import ShortTermPolicy
from .store import ShortTermStore

__all__ = [
    "BufferStats",
    "SegmentState",
    "ShortTermBreakpoint",
    "ShortTermMemoryBuffer",
    "ShortTermMessage",
    "ShortTermPolicy",
    "ShortTermStateError",
    "ShortTermStore",
    "SummaryRequest",
]
```

- [ ] **Step 5: Run old and new tests**

Run the command from Step 2.

Expected: both test modules pass.

- [ ] **Step 6: Commit package migration**

```bash
git add \
  backend/memory/short_term/__init__.py \
  backend/memory/short_term/buffer.py \
  backend/tests/test_short_term_memory.py \
  backend/tests/test_memory_invode.py
git commit -m "模块化短期记忆缓冲区"
```

### Task 6: Legacy JSON Import, Exports, and Full Verification

**Files:**

- Modify: `backend/memory/stores/short_term_sqlite.py`
- Modify: `backend/memory/short_term/buffer.py`
- Modify: `backend/memory/short_term/__init__.py`
- Modify: `backend/memory/__init__.py`
- Modify: `backend/memory/invode.py`
- Modify: `backend/tests/test_short_term_memory.py`

- [ ] **Step 1: Write failing legacy migration and export tests**

Add:

```python
def test_legacy_json_is_imported_once_into_sqlite_sidecar(self):
    legacy_path = Path(self.tmpdir.name) / "short-term.json"
    legacy_path.write_text(
        json.dumps(
            {
                "profile_id": "profile-1",
                "source_type": "chat",
                "messages": [
                    {
                        "role": "user",
                        "content": "旧对话",
                        "source_id": "m1",
                        "created_at": "2026-07-25T00:00:00+00:00",
                    }
                ],
                "breakpoints": [
                    {
                        "id": "breakpoint-1",
                        "message_index": 1,
                        "topic": "legacy",
                        "reason": "topic_changed",
                        "created_at": "2026-07-25T00:00:01+00:00",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    first = ShortTermMemoryBuffer(
        profile_id="profile-1",
        source_type="chat",
        persistence_path=legacy_path,
    )
    first.close()
    second = ShortTermMemoryBuffer(
        profile_id="profile-1",
        source_type="chat",
        persistence_path=legacy_path,
    )

    self.assertEqual([message.content for message in second.messages], ["旧对话"])
    self.assertEqual(len(second.breakpoints), 1)
    self.assertTrue(Path(f"{legacy_path}.sqlite3").exists())
```

Add import assertions:

```python
from backend.memory import (
    SegmentState,
    ShortTermMemoryBuffer,
    SQLiteShortTermStore,
    SummaryRequest,
)
from backend.memory.invode import SQLiteShortTermStore as FacadeShortTermStore
```

- [ ] **Step 2: Run migration tests and verify failure**

Run:

```bash
uv run --project backend python -m unittest \
  backend.tests.test_short_term_memory.LegacyShortTermMigrationTests
```

Expected: failure because legacy JSON import and exports are not implemented.

- [ ] **Step 3: Implement one-time legacy import**

When `persistence_path` ends in `.json`:

- Use `<path>.sqlite3` as the new SQLite database.
- If the database has no messages, parse the old JSON snapshot.
- Validate matching `profile_id` and `source_type`.
- Import messages in original order.
- Convert each legacy `message_index` to the imported message's stable sequence.
- Ignore malformed optional records but reject malformed top-level JSON with a clear
  `ValueError`.
- Never delete or rewrite the legacy JSON file.
- Reopening the buffer must not import duplicates.

For non-JSON paths, use the supplied path directly as SQLite.

- [ ] **Step 4: Export the public short-term contract**

Export from both `backend.memory` and `backend.memory.invode`:

```python
SegmentState
ShortTermMemoryBuffer
ShortTermPolicy
ShortTermStateError
SQLiteShortTermStore
SummaryRequest
```

Keep all existing façade exports unchanged.

- [ ] **Step 5: Run formatting and complete memory tests**

Run:

```bash
uv run --project backend python -m unittest \
  backend.tests.test_short_term_memory \
  backend.tests.test_memory_invode
git diff --check
```

Expected: all tests pass and `git diff --check` prints nothing.

- [ ] **Step 6: Run the complete backend test suite**

Run:

```bash
uv run --project backend python -m unittest discover -s backend/tests
```

Expected: all backend tests pass. If an unrelated environment-dependent E2E test is
skipped by its existing guard, report the skip without changing that test.

- [ ] **Step 7: Commit migration and exports**

```bash
git add \
  backend/memory/__init__.py \
  backend/memory/invode.py \
  backend/memory/short_term \
  backend/memory/stores/short_term_sqlite.py \
  backend/tests/test_short_term_memory.py
git commit -m "完成短期记忆可靠交付迁移"
```

## Completion Checks

Before reporting completion:

```bash
git status --short
git log --oneline -8
uv run --project backend python -m unittest \
  backend.tests.test_short_term_memory \
  backend.tests.test_memory_invode
uv run --project backend python -m unittest discover -s backend/tests
```

Confirm:

- Existing unrelated untracked files remain untouched.
- `ShortTermMemoryBuffer` has no `MemoryService` import.
- No memory code calls a summarizer.
- Segment creation is transactional.
- Claim/ack/release are profile- and owner-scoped.
- Expired leases recover after restart.
- Completed segments are not claimed again.
- Existing `backend.memory.short_term` imports still work.
