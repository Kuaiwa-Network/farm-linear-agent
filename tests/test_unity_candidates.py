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
        self.mcp.console_errors_since = lambda slot, commit: ['candidate CS0246'] if commit == self.bad else []
        self.assertEqual(self.pool.grant(), 0)
        self.assertEqual(self.controller().tick()['repaired'], 1)
        self.assertEqual(self.ledger.item(self.item)['state'], 'queued')
        self.assertIn('candidate CS0246', self.ledger.item(self.item)['candidate_repair']['reason'])
        self.assertFalse(any(r['item_id'] == self.item for r in self.ledger.reservations(states=('queued',))))


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
