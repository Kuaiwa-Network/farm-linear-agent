"""Review feedback regressions: recovery, evidence fencing and terminal outcomes."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

from agent.review_controller import Controller
from agent.review_policy import ReviewUnavailable, encoded
from test_review import APP, BASE, ReviewFixture, policy
import test_review_controller as fixtures


class RegressionTests(ReviewFixture):
    def setUp(self):
        super().setUp()
        self.github = fixtures.GitHub()
        self.git = fixtures.Git(self.github)
        self.workers = fixtures.Workers(self.store)
        self.config = SimpleNamespace(auto_merge=policy(mode='merge'), repos={'common': 'unused'},
                                      instance_id='test', local_root=self.path.parent)
        self.api = SimpleNamespace(app_user_id=APP, fetch_issue=self.ledger.issue)
        self.controller = Controller(self.ledger, self.config, self.api, self.github, self.git, self.workers)

    def delivery(self, number=1, **changes):
        run, pr = super().delivery(number, **changes)
        self.store.db.execute('UPDATE review_runs SET policy=? WHERE id=?', (self.controller.run_policy(run['id']), run['id']))
        return self.store.row('review_runs', run['id']), pr

    good = fixtures.ControllerTests.good
    multi_delivery = fixtures.ControllerTests.multi_delivery

    def test_refresh_outage_respects_backoff_and_success_resets_only_that_phase(self):
        run, pr = self.delivery()
        self.api.fetch_issue = Mock(side_effect=OSError('outage'))
        self.controller.tick()
        for _ in range(5):
            self.now += 5
            self.controller.tick()
        self.assertEqual(self.api.fetch_issue.call_count, 1)
        self.assertEqual(self.store.row('review_prs', pr['id'])['retries'], 1)
        self.now += 60
        self.api.fetch_issue = self.ledger.issue
        self.controller.tick()
        self.assertEqual(self.store.row('review_prs', pr['id'])['retries'], 0)
        self.api.fetch_issue = Mock(side_effect=OSError('unrelated later blip'))
        self.controller.tick()
        self.assertEqual(self.store.row('review_prs', pr['id'])['retries'], 1)
        self.assertIsNone(self.store.row('review_runs', run['id'])['disposition'])

    def test_stale_pr_base_metadata_does_not_rebuild_forever(self):
        _, pr = self.delivery()
        original = self.github.pr
        self.github.pr = lambda row: {**original(row), 'base': {'sha': BASE}}
        self.controller.tick()
        first = self.good()
        self.github.base = 'e' * 40
        self.controller.tick()
        replacement = self.good()
        self.assertEqual(replacement['base'], self.github.base)
        self.assertNotEqual(first['id'], replacement['id'])
        self.github.states[replacement['commit']] = 'passed'
        self.controller.tick()
        self.assertEqual(self.controller.prs()[0]['state'], 'merged')
        self.assertEqual(self.controller.prs()[0]['rebuilds'], 1)

    def test_authority_identity_survives_restart_and_poll_cache_avoids_repeat_calls(self):
        self.delivery()
        self.github.authority = Mock()
        self.controller.tick()
        owner = self.github.authority.call_args.args[1]
        self.controller.tick()
        self.github.authority.assert_called_once()
        restarted = Controller(self.ledger, self.config, self.api, self.github, self.git, self.workers)
        restarted.tick()
        self.assertEqual(self.github.authority.call_args.args[1], owner)

    def test_authority_failure_does_not_block_terminal_notifications(self):
        run, _ = self.delivery()
        self.store.handoff(run['id'], 'high risk')
        self.github.authority = Mock(side_effect=OSError('unavailable'))
        self.controller.linear_notice = Mock()
        self.controller.tick()
        self.assertEqual(self.store.row('review_runs', run['id'])['notified'], 1)
        self.assertEqual(self.workers.launched, [])

    def test_operational_tuning_and_unrelated_repository_do_not_invalidate_review(self):
        run, _ = self.delivery()
        self.controller.tick()
        snapshot = self.good()
        self.config.auto_merge.update(workers=4, poll_seconds=600, retry_limit=4, circuit_limit=3,
                                      hold_labels=['needs-person', 'hold-auto-merge', 'manual-stop'])
        self.config.auto_merge['repositories']['Farm-Contract'] = dict(self.config.auto_merge['repositories']['common'])
        self.config.repos['Farm-Contract'] = 'unused'
        self.controller = Controller(self.ledger, self.config, self.api, self.github, self.git, self.workers)
        self.controller.tick()
        self.assertIsNone(self.store.row('review_runs', run['id'])['disposition'])
        self.assertEqual(self.controller.prs()[0]['candidate_id'], snapshot['id'])

    def test_check_policy_change_still_fences_old_evidence(self):
        run, _ = self.delivery()
        self.controller.tick()
        self.controller.policy['repositories']['common']['checks'][0]['app_id'] = 88
        self.controller.tick()
        self.assertEqual(self.store.row('review_runs', run['id'])['disposition'], 'human_handoff')

    def test_missing_ci_has_a_durable_deadline_without_code_repairs(self):
        run, _ = self.delivery()
        self.controller.tick()
        self.good()
        self.controller.tick()
        self.now += self.controller.policy['ci_timeout_seconds']
        restarted = Controller(self.ledger, self.config, self.api, self.github, self.git, self.workers)
        restarted.tick()
        self.assertEqual(self.store.row('review_runs', run['id'])['disposition'], 'human_handoff')
        self.assertEqual(self.controller.prs()[0]['repairs'], 0)

    def test_cancelled_ci_reruns_with_cooldown_then_can_merge(self):
        run, _ = self.delivery()
        self.controller.tick()
        snapshot = self.good()
        self.github.states[snapshot['commit']] = 'unavailable'
        self.controller.tick()
        self.controller.tick()
        self.assertEqual([c for c in self.github.calls if c[0] == 'rerun'], [('rerun', snapshot['commit'])])
        self.assertEqual(self.controller.prs()[0]['repairs'], 0)
        self.github.states[snapshot['commit']] = 'passed'
        self.controller.tick()
        self.assertEqual(self.store.row('review_runs', run['id'])['disposition'], 'auto_merged')

    def test_repeated_ci_cancellations_have_bounded_recovery(self):
        run, _ = self.delivery()
        self.controller.tick()
        snapshot = self.good()
        self.github.states[snapshot['commit']] = 'unavailable'
        for _ in range(4):
            self.controller.tick()
            self.now += 300
        self.assertEqual(sum(c[0] == 'rerun' for c in self.github.calls), 3)
        self.assertEqual(self.store.row('review_runs', run['id'])['disposition'], 'human_handoff')
        self.assertEqual(self.controller.prs()[0]['repairs'], 0)

    def test_high_risk_verdict_wins_over_failed_ci_without_launching_repair(self):
        run, _ = self.delivery()
        self.controller.tick()
        attempt = self.workers.launched[-1]
        snapshot = json.loads(self.store.row('review_candidates', attempt['candidate_id'])['snapshot'])
        self.workers.finish(attempt, self.verdict(snapshot, risk='high', reasons=['migration risk']))
        self.github.states[snapshot['commit']] = 'failed'
        self.controller.tick()
        self.assertEqual(self.store.row('review_runs', run['id'])['disposition'], 'human_handoff')
        self.assertEqual(len(self.workers.launched), 1)
        self.assertEqual(self.controller.prs()[0]['candidate_id'], snapshot['id'])

    def test_new_bot_feedback_requires_fresh_review_without_rebuilding_candidate(self):
        run, _ = self.delivery()
        self.controller.tick()
        snapshot = self.good()
        self.github.bot_feedback = lambda row: [{'id': 'bot-1', 'body': 'Possible overflow'}]
        self.controller.tick()
        self.assertEqual(len(self.workers.launched), 2)
        self.assertEqual(self.controller.prs()[0]['candidate_id'], snapshot['id'])
        record = self.store.row('review_candidates', snapshot['id'])
        self.assertFalse(self.store.inputs_current(run['id'], record['review_inputs']))
        self.assertIsNone(self.store.row('review_runs', run['id'])['disposition'])

    def test_missing_ancestry_evidence_preserves_pending_merge_intent(self):
        run, _ = self.delivery()
        self.controller.tick()
        snapshot = self.good()
        self.github.states[snapshot['commit']] = 'passed'
        self.git.ancestor = Mock(side_effect=ReviewUnavailable('object not fetched'))
        self.controller.tick()
        self.assertEqual(self.store.db.execute('SELECT state FROM review_merges').fetchone()[0], 'pending')
        self.assertIsNone(self.store.row('review_runs', run['id'])['disposition'])

    def test_superseded_partial_merge_is_reported_and_observed_for_incidents(self):
        run = self.multi_delivery()
        self.controller.tick()
        for attempt in self.workers.launched:
            snapshot = self.good(attempt)
            self.github.states[snapshot['commit']] = 'passed'
        merge = self.git.merge_exact
        def only_common(snapshot, base):
            if snapshot['repository'] == 'server':
                raise OSError('offline')
            return merge(snapshot, base)
        self.git.merge_exact = only_common
        self.controller.tick()
        self.assertEqual(self.controller.prs()[0]['state'], 'merged')
        self.store.handoff(run['id'], 'Issue changed after common merged', superseded=True)
        self.controller.linear_notice = Mock()
        self.github.reverts = Mock(return_value=['revert:confirmed-common'])
        self.now += 301
        self.controller.tick()
        self.assertEqual(self.store.row('review_runs', run['id'])['notified'], 1)
        self.github.reverts.assert_called_once()
        self.assertEqual(self.store.db.execute('SELECT kind FROM review_incidents').fetchone()[0], 'revert')
        self.assertIn('merged', self.controller.linear_notice.call_args.args[1])

    def test_historical_merged_candidates_are_not_scanned_for_active_review(self):
        _, pr = self.delivery()
        self.controller.tick()
        current = self.controller.candidate(self.controller.prs()[0])
        for n in range(100):
            snapshot = {**current, 'id': str(n), 'commit': f'{n + 50:040x}'}
            self.store.db.execute("INSERT INTO review_candidates(id,pr_id,snapshot,state,created_at) VALUES(?,?,?,'merged',?)",
                                  (snapshot['id'], pr['id'], encoded(snapshot), self.now))
        self.git.ancestor = Mock(side_effect=AssertionError('historical ancestry scan'))
        self.controller.tick()
        self.git.ancestor.assert_not_called()
        self.assertEqual(self.controller.prs()[0]['retries'], 0)
