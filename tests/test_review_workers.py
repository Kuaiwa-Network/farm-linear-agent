"""Review admission and teardown reuse the existing native containment boundary."""
from types import SimpleNamespace
from unittest.mock import Mock

from agent.review_workers import ReviewWorkers
from agent.launcher import Launcher
from agent.review_policy import digest
from test_review import ReviewFixture, policy


class WorkerTests(ReviewFixture):
    def setUp(self):
        super().setUp()
        self.launcher = Mock(spec=Launcher)
        self.launcher.running.return_value = {}
        self.launcher.certified_pids.return_value = set()
        self.launcher.alive.return_value = False
        self.launcher.state_dir.side_effect = lambda identity: self.path.parent / 'runs' / identity
        self.launcher.spawn.return_value = SimpleNamespace(pid=123)
        self.git = Mock()
        self.git.read.return_value = self.path.parent / 'read-only source'
        self.config = SimpleNamespace(local_root=self.path.parent, issue_prefix='FARM', kw_ops={}, codex_workers={}, source_path=None)
        self.workers = ReviewWorkers(self.store, self.launcher, self.git, self.config, policy())

    def test_separate_three_attempt_budget_does_not_count_general_workers(self):
        snapshots = [self.candidate(self.delivery(n)[1]) for n in (1, 2, 3, 4)]
        for snapshot in snapshots[:3]:
            self.assertTrue(self.workers.launch(snapshot, 'review', {}))
        self.assertFalse(self.workers.launch(snapshots[3], 'review', {}))
        self.assertEqual(self.launcher.spawn.call_count, 3)
        for call in self.launcher.spawn.call_args_list:
            self.assertNotIn(self.git.read.return_value, call.kwargs['writable'])
            self.assertIn('GH_TOKEN', call.kwargs['withheld_env'])

    def test_submitted_report_keeps_slot_until_descendant_quiescence_is_verified(self):
        snapshot = self.candidate(self.delivery()[1])
        attempt = self.store.attempt(snapshot['id'], 'review')
        token = self.store.claim(attempt['id'], 60)
        self.store.submit(attempt['id'], token, self.verdict(snapshot))
        self.launcher.assert_quiescent.side_effect = RuntimeError('descendant alive')
        self.assertEqual(self.workers.reap(), [])
        self.assertEqual(self.workers.busy(), 1)
        self.launcher.assert_quiescent.side_effect = None
        self.assertEqual(len(self.workers.reap()), 1)
        self.assertEqual(self.workers.busy(), 0)
        # Restart before controller consumes it: the settled result remains recoverable.
        self.assertEqual(len(self.workers.reap()), 1)

    def test_restart_stops_only_owned_live_pid_and_retains_kill_evidence(self):
        snapshot = self.candidate(self.delivery()[1])
        attempt = self.store.attempt(snapshot['id'], 'review')
        self.store.db.execute('UPDATE review_attempts SET pid=123 WHERE id=?', (attempt['id'],))
        self.launcher.alive.return_value = True
        self.launcher.owned_pid.return_value = False
        self.workers.reap()
        self.launcher.kill_owned_attempt.assert_not_called()
        self.assertEqual(self.workers.busy(), 1)
        self.launcher.owned_pid.return_value = True
        self.workers.reap()
        self.launcher.kill_owned_attempt.assert_called_once_with(attempt['id'], 123)
        self.launcher.kill_pid.assert_not_called()

    def test_author_exit_without_descendant_proof_cannot_launch_reviewer(self):
        snapshot = self.candidate(self.delivery()[1])
        self.workers.author_launcher = Mock(spec=Launcher)
        self.workers.author_launcher.assert_quiescent.side_effect = RuntimeError('held cleanup')
        self.assertFalse(self.workers.launch(snapshot, 'review', {}))
        self.launcher.spawn.assert_not_called()

    def test_parallel_readers_are_allowed_but_repair_waits_for_all_related_readers(self):
        run, first = self.delivery()
        second = {**first, 'id': digest(['second', run['id']]), 'url': first['url'] + '0'}
        self.store.db.execute('INSERT INTO review_prs(id,run_id,repository,url,target,source_head,source_branch) VALUES(?,?,?,?,?,?,?)',
            tuple(second[k] for k in ('id', 'run_id', 'repository', 'url', 'target', 'source_head', 'source_branch')))
        a, b = self.candidate(first), self.candidate(second, letter='e')
        self.assertTrue(self.workers.launch(a, 'review', {}))
        self.assertTrue(self.workers.launch(b, 'review', {}))
        self.assertFalse(self.workers.launch(a, 'review', {}))
        self.assertFalse(self.workers.launch(a, 'repair', {}))
        self.assertEqual(self.workers.busy(), 2)
