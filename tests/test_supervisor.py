import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from agent.config import Config
from agent.environment import ControllerGuard
from agent.supervisor import InstanceLock, Supervisor, digest, main, selected_environment


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / 'state with spaces 测试'
        self.config_path = Path(self.tmp.name).resolve() / 'test config 配置.json'
        self.values = dict(client_id='client', client_secret='secret', webhook_secret='sign',
                           environment='development', instance_id='test', local_root=str(self.root),
                           expected_bot_name='TestBot', expected_app_user_id='app',
                           expected_organization_id='org')
        self.config_path.write_text(json.dumps(self.values), encoding='utf-8')
        self.config = Config(**self.values)
        self.clean_env = patch.dict(os.environ, {'FARMBOT_LINEAR_STUB_DIR': ''})
        self.clean_env.start(); self.addCleanup(self.clean_env.stop)
        with ControllerGuard(self.config):
            pass
        self.now = 1000
        self.spawn = Mock()
        self.child = Mock(pid=1234)
        self.child.poll.return_value = None
        self.spawn.return_value = self.child
        self.supervisor = Supervisor(self.config, Path(__file__).resolve().parents[1], self.config_path,
                                     {'FARMBOT_CONFIG': 'wrong'}, config_sha256=digest(self.config_path),
                                     spawn=self.spawn, clock=lambda: self.now)

    def test_adopts_existing_controller_without_launch_or_ledger_access(self):
        sentinel = self.root / 'agent' / 'ledger.sqlite3'
        sentinel.parent.mkdir(exist_ok=True)
        sentinel.write_bytes(b'must not open or migrate')
        with ControllerGuard(self.config):
            self.assertEqual(self.supervisor.tick(), 'existing_controller')
        self.spawn.assert_not_called()
        self.assertEqual(sentinel.read_bytes(), b'must not open or migrate')
        self.assertEqual(self.supervisor.tick(), 'child_running')
        args, options = self.spawn.call_args
        self.assertEqual(args[0][:2], [sys.executable, '-X'])
        self.assertEqual(args[0][-2:], ['--config', str(self.config_path)])
        self.assertEqual(options['env']['FARMBOT_CONFIG'], str(self.config_path))
        self.assertEqual(options['cwd'], Path(__file__).resolve().parents[1])

    def test_restarts_even_zero_exit_with_bounded_backoff(self):
        self.supervisor.tick()
        for exit_code, delay in [(0, 10), (7, 20), (1, 40), (1, 80), (1, 160), (1, 300), (1, 300)]:
            self.child.poll.return_value = exit_code
            self.assertEqual(self.supervisor.tick(), 'backoff')
            calls = self.spawn.call_count
            self.now += delay - 1
            self.assertEqual(self.supervisor.tick(), 'backoff')
            self.assertEqual(self.spawn.call_count, calls)
            self.now += 1
            self.child.poll.return_value = None
            self.assertEqual(self.supervisor.tick(), 'child_running')
            self.assertEqual(self.spawn.call_count, calls + 1)

    def test_stable_controller_resets_crash_backoff(self):
        self.supervisor.failures = 5
        self.supervisor.tick()
        self.now += 301
        self.child.poll.return_value = 1
        self.supervisor.tick()
        self.assertEqual(self.supervisor.retry_at, self.now + 10)

    def test_pause_suppresses_restart_without_stopping_live_child(self):
        self.supervisor.tick()
        self.supervisor.pause_path.write_text('planned maintenance', encoding='utf-8')
        self.assertEqual(self.supervisor.tick(), 'child_running')
        self.child.terminate.assert_not_called()
        self.child.kill.assert_not_called()
        self.child.poll.return_value = 1
        self.assertEqual(self.supervisor.tick(), 'paused')
        self.now += 600
        self.assertEqual(self.supervisor.tick(), 'paused')
        self.assertEqual(self.spawn.call_count, 1)
        self.supervisor.pause_path.unlink()
        self.child.poll.return_value = None
        self.assertEqual(self.supervisor.tick(), 'child_running')

    def test_config_or_marker_change_refuses_spawn(self):
        before = (self.root / 'environment.json').read_bytes()
        self.config_path.write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'config changed'):
            self.supervisor.tick()
        self.spawn.assert_not_called()
        self.config_path.write_text(json.dumps(self.values), encoding='utf-8')
        (self.root / 'environment.json').write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, 'ownership'):
            self.supervisor.tick()
        self.spawn.assert_not_called()
        self.assertNotEqual((self.root / 'environment.json').read_bytes(), before)

    def test_sealed_launch_checks_source_and_tool_pins_before_spawn(self):
        env_file = Path(self.tmp.name) / 'environment.json'
        env_file.write_text('{}', encoding='utf-8')
        tool = Path(self.tmp.name) / 'pinned tool'
        tool.write_text('accepted', encoding='utf-8')
        manifest = Path(self.tmp.name) / 'launch.json'
        data = dict(source_root=str(self.supervisor.source_root),
                    production_config=str(self.config_path),
                    new_production_config_sha256=digest(self.config_path),
                    environment=str(env_file), environment_sha256=digest(env_file),
                    pins={str(tool):digest(tool)}, candidate='accepted', tree='tree')
        manifest.write_text(json.dumps(data), encoding='utf-8')
        self.supervisor.launch_manifest = manifest
        self.supervisor.launch_manifest_sha256 = digest(manifest)
        with patch('agent.supervisor.subprocess.check_output', side_effect=[b'accepted', b'tree', b'']):
            self.supervisor.validate_launch()
        with patch('agent.supervisor.subprocess.check_output', return_value=b'wrong'):
            with self.assertRaisesRegex(ValueError, 'release'):
                self.supervisor.tick()
        self.spawn.assert_not_called()
        tool.write_text('changed', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'pinned host input'):
            self.supervisor.tick()
        self.spawn.assert_not_called()
        manifest.write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'manifest changed'):
            self.supervisor.tick()

    def test_live_supervisor_rejects_fake_or_stub_integrations(self):
        for environment in ({'FARMBOT_LINEAR_STUB_DIR':'fixture'},):
            with self.assertRaisesRegex(ValueError, 'live integrations'):
                Supervisor(self.config, self.supervisor.source_root, self.config_path,
                           environment, config_sha256=digest(self.config_path))

    def test_cli_rejects_unsealed_legacy_profile_without_opening_ledger(self):
        legacy = dict(self.values, environment='legacy')
        self.config_path.write_text(json.dumps(legacy), encoding='utf-8')
        with patch('agent.supervisor.selected_environment', return_value=dict(os.environ)), \
                patch('agent.supervisor.Supervisor') as constructor:
            self.assertEqual(main(['--config',str(self.config_path),
                                   '--config-sha256',digest(self.config_path),
                                   '--source-root',str(self.supervisor.source_root),'--check']), 1)
        constructor.assert_not_called()

    def test_cli_rejects_environment_substitution_for_sealed_manifest(self):
        manifest = Path(self.tmp.name) / 'sealed launch.json'
        manifest.write_text(json.dumps({'environment':str(Path(self.tmp.name)/'accepted.json'),
                                        'environment_sha256':'a'*64}), encoding='utf-8')
        with patch('agent.supervisor.selected_environment') as select:
            self.assertEqual(main(['--config',str(self.config_path),
                                   '--config-sha256',digest(self.config_path),
                                   '--source-root',str(self.supervisor.source_root),
                                   '--launch-manifest',str(manifest),
                                   '--launch-manifest-sha256',digest(manifest),
                                   '--environment-file',str(Path(self.tmp.name)/'wrong.json'),
                                   '--environment-sha256','a'*64,'--check']), 1)
        select.assert_not_called()

    def test_child_launch_writes_only_a_diagnostic_pid_hint(self):
        self.supervisor.tick()
        self.assertEqual((self.root/'agent'/'receiver.pid').read_text(encoding='utf-8'), '1234\n')
        # A stale/reused PID file never decides whether another launch is allowed.
        (self.root/'agent'/'receiver.pid').write_text('9999\n', encoding='utf-8')
        self.assertEqual(self.supervisor.tick(), 'child_running')
        self.assertEqual(self.spawn.call_count, 1)

    def test_private_environment_is_pinned_and_conflicting_selectors_removed(self):
        path = Path(self.tmp.name) / 'private env.json'
        path.write_text(json.dumps({'PATH': 'selected path', 'FARMBOT_CONFIG': 'wrong'}), encoding='utf-8')
        with patch.dict(os.environ, {'GH_TOKEN': 'never print', 'FARMBOT_LINEAR_STUB_DIR': 'wrong'}):
            env = selected_environment(self.config_path, path, digest(path))
        self.assertNotIn('GH_TOKEN', env)
        self.assertNotIn('FARMBOT_LINEAR_STUB_DIR', env)
        self.assertEqual(env['FARMBOT_CONFIG'], str(self.config_path))
        with self.assertRaisesRegex(ValueError, 'digest'):
            selected_environment(self.config_path, path, '0' * 64)

    def test_supervisor_lock_rejects_second_owner(self):
        lock = self.root / '.supervisor.lock'
        with InstanceLock(lock):
            with self.assertRaisesRegex(RuntimeError, 'another supervisor'):
                with InstanceLock(lock):
                    pass
        with InstanceLock(lock):
            pass

    @unittest.skipUnless(os.name == 'nt', 'native Windows controller exit/restart')
    def test_native_windows_owned_process_exit_and_restart(self):
        repo = Path(__file__).resolve().parents[1]
        fixture = Path(self.tmp.name) / 'fake controller 测试.py'
        fixture.write_text('''import json, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from agent.config import Config
from agent.environment import ControllerGuard
config = Config(**json.loads(Path(sys.argv[2]).read_text(encoding='utf-8')))
with ControllerGuard(config):
    Path(sys.argv[3]).write_text('locked', encoding='utf-8')
    time.sleep(30)
''', encoding='utf-8')
        ready = Path(self.tmp.name) / 'ready'
        processes = []
        def spawn(command, **options):
            process = subprocess.Popen([sys.executable, '-B', str(fixture), str(repo), str(self.config_path),
                                        str(ready)], **options)
            processes.append(process)
            return process
        def cleanup():
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
        self.addCleanup(cleanup)
        self.supervisor.spawn = spawn
        self.supervisor.environment = dict(os.environ, FARMBOT_CONFIG=str(self.config_path))
        for attempt in range(2):
            ready.unlink(missing_ok=True)
            self.assertEqual(self.supervisor.tick(), 'child_running')
            deadline = time.monotonic() + 8
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertTrue(ready.exists(), 'fixture acquired controller lock')
            # Kill the pinned creation object, not a PID guessed from a ledger.
            self.supervisor.child.kill(); self.supervisor.child.wait(timeout=5)
            self.assertEqual(self.supervisor.tick(), 'backoff')
            self.now = self.supervisor.retry_at
        self.assertEqual(len(processes), 2)


if __name__ == '__main__':
    unittest.main()
