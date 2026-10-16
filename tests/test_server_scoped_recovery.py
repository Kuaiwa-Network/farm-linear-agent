"""Scoped race, partial-suite evidence and fenced host continuation regressions."""
from contextlib import nullcontext
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from agent.ledger import LedgerError
from agent.maintenance import Maintenance
from agent.server_evidence import completed_packages, digest, verify_manifest
from agent.server_recovery import resume_server
from agent.server_test_plan import changed_scope, check_scope, profile, scope, timeout_seconds
from test_server_slots import ServerPoolFixture

PLAN = {'packages': ['./modules/animal'], 'tests': [], 'race': True}
PACKAGE = 'example/farm/modules/animal'


class SelectionTests(unittest.TestCase):
    def test_race_memory_does_not_expand_verification_or_deadline(self):
        self.assertEqual(profile(PLAN), 'full')
        self.assertEqual(scope(PLAN), 'targeted')
        self.assertEqual(timeout_seconds(PLAN), 1800)
        self.assertEqual(timeout_seconds({**PLAN, 'packages': ['./...']}), 7200)
        self.assertEqual(timeout_seconds({**PLAN, 'packages': ['./...']}, 7200), 7200)

    def test_changes_cannot_be_omitted_or_silently_broadened(self):
        changes = changed_scope(['modules/animal/ops.go', 'modules/animal/ops_test.go', 'README.md'])
        check_scope(PLAN, changes)
        for plan in ({**PLAN, 'packages': ['./modules/other']}, {**PLAN, 'packages': ['./...']},
                     {**PLAN, 'tests': ['TestSave']}):
            with self.assertRaises(ValueError):
                check_scope(plan, changes)
        check_scope({**PLAN, 'packages': ['./...']}, changes, 'storage transaction affects all consumers')
        shared = changed_scope(['go.mod'])
        with self.assertRaises(ValueError):
            check_scope(PLAN, shared)
        check_scope({**PLAN, 'packages': ['./...']}, shared)


