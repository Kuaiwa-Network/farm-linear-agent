import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent.shared_test_findings import evidence_id, lookup, record, verify
from agent.test_baseline import ComparisonError, compare
from test_test_baseline import run, test as leaf


def reviewed_scope(baseline, candidate, paths=None, evidence=b'actual clock/source observation'):
    paths = paths or ['Assets/Animal/Tuning.cs']
    return {'affected_tests': ['Animal.Counter'], 'changed_paths': paths,
        'issue_scope': {'baseline_commit': baseline['commit'], 'candidate_commit': candidate['commit'],
            'changed_paths': paths, 'affected_tests': ['Animal.Counter'],
            'reason': 'Counter getter acceptance and complete dependency review', 'evidence': 'full diff and counter QA'},
        'unrelated_failures': {'Shared.Animation': {
            'reason': 'Scaled wait races an unscaled tween; counter getter has no clock or animation interaction',
            'evidence': 'source review and retained controlled observation',
            'confirmed_unrelated': {
                'evidence_id': evidence_id(baseline, candidate, 'Shared.Animation',
                                            baseline['tests'][1], candidate['tests'][1]),
                'kind': 'shared_test_defect', 'fixture_path': 'Assets/Tests/Shared.cs',
                'dependencies': ['Assets/Animation.cs'], 'cause': 'scaled wait versus unscaled tween',
                'exclusion': 'complete counter getter diff does not change clocks, tween or fixture setup',
                'maintenance_action': 'repair clock ownership in the shared fixture separately',
                'evidence_file': 'causal-review.txt', 'evidence_sha256': hashlib.sha256(evidence).hexdigest()}}}}


