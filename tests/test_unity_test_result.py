"""Retained-report adapter and executable C# probe; no production Editor."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

from agent.ledger import LedgerError
from agent.unity_test_result import retained_result

ROOT = Path(__file__).resolve().parents[1]
JOB = 'b' * 32


class RetainedResultTests(unittest.TestCase):
    def test_accepts_object_or_json_report_and_keeps_unavailable(self):
        report = {'schema': 1, 'job_id': JOB, 'available': False, 'reason': 'job_not_retained'}
        for value in (report, json.dumps(report)):
            client = Mock()
            client.call_tool.return_value = {'result': value}
            self.assertEqual(retained_result(client, JOB, 100.0001), report)
            name, args = client.call_tool.call_args.args
            self.assertEqual(name, 'execute_code')
            self.assertTrue(args['safety_checks'])
            self.assertIn('100001L', args['code'])

    def test_rejects_injection_and_missing_acquisition_before_mcp_call(self):
        client = Mock()
        for job, acquired in [('";run();', 100), ('a'*31, 100), (JOB, None), (JOB, float('inf')),
                              (JOB, 0), (JOB, True)]:
            with self.subTest(job=job, acquired=acquired), self.assertRaises(LedgerError):
                retained_result(client, job, acquired)
        client.call_tool.assert_not_called()

    def test_missing_or_wrong_job_response_is_not_a_result(self):
        for value in (None, {}, {'schema': 1, 'job_id': 'c'*32}, {'schema': 2, 'job_id': JOB}):
            client = Mock()
            client.call_tool.return_value = {'result': value}
            with self.subTest(value=value), self.assertRaises(LedgerError):
                retained_result(client, JOB, 100)

    def test_transport_failure_is_not_retried(self):
        client = Mock()
        client.call_tool.side_effect = TimeoutError('lost read response')
        with self.assertRaises(TimeoutError):
            retained_result(client, JOB, 100)
        self.assertEqual(client.call_tool.call_count, 1)

    def test_cli_requires_explicit_job_and_claim(self):
        from agent.__main__ import parser
        args = parser().parse_args(['--db', 'private.sqlite', 'unity-test-result',
            '--item', 'issue', '--job-id', JOB, '--token-file', 'private.token'])
        self.assertEqual((args.command, args.job_id, args.token_file), ('unity-test-result', JOB, 'private.token'))


@unittest.skipUnless(os.name == 'nt', 'native Windows C# probe compatibility')
class NativeRetainedProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='farm report 测试 ')
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)
        compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        if not compiler.is_file():
            raise RuntimeError('Windows C# compiler required for retained probe validation')
        probe = (ROOT / 'agent/probes/test-result.cs.txt').read_text(encoding='utf-8')
        harness = (ROOT / 'tests/fixtures/unity-test-result-harness.cs').read_text(encoding='utf-8')
        source = root / 'probe.cs'
        source.write_text(harness.replace('// PROBE', probe.replace('__JOB_ID__', JOB)
            .replace('__ACQUIRED_MS__', '100000')), encoding='utf-8')
        cls.binary = root / 'MCPForUnity.Editor.exe'
        built = subprocess.run([str(compiler), '/nologo', '/r:System.Core.dll', '/r:System.Web.Extensions.dll',
                                '/out:' + str(cls.binary), str(source)], capture_output=True, text=True, timeout=30)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)

    def test_probe_reads_failed_results_without_mutating_the_job(self):
        for case in ('failed', 'passed', 'running', 'missing', 'evicted', 'old', 'wrong-id', 'capped', 'unicode'):
            with self.subTest(case=case):
                ran = subprocess.run([str(self.binary), case], capture_output=True, text=True, timeout=15,
                                     encoding='utf-8')
                self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
                report = json.loads(ran.stdout)
                if case in ('old', 'wrong-id'):
                    self.assertIn('rejected', report)
                elif case in ('running', 'missing', 'evicted'):
                    self.assertFalse(report['available'])
                else:
                    self.assertTrue(report['available'])
                    self.assertEqual(report['summary']['total'], 123)
                    self.assertEqual(report['summary']['failed'], 0 if case == 'passed' else 23)
                    self.assertEqual(report['status'], 'succeeded' if case == 'passed' else 'failed')
                    if case in ('capped', 'unicode'):
                        self.assertTrue(report['details_capped'])
                        self.assertTrue(report['text_truncated'])
                        self.assertEqual(len(report['details']), 50)
                        # MCP may duplicate the payload in text and structuredContent.
                        # Account for both copies and worst-case Unicode escaping.
                        payload = {'success': True, 'data': {'result': report, 'compiler': 'codedom'}}
                        wire = json.dumps({'content': [{'type': 'text', 'text': json.dumps(payload)}],
                                           'structuredContent': {'result': payload}})
                        self.assertLess(len(wire.encode()), 1024 * 1024)
