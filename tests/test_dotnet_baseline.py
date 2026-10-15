import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
import xml.etree.ElementTree as ET

from agent.dotnet_baseline import NS, compare, load_run, main
from agent.test_baseline import ComparisonError


class DotnetBaselineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='TRX 路径 ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.rows = [('Animal.Counter', 'Passed'), ('Card.Rule', 'Failed'), ('Platform.Skip', 'NotExecuted'),
                     ('Duplicate.Name', 'Passed'), ('Duplicate.Name', 'Passed')]
        self.scope = {'affected_tests': ['Animal.Counter'], 'changed_paths': ['Assets/Animal.cs'],
                      'unrelated_failures': {'Card.Rule': {'reason': 'reviewed unchanged dependency', 'evidence': 'source audit'}}}
        self.baseline = self.write('a', self.rows)
        self.candidate = self.write('b', self.rows + [('Animal.New', 'Passed')])

    def write(self, commit, rows):
        root = ET.Element(NS + 'TestRun', id=str(uuid.uuid4()))
        stamp = '2026-10-15T12:00:00+08:00'
        ET.SubElement(root, NS + 'Times', start=stamp, finish=stamp)
        results = ET.SubElement(root, NS + 'Results')
        definitions = ET.SubElement(root, NS + 'TestDefinitions')
        entries = ET.SubElement(root, NS + 'TestEntries')
        seen = set()
        for name, state in rows:
            cls, display = name.rsplit('.', 1)
            test_id, execution = str(uuid.uuid5(uuid.NAMESPACE_URL, name)), str(uuid.uuid4())
            if name not in seen:
                definition = ET.SubElement(definitions, NS + 'UnitTest', id=test_id, name=display)
                ET.SubElement(definition, NS + 'Execution', id=execution)
                ET.SubElement(definition, NS + 'TestMethod', className=cls, name=display)
                seen.add(name)
            ET.SubElement(entries, NS + 'TestEntry', testId=test_id, executionId=execution)
            result = ET.SubElement(results, NS + 'UnitTestResult', testId=test_id, executionId=execution,
                                   testName=display, outcome=state, startTime=stamp, endTime=stamp)
            if state != 'Passed':
                error = ET.SubElement(ET.SubElement(result, NS + 'Output'), NS + 'ErrorInfo')
                ET.SubElement(error, NS + 'Message').text = 'Existing failure' if state == 'Failed' else 'Unsupported platform'
                ET.SubElement(error, NS + 'StackTrace').text = f'at Card.Rule() in D:/{commit}/tests/Card.cs:line 12'
        failed = sum(state == 'Failed' for _, state in rows)
        passed = sum(state == 'Passed' for _, state in rows)
        summary = ET.SubElement(root, NS + 'ResultSummary', outcome='Failed' if failed else 'Completed')
        ET.SubElement(summary, NS + 'Counters', total=str(len(rows)), passed=str(passed), failed=str(failed),
                      executed=str(failed + passed), notExecuted='0', aborted='0', pending='0')
        path = self.root / (commit + '.trx')
        path.write_bytes(ET.tostring(root))
        return {'commit': commit * 40, 'completed': True, 'mode': 'dotnet-test',
                'selection': {'project': 'tests/Unit.csproj', 'filter': None},
                'environment': {'host': 'windows', 'execution_platform': 'win32',
                                'toolchain_digest': '1' * 64, 'config_digest': '2' * 64, 'package_digest': '3' * 64},
                'trx': str(path), 'trx_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}

    def edit(self, manifest, fn):
        path = Path(manifest['trx'])
        root = ET.fromstring(path.read_bytes())
        fn(root)
        path.write_bytes(ET.tostring(root))
        manifest['trx_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()

    def compare(self):
        return compare(load_run(self.baseline), load_run(self.candidate), **self.scope)

    def test_real_execution_identity_and_duplicate_passing_results_are_retained(self):
        report = self.compare()
        self.assertTrue(report['draft_eligible'])
        self.assertFalse(report['full_suite_passed'])
        self.assertEqual(report['candidate_counts'], {'total': 6, 'passed': 4, 'failed': 1, 'skipped': 1})
        self.assertEqual(report['reviewed_unrelated_failures'], ['Card.Rule'])
        self.assertEqual(report['existing_skips'], ['Platform.Skip'])
        duplicate = report['duplicate_executions'][0]
        self.assertEqual(len(duplicate['baseline_executions']), 2)
        self.assertEqual(len(duplicate['candidate_executions']), 2)
        self.assertNotIn('baseline_reservation', report)

    def test_duplicate_failure_is_ambiguous_even_if_aggregate_signatures_match(self):
        rows = self.rows + [('Card.Rule', 'Failed')]
        self.baseline, self.candidate = self.write('a', rows), self.write('b', rows)
        self.assertFalse(self.compare()['draft_eligible'])

    def test_removed_duplicate_execution_blocks(self):
        self.candidate = self.write('b', self.rows[:-1])
        self.assertFalse(self.compare()['draft_eligible'])

    def test_added_failure_and_skip_block(self):
        for state in ('Failed', 'NotExecuted'):
            with self.subTest(state=state):
                self.candidate = self.write('b', self.rows + [('New.Case', state)])
                self.assertFalse(self.compare()['draft_eligible'])

    def test_affected_missing_or_failed_or_skipped_blocks(self):
        for rows in (self.rows[1:], [('Animal.Counter', 'Failed')] + self.rows[1:],
                     [('Animal.Counter', 'NotExecuted')] + self.rows[1:]):
            self.candidate = self.write('b', rows)
            self.assertFalse(self.compare()['draft_eligible'])

    def test_source_review_and_changed_stack_path_are_required(self):
        self.scope['unrelated_failures'] = {}
        self.assertFalse(self.compare()['draft_eligible'])
        self.scope['unrelated_failures']['Card.Rule'] = {'reason': 'review', 'evidence': 'audit'}
        self.scope['changed_paths'] = ['tests/Card.cs']
        self.assertFalse(self.compare()['draft_eligible'])

    def test_signature_change_is_not_hidden(self):
        self.edit(self.candidate, lambda r: setattr(r.find('.//' + NS + 'Message'), 'text', 'Different failure'))
        self.assertFalse(self.compare()['draft_eligible'])

    def test_environment_and_selection_must_match(self):
        for key in ('host', 'execution_platform', 'toolchain_digest', 'config_digest', 'package_digest'):
            with self.subTest(key=key):
                candidate = copy.deepcopy(self.candidate)
                candidate['environment'][key] = '9' * 64
                self.assertFalse(compare(load_run(self.baseline), load_run(candidate), **self.scope)['draft_eligible'])
        self.candidate['selection']['filter'] = 'narrow subset'
        self.assertFalse(self.compare()['draft_eligible'])

    def test_identical_run_or_commit_cannot_act_as_baseline(self):
        self.assertFalse(compare(load_run(self.baseline), load_run(self.baseline), **self.scope)['draft_eligible'])
        self.candidate['commit'] = self.baseline['commit']
        self.assertFalse(self.compare()['draft_eligible'])

    def test_raw_hash_mismatch_is_gap(self):
        self.candidate['trx_sha256'] = '0' * 64
        with self.assertRaisesRegex(ComparisonError, 'digest'):
            load_run(self.candidate)

    def test_missing_or_duplicate_result_entry_definition_is_gap(self):
        mutations = [lambda r: r.find(NS + 'Results').remove(r.find(NS + 'Results')[0]),
                     lambda r: r.find(NS + 'Results').append(copy.deepcopy(r.find(NS + 'Results')[0])),
                     lambda r: r.find(NS + 'TestEntries').remove(r.find(NS + 'TestEntries')[0]),
                     lambda r: r.find(NS + 'TestDefinitions').append(copy.deepcopy(r.find(NS + 'TestDefinitions')[0]))]
        for fn in mutations:
            with self.subTest(fn=fn):
                self.candidate = self.write('b', self.rows)
                self.edit(self.candidate, fn)
                with self.assertRaises(ComparisonError):
                    load_run(self.candidate)

    def test_interrupted_unknown_outcome_counter_and_time_are_gaps(self):
        mutations = [lambda r: r.find(NS + 'Times').set('finish', ''),
                     lambda r: r.find(NS + 'ResultSummary').set('outcome', 'Aborted'),
                     lambda r: r.find('.//' + NS + 'Counters').set('pending', '1'),
                     lambda r: r.find('.//' + NS + 'Counters').set('total', '10'),
                     lambda r: r.find(NS + 'Results')[0].set('outcome', 'Timeout'),
                     lambda r: r.find(NS + 'Results')[0].set('endTime', '2026-10-16T12:00:00+08:00')]
        for fn in mutations:
            with self.subTest(fn=fn):
                self.candidate = self.write('b', self.rows)
                self.edit(self.candidate, fn)
                with self.assertRaises(ComparisonError):
                    load_run(self.candidate)

    def test_missing_skip_reason_is_gap(self):
        self.edit(self.candidate, lambda r: setattr(r.findall('.//' + NS + 'Message')[1], 'text', ''))
        with self.assertRaises(ComparisonError):
            load_run(self.candidate)

    def test_dtd_and_non_trx_input_are_rejected(self):
        for raw in (b'<!DOCTYPE x><x/>', b'<TestRun/>'):
            path = Path(self.candidate['trx']); path.write_bytes(raw)
            self.candidate['trx_sha256'] = hashlib.sha256(raw).hexdigest()
            with self.assertRaises(ComparisonError):
                load_run(self.candidate)

    def test_cli_reads_unicode_paths_and_retains_raw_files(self):
        for name, value in (('baseline', self.baseline), ('candidate', self.candidate), ('scope', self.scope)):
            (self.root / (name + '.json')).write_text(json.dumps(value), encoding='utf-8')
        args = [arg for name in ('baseline', 'candidate', 'scope') for arg in ('--' + name, str(self.root / (name + '.json')))]
        with patch('sys.stdout'):
            self.assertEqual(main(args), 0)
        self.assertEqual(hashlib.sha256(Path(self.candidate['trx']).read_bytes()).hexdigest(), self.candidate['trx_sha256'])


if __name__ == '__main__':
    unittest.main()
