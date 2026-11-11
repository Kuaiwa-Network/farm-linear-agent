"""Real launcher/CLI/Git journeys; local origins and file-backed Linear and GitHub, no live services."""
import json
import os
import unittest
from pathlib import Path
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

    def github(self, changes, branch, base='main'):
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
                'base': {'ref': base, 'repo': metadata}}
        return ['file', str(self.f.stub / 'github.json'), json.dumps(answers)]

    def journey(self, title, changes, test_code, *, version=None):
        f = self.f
        f.refresh_issue(title=title, labels=[version] if version else ['Bug'] if 'fix' in title else ['Feature'],
                        label_groups=[{'group': 'Target version', 'label': version}] if version else [])
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
                'reports': [str(reports)], 'prs': prs, 'handoff': 'Candidate tested; deliver drafts'})],
            ['checkpoint', '--item', '{item}', '--token', '{token}', '--input', str(checkpoint)],
            ['file', str(body), title + '\n' + '\n'.join(prs)],
            ['prepare-comment', '--item', '{item}', '--token', '{token}', '--kind', 'delivery', '--body-file', str(body)],
            ['post-comment', '--item', '{item}', '--token', '{token}', '--action-id', '{action_id}'],
            self.github(changes, 'farmbot/' + identifier.lower(), version or 'main'),
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
        self.assertEqual(f.c.ledger.issue_context(key)['published_prs'], sorted(prs))
        return key

    def test_version_runs_on_selected_sources_and_delivers_exact_base_pr(self):
        from test_worktrees import git
        for repo in fixture.REPOS:
            origin = self.f.origins / (repo + '.git')
            git('branch', 'v1.0.0', cwd=origin)
            (origin / 'next-version.txt').write_text('main only', encoding='utf-8')
            git('add', '.', cwd=origin)
            git('commit', '-qm', 'main advances', cwd=origin)
        key = self.journey('fix version 1.0', {'Farm-Client': 'VALUE = 10\n', 'farm-hive': 'VALUE = 10\n'},
            "assert all(not (Path(root) / 'next-version.txt').exists() for root in roots.values())",
            version='v1.0.0')
        record = self.f.c.ledger.item(key)['branch_target']
        self.assertEqual(set(record['repositories']), set(fixture.REPOS))
        self.assertTrue(all(v['branch'] == 'v1.0.0' for v in record['repositories'].values()))
        self.f.tick_until(lambda: self.f.cleaned(key), 'version cleanup')
        self.f.restart()
        self.assertEqual(self.f.c.ledger.item(key)['branch_target'], record)

    def test_new_version_is_created_before_worker_and_used_as_pr_base(self):
        import agent.worktrees as module
        from test_worktrees import git
        version = 'v1.2.3'
        (self.f.stub / 'github.json').write_text(
            self.github(fixture.REPOS, 'farmbot/farm-1', version)[2], encoding='utf-8')
        self.f.restart()  # Production wiring selects the file-backed GitHub reader.
        original = module._git
        def transport(*args, **kwargs):
            if args[0] == 'push':
                self.assertEqual(args[-2], 'origin')
                local = self.f.origins / Path(kwargs['cwd']).name
                args = (*args[:-2], str(local), args[-1])
            return original(*args, **kwargs)
        with patch.object(module, '_git', side_effect=transport):
            key = self.journey('fix new patch version', {'Farm-Client': 'VALUE = 123\n'},
                               'assert len(roots) == 1', version=version)
        record = self.f.c.ledger.item(key)['branch_target']
        for repo, target in record['repositories'].items():
            self.assertEqual(target['branch'], version)
            self.assertEqual(target['commit'], git('rev-parse', version, cwd=self.f.origins / (repo + '.git')))

    def test_delivered_owner_resumes_original_branches_and_keeps_its_prs_after_cleanup(self):
        from agent.foreign_work import foreign_work
        from agent.publication import PublicationVerifier, github_reader
        from test_worktrees import git
        changes = {'Farm-Client': 'VALUE = 1\n', 'farmgui': 'VALUE = 1\n'}
        key = self.journey('fix honor wall', changes, 'assert len(roots) == 2')
        f, branch = self.f, 'farmbot/farm-1'
        f.tick_until(lambda: f.cleaned(key), 'delivered owner cleanup')
        saved = f.c.ledger.cleanup_record(key)['result']
        self.assertEqual(saved['branches'], dict.fromkeys(fixture.REPOS, branch))
        self.assertFalse((f.c.paths.worktrees / key).exists())
        # Restart while settled, before the reply queues another worker.
        f.restart()
        prs = [fixture.ORG + repo + '/pull/123' for repo in changes]
        f.refresh_issue(attachments=prs)
        f.reply('Resolve the pull request merge conflicts.')
        (chat,) = [row['id'] for row in f.c.ledger.items_for_session(fixture.SESSION) if row['skill'] == 'chat']
        message = f.context(chat)['session_messages'][-1]['id']
        summary = f.work / 'repair-summary.txt'
        summary.write_text('Update the existing two PRs.', encoding='utf-8')
        f.attempt(chat, [['claim', '--item', '{item}', '--worker-id', 'chat'],
            ['request-repair', '--item', '{item}', '--token', '{token}', '--message-id', str(message),
             '--summary-file', str(summary)]], 'request conflict repair')
        self.assertEqual(f.c.ledger.item(key)['generation'], 1)
        self.assertEqual(f.c.ledger.item(key)['state'], 'queued')
        state = f.c.paths.runs / key
        body, evidence = state / 'repair.txt', state / 'repair.json'
        scope_file = state / 'repair-scope.json'
        current = f.c.ledger.issue_context(key)['current_request']
        self.assertTrue(current['review_required'])
        self.assertIn('merge conflicts', current['latest_request']['body'])
        restored = state / 'restored.json'
        roots = {repo: str(f.c.paths.worktrees / key / repo) for repo in changes}
        program = ('import json, subprocess\nfrom pathlib import Path\nroots = ' + repr(roots) + '\n'
            'result = {repo: [subprocess.check_output(["git", "branch", "--show-current"], cwd=p, text=True).strip(), '
            'subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=p, text=True).strip()] for repo,p in roots.items()}\n'
            f'Path({str(restored)!r}).write_text(json.dumps(result), encoding="utf-8")')
        steps = [['claim', '--item', '{item}', '--worker-id', 'resumed-owner'],
            ['file', str(scope_file), json.dumps({'request_scope': {
                'through_message_id': current['latest_message_id'],
                'summary': 'Resolve existing PR conflicts and validate the changed candidates'}})],
            ['checkpoint', '--item', '{item}', '--token', '{token}', '--input', str(scope_file)],
            ['python', '-c', program]]
        for repo in changes:
            steps += [['file', str(Path(roots[repo]) / 'behavior.py'), 'VALUE = 2\n'],
                      ['git', repo, 'commit', '-qam', 'Repair existing PR']]
        steps += [self.github(changes, branch),
            ['file', str(body), 'Repaired existing PRs\n' + '\n'.join(prs)],
            ['prepare-comment', '--item', '{item}', '--token', '{token}', '--kind', 'delivery', '--body-file', str(body)],
            ['post-comment', '--item', '{item}', '--token', '{token}', '--action-id', '{action_id}'],
            ['file', str(evidence), json.dumps({'summary': 'Repaired', 'verification': 'Fixture candidates verified',
                'prs': prs, 'comment_action_id': '{action_id}'})],
            ['finish', '--item', '{item}', '--token', '{token}', '--outcome', 'delivered', '--input', str(evidence)]]
        f.attempt(key, steps, 'repair existing drafts')
        before = json.loads(restored.read_text(encoding='utf-8'))
        self.assertEqual(before, {repo: [branch, saved['committed'][repo]] for repo in changes})
        self.assertEqual(f.c.ledger.item(key)['state'], 'delivered')
        self.assertEqual(f.c.ledger.issue_context(key)['published_prs'], sorted(prs))
        verifier = PublicationVerifier(f.c.scheduler.worktrees, api=github_reader(f.config))
        for repo in changes:
            path = f.c.paths.worktrees / key / repo
            self.assertEqual(git('branch', '--show-current', cwd=path), branch)
            verified = verifier.verify(repo, key, 'FARM-1')
            self.assertEqual(verified['push_argv'][-1], 'HEAD:refs/heads/' + branch)
        with patch('agent.foreign_work.search_prs', return_value=[]), \
                patch('agent.foreign_work.remote_branches', return_value=[]):
            report = foreign_work(f.c.ledger, key, f.config.repos, f.c.paths.repos)
        self.assertEqual(report['own']['prs'], sorted(prs))
        self.assertEqual(report['foreign']['prs'], [])

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
