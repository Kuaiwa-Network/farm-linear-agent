import copy
import hashlib
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent.candidate_verification import assess, failure_id
from agent.test_baseline import ComparisonError, compare
from agent.verification import verify_recorded_candidate
from agent.ledger import LedgerError
from test_test_baseline import run, test as leaf
import test_verification_pair as pair_fixture


def scope_for(candidate, base=None, paths=None):
    base = base or candidate['commit']
    paths = [] if paths is None else paths
    affected = ['Animal.Delivery']
    return {'source_base_commit': base, 'changed_paths': paths, 'affected_tests': affected,
            'issue_scope': {'source_base_commit': base, 'candidate_commit': candidate['commit'],
                            'changed_paths': list(paths), 'affected_tests': list(affected),
                            'requirements': ['online delivery refreshes slot without crediting points'],
                            'reason': 'review actual change and notification/model/view dependencies',
                            'evidence': 'retained complete diff, acceptance and dependency review'},
            'unrelated_failures': {}, 'reviewed_skips': {}}


class CandidateAssessmentTests(unittest.TestCase):
    def setUp(self):
        self.candidate = run('b', [leaf('Animal.Delivery'),
            leaf('Card.Other', 'Failed', 'missing widget', 'at Card.Other in Assets/Tests/Card.cs:42'),
            leaf('Unix.Optional', 'Skipped', 'Unix only')])
        self.scope = scope_for(self.candidate)

    def review(self):
        for row in self.candidate['tests'][1:]:
            skipped = row['state'] == 'Skipped'
            self.scope['reviewed_skips' if skipped else 'unrelated_failures'][row['fullName']] = {
                'classification': 'not_applicable' if skipped else 'unchanged_source',
                'evidence_id': failure_id(self.candidate, self.scope['source_base_commit'], row['fullName'], row),
                'reason': 'unchanged card path is outside slot notification acceptance' if not skipped else
                          'Unix process implementation is not applicable on this Windows host',
                'evidence': 'source/acceptance/platform review and complete raw leaves'}

    def test_same_source_uses_one_real_run_without_relabeling_failure(self):
        self.review()
        original = copy.deepcopy((self.candidate, self.scope))
        result = assess(self.candidate, **self.scope)
        self.assertTrue(result['draft_eligible'])
        self.assertFalse(result['baseline_executed'])
        self.assertFalse(result['full_suite_passed'])
        self.assertNotIn('baseline_job', result)
        self.assertNotIn('baseline_commit', result)
        self.assertEqual(result['candidate_counts'], self.candidate['summary'])
        self.assertEqual((self.candidate, self.scope), original)

    def test_unreviewed_failures_skips_and_changed_source_need_real_evidence(self):
        initial = assess(self.candidate, **self.scope)
        self.assertFalse(initial['draft_eligible'])
        self.assertFalse(initial['comparison_needed'])  # Same-source uncertainty needs diagnosis, not a duplicate suite.
        self.review()
        self.scope['source_base_commit'] = self.scope['issue_scope']['source_base_commit'] = 'a'*40
        result = assess(self.candidate, **self.scope)
        self.assertFalse(result['draft_eligible'])
        self.assertTrue(result['comparison_needed'])
        self.assertEqual(result['unresolved_failures'], ['Card.Other'])

    def test_affected_failed_skipped_missing_or_ambiguous_are_never_excused(self):
        for kind in ('failed', 'skipped', 'missing', 'duplicate', 'invalid'):
            with self.subTest(kind=kind):
                self.setUp(); self.review()
                if kind == 'failed':
                    self.candidate['tests'][0] = leaf('Animal.Delivery', 'Failed', 'wrong slot')
                elif kind == 'skipped':
                    self.candidate['tests'][0] = leaf('Animal.Delivery', 'Skipped', 'missing input')
                elif kind == 'missing': self.candidate['tests'].pop(0)
                elif kind == 'duplicate': self.candidate['tests'].append(copy.deepcopy(self.candidate['tests'][1]))
                else: self.candidate['tests'][1]['state'] = 'Failed:Invalid'
                self.candidate = run('b', self.candidate['tests'])
                self.assertFalse(assess(self.candidate, **self.scope)['draft_eligible'])

    def test_distinct_commits_need_host_tree_proof_and_bound_review(self):
        self.scope = scope_for(self.candidate, 'a'*40)
        self.review()
        self.assertFalse(assess(self.candidate, **self.scope)['draft_eligible'])
        result = assess(self.candidate, **self.scope, verified_source_tree='e'*40)
        self.assertTrue(result['draft_eligible'])
        self.assertFalse(result['baseline_executed'])
        self.assertFalse(result['comparison_needed'])
        self.assertFalse(result['full_suite_passed'])
        self.scope['affected_tests'].append('Card.Other')
        self.scope['issue_scope']['affected_tests'] = list(self.scope['affected_tests'])
        self.assertFalse(assess(self.candidate, **self.scope, verified_source_tree='e'*40)['draft_eligible'])

    def test_review_is_bound_to_execution_and_environment(self):
        for kind in ('job_id', 'reservation_id', 'message', 'host'):
            with self.subTest(kind=kind):
                self.setUp(); self.review()
                if kind == 'message': self.candidate['tests'][1]['message'] = 'different symptom'
                elif kind == 'host': self.candidate['environment']['host'] = 'other'
                else: self.candidate[kind] = 'other'
                self.assertFalse(assess(self.candidate, **self.scope)['draft_eligible'])

    def test_malformed_scope_partial_results_and_false_same_source_diff_fail_closed(self):
        for kind in ('counts', 'complete', 'scope', 'requirements', 'alias', 'same_diff', 'skips'):
            with self.subTest(kind=kind):
                self.setUp(); self.review()
                if kind == 'counts': self.candidate['summary']['total'] += 1
                elif kind == 'complete': self.candidate['completed'] = False
                elif kind == 'scope': self.scope['issue_scope']['candidate_commit'] = 'c'*40
                elif kind == 'requirements': self.scope['issue_scope']['requirements'] = []
                elif kind == 'alias': self.scope['changed_paths'] = ['../escape']
                elif kind == 'same_diff': self.scope['changed_paths'] = ['Assets/Changed.cs']
                else: self.scope['reviewed_skips'] = []
                with self.assertRaises(ComparisonError): assess(self.candidate, **self.scope)

    def test_changed_candidate_with_concrete_independent_defect_needs_no_baseline(self):
        self.candidate = run('b', self.candidate['tests'][:2])
        self.scope = scope_for(self.candidate, 'a'*40, ['Assets/Animal/Delivery.cs'])
        row = self.candidate['tests'][1]
        self.scope['unrelated_failures']['Card.Other'] = {
            'reason': 'unchanged shared fixture references absent card widget', 'evidence': 'causal source review',
            'confirmed_unrelated': {
                'evidence_id': failure_id(self.candidate, 'a'*40, 'Card.Other', row),
                'kind': 'shared_test_defect', 'fixture_path': 'Assets/Tests/Card.cs',
                'dependencies': ['Assets/Card.cs'], 'cause': 'fixture setup omits the widget',
                'exclusion': 'delivery change has no Card dependency or shared state interaction',
                'maintenance_action': 'repair card fixture independently', 'evidence_file': 'cause.txt',
                'evidence_sha256': hashlib.sha256(b'causal review').hexdigest()}}
        result = assess(self.candidate, **self.scope)
        self.assertTrue(result['draft_eligible'])
        self.assertFalse(result['baseline_executed'])
        self.assertEqual(len(result['confirmed_unrelated_failures']), 1)
        self.scope['changed_paths'].append('Assets/Card.cs')
        self.scope['issue_scope']['changed_paths'].append('Assets/Card.cs')
        with self.assertRaises(ComparisonError): assess(self.candidate, **self.scope)

    def test_passing_focused_selection_is_not_a_full_suite_pass(self):
        self.candidate = run('b', [leaf('Animal.Delivery')])
        self.candidate['selection'] = {'test_names': ['Animal.Delivery']}
        result = assess(self.candidate, **scope_for(self.candidate, 'a'*40, ['Assets/Animal.cs']))
        self.assertTrue(result['draft_eligible'])
        self.assertFalse(result['full_suite_passed'])
        self.assertFalse(result['comparison_needed'])

    def test_existing_same_commit_pair_can_finish_but_never_same_job_twice(self):
        baseline = copy.deepcopy(self.candidate)
        baseline.update(reservation_id='actual-other-reservation', job_id='actual-other-job')
        scope = {'changed_paths': [], 'affected_tests': ['Animal.Delivery'], 'unrelated_failures': {},
                 'issue_scope': {'baseline_commit': baseline['commit'], 'candidate_commit': self.candidate['commit'],
                                 'changed_paths': [], 'affected_tests': ['Animal.Delivery'],
                                 'reason': 'unchanged Client with server-only fix', 'evidence': 'complete empty diff'}}
        self.assertTrue(compare(baseline, self.candidate, **scope)['draft_eligible'])
        self.assertFalse(compare(self.candidate, self.candidate, **scope)['draft_eligible'])


