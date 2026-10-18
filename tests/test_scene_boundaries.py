"""Exact asset boundaries and a quiescent, bounded parent repair round."""
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent.ledger import LedgerError
from agent.presentation_repair import yield_client, resume
from agent.scene_scope import verify_scope, require_loading
from agent.skills import load_skills
from agent.workflow import JobWorktrees, repository_busy
from agent.worktrees import Worktrees
import test_scene_delivery as fixtures
from test_worktrees import git

SKILLS = load_skills(Path(__file__).resolve().parents[1] / 'skills')
HANDOFF = {'facts': [], 'hypotheses': [], 'checks': [], 'repositories': [], 'next_actions': ['parent prerequisite repair']}


class SceneBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.f = f = fixtures.SceneDeliveryTests()
        f.setUp(); self.addCleanup(f.doCleanups)
        self.trees = JobWorktrees(Worktrees(f.config.local_root / 'repos', f.config.local_root / 'worktrees', f.config.repos), f.ledger)

    def test_new_scene_delivers_asset_evidence_without_a_separate_client_pr(self):
        f = self.f
        f.changed()
        proof = f.proof({'summary': 'Assets ready', 'verification': 'Source scope checked', 'prs': []})
        self.assertEqual(proof['prs'], [])
        self.assertEqual(proof['asset_checks']['scope']['assets'], ['Assets/nursery.prefab'])
        self.assertEqual(len(proof['asset_checks']['files']), 2)

    def test_unrelated_script_and_invalid_metadata_are_refused(self):
        f = self.f
        f.changed()
        (f.client / 'Assets/Unexpected.cs').write_text('// extra', encoding='utf-8')
        git('add', '.', cwd=f.client); git('commit', '-qm', 'unrelated code', cwd=f.client)
        with self.assertRaisesRegex(LedgerError, 'declared asset scope'):
            f.proof()
        git('rm', 'Assets/Unexpected.cs', cwd=f.client); git('commit', '-qm', 'remove unrelated code', cwd=f.client)
        (f.client / 'Assets/nursery.prefab.meta').write_text('guid: ' + 'a' * 33 + '\n', encoding='utf-8')
        git('add', '.', cwd=f.client); git('commit', '-qm', 'invalid metadata', cwd=f.client)
        with self.assertRaisesRegex(LedgerError, 'valid GUID'):
            f.proof()

    def test_unity_mode_requires_real_controller_asset_loading_not_a_claimed_pass(self):
        f = self.f
        f.changed()
        checked = verify_scope(f.ledger, f.ledger.item(f.task), self.trees, f.client, git('rev-parse', 'HEAD', cwd=f.client))
        checked['scope']['validation'] = 'unity'
        with self.assertRaisesRegex(LedgerError, 'verify-scene-assets'):
            require_loading(f.ledger, f.ledger.item(f.task), checked)

    def test_asset_loading_uses_exact_reserved_candidate_and_rejects_wrong_results(self):
        from agent.scene_scope import verify_loading
        from agent.slots import SlotPool, slot_entry
        from test_slots import FakeMcp
        f = self.f
        f.changed()
        head = git('rev-parse', 'HEAD', cwd=f.client)
        entry = slot_entry({'id': 'unity_slot:1', 'folder': str(f.config.local_root / 'Scene Editor 空间'),
                            'build_target': 'StandaloneWindows64'})
        f.config.slots = [entry]
        mcp = FakeMcp()
        pool = SlotPool(f.ledger, self.trees, [entry], host='fixture',
                        editors_root=f.config.local_root / 'editors', mcp=mcp,
                        editor_scan=lambda folder: None,
                        editor_pid=lambda folder: 4242 if str(folder) in mcp.open_folders else None,
                        state_dir=lambda item: f.config.local_root / 'runs' / item)
        pool.ensure()
        f.ledger.await_resource(f.task, f.token, 'unity_slot', 'interactive', skill=SKILLS['scene'],
                               commit_sha=head, issue_prefix=f.config.issue_prefix)
        self.assertEqual(pool.grant(), 1)
        f.token = f.ledger.claim(f.task, worker_id='scene-loading')['token']
        identity = Mock(); identity.probe.return_value = {'aggregate': 'match'}
        identity._client.return_value.call_tool.return_value = {'result': {'loaded': True, 'assets': []}}
        with patch('agent.slots.UnityIdentity', return_value=identity):
            with self.assertRaisesRegex(LedgerError, 'successful loading'):
                verify_loading(f.ledger, f.task, f.token, f.config, lambda: f.api)
            identity._client.return_value.call_tool.return_value = {'result': {
                'loaded': True, 'assets': ['Assets/nursery.prefab']}}
            proof = verify_loading(f.ledger, f.task, f.token, f.config, lambda: f.api)
        self.assertEqual(proof['commit'], head)
        self.assertEqual(f.ledger.connection.execute("SELECT count(*) FROM audit WHERE kind='scene_assets_loaded'").fetchone()[0], 1)

    def test_scope_rejects_windows_aliases_and_non_asset_paths(self):
        from agent.presentation_contract import validate_scope
        for paths in (['Assets/../outside.prefab'], ['Assets/NUL.prefab'], ['Assets/a .prefab.'],
                      ['Assets/a.prefab', 'Assets/A.prefab'], ['Assets/a*.prefab'], ['Assets/code.cs']):
            with self.subTest(paths=paths), self.assertRaises(LedgerError):
                validate_scope({'assets': paths, 'validation': 'source'})

    def test_stale_child_claim_cannot_request_parent_repairs(self):
        f = self.f
        f.ledger.connection.execute("UPDATE work_items SET claimed_fingerprint='stale' WHERE id=?", (f.task,))
        with self.assertRaisesRegex(LedgerError, 'changed issue inputs'):
            yield_client(f.ledger, f.task, f.token, self.trees, f.config,
                         {'reason': 'Existing metadata needs parent repair', 'paths': ['Assets/Existing.cs.meta']})

    def test_parent_repairs_only_named_prerequisite_after_child_exit_without_repinning(self):
        f = self.f
        f.changed()
        baseline = f.ledger.item(f.task)['task_client_base']
        f.ledger.checkpoint(f.task, f.token, {'plan': {}, 'handoff': HANDOFF})
        proof = yield_client(f.ledger, f.task, f.token, self.trees, f.config,
            {'reason': 'Existing dependency metadata prevents local validation', 'paths': ['Assets/Existing.cs.meta']})
        f.ledger.connection.execute("UPDATE work_items SET root_repo='Farm-Client' WHERE id=?", (f.parent,))
        launcher = Mock(spec=['assert_quiescent'])
        launcher.assert_quiescent.side_effect = RuntimeError('writer still alive')
        self.assertTrue(repository_busy(f.ledger, f.ledger.item(f.parent), SKILLS, launcher=launcher))
        launcher.assert_quiescent.side_effect = None
        self.assertFalse(repository_busy(f.ledger, f.ledger.item(f.parent), SKILLS, launcher=launcher))
        (f.client / 'Assets/Existing.cs.meta').write_text('guid: ' + 'c' * 32 + '\n', encoding='utf-8')
        git('add', '.', cwd=f.client); git('commit', '-qm', 'repair named prerequisite', cwd=f.client)
        resumed = resume(f.ledger, f.parent, f.parent_token, 'scene', self.trees, f.config, launcher)
        self.assertEqual(resumed['yield_id'], proof['yield_id'])
        self.assertEqual(f.ledger.item(f.task)['task_client_base'], baseline)
        f.token = f.ledger.claim(f.task, worker_id='scene-after-repair')['token']
        checked = verify_scope(f.ledger, f.ledger.item(f.task), self.trees, f.client, resumed['commit'])
        self.assertNotIn('Assets/Existing.cs.meta', [v['path'] for v in checked['files']])
        (f.client / 'Assets/Existing.cs.meta').write_text('guid: ' + 'd' * 32 + '\n', encoding='utf-8')
        git('add', '.', cwd=f.client); git('commit', '-qm', 'tamper parent repair', cwd=f.client)
        with self.assertRaisesRegex(LedgerError, 'inherited parent'):
            verify_scope(f.ledger, f.ledger.item(f.task), self.trees, f.client, git('rev-parse', 'HEAD', cwd=f.client))
