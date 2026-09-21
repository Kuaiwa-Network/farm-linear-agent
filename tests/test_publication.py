import copy
import json
import tempfile
import unittest
from pathlib import Path

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

    def test_verified_private_destination_identifies_exact_repo_branch_and_head(self):
        result = self.verify()
        self.assertEqual(result['repository'], 'Kuaiwa-Network/farmgui')
        self.assertEqual(result['url'], 'https://github.com/Kuaiwa-Network/farmgui')
        self.assertEqual(result['branch'], self.branch)
        self.assertEqual(result['head'], self.trees.head(self.path))
        self.assertEqual(result['status'], 'verified')

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

    def test_publish_names_origin_so_its_expanded_url_is_not_rewritten_again(self):
        git('remote', 'set-url', 'origin', 'https://alias.example/farmgui.git', cwd=self.path)
        git('config', 'url.https://github.com/Kuaiwa-Network/.pushInsteadOf', 'https://alias.example/', cwd=self.path)
        git('config', 'url.https://github.com/stranger/.pushInsteadOf', 'https://github.com/Kuaiwa-Network/', cwd=self.path)
        result = self.verify()
        self.assertEqual(result['push_remote'], 'origin')
        self.assertEqual(result['push_url'], self.remote)

    def test_successor_scope_uses_its_actual_suffixed_issue_branch(self):
        # Retained predecessor branches cause Worktrees.add to choose an issue-specific suffix.
        git('remote', 'set-url', 'origin', str(self.origin), cwd=self.path)
        successor = self.trees.add('farmgui', 'successor', self.branch)
        git('remote', 'set-url', 'origin', self.remote, cwd=self.path)
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
