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
    PROFILE_ID = "profile-1"
    SOURCE_TYPE = "chat"
    NOW = "2026-07-25T00:00:00+00:00"

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "delivery.sqlite3"
        self.store = SQLiteShortTermStore(self.path)

    def tearDown(self):
        self.store.close()
        self.tmpdir.cleanup()

    def _prepare_segment(
        self,
        *,
        profile_id=PROFILE_ID,
        source_type=SOURCE_TYPE,
        topic="coffee",
        reason="topic_changed",
        content="想喝咖啡",
    ):
        message = self.store.append_message(
            profile_id=profile_id,
            source_type=source_type,
            role="user",
            content=content,
            source_id="message-source",
        )
        self.store.add_breakpoint(
            profile_id=profile_id,
            source_type=source_type,
            topic=topic,
            reason=reason,
        )
        request = self.store.prepare_oldest_segment(
            profile_id=profile_id,
            source_type=source_type,
        )
        self.assertIsNotNone(request)
        return request, message

    def _segment_row(self, segment_id):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                select *
                from short_term_segments
                where id = ?
                """,
                (segment_id,),
            ).fetchone()
        return None if row is None else dict(row)

    def _update_segment(self, segment_id, **values):
        assignments = ", ".join(f"{column} = ?" for column in values)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                f"update short_term_segments set {assignments} where id = ?",
                (*values.values(), segment_id),
            )

    def _corrupt_segment(self, segment_id, **values):
        assignments = ", ".join(f"{column} = ?" for column in values)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("pragma ignore_check_constraints = on")
            connection.execute(
                f"update short_term_segments set {assignments} where id = ?",
                (*values.values(), segment_id),
            )

    def _payload(self, segment_id):
        return json.loads(self._segment_row(segment_id)["payload_json"])

    def _assert_cross_source_error_is_opaque(self, operation, *, state):
        before = self._segment_row(operation["segment_id"])

        with self.assertRaises(ShortTermStateError) as raised:
            operation["call"]()

        message = str(raised.exception)
        self.assertNotIn("source-only secret", message)
        self.assertNotIn(state, message)
        self.assertEqual(self._segment_row(operation["segment_id"]), before)

    def test_acknowledge_requires_exact_source_and_correct_source_succeeds(self):
        prepared, _ = self._prepare_segment(content="source-only secret")
        self._prepare_segment(
            source_type="scheduler",
            content="other source",
        )
        self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )

        self._assert_cross_source_error_is_opaque(
            {
                "segment_id": prepared.segment_id,
                "call": lambda: self.store.acknowledge_segment(
                    profile_id=self.PROFILE_ID,
                    source_type="scheduler",
                    segment_id=prepared.segment_id,
                    owner="main-agent",
                    outcome="remembered",
                    episode_id=None,
                    memory_ids=[],
                    reason=None,
                    now="2026-07-25T00:00:30+00:00",
                ),
            },
            state="processing",
        )

        completed = self.store.acknowledge_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=prepared.segment_id,
            owner="main-agent",
            outcome="remembered",
            episode_id=None,
            memory_ids=[],
            reason=None,
            now="2026-07-25T00:00:30+00:00",
        )
        self.assertEqual(completed.state, SegmentState.COMPLETED)

    def test_release_requires_exact_source_and_correct_source_succeeds(self):
        prepared, _ = self._prepare_segment(content="source-only secret")
        self._prepare_segment(
            source_type="scheduler",
            content="other source",
        )
        self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )

        self._assert_cross_source_error_is_opaque(
            {
                "segment_id": prepared.segment_id,
                "call": lambda: self.store.release_segment(
                    profile_id=self.PROFILE_ID,
                    source_type="scheduler",
                    segment_id=prepared.segment_id,
                    owner="main-agent",
                    error="temporary",
                    max_attempts=2,
                    now="2026-07-25T00:00:30+00:00",
                ),
            },
            state="processing",
        )

        released = self.store.release_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=prepared.segment_id,
            owner="main-agent",
            error="temporary",
            max_attempts=2,
            now="2026-07-25T00:00:30+00:00",
        )
        self.assertEqual(released.state, SegmentState.PENDING)

    def test_requeue_requires_exact_source_and_correct_source_succeeds(self):
        prepared, _ = self._prepare_segment(content="source-only secret")
        self._prepare_segment(
            source_type="scheduler",
            content="other source",
        )
        self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )
        self.store.release_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=prepared.segment_id,
            owner="main-agent",
            error="permanent",
            max_attempts=1,
            now="2026-07-25T00:00:30+00:00",
        )

        self._assert_cross_source_error_is_opaque(
            {
                "segment_id": prepared.segment_id,
                "call": lambda: self.store.requeue_failed_segment(
                    profile_id=self.PROFILE_ID,
                    source_type="scheduler",
                    segment_id=prepared.segment_id,
                ),
            },
            state="failed",
        )

        requeued = self.store.requeue_failed_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=prepared.segment_id,
        )
        self.assertEqual(requeued.state, SegmentState.PENDING)

    def test_acknowledge_and_release_require_live_lease_at_exact_instant(self):
        cases = (
            ("before", "2026-07-25T08:00:59.999999+08:00", True),
            ("equal", "2026-07-24T20:01:00-04:00", False),
            ("after", "2026-07-25T09:01:00+08:00", False),
        )
        for operation in ("acknowledge", "release"):
            for label, now, succeeds in cases:
                with self.subTest(operation=operation, timing=label):
                    profile_id = f"profile-{operation}-{label}"
                    prepared, _ = self._prepare_segment(profile_id=profile_id)
                    self.store.claim_next_segment(
                        profile_id=profile_id,
                        source_type=self.SOURCE_TYPE,
                        owner="stale-owner",
                        lease_seconds=60,
                        now=self.NOW,
                    )
                    before = self._segment_row(prepared.segment_id)

                    if operation == "acknowledge":
                        transition = lambda: self.store.acknowledge_segment(
                            profile_id=profile_id,
                            source_type=self.SOURCE_TYPE,
                            segment_id=prepared.segment_id,
                            owner="stale-owner",
                            outcome="remembered",
                            episode_id=None,
                            memory_ids=[],
                            reason=None,
                            now=now,
                        )
                    else:
                        transition = lambda: self.store.release_segment(
                            profile_id=profile_id,
                            source_type=self.SOURCE_TYPE,
                            segment_id=prepared.segment_id,
                            owner="stale-owner",
                            error="failed",
                            max_attempts=2,
                            now=now,
                        )

                    if succeeds:
                        result = transition()
                        self.assertIn(
                            result.state,
                            (SegmentState.COMPLETED, SegmentState.PENDING),
                        )
                        continue

                    with self.assertRaisesRegex(
                        ShortTermStateError,
                        "lease",
                    ):
                        transition()
                    self.assertEqual(
                        self._segment_row(prepared.segment_id),
                        before,
                    )
                    self.store.close()
                    self.store = SQLiteShortTermStore(self.path)
                    self.assertEqual(
                        self._segment_row(prepared.segment_id),
                        before,
                    )

    def test_completed_acknowledge_is_idempotent_after_original_lease_expiry(self):
        prepared, _ = self._prepare_segment()
        self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )
        first = self.store.acknowledge_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=prepared.segment_id,
            owner="main-agent",
            outcome="remembered",
            episode_id="episode-1",
            memory_ids=["memory-1"],
            reason=None,
            now="2026-07-25T00:00:30+00:00",
        )
        before = self._segment_row(prepared.segment_id)
        self.store.close()
        self.store = SQLiteShortTermStore(self.path)

        repeated = self.store.acknowledge_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=prepared.segment_id,
            owner="retry-owner",
            outcome="discarded",
            episode_id=None,
            memory_ids=[],
            reason="late",
            now="2026-07-25T00:02:00+00:00",
        )

        self.assertEqual(repeated, first)
        self.assertEqual(self._segment_row(prepared.segment_id), before)

    def test_claim_and_owner_scoped_acknowledge_are_idempotent(self):
        prepared, _ = self._prepare_segment()

        claimed = self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )

        self.assertEqual(claimed.state, SegmentState.PROCESSING)
        self.assertEqual(claimed.attempt_count, 1)
        self.assertEqual(
            claimed.lease_expires_at,
            "2026-07-25T00:01:00+00:00",
        )
        claimed_row = self._segment_row(prepared.segment_id)
        self.assertEqual(claimed_row["lease_owner"], "main-agent")

        completed = self.store.acknowledge_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=prepared.segment_id,
            owner="main-agent",
            outcome="remembered",
            episode_id="episode-1",
            memory_ids=["memory-1", "memory-2"],
            reason=None,
            now=self.NOW,
        )
        first_row = self._segment_row(prepared.segment_id)

        self.assertEqual(completed.state, SegmentState.COMPLETED)
        self.assertEqual(
            json.loads(first_row["result_json"]),
            {
                "episode_id": "episode-1",
                "memory_ids": ["memory-1", "memory-2"],
                "outcome": "remembered",
                "reason": None,
            },
        )
        self.assertIsNotNone(first_row["completed_at"])
        self.assertIsNone(first_row["lease_owner"])
        self.assertIsNone(first_row["lease_expires_at"])
        self.assertIsNone(first_row["last_error"])

        repeated = self.store.acknowledge_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=prepared.segment_id,
            owner="different-retry-owner",
            outcome="discarded",
            episode_id=None,
            memory_ids=[],
            reason="late response",
            now=self.NOW,
        )
        repeated_row = self._segment_row(prepared.segment_id)

        self.assertEqual(repeated, completed)
        self.assertEqual(repeated_row["result_json"], first_row["result_json"])
        self.assertEqual(repeated_row["completed_at"], first_row["completed_at"])
        self.assertIsNone(
            self.store.claim_next_segment(
                profile_id=self.PROFILE_ID,
                source_type=self.SOURCE_TYPE,
                owner="main-agent",
                lease_seconds=60,
                now="2026-07-25T00:02:00+00:00",
            )
        )

    def test_release_retries_then_marks_segment_failed(self):
        prepared, _ = self._prepare_segment()
        first_claim = self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )

        pending = self.store.release_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=prepared.segment_id,
            owner="main-agent",
            error="temporary failure",
            max_attempts=2,
            now=self.NOW,
        )

        self.assertEqual(pending.state, SegmentState.PENDING)
        self.assertEqual(pending.attempt_count, first_claim.attempt_count)
        pending_row = self._segment_row(prepared.segment_id)
        self.assertEqual(pending_row["last_error"], "temporary failure")
        self.assertIsNone(pending_row["lease_owner"])
        self.assertIsNone(pending_row["lease_expires_at"])

        second_claim = self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now="2026-07-25T00:02:00+00:00",
        )
        self.assertEqual(
            self._segment_row(prepared.segment_id)["last_error"],
            "temporary failure",
        )
        failed = self.store.release_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=prepared.segment_id,
            owner="main-agent",
            error="permanent failure",
            max_attempts=2,
            now=self.NOW,
        )

        self.assertEqual(second_claim.attempt_count, 2)
        self.assertEqual(failed.state, SegmentState.FAILED)
        self.assertEqual(failed.attempt_count, 2)
        failed_row = self._segment_row(prepared.segment_id)
        self.assertEqual(failed_row["last_error"], "permanent failure")
        self.assertIsNone(failed_row["lease_owner"])
        self.assertIsNone(failed_row["lease_expires_at"])

    def test_expired_processing_lease_can_be_claimed_by_another_owner(self):
        prepared, _ = self._prepare_segment()
        first = self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="dead-worker",
            lease_seconds=10,
            now=self.NOW,
        )

        reclaimed = self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now="2026-07-25T00:01:00+00:00",
        )

        self.assertEqual(first.segment_id, prepared.segment_id)
        self.assertEqual(reclaimed.segment_id, prepared.segment_id)
        self.assertEqual(reclaimed.state, SegmentState.PROCESSING)
        self.assertEqual(reclaimed.attempt_count, 2)
        self.assertEqual(
            reclaimed.lease_expires_at,
            "2026-07-25T00:02:00+00:00",
        )
        row = self._segment_row(prepared.segment_id)
        self.assertEqual(row["lease_owner"], "main-agent")

    def test_claim_recovery_preserves_full_microsecond_precision(self):
        prepared, _ = self._prepare_segment()
        self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="original-owner",
            lease_seconds=60,
            now=self.NOW,
        )
        self._update_segment(
            prepared.segment_id,
            lease_expires_at="2026-07-25T00:01:00.123456+00:00",
        )
        before = self._segment_row(prepared.segment_id)

        early_claim = self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="early-owner",
            lease_seconds=60,
            now="2026-07-25T00:01:00.123455+00:00",
        )

        self.assertIsNone(early_claim)
        self.assertEqual(self._segment_row(prepared.segment_id), before)

        exact_claim = self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="exact-owner",
            lease_seconds=60,
            now="2026-07-25T00:01:00.123456+00:00",
        )

        self.assertEqual(exact_claim.segment_id, prepared.segment_id)
        self.assertEqual(exact_claim.attempt_count, 2)
        exact_row = self._segment_row(prepared.segment_id)
        self.assertEqual(exact_row["lease_owner"], "exact-owner")
        self.assertEqual(exact_row["attempt_count"], 2)

    def test_recover_expired_lease_compares_equivalent_offset_instants(self):
        prepared, _ = self._prepare_segment()
        self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="offset-owner",
            lease_seconds=60,
            now=self.NOW,
        )
        self._update_segment(
            prepared.segment_id,
            lease_expires_at="2026-07-25T08:01:00.123456+08:00",
        )

        recovered = self.store.recover_expired_leases(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            now="2026-07-24T20:01:00.123456-04:00",
        )

        self.assertEqual(recovered, 1)
        row = self._segment_row(prepared.segment_id)
        self.assertEqual(row["state"], "pending")
        self.assertEqual(row["attempt_count"], 1)
        self.assertIsNone(row["lease_owner"])
        self.assertIsNone(row["lease_expires_at"])

    def test_invalid_persisted_lease_rolls_back_all_recovery_paths(self):
        corrupt_leases = (
            ("null-owner", {"lease_owner": None}, "lease_owner"),
            ("blank-owner", {"lease_owner": " "}, "lease_owner"),
            ("null-expiry", {"lease_expires_at": None}, "lease_expires_at"),
            (
                "malformed-expiry",
                {"lease_expires_at": "not-a-timestamp"},
                "lease_expires_at",
            ),
            (
                "naive-expiry",
                {"lease_expires_at": "2026-07-25T00:00:30.000000"},
                "lease_expires_at",
            ),
        )
        for operation in ("explicit", "claim"):
            for label, corruption, error_field in corrupt_leases:
                with self.subTest(operation=operation, timestamp=label):
                    profile_id = f"profile-{operation}-{label}"
                    valid, _ = self._prepare_segment(
                        profile_id=profile_id,
                        content="valid expired lease",
                    )
                    invalid, _ = self._prepare_segment(
                        profile_id=profile_id,
                        content="invalid lease",
                    )
                    for owner in ("valid-owner", "invalid-owner"):
                        self.store.claim_next_segment(
                            profile_id=profile_id,
                            source_type=self.SOURCE_TYPE,
                            owner=owner,
                            lease_seconds=60,
                            now=self.NOW,
                        )
                    self._update_segment(
                        valid.segment_id,
                        lease_expires_at="2026-07-25T00:00:30+00:00",
                    )
                    self._corrupt_segment(
                        invalid.segment_id,
                        **corruption,
                    )
                    before = {
                        valid.segment_id: self._segment_row(valid.segment_id),
                        invalid.segment_id: self._segment_row(invalid.segment_id),
                    }

                    with self.assertRaisesRegex(
                        RuntimeError,
                        error_field,
                    ):
                        if operation == "explicit":
                            self.store.recover_expired_leases(
                                profile_id=profile_id,
                                source_type=self.SOURCE_TYPE,
                                now="2026-07-25T00:01:00+00:00",
                            )
                        else:
                            self.store.claim_next_segment(
                                profile_id=profile_id,
                                source_type=self.SOURCE_TYPE,
                                owner="replacement-owner",
                                lease_seconds=60,
                                now="2026-07-25T00:01:00+00:00",
                            )

                    after = {
                        valid.segment_id: self._segment_row(valid.segment_id),
                        invalid.segment_id: self._segment_row(invalid.segment_id),
                    }
                    self.assertEqual(after, before)

    def test_recovery_and_claim_race_preserves_one_processing_claim(self):
        prepared, _ = self._prepare_segment()
        self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="expired-owner",
            lease_seconds=10,
            now=self.NOW,
        )
        competing_store = SQLiteShortTermStore(self.path)
        self.addCleanup(competing_store.close)
        barrier = Barrier(2)
        result_lock = Lock()
        results = {}
        errors = []

        def recover():
            try:
                barrier.wait()
                recovered = self.store.recover_expired_leases(
                    profile_id=self.PROFILE_ID,
                    source_type=self.SOURCE_TYPE,
                    now="2026-07-25T00:01:00+00:00",
                )
                with result_lock:
                    results["recovered"] = recovered
            except BaseException as error:
                with result_lock:
                    errors.append(error)

        def claim():
            try:
                barrier.wait()
                claimed = competing_store.claim_next_segment(
                    profile_id=self.PROFILE_ID,
                    source_type=self.SOURCE_TYPE,
                    owner="replacement-owner",
                    lease_seconds=60,
                    now="2026-07-25T00:01:00+00:00",
                )
                with result_lock:
                    results["claimed"] = claimed
            except BaseException as error:
                with result_lock:
                    errors.append(error)

        threads = [Thread(target=recover), Thread(target=claim)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertIn(results["recovered"], (0, 1))
        self.assertEqual(results["claimed"].segment_id, prepared.segment_id)
        self.assertEqual(results["claimed"].attempt_count, 2)
        row = self._segment_row(prepared.segment_id)
        self.assertEqual(row["state"], "processing")
        self.assertEqual(row["lease_owner"], "replacement-owner")
        self.assertEqual(row["attempt_count"], 2)

    def test_recover_expired_leases_only_changes_exact_stream(self):
        expired, _ = self._prepare_segment(content="expired")
        future, _ = self._prepare_segment(content="future")
        other_source, _ = self._prepare_segment(
            source_type="scheduler",
            content="other source",
        )
        for request, owner in (
            (expired, "expired-owner"),
            (future, "future-owner"),
        ):
            claimed = self.store.claim_next_segment(
                profile_id=request.profile_id,
                source_type=request.source_type,
                owner=owner,
                lease_seconds=60,
                now=self.NOW,
            )
            self.assertEqual(claimed.segment_id, request.segment_id)
        self.store.claim_next_segment(
            profile_id=other_source.profile_id,
            source_type=other_source.source_type,
            owner="other-owner",
            lease_seconds=10,
            now=self.NOW,
        )
        self._update_segment(
            expired.segment_id,
            lease_expires_at="2026-07-25T00:00:30+00:00",
        )
        self._update_segment(
            future.segment_id,
            lease_expires_at="2026-07-25T00:02:00+00:00",
        )

        recovered = self.store.recover_expired_leases(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            now="2026-07-25T00:01:00+00:00",
        )

        self.assertEqual(recovered, 1)
        expired_row = self._segment_row(expired.segment_id)
        self.assertEqual(expired_row["state"], "pending")
        self.assertEqual(expired_row["attempt_count"], 1)
        self.assertIsNone(expired_row["lease_owner"])
        self.assertIsNone(expired_row["lease_expires_at"])
        self.assertEqual(self._segment_row(future.segment_id)["state"], "processing")
        self.assertEqual(
            self._segment_row(other_source.segment_id)["state"],
            "processing",
        )

    def test_owner_mismatch_does_not_change_durable_segment(self):
        prepared, _ = self._prepare_segment()
        self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )

        for operation in ("ack", "release"):
            with self.subTest(operation=operation):
                before = self._segment_row(prepared.segment_id)
                with self.assertRaises(ShortTermStateError):
                    if operation == "ack":
                        self.store.acknowledge_segment(
                            profile_id=self.PROFILE_ID,
                            source_type=self.SOURCE_TYPE,
                            segment_id=prepared.segment_id,
                            owner="wrong-owner",
                            outcome="remembered",
                            episode_id=None,
                            memory_ids=[],
                            reason=None,
                            now=self.NOW,
                        )
                    else:
                        self.store.release_segment(
                            profile_id=self.PROFILE_ID,
                            source_type=self.SOURCE_TYPE,
                            segment_id=prepared.segment_id,
                            owner="wrong-owner",
                            error="failed",
                            max_attempts=2,
                            now=self.NOW,
                        )
                self.assertEqual(self._segment_row(prepared.segment_id), before)

    def test_cross_profile_and_missing_segment_access_are_rejected(self):
        prepared, _ = self._prepare_segment()
        self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )

        for profile_id, segment_id in (
            ("profile-2", prepared.segment_id),
            (self.PROFILE_ID, "missing-segment"),
        ):
            for operation in ("ack", "release", "requeue"):
                with self.subTest(
                    profile_id=profile_id,
                    segment_id=segment_id,
                    operation=operation,
                ):
                    before = self._segment_row(prepared.segment_id)
                    with self.assertRaises(ShortTermStateError):
                        if operation == "ack":
                            self.store.acknowledge_segment(
                                profile_id=profile_id,
                                source_type=self.SOURCE_TYPE,
                                segment_id=segment_id,
                                owner="main-agent",
                                outcome="remembered",
                                episode_id=None,
                                memory_ids=[],
                                reason=None,
                                now=self.NOW,
                            )
                        elif operation == "release":
                            self.store.release_segment(
                                profile_id=profile_id,
                                source_type=self.SOURCE_TYPE,
                                segment_id=segment_id,
                                owner="main-agent",
                                error="failed",
                                max_attempts=2,
                                now=self.NOW,
                            )
                        else:
                            self.store.requeue_failed_segment(
                                profile_id=profile_id,
                                source_type=self.SOURCE_TYPE,
                                segment_id=segment_id,
                            )
                    self.assertEqual(self._segment_row(prepared.segment_id), before)

    def test_acknowledge_and_release_reject_nonprocessing_states(self):
        failed, _ = self._prepare_segment(content="failed")
        self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )
        self.store.release_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=failed.segment_id,
            owner="main-agent",
            error="failed",
            max_attempts=1,
            now=self.NOW,
        )
        pending, _ = self._prepare_segment(content="pending")

        for request in (pending, failed):
            for operation in ("ack", "release"):
                with self.subTest(state=request.state, operation=operation):
                    before = self._segment_row(request.segment_id)
                    with self.assertRaises(ShortTermStateError):
                        if operation == "ack":
                            self.store.acknowledge_segment(
                                profile_id=self.PROFILE_ID,
                                source_type=self.SOURCE_TYPE,
                                segment_id=request.segment_id,
                                owner="main-agent",
                                outcome="remembered",
                                episode_id=None,
                                memory_ids=[],
                                reason=None,
                                now=self.NOW,
                            )
                        else:
                            self.store.release_segment(
                                profile_id=self.PROFILE_ID,
                                source_type=self.SOURCE_TYPE,
                                segment_id=request.segment_id,
                                owner="main-agent",
                                error="failed",
                                max_attempts=1,
                                now=self.NOW,
                            )
                    self.assertEqual(self._segment_row(request.segment_id), before)

    def test_delivery_arguments_are_validated(self):
        prepared, _ = self._prepare_segment()

        claim_cases = (
            {"profile_id": "", "source_type": "chat", "owner": "owner", "lease_seconds": 1},
            {"profile_id": "profile", "source_type": " ", "owner": "owner", "lease_seconds": 1},
            {"profile_id": "profile", "source_type": "chat", "owner": "", "lease_seconds": 1},
            {"profile_id": "profile", "source_type": "chat", "owner": "owner", "lease_seconds": 0},
            {"profile_id": "profile", "source_type": "chat", "owner": "owner", "lease_seconds": -1},
            {"profile_id": "profile", "source_type": "chat", "owner": "owner", "lease_seconds": True},
        )
        for arguments in claim_cases:
            with self.subTest(claim=arguments):
                with self.assertRaises((TypeError, ValueError)):
                    self.store.claim_next_segment(**arguments, now=self.NOW)

        for invalid_now in ("not-a-time", "2026-07-25T00:00:00"):
            with self.subTest(now=invalid_now):
                with self.assertRaises(ValueError):
                    self.store.claim_next_segment(
                        profile_id=self.PROFILE_ID,
                        source_type=self.SOURCE_TYPE,
                        owner="owner",
                        lease_seconds=1,
                        now=invalid_now,
                    )

        ack_cases = (
            {"profile_id": "", "segment_id": prepared.segment_id, "owner": "owner", "outcome": "ok", "memory_ids": []},
            {"profile_id": self.PROFILE_ID, "segment_id": "", "owner": "owner", "outcome": "ok", "memory_ids": []},
            {"profile_id": self.PROFILE_ID, "segment_id": prepared.segment_id, "owner": " ", "outcome": "ok", "memory_ids": []},
            {"profile_id": self.PROFILE_ID, "segment_id": prepared.segment_id, "owner": "owner", "outcome": "", "memory_ids": []},
            {"profile_id": self.PROFILE_ID, "segment_id": prepared.segment_id, "owner": "owner", "outcome": "ok", "memory_ids": ("not", "list")},
            {"profile_id": self.PROFILE_ID, "segment_id": prepared.segment_id, "owner": "owner", "outcome": "ok", "memory_ids": ["valid", 1]},
        )
        for arguments in ack_cases:
            with self.subTest(ack=arguments):
                with self.assertRaises((TypeError, ValueError)):
                    self.store.acknowledge_segment(
                        **arguments,
                        source_type=self.SOURCE_TYPE,
                        episode_id=None,
                        reason=None,
                        now=self.NOW,
                    )

        release_cases = (
            {"owner": "", "error": "failed", "max_attempts": 1},
            {"owner": "owner", "error": " ", "max_attempts": 1},
            {"owner": "owner", "error": "failed", "max_attempts": 0},
            {"owner": "owner", "error": "failed", "max_attempts": True},
        )
        for arguments in release_cases:
            with self.subTest(release=arguments):
                with self.assertRaises((TypeError, ValueError)):
                    self.store.release_segment(
                        profile_id=self.PROFILE_ID,
                        source_type=self.SOURCE_TYPE,
                        segment_id=prepared.segment_id,
                        **arguments,
                        now=self.NOW,
                    )

    def test_only_failed_segments_can_be_requeued_cleanly(self):
        failed, _ = self._prepare_segment(content="failed")
        self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )
        self.store.release_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=failed.segment_id,
            owner="main-agent",
            error="failed permanently",
            max_attempts=1,
            now=self.NOW,
        )

        processing, _ = self._prepare_segment(content="processing")
        processing_claim = self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )
        self.assertEqual(processing_claim.segment_id, processing.segment_id)

        completed, _ = self._prepare_segment(content="completed")
        completed_claim = self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )
        self.assertEqual(completed_claim.segment_id, completed.segment_id)
        self.store.acknowledge_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=completed.segment_id,
            owner="main-agent",
            outcome="remembered",
            episode_id=None,
            memory_ids=[],
            reason=None,
            now=self.NOW,
        )

        pending, _ = self._prepare_segment(content="pending")

        requeued = self.store.requeue_failed_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=failed.segment_id,
        )

        self.assertEqual(requeued.state, SegmentState.PENDING)
        self.assertEqual(requeued.attempt_count, 0)
        row = self._segment_row(failed.segment_id)
        self.assertIsNone(row["last_error"])
        self.assertIsNone(row["lease_owner"])
        self.assertIsNone(row["lease_expires_at"])
        self.assertIsNone(row["completed_at"])
        self.assertEqual(row["result_json"], "{}")

        for segment_id in (
            pending.segment_id,
            processing.segment_id,
            completed.segment_id,
        ):
            with self.subTest(segment_id=segment_id):
                before = self._segment_row(segment_id)
                with self.assertRaises(ShortTermStateError):
                    self.store.requeue_failed_segment(
                        profile_id=self.PROFILE_ID,
                        source_type=self.SOURCE_TYPE,
                        segment_id=segment_id,
                    )
                self.assertEqual(self._segment_row(segment_id), before)

    def test_claims_pending_segments_oldest_first(self):
        first, _ = self._prepare_segment(content="first")
        second, _ = self._prepare_segment(content="second")
        first_payload = self._payload(first.segment_id)
        first_payload["created_at"] = "2026-07-25T00:00:00+00:00"
        self._update_segment(
            first.segment_id,
            payload_json=json.dumps(first_payload),
            created_at="2026-07-25T00:00:00+00:00",
        )
        second_payload = self._payload(second.segment_id)
        second_payload["created_at"] = "2026-07-25T00:00:01+00:00"
        self._update_segment(
            second.segment_id,
            payload_json=json.dumps(second_payload),
            created_at="2026-07-25T00:00:01+00:00",
        )

        first_claim = self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="worker-1",
            lease_seconds=60,
            now=self.NOW,
        )
        second_claim = self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="worker-2",
            lease_seconds=60,
            now=self.NOW,
        )

        self.assertEqual(
            [first_claim.segment_id, second_claim.segment_id],
            [first.segment_id, second.segment_id],
        )

    def test_concurrent_claim_returns_segment_to_exactly_one_owner(self):
        prepared, _ = self._prepare_segment()
        competing_store = SQLiteShortTermStore(self.path)
        self.addCleanup(competing_store.close)
        barrier = Barrier(2)
        result_lock = Lock()
        results = []
        errors = []

        def claim(store, owner):
            try:
                barrier.wait()
                result = store.claim_next_segment(
                    profile_id=self.PROFILE_ID,
                    source_type=self.SOURCE_TYPE,
                    owner=owner,
                    lease_seconds=60,
                    now=self.NOW,
                )
                with result_lock:
                    results.append((owner, result))
            except BaseException as error:
                with result_lock:
                    errors.append(error)

        threads = [
            Thread(target=claim, args=(self.store, "worker-1")),
            Thread(target=claim, args=(competing_store, "worker-2")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        claimed = [(owner, result) for owner, result in results if result is not None]
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0][1].segment_id, prepared.segment_id)
        self.assertEqual(self._segment_row(prepared.segment_id)["lease_owner"], claimed[0][0])

    def test_transitions_reconstruct_immutable_payload_and_row_state(self):
        prepared, message = self._prepare_segment(
            topic="exact topic",
            reason="exact reason",
            content="exact message",
        )

        claimed = self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )
        released = self.store.release_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=prepared.segment_id,
            owner="main-agent",
            error="retry",
            max_attempts=2,
            now=self.NOW,
        )
        self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now="2026-07-25T00:02:00+00:00",
        )
        completed = self.store.acknowledge_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=prepared.segment_id,
            owner="main-agent",
            outcome="remembered",
            episode_id=None,
            memory_ids=[],
            reason=None,
            now=self.NOW,
        )

        for request, state, attempts, lease in (
            (
                claimed,
                SegmentState.PROCESSING,
                1,
                "2026-07-25T00:01:00+00:00",
            ),
            (released, SegmentState.PENDING, 1, None),
            (completed, SegmentState.COMPLETED, 2, None),
        ):
            with self.subTest(state=state):
                self.assertEqual(request.topic, "exact topic")
                self.assertEqual(request.reason, "exact reason")
                self.assertEqual(request.messages, (message,))
                self.assertEqual(request.created_at, prepared.created_at)
                self.assertEqual(request.state, state)
                self.assertEqual(request.attempt_count, attempts)
                self.assertEqual(request.lease_expires_at, lease)

    def test_corrupt_payload_does_not_mutate_pending_segment(self):
        prepared, _ = self._prepare_segment()
        self._update_segment(
            prepared.segment_id,
            payload_json=json.dumps({"segment_id": prepared.segment_id}),
        )
        before = self._segment_row(prepared.segment_id)

        with self.assertRaisesRegex(RuntimeError, "payload"):
            self.store.claim_next_segment(
                profile_id=self.PROFILE_ID,
                source_type=self.SOURCE_TYPE,
                owner="main-agent",
                lease_seconds=60,
                now=self.NOW,
            )

        self.assertEqual(self._segment_row(prepared.segment_id), before)

    def test_claim_rejects_semantically_corrupt_payloads_without_mutation(self):
        cases = (
            ("empty-messages", lambda payload: payload.update(messages=[])),
            (
                "mismatched-created-at",
                lambda payload: payload.update(
                    created_at="2026-07-25T00:00:01+00:00"
                ),
            ),
            (
                "malformed-created-at",
                lambda payload: payload.update(created_at="not-a-timestamp"),
            ),
            (
                "naive-message-created-at",
                lambda payload: payload["messages"][0].update(
                    created_at="2026-07-25T00:00:00"
                ),
            ),
            (
                "duplicate-message-id",
                lambda payload: payload["messages"].append(
                    {
                        **payload["messages"][0],
                        "sequence": payload["messages"][0]["sequence"] + 1,
                    }
                ),
            ),
            (
                "duplicate-sequence",
                lambda payload: payload["messages"].append(
                    {
                        **payload["messages"][0],
                        "id": "different-message",
                    }
                ),
            ),
            (
                "out-of-order-sequence",
                lambda payload: payload["messages"].append(
                    {
                        **payload["messages"][0],
                        "id": "different-message",
                        "sequence": payload["messages"][0]["sequence"] - 1,
                    }
                ),
            ),
            (
                "invalid-payload-state",
                lambda payload: payload.update(state=1),
            ),
            (
                "invalid-payload-attempt",
                lambda payload: payload.update(attempt_count=True),
            ),
        )

        for label, corrupt in cases:
            with self.subTest(label=label):
                profile_id = f"profile-payload-{label}"
                prepared, _ = self._prepare_segment(profile_id=profile_id)
                payload = self._payload(prepared.segment_id)
                corrupt(payload)
                self._update_segment(
                    prepared.segment_id,
                    payload_json=json.dumps(payload),
                )
                before = self._segment_row(prepared.segment_id)

                with self.assertRaisesRegex(RuntimeError, "payload"):
                    self.store.claim_next_segment(
                        profile_id=profile_id,
                        source_type=self.SOURCE_TYPE,
                        owner="main-agent",
                        lease_seconds=60,
                        now=self.NOW,
                    )

                self.assertEqual(self._segment_row(prepared.segment_id), before)

    def test_claim_rejects_payload_identity_mismatches_without_mutation(self):
        identity_fields = (
            "segment_id",
            "profile_id",
            "source_type",
            "breakpoint_id",
        )

        for field_name in identity_fields:
            with self.subTest(field=field_name):
                profile_id = f"profile-identity-{field_name}"
                prepared, _ = self._prepare_segment(profile_id=profile_id)
                payload = self._payload(prepared.segment_id)
                payload[field_name] = f"wrong-{field_name}"
                self._update_segment(
                    prepared.segment_id,
                    payload_json=json.dumps(payload),
                )
                before = self._segment_row(prepared.segment_id)

                with self.assertRaisesRegex(RuntimeError, field_name):
                    self.store.claim_next_segment(
                        profile_id=profile_id,
                        source_type=self.SOURCE_TYPE,
                        owner="main-agent",
                        lease_seconds=60,
                        now=self.NOW,
                    )

                self.assertEqual(self._segment_row(prepared.segment_id), before)

    def test_equivalent_payload_and_row_created_at_instants_are_accepted(self):
        prepared, _ = self._prepare_segment()
        payload = self._payload(prepared.segment_id)
        payload["created_at"] = "2026-07-25T08:00:00+08:00"
        self._update_segment(
            prepared.segment_id,
            payload_json=json.dumps(payload),
            created_at="2026-07-25T00:00:00+00:00",
        )

        claimed = self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )

        self.assertEqual(claimed.segment_id, prepared.segment_id)
        self.assertEqual(claimed.created_at, "2026-07-25T00:00:00+00:00")

    def test_corrupt_payload_blocks_every_delivery_transition(self):
        operations = ("ack", "release", "recover", "requeue")

        for operation in operations:
            with self.subTest(operation=operation):
                profile_id = f"profile-transition-{operation}"
                prepared, _ = self._prepare_segment(profile_id=profile_id)
                self.store.claim_next_segment(
                    profile_id=profile_id,
                    source_type=self.SOURCE_TYPE,
                    owner="main-agent",
                    lease_seconds=60,
                    now=self.NOW,
                )
                if operation == "requeue":
                    self.store.release_segment(
                        profile_id=profile_id,
                        source_type=self.SOURCE_TYPE,
                        segment_id=prepared.segment_id,
                        owner="main-agent",
                        error="failed",
                        max_attempts=1,
                        now=self.NOW,
                    )
                elif operation == "recover":
                    self._update_segment(
                        prepared.segment_id,
                        lease_expires_at="2026-07-25T00:00:30+00:00",
                    )
                payload = self._payload(prepared.segment_id)
                payload["messages"] = []
                self._update_segment(
                    prepared.segment_id,
                    payload_json=json.dumps(payload),
                )
                before = self._segment_row(prepared.segment_id)

                with self.assertRaisesRegex(RuntimeError, "payload"):
                    if operation == "ack":
                        self.store.acknowledge_segment(
                            profile_id=profile_id,
                            source_type=self.SOURCE_TYPE,
                            segment_id=prepared.segment_id,
                            owner="main-agent",
                            outcome="remembered",
                            episode_id=None,
                            memory_ids=[],
                            reason=None,
                            now=self.NOW,
                        )
                    elif operation == "release":
                        self.store.release_segment(
                            profile_id=profile_id,
                            source_type=self.SOURCE_TYPE,
                            segment_id=prepared.segment_id,
                            owner="main-agent",
                            error="retry",
                            max_attempts=2,
                            now=self.NOW,
                        )
                    elif operation == "recover":
                        self.store.recover_expired_leases(
                            profile_id=profile_id,
                            source_type=self.SOURCE_TYPE,
                            now="2026-07-25T00:01:00+00:00",
                        )
                    else:
                        self.store.requeue_failed_segment(
                            profile_id=profile_id,
                            source_type=self.SOURCE_TYPE,
                            segment_id=prepared.segment_id,
                        )

                self.assertEqual(self._segment_row(prepared.segment_id), before)

    def test_completed_result_is_validated_after_close_and_reopen(self):
        invalid_results = (
            ("null", "null"),
            ("array", "[]"),
            ("missing", '{"outcome":"remembered"}'),
            (
                "wrong-types",
                json.dumps(
                    {
                        "outcome": 1,
                        "episode_id": [],
                        "memory_ids": ["valid", 2],
                        "reason": {},
                    }
                ),
            ),
        )

        for label, result_json in invalid_results:
            with self.subTest(label=label):
                profile_id = f"profile-result-{label}"
                prepared, _ = self._prepare_segment(profile_id=profile_id)
                self.store.claim_next_segment(
                    profile_id=profile_id,
                    source_type=self.SOURCE_TYPE,
                    owner="main-agent",
                    lease_seconds=60,
                    now=self.NOW,
                )
                self.store.acknowledge_segment(
                    profile_id=profile_id,
                    source_type=self.SOURCE_TYPE,
                    segment_id=prepared.segment_id,
                    owner="main-agent",
                    outcome="remembered",
                    episode_id=None,
                    memory_ids=[],
                    reason=None,
                    now=self.NOW,
                )
                self._update_segment(
                    prepared.segment_id,
                    result_json=result_json,
                )
                self.store.close()
                self.store = SQLiteShortTermStore(self.path)
                before = self._segment_row(prepared.segment_id)

                with self.assertRaisesRegex(RuntimeError, "result"):
                    self.store.acknowledge_segment(
                        profile_id=profile_id,
                        source_type=self.SOURCE_TYPE,
                        segment_id=prepared.segment_id,
                        owner="retry-owner",
                        outcome="discarded",
                        episode_id=None,
                        memory_ids=[],
                        reason="retry",
                        now=self.NOW,
                    )

                self.assertEqual(self._segment_row(prepared.segment_id), before)

    def test_repeated_ack_preserves_durable_result_after_reopen(self):
        prepared, _ = self._prepare_segment()
        self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )
        first = self.store.acknowledge_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=prepared.segment_id,
            owner="main-agent",
            outcome="remembered",
            episode_id="episode-1",
            memory_ids=["memory-1"],
            reason=None,
            now=self.NOW,
        )
        before = self._segment_row(prepared.segment_id)
        self.store.close()
        self.store = SQLiteShortTermStore(self.path)

        repeated = self.store.acknowledge_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=prepared.segment_id,
            owner="retry-owner",
            outcome="discarded",
            episode_id=None,
            memory_ids=[],
            reason="late",
            now=self.NOW,
        )

        self.assertEqual(repeated, first)
        self.assertEqual(self._segment_row(prepared.segment_id), before)

    def test_claim_lease_overflow_is_stable_and_does_not_mutate(self):
        cases = (
            ("huge-seconds", self.NOW, 10**100),
            ("max-datetime", "9999-12-31T23:59:59+00:00", 1),
        )

        for label, now, lease_seconds in cases:
            with self.subTest(label=label):
                profile_id = f"profile-overflow-{label}"
                prepared, _ = self._prepare_segment(profile_id=profile_id)
                before = self._segment_row(prepared.segment_id)

                with self.assertRaisesRegex(ValueError, "lease"):
                    self.store.claim_next_segment(
                        profile_id=profile_id,
                        source_type=self.SOURCE_TYPE,
                        owner="main-agent",
                        lease_seconds=lease_seconds,
                        now=now,
                    )

                self.assertEqual(self._segment_row(prepared.segment_id), before)

    def test_payload_reconstruction_accepts_empty_message_content(self):
        prepared, message = self._prepare_segment(content="")

        claimed = self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )

        self.assertEqual(claimed.segment_id, prepared.segment_id)
        self.assertEqual(claimed.messages, (message,))

    def test_claim_update_failure_rolls_back_transaction(self):
        prepared, _ = self._prepare_segment()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                """
                create trigger abort_segment_claim
                before update on short_term_segments
                when new.state = 'processing'
                begin
                  select raise(abort, 'claim update failed');
                end
                """
            )
        before = self._segment_row(prepared.segment_id)

        with self.assertRaises(sqlite3.IntegrityError):
            self.store.claim_next_segment(
                profile_id=self.PROFILE_ID,
                source_type=self.SOURCE_TYPE,
                owner="main-agent",
                lease_seconds=60,
                now=self.NOW,
            )

        self.assertEqual(self._segment_row(prepared.segment_id), before)

    def test_acknowledge_update_failure_rolls_back_transaction(self):
        prepared, _ = self._prepare_segment()
        self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                """
                create trigger abort_segment_acknowledge
                before update on short_term_segments
                when new.state = 'completed'
                begin
                  select raise(abort, 'acknowledge update failed');
                end
                """
            )
        before = self._segment_row(prepared.segment_id)

        with self.assertRaises(sqlite3.IntegrityError):
            self.store.acknowledge_segment(
                profile_id=self.PROFILE_ID,
                source_type=self.SOURCE_TYPE,
                segment_id=prepared.segment_id,
                owner="main-agent",
                outcome="remembered",
                episode_id="episode-1",
                memory_ids=["memory-1"],
                reason=None,
                now=self.NOW,
            )

        self.assertEqual(self._segment_row(prepared.segment_id), before)

    def test_release_update_failure_is_durably_rolled_back(self):
        prepared, _ = self._prepare_segment()
        self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                """
                create trigger abort_segment_release
                before update on short_term_segments
                when old.state = 'processing' and new.state != 'processing'
                begin
                  select raise(abort, 'release update failed');
                end
                """
            )
        before = self._segment_row(prepared.segment_id)

        with self.assertRaisesRegex(
            sqlite3.IntegrityError,
            "release update failed",
        ):
            self.store.release_segment(
                profile_id=self.PROFILE_ID,
                source_type=self.SOURCE_TYPE,
                segment_id=prepared.segment_id,
                owner="main-agent",
                error="retry",
                max_attempts=2,
                now=self.NOW,
            )

        self.store.close()
        self.store = SQLiteShortTermStore(self.path)
        self.assertEqual(self._segment_row(prepared.segment_id), before)

    def test_requeue_update_failure_is_durably_rolled_back(self):
        prepared, _ = self._prepare_segment()
        self.store.claim_next_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            owner="main-agent",
            lease_seconds=60,
            now=self.NOW,
        )
        self.store.release_segment(
            profile_id=self.PROFILE_ID,
            source_type=self.SOURCE_TYPE,
            segment_id=prepared.segment_id,
            owner="main-agent",
            error="failed",
            max_attempts=1,
            now=self.NOW,
        )
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                """
                create trigger abort_segment_requeue
                before update on short_term_segments
                when old.state = 'failed' and new.state = 'pending'
                begin
                  select raise(abort, 'requeue update failed');
                end
                """
            )
        before = self._segment_row(prepared.segment_id)

        with self.assertRaisesRegex(
            sqlite3.IntegrityError,
            "requeue update failed",
        ):
            self.store.requeue_failed_segment(
                profile_id=self.PROFILE_ID,
                source_type=self.SOURCE_TYPE,
                segment_id=prepared.segment_id,
            )

        self.store.close()
        self.store = SQLiteShortTermStore(self.path)
        self.assertEqual(self._segment_row(prepared.segment_id), before)
