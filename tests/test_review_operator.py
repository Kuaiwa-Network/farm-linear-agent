import os
from types import SimpleNamespace
from unittest.mock import patch

from agent.review_operator import control
from test_review import ReviewFixture, policy


class OperatorTests(ReviewFixture):
    def call(self, command, **kwargs):
        with patch('agent.review_operator.check_ownership'), \
                patch('agent.review_operator.Paths', return_value=SimpleNamespace(ledger=self.path)):
            return control(SimpleNamespace(auto_merge=policy(), repos={'common': 'unused'}), command, **kwargs)

    def test_pause_resume_is_audited_and_never_revives_human_handoffs(self):
        run, _ = self.delivery()
        self.store.handoff(run['id'], 'high risk')
        self.assertEqual(self.call('review-pause', reason='investigate')['control']['paused'], 1)
        self.assertEqual(self.call('review-resume', reason='resolved')['control']['paused'], 0)
        self.assertEqual(self.store.row('review_runs', run['id'])['disposition'], 'human_handoff')
        self.assertEqual([r[0] for r in self.store.db.execute("SELECT kind FROM audit WHERE item_id='review-control' ORDER BY id")],
                         ['review_pause', 'review_resume'])

    def test_worker_cannot_mutate_operator_controls(self):
        with patch.dict(os.environ, {'FARMBOT_ITEM_ID': 'worker'}), patch('agent.review_operator.Ledger') as ledger:
            with self.assertRaisesRegex(ValueError, 'host-operator'):
                self.call('review-resume', reason='worker asked')
            ledger.assert_not_called()

    def test_distinct_regressions_trip_circuit_but_replayed_events_do_not_repause(self):
        runs = [self.delivery(n)[0] for n in (1, 2)]
        for run in runs:
            self.store.db.execute("UPDATE review_runs SET state='delivered',disposition='auto_merged' WHERE id=?", (run['id'],))
        self.store.incident('revert-1', runs[0]['id'], 'revert', policy())
        self.store.incident('reopen-1', runs[0]['id'], 'reopen', policy())
        self.assertEqual(self.call('review-status')['control']['paused'], 0)
        self.store.incident('revert-2', runs[1]['id'], 'revert', policy())
        self.assertEqual(self.call('review-status')['control']['paused'], 1)
        self.call('review-resume', reason='fixed regressions')
        self.store.incident('revert-2', runs[1]['id'], 'revert', policy())
        self.assertEqual(self.call('review-status')['control']['paused'], 0)
