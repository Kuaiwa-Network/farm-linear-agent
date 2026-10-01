import contextlib
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote
from unittest.mock import patch

from agent import publication
from agent.worktrees import Worktrees
from test_worktrees import git


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        origin = root / 'origin'
        self.origin = origin
        origin.mkdir()
        git('init', '-q', '-b', 'main', '.', cwd=origin)
        (origin / 'source.txt').write_text('source')
        git('add', '.', cwd=origin)
        git('commit', '-qm', 'initial', cwd=origin)
        self.trees = Worktrees(root / 'repos', root / 'worktrees', {'farmgui': str(origin)})
        self.branch = 'farmbot/farm-1248-材料商店'
        self.path = self.trees.add('farmgui', 'job', self.branch)
        self.remote = 'https://github.com/Kuaiwa-Network/farmgui.git'
        self.trees.remotes['farmgui'] = self.remote
        git('remote', 'set-url', 'origin', self.remote, cwd=self.path)
        self.repo = {'full_name': 'Kuaiwa-Network/farmgui', 'private': True,
                     'owner': {'login': 'Kuaiwa-Network'}, 'permissions': {'push': True},
                     'html_url': 'https://github.com/Kuaiwa-Network/farmgui', 'default_branch': 'main'}
        self.remote_branch = None
        def api(endpoint, *, missing_ok=False):
            if '/branches/' in endpoint:
                return copy.deepcopy(self.remote_branch)
            return copy.deepcopy(self.repo)
        self.verifier = publication.PublicationVerifier(self.trees, api=api)

    def verify(self):
        return self.verifier.verify('farmgui', 'job', 'FARM-1248', self.branch)

    @contextlib.contextmanager
    def fetching_from_the_local_origin(self):
        """The configured remote is github.com, which these tests never reach. A host-level rewrite, as an operator's
        own git config could hold, sends fetches to the local origin; the clone's origin stays the configured remote,
        since FarmBot refuses a clone whose origin is another (plan P10)."""
        host = Path(self.tmp.name) / 'host.gitconfig'
        # Git drops a backslash in a quoted section name unless it is doubled, which a Windows path needs.
        base = str(self.origin).replace('\\', '\\\\').replace('"', '\\"')
        host.write_text(f'[url "{base}"]\n\tinsteadOf = {self.remote}\n', encoding='utf-8')
        with patch.dict(os.environ, {'GIT_CONFIG_GLOBAL': str(host)}):
            yield

    def test_test_workspace_issue_can_publish_only_its_own_feature_branch(self):
        verifier = publication.PublicationVerifier(self.trees, api=self.verifier.api, issue_prefix='FBTEST')
        branch = 'farmbot/fbtest-42-材料商店'
        git('branch', '-m', branch, cwd=self.path)
        result = verifier.verify('farmgui', 'job', 'FBTEST-42')
        self.assertEqual(result['branch'], branch)
        self.assertEqual(result['status'], 'verified')
        for identifier in ('FARM-42', 'FBTEST-420', '../FBTEST-42', 'FBTEST-42/other',
                           'FBTEST-.*', 'FBTEST-42\n', None):
            with self.subTest(identifier=identifier), self.assertRaises(publication.PublicationError):
                verifier.verify('farmgui', 'job', identifier)

    def test_scheduler_test_workspace_branch_passes_publication_policy(self):
        from types import SimpleNamespace
        from agent.scheduler import Scheduler
        scheduler = Scheduler(None, None, None, self.trees, skill_root=Path(self.tmp.name),
                              db_path=Path(self.tmp.name) / 'ledger', runtime_name='fake', host='test',
                              issue_prefix='FBTEST')
        verifier = publication.PublicationVerifier(self.trees, api=self.verifier.api, issue_prefix='FBTEST')
        for number, suggested, expected in (
                (42, 'alice/fbtest-42-fix', 'farmbot/fbtest-42'),
                (43, 'farmbot/fbtest-43-材料商店', 'farmbot/fbtest-43-材料商店'),
                (44, 'farmbot/fbtest-440', 'farmbot/fbtest-44')):
            with self.subTest(suggested=suggested):
                identifier, item_id = f'FBTEST-{number}', f'job-{number}'
                with self.fetching_from_the_local_origin():
                    paths = scheduler._worktrees_for(SimpleNamespace(writes=['farmgui'], initial_root=None),
                        {'id': item_id, 'publication_retries': 0},
                        {'identifier': identifier, 'branch_name': suggested})
                scope = verifier.scope(item={'id': item_id, 'skill': 'fix'},
                    issue={'identifier': identifier}, paths=paths, delegated=True)
                self.assertEqual(scope['repositories']['farmgui']['status'], 'verified')
                self.assertEqual(scope['repositories']['farmgui']['branch'], expected)

    def test_scheduler_rejects_wrong_or_malformed_keys_before_creating_worktrees(self):
        from types import SimpleNamespace
        from agent.scheduler import Scheduler
        scheduler = Scheduler(None, None, None, self.trees, skill_root=Path(self.tmp.name),
                              db_path=Path(self.tmp.name) / 'ledger', runtime_name='fake', host='test',
                              issue_prefix='FBTEST')
        for identifier in ('FARM-42', '../FBTEST-42', 'FBTEST-42/other', 'FBTEST-.*', None):
            with self.subTest(identifier=identifier), self.assertRaises(publication.PublicationError):
                scheduler._worktrees_for(SimpleNamespace(writes=['farmgui'], initial_root=None),
                    {'id': 'rejected', 'publication_retries': 0},
                    {'identifier': identifier, 'branch_name': 'farmbot/fbtest-42'})
        self.assertFalse((self.trees.worktrees_root / 'rejected').exists())

    def test_verified_private_destination_identifies_exact_repo_branch_and_head(self):
        result = self.verify()
        self.assertEqual(result['repository'], 'Kuaiwa-Network/farmgui')
        self.assertEqual(result['url'], 'https://github.com/Kuaiwa-Network/farmgui')
        self.assertEqual(result['branch'], self.branch)
        self.assertEqual(result['head'], self.trees.head(self.path))
        self.assertEqual(result['status'], 'verified')

    def test_branch_with_force_added_ignored_run_report_cannot_publish(self):
        (self.path / '.gitignore').write_text('/reports/\n', encoding='utf-8')
        git('add', '.gitignore', cwd=self.path)
        git('commit', '-qm', 'ignore run reports', cwd=self.path)
        report = self.path / 'reports' / 'run' / 'report.md'
        report.parent.mkdir(parents=True)
        report.write_text('run evidence\n', encoding='utf-8')
        git('add', '-f', 'reports/run/report.md', cwd=self.path)
        git('commit', '-qm', 'force add report', cwd=self.path)

        with self.assertRaisesRegex(publication.PublicationError, 'run report'):
            self.verify()

    def test_staged_run_report_change_cannot_publish(self):
        report = self.path / 'reports' / 'run' / 'report.md'
        report.parent.mkdir(parents=True)
        report.write_text('run evidence\n', encoding='utf-8')
        git('add', 'reports/run/report.md', cwd=self.path)

        with self.assertRaisesRegex(publication.PublicationError, 'run report'):
            self.verify()

    def test_late_pr_must_be_open_draft_for_the_exact_job_head_and_repository(self):
        url = 'https://github.com/Kuaiwa-Network/farmgui/pull/113'
        metadata = {'html_url': url, 'state': 'open', 'draft': True,
                    'head': {'ref': self.branch, 'sha': self.trees.head(self.path), 'repo': self.repo},
                    'base': {'ref': 'main', 'repo': self.repo}}
        api = self.verifier.api
        current = copy.deepcopy(metadata)
        self.verifier.api = lambda endpoint, **kwargs: (current if endpoint.endswith('/pulls/113')
                                                        else api(endpoint, **kwargs))
        self.assertEqual(self.verifier.verify_pr('farmgui', 'job', 'FARM-1248', url), url)
        for key, value in [('draft', False), ('state', 'closed'), ('html_url', url + '0'),
                           ('head', {**metadata['head'], 'sha': '0' * 40}),
                           ('head', {**metadata['head'], 'ref': 'farmbot/farm-1249'}),
                           ('head', {**metadata['head'], 'repo': {'full_name': 'other/farmgui'}}),
                           ('base', {**metadata['base'], 'repo': {'full_name': 'other/farmgui'}})]:
            with self.subTest(key=key, value=value):
                current = {**metadata, key: value}
                with self.assertRaises(publication.PublicationError):
                    self.verifier.verify_pr('farmgui', 'job', 'FARM-1248', url)

    def test_equivalent_ssh_remote_is_verified(self):
        git('remote', 'set-url', '--push', 'origin', 'git@github.com:Kuaiwa-Network/farmgui.git', cwd=self.path)
        self.assertEqual(self.verify()['status'], 'verified')

    def test_changed_multiple_and_rewritten_push_destinations_are_rejected(self):
        for setup in ('changed', 'multiple', 'rewrite'):
            with self.subTest(setup=setup):
                git('config', '--unset-all', 'remote.origin.pushurl', cwd=self.path, allow_failure=True)
                if setup == 'changed':
                    git('remote', 'set-url', '--push', 'origin', 'https://github.com/stranger/farmgui.git', cwd=self.path)
                elif setup == 'multiple':
                    git('remote', 'set-url', '--add', '--push', 'origin', self.remote, cwd=self.path)
                    git('remote', 'set-url', '--add', '--push', 'origin', 'https://github.com/stranger/farmgui.git', cwd=self.path)
                else:
                    git('config', 'url.https://github.com/stranger/.pushInsteadOf', 'https://github.com/Kuaiwa-Network/', cwd=self.path)
                with self.assertRaises(publication.PublicationError):
                    self.verify()

    def test_public_read_only_renamed_and_unverifiable_metadata_is_rejected(self):
        original = copy.deepcopy(self.repo)
        for changes in ({'private': False}, {'permissions': {'push': False}},
                        {'full_name': 'stranger/farmgui'}, {'owner': {'login': 'stranger'}},
                        {'html_url': 'https://github.com/stranger/farmgui'}, {'permissions': {}},
                        {'default_branch': self.branch}):
            with self.subTest(changes=changes):
                self.repo = {**original, **changes}
                with self.assertRaises(publication.PublicationError):
                    self.verify()

    def test_wrong_issue_branch_and_protected_branch_are_rejected(self):
        for branch in ('main', 'farmbot/farm-12480', 'farmbot/farm-1238'):
            with self.subTest(branch=branch), self.assertRaises(publication.PublicationError):
                self.verifier.verify('farmgui', 'job', 'FARM-1248', branch)
        self.remote_branch = {'name': self.branch, 'protected': True}
        with self.assertRaises(publication.PublicationError):
            self.verify()

    def test_worktree_from_a_different_clone_is_rejected(self):
        self.trees.repos_root = self.path.parent
        with self.assertRaises(publication.PublicationError):
            self.verify()

    def test_a_worktree_whose_pointer_names_another_worktrees_entry_is_rejected(self):
        """Plan P10: the controller runs this check at every launch, and a worker writes its worktree's `.git` file.
        Pointed at another worktree of this issue, the job's worktree would pass as that one."""
        with self.fetching_from_the_local_origin():
            other = self.trees.add('farmgui', 'other', 'farmbot/farm-1248-other')
        pointer = (other / '.git').read_text(encoding='utf-8')
        # Git for Windows hides .git, and Windows refuses to open a hidden file for overwrite: replace it instead.
        (self.path / '.git').unlink()
        (self.path / '.git').write_text(pointer, encoding='utf-8')
        with self.assertRaises(publication.PublicationError):
            self.verifier.verify('farmgui', 'job', 'FARM-1248')

    def test_publish_names_origin_so_its_expanded_url_is_not_rewritten_again(self):
        """The worker pushes to `origin`, never to the expanded URL, which git would rewrite again. Push rewrites
        belong to the host's own git config (plan P10): a clone whose origin is renamed away from the configured
        remote and rewritten back by the clone's config is refused."""
        ssh = 'git' + '@github.com:'
        host = Path(self.tmp.name) / 'host.gitconfig'
        host.write_text(f'[url "{ssh}"]\n\tpushInsteadOf = https://github.com/\n', encoding='utf-8')
        with patch.dict(os.environ, {'GIT_CONFIG_GLOBAL': str(host)}):
            result = self.verify()
        self.assertEqual((result['push_remote'], result['push_url']), ('origin', ssh + 'Kuaiwa-Network/farmgui.git'))
        git('remote', 'set-url', 'origin', 'https://alias.example/farmgui.git', cwd=self.path)
        git('config', 'url.https://github.com/Kuaiwa-Network/.pushInsteadOf', 'https://alias.example/', cwd=self.path)
        with self.assertRaises(publication.PublicationError):
            self.verify()

    def test_successor_scope_uses_its_actual_suffixed_issue_branch(self):
        # Retained predecessor branches cause Worktrees.add to choose an issue-specific suffix.
        with self.fetching_from_the_local_origin():
            successor = self.trees.add('farmgui', 'successor', self.branch)
        scope = self.verifier.scope(item={'id': 'successor', 'skill': 'fix'},
            issue={'identifier': 'FARM-1248'}, paths={'farmgui': successor}, delegated=True)
        self.assertEqual(scope['repositories']['farmgui']['status'], 'verified')
        self.assertEqual(scope['repositories']['farmgui']['branch'], self.branch + '-successor')

    def test_malformed_metadata_withholds_scope_without_failing_worker_launch(self):
        original = copy.deepcopy(self.repo)
        for changes in ({'full_name': None}, {'owner': []}, {'owner': {'login': 9}},
                        {'permissions': ['push']}, {'html_url': 1}, {'default_branch': {'name': 'main'}}):
            with self.subTest(changes=changes):
                self.repo = {**original, **changes}
                scope = self.verifier.scope(item={'id': 'job', 'skill': 'fix'}, issue={'identifier': 'FARM-1248'},
                                            paths={'farmgui': self.path}, delegated=True)
                self.assertEqual(scope['repositories']['farmgui']['status'], 'unverified')

    def test_read_only_and_undelegated_jobs_get_no_publishing_destinations(self):
        for delegated, skill in ((False, 'fix'), (True, 'chat')):
            scope = self.verifier.scope(item={'id': 'job', 'skill': skill}, issue={'identifier': 'FARM-1248'},
                                        paths={'farmgui': self.path}, delegated=delegated)
            self.assertEqual(scope['repositories'], {})

    def test_failed_verification_is_a_gap_not_an_invented_authorization(self):
        self.repo['private'] = False
        scope = self.verifier.scope(item={'id': 'job', 'skill': 'fix'}, issue={'identifier': 'FARM-1248'},
                                    paths={'farmgui': self.path}, delegated=True)
        self.assertEqual(scope['repositories']['farmgui']['status'], 'unverified')
        self.assertNotIn('url', scope['repositories']['farmgui'])


