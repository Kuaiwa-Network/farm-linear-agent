"""Native importer edits already committed by the owner can be recovered without data loss."""
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from agent.identity import source_snapshot
from agent.worktrees import WorktreeError
from test_slots import SlotFixture, git


class CommittedSourceTests(SlotFixture):
    def setUp(self):
        super().setUp()
        self.relative = Path('Assets/幕布 [curtain]/Atlas.asset')
        asset = self.origin / self.relative
        asset.parent.mkdir(parents=True)
        asset.write_bytes(b'm_EditorClassIdentifier:\n')
        git('add', '.', cwd=self.origin)
        git('commit', '-qm', 'original serialization', cwd=self.origin)
        self.old = self.trees.head(self.origin)
        self.corrected_bytes = b'm_EditorClassIdentifier: \n'
        asset.write_bytes(self.corrected_bytes)
        git('commit', '-qam', 'native serialization', cwd=self.origin)
        self.corrected = self.trees.resolve_commit('Farm-Client')
        self.slot = self.trees.add_slot('Farm-Client', self.root / 'slot 空间', self.old)
        self.asset = self.slot / self.relative
        self.asset.write_bytes(self.corrected_bytes)
        self.evidence = self.root / 'evidence'

    def reconcile(self):
        return self.trees.reconcile_slot_meta('Farm-Client', self.slot, 'corrected-source', self.evidence,
                                              committed_candidate=self.corrected)

    def test_exact_committed_asset_is_archived_before_restore(self):
        result = self.reconcile()
        manifest = json.loads(Path(result['manifest']).read_text(encoding='utf-8'))
        self.assertEqual(Path(result['manifest']).name, 'source-candidate.json')
        self.assertEqual(manifest['committed_candidate'], self.corrected)
        saved = subprocess.check_output(['git', 'show', f"{result['tracked_ref']}:{self.relative.as_posix()}"],
                                        cwd=self.slot)
        self.assertEqual(saved, self.corrected_bytes)
        self.assertFalse(source_snapshot(self.slot)['dirty'])
        self.trees.checkout_commit(self.slot, self.corrected)
        self.assertEqual(self.asset.read_bytes(), self.corrected_bytes)

    def test_no_correction_keeps_the_existing_non_metadata_refusal(self):
        self.assertIsNone(self.trees.reconcile_slot_meta('Farm-Client', self.slot, 'ordinary', self.evidence))
        self.assertEqual(self.asset.read_bytes(), self.corrected_bytes)

    def test_extra_bytes_and_eol_normalization_are_not_exact_matches(self):
        for data in (self.corrected_bytes + b'other edit\n', self.corrected_bytes.replace(b'\n', b'\r\n')):
            with self.subTest(data=data):
                self.asset.write_bytes(data)
                self.assertIsNone(self.reconcile())
                self.assertEqual(self.asset.read_bytes(), data)

    def test_untracked_addition_blocks_the_entire_restore(self):
        (self.slot / 'Assets/new.mat').write_bytes(b'unknown')
        self.assertIsNone(self.reconcile())
        self.assertEqual(self.asset.read_bytes(), self.corrected_bytes)

    def test_staged_intermediate_content_is_preserved(self):
        self.asset.write_bytes(b'important staged version\n')
        git('add', '.', cwd=self.slot)
        self.asset.write_bytes(self.corrected_bytes)
        before = source_snapshot(self.slot)
        self.assertIsNone(self.reconcile())
        self.assertEqual(source_snapshot(self.slot), before)

    def test_deleted_file_is_not_discarded(self):
        self.asset.unlink()
        self.assertIsNone(self.reconcile())
        self.assertFalse(self.asset.exists())

    def test_hardlinked_file_is_refused(self):
        self.asset.unlink()
        other = self.root / 'outside.asset'
        other.write_bytes(self.corrected_bytes)
        os.link(other, self.asset)
        self.assertIsNone(self.reconcile())
        self.assertEqual(other.read_bytes(), self.corrected_bytes)

    def test_candidate_symlink_mode_does_not_authorize_regular_file_cleanup(self):
        oid = subprocess.check_output(['git', 'rev-parse', f'{self.corrected}:{self.relative.as_posix()}'],
                                      cwd=self.origin, text=True).strip()
        git('update-index', '--cacheinfo', f'120000,{oid},{self.relative.as_posix()}', cwd=self.origin)
        git('commit', '-qm', 'candidate link instead of asset', cwd=self.origin)
        self.corrected = self.trees.resolve_commit('Farm-Client')
        self.assertIsNone(self.reconcile())
        self.assertEqual(self.asset.read_bytes(), self.corrected_bytes)

    @unittest.skipIf(os.name == 'nt', 'POSIX executable mode; Windows uses Git index modes')
    def test_uncommitted_executable_bit_is_preserved(self):
        self.asset.chmod(self.asset.stat().st_mode | 0o111)
        self.assertIsNone(self.reconcile())
        self.assertTrue(self.asset.stat().st_mode & 0o111)

    def test_changes_during_archival_are_retained(self):
        import agent.worktrees as module
        original = module._git
        def git_call(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[:2] == ('update-ref', 'refs/farmbot/slot-recovery/corrected-source'):
                self.asset.write_bytes(b'changed during archive\n')
            return result
        with patch.object(module, '_git', side_effect=git_call), self.assertRaisesRegex(WorktreeError, 'changed while'):
            self.reconcile()
        self.assertEqual(self.asset.read_bytes(), b'changed during archive\n')

    def test_archive_failure_does_not_restore_source(self):
        import agent.worktrees as module
        original = module._git
        def git_call(*args, **kwargs):
            if 'stash' in args:
                raise WorktreeError('archive failed')
            return original(*args, **kwargs)
        with patch.object(module, '_git', side_effect=git_call), self.assertRaisesRegex(WorktreeError, 'archive failed'):
            self.reconcile()
        self.assertEqual(self.asset.read_bytes(), self.corrected_bytes)
