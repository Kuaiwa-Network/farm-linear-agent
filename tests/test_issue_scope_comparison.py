"""Issue acceptance is behavioral; sharing an exported package does not expand it."""
import copy
import unittest

from agent.test_baseline import ComparisonError, compare
import test_test_baseline as fixtures


class IssueScopeTests(unittest.TestCase):
    compare = fixtures.BaselineTests.compare
    ui_inputs = fixtures.BaselineTests.ui_inputs
    rehash = fixtures.BaselineTests.rehash
    clock_failure = fixtures.BaselineTests.clock_failure

    def setUp(self):
        fixtures.BaselineTests.setUp(self)
        self.ui_inputs()
        self.scope['unrelated_failures'] = {}
        self.bind_scope()

    def bind_scope(self):
        self.scope['issue_scope'] = {
            'baseline_commit': self.baseline['commit'], 'candidate_commit': self.candidate['commit'],
            'changed_paths': list(self.scope['changed_paths']),
            'affected_tests': list(self.scope['affected_tests']),
            'reason': 'Counter rendering is the changed behavior; cleanup is not linked by the actual diff.',
            'evidence': 'accepted issue requirements, complete source/resource delta and passing counter QA'}

    def test_existing_failure_does_not_require_proving_whole_package_independence(self):
        original = copy.deepcopy((self.baseline, self.candidate, self.scope))
        report = self.compare()
        self.assertTrue(report['draft_eligible'])
        self.assertEqual(report['retained_baseline_failures'], ['Card.Cleanup'])
        self.assertEqual(report['reviewed_unrelated_failures'], [])
        self.assertEqual(report['scope_policy'], 'issue-delta-v1')
        self.assertEqual(report['verdict'], 'passed_with_existing_failures')
        self.assertFalse(report['full_suite_passed'])
        self.assertEqual(report['candidate_counts']['failed'], 1)
        self.assertEqual((self.baseline, self.candidate, self.scope), original)

    def test_same_package_membership_is_not_a_failure_gate(self):
        self.scope['unrelated_failures']['Card.Cleanup'] = {
            'reason': 'Legacy broad review only established shared package membership', 'evidence': 'old report',
            'resource_review': {'dependencies': [self.ui_path], 'reason': 'same changed package', 'evidence': 'inventory'}}
        self.assertTrue(self.compare()['draft_eligible'])
        self.scope.pop('issue_scope')
        self.assertFalse(self.compare()['draft_eligible'])  # No silent reinterpretation of historical scopes.

    def test_affected_existing_failure_still_blocks(self):
        self.scope['affected_tests'].append('Card.Cleanup')
        self.bind_scope()
        self.assertFalse(self.compare()['draft_eligible'])

    def test_changed_failure_stack_is_concrete_association_even_outside_scope(self):
        self.scope['changed_paths'].append('Assets/Tests/Card.cs')
        self.bind_scope()
        self.assertFalse(self.compare()['draft_eligible'])

    def test_new_changed_missing_affected_and_skipped_tests_still_block(self):
        original = copy.deepcopy((self.baseline, self.candidate, self.scope))
        for kind in ('new_failure', 'message', 'state', 'site', 'removed', 'missing_affected', 'affected_failed', 'skip'):
            with self.subTest(kind=kind):
                self.baseline, self.candidate, self.scope = copy.deepcopy(original)
                row = self.candidate['tests'][1]
                if kind == 'new_failure': row['fullName'] = 'New.Failure'
                elif kind == 'message': row['message'] = 'Different business value'
                elif kind == 'state': row['state'] = 'Failed:Invalid'
                elif kind == 'site': row['stackTrace'] = 'at Other.Method() in Assets/Other.cs:3'
                elif kind == 'removed':
                    self.candidate['tests'].pop(1)
                    self.candidate['summary']['total'] -= 1; self.candidate['summary']['failed'] -= 1
                elif kind == 'missing_affected':
                    self.scope['affected_tests'].append('Missing.Required'); self.bind_scope()
                elif kind == 'affected_failed':
                    self.candidate['tests'][0].update(state='Failed', message='counter regression')
                    self.candidate['summary']['passed'] -= 1; self.candidate['summary']['failed'] += 1
                else: self.candidate['tests'][2]['message'] = 'New skip reason'
                self.assertFalse(self.compare()['draft_eligible'])

    def test_scope_review_cannot_be_empty_stale_or_bound_to_different_selection(self):
        original = copy.deepcopy(self.scope['issue_scope'])
        for key, value in (('reason', ''), ('evidence', ''), ('baseline_commit', 'c' * 40),
                           ('candidate_commit', 'd' * 40), ('changed_paths', []), ('affected_tests', []),
                           ('affected_tests', ['Animal.Counter', 'Animal.Counter'])):
            with self.subTest(key=key, value=value):
                self.scope['issue_scope'] = {**original, key: value}
                with self.assertRaises(ComparisonError): self.compare()

    def test_identity_and_unreviewed_resource_drift_still_block(self):
        original = copy.deepcopy((self.baseline, self.candidate, self.scope))
        for kind in ('host', 'selection', 'inventory', 'ui_review', 'lock'):
            with self.subTest(kind=kind):
                self.baseline, self.candidate, self.scope = copy.deepcopy(original)
                if kind == 'host': self.candidate['environment']['host'] = 'other'
                elif kind == 'selection': self.candidate['selection'] = {'assembly_names': ['Other']}
                elif kind == 'inventory': self.candidate.pop('package_inventory')
                elif kind == 'ui_review': self.scope['ui_resource_changes'] = {}
                else:
                    self.candidate['package_inventory'][1]['sha256'] = '8' * 64; self.rehash()
                self.assertFalse(self.compare()['draft_eligible'])

    def test_clock_diagnostic_stays_narrow_without_transitive_independence_requirement(self):
        self.scope['unrelated_failures']['Card.Cleanup'] = {'reason': 'unchanged timeout assertion', 'evidence': 'source hashes'}
        self.clock_failure()
        report = self.compare()
        self.assertTrue(report['draft_eligible'])
        self.assertEqual(len(report['reviewed_clock_diagnostics']), 1)
        self.scope['unrelated_failures'] = {}
        self.assertFalse(self.compare()['draft_eligible'])

    def test_equal_invalid_failures_remain_failed_and_are_retained(self):
        for run in (self.baseline, self.candidate):
            run['tests'][1].update(state='Failed:Invalid', message='Invalid NUnit signature')
        report = self.compare()
        self.assertTrue(report['draft_eligible'])
        self.assertFalse(report['full_suite_passed'])

    def test_duplicate_nonpassing_executions_are_not_waived(self):
        for run in (self.baseline, self.candidate):
            run['tests'].append(copy.deepcopy(run['tests'][1]))
            run['summary']['total'] += 1; run['summary']['failed'] += 1
        self.assertFalse(self.compare()['draft_eligible'])


if __name__ == '__main__':
    unittest.main()
