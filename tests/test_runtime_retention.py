"""Containment and filesystem boundaries for disposable runtime retention."""
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from agent.runtime_retention import _proof, _instance, _locked_file, RetentionHeld, retire_codex_copy, sweep


class ProofTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='retention spaces 中文 ')
        self.addCleanup(self.tmp.cleanup)
        self.attempt = Path(self.tmp.name)
        self.job = 'Local\\FarmBot-' + 'a' * 32
        self.process = {'pid': 123, 'windows_job': self.job}
        self.killed = {'pid': 123, 'windows_job': self.job, 'empty': True, 'descendants': []}
        self.records()

    def records(self):
        for name, data in (('process.json', self.process), ('killed.json', self.killed)):
            (self.attempt / name).write_text(json.dumps(data), encoding='utf-8')

    def test_proof_requires_matching_pid_and_job_and_empty_descendants(self):
        self.assertEqual(len(_proof(self.attempt, lambda _: True)), 2)
        for key, value in (('pid', 124), ('windows_job', 'Local\\FarmBot-' + 'b' * 32),
                           ('empty', 1), ('descendants', [456])):
            with self.subTest(field=key):
                original = self.killed[key]
                self.killed[key] = value
                self.records()
                with self.assertRaises(RetentionHeld):
                    _proof(self.attempt, lambda _: True)
                self.killed[key] = original

    def test_dead_parent_or_missing_teardown_is_insufficient(self):
        (self.attempt / 'killed.json').unlink()
        with self.assertRaises(OSError):
            _proof(self.attempt, lambda _: True)

    def test_native_job_check_overrides_claimed_empty_record(self):
        with self.assertRaisesRegex(RetentionHeld, 'active_job'):
            _proof(self.attempt, lambda _: False)

    def test_unknown_native_job_state_is_not_cleanup_authority(self):
        def unknown(_):
            raise PermissionError('cannot query')
        with self.assertRaises(PermissionError):
            _proof(self.attempt, unknown)

    def test_malformed_or_oversized_evidence_is_refused(self):
        for text in ('[]', '{', ' ' * 65537):
            (self.attempt / 'killed.json').write_text(text, encoding='utf-8')
            with self.assertRaises((RetentionHeld, ValueError)):
                _proof(self.attempt, lambda _: True)

    def test_maintenance_requires_exact_instance_and_root_marker(self):
        runs = self.attempt / 'runs'
        runs.mkdir()
        marker = self.attempt / 'environment.json'
        data = {'version': 1, 'instance_id': 'development', 'environment': 'development',
                'local_root': str(self.attempt.resolve())}
        marker.write_text(json.dumps(data), encoding='utf-8')
        self.assertEqual(len(_instance(runs, 'development')), 64)
        with self.assertRaises(RetentionHeld):
            _instance(runs, 'production')
        data['local_root'] = str(self.attempt / 'other')
        marker.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaises(RetentionHeld):
            _instance(runs, 'development')


