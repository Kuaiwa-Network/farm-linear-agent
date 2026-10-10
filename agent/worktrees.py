"""FarmBot-owned bare clones and per-item worktrees (spec §8). Never touches human checkouts."""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import time

from .launcher import _read_worker_text

SAFE_BRANCH = re.compile(r"^[A-Za-z0-9._/一-鿿-]+$")
# Task worktrees are for code: LFS pointers stay pointers (Farm-Client carries gigabytes of binaries), and a
# missing credential fails at once instead of waiting on a prompt no one will answer.
GIT_ENV = {"GIT_LFS_SKIP_SMUDGE": "1", "GIT_TERMINAL_PROMPT": "0"}
# FarmBot's own git runs outside every worker sandbox, in clones and worktrees a worker writes parts of, so every
# call runs with hooks and fsmonitor off (plan P10): a worker can no longer write a clone's hooks or config, and this
# keeps anything written there before, or by hand, from running.
HOOKS_OFF = ("-c", f"core.hooksPath={os.devnull}", "-c", "core.fsmonitor=false")
# What a clone's config may hold (plan P10): what ensure_clone, `git init`, `worktree add --track`, `branch
# --set-upstream-to` and git-lfs write there, and a commit identity. Anything else could make FarmBot's git run a
# program (an ssh command, a filter, an include) or fetch from elsewhere, so a clone that holds it is not used. Git
# lists section and key names in lower case.
CLONE_CONFIG = re.compile(r"core\.(?:repositoryformatversion|filemode|bare|ignorecase|precomposeunicode|symlinks"
                          r"|logallrefupdates)|remote\.origin\.(?:url|pushurl|fetch)|branch\..+\.(?:remote|merge)"
                          r"|lfs\.repositoryformatversion|lfs\..+\.(?:access|locksverify)|user\.(?:name|email)")
