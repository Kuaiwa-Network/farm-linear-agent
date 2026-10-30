"""Candidate input failures preserve source, free shared Editors and resume scoped code work."""
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from agent.identity import CHECKS, source_snapshot
from agent.ledger import LedgerError
from agent.resource_recovery import RecoveryController, RecoveryStore
from agent.slots import UnityIdentity
import test_fixed_unity_pool as fixed
from test_ledger import ISSUE, OTHER, SKILLS
from test_slots import FakeMcp, SlotFixture, git


class CandidateMcp(FakeMcp):
    failed_commit = None
    metadata = Path('Assets/纹理 [atlas].png.meta')

    def refresh(self, slot):
        super().refresh(slot)
        if source_snapshot(slot['folder'])['commit_sha'] == self.failed_commit:
            (Path(slot['folder']) / self.metadata).write_text('guid: preserved\nenableMipMap: 0\n', encoding='utf-8')

    def probe(self, slot, target):
        super().probe(slot, target)
        snapshot = source_snapshot(slot['folder'])
        checks = {key: 'match' for key in CHECKS}
        checks['source_clean'] = 'mismatch' if snapshot['dirty'] else 'match'
        return {'aggregate': 'mismatch' if snapshot['dirty'] else 'match', 'checks': checks,
                'expected': target, 'source_after': snapshot}

    def discover_instance(self, slot):
        return self.instance

    def cancel_tests(self, slot):
        self.calls.append(('cancel', slot['slot_id']))

    def recovery_snapshot(self, slot):
        return {'state': {'tests': {'is_running': False}}}

    @contextmanager
    def suspend_imports(self, slot):
        self.calls.append(('disallow_refresh', slot['slot_id']))
        try:
            yield
        finally:
            self.calls.append(('allow_refresh', slot['slot_id']))


