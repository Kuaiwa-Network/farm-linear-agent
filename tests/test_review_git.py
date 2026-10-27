"""Real local-Git tests for immutable integration and race-safe exact promotion."""
import tempfile
import unittest
from pathlib import Path

from agent.review_git import CandidateGit
from agent.review_policy import digest
from agent.worktrees import Worktrees, WorktreeError
from test_worktrees import git


class CandidateGitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='review Git 空间 ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.remote, self.author = self.root / 'remote.git', self.root / 'author'
        self.remote.mkdir()
        git('init', '--bare', '-q', '-b', 'main', '.', cwd=self.remote)
        self.author.mkdir()
        git('init', '-q', '-b', 'main', '.', cwd=self.author)
        (self.author / 'feature.txt').write_text('base\n', encoding='utf-8')
        git('add', '.', cwd=self.author)
        git('commit', '-qm', 'base', cwd=self.author)
        git('remote', 'add', 'origin', str(self.remote), cwd=self.author)
        git('push', '-q', 'origin', 'main', cwd=self.author)
        self.base = git('rev-parse', 'HEAD', cwd=self.author)
        self.trees = Worktrees(self.root / 'owned clones', self.root / 'worktrees', {'common': str(self.remote)})
        self.git = CandidateGit(self.trees)

    def source(self, number, name, content):
        branch = 'farmbot/farm-' + str(number)
        git('checkout', '-qb', branch, self.base, cwd=self.author)
        (self.author / name).write_text(content, encoding='utf-8')
        git('add', '.', cwd=self.author)
        git('commit', '-qm', 'change ' + str(number), cwd=self.author)
        head = git('rev-parse', 'HEAD', cwd=self.author)
        git('push', '-q', 'origin', branch, cwd=self.author)
        return {'repository': 'common', 'target': 'main', 'source_head': head, 'source_branch': branch,
                'url': f'https://github.com/example/common/pull/{number}'}

    def prepare(self, pr, predecessor=None):
        snapshot = self.git.prepare(pr, {'base': {'sha': self.base}, 'head': {'sha': pr['source_head']}}, predecessor)
        snapshot['id'] = digest(snapshot)
        return snapshot

    def test_speculative_chain_preserves_exact_reviewed_commits_and_all_sources(self):
        a, b, c = [self.source(n, f'file {n}.txt', str(n)) for n in (1, 2, 3)]
        sa, sb, sc = self.prepare(a), None, None
        sb = self.prepare(b, sa)
        sc = self.prepare(c, sb)
        self.assertEqual(sa, self.prepare(a))
        self.git.publish_candidate(sc)
        self.assertEqual(git('rev-parse', 'main', cwd=self.remote), self.base)
        for snapshot, previous in ((sa, self.base), (sb, sa['commit']), (sc, sb['commit'])):
            self.git.merge_exact(snapshot, previous)
            self.assertEqual(git('rev-parse', 'main', cwd=self.remote), snapshot['commit'])
            self.assertTrue(self.git.ancestor('common', snapshot['head'], snapshot['commit']))
        read = self.git.read(sc)
        self.assertTrue(all((read / f'file {n}.txt').exists() for n in (1, 2, 3)))

    def test_external_main_race_rejects_normal_push_instead_of_overwriting_or_merging_untested_content(self):
        pr = self.source(1, 'new.txt', 'feature')
        candidate = self.prepare(pr)
        git('checkout', 'main', cwd=self.author)
        (self.author / 'external.txt').write_text('human update', encoding='utf-8')
        git('add', '.', cwd=self.author)
        git('commit', '-qm', 'external update', cwd=self.author)
        external = git('rev-parse', 'HEAD', cwd=self.author)
        git('push', '-q', 'origin', 'main', cwd=self.author)
        with self.assertRaises(WorktreeError):
            self.git.merge_exact(candidate, self.base)
        self.assertEqual(git('rev-parse', 'main', cwd=self.remote), external)

    def test_conflicts_are_repaired_before_review_and_source_branch_stays_clean_of_predecessors(self):
        a = self.source(1, 'feature.txt', 'A\n')
        b = self.source(2, 'feature.txt', 'B\n')
        sa, sb = self.prepare(a), None
        sb = self.prepare(b, sa)
        self.assertTrue(sb['conflicts'])
        attempt = '10000000-0000-4000-8000-000000000001'
        path = self.git.repair(sb, attempt, 'FARM-2', 'FARM')
        (path / 'feature.txt').write_text('A and B\n', encoding='utf-8')
        git('add', '.', cwd=path)
        git('commit', '-qm', 'resolve both features', cwd=path)
        repaired = self.git.repaired(sb, attempt, git('rev-parse', 'HEAD', cwd=path))
        self.assertFalse(repaired['conflicts'])
        self.assertEqual(git('rev-parse', b['source_branch'], cwd=self.remote), b['source_head'])
        self.git.merge_exact(sa, self.base)
        self.git.merge_exact(repaired, sa['commit'])
        self.assertEqual(git('rev-parse', b['source_branch'], cwd=self.remote), repaired['commit'])
        self.assertEqual(git('show', 'main:feature.txt', cwd=self.remote), 'A and B')

    def test_dirty_or_non_descendant_repair_is_rejected(self):
        candidate = self.prepare(self.source(1, 'new.txt', 'feature'))
        attempt = '10000000-0000-4000-8000-000000000001'
        path = self.git.repair(candidate, attempt, 'FARM-1', 'FARM')
        (path / 'new.txt').write_text('uncommitted', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.git.repaired(candidate, attempt, candidate['commit'])

    def test_repair_cannot_publish_private_run_reports(self):
        candidate = self.prepare(self.source(1, 'new.txt', 'feature'))
        attempt = '10000000-0000-4000-8000-000000000001'
        path = self.git.repair(candidate, attempt, 'FARM-1', 'FARM')
        (path / 'reports').mkdir()
        (path / 'reports' / 'test-output.txt').write_text('private verification evidence', encoding='utf-8')
        git('add', '.', cwd=path)
        git('commit', '-qm', 'accidental report', cwd=path)
        with self.assertRaisesRegex(ValueError, 'private state'):
            self.git.repaired(candidate, attempt, git('rev-parse', 'HEAD', cwd=path))

    def test_source_race_refuses_both_ref_updates(self):
        pr = self.source(1, 'new.txt', 'feature')
        candidate = self.prepare(pr)
        (self.author / 'human.txt').write_text('human edit', encoding='utf-8')
        git('add', '.', cwd=self.author)
        git('commit', '-qm', 'source advanced', cwd=self.author)
        human = git('rev-parse', 'HEAD', cwd=self.author)
        git('push', 'origin', pr['source_branch'], cwd=self.author)
        with self.assertRaises(WorktreeError):
            self.git.merge_exact(candidate, self.base)
        self.assertEqual(git('rev-parse', 'main', cwd=self.remote), self.base)
        self.assertEqual(git('rev-parse', pr['source_branch'], cwd=self.remote), human)

    def test_cas_flags_cannot_authorize_a_non_fast_forward(self):
        pr = self.source(1, 'new.txt', 'feature')
        candidate = self.prepare(pr)
        # A forged commit would discard the author's work; the local ancestry
        # guard refuses it before any push despite explicit CAS lease flags.
        candidate['commit'] = self.base
        with self.assertRaises(ValueError):
            self.git.merge_exact(candidate, self.base)
