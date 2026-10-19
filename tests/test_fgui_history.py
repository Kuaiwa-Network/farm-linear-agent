"""The host fills source objects; the Client claim keeps its original stage."""
import json
import unittest
from unittest.mock import patch

from agent.config import ROOT
from agent.fgui_history import REQUEST, RESULT, ensure_history, refresh_pending
from agent.ledger import LedgerError
from agent.scheduler import Scheduler
from agent.worktrees import HOOKS_OFF, WorktreeError, Worktrees
import test_fgui_workflow as fixtures
from test_scheduler import FakeLauncher


class SourceHistoryHostTests(unittest.TestCase):
    def setUp(self):
        self.fixture = f = fixtures.UiWorkflowTests()
        f.setUp(); self.addCleanup(f.doCleanups)
        self.workflow = f.workflow()
        self.main = f.advance_source_main()

    def request(self):
        with self.assertRaisesRegex(LedgerError, 'refresh pending'):
            ensure_history(self.workflow, self.main, self.fixture.head, timeout=0)

    def result(self):
        row = self.fixture.ledger.connection.execute(
            'SELECT details FROM audit WHERE kind=? ORDER BY id DESC LIMIT 1', (RESULT,)).fetchone()
        return json.loads(row['details'])

    def test_worker_only_requests_then_real_scheduler_tick_refreshes_without_handoff(self):
        f = self.fixture
        before = f.ledger.item(f.item)
        with patch.object(Worktrees, 'fetch', side_effect=AssertionError('worker must not fetch source')):
            self.request()
        self.assertFalse(f.trees.has_commit('farmgui', self.main))
        scheduler = Scheduler(f.ledger, FakeLauncher(f.paths.runs), {'fgui': f.skill}, f.trees,
            replay_legacy=True, skill_root=ROOT / 'skills', db_path=f.paths.ledger, runtime_name='fake', host='fixture',
            enabled_skills={'fgui'})
        original = Worktrees.fetch
        with patch.object(Worktrees, 'fetch', autospec=True,
                          side_effect=lambda trees, repo, **options: original(trees, repo, **options)) as fetch:
            scheduler.tick()
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(fetch.call_args.kwargs, {'config': HOOKS_OFF, 'timeout': 60})
        self.assertEqual(self.result()['state'], 'ready')
        self.assertTrue(f.trees.has_commit('farmgui', self.main))
        current = f.ledger.item(f.item)
        for key in ('state', 'generation', 'root_repo', 'next_root_repo', 'checkpoint'):
            self.assertEqual(current[key], before[key])
        self.assertEqual(f.git(f.path, 'rev-parse', 'HEAD'), f.head)

    def test_multiple_requests_share_one_host_fetch_and_are_not_reprocessed(self):
        self.request(); self.request(); f = self.fixture
        original = Worktrees.fetch
        with patch.object(Worktrees, 'fetch', autospec=True,
                          side_effect=lambda trees, repo, **options: original(trees, repo, **options)) as fetch:
            self.assertEqual(refresh_pending(f.ledger, f.trees), 2)
            self.assertEqual(refresh_pending(f.ledger, f.trees), 0)
            self.assertEqual(fetch.call_count, 1)

    def test_stopped_claim_does_not_request_a_host_fetch(self):
        self.request(); f = self.fixture
        f.ledger.cancel(f.item, 'Stop before host refresh')
        with patch.object(Worktrees, 'fetch', side_effect=AssertionError('stale fetch')):
            refresh_pending(f.ledger, f.trees)
        self.assertEqual(self.result()['state'], 'stale')

    def test_replaced_claim_with_same_generation_and_pid_cannot_refresh(self):
        self.request(); f = self.fixture
        f.ledger.connection.execute('UPDATE work_items SET token=? WHERE id=?', ('replacement-hash', f.item))
        with patch.object(Worktrees, 'fetch', side_effect=AssertionError('old claim fetch')):
            refresh_pending(f.ledger, f.trees)
        self.assertEqual(self.result()['state'], 'stale')

    def test_host_failure_is_bounded_and_does_not_disclose_transport_output(self):
        f = self.fixture
        with patch.object(Worktrees, 'fetch', side_effect=WorktreeError('private transport detail')), \
                self.assertRaisesRegex(LedgerError, 'host prerequisite gap'):
            ensure_history(self.workflow, self.main, f.head)
        result = self.result()
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(result['error_type'], 'WorktreeError')
        self.assertNotIn('private transport detail', json.dumps(result))

    def test_request_cannot_supply_git_arguments_or_another_remote(self):
        self.request(); f = self.fixture
        row = f.ledger.connection.execute('SELECT id,details FROM audit WHERE kind=?', (REQUEST,)).fetchone()
        requested = json.loads(row['details']); requested['main'] = '--upload-pack=foreign'
        requested['repository'] = 'foreign'; requested['url'] = 'https://foreign.invalid'
        f.ledger.connection.execute('UPDATE audit SET details=? WHERE id=?', (json.dumps(requested), row['id']))
        with patch.object(Worktrees, 'fetch', side_effect=AssertionError('untrusted request')):
            refresh_pending(f.ledger, f.trees)
        self.assertEqual(self.result()['state'], 'failed')