class CandidateRepairTests(SlotFixture):
    fixed_pool = fixed.FixedEditorPoolTests.fixed_pool

    def test_warm_editor_console_failure_uses_environment_recovery(self):
        mcp = CandidateMcp(console_errors=['FMOD: Error initializing output device (60)'])
        pool = self.fixed_pool(1, mcp=mcp)
        pool.ensure()
        [record] = RecoveryStore(self.ledger).pending('test')
        self.assertEqual(record['recovery_kind'], 'environment')
        self.assertIsNone(record['item_id'])
        self.assertEqual(self.ledger.slot('unity_slot:1')['state'], 'held')

    def test_console_environment_failure_recovers_and_regrants_the_unchanged_candidate(self):
        self.candidates()
        self.mcp.failed_commit = None  # Clean source; the audio device is the only failure.
        self.mcp.console_errors = ['FMOD failed to switch back to normal output ... Error initializing output device (60)']
        item = self.waiting(ISSUE, self.bad)
        self.assertEqual(self.pool.grant(), 0)
        store = RecoveryStore(self.ledger)
        [record] = store.pending('test')
        self.assertEqual(record['recovery_kind'], 'environment')
        self.assertFalse(store.rejected_candidate(item, self.ledger.item(item)['generation'], self.bad))
        self.assertIsNone(store.known_candidate('test', ['unity_slot:1'], self.bad))
        self.assertEqual(self.ledger.slot('unity_slot:1')['state'], 'held')

        # A new process has a working device. The old process must not be reused
        # even if it looks idle or a refresh would clear its error.
        original_start = self.mcp.start
        def start(slot, entry):
            self.mcp.console_errors = []
            return original_start(slot, entry)
        self.mcp.start = start
        self.assertEqual(self.controller().tick()['repaired'], 1)
        self.assertIn(('terminate', 'unity_slot:1'), self.mcp.calls)
        self.assertEqual(store.job_attempts(item, recovery_kind='setup'), 1)
        self.assertEqual(store.job_attempts(item), 0)
        self.assertIsNone(self.ledger.item(item)['candidate_repair'])
        [replacement] = self.ledger.reservations(states=('queued',))
        self.assertEqual(replacement['commit_sha'], self.bad)
        self.assertEqual(self.pool.grant(), 1)
        self.assertEqual(self.ledger.active_reservation(item)['commit_sha'], self.bad)

    def waiting(self, issue_id, commit, mode='interactive'):
        item = fixed.FixedEditorPoolTests.waiting(self, issue_id, commit, mode)
        self.ledger.connection.execute("UPDATE work_items SET root_repo='Farm-Client' WHERE id=?", (item,))
        return item

    def candidates(self):
        (self.origin / 'Assets').mkdir(exist_ok=True)
        self.meta = CandidateMcp.metadata
        (self.origin / self.meta).write_text('guid: preserved\nenableMipMap: 0\n', encoding='utf-8')
        (self.origin / '.gitignore').write_text('Temp/\nLibrary/\nLogs/\n', encoding='utf-8')
        git('add', '.', cwd=self.origin)
        git('commit', '-qm', 'clean main', cwd=self.origin)
        self.main = self.trees.head(self.origin)
        git('checkout', '-qb', 'stale-candidate', cwd=self.origin)
        (self.origin / self.meta).write_text('guid: preserved\nenableMipMap: 1\n', encoding='utf-8')
        git('commit', '-qam', 'stale import inputs', cwd=self.origin)
        self.bad = self.trees.head(self.origin)
        git('checkout', 'main', cwd=self.origin)
        self.mcp = CandidateMcp()
        self.mcp.failed_commit = self.bad
        self.pool = self.fixed_pool(1, mcp=self.mcp)
        self.pool.ensure()

    def fail_candidate(self):
        self.candidates()
        self.item = self.waiting(ISSUE, self.bad)
        self.other = self.waiting(OTHER, self.main)
        self.assertEqual(self.pool.grant(), 0)
        self.store = RecoveryStore(self.ledger)
        self.record = self.store.pending('test')[0]
        self.assertEqual(self.record['recovery_kind'], 'candidate')
        self.assertEqual(self.ledger.item(self.item)['state'], 'awaiting_resource')

    def controller(self, **kwargs):
        return RecoveryController(self.ledger, self.pool, host='test',
            evidence_root=self.root / 'recovery evidence', fence=lambda r: None, **kwargs)

    def test_metadata_failure_frees_same_live_editor_and_returns_only_candidate_to_worker(self):
        self.fail_candidate()
        self.assertEqual(self.controller().tick()['repaired'], 1)
        self.assertEqual(self.ledger.item(self.item)['state'], 'queued')
        self.assertEqual(self.ledger.item(self.item)['root_repo'], 'Farm-Client')
        self.assertEqual(self.ledger.active_reservation(self.item), None)
        self.assertFalse(any(r['item_id'] == self.item for r in self.ledger.reservations(states=('queued',))))
        self.assertEqual(self.ledger.slot('unity_slot:1')['state'], 'idle_open')
        self.assertEqual(self.ledger.slot('unity_slot:1')['parked_commit'], self.main)
        self.assertEqual(self.trees.head(self.pool.folder(self.pool.entries['unity_slot:1'])), self.main)
        self.assertEqual(self.ledger.item(self.item)['candidate_repair']['failed_commit'], self.bad)
        self.assertEqual(self.store.job_attempts(self.item), 0)
        self.assertFalse(any(c[0] in ('terminate', 'reap_server') for c in self.mcp.calls))
        self.assertEqual(sum(c[0] == 'start' for c in self.mcp.calls), 1)
        manifest = json.loads((self.root / 'recovery evidence' / self.record['id'] / 'source-meta.json').read_text(encoding='utf-8'))
        archived = subprocess.check_output(['git', 'show', f"{manifest['tracked_ref']}:{self.meta.as_posix()}"],
                                           cwd=self.trees.clone_path('Farm-Client'), encoding='utf-8')
        self.assertIn('guid: preserved', archived)
        self.assertIn('enableMipMap: 0', archived)
        self.assertEqual(self.pool.grant(), 1)
        self.assertEqual(self.ledger.active_reservation(self.other)['resource'], 'unity_slot:1')

    def test_same_bad_sha_is_refused_but_corrected_head_can_be_verified(self):
        self.fail_candidate()
        self.controller().tick()
        token = self.ledger.claim(self.item, worker_id='repair')['token']
        with self.assertRaisesRegex(LedgerError, 'unchanged candidate'):
            self.ledger.await_resource(self.item, token, 'unity_slot', 'interactive', commit_sha=self.bad, skill=SKILLS['fix'])
        git('checkout', 'stale-candidate', cwd=self.origin)
        (self.origin / self.meta).write_text('guid: preserved\nenableMipMap: 0\n', encoding='utf-8')
        git('commit', '-qam', 'corrected inputs', cwd=self.origin)
        corrected = self.trees.head(self.origin)
        git('checkout', 'main', cwd=self.origin)
        self.ledger.cancel(self.other, 'fixture complete')
        self.ledger.await_resource(self.item, token, 'unity_slot', 'interactive', commit_sha=corrected, skill=SKILLS['fix'])
        self.assertEqual(self.pool.grant(), 1)
        self.assertEqual(self.ledger.active_reservation(self.item)['commit_sha'], corrected)
        self.assertIsNone(self.ledger.item(self.item)['candidate_repair'])

    def test_stop_during_fence_never_revives_the_candidate(self):
        self.fail_candidate()
        controller = RecoveryController(self.ledger, self.pool, host='test', evidence_root=self.root / 'recovery evidence',
            fence=lambda r: self.ledger.cancel(self.item, 'human Stop'))
        controller.tick()
        self.assertEqual(self.ledger.item(self.item)['state'], 'cancelled')
        self.assertEqual(self.ledger.slot('unity_slot:1')['state'], 'idle_open')

    def test_unknown_project_identity_uses_editor_recovery_instead_of_candidate_repair(self):
        self.candidates()
        self.item = self.waiting(ISSUE, self.bad)
        original = self.mcp.probe
        def wrong(slot, target):
            result = original(slot, target)
            result['checks']['project'] = 'unknown'
            return result
        self.mcp.probe = wrong
        self.pool.grant()
        self.assertEqual(RecoveryStore(self.ledger).pending('test')[0]['recovery_kind'], 'setup')

    def test_restore_failure_keeps_source_and_slot_fenced_but_worker_can_repair(self):
        self.fail_candidate()
        with patch.object(self.trees, 'reconcile_slot_meta', side_effect=OSError('archive disk unavailable')):
            self.controller().tick()
        self.assertEqual(self.ledger.item(self.item)['state'], 'queued')
        self.assertEqual(self.ledger.slot('unity_slot:1')['state'], 'held')
        self.assertTrue(source_snapshot(self.pool.folder(self.pool.entries['unity_slot:1']))['dirty'])
        self.assertIn(('allow_refresh', 'unity_slot:1'), self.mcp.calls)

    def test_closed_unselected_project_cannot_hide_exhausted_selected_pool(self):
        self.candidates()
        item = self.waiting(ISSUE, self.main)
        self.ledger.ensure_slot('unity_slot:3', kind='unity_slot', host='test', folder=str(self.root / 'unused'))
        store = RecoveryStore(self.ledger)
        with self.ledger._transaction():
            record = store.request_in_transaction('unity_slot:1', 'unknown Editor', recovery_kind='setup')
            store._exhaust(record, 'repair exhausted')
        store.fail_unserviceable('test', slot_ids={'unity_slot:1'})
        self.assertEqual(self.ledger.item(item)['state'], 'failed')

    def test_failed_main_uses_a_previous_fully_verified_candidate_without_restart(self):
        self.candidates()
        # Publish the old candidate as main after the initial healthy warmup.
        git('reset', '--hard', self.bad, cwd=self.origin)
        self.trees.resolve_commit('Farm-Client')
        self.ledger.set_slot_state('unity_slot:1', 'switching', parked_commit=self.bad)
        with self.assertRaises(Exception):
            self.pool.switch('unity_slot:1', self.bad, 'interactive')
        RecoveryStore(self.ledger).quarantine_source('unity_slot:1', 'main changed inputs', self.pool.last_observation)
        self.assertEqual(self.controller().tick()['repaired'], 1)
        self.assertEqual(self.ledger.slot('unity_slot:1')['parked_commit'], self.main)
        self.assertEqual(sum(c[0] == 'start' for c in self.mcp.calls), 1)

    def test_no_distinct_clean_baseline_returns_worker_but_preserves_editor_and_source(self):
        self.fail_candidate()
        git('reset', '--hard', self.bad, cwd=self.origin)
        self.ledger.connection.execute("DELETE FROM audit WHERE kind='unity_editor_verified'")
        self.assertEqual(self.controller().tick()['repaired'], 0)
        self.assertEqual(self.ledger.item(self.item)['state'], 'queued')
        self.assertEqual(self.ledger.slot('unity_slot:1')['state'], 'held')
        self.assertTrue(source_snapshot(self.pool.folder(self.pool.entries['unity_slot:1']))['dirty'])
        self.assertFalse(any(c[0] == 'terminate' for c in self.mcp.calls))

    def test_non_metadata_changes_are_never_discarded(self):
        self.fail_candidate()
        folder = self.pool.folder(self.pool.entries['unity_slot:1'])
        (folder / 'README.md').write_text('preserve authored source', encoding='utf-8')
        self.assertEqual(self.controller().tick()['repaired'], 0)
        self.assertEqual((folder / 'README.md').read_text(), 'preserve authored source')
        self.assertEqual(self.ledger.slot('unity_slot:1')['state'], 'held')

    def test_failed_allow_refresh_is_restarted_before_publishing_the_slot(self):
        self.fail_candidate()
        @contextmanager
        def interrupted(slot):
            yield
            raise OSError('Editor disappeared while enabling refresh')
        original = self.mcp.suspend_imports
        self.mcp.suspend_imports = interrupted
        self.assertEqual(self.controller().tick()['repaired'], 0)
        self.assertEqual(self.ledger.slot('unity_slot:1')['state'], 'held')
        self.mcp.suspend_imports = original
        self.advance(61)
        self.assertEqual(self.controller().tick()['repaired'], 1)
        self.assertEqual(self.ledger.slot('unity_slot:1')['state'], 'idle_open')
        self.assertTrue(any(c[0] == 'terminate' for c in self.mcp.calls))

    def test_unreachable_cancellation_uses_owned_restart_and_preserves_metadata(self):
        self.fail_candidate()
        self.mcp.cancel_tests = lambda slot: (_ for _ in ()).throw(TimeoutError('MCP unreachable'))
        self.assertEqual(self.controller().tick()['repaired'], 1)
        self.assertEqual(self.ledger.slot('unity_slot:1')['state'], 'idle_open')
        self.assertEqual(self.ledger.slot('unity_slot:1')['parked_commit'], self.main)
        self.assertTrue(any(c[0] == 'terminate' for c in self.mcp.calls))
        self.assertTrue((self.root / 'recovery evidence' / self.record['id'] / 'source-meta.json').is_file())

    def test_post_execution_metadata_becomes_candidate_repair_before_same_sha_retry(self):
        self.candidates()
        self.mcp.failed_commit = None
        self.item = self.waiting(ISSUE, self.bad)
        self.assertEqual(self.pool.grant(), 1)
        reservation = self.ledger.active_reservation(self.item)
        (self.pool.folder(self.pool.entries['unity_slot:1']) / self.meta).write_text('guid: preserved\nenableMipMap: 0\n', encoding='utf-8')
        self.ledger.hold(reservation['reservation_id'], 'test cleanup changed source')
        self.assertEqual(self.controller().tick()['repaired'], 1)
        self.assertEqual(self.ledger.item(self.item)['state'], 'queued')
        self.assertEqual(self.ledger.item(self.item)['candidate_repair']['failed_commit'], self.bad)
        self.assertFalse(any(r['item_id'] == self.item for r in self.ledger.reservations(states=('queued',))))

    def test_compile_failure_returns_worker_and_never_requeues_the_failed_pin(self):
        self.candidates()
        self.mcp.failed_commit = None
        self.item = self.waiting(ISSUE, self.bad)
        self.mcp.console_errors_since = lambda slot, commit: ['Assets/Candidate.cs(1,1): error CS0246: missing type'] if commit == self.bad else []
        self.assertEqual(self.pool.grant(), 0)
        self.assertEqual(self.controller().tick()['repaired'], 1)
        self.assertEqual(self.ledger.item(self.item)['state'], 'queued')
        self.assertIn('error CS0246', self.ledger.item(self.item)['candidate_repair']['reason'])
        self.assertFalse(any(r['item_id'] == self.item for r in self.ledger.reservations(states=('queued',))))

    def failed_baseline(self, *, exhausted=False, generated=False):
        self.fail_candidate()
        self.ledger.cancel(self.other, 'fixture complete')
        original = self.mcp.refresh
        self.generated = Path('Assets/新增 图集.png.meta') if generated else self.meta
        self.imported = 'guid: preserved\ntextureCompression: 2\n'
        def refresh(slot):
            original(slot)
            (Path(slot['folder']) / self.generated).write_text(self.imported, encoding='utf-8')
        self.mcp.refresh = refresh
        self.assertEqual(self.controller().tick()['repaired'], 0)
        if exhausted:
            for delay in (61, 181):
                self.advance(delay)
                self.assertEqual(self.controller().tick()['repaired'], 0)
            self.assertEqual(self.store.get(self.record['id'])['state'], 'exhausted')

    def corrected_request(self, *, correct=True):
        from agent.execution import queue_resource
        self.correction = self.trees.add_stage('Farm-Client', self.item, 'farmbot/farm-1', self.bad)
        (self.correction / self.meta).write_text('guid: preserved\nenableMipMap: 0\n', encoding='utf-8')
        (self.correction / self.generated).write_text(self.imported if correct else 'guid: preserved\n',
                                                    encoding='utf-8')
        git('add', '.', cwd=self.correction)
        git('commit', '-qm', 'retain native metadata correction', cwd=self.correction)
        self.corrected = self.trees.head(self.correction)
        self.ledger.connection.execute("UPDATE work_items SET execution_contract='single-worker-v1',skill='work' WHERE id=?",
                                       (self.item,))
        self.ledger.set_worker(self.item, 123, 'test')
        self.token = self.ledger.claim(self.item, worker_id='correcting-owner')['token']
        self.ledger.connection.execute("UPDATE work_items SET lease_seconds=3600,lease_expires_at=? WHERE id=?",
                                       (self.now + 3600, self.item))
        self.advance(1)
        self.request = queue_resource(self.ledger, self.item, self.token, 'unity_slot', 'interactive', self.corrected)

    def test_corrected_request_replaces_dirty_pinned_baseline_and_preserves_tracked_metadata(self):
        self.failed_baseline()
        self.corrected_request()
        self.assertEqual(self.controller().tick()['repaired'], 1)
        self.assertEqual(self.store.get(self.record['id'])['state'], 'superseded')
        row = self.ledger.connection.execute('SELECT * FROM resource_recoveries ORDER BY rowid DESC LIMIT 1').fetchone()
        self.assertEqual((row['state'], row['attempts']), ('recovered', 1))
        self.assertEqual(self.ledger.slot('unity_slot:1')['parked_commit'], self.corrected)
        self.assertEqual(self.ledger.item(self.item)['state'], 'running')
        self.assertEqual(self.ledger.reservation(self.request['reservation_id'])['state'], 'queued')
        self.assertIsNone(self.ledger.active_reservation(self.item))
        context = self.ledger.item(self.item)['candidate_repair']
        self.assertEqual(context['metadata_ref_hint'], f"refs/farmbot/slot-recovery/{self.record['id']}")
        self.assertTrue(Path(context['metadata_evidence']).joinpath('source-meta.json').exists())
        self.assertEqual(source_snapshot(self.pool.folder(self.pool.entries['unity_slot:1']))['dirty'], [])

    def test_new_metadata_correction_recovers_an_exhausted_slot_after_controller_restart(self):
        self.failed_baseline(exhausted=True, generated=True)
        self.corrected_request()
        self.assertEqual(self.controller().tick()['repaired'], 1)
        self.assertEqual(self.ledger.slot('unity_slot:1')['parked_commit'], self.corrected)
        backup = (self.root / 'recovery evidence' / self.record['id'] /
                  f"{self.record['id']}-baseline-2" / 'untracked-meta' / self.generated)
        self.assertEqual(backup.read_text(encoding='utf-8'), self.imported)
        previous = self.store.get(self.record['id'])
        self.assertEqual((previous['state'], previous['attempts']), ('superseded', 3))
        self.assertEqual(self.controller().tick()['repaired'], 0)

    def test_cancelled_stale_expired_withdrawn_or_paused_request_cannot_retarget(self):
        from agent.maintenance import Maintenance
        self.failed_baseline()
        self.corrected_request()
        db = self.ledger.connection
        for sql, restore in (
            ("UPDATE reservations SET state='cancelled'", "UPDATE reservations SET state='queued'"),
            ("UPDATE reservations SET generation=generation+1", "UPDATE reservations SET generation=generation-1"),
            ("UPDATE work_items SET lease_expires_at=0", f"UPDATE work_items SET lease_expires_at={self.now+3600}"),
            ("UPDATE work_items SET withdraw_deadline=1", "UPDATE work_items SET withdraw_deadline=NULL"),
            ("UPDATE work_items SET state='awaiting_input'", "UPDATE work_items SET state='running'"),
            ("UPDATE work_items SET host='other'", "UPDATE work_items SET host='test'"),
        ):
            table_id = 'reservation_id' if 'reservations' in sql else 'id'
            key = self.request['reservation_id'] if table_id == 'reservation_id' else self.item
            with self.subTest(sql=sql):
                db.execute(sql + f' WHERE {table_id}=?', (key,))
                self.controller().retarget_candidates()
                self.assertEqual(self.store.get(self.record['id'])['state'], 'pending')
                db.execute(restore + f' WHERE {table_id}=?', (key,))
        Maintenance(self.ledger).pause('hold admission')
        self.controller().retarget_candidates()
        self.assertEqual(self.store.get(self.record['id'])['state'], 'pending')

    def test_dirty_owned_candidate_or_changed_head_cannot_offer_recovery_source(self):
        self.failed_baseline()
        self.corrected_request()
        (self.correction / self.meta).write_text('uncommitted importer setting\n', encoding='utf-8')
        self.controller().retarget_candidates()
        self.assertEqual(self.store.get(self.record['id'])['state'], 'pending')
        git('commit', '-qam', 'different HEAD', cwd=self.correction)
        self.controller().retarget_candidates()
        self.assertEqual(self.store.get(self.record['id'])['state'], 'pending')

    def test_corrected_request_cannot_discard_non_metadata_changes_in_old_baseline(self):
        self.failed_baseline()
        self.corrected_request()
        folder = self.pool.folder(self.pool.entries['unity_slot:1'])
        (folder / 'README.md').write_text('unexplained source change', encoding='utf-8')
        self.assertEqual(self.controller().tick()['repaired'], 0)
        self.assertEqual((folder / 'README.md').read_text(encoding='utf-8'), 'unexplained source change')
        self.assertEqual(self.ledger.slot('unity_slot:1')['state'], 'held')

    def test_known_bad_commit_and_unselected_issue_cannot_retarget(self):
        from agent.admission_scope import KIND
        self.failed_baseline()
        self.corrected_request()
        controller = self.controller()
        with patch.object(controller.store, 'known_candidate', return_value={'reason': 'already rejected'}):
            controller.retarget_candidates()
        self.assertEqual(self.store.get(self.record['id'])['state'], 'pending')
        self.ledger.note('maintenance', KIND, details={'issue_id': OTHER, 'identifier': 'FARM-2'})
        controller.retarget_candidates()
        self.assertEqual(self.store.get(self.record['id'])['state'], 'pending')

    def test_cancel_during_source_validation_is_rechecked_before_retarget(self):
        from agent.execution import cancel_queued_resource
        self.failed_baseline()
        self.corrected_request()
        verify = self.trees.verification_commit
        def cancelled(*args):
            result = verify(*args)
            cancel_queued_resource(self.ledger, self.item, self.token, self.request['reservation_id'])
            return result
        with patch.object(self.trees, 'verification_commit', side_effect=cancelled):
            self.controller().retarget_candidates()
        self.assertEqual(self.store.get(self.record['id'])['state'], 'pending')
        self.assertEqual(self.ledger.connection.execute("SELECT COUNT(*) FROM audit WHERE kind='candidate_recovery_retarget'").fetchone()[0], 0)

    def test_still_bad_correction_has_three_attempts_and_resubmitting_same_sha_does_not_reset(self):
        from agent.execution import cancel_queued_resource, queue_resource
        self.failed_baseline()
        self.corrected_request(correct=False)
        self.assertEqual(self.controller().tick()['repaired'], 0)
        successor = self.store.pending('test')[0]
        for delay in (61, 181):
            self.advance(delay)
            self.ledger.renew(self.item, self.token)
            self.assertEqual(self.controller().tick()['repaired'], 0)
        self.assertEqual(self.store.get(successor['id'])['state'], 'exhausted')
        cancel_queued_resource(self.ledger, self.item, self.token, self.request['reservation_id'])
        self.advance(1)
        queue_resource(self.ledger, self.item, self.token, 'unity_slot', 'interactive', self.corrected)
        self.assertEqual(self.controller().tick()['repaired'], 0)
        self.assertEqual(self.store.get(successor['id'])['attempts'], 3)
        self.assertEqual(self.ledger.connection.execute("SELECT COUNT(*) FROM audit WHERE kind='candidate_recovery_retarget'").fetchone()[0], 1)

    def test_operator_recovery_requires_maintenance_and_never_resumes_waiting_job(self):
        from agent.execution import cancel_queued_resource
        from agent.maintenance import Maintenance
        self.failed_baseline(exhausted=True)
        self.corrected_request()
        cancel_queued_resource(self.ledger, self.item, self.token, self.request['reservation_id'])
        self.ledger.connection.execute("UPDATE work_items SET state='awaiting_input',token=NULL WHERE id=?", (self.item,))
        with self.assertRaisesRegex(ValueError, 'maintenance'):
            self.controller().retarget_candidate(self.record['id'], self.corrected, operator_reason='restore slot')
        Maintenance(self.ledger).pause('restore slot')
        result = self.controller().retarget_candidate(self.record['id'], self.corrected,
            operator_reason='restore slot', repair_now=True)
        self.assertIsNotNone(result)
        self.assertEqual(result['state'], 'recovered')
        self.assertEqual(self.ledger.item(self.item)['state'], 'awaiting_input')


class ImportSuspensionTests(unittest.TestCase):
    def test_unconfirmed_disable_never_enters_mutation_section(self):
        class Client:
            def call_tool(self, *args):
                return {'error': 'failed to execute'}
        identity = UnityIdentity(Path('unused'))
        identity._client = lambda slot: Client()
        with self.assertRaisesRegex(Exception, 'did not confirm'):
            with identity.suspend_imports({'slot_id': 'owned'}):
                self.fail('unconfirmed suspension must not mutate files')

    def test_refresh_is_reenabled_even_when_preservation_fails(self):
        calls = []
        class Client:
            def call_tool(self, name, arguments):
                method = 'DisallowAutoRefresh' if 'Disallow' in arguments['code'] else 'AllowAutoRefresh'
                calls.append(method)
                return {'result': method}
        identity = UnityIdentity(Path('unused'))
        identity._client = lambda slot: Client()
        with self.assertRaisesRegex(ValueError, 'archive failed'):
            with identity.suspend_imports({'slot_id': 'owned'}):
                raise ValueError('archive failed')
        self.assertEqual(calls, ['DisallowAutoRefresh', 'AllowAutoRefresh'])
