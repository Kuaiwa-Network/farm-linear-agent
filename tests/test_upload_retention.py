"""ZIP cache expiry preserves evidence and uses the existing native teardown gate."""
import hashlib
import json
import os
import time
import unittest
from unittest.mock import patch

from agent import uploads
from agent.launcher import Launcher, RUNTIMES
from agent.worker_retention import Controller, retire_attempt
from test_uploads import FakeUploads, ART, LOG, an_issue, archive
from test_worker_retention import _RetentionFixture


class UploadRetentionTests(_RetentionFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.cache = self.attempt.parent / 'uploads'
        self.cache.mkdir()
        self.issue = an_issue(f'[资源 包.zip]({ART}) [需求.md]({LOG})')
        self.api = FakeUploads({ART: archive({'子目录/素材.png': b'original image',
                                             '说明.txt': b'archive member'}), LOG: b'requirements'})
        uploads.download_issue_uploads(self.api, self.issue, self.cache)
        self.manifest = self.cache / 'manifest.json'
        self.original_manifest = self.manifest.read_bytes()
        self.records = json.loads(self.original_manifest)['files']
        self.archive = next(e for e in self.records if e['name'].endswith('.zip'))
        self.members = [e for e in self.records if e['source_zip']]
        self.targets = [self.cache / e['name'] for e in [self.archive, *self.members]]
        for path in (self.cache / '核查报告.md', self.cache / self.archive['extract_dir'] / '对照图.png'):
            path.write_bytes(b'derived evidence')
            self.durable[path] = path.read_bytes()
        self.durable[self.manifest] = self.original_manifest
        self.durable[self.cache / '需求.md'] = b'requirements'

    def edit_manifest(self, change):
        data = json.loads(self.original_manifest)
        change(data)
        self.manifest.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

    def test_zip_and_members_expire_but_inventory_hashes_and_reports_survive(self):
        result = self.clean()
        self.assertEqual(result['status'], 'complete', result)
        self.assertEqual(result['files'], len(self.caches) + len(self.targets))
        self.assertFalse(any(p.exists() for p in self.targets))
        self.preserved()
        receipt = json.loads((self.attempt / 'cache-retention.json').read_text())
        self.assertEqual(receipt['upload_cache'], {
            'root': 'uploads', 'manifest_sha256': hashlib.sha256(self.original_manifest).hexdigest(),
            'candidates': len(self.targets)})

    def test_grace_period_is_at_least_24_hours_after_latest_attempt_exit(self):
        ended = self.attempt / 'killed.json'
        now = time.time()
        os.utime(ended, (now, now))
        before = retire_attempt(self.runs, self.attempt, now=now + 24 * 3600 - 1, apply=True)
        self.assertEqual(before['status'], 'young')
        self.assertTrue(all(p.exists() for p in self.targets))
        after = retire_attempt(self.runs, self.attempt, now=now + 24 * 3600 + 1, apply=True)
        self.assertEqual(after['status'], 'complete', after)
        self.assertFalse(any(p.exists() for p in self.targets))

    def test_resume_restarts_job_cache_grace(self):
        sibling = self.attempt.parent / 'recent-attempt'
        sibling.mkdir()
        self.evidence(sibling)
        os.utime(sibling / 'killed.json', (self.now, self.now))
        self.clean()
        self.assertTrue(all(p.exists() for p in self.targets))
        self.now += 25 * 3600
        self.assertEqual(self.clean()['files'], len(self.targets))

    def test_active_and_uncertified_attempts_preserve_uploads(self):
        launcher = Launcher(self.runs, RUNTIMES['fake'], 'test')
        controller = Controller(launcher, {}, clock=lambda: self.now)
        self.addCleanup(controller.close)
        launcher._handles['item'] = object()
        controller.tick()
        self.assertTrue(all(p.exists() for p in self.targets))
        (self.attempt / 'killed.json').unlink()
        self.assertEqual(self.clean()['status'], 'held')
        self.assertTrue(all(p.exists() for p in self.targets))

    def test_changed_file_and_recent_file_are_preserved(self):
        self.targets[1].write_bytes(b'X' * self.members[0]['size'])
        os.utime(self.targets[2], (self.now, self.now))
        result = self.clean()
        self.assertEqual(result['status'], 'complete', result)
        self.assertFalse(self.targets[0].exists())
        self.assertTrue(all(p.exists() for p in self.targets[1:]))
        self.preserved()

    def test_recent_manifest_defers_all_uploads(self):
        os.utime(self.manifest, (self.now, self.now))
        self.clean()
        self.assertTrue(all(p.exists() for p in self.targets))

    def test_dry_run_batch_resume_and_repeat_are_safe(self):
        result = retire_attempt(self.runs, self.attempt, now=self.now)
        self.assertEqual(result['files'], len(self.caches) + len(self.targets))
        self.assertTrue(all(p.exists() for p in self.targets))
        self.assertFalse((self.attempt / 'cache-retention.json').exists())
        total = 0
        for _ in range(20):
            result = self.clean(limit=1)
            self.assertLessEqual(result['files'], 1)
            total += result['files']
            if result['status'] == 'complete':
                break
        self.assertEqual(total, len(self.caches) + len(self.targets))
        self.assertEqual(self.clean()['files'], 0)
        self.preserved()

    def test_download_helper_refetches_expired_cache(self):
        self.clean()
        self.api.calls.clear()
        result = uploads.download_issue_uploads(self.api, self.issue, self.cache)
        self.assertEqual(result['counts']['downloaded'], 1)
        self.assertEqual(self.api.calls, [ART])
        self.assertTrue(all(p.exists() for p in self.targets))
        self.assertEqual(self.manifest.read_bytes(), self.original_manifest)
        self.preserved()

    def test_missing_or_malformed_manifest_never_guesses_files(self):
        for body in (b'{', b'{}', b'{"format":99,"files":[]}'):
            self.manifest.write_bytes(body)
            self.assertEqual(self.clean()['status'], 'held')
            self.assertTrue(all(p.exists() for p in self.targets))
        self.manifest.unlink()
        self.assertEqual(self.clean()['status'], 'complete')
        self.assertTrue(all(p.exists() for p in self.targets))

    def test_unsafe_and_ambiguous_manifest_paths_are_refused(self):
        for name in ('../checkpoint.json', 'C:/outside.zip', 'a:stream', 'uploads\\x.zip', 'manifest.json'):
            with self.subTest(name=name):
                self.edit_manifest(lambda d: d['files'][0].update(name=name))
                self.assertEqual(self.clean()['status'], 'held')
                self.assertTrue(all(p.exists() for p in self.targets))
        self.edit_manifest(lambda d: d['files'].append(d['files'][0].copy()))
        self.assertEqual(self.clean()['status'], 'held')

    def test_members_must_belong_to_the_recorded_archive_directory(self):
        self.edit_manifest(lambda d: next(e for e in d['files'] if e['source_zip']).update(name='核查报告.md'))
        self.assertEqual(self.clean()['status'], 'held')
        self.assertEqual((self.cache / '核查报告.md').read_bytes(), b'derived evidence')
        self.assertTrue(all(p.exists() for p in self.targets))

    def test_unknown_custom_download_directory_is_preserved(self):
        custom = self.attempt.parent / 'project-art'
        self.cache.rename(custom)
        self.assertEqual(self.clean()['status'], 'complete')
        self.assertTrue(all((custom / p.relative_to(self.cache)).exists() for p in self.targets))

    def test_manifest_symlink_or_hardlink_is_held(self):
        original = self.root / 'protected.json'
        original.write_bytes(self.original_manifest)
        self.manifest.unlink()
        for link in (lambda: self.manifest.symlink_to(original), lambda: os.link(original, self.manifest)):
            link()
            self.assertEqual(self.clean()['status'], 'held')
            self.assertTrue(all(p.exists() for p in self.targets))
            self.manifest.unlink()
        self.assertEqual(original.read_bytes(), self.original_manifest)

    def test_upload_root_symlink_cannot_escape_job(self):
        outside = self.root / 'outside'
        self.cache.rename(outside)
        self.cache.symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.clean()['status'], 'held')
        self.assertTrue(all((outside / p.relative_to(self.cache)).exists() for p in self.targets))

    def test_member_symlink_hardlink_and_parent_symlink_are_held(self):
        target = self.targets[1]
        protected = self.root / 'original.png'
        protected.write_bytes(target.read_bytes())
        target.unlink()
        for link in (lambda: target.symlink_to(protected), lambda: os.link(protected, target)):
            link()
            self.assertEqual(self.clean()['status'], 'held')
            self.assertTrue(protected.exists())
            target.unlink()
        parent = target.parent
        outside = self.root / 'outside-member'
        parent.rename(outside)
        (outside / target.name).write_bytes(protected.read_bytes())
        parent.symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.clean()['status'], 'held')
        self.assertTrue((outside / target.name).exists())

    def test_audit_failure_preserves_all_downloads(self):
        with patch('agent.worker_retention._receipt', side_effect=OSError('unavailable')):
            self.assertEqual(self.clean()['status'], 'held')
        self.assertTrue(all(p.exists() for p in self.targets))

    def test_hash_cancellation_preserves_target(self):
        from agent.upload_retention import _unchanged
        def cancelled(stream, entry, batch):
            batch.stop = lambda: True
            return _unchanged(stream, entry, batch)
        with patch('agent.upload_retention._unchanged', side_effect=cancelled):
            result = self.clean()
        self.assertEqual(result['reason'], 'stopping')
        self.assertTrue(all(p.exists() for p in self.targets))

    def test_oversized_manifest_is_held_without_deleting_downloads(self):
        with patch('agent.upload_retention.MANIFEST_LIMIT', 10):
            result = self.clean()
        self.assertEqual(result['reason'], 'oversized_upload_manifest')
        self.assertTrue(all(p.exists() for p in self.targets))

    @unittest.skipUnless(os.name == 'nt', 'Windows locked handles')
    def test_native_open_download_prevents_deletion(self):
        from agent.runtime_retention import _locked_file
        with _locked_file(self.targets[0]):
            self.assertEqual(self.clean()['status'], 'held')
        self.assertTrue(all(p.exists() for p in self.targets))

    @unittest.skipUnless(os.name == 'nt', 'Windows locked handles')
    def test_native_manifest_and_ancestors_stay_pinned_during_deletion(self):
        from agent.upload_retention import retire_uploads
        def checked(plan, batch):
            with self.assertRaises(OSError):
                self.cache.rename(self.root / 'moved')
            with self.assertRaises(OSError):
                self.manifest.write_bytes(b'changed')
            return retire_uploads(plan, batch)
        with patch('agent.upload_retention.retire_uploads', side_effect=checked):
            self.assertEqual(self.clean()['status'], 'complete')
        self.preserved()


if __name__ == '__main__':
    unittest.main()
