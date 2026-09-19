"""FarmBot-owned bare clones and per-item worktrees (spec §8). Never touches human checkouts."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import time

SAFE_BRANCH = re.compile(r"^[A-Za-z0-9._/一-鿿-]+$")
# Task worktrees are for code: LFS pointers stay pointers (Farm-Client carries gigabytes of binaries), and a
# missing credential fails at once instead of waiting on a prompt no one will answer.
GIT_ENV = {"GIT_LFS_SKIP_SMUDGE": "1", "GIT_TERMINAL_PROMPT": "0"}


class WorktreeError(RuntimeError):
    pass


def _git(*args, cwd, timeout=600):
    result = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                            env={**os.environ, **GIT_ENV})
    if result.returncode:
        raise WorktreeError(f"git {args[0]} failed: {result.stderr.strip()[:500]}")
    return result.stdout.strip()


class Worktrees:
    def __init__(self, repos_root, worktrees_root, remotes):
        self.repos_root = Path(repos_root)
        self.worktrees_root = Path(worktrees_root)
        self.remotes = dict(remotes)
        self._head_cache = {}

    def clone_path(self, repo):
        if repo not in self.remotes:
            raise WorktreeError(f"unknown repository: {repo}")
        return self.repos_root / f"{repo}.git"

    def ensure_clone(self, repo, seed_from=None):
        path = self.clone_path(repo)
        if path.exists():
            return path
        self.repos_root.mkdir(parents=True, exist_ok=True)
        # The destination directory is this method's only readiness marker, so build under a temporary
        # name: a fetch that fails midway must leave nothing that a later run reads as a finished clone.
        staging = self.repos_root / f".{repo}.git.incomplete-{os.getpid()}"
        shutil.rmtree(staging, ignore_errors=True)
        try:
            _git("init", "--quiet", "--bare", str(staging), cwd=self.repos_root)
            _git("remote", "add", "origin", self.remotes[repo], cwd=staging)
            _git("config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*", cwd=staging)
            # Seeding from a local checkout of the same remote turns the first origin fetch into a small
            # delta; without it a first launch pays a full clone inside a scheduler tick. The path is
            # resolved because git would otherwise read a relative one against the clone directory.
            if seed_from is not None:
                seed = Path(seed_from).expanduser().resolve()
                if seed.exists():
                    _git("fetch", "--quiet", str(seed), "+refs/remotes/origin/*:refs/remotes/origin/*", cwd=staging)
            _git("fetch", "--quiet", "--prune", "origin", cwd=staging)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        if path.exists():
            shutil.rmtree(staging, ignore_errors=True)  # another run finished first; keep theirs
        else:
            staging.rename(path)
        return path

    def fetch(self, repo):
        _git("fetch", "--quiet", "--prune", "origin", cwd=self.ensure_clone(repo))

    def default_branch(self, repo):
        clone = self.ensure_clone(repo)
        out = _git("ls-remote", "--symref", "origin", "HEAD", cwd=clone)
        for line in out.splitlines():
            if line.startswith("ref:"):
                return line.split()[1].removeprefix("refs/heads/")
        raise WorktreeError("origin has no HEAD")

    COMMIT = re.compile(r"^[0-9a-f]{40}$")

    def resolve_commit(self, repo, ref=None):
        """A pinned target is always a full commit: git refuses the same branch in two worktrees (spec §8).
        This is the pool's path: it clones if it must and fetches every time, so it may take minutes."""
        clone = self.ensure_clone(repo)
        self.fetch(repo)
        ref = ref or f"origin/{self.default_branch(repo)}"
        commit = _git("rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}", cwd=clone)
        if not self.COMMIT.match(commit):
            raise WorktreeError(f"{ref} did not resolve to a commit")
        return commit

    def remote_head(self, repo, timeout=8):
        """The receiver's path: one ls-remote against the remote URL, no clone and no fetch, so pinning a new
        session cannot cost the ten seconds spec §17 criterion 1 gives the first activity. Cached for 60s so a
        burst of events pays once."""
        if repo not in self.remotes:
            raise WorktreeError(f"unknown repository: {repo}")
        cached = self._head_cache.get(repo)
        if cached and time.monotonic() - cached[0] < 60:
            return cached[1]
        self.repos_root.mkdir(parents=True, exist_ok=True)
        out = _git("ls-remote", "--exit-code", "--", self.remotes[repo], "HEAD",
                   cwd=self.repos_root, timeout=timeout)
        commit = out.split()[0] if out else ""
        if not self.COMMIT.match(commit):
            raise WorktreeError(f"{repo}: origin HEAD did not resolve to a commit")
        self._head_cache[repo] = (time.monotonic(), commit)
        return commit

    def add(self, repo, item_id, branch):
        if not branch or not SAFE_BRANCH.match(branch) or branch.startswith("-"):
            raise WorktreeError("unsafe branch name")
        clone = self.ensure_clone(repo)
        self.fetch(repo)
        path = self.worktrees_root / item_id / repo
        if path.exists():
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        remote_branches = set(_git("for-each-ref", "--format=%(refname:short)", "refs/remotes/origin", cwd=clone).splitlines())
        local_branches = set(_git("for-each-ref", "--format=%(refname:short)", "refs/heads", cwd=clone).splitlines())
        if f"origin/{branch}" in remote_branches and branch not in local_branches:
            _git("worktree", "add", "--quiet", "--track", "-b", branch, str(path), f"origin/{branch}", cwd=clone)
        else:
            name = branch if branch not in local_branches else f"{branch}-{item_id}"
            _git("worktree", "add", "--quiet", "-b", name, str(path), f"origin/{self.default_branch(repo)}", cwd=clone)
        return path

    def head(self, path):
        return _git("rev-parse", "HEAD", cwd=path)

    def remove(self, item_id):
        item_root = self.worktrees_root / item_id
        if not item_root.exists():
            return
        for path in item_root.iterdir():
            clone = self.clone_path(path.name)
            _git("worktree", "remove", "--force", str(path), cwd=clone)
            _git("worktree", "prune", cwd=clone)
        shutil.rmtree(item_root, ignore_errors=True)

    def add_detached(self, repo, item_id):
        clone = self.ensure_clone(repo)
        self.fetch(repo)
        path = self.worktrees_root / item_id / repo
        if path.exists():
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        _git("worktree", "add", "--quiet", "--detach", str(path), f"origin/{self.default_branch(repo)}", cwd=clone)
        return path
