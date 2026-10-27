import contextlib
import io
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

from agent.review_cli import main
from test_review import ReviewFixture, policy


class CliTests(ReviewFixture):
    def setUp(self):
        super().setUp()
        self.config = SimpleNamespace(auto_merge=policy(), repos={'common': 'unused'})
        self.paths = SimpleNamespace(ledger=self.path, runs=self.path.parent / 'runs')

    def call(self, attempt, command, *extra, worker=None):
        output = io.StringIO()
        token = self.paths.runs / 'review' / attempt['id'] / 'token'
        token.parent.mkdir(parents=True, exist_ok=True)
        with patch('agent.review_cli.check_worker_state'), patch('agent.review_cli.load_config', return_value=self.config), \
                patch('agent.review_cli.Paths', return_value=self.paths), \
                patch.dict(os.environ, {'FARMBOT_ITEM_ID': worker or attempt['id']}), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = main(['--db', str(self.path), command, '--attempt', attempt['id'], '--token-file', str(token), *extra])
        return result, output.getvalue(), token

    def test_claim_submit_replay_and_token_privacy(self):
        snapshot = self.candidate(self.delivery()[1])
        attempt = self.store.attempt(snapshot['id'], 'review')
        status, text, path = self.call(attempt, 'claim')
        self.assertEqual(status, 0, text)
        secret = path.read_text(encoding='utf-8')
        self.assertNotIn(secret, text)
        report = path.parent / 'report.json'
        report.write_text(json.dumps(self.verdict(snapshot)), encoding='utf-8')
        status, text, _ = self.call(attempt, 'submit', '--input', str(report))
        self.assertEqual(status, 0, text)
        self.assertNotIn(secret, text)
        self.assertEqual(self.call(attempt, 'submit', '--input', str(report))[0], 1)
        self.assertEqual(self.store.row('review_attempts', attempt['id'])['state'], 'submitted')

    def test_wrong_worker_refuses_before_opening_ledger_or_creating_token(self):
        snapshot = self.candidate(self.delivery()[1])
        attempt = self.store.attempt(snapshot['id'], 'review')
        with patch('agent.review_cli.Ledger') as ledger:
            status, _, path = self.call(attempt, 'claim', worker='another-worker')
        self.assertEqual(status, 1)
        ledger.assert_not_called()
        self.assertFalse(path.exists())
