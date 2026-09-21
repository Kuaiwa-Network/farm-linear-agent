import json
import ssl
import unittest
import urllib.error
from unittest.mock import Mock, patch
import test_cli
import test_scheduler
import test_publication
from agent.launcher import Finished
from agent.__main__ import run
from agent.ledger import Ledger, LedgerError
from agent import publication


class PublicationRetryTests(unittest.TestCase):
    setUp = test_cli.CliTests.setUp
    publication_fixture = test_cli.CliTests.publication_fixture
    verification_fixture = test_cli.CliTests.verification_fixture
    seeded_item = test_cli.CliTests.seeded_item
    json_file = test_cli.CliTests.json_file
    run_cli = test_cli.CliTests.run_cli

    def invoke(self, args, ledger, config, api, github, sleeper=None):
        with patch('agent.__main__.load_config', return_value=config), patch('agent.publication.github_api', side_effect=github), patch('agent.publication_retry.time.sleep', side_effect=sleeper):
            return run(args, ledger, lambda: api)

    def test_linear_timeout_retries_fresh_verification_then_succeeds(self):
        args, ledger, config, api, github = self.publication_fixture()
        issue = api.fetch_issue(None)
        api.fetch_issue = Mock(side_effect=[urllib.error.URLError(TimeoutError('handshake timed out')), issue])
        result = self.invoke(args, ledger, config, api, github)
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(api.fetch_issue.call_count, 2)
        self.assertEqual(ledger.item(args.item)['state'], 'running')

    def test_transient_exhaustion_queues_durably_and_revokes_claim(self):
        args, ledger, config, api, github = self.publication_fixture()
        api.fetch_issue = Mock(side_effect=TimeoutError('read timed out'))
        result = self.invoke(args, ledger, config, api, github)
        self.assertEqual(result['status'], 'retry_queued')
        self.assertEqual(result['delay_seconds'], 60)
        self.assertEqual(api.fetch_issue.call_count, 3)
        row = ledger.item(args.item)
        self.assertEqual(row['state'], 'queued')
        self.assertIsNone(row['worker_pid'])
        self.assertEqual(row['publication_retries'], 1)
        with self.assertRaises(LedgerError):
            ledger.renew(args.item, args.token)
        other = Ledger(args.db)
        try:
            self.assertEqual(other.item(args.item)['publication_retries'], 1)
            self.assertEqual(other.queue(), [])
        finally:
            other.close()

    def assert_not_retried(self, error):
        args, ledger, config, api, github = self.publication_fixture()
        api.fetch_issue = Mock(side_effect=error)
        with self.assertRaises(type(error)):
            self.invoke(args, ledger, config, api, github)
        self.assertEqual(api.fetch_issue.call_count, 1)
        self.assertEqual(ledger.item(args.item)['state'], 'running')

    def test_permission_failure_never_requeues(self):
        error = urllib.error.HTTPError('url', 403, 'denied', {}, None)
        try:
            self.assert_not_retried(error)
        finally:
            error.close()

    def test_certificate_failure_never_requeues(self):
        self.assert_not_retried(urllib.error.URLError(ssl.SSLCertVerificationError('bad certificate')))

    def test_job_retries_are_bounded_and_human_retry_resets_allowance(self):
        args, ledger, config, api, github = self.publication_fixture()
        now = [ledger.clock()]
        ledger.clock = lambda: now[0]
        api.fetch_issue = Mock(side_effect=TimeoutError())
        for attempt, delay in enumerate((60, 180, 600), 1):
            result = self.invoke(args, ledger, config, api, github)
            self.assertEqual(result['attempt'], attempt)
            self.assertEqual(result['delay_seconds'], delay)
            now[0] += delay
            args.token = ledger.claim(args.item, worker_id='retry')['token']
        result = self.invoke(args, ledger, config, api, github)
        self.assertEqual(result['status'], 'retry_exhausted')
        self.assertEqual(ledger.item(args.item)['state'], 'failed')
        self.assertEqual(api.fetch_issue.call_count, 12)
        row = ledger.retry(args.item, 'human retries after outage')
        self.assertEqual(row['publication_retries'], 0)
        self.assertEqual(row['retry_not_before'], 0)

    def test_cancellation_during_backoff_prevents_next_network_call(self):
        args, ledger, config, api, github = self.publication_fixture()
        api.fetch_issue = Mock(side_effect=TimeoutError())
        with self.assertRaises(LedgerError):
            self.invoke(args, ledger, config, api, github, lambda _: ledger.cancel(args.item, 'stop'))
        self.assertEqual(api.fetch_issue.call_count, 1)
        self.assertEqual(ledger.item(args.item)['state'], 'cancelled')

    def test_changed_delegation_on_retry_cannot_publish(self):
        args, ledger, config, api, github = self.publication_fixture()
        revoked = {**api.fetch_issue(None), 'delegate_id': None}
        api.fetch_issue = Mock(side_effect=[TimeoutError(), revoked])
        with self.assertRaisesRegex(LedgerError, 'delegated'):
            self.invoke(args, ledger, config, api, github)
        self.assertEqual(api.fetch_issue.call_count, 2)

    def test_github_transient_failure_rechecks_linear_too(self):
        args, ledger, config, api, github = self.publication_fixture()
        current = api.fetch_issue(None);api.fetch_issue = Mock(return_value=current)
        calls = []
        def flaky(endpoint, **kwargs):
            calls.append(endpoint)
            if len(calls) == 1:
                raise publication.PublicationUnavailable('temporary')
            return github(endpoint, **kwargs)
        self.assertEqual(self.invoke(args, ledger, config, api, flaky)['status'], 'verified')
        self.assertEqual(api.fetch_issue.call_count, 2)


