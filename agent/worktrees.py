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
# The "0" is explicit and not an omission: _git merges os.environ, so merely leaving the key out lets an
# operator shell that exported GIT_LFS_SKIP_SMUDGE=1 — the shell that built this host's first slot did — win
# silently and hand Unity pointer files, which is the failure that reads as project corruption.
SLOT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "0"}
LFS_POINTER = b"version https://git-lfs"
# The operator's two problems need two different fixes (Task 0 Step 2): a credential missing for the LFS
# endpoint, or an origin that cannot be reached. Anything else is neither and is left unlabelled.
CREDENTIAL_FAILURE = re.compile(r"401|Authoriz|credential", re.I)
TRANSFER_FAILURE = re.compile(r"lfs|smudge|connect|resolve|timed out|timeout|unable to access|"
                              r"could not read|not found|access denied", re.I)


def _branch_safe(name):
    """A ref name git will accept, from an identifier this module did not choose."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(name)).strip("-.") or "item"


def _transfer_kind(exc):
    if CREDENTIAL_FAILURE.search(str(exc)):
        return "credentials"
    return "reachability" if TRANSFER_FAILURE.search(str(exc)) else None


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

    def add(self, repo, item_id, branch, *, refresh=True):
        if not branch or not SAFE_BRANCH.match(branch) or branch.startswith("-"):
            raise WorktreeError("unsafe branch name")
        path = self.worktrees_root / item_id / repo
        # A publication retry resumes the existing commits even while origin is offline.
        # PublicationVerifier still checks the worktree and destination before any push.
        if not refresh and path.exists():
            return path
        clone = self.ensure_clone(repo)
        recovery = self._recovery_commit(clone, self._recovery_ref(item_id)) if not path.exists() else None
        if recovery:
            path.parent.mkdir(parents=True, exist_ok=True)
            local_branches = set(_git("for-each-ref", "--format=%(refname:short)", "refs/heads", cwd=clone).splitlines())
            name = branch if branch not in local_branches else f"{branch}-{item_id}"
            _git("worktree", "add", "--quiet", "-b", self._unused_branch(name, clone), str(path), recovery, cwd=clone)
            return path
        self.fetch(repo)
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

    @staticmethod
    def _unused_branch(name, cwd):
        branches = set(_git("for-each-ref", "--format=%(refname:short)", "refs/heads", cwd=cwd).splitlines())
        candidate, suffix = name, 2
        while candidate in branches:
            candidate = f"{name}-{suffix}"
            suffix += 1
        return candidate

    @staticmethod
    def _recovery_ref(item_id):
        return f"refs/farmbot/recovery/{_branch_safe(item_id)}"

    def _recovery_commit(self, clone, ref):
        # Enumerating covers packed refs; checking the loose file also catches malformed or dangling refs
        # that Git omits from enumeration. Such evidence must never look like a fresh job with no recovery.
        if not (clone / ref).exists():
            refs = _git("for-each-ref", "--format=%(refname)", ref, cwd=clone).splitlines()
            if ref not in refs:
                return None
        try:
            commit = _git("rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}", cwd=clone)
            if not self.COMMIT.fullmatch(commit):
                raise WorktreeError("not a full commit SHA")
            return commit
        except WorktreeError as exc:
            raise WorktreeError(f"invalid recovery ref {ref}: {exc}") from exc

    def head(self, path):
        return _git("rev-parse", "HEAD", cwd=path)

    def verification_commit(self, repo, item_id, commit):
        """Validate a worker's immutable test input without fetching or changing files."""
        if not isinstance(commit, str) or not self.COMMIT.fullmatch(commit):
            raise WorktreeError("verification commit must be a full lowercase commit SHA")
        root = self.worktrees_root.resolve()
        path = root / item_id / repo
        if (not path.is_dir() or path.resolve() != path or not path.is_relative_to(root)
                or Path(_git("rev-parse", "--show-toplevel", cwd=path)).resolve() != path):
            raise WorktreeError("verification requires the item's own worktree")
        common = Path(_git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=path)).resolve()
        if common != self.clone_path(repo).resolve():
            raise WorktreeError("verification worktree does not belong to FarmBot's configured clone")
        if self.head(path) != commit:
            raise WorktreeError("verification commit must equal the item's current worktree HEAD")
        if _git("status", "--porcelain", "--untracked-files=all", cwd=path):
            raise WorktreeError("verification requires a clean worktree; commit all intended changes first")
        return commit

    # A failed item's worktrees are swept, so anything only in them is gone; the commit below is what keeps
    # it. FarmBot commits as itself rather than as the operator, and never relies on a global git identity,
    # which a launchd service does not necessarily have.
    WIP_IDENTITY = ("-c", "user.name=FarmBot", "-c", "user.email=farmbot@localhost")

    def commit_wip(self, item_id, message):
        """Commit whatever a failed item left in its worktrees, before `remove` takes it with the folder.

        A failure is exactly when an operator most wants to see what the worker did, and on 2026-09-20 a
        worker claimed a farm-hive fix with three passing tests that left no commit anywhere; the sweep had
        already removed the worktree, so the claim could not be adjudicated even in principle (Finding 1
        and Finding 3).

        Commit, never push: publishing is a side effect the failure path has no mandate for. An untouched
        worktree produces no commit, and a repository that cannot be committed is reported rather than
        raised, because nothing here may mask the failure that brought us here or stop the sweep.

        Returns {"committed": {repo: sha}, "errors": {repo: message}}.
        """
        if not isinstance(message, str) or not message.strip():
            raise WorktreeError("a work-in-progress commit needs a message")
        report = {"committed": {}, "errors": {}}
        item_root = self.worktrees_root / item_id
        if not item_root.exists():
            return report
        for path in sorted(item_root.iterdir()):
            if not path.is_dir():
                continue
            try:
                # Two guards against an empty commit, and either alone would do: the first so a clean
                # worktree never pays for `git add --all`, which walks the whole index and a Farm-Client
                # worktree is gigabytes; the second because the two calls are not one atomic act and the
                # worker's own processes have only just been killed.
                if _git("status", "--porcelain=v1", cwd=path) == "":
                    continue
                _git("add", "--all", "--", ".", cwd=path)
                if _git("diff", "--cached", "--name-only", cwd=path) == "":
                    continue
                if _git("rev-parse", "--abbrev-ref", "HEAD", cwd=path) == "HEAD":
                    # A commit on a detached head is referenced by nothing, so `worktree prune` would sweep
                    # it as surely as the files. Read-only skills get detached worktrees (add_detached).
                    # After the staged-diff guard, so a worktree with nothing to keep leaves no stray ref.
                    name = self._unused_branch(f"farmbot/wip/{_branch_safe(item_id)}", path)
                    _git("checkout", "--quiet", "-b", name, cwd=path)
                _git(*self.WIP_IDENTITY, "commit", "--no-verify", "--quiet", "-m", message, cwd=path)
                report["committed"][path.name] = _git("rev-parse", "HEAD", cwd=path)
            except (WorktreeError, subprocess.SubprocessError, OSError) as exc:
                report["errors"][path.name] = f"{type(exc).__name__}: {exc}"[:500]
        return report

    def _managed_paths(self, item_id):
        if not item_id or Path(item_id).name != item_id or item_id in (".", ".."):
            raise WorktreeError("unsafe item path")
        root = self.worktrees_root / item_id
        if self.worktrees_root.is_symlink() or root.is_symlink():
            raise WorktreeError("symlinked worktree root")
        if not root.exists():
            return []
        paths = sorted(root.iterdir())
        for path in paths:
            if path.is_symlink() or not path.is_dir() or (path / ".git").is_symlink():
                raise WorktreeError("unexpected managed worktree entry")
            clone = self.clone_path(path.name)
            common = Path(_git("rev-parse", "--git-common-dir", cwd=path))
            if not common.is_absolute():
                common = path / common
            if common.resolve() != clone.resolve():
                raise WorktreeError("worktree belongs to a different clone")
        return paths

    def preserve(self, item_id):
        """Keep every HEAD reachable, including clean unpublished and detached commits.

        `refs` remains the latest recovery pointer. `snapshots` maps each repository's historical commit
        SHAs to immutable recovery refs, so reset/divergent later attempts cannot hide earlier evidence.
        """
        paths = self._managed_paths(item_id)
        report = self.commit_wip(item_id, f"wip({item_id[:8]}): preserve ended work")
        report["refs"] = {}
        report["snapshots"] = {}
        for path in paths:
            try:
                sha = self.head(path)
                clone = self.clone_path(path.name)
                ref = self._recovery_ref(item_id)
                previous = self._recovery_commit(clone, ref)
                history = f"refs/farmbot/recovery-history/{_branch_safe(item_id)}/"
                # Save the old pointer too: it may predate immutable snapshots. Never replace a snapshot,
                # and do not advance the latest pointer if any archive write fails.
                for commit in dict.fromkeys(filter(None, (previous, sha))):
                    snapshot = history + commit
                    archived = self._recovery_commit(clone, snapshot)
                    if archived is None:
                        _git("update-ref", snapshot, commit, "0" * 40, cwd=clone)
                    elif archived != commit:
                        raise WorktreeError(f"recovery snapshot no longer matches {snapshot}")
                _git("update-ref", ref, sha, previous or "0" * 40, cwd=clone)
                report["committed"][path.name] = sha
                report["refs"][path.name] = ref
                report["snapshots"][path.name] = dict(line.split(" ", 1) for line in
                    _git("for-each-ref", "--format=%(objectname) %(refname)", history, cwd=clone).splitlines())
            except (WorktreeError, subprocess.SubprocessError, OSError) as exc:
                report["errors"][path.name] = str(exc)[:500]
        return report

    def remove_preserved(self, item_id, evidence):
        if evidence.get("errors"):
            raise WorktreeError("preservation incomplete")
        paths = self._managed_paths(item_id)
        # Validate every repository before removing any of them.
        for path in paths:
            sha = evidence.get("committed", {}).get(path.name)
            ref = evidence.get("refs", {}).get(path.name)
            if not sha or not ref or self.head(path) != sha:
                raise WorktreeError("HEAD was not preserved")
            if _git("rev-parse", "--verify", ref, cwd=self.clone_path(path.name)) != sha:
                raise WorktreeError("recovery ref no longer matches")
            if _git("status", "--porcelain=v1", cwd=path):
                raise WorktreeError("worktree changed after preservation")
        for path in paths:
            # No --force: Git performs its own final dirty-worktree check.
            _git("worktree", "remove", str(path), cwd=self.clone_path(path.name))
        root = self.worktrees_root / item_id
        if root.exists():
            root.rmdir()

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
        path = self.worktrees_root / item_id / repo
        recovery = self._recovery_commit(clone, self._recovery_ref(item_id)) if not path.exists() else None
        if recovery:
            path.parent.mkdir(parents=True, exist_ok=True)
            _git("worktree", "add", "--quiet", "--detach", str(path), recovery, cwd=clone)
            return path
        self.fetch(repo)
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
            try:
                _git("worktree", "add", "--quiet", "--detach", str(path), commit, cwd=clone, env=SLOT_ENV,
                     timeout=7200)
            except WorktreeError as exc:
                # With smudge on, the LFS download happens *here* and not in materialize's `git lfs fetch`,
                # so this is where a 401 or an unreachable origin actually surfaces on a fresh host. A
                # failure that is neither is re-raised as it came: a bad reference is a third problem.
                kind = _transfer_kind(exc)
                if kind is None:
                    raise
                raise WorktreeError(f"git worktree add failed ({kind}): {exc}") from exc
        self.materialize(path)
        self.skip_generated(path)
        return path

    def checkout_commit(self, path, commit):
        if not self.COMMIT.match(commit or ""):
            raise WorktreeError("a slot is only ever moved to a full commit")
        try:
            _git("checkout", "--detach", "--force", commit, cwd=path, env=SLOT_ENV, timeout=3600)
        except WorktreeError as exc:
            # Smudge is on here exactly as it is in add_slot, so the LFS download for the incoming commit
            # happens inside this checkout: a 401 or an unreachable origin surfaces here on every switch
            # after the first. The switch wraps this in a SlotError the operator reads, and an unlabelled
            # "git checkout failed" is the one message that does not say which of their two problems it is.
            # A failure that is neither is re-raised as it came: `reference is not a tree` is a third problem.
            kind = _transfer_kind(exc)
            if kind is None:
                raise
            raise WorktreeError(f"git checkout failed ({kind}): {exc}") from exc
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
            # Unlike worktree add, every way this one fails is a transfer, so it is never left unlabelled.
            raise WorktreeError(f"git lfs fetch failed ({_transfer_kind(exc) or 'reachability'}): {exc}") from exc
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
