"""Real Git cleanup retains recovery evidence and refuses uncertain ownership."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent.launcher import Launcher
from agent.review_retention import Retention
from test_review import ReviewFixture
import test_review_git as fixtures


class RetentionTests(ReviewFixture):
    def setUp(self):
        super().setUp()
        self.fixture = fixtures.CandidateGitTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.git = self.fixture.git
        self.run, pr = self.delivery()
        source = self.fixture.source(1, 'feature-new.txt', 'implementation')
        prepared = self.git.prepare(source, {'head': {'sha': source['source_head']}})
        self.snapshot = self.store.candidate(pr['id'], prepared, [])
        self.read = self.git.read(self.snapshot)
        self.git.publish_candidate(self.snapshot)
        self.launcher = Mock(spec=Launcher)
        self.launcher.runs_root = Path(self.tmp.name) / 'runs' / 'review'
        self.launcher.running.return_value = {}
        self.workers = SimpleNamespace(launcher=self.launcher, busy=lambda: 0)
        self.controller = SimpleNamespace(store=self.store, workers=self.workers, git=self.git,
                                          authorized={'common'}, writes=True)
        self.retention = Retention(self.controller, {'enabled': True, 'keep_hours': 1, 'sweep_seconds': 1})
        self.addCleanup(self.retention.close)

    def terminal(self):
        self.store.handoff(self.run['id'], 'human owns this now')
        self.now += 3601

    def test_retire_sources_and_branches_only_after_grace_preserving_repair_and_reports(self):
        attempt = self.store.attempt(self.snapshot['id'], 'repair')
        path = self.git.repair(self.snapshot, attempt['id'], 'FARM-1', 'FARM')
        (path / 'unfinished.txt').write_text('retain my unfinished repair', encoding='utf-8')
        self.store.db.execute("UPDATE review_attempts SET state='processed' WHERE id=?", (attempt['id'],))
        reports = self.launcher.runs_root / attempt['id'] / 'reports'
        reports.mkdir(parents=True)
        (reports / 'result.json').write_text('{}', encoding='utf-8')
        self.store.handoff(self.run['id'], 'human handoff')
        self.retention.tick()
        self.assertTrue(self.read.exists())
        self.now += 3601
        self.retention.tick()
        row = self.store.db.execute('SELECT * FROM review_cleanup').fetchone()
        self.assertIsNotNone(row['completed_at'], row['error'])
        self.assertFalse(path.exists())
        self.assertFalse(self.read.exists())
        self.assertTrue((reports / 'result.json').exists())
        self.assertEqual(self.git.local('common', 'rev-parse', 'refs/farmbot/review/' + self.snapshot['id']), self.snapshot['commit'])
        self.assertIn('retain my unfinished repair', self.git.local('common', 'show',
                      self.git.worktrees._recovery_ref(attempt['id']) + ':unfinished.txt'))
        self.assertEqual(self.git.local('common', 'for-each-ref', '--format=%(refname)', 'refs/heads/farmbot/*-review-*'), '')

    def test_unproven_descendants_hold_cleanup_and_retain_sources(self):
        attempt = self.store.attempt(self.snapshot['id'], 'repair')
        self.store.db.execute("UPDATE review_attempts SET state='processed' WHERE id=?", (attempt['id'],))
        self.terminal()
        self.launcher.assert_quiescent.side_effect = RuntimeError('descendant ownership unknown')
        self.retention.tick()
        self.assertTrue(self.read.exists())
        row = self.store.db.execute('SELECT * FROM review_cleanup').fetchone()
        self.assertIsNone(row['completed_at'])
        self.assertGreater(row['retry_at'], self.now)

    def test_pending_merge_and_active_related_workers_prevent_cleanup(self):
        attempt = self.store.attempt(self.snapshot['id'], 'repair')
        self.terminal()
        self.retention.tick()
        self.assertTrue(self.read.exists())
        self.store.db.execute("UPDATE review_attempts SET state='processed' WHERE id=?", (attempt['id'],))
        self.store.db.execute("INSERT INTO review_merges(candidate_id,state,request,created_at,updated_at) VALUES(?,'pending','{}',?,?)",
                              (self.snapshot['id'], self.now, self.now))
        self.retention.tick()
        self.assertTrue(self.read.exists())
        self.assertIsNone(self.store.db.execute('SELECT * FROM review_cleanup').fetchone())

    def test_unrelated_active_delivery_does_not_starve_terminal_cleanup(self):
        self.terminal()
        run, pr = self.delivery(2)
        snapshot = self.candidate(pr)
        self.store.attempt(snapshot['id'], 'review')
        self.workers.busy = lambda: 1
        self.retention.tick()
        self.assertFalse(self.read.exists())
        self.assertEqual(self.store.row('review_runs', run['id'])['state'], 'queued')

    def test_restart_after_checkout_removal_finishes_branch_retirement_from_audit(self):
        attempt = self.store.attempt(self.snapshot['id'], 'repair')
        path = self.git.repair(self.snapshot, attempt['id'], 'FARM-1', 'FARM')
        self.store.db.execute("UPDATE review_attempts SET state='processed' WHERE id=?", (attempt['id'],))
        self.terminal()
        original = self.git.local
        def interrupted(repo, *args, **kwargs):
            if args[:2] == ('update-ref', '-d'):
                raise OSError('interrupted after worktree removal')
            return original(repo, *args, **kwargs)
        with patch.object(self.git, 'local', side_effect=interrupted):
            self.retention.tick()
        self.assertFalse(path.exists())
        self.assertIsNone(self.store.db.execute('SELECT completed_at FROM review_cleanup').fetchone()[0])
        self.now += 3601
        self.retention.tick()
        row = self.store.db.execute('SELECT * FROM review_cleanup').fetchone()
        self.assertIsNotNone(row['completed_at'], row['error'])
        self.assertEqual(self.git.local('common', 'for-each-ref', '--format=%(refname)', 'refs/heads/farmbot/*-review-*'), '')