class PublicationSchedulerTests(unittest.TestCase):
    setUp = test_scheduler.SchedulerTests.setUp
    item = test_scheduler.SchedulerTests.item

    def test_unavailable_delegation_preflight_keeps_retry_queued_without_launch(self):
        item = self.item()
        self.scheduler.tick()
        token = self.ledger.claim(item['id'], worker_id='first')['token']
        self.ledger.defer_publication_retry(item['id'], token, 'farmgui')
        self.scheduler.tick()
        self.now += 60
        self.scheduler.preflight = lambda _: False
        self.assertEqual(self.scheduler.tick()['launched'], 0)
        row = self.ledger.item(item['id'])
        self.assertEqual(row['state'], 'queued')
        self.assertEqual(row['publication_retries'], 1)

    def test_queued_retry_stops_old_worker_and_preserves_work_for_next_claim(self):
        item = self.item()
        self.scheduler.tick()
        old_pid = self.ledger.item(item['id'])['worker_pid']
        token = self.ledger.claim(item['id'], worker_id='first')['token']
        self.ledger.defer_publication_retry(item['id'], token, 'farmgui')
        self.assertEqual(self.scheduler.tick()['launched'], 0)
        self.assertIn(item['id'], self.launcher.stopped)
        self.launcher.finished.append(Finished(item['id'], 1, '', True, 'stopped', None, old_pid))
        self.scheduler.tick()
        self.assertEqual(self.ledger.item(item['id'])['state'], 'queued')
        self.assertFalse(any(row[0] == 'removed' for row in self.trees.added))
        self.now += 60
        original_add = self.trees.add
        refreshes = []
        def add(repo, item_id, branch, *, refresh=True):
            refreshes.append(refresh)
            return original_add(repo, item_id, branch)
        self.trees.add = add
        self.assertEqual(self.scheduler.tick()['launched'], 1)
        self.assertTrue(refreshes)
        self.assertFalse(any(refreshes))
        new = self.ledger.claim(item['id'], worker_id='second')
        self.assertNotEqual(new['token'], token)
        self.assertEqual(self.ledger.item(item['id'])['publication_retries'], 1)


class PublicationWorktreeTests(unittest.TestCase):
    setUp = test_publication.PublicationTests.setUp

    def test_publication_retry_reuses_existing_worktree_without_network_fetch(self):
        with patch.object(self.trees, 'fetch', side_effect=RuntimeError('GitHub still offline')):
            reused = self.trees.add('farmgui', 'job', self.branch, refresh=False)
        self.assertEqual(reused, self.trees.worktrees_root / 'job' / 'farmgui')


class TransportClassificationTests(unittest.TestCase):
    def test_github_timeout_is_retryable_but_denial_is_not(self):
        import subprocess
        with patch('agent.publication.subprocess.run', side_effect=subprocess.TimeoutExpired('gh',20)):
            with self.assertRaises(publication.PublicationUnavailable):
                publication.github_api('repos/example/repo')
        for status in (401,403,404):
            result = subprocess.CompletedProcess('gh',1,json.dumps({'status':status}), 'denied')
            with patch('agent.publication.subprocess.run',return_value=result), self.assertRaises(publication.PublicationError) as caught:
                publication.github_api('repos/example/repo')
            self.assertNotIsInstance(caught.exception, publication.PublicationUnavailable)

    def test_github_transient_http_and_tls_errors_are_retryable(self):
        import subprocess
        for stdout, stderr in ((json.dumps({'status':503}), ''), ('', 'HTTP 502'),
                               ('', 'net/http: TLS handshake timeout'), ('', 'unexpected EOF')):
            with self.subTest(stderr=stderr, stdout=stdout):
                result = subprocess.CompletedProcess('gh',1,stdout,stderr)
                with patch('agent.publication.subprocess.run',return_value=result), self.assertRaises(publication.PublicationUnavailable):
                    publication.github_api('repos/example/repo')

    def test_certificate_errors_are_not_temporary(self):
        from agent.publication_retry import transient
        self.assertFalse(transient(urllib.error.URLError(ssl.SSLCertVerificationError('certificate'))))
        self.assertTrue(transient(urllib.error.URLError(TimeoutError('timeout'))))