class CandidateControllerTests(pair_fixture.ServerPoolFixture):
    BASE, CANDIDATE = 'b'*40, 'c'*40
    candidate_first = pair_fixture.VerificationPairTests.candidate_first
    reuse = pair_fixture.VerificationPairTests.reuse
    settled = pair_fixture.VerificationPairTests.settled
    # Existing reservation fixtures exercise actual claims, generations and releases.
    def prepared(self):
        from agent.config import Config, Paths
        item, token, reservation = self.candidate_first()
        config = Config('test', 'test', 'test', local_root=self.root.parent)
        paths = Paths(config)
        paths.ledger = Path(self.ledger.connection.execute('PRAGMA database_list').fetchone()[2])
        directory = paths.runs/item; directory.mkdir(parents=True)
        candidate = run('c', [leaf('Animal.Delivery')]); candidate['reservation_id'] = reservation['reservation_id']
        scope = scope_for(candidate, self.BASE, ['Assets/Animal.cs'])
        args = SimpleNamespace(item=item, db=str(paths.ledger), candidate=str(directory/'candidate.json'),
                               scope=str(directory/'scope.json'))
        Path(args.candidate).write_text(json.dumps(candidate), encoding='utf-8')
        Path(args.scope).write_text(json.dumps(scope), encoding='utf-8')
        trees = SimpleNamespace(verification_commit=Mock(), git_in=Mock(return_value='Assets/Animal.cs\0'),
                                verification_tree=Mock(return_value='e'*40))
        self.addCleanup(patch.stopall)
        patch('agent.verification.Paths', return_value=paths).start()
        patch('agent.workflow.JobWorktrees', return_value=trees).start()
        patch('agent.workflow.repository_path', return_value=directory).start()
        return item, token, reservation, config, args, candidate, scope, trees

    def test_tree_proof_is_derived_by_controller_and_cannot_be_supplied_in_scope(self):
        item, token, reservation, config, args, candidate, scope, trees = self.prepared()
        scope['changed_paths'] = scope['issue_scope']['changed_paths'] = []
        trees.git_in.return_value = ''
        Path(args.scope).write_text(json.dumps(scope), encoding='utf-8')
        result = verify_recorded_candidate(args, self.ledger, config, token)
        self.assertEqual(result['verified_source_tree'], 'e'*40)
        self.assertEqual(trees.verification_tree.call_count, 2)
        scope['verified_source_tree'] = 'e'*40
        Path(args.scope).write_text(json.dumps(scope), encoding='utf-8')
        with self.assertRaises(ComparisonError): verify_recorded_candidate(args, self.ledger, config, token)

    def test_claim_fenced_assessment_keeps_one_reservation_and_current_stage(self):
        item, token, reservation, config, args, candidate, scope, trees = self.prepared()
        report = verify_recorded_candidate(args, self.ledger, config, token)
        self.assertTrue(report['draft_eligible'])
        self.assertEqual(len(self.ledger.reservations()), 1)
        self.assertEqual(self.ledger.item(item)['stage'], 'client-verified-awaiting-publication')
        audit = json.loads(self.ledger.connection.execute(
            "SELECT details FROM audit WHERE kind='verification_comparison'").fetchone()[0])
        self.assertFalse(audit['baseline_executed'])
        self.assertEqual(audit['source_base_commit'], self.BASE)
        self.assertNotIn('baseline_commit', audit)
        trees.verification_commit.assert_called_once()
        trees.git_in.return_value = 'Assets/Animal.cs\0Assets/Unreviewed.cs\0'
        with self.assertRaisesRegex(ComparisonError, 'complete actual source diff'):
            verify_recorded_candidate(args, self.ledger, config, token)

    def test_stale_claim_foreign_reservation_source_drift_and_existing_pair_rejected(self):
        item, token, reservation, config, args, candidate, scope, trees = self.prepared()
        with self.assertRaises(LedgerError): verify_recorded_candidate(args, self.ledger, config, 'invalid')
        for field, value in (('reservation_id', 'foreign'), ('commit', 'd'*40)):
            changed = {**candidate, field: value}
            Path(args.candidate).write_text(json.dumps(changed), encoding='utf-8')
            with self.assertRaises(ComparisonError): verify_recorded_candidate(args, self.ledger, config, token)
        Path(args.candidate).write_text(json.dumps(candidate), encoding='utf-8')
        self.reuse(item, token, reservation)
        baseline = self.ledger.acquire('unity_slot', owner='fixture', host='test')
        token = self.settled(item, baseline)
        with self.assertRaisesRegex(ComparisonError, 'existing comparison'):
            verify_recorded_candidate(args, self.ledger, config, token)

    def test_candidate_causal_evidence_is_checked_and_recorded_without_fake_baseline(self):
        item, token, reservation, config, args, candidate, scope, trees = self.prepared()
        row = leaf('Card.Other', 'Failed', 'missing widget', 'at Card.Other in Assets/Tests/Card.cs:42')
        candidate['tests'].append(row); candidate['summary'].update(total=2, failed=1)
        evidence = Path(args.scope).parent/'cause.txt'; evidence.write_bytes(b'actual source causal review')
        scope['unrelated_failures']['Card.Other'] = {
            'reason': 'independent fixture defect', 'evidence': 'causal source and setup review',
            'confirmed_unrelated': {
                'evidence_id': failure_id(candidate, self.BASE, 'Card.Other', row),
                'kind': 'shared_test_defect', 'fixture_path': 'Assets/Tests/Card.cs',
                'dependencies': ['Assets/Card.cs'], 'cause': 'fixture setup omits widget',
                'exclusion': 'animal change cannot affect card fixture setup',
                'maintenance_action': 'repair fixture separately', 'evidence_file': 'cause.txt',
                'evidence_sha256': hashlib.sha256(evidence.read_bytes()).hexdigest()}}
        Path(args.candidate).write_text(json.dumps(candidate), encoding='utf-8')
        Path(args.scope).write_text(json.dumps(scope), encoding='utf-8')
        with patch('agent.shared_test_repairs.blob', return_value='f'*40):
            result = verify_recorded_candidate(args, self.ledger, config, token)
            self.assertTrue(result['draft_eligible'])
            saved = json.loads(self.ledger.connection.execute(
                "SELECT details FROM audit WHERE kind='shared_test_followup'").fetchone()[0])
            self.assertFalse(saved['baseline_executed'])
            self.assertIsNone(saved['baseline_commit'])
            self.assertEqual(saved['source_base_commit'], self.BASE)
            evidence.write_bytes(b'changed review')
            with self.assertRaisesRegex(ComparisonError, 'evidence missing or changed'):
                verify_recorded_candidate(args, self.ledger, config, token)

    def test_candidate_generation_unclean_release_and_foreign_input_remain_fenced(self):
        item, token, reservation, config, args, candidate, scope, trees = self.prepared()
        for field, value in (('generation', 90), ('release_reason', 'uncertain'), ('state', 'cancelled')):
            old = self.ledger.reservation(reservation['reservation_id'])[field]
            self.ledger.connection.execute(f'UPDATE reservations SET {field}=? WHERE reservation_id=?',
                                            (value, reservation['reservation_id']))
            try:
                with self.assertRaises(ComparisonError): verify_recorded_candidate(args, self.ledger, config, token)
            finally:
                self.ledger.connection.execute(f'UPDATE reservations SET {field}=? WHERE reservation_id=?',
                                                (old, reservation['reservation_id']))
        args.scope = str(Path(args.scope).parent.parent/'foreign.json')
        with self.assertRaises(LedgerError): verify_recorded_candidate(args, self.ledger, config, token)

    def test_actual_same_commit_legacy_pair_is_returned_only_after_both_release(self):
        self.CANDIDATE = self.BASE
        item, token, candidate = self.candidate_first()
        self.reuse(item, token, candidate)
        baseline = self.ledger.acquire('unity_slot', owner='fixture', host='test')
        self.assertIsNone(self.ledger.client_verification_pair(item, self.CANDIDATE, require_candidate=True))
        self.settled(item, baseline)
        pair = self.ledger.client_verification_pair(item, self.CANDIDATE, require_candidate=True)
        self.assertEqual(pair['baseline_commit'], pair['candidate_commit'])
        self.assertNotEqual(pair['baseline_reservation'], pair['candidate_reservation'])
        self.ledger.fail(item, 'maintenance')
        self.ledger.retry(item, 'retain completed reports')
        self.assertEqual(pair, self.ledger.client_verification_pair(item, self.CANDIDATE, require_candidate=True))
        # A legacy baseline-first round may complete its candidate after an
        # authorized retry. Preserve that existing behavior and round ordering.
        item, baseline = pair_fixture.VerificationPairTests.baseline(self)
        self.settled(item, baseline)
        self.ledger.fail(item, 'interrupted before candidate')
        self.ledger.retry(item, 'continue the same candidate with retained baseline')
        token = self.ledger.claim(item, worker_id='retry')['token']
        self.ledger.await_resource(item, token, 'unity_slot', 'interactive',
            skill=pair_fixture.SKILLS['fix'], commit_sha=self.CANDIDATE)
        candidate = self.ledger.acquire('unity_slot', owner='fixture', host='test')
        self.settled(item, candidate)
        pair = self.ledger.client_verification_pair(item, self.CANDIDATE, require_candidate=True)
        self.assertEqual(pair['baseline_reservation'], baseline['reservation_id'])
        self.assertEqual(pair['candidate_reservation'], candidate['reservation_id'])


if __name__ == '__main__':
    unittest.main()
