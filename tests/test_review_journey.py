"""A real contained worker claims/submits through the selected configuration and CLI."""
import json
import os
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent.config import Paths, load_config
from agent.launcher import Launcher, RUNTIMES, _PINNED_EXIT
from agent.ledger import Ledger
from agent.review_store import ReviewStore
from agent.review_workers import ReviewWorkers
from agent.review_controller import Controller
from agent.review_policy import digest
from test_review import APP, ReviewFixture, policy


class ReviewJourneyTests(ReviewFixture):
    def test_controller_integrates_reviews_and_atomically_merges_real_local_git_candidates(self):
        from test_review_git import CandidateGitTests
        from test_review_controller import GitHub, Workers
        from test_worktrees import git
        fixture = CandidateGitTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        github = GitHub()
        github.target = lambda pr: git('rev-parse', pr['target'], cwd=fixture.remote)
        def metadata(pr):
            head = git('rev-parse', pr['source_branch'], cwd=fixture.remote)
            base = github.target(pr)
            return {'html_url': pr['url'], 'head': {'sha': head}, 'base': {'sha': base},
                    'merged': fixture.git.ancestor('common', head, base),
                    'draft': False, 'mergeable': True, 'labels': []}
        github.pr = metadata
        workers = Workers(self.store)
        config = SimpleNamespace(auto_merge=policy(mode='merge'), repos={'common': str(fixture.remote)},
                                 instance_id='test', local_root=self.path.parent)
        controller = Controller(self.ledger, config, SimpleNamespace(app_user_id=APP, fetch_issue=self.ledger.issue),
                                github, fixture.git, workers)
        runs = []
        for number in (1, 2):
            source = fixture.source(number, 'feature-' + str(number) + '.txt', str(number))
            run, pr = self.delivery(number)
            runs.append(run)
            self.store.db.execute('UPDATE review_runs SET policy=? WHERE id=?', (controller.run_policy(run['id']), run['id']))
            self.store.db.execute('UPDATE review_prs SET source_head=? WHERE id=?', (source['source_head'], pr['id']))
        controller.tick()
        self.assertEqual(len(workers.launched), 2)
        snapshots = []
        for attempt in workers.launched:
            snapshot = json.loads(self.store.row('review_candidates', attempt['candidate_id'])['snapshot'])
            snapshots.append(snapshot)
            workers.finish(attempt, self.verdict(snapshot))
            github.states[snapshot['commit']] = 'passed'
        controller.tick()
        self.assertEqual(git('rev-parse', 'main', cwd=fixture.remote), snapshots[-1]['commit'])
        self.assertEqual([self.store.row('review_runs', run['id'])['disposition'] for run in runs], ['auto_merged'] * 2)
        for snapshot in snapshots:
            self.assertEqual(git('rev-parse', snapshot['source_branch'], cwd=fixture.remote), snapshot['commit'])

    @unittest.skipUnless(os.name == 'nt' or _PINNED_EXIT, 'native self-exit proof requires Windows or POSIX waitid WNOWAIT')
    def test_native_worker_claim_submit_and_verified_slot_release(self):
        self.ledger.close()
        root = self.path.parent / 'isolated state 空间'
        config_path = self.path.parent / 'selected.json'
        config_path.write_text(json.dumps({
            'client_id': 'offline', 'client_secret': 'offline', 'webhook_secret': 'offline', 'host': 'test',
            'runtime': 'fake', 'local_root': str(root), 'repos': {'common': 'https://github.com/example/common.git'},
            'auto_merge': policy(), 'instance_id': 'test'}), encoding='utf-8')
        config = load_config(config_path)
        self.path = Paths(config).ledger
        self.ledger = Ledger(self.path, clock=lambda: self.now)
        self.addCleanup(self.ledger.close)
        self.store = ReviewStore(self.ledger)
        snapshot = self.candidate(self.delivery()[1])
        script = root / 'review_worker.py'
        script.write_text('''import json, os, subprocess, sys
from pathlib import Path
assert "GH_TOKEN" not in os.environ
sys.stdin.reconfigure(encoding="utf-8")
prompt = sys.stdin.read()
p = json.loads(prompt[prompt.index("\\n{") + 1:])
subprocess.run(p["claim_argv"], check=True)
report = {"snapshot":p["snapshot"]["id"], "risk":"routine", "reasons":[], "findings":[], "summary":"No introduced defect"}
Path(p["submit_argv"][-1]).write_text(json.dumps(report), encoding="utf-8")
subprocess.run(p["submit_argv"], check=True)
Path(sys.argv[1]).write_text("review submitted", encoding="utf-8")
''', encoding='utf-8')
        runtime = RUNTIMES['fake']._replace(command=[sys.executable, str(script), '{last_message}'])
        launcher = Launcher(Paths(config).runs / 'review', runtime, 'test')
        git = Mock()
        git.read.return_value = root / 'read-only source'
        git.read.return_value.mkdir()
        workers = ReviewWorkers(self.store, launcher, git, config, policy())
        self.addCleanup(workers.close)
        with patch.dict(os.environ, {'FARMBOT_CONFIG': str(root / 'wrong.json'), 'GH_TOKEN': 'offline-canary'}):
            self.assertTrue(workers.launch(snapshot, 'review', {}))
        self.assertEqual(workers.busy(), 1)
        deadline, finished = time.monotonic() + 15, []
        while time.monotonic() < deadline and not finished:
            finished = workers.reap()
            time.sleep(.03)
        logs = '\n'.join(p.read_text(encoding='utf-8') for p in Paths(config).runs.rglob('stderr.log'))
        self.assertEqual(len(finished), 1, logs)
        self.assertEqual(json.loads(finished[0]['result'])['snapshot'], snapshot['id'])
        self.assertEqual(workers.busy(), 0)
        self.assertEqual(launcher.running(), {})
        token = (launcher.state_dir(finished[0]['id']) / 'token').read_text(encoding='utf-8')
        logs += '\n'.join(p.read_text(encoding='utf-8') for p in Paths(config).runs.rglob('stdout.log'))
        self.assertNotIn(token, logs)