class SharedFindingTests(unittest.TestCase):
    def setUp(self):
        self.baseline = run('a', [leaf('Animal.Counter'), leaf('Shared.Animation')])
        self.candidate = run('b', [leaf('Animal.Counter'), leaf('Shared.Animation', 'Failed', 'alpha was 1',
                                      'at Shared.Animation() in Assets/Tests/Shared.cs:42')])
        self.scope = reviewed_scope(self.baseline, self.candidate)

    def compare(self):
        return compare(self.baseline, self.candidate, **self.scope)

    def test_confirmed_unrelated_candidate_failure_preserves_raw_results_and_allows_delivery(self):
        before = copy.deepcopy((self.baseline, self.candidate, self.scope))
        result = self.compare()
        self.assertTrue(result['draft_eligible'])
        self.assertEqual(result['verdict'], 'passed_with_unrelated_findings')
        self.assertFalse(result['full_suite_passed'])
        self.assertEqual(result['candidate_counts']['failed'], 1)
        self.assertEqual(result['unresolved_failures'], [])
        self.assertEqual(result['confirmed_unrelated_failures'][0]['fullName'], 'Shared.Animation')
        self.assertEqual((self.baseline, self.candidate, self.scope), before)

    def test_plain_unrelated_claim_is_not_causal_confirmation(self):
        del self.scope['unrelated_failures']['Shared.Animation']['confirmed_unrelated']
        result = self.compare()
        self.assertFalse(result['draft_eligible'])
        self.assertIn('Shared.Animation', result['failure_evidence_ids'])

    def test_scope_identity_affected_and_changed_stack_gates_remain(self):
        for kind in ('no_scope', 'affected', 'stack', 'host', 'skip', 'missing'):
            with self.subTest(kind=kind):
                self.setUp()
                if kind == 'no_scope': del self.scope['issue_scope']
                elif kind == 'affected':
                    self.scope['affected_tests'].append('Shared.Animation')
                    self.scope['issue_scope']['affected_tests'].append('Shared.Animation')
                elif kind == 'stack':
                    self.scope['changed_paths'].append('Assets/Tests/Shared.cs')
                elif kind == 'host': self.candidate['environment']['host'] = 'different'
                elif kind == 'skip':
                    self.candidate['tests'][0].update(state='Skipped', message='unavailable')
                    self.candidate['summary'].update(passed=0, skipped=1)
                else:
                    self.candidate['tests'].pop(0)
                    self.candidate['summary'].update(total=1, passed=0)
                self.assertFalse(self.compare()['draft_eligible'])

    def test_missing_causal_fields_and_changed_dependency_are_refused(self):
        for key in ('cause', 'exclusion', 'maintenance_action', 'evidence_file', 'evidence_sha256',
                    'dependencies', 'evidence_id', 'fixture_path'):
            with self.subTest(key=key):
                self.setUp()
                del self.scope['unrelated_failures']['Shared.Animation']['confirmed_unrelated'][key]
                with self.assertRaises(ComparisonError): self.compare()
        self.setUp()
        self.scope['changed_paths'].append('Assets/Animation.cs')
        with self.assertRaises(ComparisonError): self.compare()

    def test_stale_review_new_test_invalid_and_ambiguous_failure_are_not_waived(self):
        for kind in ('message', 'job', 'new', 'invalid', 'duplicate'):
            with self.subTest(kind=kind):
                self.setUp()
                if kind == 'message': self.candidate['tests'][1]['message'] = 'new regression'
                elif kind == 'job': self.candidate['job_id'] = 'different run'
                elif kind == 'new':
                    self.baseline['tests'].pop(1); self.baseline['summary'].update(total=1, passed=1)
                elif kind == 'invalid': self.candidate['tests'][1]['state'] = 'Failed:Invalid'
                else:
                    self.candidate['tests'].append(copy.deepcopy(self.candidate['tests'][1]))
                    self.candidate['summary'].update(total=3, failed=2)
                if kind == 'duplicate': self.assertFalse(self.compare()['draft_eligible'])
                else:
                    with self.assertRaises(ComparisonError): self.compare()

    def test_worker_review_cannot_override_controller_record_identity(self):
        for key in ('schema', 'source_item', 'candidate_commit', 'followup_id', 'status', 'inputs'):
            with self.subTest(key=key):
                self.setUp()
                self.scope['unrelated_failures']['Shared.Animation']['confirmed_unrelated'][key] = 'forged'
                with self.assertRaises(ComparisonError): self.compare()

    def test_verify_pins_complete_diff_blobs_and_evidence_before_recording(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'causal-review.txt').write_bytes(b'actual clock/source observation')
            trees = SimpleNamespace(git_in=Mock(return_value='Assets/Animal/Tuning.cs\0'))
            report = self.compare()
            with patch('agent.shared_test_repairs.blob', return_value='c'*40):
                verify(trees, root, self.baseline, self.candidate, self.scope, report, root)
            finding = report['confirmed_unrelated_failures'][0]
            self.assertEqual(finding['status'], 'open')
            self.assertFalse(finding['blocks_business_delivery'])
            self.assertEqual(len(finding['inputs']), 2)
            for kind in ('diff', 'blob', 'evidence', 'foreign_path'):
                with self.subTest(kind=kind):
                    current = self.compare()
                    trees.git_in.return_value = 'hidden-runtime.cs\0' if kind == 'diff' else 'Assets/Animal/Tuning.cs\0'
                    if kind == 'evidence': current['confirmed_unrelated_failures'][0]['evidence_sha256'] = '0'*64
                    if kind == 'foreign_path': current['confirmed_unrelated_failures'][0]['evidence_file'] = '../outside.txt'
                    with patch('agent.shared_test_repairs.blob', side_effect=lambda t, sha, p:
                               sha if kind == 'blob' else 'c'*40):
                        with self.assertRaises((ComparisonError, ValueError)):
                            verify(trees, root, self.baseline, self.candidate, self.scope, current, root)
            self.check_record_and_lookup(report)

    def check_record_and_lookup(self, report):
        db = sqlite3.connect(':memory:'); db.row_factory = sqlite3.Row
        self.addCleanup(db.close)
        db.execute('CREATE TABLE audit(id INTEGER PRIMARY KEY,item_id TEXT,kind TEXT,details TEXT)')
        def audit(item, kind, reason, *, details):
            db.execute('INSERT INTO audit(item_id,kind,details) VALUES(?,?,?)', (item, kind, json.dumps(details)))
        ledger = SimpleNamespace(connection=db, _audit=audit)
        record(ledger, 'source', report); record(ledger, 'source', report)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM audit').fetchone()[0], 1)
        with patch('agent.shared_test_repairs.blob', return_value='c'*40):
            found = lookup(ledger, None, self.candidate)
            self.assertEqual(found[0]['reuse_status'], 'matching_diagnosis')
            self.candidate['environment']['unity_version'] = 'changed'
            self.assertEqual(lookup(ledger, None, self.candidate)[0]['reuse_status'], 'review_required')
        with patch('agent.shared_test_repairs.blob', return_value='d'*40):
            self.assertEqual(lookup(ledger, None, self.candidate)[0]['reuse_status'], 'review_required')


if __name__ == '__main__':
    unittest.main()
