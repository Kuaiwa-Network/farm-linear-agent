import json
from types import SimpleNamespace
from unittest.mock import Mock

from agent.equal_tree_verification import select_candidate
from test_server_slots import ServerPoolFixture
import test_verification_pair as fixtures


class EqualTreeQueueTests(ServerPoolFixture):
    BASE, CANDIDATE = 'b'*40, 'c'*40
    baseline = fixtures.VerificationPairTests.baseline
    settled = fixtures.VerificationPairTests.settled
    candidate_first = fixtures.VerificationPairTests.candidate_first
    reuse = fixtures.VerificationPairTests.reuse

    def trees(self, same=True):
        return SimpleNamespace(verification_tree=Mock(side_effect=['a'*40, ('a' if same else 'd')*40]))

    def test_unstarted_legacy_baseline_executes_actual_candidate_once(self):
        item, lease = self.baseline()
        selected = select_candidate(self.ledger, self.trees(), lease)
        self.assertEqual(selected['commit_sha'], self.CANDIDATE)
        for key in ('reservation_id', 'sequence', 'resource', 'token', 'acquired_at'):
            self.assertEqual(selected[key], lease[key])
        self.assertIsNone(self.ledger.reservation_comparison(lease['reservation_id']))
        self.assertEqual(self.ledger.candidate_verification_base(lease['reservation_id']), self.BASE)
        self.settled(item, selected)
        self.assertIsNotNone(self.ledger.client_candidate_round(item, self.CANDIDATE))
        audit = json.loads(self.ledger.connection.execute(
            "SELECT details FROM audit WHERE kind='verification_equal_tree'").fetchone()[0])
        self.assertFalse(audit['baseline_executed'])
        self.assertNotIn(lease['token'], json.dumps(audit))
        self.assertEqual(len(self.ledger.reservations()), 1)

    def test_different_tree_keeps_two_real_sources(self):
        item, lease = self.baseline()
        self.assertEqual(select_candidate(self.ledger, self.trees(False), lease), lease)
        self.assertIsNotNone(self.ledger.reservation_comparison(lease['reservation_id']))

    def test_started_worker_is_not_relabelled_even_when_trees_match(self):
        item, lease = self.baseline()
        self.ledger.resume(item, 'already handed over')
        self.ledger.claim(item, worker_id='actual baseline')
        selected = select_candidate(self.ledger, self.trees(), lease)
        self.assertEqual(selected['commit_sha'], self.BASE)
        self.assertIsNotNone(self.ledger.reservation_comparison(lease['reservation_id']))

    def test_retained_candidate_comparison_does_not_rewrite_executions(self):
        item, token, candidate = self.candidate_first()
        self.reuse(item, token, candidate)
        lease = self.ledger.acquire('unity_slot', owner='fixture', host='test')
        trees = self.trees()
        self.assertEqual(select_candidate(self.ledger, trees, lease), lease)
        trees.verification_tree.assert_not_called()

    def test_retry_after_partial_preparation_keeps_recovery_evidence(self):
        item, lease = self.baseline()
        self.ledger.release(lease['reservation_id'], lease['token'], 'preparation interrupted')
        self.ledger.requeue_reservation(lease['reservation_id'], 'retry preparation')
        self.ledger.set_slot_state('unity_slot:1', 'idle_open')
        retried = self.ledger.acquire('unity_slot', owner='fixture', host='test')
        trees = self.trees()
        self.assertEqual(select_candidate(self.ledger, trees, retried), retried)
        trees.verification_tree.assert_not_called()
