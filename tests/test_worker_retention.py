"""Automatic retention must preserve durable evidence and uncertain/active workers."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from agent.config import Config
from agent.launcher import Launcher, RUNTIMES
from agent.worker_retention import CACHE_ROOTS, Controller, retire_attempt, settings


class PolicyTests(unittest.TestCase):
    def test_defaults_and_disable(self):
        self.assertEqual(settings({}), {'enabled': True, 'keep_hours': 24, 'sweep_seconds': 3600})
        self.assertFalse(Config('c', 's', 'w', worker_retention={'enabled': False}).worker_retention['enabled'])

    def test_invalid_policy_never_silently_expands_cleanup(self):
        for block in (None, [], {'paths': ['/']}, {'enabled': 1}, {'keep_hours': 0},
                      {'keep_hours': float('nan')}, {'keep_hours': -1}, {'keep_hours': True},
                      {'sweep_seconds': '60'}, {'sweep_seconds': float('inf')}):
            with self.subTest(block=block), self.assertRaisesRegex(ValueError, 'worker_retention'):
                Config('c', 's', 'w', worker_retention=block)


class _RetentionFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='worker retention 空间 ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.runs = self.root / 'state' / 'runs'
        self.attempt = self.runs / 'item' / 'attempt'
        self.attempt.mkdir(parents=True)
        self.now = time.time() + 48 * 3600
        self.evidence(self.attempt)
        self.caches = []
        for name in CACHE_ROOTS:
            cache = self.attempt / name / 'nested' / 'artifact'
            cache.parent.mkdir(parents=True)
            cache.write_bytes(('regenerable ' + name).encode())
            self.caches.append(cache)
        self.durable = {}
        for name in ('stdout.log', 'stderr.log', 'prompt.md', 'checkpoint.json', 'last_message.txt',
                     'previous-claim.token', 'home/auth.json', 'home/config.toml', 'home/state_5.sqlite',
                     'home/thread_history_1.sqlite-wal', 'home/sessions/rollout.jsonl',
                     'home/plugins/installed_plugins.json', 'home/.sandbox-bin/helper.exe',
                     'reports/TestResults.xml', 'reports/result.trx', 'source/main.go', 'bin/recovery.dll'):
            path = self.attempt / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('durable ' + name).encode())
        # Source snapshots and old arbitrary build folders at job scope are never guessed.
        for name in ('token', 'checkpoint.json', 'server-gocache/source.go', 'unit-client/bin/result.dll'):
            path = self.attempt.parent / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('job data ' + name).encode())
        for path in self.runs.rglob('*'):
            if path.is_file() and path not in self.caches:
                self.durable[path] = path.read_bytes()

    def evidence(self, attempt):
        if os.name == 'nt':
            from agent.windows_job import WindowsJob
            job = WindowsJob()
            record = {'pid': 2147483647, 'windows_job': job.name}
            killed = {**record, 'empty': True, 'descendants': []}
            job.close()
        else:
            record = {'pid': 2147483647}
            killed = {**record, 'posix_session': record['pid'], 'exited': True, 'empty': True, 'descendants': []}
        for name, data in (('process.json', record), ('killed.json', killed)):
            (attempt / name).write_text(json.dumps(data), encoding='utf-8')

    def clean(self, **options):
        return retire_attempt(self.runs, self.attempt, now=self.now, apply=True, **options)

    def preserved(self):
        for path, content in self.durable.items():
            self.assertEqual(path.read_bytes(), content, path)


class RetentionTests(_RetentionFixture, unittest.TestCase):
    def test_old_allowlisted_data_removed_and_all_durable_bytes_preserved(self):
        result = self.clean()
        self.assertEqual(result['status'], 'complete', result)
        self.assertEqual(result['files'], len(self.caches))
        self.assertTrue(all(not path.exists() for path in self.caches))
        self.preserved()
        receipt = json.loads((self.attempt / 'cache-retention.json').read_text())
        self.assertEqual(receipt['files'], len(self.caches))
        self.assertEqual(receipt['status'], 'complete')
        self.assertEqual(len(receipt['process_sha256']), 64)

    def test_missing_cache_root_does_not_skip_later_build_artifacts(self):
        self.caches[0].unlink()
        result = self.clean()
        self.assertEqual(result['status'], 'complete', result)
        self.assertFalse(self.caches[-1].exists())

    def test_inventory_is_read_only_and_second_cleanup_is_idempotent(self):
        result = retire_attempt(self.runs, self.attempt, now=self.now)
        self.assertEqual(result['files'], len(self.caches))
        self.assertTrue(all(path.exists() for path in self.caches))
        self.assertFalse((self.attempt / 'cache-retention.json').exists())
        self.clean()
        self.assertEqual(self.clean()['files'], 0)
        self.preserved()

    def test_grace_period_and_recent_cache_files_are_preserved(self):
        self.assertEqual(retire_attempt(self.runs, self.attempt, apply=True)['status'], 'young')
        self.assertTrue(all(path.exists() for path in self.caches))
        os.utime(self.caches[0], (self.now, self.now))
        result = self.clean()
        self.assertEqual(result['files'], len(self.caches) - 1)
        self.assertTrue(self.caches[0].exists())

    def test_missing_or_mismatched_teardown_holds_every_cache(self):
        killed = self.attempt / 'killed.json'
        for contents in ('{}', '{', json.dumps({'pid': 1, 'empty': True, 'descendants': []})):
            killed.write_text(contents)
            self.assertEqual(self.clean()['status'], 'held')
            self.assertTrue(all(path.exists() for path in self.caches))
        killed.unlink()
        self.assertEqual(self.clean()['status'], 'held')

    def test_uncertified_sibling_attempt_blocks_old_attempt_cleanup(self):
        sibling = self.attempt.parent / 'new-attempt'
        sibling.mkdir()
        (sibling / 'process.json').write_text('{"pid": 1}')
        self.assertEqual(self.clean()['status'], 'held')
        self.assertTrue(all(path.exists() for path in self.caches))

    def test_outside_root_and_traversal_are_refused(self):
        self.assertEqual(retire_attempt(self.runs, self.root, apply=True)['status'], 'held')
        self.assertEqual(retire_attempt(self.runs, self.attempt / '..' / 'attempt', apply=True)['status'], 'held')
        self.assertTrue(all(path.exists() for path in self.caches))

    def test_cache_directory_symlink_cannot_delete_external_files(self):
        outside = self.root / 'external'
        outside.mkdir()
        protected = outside / 'secret'
        protected.write_bytes(b'preserve')
        target = self.attempt / CACHE_ROOTS[0] / 'redirect'
        target.symlink_to(outside, target_is_directory=True)
        result = self.clean()
        self.assertEqual(result['status'], 'held')
        self.assertEqual(protected.read_bytes(), b'preserve')
        self.preserved()

    def test_hardlink_and_symlink_files_are_never_deleted(self):
        outside = self.root / 'keep'
        outside.write_bytes(b'preserve')
        target = self.attempt / CACHE_ROOTS[0] / 'link'
        os.link(outside, target)
        self.assertEqual(self.clean()['status'], 'held')
        self.assertEqual(outside.read_bytes(), b'preserve')
        target.unlink()
        target.symlink_to(outside)
        self.assertEqual(self.clean()['status'], 'held')
        self.assertEqual(outside.read_bytes(), b'preserve')

    def test_audit_failure_prevents_deletion(self):
        with patch('agent.worker_retention._receipt', side_effect=OSError('private host path')):
            result = self.clean()
        self.assertEqual(result['status'], 'held')
        self.assertNotIn('private host path', json.dumps(result))
        self.assertTrue(all(path.exists() for path in self.caches))

    def test_shutdown_interrupts_proof_scans_without_deleting_data(self):
        result = self.clean(stop=lambda: True)
        self.assertEqual(result['status'], 'held')
        self.assertEqual(result['reason'], 'stopping')
        self.assertTrue(all(path.exists() for path in self.caches))

    def test_receipt_link_preserves_external_data_and_all_caches(self):
        outside = self.root / 'external.json'
        outside.write_bytes(b'preserve')
        (self.attempt / 'cache-retention.json').symlink_to(outside)
        self.assertEqual(self.clean()['status'], 'held')
        self.assertTrue(all(path.exists() for path in self.caches))
        self.assertEqual(outside.read_bytes(), b'preserve')

    def test_bounded_batches_resume_without_deleting_durable_files(self):
        total = 0
        for _ in range(12):
            result = self.clean(limit=2)
            self.assertLessEqual(result['files'], 2)
            total += result['files']
            if result['status'] == 'complete':
                break
            self.assertEqual(result['status'], 'partial', result)
        self.assertEqual(total, len(self.caches))
        receipt = json.loads((self.attempt / 'cache-retention.json').read_text())
        self.assertEqual(receipt['total_files'], total)
        self.preserved()

    def test_old_controller_managed_server_caches_only_after_all_attempts_age_out(self):
        targets = [self.attempt.parent / 'server-tests' / name for name in ('go-cache/ab/object', 'tmp/go-build1/b001/a.o')]
        for target in targets:
            target.parent.mkdir(parents=True)
            target.write_bytes(b'old compiler data')
        sibling = self.attempt.parent / 'recent-attempt'
        sibling.mkdir()
        self.evidence(sibling)
        ended = sibling / 'killed.json'
        os.utime(ended, (self.now, self.now))
        self.assertEqual(self.clean()['status'], 'complete')
        self.assertTrue(all(target.exists() for target in targets))
        self.now += 25 * 3600
        self.assertEqual(self.clean()['files'], 2)
        self.assertFalse(any(target.exists() for target in targets))
        self.preserved()

    def test_missing_identity_in_an_attempt_named_by_the_launcher_holds_cleanup(self):
        sibling = self.attempt.parent / '20261018T010203Z-123456789abc'
        sibling.mkdir()
        self.assertEqual(self.clean()['status'], 'held')
        self.assertTrue(all(path.exists() for path in self.caches))

    def test_controller_backlog_interval_disable_and_active_job(self):
        launcher = Launcher(self.runs, RUNTIMES['fake'], 'test')
        controller = Controller(launcher, {}, clock=lambda: self.now)
        self.addCleanup(controller.close)
        launcher._handles['item'] = object()
        self.assertTrue(controller.tick())
        self.assertTrue(all(path.exists() for path in self.caches))
        self.assertFalse(controller.tick())  # End of this pass; wait the configured interval.
        launcher._handles.clear()
        self.assertFalse(controller.tick())
        self.now += 3601
        with redirect_stdout(io.StringIO()):
            self.assertTrue(controller.tick())
        self.assertFalse(any(path.exists() for path in self.caches))
        self.assertFalse(Controller(launcher, {'enabled': False}).tick())
        self.preserved()

    def test_spawn_and_retention_share_one_lock(self):
        launcher = Launcher(self.runs, RUNTIMES['fake'], 'test')
        controller = Controller(launcher, {}, clock=lambda: self.now)
        self.addCleanup(controller.close)
        entered = threading.Event()
        with patch('agent.worker_retention.retire_attempt', side_effect=lambda *a, **k: entered.set() or
                   {'status': 'complete', 'files': 0, 'bytes': 0}):
            with launcher.retention_lock:
                thread = threading.Thread(target=controller.tick)
                thread.start()
                self.assertFalse(entered.wait(.1))
            thread.join(5)
        self.assertTrue(entered.is_set())
        self.assertFalse(thread.is_alive())

    def test_unreadable_job_does_not_abort_the_rest_of_the_backlog(self):
        launcher = Launcher(self.runs, RUNTIMES['fake'], 'test')
        controller = Controller(launcher, {}, clock=lambda: self.now)
        self.addCleanup(controller.close)
        unreadable = self.runs / 'unreadable'
        unreadable.mkdir()
        scan = os.scandir
        def read(path):
            if isinstance(path, (str, os.PathLike)) and Path(path) == unreadable:
                raise PermissionError('private host path')
            return scan(path)
        output = io.StringIO()
        with patch('agent.worker_retention.os.scandir', side_effect=read), redirect_stdout(output):
            for _ in range(5):
                if not controller.tick():
                    break
        self.assertFalse(any(path.exists() for path in self.caches))
        self.assertIn('PermissionError', output.getvalue())
        self.assertNotIn('private host path', output.getvalue())
        self.preserved()

    def test_real_active_job_is_held_even_with_forged_empty_evidence(self):
        launcher = Launcher(self.runs, RUNTIMES['fake'], 'test')
        handle = launcher.spawn('active', 'prompt', {}, 30, self.root, extra_env={'FAKE_CLI_MODE': 'sleep'})
        self.addCleanup(launcher.poll)
        self.addCleanup(launcher.stop, 'active', 1)
        process = json.loads((handle.run_dir / 'process.json').read_text())
        killed = {**process, 'empty': True, 'descendants': [], 'posix_session': handle.pid, 'exited': True}
        (handle.run_dir / 'killed.json').write_text(json.dumps(killed))
        target = handle.run_dir / CACHE_ROOTS[0] / 'cache.json'
        target.parent.mkdir(parents=True)
        target.write_bytes(b'active cache')
        result = retire_attempt(self.runs, handle.run_dir, apply=True, now=self.now)
        self.assertEqual(result['status'], 'held', result)
        self.assertTrue(target.exists())

    def test_new_spawn_waits_for_the_retention_batch(self):
        launcher = Launcher(self.runs, RUNTIMES['fake'], 'test')
        started = threading.Event()
        handles = []
        def spawn():
            started.set()
            handles.append(launcher.spawn('new-job', 'prompt', {}, 30, self.root))
        self.addCleanup(launcher.poll)
        self.addCleanup(launcher.stop, 'new-job', 1)
        with launcher.retention_lock:
            thread = threading.Thread(target=spawn)
            thread.start()
            self.assertTrue(started.wait(2))
            time.sleep(.05)
            self.assertFalse(launcher.state_dir('new-job').exists())
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(handles), 1)

    def test_launcher_places_actual_child_build_data_in_its_attempt(self):
        runtime = RUNTIMES['fake']._replace(command=[sys.executable, '-c',
            'import json,os;from pathlib import Path;'
            'p=Path(os.environ["FARMBOT_BUILD_ROOT"]);(p/"artifact.dll").write_bytes(b"compiled");'
            'print(json.dumps(dict((k,os.environ[k]) for k in ("GOCACHE","GOTMPDIR","FARMBOT_BUILD_ROOT"))))'])
        launcher = Launcher(self.runs, runtime, 'test')
        handle = launcher.spawn('environment', 'prompt', {}, 30, self.root,
                                extra_env={'GOCACHE': str(self.root / 'wrong')})
        self.addCleanup(launcher.poll)
        self.addCleanup(launcher.stop, 'environment', 1)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not launcher.poll():
            time.sleep(.02)
        self.assertEqual(launcher.running(), {})
        environment = json.loads((handle.run_dir / 'stdout.log').read_text())
        self.assertEqual(Path(environment['GOCACHE']), launcher.state_dir('environment') / 'compiler-cache-v1' / 'go-cache')
        for name in ('GOTMPDIR', 'FARMBOT_BUILD_ROOT'):
            self.assertTrue(Path(environment[name]).is_relative_to(handle.run_dir / 'disposable'))
        target = Path(environment['FARMBOT_BUILD_ROOT']) / 'artifact.dll'
        self.assertTrue(target.exists())
        result = retire_attempt(self.runs, handle.run_dir, apply=True, now=self.now)
        self.assertEqual(result['status'], 'complete', result)
        self.assertFalse(target.exists())
        self.assertTrue((handle.run_dir / 'stdout.log').exists())


@unittest.skipUnless(os.name == 'nt', 'Windows handles prevent ancestor replacement and file contention')
class NativeBoundaryTests(_RetentionFixture, unittest.TestCase):
    def test_open_cache_file_is_held_and_parent_rename_is_denied(self):
        from agent.runtime_retention import _locked_file
        with _locked_file(self.caches[0]):
            self.assertEqual(self.clean()['status'], 'held')
        original = self.caches[0].parent
        from agent.worker_retention import _windows_tree
        def race(root, batch, **options):
            with self.assertRaises(OSError):
                self.attempt.rename(self.root / 'escaped')
            return _windows_tree(root, batch, **options)
        with patch('agent.worker_retention._windows_tree', side_effect=race):
            self.assertEqual(self.clean()['status'], 'complete')
        self.preserved()


if __name__ == '__main__':
    unittest.main()