class SuffixBranchTests(unittest.TestCase):
    """The named suffix branches of a job with an initial root (spec §6.1, §6.4, §6.8, §11; P4), on local remotes.

    Each passes the issue-branch policy under today's rules. -config, the branch a human runs designer-source.pipeline
    on, also adds no commits: for such a job it verifies only at a commit already on another branch of origin."""

    ORG = 'https://github.com/Kuaiwa-Network/'

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='后缀 分支 ')
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.origins = {}
        for repo in ('common', 'Farm-Contract', 'farm-hive'):
            origin = root / 'origins' / repo
            origin.mkdir(parents=True)
            git('init', '-q', '-b', 'main', '.', cwd=origin)
            (origin / 'README.md').write_text(repo, encoding='utf-8')
            git('add', '.', cwd=origin)
            git('commit', '-qm', 'init', cwd=origin)
            self.origins[repo] = origin
        self.trees = Worktrees(root / 'repos', root / 'worktrees', {repo: str(path) for repo, path in self.origins.items()})
        self.paths = {repo: self.trees.add(repo, 'job', 'farmbot/farm-1') for repo in self.origins}
        host = root / 'host.gitconfig'
        rewrites = []
        for repo, path in self.paths.items():
            # P10: the clone's origin is the configured repository. A host-level rewrite keeps fetches local;
            # its HTTPS push URL stays GitHub, so publication checks the real destination spelling.
            remote = f'git@github.com:Kuaiwa-Network/{repo}.git'
            git('remote', 'set-url', 'origin', remote, cwd=path)
            git('remote', 'set-url', '--push', 'origin', f'{self.ORG}{repo}.git', cwd=path)
            self.trees.remotes[repo] = remote
            base = str(self.origins[repo]).replace('\\', '\\\\').replace('"', '\\"')
            rewrites.append(f'[url "{base}"]\n\tinsteadOf = {remote}\n')
        host.write_text(''.join(rewrites), encoding='utf-8')
        self.enterContext(patch.dict(os.environ, {'GIT_CONFIG_GLOBAL': str(host)}))
        self.github_branches = {}

        def api(endpoint, *, missing_ok=False):
            parts = endpoint.split('/')  # repos/Kuaiwa-Network/<repo>[/branches/<quoted branch>]
            if len(parts) > 3 and parts[3] == 'branches':
                return copy.deepcopy(self.github_branches.get(unquote(parts[4])))
            return {'full_name': f'Kuaiwa-Network/{parts[2]}', 'private': True, 'owner': {'login': 'Kuaiwa-Network'},
                    'permissions': {'push': True}, 'html_url': f'https://github.com/Kuaiwa-Network/{parts[2]}',
                    'default_branch': 'main'}
        self.verifier = publication.PublicationVerifier(self.trees, api=api)

    def commit(self, repo, name, text, message, *, cwd=None, author=None):
        path = cwd or self.paths[repo]
        (path / name).write_text(text, encoding='utf-8')
        git('add', name, cwd=path)
        git(*(('-c', f'user.name={author}') if author else ()), 'commit', '-qm', message, cwd=path)
        return git('rev-parse', 'HEAD', cwd=path)

    def push(self, repo, branch):
        """A worker's push, here to the local origin, then the fetch every launch makes."""
        git('push', '-q', str(self.origins[repo]), branch, cwd=self.paths[repo])
        self.trees.fetch(repo)

    def merge(self, repo, *, squash):
        """The owner merges the issue branch's PR and GitHub deletes the branch."""
        origin = self.origins[repo]
        if squash:
            git('merge', '-q', '--squash', 'farmbot/farm-1', cwd=origin)
            git('commit', '-qm', 'The issue branch (#2)', cwd=origin)
        else:
            git('merge', '-q', '--no-ff', '-m', 'Merge pull request #3 from farmbot/farm-1', 'farmbot/farm-1', cwd=origin)
        git('branch', '-q', '-D', 'farmbot/farm-1', cwd=origin)

    def verify(self, repo, *, suffix_roles=True):
        return self.verifier.verify(repo, 'job', 'FARM-1', git('branch', '--show-current', cwd=self.paths[repo]),
                                    suffix_roles=suffix_roles)

    def test_a_config_branch_at_a_commit_someone_else_pushed_verifies(self):
        self.commit('common', '_table.xml', '<declared/>', 'Declare the columns')
        self.push('common', 'farmbot/farm-1')
        origin = self.origins['common']
        git('switch', '-q', '-c', 'designer-one/farm-1-data', 'farmbot/farm-1', cwd=origin)
        data = self.commit('common', 'animal.xml', '<data/>', '策划填表', cwd=origin, author='Designer One')
        git('switch', '-q', 'main', cwd=origin)
        self.trees.fetch('common')
        git('switch', '-q', '-c', 'farmbot/farm-1-config', data, cwd=self.paths['common'])
        result = self.verify('common')
        self.assertEqual((result['status'], result['branch'], result['head']), ('verified', 'farmbot/farm-1-config', data))

    def test_a_config_branch_that_adds_a_commit_is_refused_for_a_job_with_an_initial_root(self):
        path = self.paths['common']
        git('switch', '-q', '-c', 'farmbot/farm-1-config', 'origin/main', cwd=path)
        own = self.commit('common', 'animal.xml', '<data/>', 'A value FarmBot must never publish')
        with self.assertRaisesRegex(publication.PublicationError, 'adds no commits'):
            self.verify('common')
        # Pushed once, the commit is on origin only under the branch's own name, which proves nothing.
        git('update-ref', 'refs/remotes/origin/farmbot/farm-1-config', own, cwd=path)
        with self.assertRaisesRegex(publication.PublicationError, 'adds no commits'):
            self.verify('common')
        # A fix never has the role, and its branch may carry any suffix Linear suggests: today's rules only.
        self.assertEqual(self.verify('common', suffix_roles=False)['head'], own)

    def test_a_repin_takes_the_next_numbered_config_branch_under_the_same_rule(self):
        """P12: a re-pin to a farm-common commit that does not descend from the pushed -config tip takes
        farmbot/<key>-config-<n>, n from 2, never a force push. No -config branch of the issue vouches for a commit."""
        origin = self.origins['common']
        git('switch', '-q', '-c', 'designer-one/farm-1-data', 'main', cwd=origin)
        first = self.commit('common', 'animal.xml', '<data/>', '策划填表', cwd=origin, author='Designer One')
        git('switch', '-q', '-c', 'designer-one/farm-1-redo', 'main', cwd=origin)
        second = self.commit('common', 'animal.xml', '<data v="2"/>', '策划重填', cwd=origin, author='Designer One')
        git('switch', '-q', 'main', cwd=origin)
        git('branch', '-q', 'farmbot/farm-1-config', first, cwd=origin)  # the first Jenkins branch, pushed earlier
        self.trees.fetch('common')
        path = self.paths['common']
        git('switch', '-q', '-c', 'farmbot/farm-1-config-2', second, cwd=path)
        result = self.verify('common')
        self.assertEqual((result['status'], result['branch'], result['head']),
                         ('verified', 'farmbot/farm-1-config-2', second))
        own = self.commit('common', 'animal.xml', '<data v="3"/>', 'A value FarmBot must never publish')
        with self.assertRaisesRegex(publication.PublicationError, 'adds no commits'):
            self.verify('common')
        # On origin only under this issue's -config branches, the first one or another re-pin, a commit proves nothing.
        for ref in ('farmbot/farm-1-config', 'farmbot/farm-1-config-3'):
            with self.subTest(ref=ref):
                git('update-ref', f'refs/remotes/origin/{ref}', own, cwd=path)
                with self.assertRaisesRegex(publication.PublicationError, 'adds no commits'):
                    self.verify('common')
                git('update-ref', '-d', f'refs/remotes/origin/{ref}', cwd=path)
        self.assertEqual(self.verify('common', suffix_roles=False)['head'], own)  # a fix: today's rules only

    def test_suffix_of_names_the_reserved_suffixes_and_numbers_repins_from_two(self):
        self.assertEqual(publication.SUFFIXES, ('-config', '-config-<n>', '-waivers', '-followup'))
        for branch, suffix in (('farmbot/farm-1-config', '-config'), ('farmbot/farm-1-config-2', '-config-<n>'),
                               ('farmbot/farm-1-config-10', '-config-<n>'), ('farmbot/farm-1-waivers', '-waivers'),
                               ('farmbot/farm-1-followup', '-followup'), ('farmbot/farm-1', None),
                               ('farmbot/farm-1-config-1', None), ('farmbot/farm-1-config-02', None),
                               ('farmbot/farm-1-config-<n>', None), ('farmbot/farm-1-config-2-data', None),
                               ('farmbot/farm-1-configs', None), ('farmbot/farm-1-config-٢', None),
                               ('farmbot/farm-1-config-2x', None),
                               ('farmbot/farm-12-config', None), ('designer-one/farm-1-config', None), (None, None)):
            with self.subTest(branch=branch):
                self.assertEqual(publication.suffix_of(branch, 'farmbot/farm-1'), suffix)

    def test_a_config_branch_verifies_after_the_declarations_pr_merged_and_its_branch_was_deleted(self):
        self.commit('common', '_table.xml', '<declared/>', 'Declare the columns')
        self.push('common', 'farmbot/farm-1')
        self.merge('common', squash=False)
        data = self.commit('common', 'animal.xml', '<data/>', '策划填表', cwd=self.origins['common'], author='Designer One')
        self.trees.fetch('common')  # --prune: the merged branch is gone from origin
        path = self.paths['common']
        self.assertNotIn('origin/farmbot/farm-1', git('branch', '-r', cwd=path).split())
        git('switch', '-q', '-c', 'farmbot/farm-1-config', 'origin/main', cwd=path)
        self.assertEqual(self.verify('common')['head'], data)

    def test_waivers_and_followup_branches_publish_from_main_after_the_issue_branch_merged(self):
        for repo, suffix, squash in (('Farm-Contract', 'waivers', True), ('farm-hive', 'followup', False)):
            with self.subTest(repo=repo):
                self.commit(repo, 'change.txt', 'feature', 'The issue branch')
                self.push(repo, 'farmbot/farm-1')
                self.merge(repo, squash=squash)
                self.trees.fetch(repo)
                path, branch = self.paths[repo], f'farmbot/farm-1-{suffix}'
                git('switch', '-q', '-c', branch, 'origin/main', cwd=path)
                head = self.commit(repo, f'{suffix}.txt', suffix, f'The {suffix} change')
                result = self.verify(repo)
                self.assertEqual((result['status'], result['branch'], result['head']), ('verified', branch, head))
                self.github_branches[branch] = {'name': branch, 'protected': False}  # after its first push
                self.assertEqual(self.verify(repo)['status'], 'verified')
                self.github_branches[branch] = {'name': branch, 'protected': True}
                with self.assertRaisesRegex(publication.PublicationError, 'protected'):
                    self.verify(repo)

    def test_a_suffix_prs_late_registration_needs_its_branch_checked_out(self):
        path, branch = self.paths['Farm-Contract'], 'farmbot/farm-1-waivers'
        git('switch', '-q', '-c', branch, 'origin/main', cwd=path)
        head = self.commit('Farm-Contract', 'BREAKING_WAIVERS', '', 'Remove the stale waivers')
        url = 'https://github.com/Kuaiwa-Network/Farm-Contract/pull/31'
        repo = {'full_name': 'Kuaiwa-Network/Farm-Contract'}
        pr = {'html_url': url, 'state': 'open', 'draft': True, 'head': {'ref': branch, 'sha': head, 'repo': repo},
              'base': {'ref': 'main', 'repo': repo}}
        api = self.verifier.api
        self.verifier.api = lambda endpoint, **kwargs: pr if endpoint.endswith('/pulls/31') else api(endpoint, **kwargs)
        self.assertEqual(self.verifier.verify_pr('Farm-Contract', 'job', 'FARM-1', url), url)
        git('switch', '-q', 'farmbot/farm-1', cwd=path)
        with self.assertRaisesRegex(publication.PublicationError, 'exact repository, branch and HEAD'):
            self.verifier.verify_pr('Farm-Contract', 'job', 'FARM-1', url)

    def test_only_this_issues_suffixes_pass_the_policy(self):
        path = self.paths['common']
        for branch in ('farmbot/farm-12-config', 'farmbot/farm-1config', 'designer-one/farm-1-config'):
            with self.subTest(branch=branch):
                git('switch', '-q', '-c', branch, 'origin/main', cwd=path)
                with self.assertRaisesRegex(publication.PublicationError, "issue's FarmBot feature branch"):
                    self.verify('common')
