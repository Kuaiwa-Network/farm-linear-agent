import copy
import unittest

from agent.repair_verification import compare_test_repair
from agent.test_baseline import ComparisonError
from test_test_baseline import run, test


class RepairComparisonTests(unittest.TestCase):
    def setUp(self):
        self.path = 'Assets/Scripts/Tests/PlayMode/PinchTests.cs'
        self.base = run('a', [test('Issue.Feature'), test('Farm.Pinch.Out'),
                              test('Other.Debt', 'Failed', 'old debt', 'at Old in Assets/Old.cs:1')])
        self.original = run('b', [test('Issue.Feature'), test('Farm.Pinch.Out', 'Failed', 'size6'),
                                  test('Other.Debt', 'Failed', 'old debt', 'at Old in Assets/Old.cs:1')])
        self.candidate = run('c', [test('Farm.Pinch.Out'), test('Farm.Pinch.Overlay'), test('Guild.Predecessor')])
        self.candidate['selection'] = {'test_names': ['Farm.Pinch', 'Guild.Predecessor']}
        self.repair = {'source_commit': 'b'*40, 'candidate_commit': 'c'*40, 'source_reservation': 'b',
                       'changed_paths': [self.path]}
        self.scope = {'affected_tests': ['Issue.Feature'], 'changed_paths': ['Assets/Issue.cs'],
                      'unrelated_failures': {'Other.Debt': {'reason': 'unchanged outside scope', 'evidence': 'diff'}},
                      'repair': {'reason': 'real pointer collision reproduced; fixture owns input now',
                                 'evidence': 'controlled same-gesture hit/no-hit report',
                                 'changed_paths': [self.path], 'fixtures': {self.path: 'Farm.Pinch'},
                                 'expected_tests': ['Farm.Pinch.Out', 'Farm.Pinch.Overlay', 'Guild.Predecessor']}}

    def compare(self):
        return compare_test_repair(self.base, self.original, self.candidate, self.scope, self.repair)

    def test_retains_failed_original_identity_without_claiming_full_suite_pass(self):
        original = copy.deepcopy(self.original)
        report = self.compare()
        self.assertTrue(report['draft_eligible'])
        self.assertFalse(report['full_suite_passed'])
        self.assertEqual(report['candidate_counts']['total'], 3)
        self.assertEqual(report['retained_original']['counts']['failed'], 2)
        self.assertEqual(report['repaired_failures'], ['Farm.Pinch.Out'])
        self.assertEqual(report['reviewed_unrelated_failures'], ['Other.Debt'])
        self.assertEqual(self.original, original)

    def test_refuses_runtime_delta_incomplete_fixtures_new_failures_skips_and_drift(self):
        variants = [
            lambda: self.repair.update(changed_paths=['Assets/Runtime.cs']),
            lambda: self.scope['repair'].update(expected_tests=['Farm.Pinch.Out']),
            lambda: self.candidate.update(selection={'test_names': ['Farm.Pinch.Out']}),
            lambda: self.candidate['tests'][1].update(state='Skipped:Ignored', message='not run'),
            lambda: self.candidate['tests'][1].update(state='Failed', message='new regression'),
            lambda: self.candidate['environment'].update(config_digest='3'*64),
            lambda: self.candidate.update(job_id=self.original['job_id']),
            lambda: self.repair.update(source_reservation='foreign'),
            lambda: self.scope['repair'].update(evidence=''),
        ]
        for mutation in variants:
            with self.subTest(mutation=mutation):
                self.setUp()
                mutation()
                with self.assertRaises(ComparisonError):
                    self.compare()

    def test_unrelated_new_failure_or_missing_original_test_cannot_be_hidden(self):
        self.original['tests'][0].update(state='Failed', message='new issue regression')
        self.original['summary'].update(passed=0, failed=3)
        with self.assertRaises(ComparisonError):
            self.compare()
        self.setUp()
        self.base['tests'].append(test('Missing.Original'))
        self.base['summary'].update(total=4, passed=3)
        report = self.compare()
        self.assertFalse(report['draft_eligible'])
        self.assertTrue(any('disappeared' in b for b in report['blockers']))

    def restored_fixture(self):
        self.original['tests'][1] = test('Farm.Pinch.Replacement')
        self.original['summary'].update(passed=2, failed=1)
        self.scope['repair']['restored_tests'] = ['Farm.Pinch.Out']
        self.candidate['tests'].append(test('Farm.Pinch.Replacement'))
        self.candidate['summary'].update(total=4, passed=4)
        self.scope['repair']['expected_tests'].append('Farm.Pinch.Replacement')

    def test_restored_identity_needs_only_fresh_whole_fixture_and_keeps_raw_reports(self):
        self.restored_fixture()
        original, baseline = copy.deepcopy(self.original), copy.deepcopy(self.base)
        self.scope['affected_tests'].append('Farm.Pinch.Out')
        report = self.compare()
        self.assertTrue(report['draft_eligible'])
        self.assertFalse(report['full_suite_passed'])
        self.assertEqual(report['restored_tests'], ['Farm.Pinch.Out'])
        self.assertEqual(report['repaired_failures'], [])
        self.assertEqual(report['retained_original']['commit'], original['commit'])
        self.assertEqual(report['candidate_counts']['total'], 4)
        self.assertEqual((self.original, self.base), (original, baseline))

    def test_restoration_cannot_hide_other_missing_cases_skips_or_environment_drift(self):
        self.restored_fixture()
        self.base['tests'].append(test('Other.Disappeared'))
        self.base['summary'].update(total=4, passed=3)
        self.assertFalse(self.compare()['draft_eligible'])
        for change in (
                lambda: self.scope['repair'].update(restored_tests=['Not.ABaselineTest']),
                lambda: self.scope['repair'].update(restored_tests=['Farm.Pinch.Out'] * 2),
                lambda: self.scope['repair'].update(restored_tests=[]),
                lambda: self.candidate['tests'][0].update(state='Skipped'),
                lambda: self.candidate['tests'][0].update(state='Failed'),
                lambda: self.candidate['environment'].update(config_digest='7'*64),
                lambda: self.candidate['tests'].__delitem__(-1),
                lambda: self.repair.update(changed_paths=['Assets/Runtime.cs'])):
            with self.subTest(change=change):
                self.setUp()
                self.restored_fixture()
                change()
                with self.assertRaises(ComparisonError):
                    self.compare()
