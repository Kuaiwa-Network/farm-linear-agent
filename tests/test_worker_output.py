"""Compact CLI output must retain control signals and never change stored evidence."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch

from agent import __main__ as cli
from agent.maintenance import Maintenance
from agent.worker_output import response
from test_ledger import LedgerBase, ISSUE, SESSION, issue


class WorkerOutputTests(LedgerBase):
    def setUp(self):
        super().setUp()
        self.ledger.observe_issue(issue())
        self.ledger.ensure_session(SESSION, ISSUE, delegation=True)
        self.key = self.ledger.create_work_item(
            issue_id=ISSUE, session_id=SESSION, skill='work')['id']
        self.ledger.set_worker(self.key, 123, 'test')
        self.token = self.ledger.claim(self.key, worker_id='owner')['token']
        self.token_file = Path(self.tmp.name) / 'claim 进度.token'
        self.token_file.write_text(self.token, encoding='utf-8')
        self.progress = {'summary': 'Candidate ready', 'handoff': 'Run affected checks',
                         'reports': ['private/report.json'], 'detail': 'retained evidence ' * 1800}
        self.ledger.checkpoint(self.key, self.token, self.progress)

    def invoke(self, command, *extra):
        out, err = io.StringIO(), io.StringIO()
        argv = ['--db', str(self.path), command, '--item', self.key,
                '--token-file', str(self.token_file), *extra]
        # Exercise the real parser/command/output boundary on the fixture ledger.
        # The frozen fixture clock and offline config replace only host plumbing.
        with (patch.object(cli, 'Ledger', return_value=self.ledger),
              patch.object(self.ledger, 'close'),
              patch('agent.environment.check_worker_state'),
              patch.object(cli, 'configured_issue_prefix', return_value='FARM'),
              redirect_stdout(out), redirect_stderr(err)):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def result(self, command, *extra):
        code, out, err = self.invoke(command, *extra)
        self.assertEqual(code, 0, err)
        self.assertEqual(err, '')
        self.assertNotIn(self.token, out)
        return json.loads(out)

    def test_default_renew_is_small_and_full_result_remains_available(self):
        before = self.ledger.item(self.key)['checkpoint']
        self.now += 10
        receipt = self.result('renew')
        self.assertEqual(receipt['state'], 'running')
        self.assertEqual(receipt['generation'], self.ledger.item(self.key)['generation'])
        self.assertEqual(receipt['lease_expires_at'], self.now + 60)
        self.assertIn('checkpoint', receipt['response_view']['omitted_fields'])
        self.assertNotIn('checkpoint', receipt)
        self.assertLess(len(json.dumps(receipt)), 2500)
        self.assertEqual(self.ledger.item(self.key)['checkpoint'], before)
        full = self.result('renew', '--view', 'full')
        self.assertEqual(full['checkpoint'], before)
        self.assertNotIn('response_view', full)

    def test_checkpoint_acknowledges_complete_write_and_full_opt_in(self):
        path = Path(self.tmp.name) / 'progress 结果.json'
        path.write_text(json.dumps(self.progress), encoding='utf-8')
        receipt = self.result('checkpoint', '--input', str(path))
        self.assertTrue(receipt['checkpoint_saved'])
        self.assertNotIn('checkpoint', receipt)
        self.assertEqual(self.ledger.item(self.key)['checkpoint']['detail'], self.progress['detail'])
        args = cli.parser().parse_args(['--db', str(self.path), 'issue-context', '--item', self.key,
                                        '--view', 'work-item'])
        before_audit = self.ledger.connection.execute('SELECT count(*) FROM audit').fetchone()[0]
        readback = cli.run(args, self.ledger, None)
        self.assertEqual(readback['checkpoint']['detail'], self.progress['detail'])
        self.assertEqual(self.ledger.connection.execute('SELECT count(*) FROM audit').fetchone()[0], before_audit)
        self.assertNotIn(self.token, json.dumps(readback))
        self.ledger.push_inbox(self.key, 'Please review this request')
        self.ledger.observe_issue(issue(description='Changed requirement'))
        full = self.result('checkpoint', '--input', str(path), '--view', 'full')
        self.assertEqual(full['checkpoint']['detail'], self.progress['detail'])
        self.assertTrue(full['checkpoint_saved'])
        self.assertEqual(full['inbox_pending'], 1)
        self.assertTrue(full['requires_issue_review'])
        self.assertIn('human_question', full)

    def test_revalidation_receipt_preserves_fingerprints_and_only_acknowledges_review(self):
        old = self.ledger._row(self.key)['claimed_fingerprint']
        self.ledger.observe_issue(issue(description='Changed requirement'))
        current = self.ledger._issue_row(ISSUE)['fingerprint']
        self.assertNotEqual(current, old)
        self.assertTrue(self.result('renew')['requires_issue_review'])
        receipt = self.result('revalidate', '--fingerprint', current)
        self.assertEqual(receipt['previous_fingerprint'], old)
        self.assertEqual(receipt['claimed_fingerprint'], current)
        self.assertFalse(receipt['requires_issue_review'])
        self.assertNotIn('checkpoint', receipt)
        full = self.result('revalidate', '--fingerprint', current, '--view', 'full')
        self.assertIn('checkpoint', full)
        self.assertEqual(full['claimed_fingerprint'], current)
        self.assertFalse(full['requires_issue_review'])
        self.assertEqual(full['inbox_pending'], 0)
        self.assertIn('human_question', full)

    def test_maintenance_withdrawal_unread_input_and_question_survive_compaction(self):
        Maintenance(self.ledger).pause('planned update')
        self.ledger.flag_withdrawal(self.key, 'undelegated', self.now + 20)
        self.ledger.push_inbox(self.key, 'Please inspect the new request')
        question = {'id': 'question-1', 'body': 'Complete unresolved question ' * 200,
                    'after_message_id': 0}
        checkpoint = self.ledger.item(self.key)['checkpoint']
        checkpoint['human_question'] = question
        self.ledger.connection.execute('UPDATE work_items SET checkpoint=? WHERE id=?',
                                       (json.dumps(checkpoint), self.key))
        self.ledger.observe_issue(issue(description='Changed requirement'))
        for view in ('compact', 'full'):
            with self.subTest(view=view):
                receipt = self.result('renew', '--view', view)
                self.assertTrue(receipt['maintenance_requested'])
                self.assertEqual(receipt['withdraw_deadline'], self.now + 20)
                self.assertEqual(receipt['withdraw_reason'], 'undelegated')
                self.assertEqual(receipt['human_question'], question)
                self.assertEqual(receipt['inbox_pending'], 1)
                self.assertTrue(receipt['requires_issue_review'])

    def test_recovery_and_unknown_fields_are_not_silently_dropped_or_truncated(self):
        result = self.ledger.renew(self.key, self.token)
        result.update(candidate_repair={'reason': 'Preserve metadata', 'evidence': 'x' * 6000},
                      future_control={'exit_required': True, 'detail': 'y' * 6000})
        receipt = response(argparse.Namespace(command='renew', view='compact'), result)
        self.assertEqual(receipt['candidate_repair'], result['candidate_repair'])
        self.assertEqual(receipt['future_control'], result['future_control'])
        self.assertEqual(receipt['authority'], result['authority'])

    def test_bad_tokens_expired_claims_and_invalid_checkpoints_emit_only_errors(self):
        self.token_file.write_text('wrong', encoding='utf-8')
        code, out, err = self.invoke('renew')
        self.assertEqual((code, out), (1, ''))
        self.assertIn('LedgerError', err)
        self.token_file.write_text(self.token, encoding='utf-8')
        path = Path(self.tmp.name) / 'bad.json'
        path.write_text('{"summary":123}', encoding='utf-8')
        code, out, err = self.invoke('checkpoint', '--input', str(path))
        self.assertEqual((code, out), (1, ''))
        self.assertIn('checkpoint summary must be text', err)
        self.assertEqual(self.ledger.item(self.key)['checkpoint']['detail'], self.progress['detail'])
        self.now += 100
        code, out, err = self.invoke('renew')
        self.assertEqual((code, out), (1, ''))
        self.assertIn('LedgerError', err)

    def test_legacy_renew_and_other_response_shapes_remain_unchanged(self):
        self.ledger.connection.execute('UPDATE work_items SET execution_contract=? WHERE id=?',
                                       ('staged-v1', self.key))
        self.assertIn('checkpoint', self.result('renew'))
        result = self.ledger.item(self.key)
        result['execution_contract'] = 'single-worker-v1'
        for command in ('claim', 'resource-status', 'unity-tool', 'finish', 'release-resource'):
            self.assertIs(response(argparse.Namespace(command=command), result), result)

    def test_work_item_read_does_not_load_full_context_or_consume_inbox(self):
        self.ledger.push_inbox(self.key, 'An unrelated conversation ' * 2000)
        args = cli.parser().parse_args(['--db', str(self.path), 'issue-context', '--item', self.key])
        self.assertNotIn('work_item', cli.run(args, self.ledger, None))
        args.view = 'work-item'
        with patch.object(self.ledger, 'issue_context', side_effect=AssertionError('full context read')):
            result = cli.run(args, self.ledger, None)
        self.assertEqual(result['checkpoint']['detail'], self.progress['detail'])
        self.assertNotIn('session_messages', result)
        self.assertNotIn('conversation_history', result)
        self.assertNotIn('unrelated conversation', json.dumps(result))
        self.assertEqual(self.ledger.inbox_pending(self.key), 1)
        args.if_changed = 'retained-context-digest'
        from agent.ledger import LedgerError
        with self.assertRaisesRegex(LedgerError, '--if-changed requires --view resume'):
            cli.run(args, self.ledger, None)

    def receipt_commands(self):
        path = Path(self.tmp.name) / 'new progress.json'
        path.write_text(json.dumps({'summary': 'New progress'}), encoding='utf-8')
        fingerprint = self.ledger._issue_row(ISSUE)['fingerprint']
        return [('renew', ()), ('checkpoint', ('--input', str(path))),
                ('revalidate', ('--fingerprint', fingerprint))]

    def test_committed_receipts_need_no_more_database_reads(self):
        real_run = cli.run

        def deny_reads_after_commit(*args):
            result = real_run(*args)
            self.assertFalse(self.ledger.connection.in_transaction)
            self.ledger.connection.set_authorizer(
                lambda action, *unused: sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ else sqlite3.SQLITE_OK)
            return result

        for command, extra in self.receipt_commands():
            for view in ('compact', 'full'):
                with self.subTest(command=command, view=view):
                    try:
                        with patch.object(cli, 'run', side_effect=deny_reads_after_commit):
                            receipt = self.result(command, *extra, '--view', view)
                    finally:
                        self.ledger.connection.set_authorizer(None)
                    self.assertIn('inbox_pending', receipt)
                    if command == 'checkpoint':
                        self.assertTrue(receipt['checkpoint_saved'])
                        self.assertEqual(self.ledger.item(self.key)['checkpoint']['summary'], 'New progress')

    def test_signal_read_failure_rolls_back_write_and_audit(self):
        self.ledger.observe_issue(issue(description='New issue input'))
        for command, extra in self.receipt_commands():
            for view in ('compact', 'full'):
                with self.subTest(command=command, view=view):
                    self.now += 1
                    original = getattr(self.ledger, command)
                    before = {}

                    def snapshot_before_write(*args, **kwargs):
                        # Checkpoint preflight legitimately renews first. Compare
                        # against the state just before the actual write begins.
                        before['row'] = dict(self.ledger._row(self.key))
                        before['audits'] = self.ledger.connection.execute('SELECT count(*) FROM audit').fetchone()[0]
                        return original(*args, **kwargs)

                    def fail_signal_read(*unused):
                        self.assertTrue(self.ledger.connection.in_transaction)
                        raise sqlite3.OperationalError('receipt signal unavailable')

                    with (patch.object(self.ledger, command, side_effect=snapshot_before_write),
                          patch.object(self.ledger, 'requires_issue_review', side_effect=fail_signal_read)):
                        code, out, err = self.invoke(command, *extra, '--view', view)
                    self.assertEqual((code, out), (1, ''))
                    self.assertIn('OperationalError', err)
                    self.assertEqual(dict(self.ledger._row(self.key)), before['row'])
                    self.assertEqual(self.ledger.connection.execute('SELECT count(*) FROM audit').fetchone()[0], before['audits'])

    def test_legacy_question_keeps_consumed_message_floor_in_both_views(self):
        self.ledger.push_inbox(self.key, 'Old answer before this question')
        self.ledger.pop_inbox(self.key, self.token)
        old_message = self.ledger.connection.execute('SELECT max(id) FROM inbox').fetchone()[0]
        self.ledger.note(self.key, 'state', 'human gate')
        self.ledger.connection.execute('UPDATE work_items SET checkpoint=? WHERE id=?',
            (json.dumps({'pending_question': 'Which option?', 'pending_reason': 'question'}), self.key))
        self.now += 1
        self.ledger.push_inbox(self.key, 'New answer')
        for view in ('compact', 'full'):
            receipt = self.result('renew', '--view', view)
            self.assertEqual(receipt['human_question'], self.ledger.human_question(self.key))
            self.assertEqual(receipt['human_question']['after_message_id'], old_message)
            self.assertEqual(receipt['inbox_pending'], 1)

    def test_only_nonempty_context_is_omitted(self):
        result = self.ledger.renew(self.key, self.token)
        result.update(checkpoint={}, evidence=None, target=[], branch_target='', task_request=None)
        receipt = response(argparse.Namespace(command='renew'), result)
        self.assertEqual(receipt['response_view']['omitted_fields'], [])
        for key, value in result.items():
            self.assertEqual(receipt[key], value)
        result['checkpoint'] = {'summary': 'Saved progress'}
        receipt = response(argparse.Namespace(command='renew'), result)
        self.assertEqual(receipt['response_view']['omitted_fields'], ['checkpoint'])
        self.assertNotIn('checkpoint', receipt)

    def test_requeue_requires_review_even_with_matching_fingerprint(self):
        self.ledger.connection.execute('UPDATE work_items SET requeue_requested=1 WHERE id=?', (self.key,))
        for view in ('compact', 'full'):
            self.assertTrue(self.result('renew', '--view', view)['requires_issue_review'])

    def test_ledger_callers_opt_in_to_receipt_controls(self):
        for command, extra in self.receipt_commands():
            args = [self.key, self.token]
            if command == 'checkpoint':
                args.append({'summary': 'Internal API write'})
            elif command == 'revalidate':
                args.append(extra[1])
            result = getattr(self.ledger, command)(*args)
            self.assertIn('checkpoint', result)
            for signal in ('human_question', 'inbox_pending', 'requires_issue_review'):
                self.assertNotIn(signal, result)
