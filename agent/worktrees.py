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
# A slot is what Unity opens, so its binaries must be real files. Smudge stays on for every slot call (spec §7).
SLOT_ENV = {"GIT_TERMINAL_PROMPT": "0"}
LFS_POINTER = b"version https://git-lfs"


class WorktreeError(RuntimeError):
    pass


def _git(*args, cwd, env=GIT_ENV, timeout=600):
    result = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                            env={**os.environ, **env})
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
            if cached[2] is not None:
                raise WorktreeError(cached[2])
            return cached[1]
        self.repos_root.mkdir(parents=True, exist_ok=True)
        try:
            out = _git("ls-remote", "--exit-code", "--", self.remotes[repo], "HEAD",
                       cwd=self.repos_root, timeout=timeout)
            commit = out.split()[0] if out else ""
            if not self.COMMIT.match(commit):
                raise WorktreeError(f"{repo}: origin HEAD did not resolve to a commit")
        except (WorktreeError, subprocess.TimeoutExpired, OSError) as exc:
            # A failure is cached for the same 60s as a success. The receiver drains events serially, so an
            # unreachable origin must cost one timeout for a whole burst rather than one per event: ten
            # queued events would otherwise breach spec §17 criterion 1 ten times over. The message is
            # cached rather than the exception, so a cached timeout does not re-raise a stale traceback.
            self._head_cache[repo] = (time.monotonic(), None, f"{repo}: {exc}")
            raise
        self._head_cache[repo] = (time.monotonic(), commit, None)
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

    def add_slot(self, repo, path, commit):
        """A slot is a long-lived detached worktree whose binaries are files, not pointers (spec §7, §8)."""
        if not self.COMMIT.match(commit or ""):
            raise WorktreeError("a slot is only ever checked out at a full commit")
        clone = self.ensure_clone(repo)
        path = Path(path)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            _git("worktree", "add", "--quiet", "--detach", str(path), commit, cwd=clone, env=SLOT_ENV, timeout=7200)
        self.materialize(path)
        self.skip_generated(path)
        return path

    def checkout_commit(self, path, commit):
        if not self.COMMIT.match(commit or ""):
            raise WorktreeError("a slot is only ever moved to a full commit")
        _git("checkout", "--detach", "--force", commit, cwd=path, env=SLOT_ENV, timeout=3600)
        self.materialize(path)
        self.skip_generated(path)   # idempotent; a forced checkout is the one thing that could drop the bit
        return commit

    def materialize(self, path):
        """Fetch then check out the LFS objects this checkout needs. The store is shared with every other
        worktree of the same clone, so only the first slot pays the full download.

        There is no --quiet: git-lfs has no per-command quiet flag, and `git lfs fetch --quiet` exits non-zero
        with `Error: unknown flag: --quiet` (verified against git-lfs/3.7.1). Since _git raises on any non-zero
        return, passing it would make every call to materialize fail, which is every slot this plan creates.
        Output is captured by _git already; GIT_LFS_PROGRESS silences the progress meter if it ever matters.
        A repository with no LFS objects fetches zero and succeeds, which is what the test fixture's origin is.
        """
        if shutil.which("git-lfs") is None:
            return "git-lfs is not installed"
        try:
            _git("lfs", "fetch", "origin", "HEAD", cwd=path, env=SLOT_ENV, timeout=7200)
        except WorktreeError as exc:
            # Task 3's error must tell the operator which of the two problems they have (see the operator
            # items): a 401/Authorization failure is a missing credential for git.kuaiwa.com, anything else
            # is reachability. GIT_TERMINAL_PROMPT=0 turns the first into an error instead of a hung prompt.
            kind = "credentials" if re.search(r"401|Authoriz|credential", str(exc), re.I) else "reachability"
            raise WorktreeError(f"git lfs fetch failed ({kind}): {exc}") from exc
        _git("lfs", "checkout", cwd=path, env=SLOT_ENV, timeout=3600)
        return "materialized"

    # Tracked files Unity regenerates per *folder*, so they differ in every slot and in none of them is the
    # difference a change anyone wants. Task 0 Step 3: the Editor rewrites .vscode/settings.json on every run
    # because the generated solution is named after the project folder (Farm-Client.slnx -> slot-1.slnx),
    # which would fail Task 4's clean-tree precondition on every switch, for ever.
    GENERATED_PER_FOLDER = (".vscode/settings.json",)

    def skip_generated(self, path):
        """Mark the per-folder generated files skip-worktree so they can neither dirty the slot nor be
        committed. Idempotent, and a file the repository does not carry is skipped rather than an error —
        git update-index refuses an unknown path, and the Windows host's list may differ."""
        marked = []
        for name in self.GENERATED_PER_FOLDER:
            if not (Path(path) / name).exists():
                continue
            _git("update-index", "--skip-worktree", "--", name, cwd=path, env=SLOT_ENV)
            marked.append(name)
        return marked

    def slot_clean(self, path):
        """Tracked files only: Library/, Temp/ and Logs/ are Unity's and are never part of the check (spec §7)."""
        return _git("status", "--porcelain=v1", "--untracked-files=no", cwd=path, env=SLOT_ENV) == ""

    def pointers_remain(self, path):
        """One sample of the tracked LFS files proves whether smudge really ran before Unity opens the folder."""
        if shutil.which("git-lfs") is None:
            return False
        names = _git("lfs", "ls-files", "--name-only", cwd=path, env=SLOT_ENV).splitlines()[:20]
        for name in names:
            candidate = Path(path) / name
            if candidate.is_file():
                with candidate.open("rb") as handle:
                    if handle.read(len(LFS_POINTER)) == LFS_POINTER:
                        return True
        return False
