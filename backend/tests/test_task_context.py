"""Exercise latest Task facts at the persistent Context interface.

Real SQLite verifies idempotence, version ordering, proposal replacement and restart recovery;
Task updates must not grow the conversation event log or require a complete Context fold.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.main_agent.context import ContextStore


class TaskContextTests(unittest.TestCase):
    def test_latest_version_survives_restart_without_folding_conversation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'context.sqlite3'
            store = ContextStore(path)
            store.append_event('user_segment', {'segment_id': 's', 'text': 'hello'})
            fact = {'id': 'task:t', 'task_id': 't', 'version': 3, 'status': 'running'}
            with patch.object(store, 'fold', side_effect=AssertionError('full fold during upsert')):
                self.assertTrue(store.upsert_task_fact(fact))
                self.assertFalse(store.upsert_task_fact(fact))
                self.assertFalse(store.upsert_task_fact({**fact, 'version': 2}))
            store.close()
            store = ContextStore(path)
            self.addCleanup(store.close)
            self.assertFalse(store.upsert_task_fact(fact))
            self.assertTrue(store.upsert_task_fact({**fact, 'version': 4, 'status': 'completed'}))
            folded = store.fold()
            self.assertEqual([f['status'] for f in folded.task_facts], ['completed'])
            self.assertEqual(folded.conversation_turns[0]['content'], 'hello')
            self.assertEqual(store._connection.execute('select count(*) from context_events').fetchone()[0], 1)

    def test_proposal_replaces_its_latest_fact(self):
        store = ContextStore(':memory:')
        self.addCleanup(store.close)
        fact = {'id': 'proposal:p', 'kind': 'task_proposal', 'status': 'pending'}
        self.assertTrue(store.upsert_task_fact(fact))
        self.assertFalse(store.upsert_task_fact(fact))
        self.assertTrue(store.upsert_task_fact({**fact, 'status': 'cancelled'}))
        self.assertEqual(len(store.fold().task_facts), 1)
        self.assertEqual(store.fold().task_facts[0]['status'], 'cancelled')
