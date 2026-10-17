import copy
import json
import sqlite3
import unittest
from unittest.mock import patch

from agent.shared_test_repairs import lookup, record
from agent.test_baseline import ComparisonError
import test_repair_verification as fixtures


class SharedRepairTests(unittest.TestCase):
    def setUp(self):
        case = fixtures.RepairComparisonTests()
        case.setUp()
        self.original, self.candidate = case.original, case.candidate
        self.report = case.compare()
        self.path = case.path
        self.report['repair_review']['shared_dependencies'] = ['Assets/SceneController.cs']
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute('CREATE TABLE audit (id INTEGER PRIMARY KEY,item_id TEXT,kind TEXT,reason TEXT,details TEXT)')
        self.addCleanup(self.db.close)
        from types import SimpleNamespace
        self.ledger = SimpleNamespace(connection=self.db, _audit=self.audit)
        self.blobs = {('b'*40, self.path): 'old', ('c'*40, self.path): 'repaired',
                      ('d'*40, self.path): 'old'}
        self.mock = patch('agent.shared_test_repairs.blob', side_effect=lambda trees, sha, path:
                          self.blobs.get((sha, path), 'dependency'))
        self.mock.start()
        self.addCleanup(self.mock.stop)
        self.target = copy.deepcopy(self.original)
        self.target.update(commit='d'*40, reservation_id='other-issue', job_id='other-job')

    def audit(self, item, kind, reason, *, details):
        self.db.execute('INSERT INTO audit(item_id,kind,reason,details) VALUES(?,?,?,?)',
                        (item, kind, reason, json.dumps(details)))

    def save(self):
        record(self.ledger, None, 'source-issue', self.original, self.candidate, self.report)

    def test_cross_issue_diagnosis_is_reused_but_no_test_verdict_is_borrowed(self):
        before = copy.deepcopy(self.target)
        self.save()
        self.save()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM audit').fetchone()[0], 1)
        result = lookup(self.ledger, None, self.target)
        match = result['matches'][0]
        self.assertEqual(match['status'], 'matching_diagnosed_repair')
        self.assertEqual(match['failures'], ['Farm.Pinch.Out'])
        self.assertEqual(match['source_item'], 'source-issue')
        self.assertEqual(match['repair_commit'], 'c'*40)
        self.assertFalse(result['evidence_is_authority'])
        self.assertFalse(result['current_tests_passed'])
        self.assertEqual(self.target, before)

    def test_signature_fixture_dependency_and_engine_drift_require_review(self):
        self.save()
        cases = [
            lambda: self.target['tests'][1].update(message='different regression'),
            lambda: self.target['tests'][1].update(stackTrace='Other.cs:9'),
            lambda: self.blobs.update({('d'*40, self.path): 'new fixture'}),
            lambda: self.blobs.update({('d'*40, 'Assets/SceneController.cs'): 'changed gate'}),
            lambda: self.target['environment'].update(unity_version='different engine'),
        ]
        for change in cases:
            with self.subTest(change=change):
                before, blobs = copy.deepcopy(self.target), dict(self.blobs)
                change()
                result = lookup(self.ledger, None, self.target)['matches'][0]
                self.assertEqual(result['status'], 'review_required')
                self.assertTrue(result['differences'])
                self.target, self.blobs = before, blobs

    def test_missing_dependency_review_is_discoverable_but_not_a_match(self):
        del self.report['repair_review']['shared_dependencies']
        self.save()
        match = lookup(self.ledger, None, self.target)['matches'][0]
        self.assertEqual(match['status'], 'review_required')
        self.assertIn('dependency review not recorded', match['differences'])

    def test_ineligible_and_unrelated_results_are_not_indexed(self):
        for field, value in [('draft_eligible', False), ('validation_scope', 'full-suite')]:
            saved = self.report[field]
            self.report[field] = value
            self.save()
            self.report[field] = saved
        self.assertEqual(lookup(self.ledger, None, self.target)['matches'], [])

    def test_changed_dependency_cannot_be_certified_as_same_repair(self):
        self.blobs[('c'*40, 'Assets/SceneController.cs')] = 'changed production'
        with self.assertRaises(ComparisonError):
            self.save()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM audit').fetchone()[0], 0)

    def test_missing_dependency_is_a_review_gap_not_a_match_or_a_retry(self):
        from agent.worktrees import WorktreeError
        self.save()
        with patch('agent.shared_test_repairs.blob', side_effect=WorktreeError('missing object')):
            result = lookup(self.ledger, None, self.target)
        self.assertEqual(result['matches'][0]['status'], 'review_required')
        self.assertFalse(result['current_tests_passed'])

    def test_new_failure_names_and_ambiguous_duplicates_do_not_match(self):
        self.save()
        self.target['tests'][1]['fullName'] = 'Farm.Pinch.New'
        self.assertEqual(lookup(self.ledger, None, self.target)['matches'], [])
        self.target = copy.deepcopy(self.original)
        self.target['tests'].append(copy.deepcopy(self.target['tests'][1]))
        self.target['summary'].update(total=4, failed=3)
        self.assertEqual(lookup(self.ledger, None, self.target)['matches'], [])


class SharedRepairInstructionsTests(unittest.TestCase):
    def test_worker_checks_existing_diagnoses_before_spending_diagnostic_budget(self):
        from agent import dispatch
        from pathlib import Path
        source = Path(dispatch.__file__).read_text(encoding='utf-8')
        self.assertIn('shared-test-repairs --candidate ORIGINAL_FULL_REPORT', source)
        self.assertIn('never the source issue\'s feature commit', source)
        self.assertIn('A lookup is not a passing result', source)
