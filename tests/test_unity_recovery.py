import subprocess
import unittest
from pathlib import Path

from agent.unity import UnityError, editor_holds_project
from test_slots import FakeMcp, SlotFixture


class RecoveryMcp(FakeMcp):
    def quiescent(self, slot, mode):
        return False

    def recovery_snapshot(self, slot):
        return {'state': {'tests': {'is_running': True}}, 'job': {'status': 'running'}}

    def cancel_tests(self, slot):
        self.calls.append(('cancel', slot['slot_id']))


class EditorRecoveryTests(SlotFixture):
    def setup_repair(self, mcp=None):
        from agent.unity_recovery import UnityRecovery
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
        with self.assertRaises(Exception):
            adapter.repair(r)
        self.assertTrue((pool.folder(self.entry) / 'Temp/UnityLockfile').exists())
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


class StrictEditorInspectionTests(unittest.TestCase):
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
