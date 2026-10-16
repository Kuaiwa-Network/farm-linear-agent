"""Hydrated guard results and owned native execution cannot become a fake pass."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from agent.fgui_guards import METHODS, TRX_NS, _native_run, guard_results, verify_guards
from agent.ledger import LedgerError
import test_fgui_client_workflow as client_fixtures


def trx(path, *, outcomes=("Passed", "Passed")):
    ns = '{' + TRX_NS['t'] + '}'
    root = ET.Element(ns + 'TestRun'); definitions = ET.SubElement(root, ns + 'TestDefinitions')
    results = ET.SubElement(root, ns + 'Results')
    for i, (fullname, outcome) in enumerate(zip(sorted(METHODS), outcomes)):
        unit = ET.SubElement(definitions, ns + 'UnitTest', id=str(i))
        cls, method = fullname.rsplit('.', 1)
        ET.SubElement(unit, ns + 'TestMethod', className=cls+', Farm.Tests.Unit', name=method)
        ET.SubElement(results, ns + 'UnitTestResult', testId=str(i), outcome=outcome)
    summary = ET.SubElement(root, ns + 'ResultSummary')
    ET.SubElement(summary, ns + 'Counters', total='2', executed='2', passed='2')
    ET.ElementTree(root).write(path, encoding='utf-8', xml_declaration=True)


class GuardResultTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='UI guards 预览 '); self.addCleanup(temp.cleanup)
        self.path = Path(temp.name).resolve() / 'guards.trx'; trx(self.path)

    def test_both_actual_fully_qualified_guards_pass(self):
        result = guard_results(self.path)
        self.assertEqual(result['passed'], 2); self.assertEqual(set(result['tests']), METHODS)

    def test_inconclusive_skipped_failed_or_missing_test_is_pending(self):
        for outcome in ('Inconclusive', 'NotExecuted', 'Failed', 'Skipped'):
            trx(self.path, outcomes=('Passed', outcome))
            with self.subTest(outcome=outcome), self.assertRaisesRegex(LedgerError, 'remain pending'):
                guard_results(self.path)
        trx(self.path, outcomes=('Passed',))
        with self.assertRaises(LedgerError): guard_results(self.path)

    def test_same_method_names_from_another_fixture_cannot_supply_identity(self):
        self.path.write_text(self.path.read_text(encoding='utf-8').replace('Farm.Tests.Unit.', 'Foreign.Tests.'), encoding='utf-8')
        with self.assertRaises(LedgerError): guard_results(self.path)

    def test_entity_xml_is_refused_before_parsing(self):
        self.path.write_text('<!DOCTYPE fake [<!ENTITY x "fake">]><fake/>', encoding='utf-8')
        with self.assertRaisesRegex(LedgerError, 'entity'): guard_results(self.path)


class UiGuardWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = f = client_fixtures.UiClientWorkflowTests()
        f.internal = getattr(self, "internal", False)
        f.setUp(); self.addCleanup(f.doCleanups)
        f.installed(); f.source.commit(f.client)
        self.tool = f.source.root / 'dotnet.exe'; self.tool.write_bytes(b'fixture tool identity')
        self.enterContext(patch('agent.fgui_guards.shutil.which', return_value=str(self.tool)))
        self.runner = self.enterContext(patch('agent.fgui_guards._native_run', side_effect=self.run_guards))

    def run_guards(self, command, cwd, run, *, fence):
        fence(); trx(run / 'fgui-guards.trx')
        return {'exit_code': 0, 'job_assigned_before_startup': True, 'job_empty': True, 'seconds': 0}

    def verify(self):
        return verify_guards(self.fixture.workflow, self.fixture.exported['receipt_id'])

    def test_actual_command_is_fixed_to_owned_client_and_fresh_result_path(self):
        proof = self.fixture.source.cli('verify-ui-guards', '--receipt-id', self.fixture.exported['receipt_id'])
        args = self.runner.call_args.args
        self.assertEqual(args[1], self.fixture.client)
        self.assertEqual(args[0][1:4], ['test', 'tests/Farm.Tests.Unit/Farm.Tests.Unit.csproj', '-c'])
        self.assertIn('--disable-build-servers', args[0])
        self.assertEqual(proof['commit'], self.fixture.source.git(self.fixture.client, 'rev-parse', 'HEAD'))
        self.assertEqual(proof['passed'], 2)

    def test_source_or_tool_change_during_guards_refuses_success(self):
        def changed(*args, **kwargs):
            result = self.run_guards(*args, **kwargs); self.tool.write_bytes(b'changed executable'); return result
        self.runner.side_effect = changed
        with self.assertRaisesRegex(LedgerError, 'changed during'): self.verify()
        self.assertEqual(self.fixture.ledger.connection.execute("SELECT count(*) FROM audit WHERE kind='fgui_guards_complete'").fetchone()[0], 0)

    def test_same_candidate_continuation_reuses_completed_guard_proof(self):
        proof = self.verify()
        self.assertEqual(self.verify(), proof)
        self.assertEqual(self.runner.call_count, 1)
        ledger = self.fixture.ledger
        self.assertEqual(ledger.connection.execute("SELECT count(*) FROM audit WHERE kind='fgui_guards_complete'").fetchone()[0], 1)
        self.assertEqual(ledger.connection.execute("SELECT count(*) FROM audit WHERE kind='fgui_guards_reused'").fetchone()[0], 1)
        status = self.fixture.source.cli('ui-verification-status', '--receipt-id', self.fixture.exported['receipt_id'])
        self.assertEqual(status['guards'], proof)
        self.assertIsNone(status['loading'])
        self.assertEqual(status['next_action'], 'await-resource')
        self.assertEqual(self.runner.call_count, 1)

    def test_current_tool_change_requires_new_actual_guards(self):
        original = self.verify()
        self.tool.write_bytes(b'new selected executable')
        status = self.fixture.source.cli('ui-verification-status', '--receipt-id', self.fixture.exported['receipt_id'])
        self.assertIsNone(status['guards'])
        self.assertEqual(status['next_action'], 'verify-ui-guards')
        current = self.verify()
        self.assertEqual(self.runner.call_count, 2)
        self.assertNotEqual(current['dotnet_sha256'], original['dotnet_sha256'])

    def test_same_candidate_survives_source_and_client_main_advances_without_handoff_or_retest(self):
        f = self.fixture; proof = self.verify()
        source_main = f.source.advance_source_main()
        origin = Path(f.source.config.repos['Farm-Client'])
        (origin / 'README.md').write_text('unrelated upstream change', encoding='utf-8')
        f.source.commit(origin)
        before = f.source.git(f.client, 'rev-parse', 'HEAD')
        with patch('agent.fgui_workflow.github_api', return_value={'object': {'sha': source_main}}):
            self.assertEqual(self.verify(), proof)
            queued = f.source.cli('await-resource', '--resource', 'unity_slot', '--mode', 'interactive', '--commit', before)
        self.assertEqual(self.runner.call_count, 1)
        self.assertEqual(f.source.git(f.client, 'rev-parse', 'HEAD'), before)
        pending = [r for r in f.ledger.reservations(states=('queued',)) if r['item_id'] == f.item]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]['commit_sha'], before)
        self.assertEqual(f.ledger.item(f.item)['state'], 'awaiting_resource')
        self.assertFalse(f.ledger.connection.execute(
            "SELECT 1 FROM audit WHERE kind='repository_handoff_requested' AND id>"
            "(SELECT MAX(id) FROM audit WHERE kind='fgui_guards_complete')").fetchone())

    def test_changed_candidate_requires_new_actual_guards(self):
        f = self.fixture; old = self.verify()
        origin = Path(f.source.config.repos['Farm-Client'])
        (origin / 'README.md').write_text('upstream integration', encoding='utf-8')
        f.source.commit(origin); f.workflow.trees.fetch('Farm-Client')
        f.source.git(f.client, 'merge', '--no-edit', 'origin/main')
        current = self.verify()
        self.assertEqual(self.runner.call_count, 2)
        self.assertNotEqual(current['commit'], old['commit'])

    def test_damaged_retained_proof_is_refused_without_running_or_reusing(self):
        self.verify()
        self.fixture.ledger.connection.execute("UPDATE audit SET details='{}' WHERE kind='fgui_guards_complete'")
        with self.assertRaisesRegex(LedgerError, 'damaged controller'):
            self.verify()
        self.assertEqual(self.runner.call_count, 1)

    def test_rewritten_remote_main_cannot_borrow_retained_qualification(self):
        self.verify(); f = self.fixture
        origin = Path(f.source.config.repos['Farm-Client'])
        f.source.git(origin, 'checkout', '--orphan', 'rewritten')
        f.source.git(origin, 'commit', '-qm', 'new unrelated root')
        f.source.git(origin, 'branch', '-M', 'main')
        from agent.worktrees import WorktreeError
        with self.assertRaises(WorktreeError):
            self.verify()
        self.assertEqual(self.runner.call_count, 1)

    def test_stop_refuses_retained_proof_reuse(self):
        self.verify()
        self.fixture.ledger.cancel(self.fixture.item, 'Stop before reuse')
        with self.assertRaises(LedgerError): self.verify()
        self.assertEqual(self.runner.call_count, 1)

    def test_stop_after_process_retains_results_without_success(self):
        def stopped(*args, **kwargs):
            result = self.run_guards(*args, **kwargs); self.fixture.ledger.cancel(self.fixture.item, 'Stop during guards'); return result
        self.runner.side_effect = stopped
        with self.assertRaises(LedgerError): self.verify()
        self.assertTrue(list((self.fixture.paths.runs / self.fixture.item / 'ui-checks').glob('*/fgui-guards.trx')))
        self.assertEqual(self.fixture.ledger.connection.execute("SELECT count(*) FROM audit WHERE kind='fgui_guards_complete'").fetchone()[0], 0)


@unittest.skipUnless(os.name == 'nt', 'native Windows UI guard Job Objects')
class NativeGuardProcessTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='native UI guard 预览 '); self.addCleanup(temp.cleanup)
        self.run = Path(temp.name).resolve()

    def test_selected_python_redirector_runs_only_after_assignment_and_drains_job(self):
        marker = self.run / 'ran.json'
        code = 'import pathlib,sys;pathlib.Path(sys.argv[1]).write_text("ran",encoding="utf-8")'
        result = _native_run([sys.executable, '-B', '-c', code, str(marker)], self.run, self.run, fence=lambda: None, timeout=5)
        self.assertEqual(marker.read_text(encoding='utf-8'), 'ran')
        self.assertTrue(result['job_assigned_before_startup']); self.assertTrue(result['job_empty'])

    def test_stop_before_resume_never_starts_process(self):
        marker = self.run / 'should-not-run'
        calls = 0
        def fence():
            nonlocal calls; calls += 1
            if calls == 2: raise LedgerError('fixture Stop before resume')
        code = 'import pathlib,sys;pathlib.Path(sys.argv[1]).write_text("ran")'
        with self.assertRaisesRegex(LedgerError, 'Stop before resume'):
            _native_run([sys.executable, '-B', '-c', code, str(marker)], self.run, self.run, fence=fence)
        self.assertFalse(marker.exists())

    def test_timeout_reaps_owned_python_tree_and_retains_logs(self):
        with self.assertRaisesRegex(LedgerError, 'timed out'):
            _native_run([sys.executable, '-B', '-c', 'import time;time.sleep(30)'], self.run, self.run,
                        fence=lambda: None, timeout=.25)
        self.assertTrue((self.run / 'stdout.log').is_file()); self.assertTrue((self.run / 'stderr.log').is_file())
