"""Real retained exports cross child teardown, without moving claims or repeating export."""
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from agent.fgui_client import install
from agent.fgui_scope import verify_candidate
from agent.fgui_workflow import UiWorkflow
from agent.ledger import LedgerError
from agent.presentation_artifacts import verify_source_delivery, require_parent_integrations
from agent.presentation_contract import source_only
from agent.skills import load_skills
from agent.stages import write_repositories
from agent.workflow import client_ready, delegate_task
import test_fgui_client_workflow as fixtures
import test_general_workflow as general

SKILLS = load_skills(Path(__file__).resolve().parents[1] / 'skills')


class SourceBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.f = f = general.GeneralWorkflowTests()
        f.setUp(); self.addCleanup(f.doCleanups)

    def test_new_source_task_cannot_enter_client_or_reserve_unity_but_parent_can_integrate(self):
        f = self.f
        task = f.task()
        self.assertTrue(source_only(task))
        self.assertEqual(write_repositories(task, SKILLS['fgui']), ('farmgui',))
        token = f.claim_task(task)
        with self.assertRaisesRegex(LedgerError, 'parent owns Client'):
            client_ready(f.ledger, f.ledger._row(task['id']))
        with self.assertRaisesRegex(LedgerError, 'parent owns Unity'):
            f.ledger.await_resource(task['id'], token, 'unity_slot', 'interactive', skill=SKILLS['fgui'])
        f.save(D='done')
        client_ready(f.ledger, f.ledger._row(f.parent))
        self.assertEqual(f.task()['id'], task['id'])

    def test_scope_and_dependencies_cannot_name_scripts_or_future_tasks(self):
        f = self.f
        for scope, deps in (({'assets': ['Assets/code.cs'], 'validation': 'source'}, []),
                            ({'assets': ['Assets/a.prefab'], 'validation': 'source'}, ['future'])):
            with self.subTest(scope=scope, deps=deps), self.assertRaises(LedgerError):
                delegate_task(f.ledger, f.parent, f.token, {'key': 'scene', 'capability': 'scene',
                    'instructions': 'scene', 'scope': scope, 'depends_on': deps})


