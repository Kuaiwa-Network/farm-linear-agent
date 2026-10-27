"""Compact CLI output must retain control signals and never change stored evidence."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
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
                                        '--view', 'full', '--include-work-item'])
        before_audit = self.ledger.connection.execute('SELECT count(*) FROM audit').fetchone()[0]
        readback = cli.run(args, self.ledger, None)
        self.assertEqual(readback['work_item']['checkpoint']['detail'], self.progress['detail'])
        self.assertEqual(self.ledger.connection.execute('SELECT count(*) FROM audit').fetchone()[0], before_audit)
        self.assertNotIn(self.token, json.dumps(readback))
        full = self.result('checkpoint', '--input', str(path), '--view', 'full')
        self.assertEqual(full['checkpoint']['detail'], self.progress['detail'])

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
        receipt = self.result('renew')
        self.assertTrue(receipt['maintenance_requested'])
        self.assertEqual(receipt['withdraw_deadline'], self.now + 20)
        self.assertEqual(receipt['withdraw_reason'], 'undelegated')
        self.assertEqual(receipt['human_question'], question)
        self.assertEqual(receipt['inbox_pending'], 1)
        self.assertEqual(self.result('renew')['inbox_pending'], 1)

    def test_recovery_and_unknown_fields_are_not_silently_dropped_or_truncated(self):
        result = self.ledger.renew(self.key, self.token)
        result.update(candidate_repair={'reason': 'Preserve metadata', 'evidence': 'x' * 6000},
                      future_control={'exit_required': True, 'detail': 'y' * 6000})
        receipt = response(argparse.Namespace(command='renew', view='compact'), result, self.ledger)
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
            self.assertIs(response(argparse.Namespace(command=command), result, self.ledger), result)

    def test_full_work_item_read_is_explicit_and_cannot_bloat_resume(self):
        args = cli.parser().parse_args(['--db', str(self.path), 'issue-context', '--item', self.key])
        self.assertNotIn('work_item', cli.run(args, self.ledger, None))
        args.view = 'resume'
        args.include_work_item = True
        from agent.ledger import LedgerError
        with self.assertRaisesRegex(LedgerError, '--include-work-item requires --view full'):
            cli.run(args, self.ledger, None)
