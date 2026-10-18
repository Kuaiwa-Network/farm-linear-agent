"""Operator recovery queues a recheck without fabricating approval or evidence."""
from contextlib import nullcontext
import json
import unittest
from unittest.mock import Mock, patch

from agent.ledger import LedgerError
from agent.maintenance import Maintenance
from agent.ui_scope_recovery import resume_scope
import test_fgui_client_workflow as fixtures


class UiScopeRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = f = fixtures.UiClientWorkflowTests(); f.internal = True; f.setUp()
        self.addCleanup(f.doCleanups)
        source_only = getattr(self, 'source_only', False)
        if not source_only:
            f.installed(); f.source.commit(f.client)
        f.source.config.host = 'fixture'
        f.ledger.pop_inbox(f.item, f.source.token)
        f.ledger.checkpoint(f.item, f.source.token, {'plan': f.source.plan,
            'stage': 'client-install-blocked' if source_only else 'client-scope-stage-limit'})
        f.ledger.await_input(f.item, f.source.token, 'Controller scope correction needed', reason='waiting')
        f.ledger.connection.execute("UPDATE work_items SET state='awaiting_input',token=NULL,lease_expires_at=NULL WHERE id=?", (f.parent,))
        Maintenance(f.ledger).pause('test host correction')
        self.enterContext(patch('agent.ui_scope_recovery.check_ownership'))
        self.guard = self.enterContext(patch('agent.ui_scope_recovery.ControllerGuard', return_value=nullcontext()))
        self.api = Mock(app_user_id=f.source.api.app_user_id)
        self.api.fetch_issue.side_effect = f.source.api.fetch_issue
        self.enterContext(patch('agent.ui_scope_recovery.linear_api', return_value=self.api))
        self.quiescent = self.enterContext(patch('agent.ui_scope_recovery.Launcher.assert_quiescent'))

    def resume(self):
        return resume_scope(self.f.source.config, self.f.item, 'scope verifier corrected; recheck retained candidate',
                            source_only=getattr(self, 'source_only', False))

    def test_resume_preserves_artifacts_and_requires_fresh_verification(self):
        f = self.f; before = f.ledger.item(f.item)
        inbox = f.ledger.connection.execute('SELECT COUNT(*) FROM inbox').fetchone()[0]
        result = self.resume(); after = f.ledger.item(f.item)
        self.assertTrue(result['verification_required'])
        self.assertEqual(after['state'], 'queued')
        self.assertEqual(after['stage'], 'client-scope-recheck')
        for key in ('generation','checkpoint','evidence','target','task_client_base','root_repo'):
            self.assertEqual(before[key], after[key], key)
        self.assertEqual(f.ledger.connection.execute('SELECT COUNT(*) FROM inbox').fetchone()[0], inbox)
        self.assertTrue(Maintenance(f.ledger).requested())
        self.assertEqual(f.ledger.connection.execute("SELECT COUNT(*) FROM audit WHERE kind='operator_ui_scope_resume'").fetchone()[0], 1)
        self.assertGreaterEqual(self.quiescent.call_count, 2)
        with self.assertRaisesRegex(LedgerError, 'technical wait'):
            self.resume()

    def test_running_controller_refuses_before_opening_ledger(self):
        self.guard.side_effect = RuntimeError('another controller owns this state root')
        with patch('agent.ui_scope_recovery.Ledger') as ledger, self.assertRaisesRegex(RuntimeError, 'another controller'):
            self.resume()
        ledger.assert_not_called()

    def test_requires_maintenance_pause(self):
        Maintenance(self.f.ledger).resume()
        with self.assertRaisesRegex(LedgerError, 'maintenance pause'):
            self.resume()

    def test_unproven_process_teardown_keeps_wait(self):
        self.quiescent.side_effect = RuntimeError('descendant not settled')
        with self.assertRaisesRegex(RuntimeError, 'not settled'):
            self.resume()
        self.assertEqual(self.f.ledger.item(self.f.item)['state'], 'awaiting_input')

    def test_queued_resource_blocks_recovery_without_releasing_it(self):
        f = self.f
        f.ledger.connection.execute("INSERT INTO reservations(reservation_id,item_id,generation,kind,mode,commit_sha,state,created_at) "
            "VALUES('pending',?,0,'unity_slot','interactive',?,'queued',1)", (f.item,f.baseline))
        with self.assertRaisesRegex(LedgerError, 'resources settled'):
            self.resume()
        self.assertEqual(f.ledger.reservation('pending')['state'], 'queued')

    def test_live_parent_and_foreign_host_are_not_assumed_quiescent(self):
        f = self.f
        f.ledger.connection.execute("UPDATE work_items SET state='running' WHERE id=?", (f.parent,))
        with self.assertRaisesRegex(LedgerError, 'settled issue workers'):
            self.resume()
        f.ledger.connection.execute("UPDATE work_items SET state='awaiting_input',host='other-host' WHERE id=?", (f.parent,))
        with self.assertRaisesRegex(LedgerError, "another host"):
            self.resume()

    def test_fresh_undelegation_refuses(self):
        self.api.fetch_issue.side_effect = None
        self.api.fetch_issue.return_value = {**self.f.source.api.fetch_issue(self.f.ledger.item(self.f.item)['issue_id']), 'delegate_id':'another'}
        with self.assertRaisesRegex(LedgerError, 'delegated'):
            self.resume()

    def test_does_not_answer_human_question_or_other_wait_stage(self):
        f = self.f
        for field, value in (('pending_reason', 'question'), ('stage', 'visual-await-approval')):
            with self.subTest(field=field):
                saved = f.ledger.item(f.item)
                if field == 'stage':
                    f.ledger.connection.execute('UPDATE work_items SET stage=? WHERE id=?', (value,f.item))
                else:
                    cp = dict(saved['checkpoint']); cp[field] = value
                    f.ledger.connection.execute('UPDATE work_items SET checkpoint=? WHERE id=?', (json.dumps(cp),f.item))
                with self.assertRaisesRegex(LedgerError, 'not a human question'):
                    self.resume()
                f.ledger.connection.execute('UPDATE work_items SET stage=?,checkpoint=? WHERE id=?',
                                             (saved['stage'],json.dumps(saved['checkpoint']),f.item))

    def test_damaged_installation_is_not_replaced_or_waived(self):
        self.f.ledger.connection.execute("UPDATE audit SET details='{}' WHERE kind='fgui_install_complete'")
        with self.assertRaisesRegex(LedgerError, 'damaged controller evidence'):
            self.resume()

    def test_recovery_dispatch_rechecks_without_claiming_human_approval(self):
        from agent.dispatch import dispatch_message
        text = dispatch_message(item={'id':'i','skill':'fgui','stage':'client-scope-recheck'},
            issue={'identifier':'FARM-1','url':'u'},skill_path='skill',worktrees={},db_path='db',
            runtime='fake',guidance=[],budget={'lease_seconds':2700,'renew_minutes':5})
        self.assertIn('Recheck verify-ui at the retained clean candidate', text)
        self.assertIn('not a human reply or a passed gate', text)
