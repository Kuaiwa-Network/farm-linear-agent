"""Host continuation preserves the actual comparison pair and all fenced evidence."""
from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from agent.ledger import LedgerError
from agent.maintenance import Maintenance
from agent.verification_recovery import resume_comparison
import test_verification_pair as fixtures


class ComparisonRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = f = fixtures.VerificationPairTests(); f.setUp()
        self.addCleanup(f.doCleanups)
        self.item, base = f.baseline()
        token = f.settled(self.item, base)
        f.ledger.await_resource(self.item, token, 'unity_slot', 'interactive',
                               skill=fixtures.SKILLS['fix'], commit_sha=f.CANDIDATE)
        self.res = f.ledger.acquire('unity_slot', owner='fixture', host='test')
        token = f.settled(self.item, self.res)
        f.ledger.checkpoint(self.item, token, {'retained_reports': ['baseline', 'candidate'], 'qa': 'passed'})
        f.ledger.fail(self.item, 'parser capability gap')
        f.ledger.connection.execute("UPDATE work_items SET state='blocked',stage='client-comparison-evidence-gap',"
            "worker_pid=NULL,capacity_retries=2,publication_retries=1 WHERE id=?", (self.item,))
        f.ledger.connection.execute('INSERT OR REPLACE INTO job_cleanup(item_id,done,updated_at) VALUES(?,1,1000)', (self.item,))
        Maintenance(f.ledger).pause('deploy comparator')
        self.config = SimpleNamespace(host='test', runtime='fake')
        self.enterContext(patch('agent.verification_recovery.check_ownership'))
        self.guard = self.enterContext(patch('agent.verification_recovery.ControllerGuard', return_value=nullcontext()))
        self.enterContext(patch('agent.verification_recovery.Paths', return_value=SimpleNamespace(ledger=f.db, runs=f.root.parent/'runs')))
        current = f.ledger.issue(f.ledger.item(self.item)['issue_id'])
        self.api = Mock(app_user_id='11111111-1111-4111-8111-111111111111')
        self.api.fetch_issue.return_value = {**current, 'delegate_id': self.api.app_user_id}
        self.enterContext(patch('agent.verification_recovery.linear_api', return_value=self.api))
        self.quiet = self.enterContext(patch('agent.verification_recovery.Launcher.assert_quiescent'))

    def resume(self, candidate=None):
        return resume_comparison(self.config, self.item, candidate or self.f.CANDIDATE, 'parser fixed; compare retained reports')

    def test_retains_round_evidence_budgets_and_does_not_invent_answer(self):
        f = self.f; before = f.ledger.item(self.item)
        pair = f.ledger.client_verification_pair(self.item, f.CANDIDATE, require_candidate=True)
        inbox = f.ledger.connection.execute('SELECT COUNT(*) FROM inbox').fetchone()[0]
        result = self.resume(); after = f.ledger.item(self.item)
        self.assertEqual(result['verification_pair'], pair)
        self.assertTrue(result['verification_required'])
        self.assertEqual(after['stage'], 'client-comparison-recheck')
        self.assertEqual(after['state'], 'queued')
        for key in ('checkpoint', 'evidence', 'generation', 'target', 'root_repo', 'capacity_retries', 'publication_retries'):
            self.assertEqual(before[key], after[key], key)
        self.assertEqual(inbox, f.ledger.connection.execute('SELECT COUNT(*) FROM inbox').fetchone()[0])
        self.assertTrue(Maintenance(f.ledger).requested())
        self.quiet.assert_called()
        with self.assertRaisesRegex(LedgerError, 'blocked Client'):
            self.resume()

    def test_guard_refuses_before_opening_ledger(self):
        self.guard.side_effect = RuntimeError('controller running')
        with patch('agent.verification_recovery.Ledger') as ledger, self.assertRaises(RuntimeError):
            self.resume()
        ledger.assert_not_called()

    def test_requires_maintenance_and_actual_candidate_pair(self):
        with self.assertRaisesRegex(LedgerError, 'completed candidate pair'):
            self.resume('d' * 40)
        Maintenance(self.f.ledger).resume()
        with self.assertRaisesRegex(LedgerError, 'maintenance pause'):
            self.resume()

    def test_live_or_unclean_resource_is_not_released(self):
        self.f.ledger.connection.execute("UPDATE reservations SET state='queued' WHERE reservation_id=?", (self.res['reservation_id'],))
        with self.assertRaisesRegex(LedgerError, 'resources settled'):
            self.resume()
        self.f.ledger.connection.execute("UPDATE reservations SET state='released',release_reason='uncertain recovery' WHERE reservation_id=?", (self.res['reservation_id'],))
        with self.assertRaisesRegex(LedgerError, 'completed candidate pair'):
            self.resume()

    def test_process_or_cleanup_uncertainty_blocks(self):
        self.quiet.side_effect = RuntimeError('descendant alive')
        with self.assertRaisesRegex(RuntimeError, 'descendant alive'):
            self.resume()
        self.quiet.side_effect = None
        self.f.ledger.connection.execute('UPDATE job_cleanup SET done=0 WHERE item_id=?', (self.item,))
        with self.assertRaisesRegex(LedgerError, 'cleanup completion'):
            self.resume()

    def test_fresh_delegation_and_open_issue_required(self):
        self.api.fetch_issue.return_value['delegate_id'] = 'other'
        with self.assertRaisesRegex(LedgerError, 'open and delegated'):
            self.resume()
        self.api.fetch_issue.return_value['delegate_id'] = self.api.app_user_id
        self.api.fetch_issue.return_value['archived'] = True
        with self.assertRaisesRegex(LedgerError, 'open and delegated'):
            self.resume()

    def test_other_stages_running_and_foreign_hosts_refuse(self):
        for field, value, pattern in (('stage', 'human-approval', 'blocked Client'),
                                      ('state', 'running', 'blocked Client'),
                                      ('host', 'other', 'another host'),
                                      ('worker_pid', 123, 'settled issue workers')):
            with self.subTest(field=field):
                old = self.f.ledger.item(self.item)[field]
                self.f.ledger.connection.execute(f'UPDATE work_items SET {field}=? WHERE id=?', (value, self.item))
                with self.assertRaisesRegex(LedgerError, pattern): self.resume()
                self.f.ledger.connection.execute(f'UPDATE work_items SET {field}=? WHERE id=?', (old, self.item))

    def test_human_question_and_unfinished_children_cannot_be_bypassed(self):
        for method, result, pattern in (('human_question', {'id': 'actual-question'}, 'human question'),
                                        ('_unfinished_children', [{'id': 'child'}], 'presentation tasks')):
            with patch('agent.verification_recovery.Ledger.' + method, return_value=result):
                with self.assertRaisesRegex(LedgerError, pattern): self.resume()

    def test_dispatch_preserves_reports_without_claiming_pass(self):
        from agent.dispatch import dispatch_message
        text = dispatch_message(item={'id': 'i', 'skill': 'fix', 'stage': 'client-comparison-recheck'},
            issue={'identifier': 'FARM-1', 'url': 'u'}, skill_path='skill', worktrees={}, db_path='db',
            runtime='fake', guidance=[], budget={'lease_seconds': 2700, 'renew_minutes': 5})
        self.assertIn('Recompare the retained reports offline', text)
        self.assertIn('not a human reply or a passed gate', text)
