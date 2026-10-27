"""Controller-owned speculative commits. Never run candidate hooks or merge drivers."""
import os
import subprocess

from .review_policy import ReviewError, commit
from .worktrees import GIT_ENV, HOOKS_OFF, _git

LOCAL_ENV = {**GIT_ENV, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
             'GIT_ATTR_NOSYSTEM': '1', 'GIT_NO_REPLACE_OBJECTS': '1',
             'GIT_AUTHOR_NAME': 'FarmBot', 'GIT_AUTHOR_EMAIL': 'farmbot@localhost',
             'GIT_COMMITTER_NAME': 'FarmBot', 'GIT_COMMITTER_EMAIL': 'farmbot@localhost'}


class CandidateGit:
    def __init__(self, worktrees):
        self.worktrees = worktrees

    def local(self, repo, *args, **kwargs):
        clone = self.worktrees.ensure_clone(repo)
        return _git(*args, cwd=clone, env=LOCAL_ENV, **kwargs)

    def prepare(self, pr, metadata, predecessor=None):
        repo = pr['repository']
        self.worktrees.fetch(repo)
        base = commit(metadata['base']['sha'])
        head = commit(metadata['head']['sha'])
        if head != pr['source_head']:
            raise ReviewError('source head changed after delivery')
        prefix = predecessor['commit'] if predecessor else base
        clone = self.worktrees.ensure_clone(repo)
        result = subprocess.run(['git', *HOOKS_OFF, 'merge-tree', '--write-tree', '--name-only', '--messages', prefix, head],
                                cwd=clone, env={**os.environ, **LOCAL_ENV}, capture_output=True,
                                encoding='utf-8', timeout=120)
        lines = result.stdout.splitlines()
        if result.returncode not in (0, 1) or not lines:
            raise ReviewError('cannot construct integrated candidate')
        tree = commit(lines[0])
        # Stable commit IDs permit a descendant to retain evidence after its exact predecessor lands.
        date = self.local(repo, 'show', '-s', '--format=%cI', head)
        identity = _git('commit-tree', tree, '-p', prefix, '-p', head,
                        cwd=clone, input_text='FarmBot review candidate for ' + pr['url'] + '\n',
                        env={**LOCAL_ENV, 'GIT_AUTHOR_DATE': date, 'GIT_COMMITTER_DATE': date})
        return {'repository': repo, 'target': pr['target'], 'url': pr['url'], 'head': head,
                'source_branch': pr['source_branch'],
                'base': base, 'prefix': prefix, 'commit': commit(identity), 'tree': tree,
                'conflicts': result.returncode == 1}

    def files(self, snapshot):
        repo = snapshot['repository']
        raw = self.local(repo, 'diff', '--no-renames', '--numstat', '-z', snapshot['prefix'], snapshot['commit'])
        result = []
        for record in raw.split('\0'):
            if not record:
                continue
            added, deleted, name = record.split('\t', 2)
            # Unknown binary size does not become zero-sized handwritten work.
            result.append({'filename': name, 'additions': int(added) if added.isdigit() else 1501,
                           'deletions': int(deleted) if deleted.isdigit() else 1501})
        return result

    def read(self, snapshot):
        return self.worktrees.read_candidate(snapshot['repository'], 'review-' + snapshot['id'], snapshot['commit'])

    def repair(self, snapshot, attempt_id, identifier, issue_prefix):
        from .publication import issue_branch
        branch = issue_branch(identifier, issue_prefix) + '-review-' + attempt_id[:8]
        path = self.worktrees.add_stage(snapshot['repository'], attempt_id, branch, snapshot['commit'], refresh=False)
        return path

    def repaired(self, snapshot, attempt_id, head):
        repo = snapshot['repository']
        path = self.worktrees._stage_path(repo, attempt_id)
        self.worktrees.worktree_entry(repo, path)
        if self.worktrees.head(path) != commit(head) or not self.worktrees.slot_clean(path):
            raise ReviewError('repair must leave an exact clean committed worktree')
        self.local(repo, 'merge-base', '--is-ancestor', snapshot['commit'], head)
        if self.local(repo, 'diff', '--name-only', snapshot['prefix'], head, '--', 'reports'):
            raise ReviewError('repair reports must remain in private state, not the published candidate')
        # Catch ordinary unresolved conflict markers, independently of the worker report.
        self.local(repo, '-c', 'core.whitespace=-blank-at-eol,-blank-at-eof,-space-before-tab,cr-at-eol',
                   'diff', '--check', snapshot['prefix'], head)
        return {**snapshot, 'commit': head, 'tree': self.local(repo, 'rev-parse', head + '^{tree}'),
                'conflicts': False, 'repair_parent': snapshot['id']}

    def publish_candidate(self, snapshot):
        clone = self.worktrees.ensure_clone(snapshot['repository'])
        ref = 'refs/heads/farmbot-review/' + snapshot['id']
        _git('push', 'origin', snapshot['commit'] + ':' + ref, cwd=clone, env={**GIT_ENV, 'GIT_NO_REPLACE_OBJECTS': '1'})
        return ref

    def merge_exact(self, snapshot, expected_base):
        """Atomically compare both refs and advance them to the exact tested commit.

        Explicit leases are CAS preconditions, not permission to rewrite history:
        both expected tips must be ancestors of the candidate. Both ref changes must
        be nonempty so Git sends both old-object checks to receive-pack. If either
        ref moved, or the server lacks atomic push, neither update can land.
        """
        repo = snapshot['repository']
        candidate, source = commit(snapshot['commit']), commit(snapshot['head'])
        commit(expected_base)
        if candidate in (source, expected_base):
            raise ReviewError('atomic promotion requires two nonempty ref advances')
        branch = snapshot['source_branch']
        if not branch.startswith('farmbot/') or branch == snapshot['target']:
            raise ReviewError('atomic promotion requires an owned issue branch')
        self.local(repo, 'merge-base', '--is-ancestor', expected_base, candidate)
        self.local(repo, 'merge-base', '--is-ancestor', source, candidate)
        clone = self.worktrees.ensure_clone(repo)
        target_ref, source_ref = 'refs/heads/' + snapshot['target'], 'refs/heads/' + branch
        for ref in (target_ref, source_ref):
            _git('check-ref-format', ref, cwd=clone)
        _git('push', '--atomic', '--force-with-lease=' + target_ref + ':' + expected_base,
             '--force-with-lease=' + source_ref + ':' + source, 'origin', candidate + ':' + target_ref,
             candidate + ':' + source_ref, cwd=clone, env={**GIT_ENV, 'GIT_NO_REPLACE_OBJECTS': '1'})
        return candidate

    def ancestor(self, repo, earlier, later):
        from .worktrees import WorktreeError
        try:
            self.local(repo, 'merge-base', '--is-ancestor', commit(earlier), commit(later))
            return True
        except WorktreeError:
            return False

    def text(self, repo, head, path):
        if path.startswith('/') or '..' in path.split('/'):
            raise ReviewError('invalid repository file path')
        return self.local(repo, 'show', commit(head) + ':' + path)
