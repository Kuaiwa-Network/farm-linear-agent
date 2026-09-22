"""Explicit profiles must reject mixed identities/state before opening a ledger."""
from dataclasses import replace
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent.config import Config, Paths, linear_api
from agent.linear_api import LinearAPI
from test_linear_api import FakeHTTP


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / 'test state 测试'

    def config(self, **changes):
        return Config(**(dict(client_id='test-client', client_secret='secret', webhook_secret='sign',
                      environment='development', instance_id='dev-mac', local_root=self.root,
                      expected_bot_name='FarmBot Dev', expected_app_user_id='dev-app',
                      expected_organization_id='dev-org', issue_prefix='FBTEST') | changes))

    def test_identity_pins_reject_right_name_in_wrong_workspace_or_app(self):
        for app, org in [('wrong', 'dev-org'), ('dev-app', 'wrong')]:
            with self.subTest(app=app, org=org):
                http = FakeHTTP({'FarmBotIdentity': [{'data': {
                    'viewer': {'id': app, 'name': 'FarmBot Dev'},
                    'organization': {'id': org, 'name': 'Test'}}}]})
                api = LinearAPI('client', 'secret', expected_name='FarmBot Dev',
                                expected_app_user_id='dev-app', expected_organization_id='dev-org', request=http)
                with self.assertRaisesRegex(RuntimeError, 'identity'):
                    api.identity()
                self.assertIsNone(api.app_user_id)

    def test_explicit_profiles_require_ids_absolute_root_and_safe_instance(self):
        for values in [dict(expected_app_user_id=''), dict(expected_organization_id=''),
                       dict(local_root=Path('relative')), dict(instance_id='../production'),
                       dict(issue_prefix='FB.*'), dict(environment='typo')]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.config(**values)

    def test_live_profile_refuses_stub_or_fake_before_network_or_state(self):
        for environment in ('development', 'production'):
            for runtime, stub in [('fake', ''), ('codex', '/nonexistent/stub')]:
                with self.subTest(environment=environment, runtime=runtime, stub=stub):
                    config = self.config(environment=environment, runtime=runtime)
                    with patch.dict(os.environ, {'FARMBOT_LINEAR_STUB_DIR': stub}), \
                            patch('urllib.request.urlopen', side_effect=AssertionError('network reached')):
                        with self.assertRaisesRegex(ValueError, 'fake|stub'):
                            linear_api(config)
                    self.assertFalse(self.root.exists())

    def test_api_factory_passes_identity_pins_and_dev_name(self):
        http = FakeHTTP({'FarmBotIdentity': [{'data': {
            'viewer': {'id': 'dev-app', 'name': 'FarmBot Dev'},
            'organization': {'id': 'dev-org', 'name': 'Test'}}}]})
        with patch.dict(os.environ, {'FARMBOT_LINEAR_STUB_DIR': ''}), \
                patch('urllib.request.urlopen', http):
            self.assertEqual(linear_api(self.config()).app_user_id, 'dev-app')

    def test_guard_rejects_other_profile_and_legacy_without_rewriting_marker(self):
        from agent.environment import ControllerGuard
        config = self.config()
        with ControllerGuard(config):
            marker = self.root / 'environment.json'
            before = marker.read_bytes()
        for other in (replace(config, instance_id='another'), replace(config, expected_organization_id='other'),
                      Config('test-client', 'secret', 'sign', local_root=self.root)):
            with self.subTest(other=other.instance_id), self.assertRaisesRegex(RuntimeError, 'ownership'):
                with ControllerGuard(other):
                    pass
            self.assertEqual(marker.read_bytes(), before)
        self.assertNotIn(b'secret', before)

    def test_guard_refuses_unmarked_runtime_and_invalid_marker(self):
        from agent.environment import ControllerGuard
        self.root.mkdir(parents=True)
        (self.root / 'agent').mkdir()
        database = self.root / 'agent' / 'ledger.sqlite3'
        database.write_bytes(b'leave this alone')
        with self.assertRaisesRegex(RuntimeError, 'unmarked'):
            with ControllerGuard(self.config()):
                pass
        self.assertEqual(database.read_bytes(), b'leave this alone')
        (self.root / 'environment.json').write_text('{broken', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, 'marker'):
            with ControllerGuard(self.config()):
                pass

    def test_state_symlink_and_external_slot_are_rejected(self):
        from agent.environment import ControllerGuard
        self.root.mkdir(parents=True)
        outside = Path(self.tmp.name) / 'outside'
        outside.mkdir()
        (self.root / 'runs').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'root'):
            with ControllerGuard(self.config()):
                pass
        (self.root / 'runs').unlink()
        with self.assertRaisesRegex(ValueError, 'root'):
            with ControllerGuard(self.config(slots=[{'id': 'unity_slot:1', 'folder': str(outside)}])):
                pass
        (self.root / 'editors').mkdir()
        (self.root / 'editors' / 'slot-1').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'root'):
            with ControllerGuard(self.config(slots=[{'id': 'unity_slot:1'}])):
                pass
        with self.assertRaisesRegex(ValueError, 'root'):
            with ControllerGuard(self.config(slots=[{'id': 'unity_slot:x/../../../outside'}])):
                pass

    def test_unmarked_state_is_rejected_by_noncontroller_consumers(self):
        from agent.environment import check_ownership
        from agent.service import main, build, enqueue
        self.root.mkdir(parents=True)
        (self.root / 'agent').mkdir()
        ledger = Paths(self.config()).ledger
        ledger.write_bytes(b'original ledger')
        before = ledger.read_bytes()
        for operation in (lambda: check_ownership(self.config()), lambda: build(self.config()),
                          lambda: enqueue(self.config(), issue_ref='FBTEST-1', skill='chat'),
                          lambda: main(['status']), lambda: main(['slots'])):
            with patch('agent.service.load_config', return_value=self.config()), \
                    patch('urllib.request.urlopen', side_effect=AssertionError('network reached')):
                with self.assertRaisesRegex(RuntimeError, 'unmarked'):
                    operation()
            self.assertEqual(ledger.read_bytes(), before)

    def test_worker_cli_rejects_mismatched_or_missing_config_before_opening_ledger(self):
        from agent.environment import ControllerGuard
        from agent.__main__ import main
        with patch.dict(os.environ, {'FARMBOT_LINEAR_STUB_DIR': ''}), ControllerGuard(self.config()):
            pass
        ledger = Paths(self.config()).ledger
        ledger.parent.mkdir()
        ledger.write_bytes(b'original ledger')
        wrong = Path(self.tmp.name) / 'other.json'
        wrong.write_text(json.dumps(dict(client_id='other', client_secret='s', webhook_secret='w',
                                       local_root=str(self.root.parent / 'other'))), encoding='utf-8')
        for selected in (wrong, wrong.with_name('missing.json')):
            with patch.dict(os.environ, {'FARMBOT_CONFIG': str(selected), 'FARMBOT_LINEAR_STUB_DIR': ''}), \
                    contextlib.redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(main(['--db', str(ledger), 'status']), 1)
            self.assertIn('profile' if selected == wrong else 'FARMBOT_CONFIG', errors.getvalue())
            self.assertEqual(ledger.read_bytes(), b'original ledger')

    def test_controller_lock_contends_across_processes_and_releases(self):
        from agent.environment import ControllerGuard
        config = self.config()
        code = '''import sys
from pathlib import Path
from agent.config import Config
from agent.environment import ControllerGuard
config = Config('test-client', 'secret', 'sign', environment='development', instance_id='dev-mac',
    local_root=Path(sys.argv[1]), expected_bot_name='FarmBot Dev', expected_app_user_id='dev-app',
    expected_organization_id='dev-org', issue_prefix='FBTEST')
try:
    with ControllerGuard(config):
        print('acquired')
except RuntimeError as exc:
    print(str(exc))
    sys.exit(7)
'''
        def probe():
            return subprocess.run([sys.executable, '-c', code, str(self.root)], capture_output=True,
                                  text=True, encoding='utf-8', timeout=15)
        with patch.dict(os.environ, {'FARMBOT_LINEAR_STUB_DIR': ''}):
            with ControllerGuard(config):
                result = probe()
                self.assertEqual(result.returncode, 7, result.stderr)
                self.assertIn('controller', result.stdout)
                with ControllerGuard(replace(config, local_root=self.root.parent / 'independent')):
                    pass
            result = probe()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('acquired', result.stdout)

    def test_failed_build_releases_guard_without_ledger_creation(self):
        from agent.environment import ControllerGuard
        from agent.service import serve
        config = self.config()
        with patch('agent.service.load_config', return_value=config), \
                patch('agent.linear_api.LinearAPI.graphql', return_value={
                    'viewer': {'id': 'wrong', 'name': 'FarmBot Dev'},
                    'organization': {'id': 'dev-org', 'name': 'Test'}}), \
                patch.dict(os.environ, {'FARMBOT_LINEAR_STUB_DIR': ''}):
            with self.assertRaisesRegex(RuntimeError, 'identity'):
                serve('unused')
            self.assertFalse(Paths(config).ledger.exists())
            with ControllerGuard(config):
                pass


if __name__ == '__main__':
    unittest.main()
