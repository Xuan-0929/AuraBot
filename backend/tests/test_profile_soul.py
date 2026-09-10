"""Verify complete SOUL projection and localized avatar labels without provider calls or persistent data."""
import unittest
from backend.main_agent.context import FoldedContext
from backend.main_agent.snapshot import SnapshotBudget, SnapshotProjector
from backend.main_agent.avatar import AvatarCapabilities

class ProfileSoulTests(unittest.TestCase):
    def test_full_document_and_explicit_budget_failure(self):
        text = '# SOUL\n' + 'あ' * 3000 + '\nTAIL_RULE'
        context = FoldedContext((), (), None, ())
        self.assertEqual(SnapshotProjector().project(context, soul=text).soul, text)
        with self.assertRaisesRegex(ValueError, 'soul_budget_exceeded'):
            SnapshotProjector(SnapshotBudget(soul_chars=2000)).project(context, soul=text)

    def test_display_names_do_not_change_callable_ids(self):
        config = dict(schema_version=1, revision=1, model={'manifest':'model/Test.model3.json'},
            validated_resources=['model/wave.motion3.json'], states={}, entries={
                'action_stable':dict(kind='motion', display_name='軽く手を振る', description='问候',
                    source={'file':'model/wave.motion3.json'}, playback={'mode':'once'}, agent_enabled=True)})
        cap = AvatarCapabilities.compile(config)
        self.assertEqual(cap.motion_names, ('action_stable',))
        self.assertIn('\\u8efd', cap.description())
        config['entries']['action_stable']['display_name'] = 'Wave gently'
        self.assertEqual(AvatarCapabilities.compile(config).motion_names, cap.motion_names)
        config['entries']['action_stable']['display_name'] = 'x' * 129
        with self.assertRaisesRegex(ValueError, 'invalid_display_name'):
            AvatarCapabilities.compile(config)
