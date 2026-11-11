"""Authenticated progress keeps a live claim, including after a model reconnect."""
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

from agent import __main__ as cli
from agent.ledger import LedgerError
from agent.maintenance import Maintenance
from test_ledger import LedgerBase, ISSUE, SESSION, issue


class WorkerLeaseTests(LedgerBase):
    def setUp(self):
        super().setUp()
        self.ledger.observe_issue(issue())
        self.ledger.ensure_session(SESSION, ISSUE, delegation=True)
        self.key = self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill='work')['id']
        self.ledger.set_worker(self.key, 123, 'test', lease_seconds=2700)
        self.token = self.ledger.claim(self.key, worker_id='owner')['token']
        self.token_file = Path(self.tmp.name) / 'claim 续约.token'
        self.token_file.write_text(self.token, encoding='utf-8')
        self.progress_file = Path(self.tmp.name) / 'checkpoint 进度.json'
        self.progress_file.write_text(json.dumps({'summary': 'Draft PRs published',
                                                  'handoff': 'Check readiness and deliver'}), encoding='utf-8')

    def invoke(self, command):
        extra = {'pop-inbox': [], 'checkpoint': ['--input', str(self.progress_file)],
                 'revalidate': ['--fingerprint', self.ledger.issue_context(self.key)['fingerprint']]}
        args = cli.parser().parse_args(['--db', str(self.path), command, '--item', self.key,
                                        '--token-file', str(self.token_file), *extra[command]])
        with patch.object(cli, 'configured_issue_prefix', return_value='FARM'):
            return cli.run(args, self.ledger, None)

    def test_reconnected_worker_survives_original_expiry_and_preserves_claim(self):
        original = self.ledger.item(self.key)
        # FARM-2168 resumed publication 33 minutes after its last renewal, then
        # polled an empty inbox. That successful CLI call used to leave 12 minutes.
        self.now += 33 * 60
        self.assertEqual(self.invoke('pop-inbox'), [])
        self.assertEqual(self.ledger.item(self.key)['lease_expires_at'], self.now + 2700)
        self.now = original['lease_expires_at'] + 1
        self.assertNotIn(self.key, self.ledger.status()['recovery_required'])
        self.invoke('checkpoint')
        after = self.ledger.item(self.key)
        self.assertEqual(after['lease_expires_at'], self.now + 2700)
        self.assertEqual(after['checkpoint']['summary'], 'Draft PRs published')
        for field in ('state', 'generation', 'worker_pid'):
            self.assertEqual(after[field], original[field])
        self.assertEqual(self.ledger.renew(self.key, self.token)['state'], 'running')

    def test_each_progress_command_renews_using_recorded_budget(self):
        for command in ('pop-inbox', 'checkpoint', 'revalidate'):
            with self.subTest(command=command):
                self.now += 600
                result = self.invoke(command)
                self.assertEqual(self.ledger.item(self.key)['lease_expires_at'], self.now + 2700)
                if command != 'pop-inbox':
                    self.assertEqual(result['lease_expires_at'], self.now + 2700)

    def assert_progress_refused(self, message):
        before = dict(self.ledger._row(self.key))
        for command in ('pop-inbox', 'checkpoint', 'revalidate'):
            with self.subTest(command=command), self.assertRaisesRegex(LedgerError, message):
                self.invoke(command)
            self.assertEqual(dict(self.ledger._row(self.key)), before)

    def test_wrong_token_cannot_extend_claim(self):
        self.token_file.write_text('wrong-token', encoding='utf-8')
        self.now += 600
        self.assert_progress_refused('matching token')

    def test_expired_claim_is_never_revived(self):
        self.now = self.ledger.item(self.key)['lease_expires_at']
        self.assert_progress_refused('lease expired')
        self.assertIn(self.key, self.ledger.status()['recovery_required'])

    def test_retired_attempt_cannot_extend_successor(self):
        self.now = self.ledger.item(self.key)['lease_expires_at']
        self.ledger.recover(self.key, 'fixture worker exited')
        self.ledger.claim(self.key, worker_id='successor')
        self.now += 600
        self.assert_progress_refused('matching token')

    def test_cancelled_claim_is_never_revived(self):
        self.ledger.cancel(self.key, 'operator stop')
        self.assert_progress_refused('running claim')

    def test_inbox_failure_rolls_back_consumption_and_renewal(self):
        self.ledger.push_inbox(self.key, 'Preserve this request')
        before = dict(self.ledger._row(self.key))
        self.now += 600
        with patch.object(self.ledger, '_audit', side_effect=sqlite3.OperationalError('fixture failure')):
            with self.assertRaises(sqlite3.OperationalError):
                self.invoke('pop-inbox')
        self.assertEqual(dict(self.ledger._row(self.key)), before)
        self.assertEqual(self.invoke('pop-inbox'), ['Preserve this request'])

    def test_stale_input_revalidation_does_not_renew_or_acknowledge_changes(self):
        fingerprint = self.ledger.issue_context(self.key)['fingerprint']
        self.ledger.observe_issue(issue(description='New request arrived'))
        before = dict(self.ledger._row(self.key))
        self.now += 600
        with self.assertRaisesRegex(LedgerError, 'issue changed since that read'):
            self.ledger.revalidate(self.key, self.token, fingerprint)
        self.assertEqual(dict(self.ledger._row(self.key)), before)
        self.assertTrue(self.ledger.requires_issue_review(self.key))

    def test_expiry_during_revalidation_rolls_back_instead_of_reviving_claim(self):
        before = dict(self.ledger._row(self.key))
        audits = self.ledger.connection.execute('SELECT count(*) FROM audit').fetchone()[0]
        original_audit = self.ledger._audit

        def audit_then_expire(*args, **kwargs):
            original_audit(*args, **kwargs)
            self.now = before['lease_expires_at']

        with patch.object(self.ledger, '_audit', side_effect=audit_then_expire):
            with self.assertRaisesRegex(LedgerError, 'lease expired'):
                self.invoke('revalidate')
        self.assertEqual(dict(self.ledger._row(self.key)), before)
        self.assertEqual(self.ledger.connection.execute('SELECT count(*) FROM audit').fetchone()[0], audits)

    def test_renewal_preserves_control_flags_and_pending_requests(self):
        self.ledger.push_inbox(self.key, 'Review this change')
        Maintenance(self.ledger).pause('planned maintenance')
        for command in ('checkpoint', 'revalidate'):
            self.now += 600
            result = self.invoke(command)
            self.assertTrue(result['maintenance_requested'])
            self.assertEqual(result['inbox_pending'], 1)
            self.assertEqual(result['lease_expires_at'], self.now + 2700)

    def test_inbox_polling_cannot_postpone_maintenance(self):
        expiry = self.ledger.item(self.key)['lease_expires_at']
        self.now += 600
        Maintenance(self.ledger).pause('planned maintenance')
        self.ledger.push_inbox(self.key, 'Save progress')
        self.assertEqual(self.invoke('pop-inbox'), ['Save progress'])
        self.assertEqual(self.ledger.item(self.key)['lease_expires_at'], expiry)
        self.assertTrue(self.ledger.renew(self.key, self.token)['maintenance_requested'])

    def test_inbox_polling_cannot_postpone_withdrawal(self):
        expiry = self.ledger.item(self.key)['lease_expires_at']
        self.now += 600
        self.ledger.flag_withdrawal(self.key, 'undelegated', self.now + 30)
        self.assertEqual(self.invoke('pop-inbox'), [])
        self.assertEqual(self.ledger.item(self.key)['lease_expires_at'], expiry)
        self.assertEqual(self.ledger.renew(self.key, self.token)['withdraw_deadline'], self.now + 30)

    def test_context_and_host_status_reads_do_not_keep_silent_worker_alive(self):
        expiry = self.ledger.item(self.key)['lease_expires_at']
        self.now += 600
        args = cli.parser().parse_args(['--db', str(self.path), 'issue-context', '--item', self.key,
                                        '--view', 'resume'])
        cli.run(args, self.ledger, None)
        self.ledger.status()
        self.assertEqual(self.ledger.item(self.key)['lease_expires_at'], expiry)
        self.now = expiry
        self.assertIn(self.key, self.ledger.status()['recovery_required'])
