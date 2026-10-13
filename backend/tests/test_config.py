"""Verify Electron-owned JSON config snapshots, literal secrets and safe missing-file startup.

Tests use temporary files and independent Config objects so process startup remains immutable.
"""
import json
import tempfile
import unittest
from pathlib import Path
from backend.config import Config, ConfigError, ConfigNotInitializedError


class ConfigTests(unittest.TestCase):
    def test_snapshot_reads_backend_only_and_never_hot_reloads(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps({'version': 1, 'revision': 9, 'desktop': {'secret': 'private'},
                                       'backend': {'chat_model': {'api_key': '${literal-key}'}, 'task': {}}}))
            config = Config().initialize(path)
            self.assertEqual(config.revision, 9)
            self.assertEqual(config.chat_model['api_key'], '${literal-key}')
            self.assertNotIn('desktop', config)
            path.write_text('{}')
            self.assertIs(config.initialize(path), config)
            with self.assertRaises(ConfigError):
                config.initialize(Path(directory) / 'another.json')

    def test_profile_owned_version_two_desktop_config_starts_without_global_persona(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            snapshot = {'version': 2, 'revision': 12, 'backend': {
                'app': {'data_dir': directory}, 'main_agent': {'profile_dir': 'profiles'},
                'chat_model': {'model_name': 'fixture', 'api_key': 'local-only'},
            }, 'desktop': {'pet': {'displayLanguage': 'zh'}}}
            path.write_text(json.dumps(snapshot))
            config = Config().initialize(path)
            self.assertEqual(config.revision, 12)
            self.assertEqual(config['main_agent']['profile_dir'], 'profiles')
            self.assertNotIn('persona', dict(config))
            self.assertNotIn('desktop', dict(config))

    def test_missing_config_starts_blank_but_invalid_json_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            config = Config().initialize(path)
            self.assertEqual(dict(config), {})
            self.assertEqual(config.revision, 0)
            for raw in ('task: {}', '[]', '{"version":3}', '{"version":true}', '{"version":2.0}', '{"version":1,"backend":[]}'):
                path.write_text(raw)
                with self.assertRaises(ConfigError):
                    Config().initialize(path)
        with self.assertRaises(ConfigNotInitializedError):
            Config()['task']