class SourceArtifactTests(unittest.TestCase):
    def setUp(self):
        self.f = f = fixtures.UiClientWorkflowTests()
        f.internal = True
        f.setUp(); self.addCleanup(f.doCleanups)
        self.ledger, self.child, self.parent = f.ledger, f.item, f.parent
        self.receipt = f.exported['receipt_id']

    def deliver(self, *, teardown=True):
        f = self.f
        proof = verify_source_delivery(f.workflow, [f.source.url])
        evidence = {'summary': 'Approved source export ready', 'verification': 'Certified native export',
            'prs': [f.source.url], 'ui_source_delivery_id': proof['delivery_id'], 'receipt_id': self.receipt}
        self.ledger.finish(self.child, f.source.token, 'delivered', evidence)
        if teardown:
            self.ledger.record_cleanup(self.child, {}, done=True)
        self.ledger.connection.execute("UPDATE work_items SET root_repo='Farm-Client' WHERE id=?", (self.parent,))
        return proof

    def parent_workflow(self):
        f = self.f
        return UiWorkflow(self.ledger, self.parent, f.source.parent_token, f.source.config, SKILLS['work'],
                          lambda: f.source.api, stage='Farm-Client', receipt_id=self.receipt, consume=True)

    def test_original_export_hands_off_after_teardown_and_parent_verifies_its_business_candidate(self):
        f = self.f
        proof = self.deliver(teardown=False)
        parent = self.parent_workflow()
        with self.assertRaisesRegex(LedgerError, 'teardown'):
            install(parent, self.receipt)
        self.ledger.record_cleanup(self.child, {}, done=True)
        before_exports = self.ledger.connection.execute("SELECT count(*) FROM audit WHERE kind='fgui_export_complete'").fetchone()[0]
        installed = install(parent, self.receipt)
        self.assertEqual(installed['source_head'], proof['heads']['farmgui'])
        (f.client / 'business.cs').write_text('// issue implementation\n', encoding='utf-8')
        f.source.commit(f.client)
        head = f.source.git(f.client, 'rev-parse', 'HEAD')
        checked = verify_candidate(parent, self.receipt, expected_commit=head)
        self.assertEqual(checked['source_task_id'], self.child)
        self.assertIn('business.cs', checked['changed_client_files'])
        self.assertEqual(self.ledger.connection.execute("SELECT count(*) FROM audit WHERE kind='fgui_export_complete'").fetchone()[0], before_exports)
        with self.assertRaisesRegex(LedgerError, 'final Client candidate'):
            require_parent_integrations(self.ledger, self.ledger.item(self.parent))
        self.assertEqual(install(parent, self.receipt)['installation_id'], installed['installation_id'])

    def test_partial_client_install_cannot_be_discarded_by_source_migration(self):
        self.f.installed()
        with self.assertRaisesRegex(LedgerError, 'journal recovery'):
            verify_source_delivery(self.f.workflow, [self.f.source.url])

    def test_parent_guards_and_loading_bind_final_candidate_without_child_unity_or_reexport(self):
        from agent.fgui_guards import verify_guards
        from agent.fgui_unity import verify_loading
        from agent.slots import SlotPool, slot_entry
        from test_fgui_guards import trx
        from test_slots import FakeMcp
        f = self.f
        self.deliver()
        workflow = self.parent_workflow()
        install(workflow, self.receipt)
        f.source.commit(f.client)
        head = f.source.git(f.client, 'rev-parse', 'HEAD')
        plan = {'stages': {'A': 'done', 'B': 'skipped: no config', 'C': 'skipped: no config', 'D': 'done'},
                'client': {'head': head}}
        f.ledger.checkpoint(self.parent, f.source.parent_token, {'plan': plan})
        tool = f.source.root / 'parent-dotnet.exe'; tool.write_bytes(b'fixture tool')

        def run_guards(command, cwd, run, *, fence):
            fence(); trx(run / 'fgui-guards.trx')
            return {'exit_code': 0, 'job_assigned_before_startup': True, 'job_empty': True, 'seconds': 0}

        with patch('agent.fgui_guards.shutil.which', return_value=str(tool)), \
                patch('agent.fgui_guards._native_run', side_effect=run_guards):
            verify_guards(workflow, self.receipt)
        with self.assertRaisesRegex(LedgerError, 'guards and Unity loading'):
            require_parent_integrations(f.ledger, f.ledger.item(self.parent))
        entry = slot_entry({'id': 'unity_slot:1', 'folder': str(f.source.root / 'Parent Editor 空间'),
                            'build_target': 'StandaloneWindows64'})
        f.source.config.slots = [entry]
        mcp = FakeMcp()
        pool = SlotPool(f.ledger, f.source.trees, [entry], host='fixture', editors_root=f.paths.editors,
                        mcp=mcp, editor_scan=lambda folder: None,
                        editor_pid=lambda folder: 4242 if str(folder) in mcp.open_folders else None,
                        state_dir=lambda item: f.paths.runs / item)
        pool.ensure()
        f.ledger.await_resource(self.parent, f.source.parent_token, 'unity_slot', 'interactive',
                               skill=SKILLS['work'], commit_sha=head, issue_prefix=f.source.config.issue_prefix)
        self.assertEqual(pool.grant(), 1)
        reservation = f.ledger.active_reservation(self.parent)
        reservation['token'] = pool.token_path(self.parent).read_text(encoding='utf-8')
        f.source.parent_token = f.ledger.claim(self.parent, worker_id='parent-loading')['token']
        workflow = self.parent_workflow()
        identity = Mock(); source = {'commit_sha': head, 'dirty': []}
        identity.probe.return_value = {'aggregate': 'match', 'source_before': source, 'source_after': source}
        identity._client.return_value.call_tool.return_value = {'result': {'packages': [
            {'name': 'One', 'id': 'pack0001', 'items': 1, 'disk_assets': 1,
             'descriptor_sha256': hashlib.sha256((f.client / 'Assets/GameRes/FairyRes/One/One_fui.bytes').read_bytes()).hexdigest()}],
            'cleanup_complete': True}}
        with patch('agent.fgui_unity.UnityIdentity', return_value=identity):
            verify_loading(workflow, self.receipt)
        f.ledger.release(reservation['reservation_id'], reservation['token'], 'fixture clean release')
        require_parent_integrations(f.ledger, f.ledger.item(self.parent))
        self.assertEqual(f.ledger.connection.execute('SELECT count(*) FROM reservations WHERE item_id=?', (self.child,)).fetchone()[0], 0)
        self.assertEqual(f.ledger.connection.execute("SELECT count(*) FROM audit WHERE kind='fgui_export_complete'").fetchone()[0], 1)
        plan['client']['head'] = 'a' * 40
        f.ledger.checkpoint(self.parent, f.source.parent_token, {'plan': plan})
        with self.assertRaisesRegex(LedgerError, 'guards and Unity loading'):
            require_parent_integrations(f.ledger, f.ledger.item(self.parent))

    def test_tampered_export_cannot_be_consumed_and_source_task_is_not_reopened(self):
        f = self.f
        self.deliver()
        stage = f.paths.runs / self.child / 'ui-exports' / self.receipt / 'staging'
        file = next(stage.rglob('*.png'))
        file.write_bytes(b'not an approved image')
        with self.assertRaises((LedgerError, ValueError)):
            install(self.parent_workflow(), self.receipt)
        self.assertEqual(self.ledger.item(self.child)['state'], 'delivered')
        self.assertEqual(self.ledger.connection.execute("SELECT count(*) FROM audit WHERE item_id=? AND kind='fgui_install_complete'",
                                                     (self.parent,)).fetchone()[0], 0)

    def test_changed_legacy_client_cannot_be_called_source_only(self):
        f = self.f
        (f.client / 'unowned.cs').write_text('// change\n', encoding='utf-8')
        f.source.commit(f.client)
        with self.assertRaisesRegex(LedgerError, 'unchanged Client entry'):
            verify_source_delivery(f.workflow, [f.source.url])

    def test_source_identity_and_parent_membership_cannot_be_borrowed(self):
        self.deliver()
        original = self.ledger._row(self.child)['claimed_fingerprint']
        self.ledger.connection.execute("UPDATE work_items SET claimed_fingerprint='changed' WHERE id=?", (self.child,))
        with self.assertRaisesRegex(LedgerError, 'identity changed'):
            install(self.parent_workflow(), self.receipt)
        self.ledger.connection.execute('UPDATE work_items SET claimed_fingerprint=?,parent_id=NULL WHERE id=?',
                                       (original, self.child))
        with self.assertRaisesRegex(LedgerError, 'from this parent'):
            install(self.parent_workflow(), self.receipt)
