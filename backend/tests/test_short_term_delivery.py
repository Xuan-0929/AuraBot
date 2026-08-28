"""Exercise the durable ShortTerm delivery contract without mirroring every SQL branch.

The scenarios cover FIFO claims, scoped ownership, live leases, retry/failure/requeue, idempotent ACK,
payload validation, concurrent claiming, and transaction rollback through the public SQLite store API.
"""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Barrier, Lock, Thread
import unittest

from backend.memory.short_term.errors import ShortTermStateError
from backend.memory.short_term.models import SegmentState
from backend.memory.stores.short_term_sqlite import SQLiteShortTermStore


class SQLiteShortTermDeliveryTests(unittest.TestCase):
    PROFILE = "profile-1"
    SOURCE = "chat"
    NOW = "2026-07-25T00:00:00+00:00"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "delivery.sqlite3"
        self.store = SQLiteShortTermStore(self.path)

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def _prepare(self, *, profile=PROFILE, source=SOURCE, content="hello"):
        message = self.store.append_message(
            profile_id=profile, source_type=source, role="user", content=content, source_id=None,
        )
        self.store.add_breakpoint(
            profile_id=profile, source_type=source, topic="topic", reason="changed",
        )
        request = self.store.prepare_oldest_segment(profile_id=profile, source_type=source)
        self.assertIsNotNone(request)
        return request, message

    def _claim(self, *, profile=PROFILE, owner="worker", now=NOW):
        return self.store.claim_next_segment(
            profile_id=profile, source_type=self.SOURCE, owner=owner, lease_seconds=60, now=now,
        )

    def _row(self, segment_id):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "select * from short_term_segments where id = ?", (segment_id,),
            ).fetchone()
        return dict(row) if row else None

    def _update(self, segment_id, **values) -> None:
        assignments = ", ".join(f"{column} = ?" for column in values)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                f"update short_term_segments set {assignments} where id = ?",
                (*values.values(), segment_id),
            )

    def test_claim_ack_is_durable_idempotent_and_fifo(self) -> None:
        first, _ = self._prepare(content="first")
        second, _ = self._prepare(content="second")
        claimed = self._claim()
        self.assertEqual((claimed.segment_id, claimed.state), (first.segment_id, SegmentState.PROCESSING))
        completed = self.store.acknowledge_segment(
            profile_id=self.PROFILE, source_type=self.SOURCE, segment_id=first.segment_id,
            owner="worker", outcome="remembered", episode_id="episode-1",
            memory_ids=["memory-1"], reason=None, now=self.NOW,
        )
        repeated = self.store.acknowledge_segment(
            profile_id=self.PROFILE, source_type=self.SOURCE, segment_id=first.segment_id,
            owner="retry-worker", outcome="discarded", episode_id=None,
            memory_ids=[], reason="late", now="2026-07-25T00:02:00+00:00",
        )
        self.assertEqual(repeated, completed)
        self.assertEqual(json.loads(self._row(first.segment_id)["result_json"])["memory_ids"], ["memory-1"])
        self.assertEqual(self._claim(owner="next").segment_id, second.segment_id)

    def test_release_retries_then_failed_segment_can_be_requeued(self) -> None:
        prepared, _ = self._prepare()
        self._claim()
        pending = self.store.release_segment(
            profile_id=self.PROFILE, source_type=self.SOURCE, segment_id=prepared.segment_id,
            owner="worker", error="temporary", max_attempts=2, now=self.NOW,
        )
        self.assertEqual(pending.state, SegmentState.PENDING)
        self._claim(now="2026-07-25T00:02:00+00:00")
        failed = self.store.release_segment(
            profile_id=self.PROFILE, source_type=self.SOURCE, segment_id=prepared.segment_id,
            owner="worker", error="permanent", max_attempts=2, now="2026-07-25T00:02:01+00:00",
        )
        self.assertEqual((failed.state, failed.attempt_count), (SegmentState.FAILED, 2))
        requeued = self.store.requeue_failed_segment(
            profile_id=self.PROFILE, source_type=self.SOURCE, segment_id=prepared.segment_id,
        )
        self.assertEqual((requeued.state, requeued.attempt_count), (SegmentState.PENDING, 0))

    def test_scope_owner_and_lease_rejections_never_mutate_the_row(self) -> None:
        prepared, _ = self._prepare(content="private")
        self._claim(owner="owner")
        cases = (
            ("source", lambda: self.store.release_segment(
                profile_id=self.PROFILE, source_type="scheduler", segment_id=prepared.segment_id,
                owner="owner", error="x", max_attempts=2, now=self.NOW,
            )),
            ("owner", lambda: self.store.release_segment(
                profile_id=self.PROFILE, source_type=self.SOURCE, segment_id=prepared.segment_id,
                owner="other", error="x", max_attempts=2, now=self.NOW,
            )),
            ("lease", lambda: self.store.release_segment(
                profile_id=self.PROFILE, source_type=self.SOURCE, segment_id=prepared.segment_id,
                owner="owner", error="x", max_attempts=2, now="2026-07-25T00:01:00+00:00",
            )),
        )
        for label, operation in cases:
            with self.subTest(label=label):
                before = self._row(prepared.segment_id)
                with self.assertRaises(ShortTermStateError):
                    operation()
                self.assertEqual(self._row(prepared.segment_id), before)

    def test_expired_lease_is_recovered_by_another_owner(self) -> None:
        prepared, _ = self._prepare()
        self._claim(owner="dead")
        recovered = self._claim(owner="replacement", now="2026-07-25T00:01:00.000001+00:00")
        self.assertEqual(recovered.segment_id, prepared.segment_id)
        self.assertEqual(self._row(prepared.segment_id)["lease_owner"], "replacement")

    def test_concurrent_claim_has_exactly_one_winner(self) -> None:
        prepared, _ = self._prepare()
        other = SQLiteShortTermStore(self.path)
        self.addCleanup(other.close)
        barrier, lock = Barrier(2), Lock()
        results, errors = [], []

        def claim(store, owner) -> None:
            try:
                barrier.wait()
                result = store.claim_next_segment(
                    profile_id=self.PROFILE, source_type=self.SOURCE, owner=owner,
                    lease_seconds=60, now=self.NOW,
                )
                with lock: results.append(result)
            except BaseException as error:
                with lock: errors.append(error)

        threads = [Thread(target=claim, args=(store, owner)) for store, owner in (
            (self.store, "one"), (other, "two"),
        )]
        for thread in threads: thread.start()
        for thread in threads: thread.join(5)
        self.assertFalse(errors)
        self.assertEqual([result.segment_id for result in results if result], [prepared.segment_id])

    def test_corrupt_payload_cases_fail_without_mutation(self) -> None:
        corruptions = {
            "missing-fields": lambda payload: payload.clear(),
            "empty-messages": lambda payload: payload.update(messages=[]),
            "wrong-identity": lambda payload: payload.update(profile_id="other"),
            "invalid-time": lambda payload: payload.update(created_at="not-a-time"),
        }
        for label, corrupt in corruptions.items():
            with self.subTest(label=label):
                profile = f"profile-{label}"
                prepared, _ = self._prepare(profile=profile)
                payload = json.loads(self._row(prepared.segment_id)["payload_json"])
                corrupt(payload)
                self._update(prepared.segment_id, payload_json=json.dumps(payload))
                before = self._row(prepared.segment_id)
                with self.assertRaises(RuntimeError):
                    self._claim(profile=profile)
                self.assertEqual(self._row(prepared.segment_id), before)

    def test_each_durable_transition_rolls_back_on_sql_failure(self) -> None:
        for name, predicate in (
            ("claim", "new.state = 'processing'"),
            ("ack", "new.state = 'completed'"),
            ("release", "old.state = 'processing' and new.state = 'pending'"),
        ):
            with self.subTest(name=name):
                profile = f"profile-{name}"
                prepared, _ = self._prepare(profile=profile)
                if name != "claim": self._claim(profile=profile)
                with closing(sqlite3.connect(self.path)) as connection, connection:
                    connection.execute(
                        f"create trigger abort_{name} before update on short_term_segments "
                        f"when {predicate} begin select raise(abort, '{name} failed'); end"
                    )
                before = self._row(prepared.segment_id)
                try:
                    with self.assertRaises(sqlite3.IntegrityError):
                        if name == "claim":
                            self._claim(profile=profile)
                        elif name == "ack":
                            self.store.acknowledge_segment(
                                profile_id=profile, source_type=self.SOURCE, segment_id=prepared.segment_id,
                                owner="worker", outcome="remembered", episode_id=None,
                                memory_ids=[], reason=None, now=self.NOW,
                            )
                        else:
                            self.store.release_segment(
                                profile_id=profile, source_type=self.SOURCE, segment_id=prepared.segment_id,
                                owner="worker", error="retry", max_attempts=2, now=self.NOW,
                            )
                finally:
                    with closing(sqlite3.connect(self.path)) as connection, connection:
                        connection.execute(f"drop trigger abort_{name}")
                self.assertEqual(self._row(prepared.segment_id), before)