class EvidenceRecoveryTests(ServerPoolFixture):
    def setUp(self):
        super().setUp()
        self.item_id = self.request(plan=PLAN)
        self.pool.tick()
        self.res = self.ledger.active_reservation(self.item_id)
        self.candidate, self.base = 'a' * 40, 'b' * 40
        self.ledger.note(self.item_id, 'server_verification_round', 'fixed',
            {'generation': 0, 'candidate_commit': self.candidate, 'base_commit': self.base,
             'reservation_id': self.res['reservation_id']})
        self.ledger.cancel(self.item_id, 'server integration slot timed out')
        self.pool.tick()
        self.ledger.connection.execute('UPDATE job_cleanup SET done=1 WHERE item_id=?', (self.item_id,))
        Maintenance(self.ledger).pause('deploy scoped tests')
        self.run = self.root.parent / 'runs' / self.item_id
        self.run.mkdir(parents=True, exist_ok=True)
        self.events = [{'Action': 'start', 'Package': PACKAGE},
                       {'Action': 'run', 'Package': PACKAGE, 'Test': 'TestSave'},
                       {'Action': 'pass', 'Package': PACKAGE, 'Test': 'TestSave'},
                       {'Action': 'pass', 'Package': PACKAGE},
                       {'Action': 'start', 'Package': 'example/farm/unrelated'}]
        self.report = self.run / 'tests.jsonl'
        self.report.write_text('\n'.join(json.dumps(e) for e in self.events), encoding='utf-8')
        self.metadata = self.run / 'source.json'
        self.metadata.write_text(json.dumps({'source_commit': self.candidate,
            'command': ['go', 'test', '-race', '-json', './...'], 'resource': {'commit': self.candidate,
            'environment': {'MONGO_REQUIRED': '1', 'REDIS_REQUIRED': '1', 'CLUSTER_REQUIRED': '1'}}}), encoding='utf-8')
        self.manifest = {'candidate_commit': self.candidate, 'base_commit': self.base, 'generation': 0,
            'reservation_id': self.res['reservation_id'], 'module': 'example/farm', 'plan': PLAN,
            'report': 'tests.jsonl', 'source_metadata': 'source.json',
            'files': {'tests.jsonl': digest(self.report), 'source.json': digest(self.metadata)},
            'required_tests': {PACKAGE: ['TestSave']}, 'provenance_review': 'operator checked exact source and local services'}
        self.manifest_path = self.root.parent / 'manifest.json'
        self.config = SimpleNamespace(host='test', runtime='fake', repos={})
        self.enterContext(patch('agent.server_recovery.check_ownership'))
        self.guard = self.enterContext(patch('agent.server_recovery.ControllerGuard', return_value=nullcontext()))
        self.enterContext(patch('agent.server_recovery.Paths', return_value=SimpleNamespace(
            ledger=self.db, runs=self.run.parent, repos=self.root/'repos', worktrees=self.root/'worktrees')))
        current = self.ledger.issue(self.ledger.item(self.item_id)['issue_id'])
        self.api = Mock(app_user_id='11111111-1111-4111-8111-111111111111')
        self.api.fetch_issue.return_value = {**current, 'delegate_id': self.api.app_user_id}
        self.enterContext(patch('agent.server_recovery.linear_api', return_value=self.api))
        self.quiet = self.enterContext(patch('agent.server_recovery.Launcher.assert_quiescent'))
        trees = self.enterContext(patch('agent.worktrees.Worktrees')).return_value
        self.enterContext(patch('agent.worktrees._git', return_value='modules/animal/ops.go\0'))

    def resume(self):
        self.manifest_path.write_text(json.dumps(self.manifest), encoding='utf-8')
        return resume_server(self.config, self.item_id, self.manifest_path, 'reuse completed affected packages')

    def test_partial_suite_can_complete_selected_packages_without_releasing_or_retesting(self):
        before = self.ledger.item(self.item_id)
        result = self.resume()
        self.assertFalse(result['result']['whole_suite_passed'])
        after = self.ledger.item(self.item_id)
        for field in ('checkpoint', 'evidence', 'generation', 'target', 'root_repo', 'capacity_retries', 'publication_retries'):
            self.assertEqual(before[field], after[field], field)
        self.assertEqual((after['state'], after['stage']), ('queued', 'server-evidence-recheck'))
        proof = self.ledger.server_verification_round(self.item_id, self.candidate, require_released=True)
        self.assertEqual(proof['retained_evidence_reservation'], self.res['reservation_id'])
        self.assertEqual(self.ledger.reservation(self.res['reservation_id'])['state'], 'cancelled')
        self.assertEqual(len(self.services.started), 1)
        self.assertTrue(Maintenance(self.ledger).requested())
        with self.assertRaises(LedgerError):
            self.resume()

    def test_publication_rechecks_hashes_and_generation(self):
        self.resume()
        self.assertIsNone(self.ledger.server_verification_round(self.item_id, 'c'*40, require_released=True))
        self.report.write_text('changed report', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.ledger.server_verification_round(self.item_id, self.candidate, require_released=True)

    def test_missing_required_skip_fail_incomplete_and_repeated_package_are_refused(self):
        variants = [self.events[:3], self.events + self.events[:1],
            [dict(e, Action='skip') if e.get('Test') and e['Action'] == 'pass' else e for e in self.events],
            self.events + [{'Action': 'fail', 'Package': PACKAGE}],
            [e for e in self.events if not e.get('Test')]]
        for events in variants:
            self.report.write_text('\n'.join(json.dumps(e) for e in events), encoding='utf-8')
            with self.assertRaises(ValueError):
                completed_packages(self.report, 'example/farm', PLAN, {PACKAGE: ['TestSave']})

    def test_source_or_hash_changes_refused_without_queueing(self):
        for field, value in [('candidate_commit', 'c'*40), ('generation', 1), ('reservation_id', 'other')]:
            original = self.manifest[field]; self.manifest[field] = value
            with self.assertRaises((ValueError, LedgerError)):
                self.resume()
            self.manifest[field] = original
        self.manifest['files']['tests.jsonl'] = '0'*64
        with self.assertRaises(ValueError):
            self.resume()
        self.assertEqual(self.ledger.item(self.item_id)['state'], 'cancelled')

    def test_new_timeout_block_recovery_and_no_cross_issue_recovery(self):
        self.ledger._set_state(self.item_id, 'blocked', 'technical deadline', stage='server-verification-timeout')
        self.resume()
        proof = self.ledger.server_verification_round(self.item_id, self.candidate, require_released=True)
        self.assertIsNotNone(proof)
        self.ledger.connection.execute("UPDATE reservations SET generation=1 WHERE reservation_id=?", (self.res['reservation_id'],))
        with self.assertRaises(LedgerError):
            self.ledger.server_verification_round(self.item_id, self.candidate, require_released=True)

    def test_live_resource_and_missing_service_requirement_refuse(self):
        self.ledger.connection.execute("UPDATE reservations SET state='active' WHERE reservation_id=?", (self.res['reservation_id'],))
        with self.assertRaisesRegex(LedgerError, 'resources settled'):
            self.resume()
        self.ledger.connection.execute("UPDATE reservations SET state='cancelled' WHERE reservation_id=?", (self.res['reservation_id'],))
        data = json.loads(self.metadata.read_text(encoding='utf-8'))
        data['resource']['environment']['MONGO_REQUIRED'] = '0'
        self.metadata.write_text(json.dumps(data), encoding='utf-8')
        self.manifest['files']['source.json'] = digest(self.metadata)
        with self.assertRaisesRegex(ValueError, 'service resource'):
            self.resume()

    def test_stop_and_changed_delegation_cannot_be_recovered(self):
        self.ledger.note(self.item_id, 'cancelled', 'operator stop')
        with self.assertRaisesRegex(LedgerError, 'not Stop'):
            self.resume()
        self.api.fetch_issue.return_value['delegate_id'] = 'someone else'
        with self.assertRaisesRegex(LedgerError, 'delegated'):
            self.resume()

    def test_requires_stopped_paused_controller_and_quiescent_cleanup(self):
        self.guard.side_effect = RuntimeError('running controller')
        with patch('agent.server_recovery.Ledger') as ledger, self.assertRaises(RuntimeError):
            self.resume()
        ledger.assert_not_called()
        self.guard.side_effect = None
        self.quiet.side_effect = RuntimeError('descendant alive')
        with self.assertRaises(RuntimeError):
            self.resume()
        self.quiet.side_effect = None
        self.ledger.connection.execute('UPDATE job_cleanup SET done=0 WHERE item_id=?', (self.item_id,))
        with self.assertRaisesRegex(LedgerError, 'cleanup'):
            self.resume()
        Maintenance(self.ledger).resume()
        with self.assertRaisesRegex(LedgerError, 'maintenance'):
            self.resume()


class DeadlineTests(ServerPoolFixture):
    def test_startup_does_not_consume_budget_and_race_remains_targeted(self):
        def slow_start(lease):
            self.now += 300
        self.services.after_start = slow_start
        item = self.request(plan=PLAN)
        self.pool.tick()
        lease, res = self.pool._lease('server_slot:1')
        self.assertEqual(lease['expires_at'], 1300 + 1800)
        self.assertEqual(lease['verification_scope'], 'targeted')
        self.now = 2900
        self.pool.tick()
        self.assertEqual(self.ledger.item(item)['state'], 'queued')
        self.now = 3101
        self.pool.tick()
        self.assertEqual(self.ledger.item(item)['stage'], 'server-verification-timeout')
        self.pool.tick()
        self.assertEqual(self.ledger.item(item)['state'], 'blocked')
        self.assertEqual(self.ledger.reservation(res['reservation_id'])['state'], 'cancelled')

    def test_timeout_never_overwrites_stop_or_a_changed_reservation(self):
        item = self.request(plan=PLAN); self.pool.tick()
        res = self.ledger.active_reservation(item)
        self.ledger.cancel(item, 'operator stop')
        self.now += 2000
        self.assertFalse(self.ledger.server_verification_timeout(res['reservation_id'], 2000))
        self.assertEqual(self.ledger.item(item)['state'], 'cancelled')

    def test_new_technical_block_can_reuse_evidence(self):
        item = self.request(plan=PLAN); self.pool.tick()
        self.now += 1801
        self.pool.tick()
        self.assertEqual(self.ledger.item(item)['state'], 'blocked')
        self.pool.tick()
        self.assertEqual(self.ledger.item(item)['state'], 'blocked')
        self.assertFalse(self.ledger.reservation_queue('server_slot'))
