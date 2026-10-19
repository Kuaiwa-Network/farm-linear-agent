"""FARM-1561: approval review capacity failures park in the retry queue, not human input."""
import json
from pathlib import Path
from unittest.mock import Mock, patch

from agent.__main__ import parser, run
from agent.execution import queue_resource
from agent.launcher import Finished
from agent.ledger import LedgerError
from agent.maintenance import Maintenance
from test_ledger import LedgerBase, issue
import test_single_worker

# The returned review message from FARM-1561, independent of the classifier.
CAPACITY_REVIEW_ERROR = (
    'Automatic approval review failed: Selected model is at capacity. Please try a different model.\n'
    'The action was not executed because automatic approval review could not be completed. '
    'This is a review failure, not a determination that the action is unsafe. '
    'Do not bypass the approval check; resolve the error or ask the user for guidance.'
)


class ApprovalRetryTests(LedgerBase):
    owner = test_single_worker.SingleWorkerTests.owner
    claim_owner = test_single_worker.SingleWorkerTests.claim_owner
    scheduler = test_single_worker.SingleWorkerTests.scheduler
    reservation = test_single_worker.SingleWorkerTests.reservation

    def invoke(self, error=CAPACITY_REVIEW_ERROR, token=None):
        evidence = Path(self.tmp.name) / '审批 failure.txt'
        evidence.write_bytes(error.encode('utf-8'))
        token_file = Path(self.tmp.name) / 'private token'
        token_file.write_text(token or self.token, encoding='utf-8')
        args = parser().parse_args(['--db', str(self.path), 'retry-approval', '--item', self.key,
                                   '--token-file', str(token_file), '--error-file', str(evidence)])
        api = Mock(side_effect=AssertionError('retry must not publish or ask a human'))
        result = run(args, self.ledger, api)
        api.assert_not_called()
        return result

    def test_not_executed_review_failure_requeues_saved_publication_without_a_question(self):
        self.claim_owner()
        report = Path(self.tmp.name) / 'retained-results.json'
        report.write_text('{"passed":8,"failed":0}', encoding='utf-8')
        self.ledger.checkpoint(self.key, self.token, {
            'summary': 'Verified candidate; approval review unavailable before git push',
            'candidates': {'Farm-Client': 'a' * 40, 'farmgui': 'b' * 40},
            'reports': [str(report)], 'handoff': 'Reverify destination and retry git push through normal approval'})
        before = self.ledger.item(self.key)
        result = self.invoke()
        row = self.ledger.item(self.key)
        self.assertEqual(result['status'], 'retry_queued')
        self.assertEqual((row['state'], row['capacity_retries'], row['retry_not_before']), ('queued', 1, self.now + 60))
        self.assertEqual(row['checkpoint'], before['checkpoint'])
        self.assertEqual(row['generation'], before['generation'])
        self.assertEqual(row['publication_retries'], 0)
        self.assertNotIn('pending_question', row['checkpoint'])
        self.assertEqual(report.read_text(encoding='utf-8'), '{"passed":8,"failed":0}')
        with self.assertRaises(LedgerError):
            self.ledger.renew(self.key, self.token)
        reopened = self.open_ledger()
        self.assertEqual(reopened.item(self.key)['retry_not_before'], self.now + 60)
        self.assertEqual(reopened.queue(), [])
        with self.assertRaisesRegex(LedgerError, 'delay'):
            reopened.claim(self.key, worker_id='too-early')

    def test_only_complete_capacity_not_executed_error_qualifies(self):
        self.claim_owner()
        for error in ('Selected model is at capacity. Please try a different model.',
                      'Quoted example: ' + CAPACITY_REVIEW_ERROR,
                      CAPACITY_REVIEW_ERROR + '\nERROR: Unauthorized',
                      CAPACITY_REVIEW_ERROR.replace('Selected model is at capacity.', 'Action is unsafe.'),
                      CAPACITY_REVIEW_ERROR.replace('was not executed', 'may have been executed')):
            with self.subTest(error=error):
                with self.assertRaisesRegex(LedgerError, 'complete capacity review failure'):
                    self.invoke(error)
                row = self.ledger.item(self.key)
                self.assertEqual((row['state'], row['capacity_retries']), ('running', 0))
        self.assertEqual(self.invoke(CAPACITY_REVIEW_ERROR.replace('\n', '\r\n') + '\r\n')['status'], 'retry_queued')

    def test_bounded_retries_share_worker_capacity_allowance_and_survive_restart(self):
        self.claim_owner()
        # A terminal worker capacity failure already consumed the first allowance.
        self.ledger.defer_capacity_retry(self.key, 123)
        self.now += 60
        for attempt, delay in ((2, 180), (3, 600)):
            self.token = self.ledger.claim(self.key, worker_id='resumed')['token']
            result = self.invoke()
            self.assertEqual((result['attempt'], result['delay_seconds']), (attempt, delay))
            self.assertEqual(self.open_ledger().item(self.key)['capacity_retries'], attempt)
            self.now += delay
        self.token = self.ledger.claim(self.key, worker_id='last')['token']
        self.assertEqual(self.invoke()['status'], 'retry_exhausted')
        self.assertEqual(self.ledger.item(self.key)['state'], 'failed')
        self.assertNotIn('pending_question', self.ledger.item(self.key)['checkpoint'])
        self.assertEqual(self.ledger.retry(self.key, 'operator retries after outage')['capacity_retries'], 0)

    def test_stop_stale_claim_and_existing_human_wait_cannot_be_resumed(self):
        self.claim_owner()
        with self.assertRaisesRegex(LedgerError, 'matching token'):
            self.invoke(token='stale')
        self.ledger.await_input(self.key, self.token, 'Which behavior is required?')
        with self.assertRaises(LedgerError):
            self.invoke()
        self.assertEqual(self.ledger.item(self.key)['state'], 'awaiting_input')
        self.ledger.cancel(self.key, 'Stop')
        with self.assertRaises(LedgerError):
            self.invoke()
        self.assertEqual(self.ledger.item(self.key)['state'], 'cancelled')

    def test_expired_claim_withdrawal_and_closed_issue_cannot_queue(self):
        self.claim_owner()
        self.now += 61
        with self.assertRaisesRegex(LedgerError, 'lease expired'):
            self.invoke()
        self.now -= 61
        with self.ledger._transaction():
            self.ledger.connection.execute('UPDATE work_items SET withdraw_deadline=? WHERE id=?',
                                           (self.now + 30, self.key))
        with self.assertRaises(LedgerError):
            self.invoke()
        with self.ledger._transaction():
            self.ledger.connection.execute('UPDATE work_items SET withdraw_deadline=NULL WHERE id=?', (self.key,))
        self.ledger.observe_issue(issue(status_type='completed'))
        with self.assertRaisesRegex(LedgerError, 'terminal status'):
            self.invoke()
        self.assertEqual(self.ledger.item(self.key)['capacity_retries'], 0)

    def test_resource_must_settle_before_retry_can_retire_claim(self):
        self.claim_owner()
        queue_resource(self.ledger, self.key, self.token, 'unity_slot', 'interactive', 'a' * 40)
        with self.assertRaisesRegex(LedgerError, 'release the resource'):
            self.invoke()
        self.assertEqual(self.ledger.item(self.key)['state'], 'running')
        self.assertEqual(self.ledger.item(self.key)['capacity_retries'], 0)

    def test_acquired_resource_keeps_current_claim(self):
        self.claim_owner()
        self.reservation()
        with self.assertRaisesRegex(LedgerError, 'release the resource'):
            self.invoke()
        self.assertEqual(self.ledger.item(self.key)['state'], 'running')

    def test_unreadable_evidence_does_not_retire_claim_and_invalid_token_is_checked_first(self):
        self.claim_owner()
        args = parser().parse_args(['--db', str(self.path), 'retry-approval', '--item', self.key,
                                   '--token', self.token, '--error-file', str(Path(self.tmp.name) / 'missing')])
        with self.assertRaises(OSError):
            run(args, self.ledger, Mock())
        args.token = 'stale'
        with self.assertRaisesRegex(LedgerError, 'matching token'):
            run(args, self.ledger, Mock())
        self.assertEqual(self.ledger.item(self.key)['capacity_retries'], 0)

    def test_scheduler_retires_old_worker_checks_teardown_and_resumes_same_candidate(self):
        self.owner()
        scheduler = self.scheduler()
        self.assertEqual(scheduler.tick()['launched'], 1)
        old_pid = self.ledger.item(self.key)['worker_pid']
        self.token = self.ledger.claim(self.key, worker_id='original')['token']
        self.ledger.checkpoint(self.key, self.token, {'summary': '8/8 passed; push not executed',
            'candidates': {'Farm-Client': 'a' * 40}, 'reports': ['retained.json'],
            'handoff': 'Retry approval, then publish existing commits'})
        self.invoke()
        self.assertEqual(scheduler.tick()['launched'], 0)
        self.assertIn(self.key, self.launcher.stopped)
        self.launcher.finished.append(Finished(self.key, 0, '', False, 'exited', None, old_pid))
        scheduler.tick()
        self.now += 60
        scheduler = self.scheduler()  # controller restart loses in-memory handles
        with patch.object(self.launcher, 'assert_quiescent', side_effect=RuntimeError('descendants remain')):
            self.assertEqual(scheduler.tick()['launched'], 0)
        scheduler.preflight = lambda _: False
        self.assertEqual(scheduler.tick()['launched'], 0)
        scheduler.preflight = lambda _: True
        Maintenance(self.ledger).pause('upgrade')
        self.assertEqual(scheduler.tick()['launched'], 0)
        Maintenance(self.ledger).resume()
        with patch.object(self.trees, 'add', wraps=self.trees.add) as add:
            self.assertEqual(scheduler.tick()['launched'], 1)
        self.assertTrue(add.call_args_list)
        self.assertTrue(all(call.kwargs.get('refresh') is False for call in add.call_args_list))
        prompt = self.launcher.spawned[-1][1]
        payload = json.loads(prompt.split('\n\n', 1)[1])
        self.assertEqual(payload['checkpoint']['reports'], ['retained.json'])
        self.assertIn('retry-approval --item', prompt)
        self.assertIn('same normal automatic approval again', prompt)
        self.assertIn('verify-publication immediately before each mutation', prompt)
        self.assertFalse(any(row[0] == 'removed' for row in self.trees.added))
        self.assertEqual(self.ledger.item(self.key)['state'], 'queued')
        new = self.ledger.claim(self.key, worker_id='resumed')
        self.assertNotEqual(new['token'], self.token)