CLONE_FETCH = "+refs/heads/*:refs/remotes/origin/*"
# A github.com repository URL, its owner and name: publication compares remotes by these, whatever the spelling.
GITHUB_REMOTE = re.compile(r"(?:https://github\.com/|ssh://git@github\.com/|git@github\.com:)"
                           r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?", re.IGNORECASE)


def _same_remote(url, configured):
    """Whether a clone's origin URL is the configured remote: the same string, or the same github.com repository
    spelled another way (https or ssh, case, a .git suffix), as publication compares them."""
    if url == configured:
        return True
    found, wanted = GITHUB_REMOTE.fullmatch(url), GITHUB_REMOTE.fullmatch(configured)
    return bool(found and wanted) and [part.casefold() for part in found.groups()] == [
        part.casefold() for part in wanted.groups()]
# The files a clone's info/ may hold: git init's exclude, and the refs list a repack writes.
CLONE_INFO = frozenset({"exclude", "refs"})
# Controller git in the read-only checkouts of a manifest's `reads` (spec §8.3, §9.6), each a repository of the
# controller's own that borrows only its clone's objects: hooks and fsmonitor off, and the LFS filter emptied, so a
# checkout holds LFS pointers and git runs no filter program, whatever the host's git configuration says.
READ_ONLY_GIT = (*HOOKS_OFF, "-c", "filter.lfs.process=", "-c", "filter.lfs.smudge=", "-c", "filter.lfs.clean=",
                 "-c", "filter.lfs.required=false")
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


def _git(*args, cwd, env=GIT_ENV, timeout=600, config=(), input_text=None):
    """`config` is further `-c` settings placed before the subcommand, after HOOKS_OFF, which every call has."""
    # Farm-Client's nested paths exceed MAX_PATH under an isolated state root. Git for Windows needs this
    # per-command setting even when Windows supports long paths; do not write host or trusted clone config.
    native_config = ("-c", "core.longpaths=true") if os.name == "nt" else ()
    result = subprocess.run(["git", *HOOKS_OFF, *native_config, *config, *args], cwd=str(cwd), capture_output=True, text=True,
                            timeout=timeout, env={**os.environ, **env}, input=input_text, encoding="utf-8")
    if result.returncode:
        raise WorktreeError(f"git {args[0]} failed: {result.stderr.strip()[:500]}")
    return result.stdout.strip()


def _is_link(path):
    """A symlink, or on Windows a junction or another reparse point: an entry that redirects a path. Path.is_symlink
    is false for a junction (agent.uploads checks links the same way)."""
    try:
        status = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return False
    return (stat.S_ISLNK(status.st_mode)
            or bool(getattr(status, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT))


def _remove_tree(path):
    """Remove a directory FarmBot made, the read-only files git leaves on Windows included. A link in place of the
    tree is refused, here and by the callers; rmtree never follows one inside it, and the retry after a failure
    clears read-only only on an entry that is not a link, so nothing outside the tree changes."""
    def writable(function, name, exc):
        if function is os.path.islink or _is_link(name):
            raise exc
        os.chmod(name, stat.S_IWRITE)
        function(name)
    if _is_link(path):
        raise WorktreeError("refusing to remove a link in place of a directory")
    if path.exists():
        shutil.rmtree(path, onexc=writable)


class Worktrees:
    def __init__(self, repos_root, worktrees_root, remotes):
        self.repos_root = Path(repos_root)
        self.worktrees_root = Path(worktrees_root)
        self.remotes = dict(remotes)
        self._head_cache = {}
        self._checked = {}  # repo -> the state of its clone's config and info/ when last found clean

    def clone_path(self, repo):
        if repo not in self.remotes:
            raise WorktreeError(f"unknown repository: {repo}")
        return self.repos_root / f"{repo}.git"

    def clone_problems(self, repo, *, environ=None):
        """What FarmBot's clone of `repo` holds that FarmBot did not write there (plan P10), or an empty list: config
        keys other than CLONE_CONFIG's, an origin or fetch refspec other than the configured ones, files in info/
        other than CLONE_INFO, and legacy remote definitions. Names keys and files, never values. Reads files only:
        `git config --file` follows no include and runs nothing. Hooks are not checked; FarmBot's git never runs
        them (HOOKS_OFF)."""
        clone = self.clone_path(repo)
        config = clone / "config"
        if _is_link(config) or not config.is_file():
            return ["config is not a regular file"]
        source = os.environ if environ is None else environ
        listed = subprocess.run(["git", "config", "--file", str(config), "--null", "--list"], capture_output=True,
                                timeout=60, env={**source, **GIT_ENV})
        if listed.returncode:
            return ["config is unreadable"]
        problems, urls, configured = [], [], self.remotes[repo]
        for entry in listed.stdout.decode("utf-8", errors="replace").split("\0"):
            if not entry:
                continue
            key, _, value = entry.partition("\n")
            if not CLONE_CONFIG.fullmatch(key):
                problems.append(f"config key {key[:120]}")
            elif key == "remote.origin.url":
                urls.append(value)
            elif key == "remote.origin.pushurl" and not _same_remote(value, configured):
                problems.append("config remote.origin.pushurl is not the configured remote")
            elif key == "remote.origin.fetch" and value != CLONE_FETCH:
                problems.append("config remote.origin.fetch is not FarmBot's refspec")
            elif key == "core.bare" and value.lower() != "true":
                problems.append("config core.bare is not true")
        if len(urls) != 1 or not _same_remote(urls[0], configured):
            problems.append("config remote.origin.url is not the configured remote")
        info = clone / "info"
        if _is_link(info):
            problems.append("info is a link")
        elif info.is_dir():
            problems += [f"info/{entry.name[:120]}" for entry in sorted(info.iterdir()) if entry.name not in CLONE_INFO]
        for legacy in ("remotes", "branches"):
            if _is_link(clone / legacy) or ((clone / legacy).is_dir() and any((clone / legacy).iterdir())):
                problems.append(f"{legacy}/ defines remotes")
        return list(dict.fromkeys(problems))

    def _check_clone(self, repo, clone):
        """Refuse a clone that holds what FarmBot did not write (clone_problems). Checked again whenever its config
        or info/ changes: FarmBot's own writes (a tracking branch) change them too."""
        def stamp(path):
            try:
                status = os.lstat(path)
            except FileNotFoundError:
                return None
            return status.st_mtime_ns, status.st_size, status.st_ino

        state = tuple(stamp(clone / name) for name in ("config", "info", "remotes", "branches"))
        if self._checked.get(repo) == state:
            return
        problems = self.clone_problems(repo)
        if problems:
            self._checked.pop(repo, None)
            raise WorktreeError(f"{repo}: FarmBot's clone holds what FarmBot did not write "
                                f"({'; '.join(problems[:10])}); inspect and remove it before this clone is used again")
        self._checked[repo] = state

    def _clone(self, repo):
        """The clone of `repo`, checked (_check_clone), for FarmBot's own git in it or its worktrees."""
        clone = self.clone_path(repo)
        if clone.exists():
            self._check_clone(repo, clone)
        return clone

    def worktree_entry(self, repo, path):
        """The entry under `<clone>/worktrees/` that belongs to the worktree of `repo` at `path` (plan P10).

        The worktree's `.git` file names it, the entry's own `gitdir` file must name that worktree back, and its
        `commondir` file must name the clone: git follows `commondir` for branch refs even where GIT_COMMON_DIR names
        the clone (git 2.54). A worker can rewrite all three, but no other entry, so it cannot claim another
        worktree's. Each is read without following a link or waiting on a FIFO."""
        clone = self._clone(repo)
        path = Path(path)
        entries = clone / "worktrees"
        try:
            if _is_link(path) or _is_link(path / ".git") or _is_link(entries):
                raise WorktreeError("linked")
            pointer = _read_worker_text(path / ".git")
            if not pointer.startswith("gitdir:"):
                raise WorktreeError("no gitdir")
            named = Path(pointer[len("gitdir:"):].strip())
            named = named if named.is_absolute() else path / named
            entry = entries / named.name
            if named.parent.resolve() != entries.resolve() or _is_link(entry) or not entry.is_dir():
                raise WorktreeError("outside the clone")
            back = Path(_read_worker_text(entry / "gitdir").strip())
            back = back if back.is_absolute() else entry / back
            if back.resolve() != (path / ".git").resolve():
                raise WorktreeError("names another worktree")
            common = Path(_read_worker_text(entry / "commondir").strip())
            common = common if common.is_absolute() else entry / common
            if _is_link(entry / "commondir") or common.resolve() != clone.resolve():
                raise WorktreeError("names another clone")
        except (WorktreeError, OSError, UnicodeDecodeError) as exc:
            raise WorktreeError(f"{repo}: {path.name} is not a worktree of FarmBot's clone "
                                f"({exc if isinstance(exc, WorktreeError) else type(exc).__name__})") from exc
        return entry

    def git_in(self, path, *args, env=GIT_ENV, **kwargs):
        """FarmBot's git in the item worktree at `<worktrees>/<item>/<repo>` (plan P10), with the repository named
        explicitly: the worktree's `.git` file and its entry's `commondir` are the worker's to rewrite, and would
        otherwise decide which repository, and so whose config, git uses."""
        path = Path(path)
        explicit = {"GIT_DIR": str(self.worktree_entry(path.name, path)), "GIT_COMMON_DIR": str(self._clone(path.name)),
                    "GIT_WORK_TREE": str(path)}
        return _git(*args, cwd=path, env={**env, **explicit}, **kwargs)

    def _item_worktree(self, path):
        """Whether `path` is an item's worktree, `<worktrees>/<item>/<repo>`, rather than a slot or a read-only
        checkout."""
        path = Path(path)
        return (path.name in self.remotes and not path.parent.name.endswith(".reads")
                and path.parent.parent.resolve() == self.worktrees_root.resolve())

    def writable_parts(self, repo, path):
        """What a worker whose worktree of `repo` is at `path` may write in FarmBot's clone (plan P10): the objects,
        refs, reflogs and LFS store its commits, fetches and pushes write, and the worktree's own entry (its HEAD,
        index and state). Never the clone's config, hooks, info or other entries, which FarmBot's own git reads
        outside the sandbox. Makes logs/ and lfs/ first, since git makes them only when it first needs them."""
        clone = self._clone(repo)
        entry = self.worktree_entry(repo, path)
        for name in ("logs", "lfs"):
            (clone / name).mkdir(exist_ok=True)
        return [clone / "objects", clone / "refs", clone / "logs", clone / "lfs", entry]

    def ensure_clone(self, repo, seed_from=None):
        path = self.clone_path(repo)
        if path.exists():
            self._check_clone(repo, path)
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

    def fetch(self, repo, *, config=()):
        _git("fetch", "--quiet", "--prune", "origin", cwd=self.ensure_clone(repo), config=config)

    def default_branch(self, repo, *, config=()):
        clone = self.ensure_clone(repo)
        out = _git("ls-remote", "--symref", "origin", "HEAD", cwd=clone, config=config)
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

    def _stage_path(self, repo, item_id):
        if repo not in self.remotes or not item_id or Path(item_id).name != item_id or item_id in (".", ".."):
            raise WorktreeError("unsafe stage repository or item path")
        self._managed_paths(item_id)  # refuse redirected roots/entries before constructing a new worktree
        return self.worktrees_root.resolve() / item_id / repo

    def stage_base(self, repo, item_id, branch):
        """Select latest trusted main only on first entry, without creating an issue branch."""
        path = self._stage_path(repo, item_id)
        if path.exists():
            raise WorktreeError("refusing an unpinned existing stage worktree")
        clone = self.ensure_clone(repo)
        _git("check-ref-format", "--branch", branch, cwd=clone)
        baseline = self.resolve_commit(repo)
        if self._ref_commit(clone, f"refs/heads/{branch}") or self._ref_commit(clone, f"refs/remotes/origin/{branch}"):
            raise WorktreeError("refusing an existing issue branch without a controller stage baseline")
        return baseline

    def add_stage(self, repo, item_id, branch, baseline, *, refresh=True, recorded=False):
        """Create/restore the exact recorded client branch at its durable baseline; never reset it to main."""
        if not isinstance(baseline, str) or not self.COMMIT.fullmatch(baseline):
            raise WorktreeError("stage baseline requires a full lowercase commit SHA")
        path = self._stage_path(repo, item_id)
        clone = self.ensure_clone(repo)
        _git("check-ref-format", "--branch", branch, cwd=clone)
        if refresh:
            self.fetch(repo)
        if _git("rev-parse", "--verify", "--end-of-options", baseline+"^{commit}", cwd=clone) != baseline:
            raise WorktreeError("stage baseline is not an available commit")
        if path.exists():
            self.worktree_entry(repo, path)
            if self.git_in(path, "symbolic-ref", "--quiet", "--short", "HEAD") != branch:
                raise WorktreeError("stage worktree is no longer on its recorded issue branch")
            self.git_in(path, "merge-base", "--is-ancestor", baseline, "HEAD")
            return path
        local = self._ref_commit(clone, f"refs/heads/{branch}")
        remote = self._ref_commit(clone, f"refs/remotes/origin/{branch}")
        recovery = self._recovery_commit(clone, self._recovery_ref(item_id))
        if local and self._checked_out(clone, f"refs/heads/{branch}"):
            raise WorktreeError("stage issue branch is held by another worktree")
        if not local and not recovery and remote and not recorded:
            raise WorktreeError("unexpected remote issue branch; recovery requires its recorded plan")
        selected = local or recovery or remote or baseline
        _git("merge-base", "--is-ancestor", baseline, selected, cwd=clone)
        if recovery:
            # Keep recovery evidence rather than restoring an older/different branch over retained work.
            _git("merge-base", "--is-ancestor", recovery, selected, cwd=clone)
        path.parent.mkdir(parents=True, exist_ok=True)
        if local:
            _git("worktree", "add", "--quiet", str(path), branch, cwd=clone)
        else:
            _git("worktree", "add", "--quiet", "-b", branch, str(path), selected, cwd=clone)
        if remote:
            _git("branch", "--quiet", f"--set-upstream-to=origin/{branch}", branch, cwd=clone)
        return path

    def add(self, repo, item_id, branch, *, refresh=True, attach=False):
        """The item's write worktree of `repo` on `branch`; an existing one is returned as it is.

        By default a new worktree restarts at the item's recovery commit when cleanup preserved one, else tracks
        origin/<branch> when only the remote has that branch, else starts from the default branch; its branch is
        `branch`, or `<branch>-<item_id>` when the clone already has `branch`.

        `attach` is for the issue branch a job's plan records (spec §5.7 "Re-attachment"): the worktree checks out
        that branch itself, never a copy under another name, after a fetch. See `_attach`.
        """
        if not branch or not SAFE_BRANCH.match(branch) or branch.startswith("-"):
            raise WorktreeError("unsafe branch name")
        path = self.worktrees_root / item_id / repo
        # A publication retry resumes the existing commits even while origin is offline.
        # PublicationVerifier still checks the worktree and destination before any push.
        if not refresh and path.exists():
            return path
        clone = self.ensure_clone(repo)
        if attach:
            return self._attach(repo, clone, path, branch)
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

    def _attach(self, repo, clone, path, branch):
        """Check out `branch` itself after a fetch (spec §5.7): the clone's own branch, moved forward to
        origin/<branch> when the remote is ahead of it and kept as it is when it has commits of its own, which the
        worker integrates without force-pushing; else a new branch tracking origin/<branch>; else, with neither (a
        clone made again since), a new branch of that name from the default branch. It tracks origin/<branch>
        whenever the remote has that branch. The item's recovery ref is not consulted: it stays where cleanup left
        it, and a successor or a cleaned-up continuation goes on from the branch the plan names. Every git call
        runs with HOOKS_OFF (P10)."""
        self.fetch(repo, config=HOOKS_OFF)
        if path.exists():
            return path
        local, remote = f"refs/heads/{branch}", f"refs/remotes/origin/{branch}"
        local_commit, remote_commit = self._ref_commit(clone, local), self._ref_commit(clone, remote)
        if local_commit and self._checked_out(clone, local):
            raise WorktreeError(f"{branch} is checked out in another worktree of the {repo} clone")
        path.parent.mkdir(parents=True, exist_ok=True)
        if (local_commit and remote_commit and local_commit != remote_commit
                and _git("rev-list", "--count", f"{remote_commit}..{local_commit}", cwd=clone, config=HOOKS_OFF) == "0"):
            # Others pushed on top of it: a fast-forward, which update-ref makes only from the commit just read.
            _git("update-ref", local, remote_commit, local_commit, cwd=clone, config=HOOKS_OFF)
        if local_commit:
            _git("worktree", "add", "--quiet", str(path), branch, cwd=clone, config=HOOKS_OFF)
            if remote_commit:
                _git("branch", "--quiet", f"--set-upstream-to=origin/{branch}", branch, cwd=clone, config=HOOKS_OFF)
        elif remote_commit:
            _git("worktree", "add", "--quiet", "--track", "-b", branch, str(path), f"origin/{branch}", cwd=clone,
                 config=HOOKS_OFF)
        else:
            default = self.default_branch(repo, config=HOOKS_OFF)
            _git("worktree", "add", "--quiet", "-b", branch, str(path), f"origin/{default}", cwd=clone,
                 config=HOOKS_OFF)
        return path

    @staticmethod
    def _ref_commit(clone, ref):
        """The commit a full ref name points at, or None. for-each-ref also lists refs below a pattern, hence the
        exact comparison."""
        for line in _git("for-each-ref", "--format=%(objectname) %(refname)", ref, cwd=clone,
                         config=HOOKS_OFF).splitlines():
            commit, _, name = line.partition(" ")
            if name == ref:
                return commit
        return None

    @staticmethod
    def _checked_out(clone, ref):
        """Whether a worktree of the clone has `ref` checked out."""
        return f"branch {ref}" in _git("worktree", "list", "--porcelain", cwd=clone, config=HOOKS_OFF).splitlines()

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
        if self._item_worktree(path):
            return self.git_in(path, "rev-parse", "HEAD")
        return _git("rev-parse", "HEAD", cwd=path)

    def verification_commit(self, repo, item_id, commit, *, expected_branch=None):
        """Validate a worker's immutable test input without fetching or changing files."""
        if not isinstance(commit, str) or not self.COMMIT.fullmatch(commit):
            raise WorktreeError("verification commit must be a full lowercase commit SHA")
        root = self.worktrees_root.resolve()
        path = root / item_id / repo
        if not path.is_dir() or path.resolve() != path or not path.is_relative_to(root):
            raise WorktreeError("verification requires the item's own worktree")
        try:
            self.worktree_entry(repo, path)
        except WorktreeError as exc:
            raise WorktreeError(f"verification worktree does not belong to FarmBot's configured clone: {exc}") from exc
        if (expected_branch is not None
                and self.git_in(path, "symbolic-ref", "--quiet", "--short", "HEAD") != expected_branch):
            raise WorktreeError("verification requires the controller-recorded client issue branch")
        if self.git_in(path, "rev-parse", "HEAD") != commit:
            raise WorktreeError("verification commit must equal the item's current worktree HEAD")
        if self.git_in(path, "status", "--porcelain", "--untracked-files=all"):
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
                if self.git_in(path, "status", "--porcelain=v1") == "":
                    continue
                self.git_in(path, "add", "--all", "--", ".")
                if self.git_in(path, "diff", "--cached", "--name-only") == "":
                    continue
                if self.git_in(path, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD":
                    # A commit on a detached head is referenced by nothing, so `worktree prune` would sweep
                    # it as surely as the files. Read-only skills get detached worktrees (add_detached).
                    # After the staged-diff guard, so a worktree with nothing to keep leaves no stray ref.
                    name = self._unused_branch(f"farmbot/wip/{_branch_safe(item_id)}", self.clone_path(path.name))
                    self.git_in(path, "checkout", "--quiet", "-b", name)
                self.git_in(path, *self.WIP_IDENTITY, "commit", "--no-verify", "--quiet", "-m", message)
                report["committed"][path.name] = self.git_in(path, "rev-parse", "HEAD")
            except (WorktreeError, subprocess.SubprocessError, OSError) as exc:
                report["errors"][path.name] = f"{type(exc).__name__}: {exc}"[:500]
        return report

    def _managed_paths(self, item_id):
        if not item_id or Path(item_id).name != item_id or item_id in (".", ".."):
            raise WorktreeError("unsafe item path")
        root = self.worktrees_root / item_id
        if _is_link(self.worktrees_root) or _is_link(root):
            raise WorktreeError("symlinked worktree root")
        if not root.exists():
            return []
        paths = sorted(root.iterdir())
        for path in paths:
            if _is_link(path) or not path.is_dir() or _is_link(path / ".git"):
                raise WorktreeError("unexpected managed worktree entry")
            self._clone(path.name)  # an unknown repository, or a clone holding what FarmBot did not write
            try:
                self.worktree_entry(path.name, path)
            except WorktreeError as exc:
                raise WorktreeError(f"worktree belongs to a different clone: {exc}") from exc
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
                clone = self._clone(path.name)
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
            if _git("rev-parse", "--verify", ref, cwd=self._clone(path.name)) != sha:
                raise WorktreeError("recovery ref no longer matches")
            if self.git_in(path, "status", "--porcelain=v1"):
                raise WorktreeError("worktree changed after preservation")
        for path in paths:
            # No --force: Git performs its own final dirty-worktree check.
            _git("worktree", "remove", str(path), cwd=self._clone(path.name))
        root = self.worktrees_root / item_id
        if root.exists():
            root.rmdir()

    def remove(self, item_id):
        item_root = self.worktrees_root / item_id
        if not item_root.exists():
            return
        for path in item_root.iterdir():
            clone = self._clone(path.name)
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

    def reads_root(self, item_id):
        """`<worktrees>/<item>.reads`: one item's read-only checkouts, beside its worktree directory and never in it,
        because cleanup maps every entry under `<worktrees>/<item>/` to one of FarmBot's clones (spec §9.6)."""
        if not item_id or Path(item_id).name != item_id or item_id in (".", ".."):
            raise WorktreeError("unsafe item path")
        return self.worktrees_root / f"{item_id}.reads"

    def read_checkout(self, repo, item_id, *, refresh=True):
        """A read-only checkout of `repo`'s default branch for a manifest's `reads` (spec §9.6), at
        `<worktrees>/<item>.reads/<repo>@main`, detached at the commit origin's default branch has now.

        It is a repository of the controller's own, fetched from the configured remote, and never a worktree of
        FarmBot's bare clone: a worker rooted in that repository can write the clone, its config, hooks and
        attributes included, and git would honour them here. It borrows only the clone's objects (alternates), so
        a large history is not fetched again. Every call runs with READ_ONLY_GIT and GIT_ENV. Each call fetches
        and checks out the default branch again; with `refresh` False an existing checkout is returned as it is,
        as a publication retry reuses its worktrees. It holds none of the job's work, so it gets no recovery ref.
        Anything else found at the path, such as a half-made checkout or one of another remote or clone, is
        replaced.
        """
        if repo not in self.remotes:
            raise WorktreeError(f"unknown repository: {repo}")
        root = self.reads_root(item_id)
        path = root / f"{repo}@main"
        if _is_link(self.worktrees_root) or _is_link(root) or _is_link(path):
            raise WorktreeError("symlinked read-only checkout")
        objects = (self.ensure_clone(repo) / "objects").absolute()  # alternates read a relative path elsewhere
        own = self._own_read_checkout(path, self.remotes[repo], objects)
        if own and not refresh:
            return path
        if not own:
            _remove_tree(path)
            path.mkdir(parents=True)
            _git("init", "--quiet", str(path), cwd=path, config=READ_ONLY_GIT)
            _git("config", "remote.origin.url", self.remotes[repo], cwd=path, config=READ_ONLY_GIT)
            info = path / ".git" / "objects" / "info"
            info.mkdir(parents=True, exist_ok=True)
            # A bare newline on every platform: git keeps a carriage return in the path and ignores the store.
            (info / "alternates").write_text(f"{objects}\n", encoding="utf-8", newline="\n")
        default = None
        for line in _git("ls-remote", "--symref", "origin", "HEAD", cwd=path, config=READ_ONLY_GIT).splitlines():
            if line.startswith("ref:"):
                default = line.split()[1].removeprefix("refs/heads/")
        if not default or not SAFE_BRANCH.match(default):
            raise WorktreeError(f"{repo}: origin has no usable HEAD")
        tracking = f"refs/remotes/origin/{default}"
        _git("fetch", "--quiet", "--no-tags", "origin", f"+refs/heads/{default}:{tracking}", cwd=path,
             config=READ_ONLY_GIT)
        commit = _git("rev-parse", "--verify", "--end-of-options", f"{tracking}^{{commit}}", cwd=path,
                      config=READ_ONLY_GIT)
        _git("checkout", "--quiet", "--detach", "--force", commit, cwd=path, config=READ_ONLY_GIT)
        return path

    @staticmethod
    def _own_read_checkout(path, url, objects):
        """Whether `path` is a read-only checkout this class made for `url` and its clone's `objects`: its own `.git`
        directory, whose origin is that remote and whose only borrowed store is that clone's. Resolved paths are
        compared because git may otherwise find a repository above it."""
        dot_git = path / ".git"
        if _is_link(dot_git) or not dot_git.is_dir():
            return False
        try:
            found = Path(_git("rev-parse", "--absolute-git-dir", cwd=path, config=READ_ONLY_GIT))
            return (found.resolve() == dot_git.resolve()
                    and _git("config", "--local", "--get", "remote.origin.url", cwd=path, config=READ_ONLY_GIT) == url
                    and (dot_git / "objects" / "info" / "alternates").read_bytes() == f"{objects}\n".encode("utf-8"))
        except (WorktreeError, OSError, UnicodeDecodeError):
            return False

    def remove_reads(self, item_id):
        """Remove the item's read-only checkouts, as its worktrees are removed (spec §9.6); nothing in them is
        preserved, and the objects they borrowed stay in FarmBot's clones."""
        root = self.reads_root(item_id)
        if _is_link(self.worktrees_root) or _is_link(root):
            raise WorktreeError("symlinked read-only checkout root")
        _remove_tree(root)

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

    def reconcile_slot_meta(self, repo, path, recovery_id, evidence_dir):
        """Archive importer-dirty Assets metadata before returning a closed slot to the pool.

        A dedicated slot starts clean at switch time. If Unity later changes only existing or
        newly generated .meta files, the requested commit cannot pass source identity. Keep
        the exact tracked tree under an immutable recovery ref and copy untracked metadata
        before restoring it. Any other change remains quarantined for human review.
        """
        from .identity import source_snapshot

        path, evidence_dir = Path(path).resolve(strict=True), Path(evidence_dir).resolve()
        common = Path(_git("rev-parse", "--path-format=absolute", "--git-common-dir",
                           cwd=path, env=SLOT_ENV)).resolve()
        if common != self._clone(repo).resolve():
            raise WorktreeError("slot does not belong to the configured clone")
        before = source_snapshot(path)
        changed = before["dirty"]
        if not changed:
            return None
        if any(
                not row["path"].startswith("Assets/")
                or not row["path"].casefold().endswith(".meta")
                or row["status"] not in (" M", "M ", "MM", "??")
                or row["sha256"] is None for row in changed):
            return None

        evidence_dir.mkdir(parents=True, exist_ok=True)
        tracked, untracked = [], []
        for row in changed:
            relative = row["path"]
            source = path / relative
            if not source.resolve().is_relative_to(path) or source.is_symlink():
                raise WorktreeError("slot metadata path escapes the configured folder")
            if row["status"] == "??":
                contents = source.read_bytes()
                if hashlib.sha256(contents).hexdigest() != row["sha256"]:
                    raise WorktreeError("slot metadata changed while preserving it")
                backup = evidence_dir / "untracked-meta" / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                if not backup.resolve().is_relative_to(evidence_dir):
                    raise WorktreeError("metadata backup path escapes recovery evidence")
                backup.write_bytes(contents)
                untracked.append(relative)
            else:
                tracked.append(relative)

        recovery_ref = f"refs/farmbot/slot-recovery/{_branch_safe(recovery_id)}"
        existing_ref = _git("for-each-ref", "--format=%(objectname)", recovery_ref,
                            cwd=common, env=SLOT_ENV)
        ref = recovery_ref if existing_ref else None
        if tracked:
            saved = _git(*self.WIP_IDENTITY, "stash", "create", f"slot metadata {recovery_id}",
                         cwd=path, env=SLOT_ENV)
            if not self.COMMIT.fullmatch(saved):
                raise WorktreeError("git did not preserve tracked slot metadata")
            if existing_ref:
                if (_git("rev-parse", f"{existing_ref}^{{tree}}", cwd=common, env=SLOT_ENV)
                        != _git("rev-parse", f"{saved}^{{tree}}", cwd=common, env=SLOT_ENV)):
                    raise WorktreeError("saved slot metadata differs from existing recovery evidence")
            else:
                _git("update-ref", recovery_ref, saved, "0" * 40, cwd=common, env=SLOT_ENV)
            ref = recovery_ref
            if _git("rev-parse", ref, cwd=common, env=SLOT_ENV) != (existing_ref or saved):
                raise WorktreeError("saved slot metadata ref could not be verified")

        manifest = evidence_dir / "source-meta.json"
        manifest.write_text(json.dumps({"slot": str(path), "head": before["commit_sha"],
                                        "tracked_ref": ref, "changes": changed,
                                        "untracked_backup": untracked}, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        if source_snapshot(path) != before:
            raise WorktreeError("slot source changed while preserving metadata")
        if tracked:
            # Importers can rewrite thousands of files. Send literal UTF-8 paths
            # on stdin instead of exceeding Windows' command-line length limit.
            _git("--literal-pathspecs", "restore", "--source=HEAD", "--staged", "--worktree",
                 "--pathspec-from-file=-", "--pathspec-file-nul", cwd=path, env=SLOT_ENV,
                 input_text="".join(relative + "\0" for relative in tracked))
        for relative in untracked:
            (path / relative).unlink()
        if source_snapshot(path)["dirty"]:
            raise WorktreeError("slot metadata remained dirty after restoration")
        return {"manifest": str(manifest), "tracked_ref": ref, "head": before["commit_sha"],
                "paths": [row["path"] for row in changed]}

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