@unittest.skipUnless(os.name == 'nt', 'native Windows file handles and Job Objects')
class NativeRetentionTests(unittest.TestCase):
    def setUp(self):
        from agent.windows_job import WindowsJob
        self.tmp = tempfile.TemporaryDirectory(prefix='retention spaces 中文 ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runs = self.root / 'state' / 'runs'
        self.attempt = self.runs / 'job' / 'attempt'
        self.bin = self.attempt / 'home' / '.sandbox-bin'
        self.bin.mkdir(parents=True)
        self.source = self.bin / 'codex.exe'
        self.source.write_bytes(b'disposable runtime fixture')
        job = WindowsJob()
        self.job = job.name
        job.close()
        (self.attempt / 'process.json').write_text(json.dumps({'pid': 123, 'windows_job': self.job}))
        (self.attempt / 'killed.json').write_text(json.dumps(
            {'pid': 123, 'windows_job': self.job, 'empty': True, 'descendants': []}))
        for relative in ('home/auth.json', 'home/config.toml', 'stderr.log', 'last_message.txt',
                         'previous-claim.token', 'checkpoint.json', 'home/.sandbox-bin/helper.exe'):
            (self.attempt / relative).write_bytes(b'preserve fixture ' + relative.encode())
        self.saved = {p: p.read_bytes() for p in self.attempt.rglob('*') if p.is_file() and p != self.source}

    def preserved(self):
        for path, content in self.saved.items():
            self.assertEqual(path.read_bytes(), content)

    def test_native_delete_preserves_credentials_logs_and_recovery_evidence(self):
        result = retire_codex_copy(self.runs, self.attempt, apply=True)
        self.assertEqual(result['status'], 'removed', result)
        self.assertFalse(self.source.exists())
        self.preserved()
        receipt = json.loads((self.attempt / 'runtime-retention.json').read_text())
        self.assertTrue(receipt['removed'])
        self.assertEqual(receipt['sha256'], hashlib.sha256(b'disposable runtime fixture').hexdigest())

    def test_dry_run_leaves_every_file_intact(self):
        self.assertEqual(retire_codex_copy(self.runs, self.attempt)['status'], 'eligible')
        self.assertTrue(self.source.exists())
        self.assertFalse((self.attempt / 'runtime-retention.json').exists())
        self.preserved()

    def test_archive_is_verified_and_deduplicated(self):
        archive = self.root / 'archive'
        archive.mkdir()
        for _ in range(2):
            self.assertEqual(retire_codex_copy(self.runs, self.attempt, apply=True,
                                             archive_root=archive)['status'], 'removed')
            self.source.write_bytes(b'disposable runtime fixture')
        files = list(archive.iterdir())
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].read_bytes(), b'disposable runtime fixture')
        self.preserved()

    def test_corrupt_archive_keeps_source(self):
        archive = self.root / 'archive'
        archive.mkdir()
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        (archive / (digest + '.exe')).write_bytes(b'wrong bytes')
        result = retire_codex_copy(self.runs, self.attempt, apply=True, archive_root=archive)
        self.assertEqual(result['reason'], 'archive_hash_mismatch')
        self.assertTrue(self.source.exists())
        self.preserved()

    def test_incomplete_teardown_and_outside_root_never_delete(self):
        self.assertEqual(retire_codex_copy(self.runs, self.root, apply=True)['status'], 'held')
        (self.attempt / 'killed.json').unlink()
        self.assertEqual(retire_codex_copy(self.runs, self.attempt, apply=True)['status'], 'held')
        self.assertTrue(self.source.exists())

    def test_active_real_job_is_preserved_even_with_empty_receipt(self):
        from agent.launcher import Launcher, RUNTIMES
        launcher = Launcher(self.runs, RUNTIMES['fake'], 'test')
        handle = launcher.spawn('active', 'prompt', {}, 30, self.root, extra_env={'FAKE_CLI_MODE': 'sleep'})
        self.addCleanup(launcher.stop, 'active', 2)
        target = handle.run_dir / 'home' / '.sandbox-bin'
        target.mkdir()
        (target / 'codex.exe').write_bytes(b'active fixture')
        (handle.run_dir / 'killed.json').write_text(json.dumps(
            {'pid': handle.pid, 'windows_job': launcher._jobs['active'].name,
             'empty': True, 'descendants': []}))
        result = retire_codex_copy(self.runs, handle.run_dir, apply=True)
        self.assertEqual(result['reason'], 'active_job')
        self.assertTrue((target / 'codex.exe').exists())

    def test_reparse_directory_or_receipt_cannot_redirect_cleanup(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'codex.exe').write_bytes(b'outside fixture')
        self.source.unlink()
        (self.bin / 'helper.exe').unlink()
        self.bin.rmdir()
        self.bin.symlink_to(outside, target_is_directory=True)
        result = retire_codex_copy(self.runs, self.attempt, apply=True)
        self.assertEqual(result['reason'], 'unsafe_path')
        self.assertEqual((outside / 'codex.exe').read_bytes(), b'outside fixture')

    def test_linked_receipt_preserves_source_and_external_file(self):
        outside = self.root / 'outside.txt'
        outside.write_bytes(b'outside evidence')
        (self.attempt / 'runtime-retention.json').symlink_to(outside)
        result = retire_codex_copy(self.runs, self.attempt, apply=True)
        self.assertEqual(result['reason'], 'unsafe_path')
        self.assertTrue(self.source.exists())
        self.assertEqual(outside.read_bytes(), b'outside evidence')

    def test_sweep_refuses_unknown_archive_and_preserves_owned_marker(self):
        state = self.runs.parent
        marker = {'version': 1, 'environment': 'development', 'instance_id': 'test',
                  'local_root': str(state.resolve())}
        (state / 'environment.json').write_text(json.dumps(marker))
        archive = self.root / 'archive'
        archive.mkdir()
        unknown = archive / 'unrelated.txt'
        unknown.write_bytes(b'preserve')
        with self.assertRaisesRegex(RetentionHeld, 'unmarked_archive_not_empty'):
            sweep(self.runs, 'test', apply=True, archive_root=archive)
        self.assertTrue(self.source.exists())
        unknown.unlink()
        result = sweep(self.runs, 'test', apply=True, archive_root=archive)
        self.assertEqual(result['removed'], 1)
        self.assertEqual(result['held'], 0)
        self.preserved()
        self.assertEqual(sweep(self.runs, 'test', apply=True, archive_root=archive)['removed'], 0)

    def test_archive_inside_state_is_refused_without_modifying_it(self):
        archive = self.runs.parent / 'archive'
        archive.mkdir()
        result = retire_codex_copy(self.runs, self.attempt, apply=True, archive_root=archive)
        self.assertEqual(result['reason'], 'archive_must_be_separate')
        self.assertEqual(list(archive.iterdir()), [])
        self.assertTrue(self.source.exists())

    def test_hardlinked_executable_is_preserved(self):
        os.link(self.source, self.root / 'shared.exe')
        self.assertEqual(retire_codex_copy(self.runs, self.attempt, apply=True)['reason'], 'unsafe_path')
        self.assertTrue(self.source.exists())

    def test_open_file_contention_preserves_source(self):
        with _locked_file(self.source):
            result = retire_codex_copy(self.runs, self.attempt, apply=True)
        self.assertEqual(result['status'], 'held')
        self.assertTrue(self.source.exists())

    def test_parent_cannot_be_renamed_during_hash_and_delete(self):
        import agent.runtime_retention as retention
        original = retention._digest
        def racing(stream):
            with self.assertRaises(OSError):
                self.bin.rename(self.root / 'escaped')
            return original(stream)
        with patch.object(retention, '_digest', side_effect=racing):
            self.assertEqual(retire_codex_copy(self.runs, self.attempt, apply=True)['status'], 'removed')
        self.preserved()

    def test_launcher_reclaims_after_native_teardown(self):
        from agent.launcher import Launcher, RUNTIMES
        launcher = Launcher(self.runs, RUNTIMES['fake']._replace(name='codex', seed_files={}), 'test')
        handle = launcher.spawn('complete', 'prompt', {}, 30, self.root)
        target = handle.run_dir / 'home' / '.sandbox-bin'
        target.mkdir()
        source = target / 'codex.exe'
        source.write_bytes(b'finished fixture')
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            result = launcher.poll()
            if result:
                break
            time.sleep(.03)
        self.assertTrue(result)
        self.assertFalse(source.exists())
        launcher.assert_quiescent('complete', handle.pid)

    def test_retention_failure_does_not_prevent_worker_reaping(self):
        from agent.launcher import Launcher, RUNTIMES
        launcher = Launcher(self.runs, RUNTIMES['fake']._replace(name='codex', seed_files={}), 'test')
        handle = launcher.spawn('failed-retention', 'prompt', {}, 30, self.root)
        deadline = time.monotonic() + 10
        output = io.StringIO()
        with redirect_stdout(output), patch('agent.runtime_retention.retire_codex_copy', side_effect=OSError('private path')):
            while time.monotonic() < deadline:
                result = launcher.poll()
                if result:
                    break
                time.sleep(.03)
        self.assertTrue(result)
        self.assertEqual(launcher.running(), {})
        self.assertIn('worker_runtime_retention_held', output.getvalue())
        self.assertNotIn('private path', output.getvalue())
        launcher.assert_quiescent('failed-retention', handle.pid)
