"""Published PR, independent child verdict, owner repair, Unity evidence and exact controller merge."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from agent import owner_review_evidence, publication
from agent.__main__ import parser, run
from agent.config import Config, Paths
from agent.lifecycle import Lifecycle
from agent.review_controller import Controller
from agent.review_policy import ReviewError
from test_ledger import issue
from test_review import APP, BASE, HEAD, ReviewFixture, policy
from test_review_controller import Git, GitHub, Workers


class OwnerReviewJourney(ReviewFixture):
    def setUp(self):
        super().setUp()
        repository = 'Farm-Client'
        self.policy = policy(mode='merge', repositories={repository: {
            'backend': 'git_ff', 'method': 'merge', 'aot': ['aot/*'],
            'checks': [{'name': 'tests', 'app_id': 1}]}})
        self.config = Config('offline', 'offline', 'offline', local_root=self.path.parent, auto_merge=self.policy, instance_id='test',
                             repos={repository: 'https://github.com/example/Farm-Client.git'})
        self.github = GitHub()
        self.github.bases[repository] = BASE
        self.git = Git(self.github)
        self.git.read = lambda s: Path('readonly') / s['commit']
        self.workers = Workers(self.store)
        self.workers.author_launcher = Mock(spec=['assert_quiescent'])
        self.url = 'https://github.com/example/Farm-Client/pull/42'
        self.worktree = {'repository': 'example/Farm-Client', 'url': 'https://github.com/example/Farm-Client',
                         'branch': 'farmbot/farm-42', 'base_branch': 'main', 'head': HEAD}
        data = issue(identifier='FARM-42', delegate_id=APP)
        self.issue_id = data['id']
        self.api = SimpleNamespace(app_user_id=APP, fetch_issue=self.ledger.issue,
                                  issue_status=lambda _: {**data, 'updated_at': '2026-11-24T00:00:00Z'},
                                  set_issue_workflow_state=Mock(return_value=True), awaiting_review=Mock(return_value=True),
                                  create_comment=Mock(return_value='review-notice'), create_activity=Mock())
        self.controller = Controller(self.ledger, self.config, self.api, self.github, self.git, self.workers)
        self.github.heads[self.url] = HEAD
        self.published_target = 'main'
        self.ledger.observe_issue(data)
        self.ledger.ensure_session('session', data['id'], delegation=True)
        self.item = self.ledger.create_work_item(issue_id=data['id'], session_id='session', skill='work')['id']
        self.token = self.ledger.claim(self.item, worker_id='original')['token']
        self.ledger.checkpoint(self.item, self.token, {'summary': 'PR opened'})
        self.enterContext(patch('agent.__main__.load_config', return_value=self.config))
        self.enterContext(patch.object(publication.PublicationVerifier, 'verify',
                                      side_effect=lambda *args: self.worktree.copy()))
        self.enterContext(patch.object(publication, 'github_reader', return_value=self.publication_pr))
        self.parent = str(uuid4())
        self.home = Paths(self.config).runs / self.item / '20261118T121212Z-0123456789ab' / 'home'
        self.home.mkdir(parents=True)
        owner_review_evidence.prepare(self.home, 'codex')
        self.sessions = self.home / 'sessions/2026/11/18'
        self.sessions.mkdir(parents=True)
        self.enterContext(patch.dict(os.environ, {'CODEX_HOME': str(self.home), 'CODEX_THREAD_ID': self.parent}))

    def publication_pr(self, endpoint, *, missing_ok=False):
        self.assertEqual(endpoint, 'repos/example/Farm-Client/pulls/42')
        repository = {'full_name': 'example/Farm-Client'}
        return {'html_url': self.url, 'state': 'open', 'draft': True,
                'head': {'ref': self.worktree['branch'], 'sha': self.github.heads[self.url], 'repo': repository},
                'base': {'ref': self.published_target, 'sha': self.github.bases['Farm-Client'], 'repo': repository}}

    def cli(self, *args):
        argv = ['--db', str(Paths(self.config).ledger), 'owner-review', '--item', self.item, '--token', self.token, *args]
        return run(parser().parse_args(argv), self.ledger, None)

    def submit(self, packet, reviewer=None, **changes):
        reviewer = reviewer or str(uuid4())
        report = {'ticket': packet['ticket'], 'verdict': self.verdict(packet['snapshot'], **changes)}
        transcript = self.sessions / ('rollout-1-' + reviewer + '.jsonl')
        transcript.write_text(json.dumps({'payload': {'id': reviewer, 'source': {'subagent': {'thread_spawn': {
            'parent_thread_id': self.parent, 'depth': 1}}}}}) + '\n' + json.dumps({
            'type': 'event_msg', 'payload': {'type': 'task_complete', 'last_agent_message': json.dumps(report)}}), encoding='utf-8')
        self.report_path = self.home.parent / 'report.json'
        self.report_path.write_text(json.dumps({**report, 'reviewer': reviewer}), encoding='utf-8')
        return self.cli('--report', str(self.report_path))

    def finish(self, *, check=True, outcome='delivered'):
        evidence = {'summary': 'done', 'verification': 'verified', 'prs': [self.url]}
        if not check:
            kind = 'delivery' if outcome == 'delivered' else 'blocker'
            comment = self.ledger.prepare_comment(self.item, self.token, kind, 'Result for ' + self.url)
            self.ledger.confirm_comment(comment['action_id'], 'published')
            evidence['comment_action_id'] = comment['action_id']
        payload = self.path.parent / 'finish.json'
        payload.write_text(json.dumps(evidence), encoding='utf-8')
        argv = ['--db', str(Paths(self.config).ledger), 'finish', '--item', self.item, '--token', self.token,
                '--outcome', outcome, '--input', str(payload)]
        if check:
            argv.append('--check-ready')
        return run(parser().parse_args(argv), self.ledger, lambda: self.api)

    def test_original_claim_repairs_tests_and_requests_fresh_review_before_merge(self):
        with self.assertRaisesRegex(ReviewError, 'start owner-review'):
            self.finish()
        self.cli('--pr', self.url)
        self.controller.tick()
        first, = self.cli()['tasks']
        finding = dict(id='recovery', severity='important', path='Guild.cs', line=10, claim='missed effect',
                       evidence='recovery clears pending', verification='independent checks')
        initial_reviewer = str(uuid4())
        self.submit(first, initial_reviewer, findings=[finding])
        self.controller.tick()
        task, = self.cli()['tasks']
        self.assertEqual(task['action'], 'repair')
        self.assertEqual(task['verdict']['findings'], [finding])
        self.github.bases['Farm-Client'] = 'e' * 40
        self.controller.tick()  # base movement during Unity work must not discard the repair
        self.assertEqual(self.cli()['tasks'][0]['action'], 'repair')
        self.github.heads[self.url] = self.worktree['head'] = 'f' * 40
        self.controller.tick()  # push is visible before the owner re-registers
        with self.assertRaisesRegex(ReviewError, 'Unity slot'):
            self.cli('--pr', self.url)
        self.store.db.execute("INSERT INTO reservations(reservation_id,item_id,generation,kind,mode,commit_sha,state,"
                              "created_at,release_reason) VALUES(?,?,0,'unity_slot','interactive',?,'released',1,"
                              "'worker reported quiescent')", ('slot-test', self.item, self.worktree['head']))
        with self.assertRaisesRegex(ReviewError, 'Unity slot'):
            self.cli('--pr', self.url)
        self.ledger.note(self.item, 'unity_test_passed',
                         details={'reservation': 'slot-test', 'commit': self.worktree['head']})
        self.cli('--pr', self.url)
        self.controller.tick()
        second, = self.cli()['tasks']
        self.assertNotEqual(first['ticket'], second['ticket'])
        self.assertEqual(second['snapshot']['open_findings'], [finding])
        with self.assertRaisesRegex(ReviewError, 'stale'):
            self.cli('--report', str(self.report_path))
        responses = [dict(id='recovery', status='fixed', evidence='new result dispatches once')]
        with self.assertRaisesRegex(ReviewError, 'fresh native subagent'):
            self.submit(second, initial_reviewer, responses=responses)
        self.submit(second, responses=responses)
        self.github.states[second['snapshot']['commit']] = 'passed'
        self.controller.tick()
        self.assertEqual(self.cli()['state'], 'ready')
        self.assertFalse(self.github.merged)
        self.assertEqual(self.workers.launched, [])
        self.finish(check=False)
        self.workers.author_launcher.assert_quiescent.side_effect = RuntimeError('author still exiting')
        self.controller.tick()
        self.assertFalse(self.github.merged)
        self.workers.author_launcher.assert_quiescent.side_effect = None
        self.controller.tick()
        self.assertEqual(self.github.merged, {self.url})
        self.assertEqual(self.workers.launched, [])

    def test_transcript_target_cannot_share_a_worker_write_root(self):
        home = self.home.parent.parent / '20261118T131313Z-0123456789ab' / 'home'
        home.mkdir(parents=True)
        target = owner_review_evidence.evidence_root(home.parent.parent)
        with self.assertRaisesRegex(ReviewError, 'outside worker write roots'):
            owner_review_evidence.prepare(home, 'codex', [target])
        self.assertFalse((target / home.parent.name).exists())

    def test_reported_handoff_can_be_polled_and_finished(self):
        self.cli('--pr', self.url)
        self.cli('--handoff', 'Independent review unavailable')
        self.controller.tick()
        self.assertEqual(self.cli()['state'], 'human_handoff')
        self.assertEqual(self.cli('--pr', self.url), self.cli())
        self.finish(check=False)
        self.controller.tick()
        self.assertFalse(self.github.merged)

    def test_farm_2539_changed_pr_head_can_finish_human_handoff_and_move_to_in_review(self):
        self.ledger.set_worker(self.item, 1234, 'test')
        lifecycle = Lifecycle(self.ledger, self.api, SimpleNamespace(), clock=lambda: self.now, review_labels=True)
        self.assertTrue(lifecycle.tick()['ok'])
        self.cli('--pr', self.url)
        self.controller.tick()
        self.assertEqual(self.cli()['tasks'][0]['action'], 'review')

        # The owner pushed before the queue requested a repair. The enrolled SHA is stale.
        latest = 'f' * 40
        self.github.heads[self.url] = self.worktree['head'] = latest
        with self.assertRaisesRegex(ReviewError, 'delivery PRs differ'):
            self.finish()
        self.controller.tick()
        self.assertEqual(self.cli()['state'], 'human_handoff')
        self.assertTrue(self.finish()['local_completion_checked'])
        self.api.set_issue_workflow_state.assert_called_once()
        self.api.create_comment.assert_not_called()

        # Actual finish checks GitHub again, including changes made after preflight.
        self.github.heads[self.url] = HEAD
        with self.assertRaises(publication.PublicationError):
            self.finish(check=False)
        self.assertEqual(self.ledger.item(self.item)['state'], 'running')
        self.api.set_issue_workflow_state.assert_called_once()

        self.github.heads[self.url] = latest
        self.assertEqual(self.finish(check=False)['state'], 'delivered')
        self.assertTrue(lifecycle.tick()['ok'])
        self.assertEqual([call.args for call in self.api.set_issue_workflow_state.call_args_list],
                         [(self.issue_id, 'In Progress'), (self.issue_id, 'In Review')])
        self.assertEqual(self.store.db.execute('SELECT synced FROM issue_workflow_updates').fetchone()[0], 1)
        self.assertEqual(self.api.awaiting_review.call_args.args, (self.issue_id, True))
        delivery, = self.store.db.execute('SELECT inputs FROM review_deliveries')
        self.assertEqual(json.loads(delivery['inputs'])[0]['head'], latest)
        self.assertEqual(self.store.db.execute('SELECT source_head FROM review_prs').fetchone()[0], HEAD)
        self.controller.tick()
        self.assertIn('已交付：交由人工审查', self.api.create_comment.call_args.args[1])
        self.assertFalse(self.github.merged)

    def test_handoff_cannot_deliver_retargeted_pr(self):
        self.ledger.set_worker(self.item, 1234, 'test')
        self.cli('--pr', self.url)
        self.cli('--handoff', 'Independent review unavailable')
        self.controller.tick()
        self.published_target = 'v1.2.3'
        with self.assertRaises(publication.PublicationError):
            self.finish()
        self.worktree['base_branch'] = self.published_target
        with self.assertRaisesRegex(ReviewError, 'delivery PR destinations differ'):
            self.finish()
        self.assertEqual(self.finish(check=False, outcome='blocked')['state'], 'blocked')
        self.controller.tick()
        self.assertIn('交付受阻', self.api.create_comment.call_args.args[1])
        self.assertEqual(self.store.db.execute('SELECT target FROM issue_workflow_updates').fetchone()[0], 'In Progress')

    def test_unobserved_or_edited_child_verdict_cannot_pass(self):
        self.cli('--pr', self.url)
        self.controller.tick()
        task, = self.cli()['tasks']
        self.submit(task)
        report = json.loads(self.report_path.read_text(encoding='utf-8'))
        report['verdict']['summary'] = 'rewritten by parent'
        with self.assertRaisesRegex(ReviewError, 'no matching'):
            owner_review_evidence.verify_native_report(self.home.parent.parent, report, 'codex')

    def test_claude_child_transcript_and_original_capture_location(self):
        home = self.home.parent.parent / '20261118T131313Z-0123456789ab' / 'home'
        home.mkdir(parents=True)
        owner_review_evidence.prepare(home, 'claude')
        reviewer = 'aabb0022'
        folder = home / 'projects/project' / self.parent / 'subagents'
        folder.mkdir(parents=True)
        payload = {'ticket': 'ticket', 'verdict': {'summary': 'observed'}}
        (folder / ('agent-' + reviewer + '.jsonl')).write_text(json.dumps({
            'type': 'assistant', 'isSidechain': True, 'agentId': reviewer, 'message': {
                'content': [{'type': 'text', 'text': json.dumps(payload)}]}}), encoding='utf-8')
        with patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(home), 'CLAUDE_CODE_SESSION_ID': self.parent}):
            owner_review_evidence.verify_native_report(home.parent.parent, {**payload, 'reviewer': reviewer}, 'claude')
            with self.assertRaises(ReviewError):
                owner_review_evidence.verify_native_report(home.parent.parent, {**payload, 'reviewer': '../other'}, 'claude')
