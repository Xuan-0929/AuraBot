"""Exercise fixed Memory with real SQLite, Qdrant and Profile composition across restarts.

Tests verify stable retrieval, refusal of writes, suppression of background archives and isolation from
normal Profile databases. Model, microphone and Task providers remain outside these local checks.
"""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('AURABOT_CONFIG_PATH', str(Path(__file__).parent / 'fixtures/runtime_config.json'))
from backend.memory.locked import build_locked_memory
from backend.tests.memory_fixtures import memory_snapshot
from backend.main import create_app, config
from dataclasses import replace
from backend.main_agent.settings import MainAgentSettings
from backend.launch_options import LaunchOptions
from backend.main_agent.context import ContextStore
from backend.task.manager import TaskManager
from backend.task.settings import TaskSettings
from backend.tests.test_main_agent_runtime import ScriptedProvider


class LockedMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_retrieval_is_stable_and_all_mutations_are_rejected(self):
        results=[]
        for _ in range(2):
            with tempfile.TemporaryDirectory() as directory:
                memory=build_locked_memory(Path(directory), 'profile_a')
                try:
                    self.assertTrue(memory.read_only)
                    with self.assertRaises(sqlite3.OperationalError):
                        memory.metadata_store.write_audit_log(profile_id='profile_a',action='test',input_summary='',result_json={})
                    before=memory_snapshot(memory.metadata_store, 'profile_a')
                    first=await memory.recall_context_async(profile_id='profile_a', query='阿杰项目和咖啡店', max_chars=2400)
                    self.assertIn('阿杰', first['text'])
                    self.assertIn('咖啡店', first['text'])
                    for _ in range(3):
                        self.assertEqual(await memory.recall_context_async(profile_id='profile_a', query='阿杰项目和咖啡店', max_chars=2400), first)
                    self.assertEqual(await memory.recall_context_async(profile_id='other', query='咖啡店'), {'text':'','memory_ids':[],'char_count':27,'max_chars':2400})
                    with self.assertRaisesRegex(RuntimeError, 'memory_read_only'):
                        await memory.remember_conversation(profile_id='profile_a',turns=[{'role':'user','content':'test'}],source_id='test')
                    self.assertEqual(memory_snapshot(memory.metadata_store, 'profile_a'),before)
                    results.append(first)
                finally:
                    await memory.shutdown()
        self.assertEqual(results[0],results[1])

    async def test_profile_switch_and_backend_restart_preserve_only_session_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve()
            normal=root/'normal'/'profile_a';normal.mkdir(parents=True)
            context=ContextStore(normal/'context.sqlite3')
            context.append_event('user_segment',{'text':'daily private conversation','segment_id':'daily'})
            context.close()
            original=(normal/'context.sqlite3').read_bytes()
            settings=replace(MainAgentSettings.from_config(config,config.path),profile_root=normal.parent)
            settings_patch=patch('backend.main.MainAgentSettings.from_config',return_value=settings)
            settings_patch.start()
            self.addCleanup(settings_patch.stop)
            session=root/'session';session.mkdir()
            options=LaunchOptions(lock_memory=True,session_data_dir=session)
            def manager():
                settings=TaskSettings.from_mapping({'task':{'user_data_dir':str(root/'tasks')}},config_root=root)
                value=TaskManager(settings)
                value.capability_available=False
                return value
            # Real Task storage remains separate from the isolated Profile storage.
            for restart in range(2):
                app=create_app(task_manager=manager(), provider_factory=lambda *_:ScriptedProvider(), launch_options=options)
                async with app.router.lifespan_context(app):
                    profiles=app.state.main_agent_profiles
                    await profiles.activate('profile_a','Test Soul')
                    runtime=profiles.runtime
                    self.assertEqual(runtime.context_store.path,session/'profiles/profile_a/context.sqlite3')
                    self.assertEqual(len(runtime.context_store.fold().conversation_turns),restart)
                    self.assertIsNone(runtime._memory_idle_task)
                    if not restart:
                        await runtime.submit_text('session conversation',segment_id='session')
                        await runtime.wait_until_idle()
                        runtime._start_memory_idle_locked()
                        runtime._resume_memory_idle_locked()
                        self.assertIsNone(runtime._memory_idle_task)
                        self.assertIsNone(runtime._memory_archive_task)
                        await profiles.activate('profile_b','Test Soul')
                        self.assertEqual(profiles.runtime.context_store.fold().conversation_turns,())
                        await profiles.activate('profile_a','Test Soul')
                        self.assertEqual(len(profiles.runtime.context_store.fold().conversation_turns),1)
                    self.assertTrue(profiles.memory.read_only)
            self.assertEqual((normal/'context.sqlite3').read_bytes(),original)
            fresh=root/'fresh';fresh.mkdir()
            app=create_app(task_manager=manager(),provider_factory=lambda *_:ScriptedProvider(),launch_options=LaunchOptions(lock_memory=True,session_data_dir=fresh))
            async with app.router.lifespan_context(app):
                await app.state.main_agent_profiles.activate('profile_a','Test Soul')
                self.assertEqual(app.state.main_agent_profiles.runtime.context_store.fold().conversation_turns,())
