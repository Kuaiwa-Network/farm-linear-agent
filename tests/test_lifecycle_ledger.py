"""Cancellation is durable history; successor execution starts with fresh authority."""
from agent.ledger import LedgerError
from test_ledger import LedgerBase, ISSUE, PIN, SESSION, issue
from test_receiver import APP


class LifecycleLedgerTests(LedgerBase):
    def test_cancel_is_repeatable_and_revokes_claim_without_losing_cleanup_pid(self):
        job = self.new_item(delegate_id=APP)
        self.ledger.set_worker(job['id'], 4321, 'host')
        token = self.ledger.claim(job['id'], worker_id='worker')['token']
        self.ledger.cancel(job['id'], 'issue closed')
        self.ledger.cancel(job['id'], 'duplicate notification')
        with self.assertRaises(LedgerError):
            self.ledger.renew(job['id'], token)
        self.assertEqual(self.ledger.cleanup_record(job['id'])['worker_pid'], 4321)
        self.assertEqual(self.ledger.item(job['id'])['state'], 'cancelled')

    def test_cancelled_retry_creates_fresh_execution_and_keeps_old_history(self):
        job = self.new_item(delegate_id=APP)
        token = self.ledger.claim(job['id'], worker_id='worker')['token']
        self.ledger.checkpoint(job['id'], token, {'progress': 'Unfinished fix needs verification'})
        self.ledger.cancel(job['id'], 'Stop')
        new = self.ledger.retry(job['id'], 'Please restart')
        self.assertNotEqual(new['id'], job['id'])
        self.assertEqual(new['predecessor_id'], job['id'])
        self.assertEqual(new['generation'], 0)
        self.assertIsNone(new['worker_pid'])
        self.assertIsNone(new['lease_expires_at'])
        self.assertIsNone(new['needs_resource'])
        self.assertEqual(self.ledger.item(job['id'])['state'], 'cancelled')
        fresh = self.ledger.claim(new['id'], worker_id='fresh')['token']
        self.assertNotEqual(fresh, token)
        context = self.ledger.issue_context(new['id'])
        self.assertEqual(context['recovery']['predecessor_id'], job['id'])
        self.assertTrue(context['recovery']['revalidation_required'])
        self.assertIn('Unfinished fix', context['recovery']['checkpoint']['progress'])
        with self.assertRaises(LedgerError):
            self.ledger.retry(job['id'], 'duplicate request')

    def test_closed_cancelled_job_cannot_be_retried(self):
        job = self.new_item()
        self.ledger.cancel(job['id'], 'closed')
        self.ledger.observe_issue(issue(status_type='completed'))
        with self.assertRaisesRegex(LedgerError, 'terminal'):
            self.ledger.retry(job['id'], 'restart')
        self.assertEqual(len(self.ledger.status()['items']), 1)

    def test_every_closed_status_type_leaves_scope_and_refuses_new_work(self):
        for status_type in ('completed', 'canceled', 'duplicate'):
            with self.subTest(status_type=status_type):
                self.assertFalse(self.ledger.observe_issue(issue(status_type=status_type))['in_scope'])
                self.ledger.ensure_session(SESSION, ISSUE, delegation=True)
                with self.assertRaisesRegex(LedgerError, 'terminal'):
                    self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill='fix', target=PIN)
        self.assertTrue(self.ledger.observe_issue(issue(status='In Review', status_type='started'))['in_scope'])
        self.assertEqual(self.ledger.status()['items'], [])

    def test_cleanup_evidence_survives_reopen_and_successor_sees_latest_record(self):
        job = self.new_item()
        self.ledger.cancel(job['id'], 'stop')
        new = self.ledger.retry(job['id'], 'restart')
        saved = {'committed': {'Farm-Client': 'a' * 40}, 'refs': {'Farm-Client': 'refs/farmbot/recovery/x'}, 'errors': {}}
        self.ledger.record_cleanup(job['id'], saved, error='removal failed')
        self.ledger.close()
        self.ledger = self.open_ledger()
        self.assertEqual(self.ledger.cleanup_record(job['id'])['result'], saved)
        self.assertEqual(self.ledger.issue_context(new['id'])['recovery']['cleanup']['error'], 'removal failed')

    def test_new_delegation_links_cancelled_fix_but_chat_does_not(self):
        job = self.new_item()
        self.ledger.cancel(job['id'], 'closed')
        new = self.ledger.create_work_item(issue_id=ISSUE, session_id=job['session_id'], skill='fix', target=PIN)
        self.assertEqual(new['predecessor_id'], job['id'])
        self.assertEqual(self.ledger.item(job['id'])['state'], 'cancelled')

    def test_existing_paused_answer_still_resumes_same_job(self):
        job = self.new_item()
        token = self.ledger.claim(job['id'], worker_id='worker')['token']
        self.ledger.await_input(job['id'], token, 'Which server?')
        self.ledger.push_inbox(job['id'], 'Public test', resume_waiting=True)
        self.assertEqual(self.ledger.item(job['id'])['state'], 'queued')
        self.assertEqual(len(self.ledger.status()['items']), 1)
