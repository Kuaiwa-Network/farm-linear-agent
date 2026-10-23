"""Offline watchdog tests; never starts or stops the host's Docker."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from agent.docker_watchdog import Watchdog, digest


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='docker watchdog 空间 ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.disk = self.root / 'data/disk/docker_data.vhdx'
        self.disk.parent.mkdir(parents=True)
        self.disk.write_bytes(b'original disk')
        self.exe = self.root / 'docker.exe'
        self.exe.write_bytes(b'pinned executable')
        self.settings = self.root / 'settings.json'
        self.settings.write_text(json.dumps({'wslDefaultDataRoot': str(self.disk.parent.parent)}))
        self.config = dict(state_root=str(self.root), host_task='FarmBot-Docker-Host', docker=str(self.exe),
            endpoint='npipe:////./pipe/docker_engine', daemon_id='original',
            pins={str(self.exe): digest(self.exe)}, settings=str(self.settings),
            data_root=str(self.disk.parent.parent), user='fixture',
            action={'Execute': 'pythonw.exe', 'Arguments': 'start.py', 'WorkingDirectory': str(self.root)})
        self.now = 1000
        self.run = Mock(return_value=subprocess.CompletedProcess([], 0, '', ''))
        self.guard = Watchdog(self.config, run=self.run, clock=lambda: self.now)
        self.host = dict(state='Ready', user='fixture', actions=[self.config['action']], processes=[])

    def test_healthy_original_engine_is_never_restarted(self):
        self.run.return_value.stdout = json.dumps({'ID': 'original', 'OSType': 'linux', 'ContainersRunning': 2})
        self.assertEqual(self.guard.tick()['state'], 'healthy')
        self.assertEqual(self.run.call_count, 1)
        self.assertIn('--host', self.run.call_args.args[0])

    def test_only_absent_backend_starts_verified_host_task_with_backoff(self):
        with patch.object(self.guard, 'health', return_value=None), patch.object(self.guard, 'host', return_value=self.host):
            self.assertEqual(self.guard.tick()['state'], 'restart_requested')
            self.run.assert_called_once()
            self.assertEqual(self.run.call_args.args[0], ['schtasks.exe', '/Run', '/TN', 'FarmBot-Docker-Host'])
            self.assertEqual(self.guard.tick()['state'], 'backoff')
            self.now += 60
            self.assertEqual(self.guard.tick()['attempt'], 2)
            self.assertEqual(self.run.call_count, 2)

    def test_live_backend_or_disabled_or_changed_task_never_restarted(self):
        with patch.object(self.guard, 'health', return_value=None), patch.object(self.guard, 'host', return_value=self.host):
            self.host['processes'] = [{'ProcessId': 123, 'ExecutablePath': 'unverified'}]
            self.assertEqual(self.guard.tick()['state'], 'unhealthy_backend_alive')
            self.host['processes'] = []
            self.host['state'] = 'Running'
            self.assertEqual(self.guard.tick()['state'], 'host_starting')
            self.host['state'] = 'Disabled'
            self.assertEqual(self.guard.tick()['state'], 'host_task_unavailable')
            self.host['user'] = 'someone else'
            with self.assertRaisesRegex(ValueError, 'ownership'):
                self.guard.tick()
            self.run.assert_not_called()

    def test_wrong_daemon_or_changed_disk_or_binary_refuses(self):
        self.run.return_value.stdout = json.dumps({'ID': 'different', 'OSType': 'linux'})
        with self.assertRaisesRegex(ValueError, 'identity'):
            self.guard.tick()
        self.assertEqual(self.run.call_count, 1)
        self.settings.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'data root'):
            self.guard.tick()
        self.exe.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'pinned'):
            self.guard.tick()

    def test_pause_and_health_timeout_are_safe(self):
        (self.root / 'pause').touch()
        self.assertEqual(self.guard.tick()['state'], 'paused')
        self.run.assert_not_called()
        self.run.side_effect = subprocess.TimeoutExpired('docker', 10)
        self.assertIsNone(self.guard.health())


if __name__ == '__main__':
    unittest.main()
