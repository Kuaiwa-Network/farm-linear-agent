"""FarmBot-owned bare clones and per-item worktrees (spec §8). Never touches human checkouts."""
from pathlib import Path
import os
import re
import shutil
import subprocess

SAFE_BRANCH = re.compile(r"^[A-Za-z0-9._/一-鿿-]+$")
# Task worktrees are for code: LFS pointers stay pointers (Farm-Client carries gigabytes of binaries), and a
# missing credential fails at once instead of waiting on a prompt no one will answer.
GIT_ENV = {"GIT_LFS_SKIP_SMUDGE": "1", "GIT_TERMINAL_PROMPT": "0"}


class WorktreeError(RuntimeError):
    pass


def _git(*args, cwd):
    result = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=600,
                            env={**os.environ, **GIT_ENV})
    if result.returncode:
        raise WorktreeError(f"git {args[0]} failed: {result.stderr.strip()[:500]}")
    return result.stdout.strip()


class Worktrees:
    def __init__(self, repos_root, worktrees_root, remotes):
        self.repos_root = Path(repos_root)
        self.worktrees_root = Path(worktrees_root)
        self.remotes = dict(remotes)

    def clone_path(self, repo):
        if repo not in self.remotes:
            raise WorktreeError(f"unknown repository: {repo}")
        return self.repos_root / f"{repo}.git"

    def ensure_clone(self, repo, seed_from=None):
        path = self.clone_path(repo)
        if not path.exists():
            self.repos_root.mkdir(parents=True, exist_ok=True)
            _git("init", "--quiet", "--bare", str(path), cwd=self.repos_root)
            _git("remote", "add", "origin", self.remotes[repo], cwd=path)
            _git("config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*", cwd=path)
            # Seeding from a local checkout of the same remote turns the first origin fetch into a small
            # delta; without it a first launch pays a full clone inside a scheduler tick.
            if seed_from is not None and Path(seed_from).exists():
                _git("fetch", "--quiet", str(seed_from), "+refs/remotes/origin/*:refs/remotes/origin/*", cwd=path)
            _git("fetch", "--quiet", "--prune", "origin", cwd=path)
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
