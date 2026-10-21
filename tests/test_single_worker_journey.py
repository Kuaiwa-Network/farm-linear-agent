"""Real launcher/CLI/Git journeys; local origins and file-backed Linear and GitHub, no live services."""
import json
import os
import unittest
from unittest.mock import patch

import test_feature_journey as fixture
from agent.execution import CONTRACT


class SingleWorkerJourneyTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.CodeJobJourneyTests()
        self.f.enabled_skills = ['chat', 'work']
        self.f.replay_legacy = False
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        # Fetches still reach the local origins; the effective push URL stays the configured GitHub one, which
        # delivery verification compares. Nothing in these journeys pushes, and no credential helper is configured.
        self.enterContext(patch.dict(os.environ, {
            'GIT_CONFIG_COUNT': '2', 'GIT_CONFIG_KEY_1': f'url.{fixture.ORG}.pushInsteadOf',
            'GIT_CONFIG_VALUE_1': fixture.ORG}))

    def github(self, changes, branch):
        """The file-backed GitHub a delivered finish verifies: each changed repository's draft PR 123 at the
        worktree HEAD when the step runs."""
        answers = {}
        for repo in changes:
            name = 'example-org/' + repo
            metadata = {'full_name': name, 'private': True, 'owner': {'login': 'example-org'},
                        'permissions': {'push': True}, 'html_url': fixture.ORG + repo, 'default_branch': 'main'}
            answers['repos/' + name] = metadata
            answers['repos/' + name + '/pulls/123'] = {
                'html_url': fixture.ORG + repo + '/pull/123', 'state': 'open', 'draft': True,
                'head': {'ref': branch, 'sha': '{rev:' + repo + ':HEAD}', 'repo': metadata},
                'base': {'ref': 'main', 'repo': metadata}}
        return ['file', str(self.f.stub / 'github.json'), json.dumps(answers)]

    def journey(self, title, changes, test_code):
        f = self.f
        f.refresh_issue(title=title, labels=['Bug'] if 'fix' in title else ['Feature'], label_groups=[])
        key = f.delegate()
        self.assertEqual(f.c.ledger.item(key)['execution_contract'], CONTRACT)
        state = f.c.paths.runs / key
        reports = state / 'candidate-report.txt'
        checkpoint = state / 'checkpoint.json'
        evidence = state / 'delivery.json'
        body = state / 'delivery.txt'
        prs = [fixture.ORG + repo + '/pull/123' for repo in changes]
        unknown = state / 'unknown-pr.json'
        refused = state / 'unknown-pr-refused.txt'
        identifier = f.c.ledger.issue(f.c.ledger.item(key)['issue_id'])['identifier']
        steps = [['claim', '--item', '{item}', '--worker-id', 'one-owner']]
        for repo, source in changes.items():
            path = f.c.paths.worktrees / key / repo / 'behavior.py'
            steps += [['file', str(path), source], ['git', repo, 'add', 'behavior.py'],
                      ['git', repo, 'commit', '-qm', title]]
        # Execute a real candidate test in the same contained worker. The
        # controller never schedules an invented baseline or a closing worker.
        program = '\n'.join(['from pathlib import Path', 'import runpy',
            'roots = ' + repr({repo: str(f.c.paths.worktrees / key / repo) for repo in changes}),
            test_code, f'Path({str(reports)!r}).write_text("candidate tests passed", encoding="utf-8")'])
        steps += [['python', '-c', program],
            ['file', str(checkpoint), json.dumps({'summary': title, 'stage': 'delivery',
                'reports': [str(reports)], 'published_prs': prs, 'handoff': 'Candidate tested; deliver drafts'})],
            ['checkpoint', '--item', '{item}', '--token', '{token}', '--input', str(checkpoint)],
            ['file', str(body), title + '\n' + '\n'.join(prs)],
            ['prepare-comment', '--item', '{item}', '--token', '{token}', '--kind', 'delivery', '--body-file', str(body)],
            ['post-comment', '--item', '{item}', '--token', '{token}', '--action-id', '{action_id}'],
            self.github(changes, 'farmbot/' + identifier.lower()),
            # A PR number GitHub does not know is refused before the card can move to review.
            ['file', str(unknown), json.dumps({'summary': title, 'verification': str(reports),
                'prs': [*prs, fixture.ORG + 'Farm-Client/pull/124'], 'comment_action_id': '{action_id}'})],
            ['refused', str(refused), 'finish', '--item', '{item}', '--token', '{token}', '--outcome', 'delivered',
             '--input', str(unknown)],
            ['file', str(evidence), json.dumps({'summary': title, 'verification': str(reports), 'prs': prs,
                                              'comment_action_id': '{action_id}'})],
            ['finish', '--item', '{item}', '--token', '{token}', '--outcome', 'delivered', '--input', str(evidence)]]
        run, payload = f.launch(key, steps, title)
        f.settle(key, run, title)
        self.assertIn('GitHub has no pull request at ' + fixture.ORG + 'Farm-Client/pull/124',
                      refused.read_text(encoding='utf-8'))
        calls = [json.loads(line)['endpoint'] for line in
                 (f.stub / 'github-calls.jsonl').read_text(encoding='utf-8').splitlines()]
        for repo in changes:
            self.assertIn(f'repos/example-org/{repo}/pulls/123', calls)
        self.assertEqual(f.c.ledger.item(key)['state'], 'delivered')
        self.assertEqual(reports.read_text(encoding='utf-8'), 'candidate tests passed')
        self.assertEqual(len(f.runs(key)), 1)
        self.assertEqual(f.c.ledger.reservations(), [])
        self.assertEqual(len(payload['stage']['write_repositories']), 5)
        self.assertEqual(f.c.ledger.connection.execute('SELECT count(*) FROM work_items WHERE parent_id=?', (key,)).fetchone()[0], 0)
        self.assertIsNone(f.c.ledger.connection.execute("SELECT 1 FROM audit WHERE item_id=? AND kind='repository_handoff'", (key,)).fetchone())

    def test_bugfix_delivers_in_one_worker(self):
        self.journey('fix daily remaining count', {'Farm-Client': 'def remaining(used, limit):\n    return max(0, limit - used)\n'},
            "m = runpy.run_path(str(Path(roots['Farm-Client']) / 'behavior.py'))\n"
            "assert m['remaining'](2, 5) == 3\nassert m['remaining'](8, 5) == 0")

    def test_feature_implements_related_repositories_in_one_worker(self):
        self.journey('add capacity display', {
            'common': "UNIT = '次'\n",
            'farm-hive': 'def capacity():\n    return {"used": 2, "limit": 5}\n',
            'Farm-Client': 'def display(value, unit):\n    return str(value["limit"] - value["used"]) + unit\n'},
            "m = {k: runpy.run_path(str(Path(v) / 'behavior.py')) for k,v in roots.items()}\n"
            "assert m['Farm-Client']['display'](m['farm-hive']['capacity'](), m['common']['UNIT']) == '3次'")
