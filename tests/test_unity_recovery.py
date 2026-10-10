import subprocess
import json
import unittest
from pathlib import Path

from agent.identity import source_snapshot
from agent.unity import UnityError, editor_holds_project, other_editor_project
from test_slots import FakeMcp, SlotFixture, git


class RecoveryMcp(FakeMcp):
    def quiescent(self, slot, mode):
        return False

    def recovery_snapshot(self, slot):
        return {'state': {'tests': {'is_running': True}}, 'job': {'status': 'running'}}

    def cancel_tests(self, slot):
        self.calls.append(('cancel', slot['slot_id']))


class EditorRecoveryTests(SlotFixture):
    def add_meta(self):
        meta = self.origin / 'Assets' / '图集.png.meta'
        meta.parent.mkdir(parents=True)
        meta.write_text('enableMipMap: 1\n', encoding='utf-8')
        git('add', '.', cwd=self.origin)
        git('commit', '-qm', 'add metadata', cwd=self.origin)
        return meta.relative_to(self.origin)

    def setup_repair(self, mcp=None):
        from agent.unity_recovery import UnityRecovery
        (self.origin / '.gitignore').write_text('Temp/\nLibrary/\nLogs/\n', encoding='utf-8')
        git('add', '.gitignore', cwd=self.origin)
        git('commit', '-qm', 'ignore Unity state', cwd=self.origin)
        mcp = mcp or RecoveryMcp()
        pool = self.pool(mcp=mcp)
        slot = pool.ensure()[0]
        mcp.start(slot, self.entry)
        self.ledger.set_slot_state(slot['slot_id'], 'held')
        record = {'slot_id': slot['slot_id'], 'host': 'test', 'commit_sha': slot['parked_commit']}
        return UnityRecovery(pool), pool, mcp, record

    def test_restart_removes_dead_lock_and_checks_identity_before_slot_can_be_reused(self):
        adapter, pool, mcp, r = self.setup_repair()
        commit, instance = adapter.repair(r)
        self.assertEqual(commit, self.trees.head(pool.folder(self.entry)))
        self.assertEqual(instance, mcp.instance)
        self.assertEqual(self.ledger.slot(r['slot_id'])['state'], 'held')
        calls = [name for name, _ in mcp.calls]
        self.assertLess(calls.index('cancel'), calls.index('terminate'))
        self.assertLess(calls.index('terminate'), calls.index('probe'))
        self.assertNotIn('reap_server', calls)  # a recovery never kills the shared broker

    def test_surviving_editor_prevents_lock_removal_and_second_launch(self):
        class Survivor(RecoveryMcp):
            def terminate(self, slot, timeout):
                pass
        adapter, pool, mcp, r = self.setup_repair(Survivor())
        relative = Path('Assets') / 'pending.png.meta'
        (pool.folder(self.entry) / relative).parent.mkdir(parents=True)
        (pool.folder(self.entry) / relative).write_text('enableMipMap: 0\n', encoding='utf-8')
        with self.assertRaises(Exception):
            adapter.repair(dict(r, id='survivor', evidence_dir=str(self.root / 'recovery')))
        self.assertTrue((pool.folder(self.entry) / 'Temp/UnityLockfile').exists())
        self.assertTrue((pool.folder(self.entry) / relative).exists())
        self.assertFalse((self.root / 'recovery' / 'source-meta.json').exists())
        self.assertEqual(sum(name == 'start' for name, _ in mcp.calls), 1)

    def test_wrong_identity_keeps_slot_quarantined(self):
        adapter, _, mcp, r = self.setup_repair(RecoveryMcp(ready=False))
        with self.assertRaisesRegex(Exception, 'identity'):
            adapter.repair(r)
        self.assertEqual(self.ledger.slot(r['slot_id'])['state'], 'held')

    def test_successful_cooperative_stop_reuses_verified_editor(self):
        adapter, _, mcp, r = self.setup_repair()
        mcp.quiescent = lambda slot, mode: True
        adapter.repair(r)
        self.assertNotIn(('terminate', r['slot_id']), mcp.calls)
        self.assertEqual(sum(name == 'start' for name, _ in mcp.calls), 1)

    def test_failed_cooperative_reuse_escalates_to_restart_on_next_attempt(self):
        adapter, _, mcp, r = self.setup_repair()
        mcp.quiescent = lambda slot, mode: True
        mcp.console_errors_since = lambda slot, commit: ['old exception'] if sum(name == 'start' for name, _ in mcp.calls) == 1 else []
        with self.assertRaisesRegex(Exception, 'old exception'):
            adapter.repair(dict(r, attempts=1))
        adapter.repair(dict(r, attempts=2))
        self.assertIn(('terminate', r['slot_id']), mcp.calls)

    def test_changed_configured_folder_is_never_signalled(self):
        adapter, pool, mcp, r = self.setup_repair()
        self.ledger.connection.execute('UPDATE slots SET folder=? WHERE slot_id=?',
                                       (str(self.root / 'personal-project'), r['slot_id']))
        with self.assertRaisesRegex(Exception, 'configured'):
            adapter.repair(r)
        self.assertNotIn(('terminate', r['slot_id']), mcp.calls)

    def test_tracked_importer_metadata_is_archived_and_slot_stays_closed(self):
        from agent.unity_recovery import SourceMetaReconciled
        relative = self.add_meta()
        adapter, pool, mcp, record = self.setup_repair()
        slot_path = pool.folder(self.entry)
        (slot_path / relative).write_text('enableMipMap: 0\n', encoding='utf-8')
        evidence = self.root / 'resource recovery' / 'tracked'
        with self.assertRaises(SourceMetaReconciled) as caught:
            adapter.repair(dict(record, id='tracked', evidence_dir=str(evidence)))
        self.assertEqual((slot_path / relative).read_text(encoding='utf-8'), 'enableMipMap: 1\n')
        self.assertEqual(source_snapshot(slot_path)['dirty'], [])
        self.assertFalse(mcp.open_folders)
        self.assertEqual(sum(name == 'start' for name, _ in mcp.calls), 1)
        self.assertEqual(self.ledger.slot(record['slot_id'])['state'], 'held')
        saved = subprocess.run(['git', 'show', f'refs/farmbot/slot-recovery/tracked:{relative.as_posix()}'],
                               cwd=slot_path, capture_output=True, text=True, check=True).stdout
        self.assertEqual(saved, 'enableMipMap: 0\n')
        manifest = json.loads((evidence / 'source-meta.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['tracked_ref'], caught.exception.evidence['tracked_ref'])

    def test_large_metadata_rewrite_is_archived_and_restored_with_literal_unicode_paths(self):
        from agent.unity_recovery import SourceMetaReconciled
        relative_root = Path('Assets') / '资源 图集 [test]'
        (self.origin / relative_root).mkdir(parents=True)
        paths = [relative_root / f'texture {index:04d}.png.meta' for index in range(1832)]
        for relative in paths:
            (self.origin / relative).write_text('textureCompression: 1\n', encoding='utf-8')
        git('add', '.', cwd=self.origin)
        git('commit', '-qm', 'large atlas', cwd=self.origin)
        adapter, pool, mcp, record = self.setup_repair()
        slot_path = pool.folder(self.entry)
        for relative in paths:
            (slot_path / relative).write_text('textureCompression: 0\n', encoding='utf-8')
        evidence = self.root / 'resource recovery' / 'large'
        with self.assertRaises(SourceMetaReconciled) as caught:
            adapter.repair(dict(record, id='large', evidence_dir=str(evidence)))
        self.assertEqual(source_snapshot(slot_path)['dirty'], [])
        self.assertFalse(mcp.open_folders)
        manifest = json.loads((evidence / 'source-meta.json').read_text(encoding='utf-8'))
        self.assertEqual(len(manifest['changes']), 1832)
        saved = subprocess.run(['git', 'show', f"{caught.exception.evidence['tracked_ref']}:{paths[-1].as_posix()}"],
                               cwd=slot_path, capture_output=True, text=True, check=True).stdout
        self.assertEqual(saved, 'textureCompression: 0\n')
        self.assertEqual((slot_path / paths[-1]).read_text(encoding='utf-8'), 'textureCompression: 1\n')

    def late_editor(self):
        mcp = FakeMcp()
        adapter, pool, _, record = self.setup_repair(mcp)
        def discover(slot):
            mcp.calls.append(('discover', slot['slot_id']))
            return mcp.instance
        mcp.discover_instance = discover
        probe = mcp.probe
        def pinned_probe(slot, target):
            self.assertEqual(slot['instance'], mcp.instance)
            return {**probe(slot, target), 'editor': {'instance': slot['instance']}}
        mcp.probe = pinned_probe
        return adapter, pool, mcp, record

    def test_late_editor_is_revalidated_without_start_stop_refresh_or_source_changes(self):
        adapter, pool, mcp, record = self.late_editor()
        mcp.calls.clear()
        before = source_snapshot(pool.folder(self.entry))
        self.assertEqual(adapter.revalidate(record), (record['commit_sha'], mcp.instance))
        self.assertEqual(source_snapshot(pool.folder(self.entry)), before)
        self.assertEqual([name for name, _ in mcp.calls], ['discover', 'quiescent', 'console', 'probe'])
        self.assertEqual(self.ledger.slot(record['slot_id'])['state'], 'held')
        self.assertIsNone(self.ledger.slot(record['slot_id'])['instance'])

    def test_late_editor_without_a_matching_connected_instance_stays_quarantined(self):
        adapter, _, mcp, record = self.late_editor()
        mcp.discover_instance = lambda slot: None
        with self.assertRaisesRegex(Exception, 'no connected project instance'):
            adapter.revalidate(record)
        mcp.discover_instance = lambda slot: mcp.instance
        mcp.probe = lambda slot, target: {'aggregate': 'match', 'editor': {'instance': 'other-project'}}
        with self.assertRaisesRegex(Exception, 'matching verified instance'):
            adapter.revalidate(record)
        self.assertEqual(self.ledger.slot(record['slot_id'])['state'], 'held')

    def test_late_editor_wrong_commit_or_dirty_source_is_never_reused(self):
        adapter, pool, mcp, record = self.late_editor()
        with self.assertRaisesRegex(Exception, 'clean at the recovery commit'):
            adapter.revalidate(dict(record, commit_sha='b' * 40))
        (pool.folder(self.entry) / 'README.md').write_text('unexpected edit', encoding='utf-8')
        with self.assertRaisesRegex(Exception, 'clean at the recovery commit'):
            adapter.revalidate(record)
        self.assertFalse(any(name == 'probe' for name, _ in mcp.calls))

    def test_late_editor_not_quiet_or_wrong_identity_stays_quarantined(self):
        adapter, _, mcp, record = self.late_editor()
        mcp.quiescent = lambda slot, mode: False
        with self.assertRaisesRegex(Exception, 'not idle'):
            adapter.revalidate(record)
        mcp.quiescent = lambda slot, mode: True
        mcp.ready = False
        with self.assertRaisesRegex(Exception, 'identity'):
            adapter.revalidate(record)
        self.assertEqual(self.ledger.slot(record['slot_id'])['state'], 'held')

    def test_new_importer_metadata_is_backed_up_before_removal(self):
        from agent.unity_recovery import SourceMetaReconciled
        adapter, pool, _, record = self.setup_repair()
        slot_path = pool.folder(self.entry)
        relative = Path('Assets') / 'new.png.meta'
        (slot_path / relative).parent.mkdir(parents=True)
        (slot_path / relative).write_bytes(b'enableMipMap: 0\n')
        evidence = self.root / 'resource recovery' / 'untracked'
        with self.assertRaises(SourceMetaReconciled):
            adapter.repair(dict(record, id='untracked', evidence_dir=str(evidence)))
        self.assertFalse((slot_path / relative).exists())
        self.assertEqual((evidence / 'untracked-meta' / relative).read_bytes(), b'enableMipMap: 0\n')
        self.assertEqual(source_snapshot(slot_path)['dirty'], [])

    def test_recovery_uses_identical_ref_left_by_interrupted_attempt(self):
        from agent.unity_recovery import SourceMetaReconciled
        relative = self.add_meta()
        adapter, pool, _, record = self.setup_repair()
        slot_path = pool.folder(self.entry)
        (slot_path / relative).write_text('enableMipMap: 0\n', encoding='utf-8')
        saved = subprocess.run(['git', '-c', 'user.name=FarmBot', '-c', 'user.email=farmbot@localhost',
                                'stash', 'create'], cwd=slot_path, capture_output=True, text=True,
                               check=True).stdout.strip()
        git('update-ref', 'refs/farmbot/slot-recovery/interrupted', saved, cwd=slot_path)
        with self.assertRaises(SourceMetaReconciled):
            adapter.repair(dict(record, id='interrupted',
                                evidence_dir=str(self.root / 'recovery' / 'interrupted')))
        self.assertEqual(source_snapshot(slot_path)['dirty'], [])

    def test_non_metadata_change_remains_quarantined(self):
        adapter, pool, _, record = self.setup_repair()
        slot_path = pool.folder(self.entry)
        (slot_path / 'README.md').write_text('unexpected edit', encoding='utf-8')
        evidence = self.root / 'resource recovery' / 'other'
        with self.assertRaisesRegex(Exception, 'metadata-only'):
            adapter.repair(dict(record, id='other', evidence_dir=str(evidence)))
        self.assertEqual((slot_path / 'README.md').read_text(encoding='utf-8'), 'unexpected edit')
        self.assertFalse((evidence / 'source-meta.json').exists())
        self.assertEqual(self.ledger.slot(record['slot_id'])['state'], 'held')


class StrictEditorInspectionTests(unittest.TestCase):
    def test_outside_editor_scan_does_not_treat_a_timeout_as_no_editor(self):
        def unavailable():
            raise subprocess.TimeoutExpired('process listing', 5)
        with self.assertRaises(UnityError):
            other_editor_project(Path('slot'), run=unavailable, strict=True)

    def test_import_helpers_do_not_hide_main_editor_and_still_hold_project_after_it_exits(self):
        folder = Path('slot').resolve()
        main = f'123 Unity -projectPath "{folder}"\n'
        helpers = (f'456 Unity "-name" "AssetImportWorker0" "-projectPath" "{folder}" "-logFile" "worker.log"\n'
                   f'789 Unity "-name" "AssetImportWorker1" "-projectPath" "{folder}"\n')
        self.assertEqual(editor_holds_project(folder, run=lambda: main + helpers, strict=True), 123)
        self.assertIn(editor_holds_project(folder, run=lambda: helpers, strict=True), (456, 789))

    def test_process_inspection_failure_is_not_proof_the_editor_exited(self):
        def unavailable():
            raise subprocess.TimeoutExpired('process listing', 5)
        with self.assertRaises(UnityError):
            editor_holds_project(Path('slot'), run=unavailable, strict=True)

    def test_project_path_with_spaces_is_matched_exactly(self):
        folder = Path('a folder') / 'slot'
        listing = f'123 Unity -projectPath "{folder.resolve()}" -logFile logfile\n'
        self.assertEqual(editor_holds_project(folder, run=lambda: listing, strict=True), 123)

    def test_ambiguous_multiple_editors_refuses_recovery(self):
        folder = Path('slot')
        listing = f'123 Unity -projectPath "{folder.resolve()}"\n456 Unity -projectPath "{folder.resolve()}"\n'
        with self.assertRaises(UnityError):
            editor_holds_project(folder, run=lambda: listing, strict=True)

    def test_flattened_unquoted_unix_path_is_not_proof_editor_exited(self):
        folder = Path('a folder') / 'slot'
        listing = f'123 Unity -projectPath {folder.resolve()} -logFile logfile\n'
        with self.assertRaises(UnityError):
            editor_holds_project(folder, system='Linux', run=lambda: listing, strict=True)


if __name__ == '__main__':
    unittest.main()
