import contextlib
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agent.worktrees
from agent.worktrees import WorktreeError, Worktrees


def git(*args, cwd, allow_failure=False):
    """allow_failure lets a caller read the stdout of a command whose non-zero exit is the answer, such as
    `symbolic-ref -q HEAD` on a detached head, which exits 1 and prints nothing."""
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd,
                          check=not allow_failure, capture_output=True, text=True).stdout.strip()


class WorktreeTests(unittest.TestCase):
    def test_verification_commit_must_be_clean_owned_worktree_head(self):
        path = self.trees.add("Farm-Client", "item-1", "farmbot/fix")
        baseline = self.trees.head(path)
        (path / "README.md").write_text("fixed\n")
        git("commit", "-qam", "fix", cwd=path)
        fixed = self.trees.head(path)
        self.assertEqual(self.trees.verification_commit("Farm-Client", "item-1", fixed), fixed)
        for candidate in (baseline, "HEAD", fixed[:8], "0" * 40):
            with self.subTest(candidate=candidate), self.assertRaises(WorktreeError):
                self.trees.verification_commit("Farm-Client", "item-1", candidate)
        (path / "untracked.cs").write_text("uncommitted change")
        with self.assertRaisesRegex(WorktreeError, "clean"):
            self.trees.verification_commit("Farm-Client", "item-1", fixed)
        (path / "untracked.cs").unlink()
        (path / "README.md").write_text("uncommitted fix\n")
        with self.assertRaisesRegex(WorktreeError, "clean"):
            self.trees.verification_commit("Farm-Client", "item-1", fixed)

    def test_verification_rejects_foreign_checkout_and_missing_worktree(self):
        sha = self.trees.head(self.origin)
        path = self.trees.worktrees_root / "item-1" / "Farm-Client"
        path.parent.mkdir(parents=True)
        path.symlink_to(self.origin, target_is_directory=True)
        with self.assertRaises(WorktreeError):
            self.trees.verification_commit("Farm-Client", "item-1", sha)
        path.unlink()
        git("clone", "-q", str(self.origin), str(path), cwd=self.origin)
        with self.assertRaises(WorktreeError):
            self.trees.verification_commit("Farm-Client", "item-1", sha)
        with self.assertRaises(WorktreeError):
            self.trees.verification_commit("Farm-Client", "missing", sha)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        origin = root / "origin"
        origin.mkdir()
        git("init", "-q", "-b", "main", ".", cwd=origin)
        (origin / "README.md").write_text("hello\n", encoding="utf-8")
        git("add", ".", cwd=origin)
        git("commit", "-qm", "init", cwd=origin)
        self.origin = origin
        self.trees = Worktrees(root / "repos", root / "worktrees", {"Farm-Client": str(origin)})

    def test_clone_is_bare_and_worktree_is_on_a_new_branch_from_default(self):
        clone = self.trees.ensure_clone("Farm-Client")
        self.assertEqual(git("rev-parse", "--is-bare-repository", cwd=clone), "true")
        path = self.trees.add("Farm-Client", "item-1", "farmbot/farm-1")
        self.assertEqual(git("rev-parse", "--abbrev-ref", "HEAD", cwd=path), "farmbot/farm-1")
        self.assertEqual(self.trees.head(path), git("rev-parse", "HEAD", cwd=self.origin))
        self.assertTrue((path / "README.md").exists())

    def test_existing_remote_branch_is_tracked_and_local_collision_gets_suffix(self):
        git("checkout", "-qb", "farmbot/farm-1", cwd=self.origin)
        (self.origin / "x.txt").write_text("x", encoding="utf-8")
        git("add", ".", cwd=self.origin)
        git("commit", "-qm", "wip", cwd=self.origin)
        git("checkout", "-q", "main", cwd=self.origin)
        first = self.trees.add("Farm-Client", "item-1", "farmbot/farm-1")
        self.assertEqual(git("rev-parse", "--abbrev-ref", "HEAD", cwd=first), "farmbot/farm-1")
        self.assertTrue((first / "x.txt").exists())
        self.assertEqual(self.trees.default_branch("Farm-Client"), "main")
        second = self.trees.add("Farm-Client", "item-2", "farmbot/farm-1")
        self.assertEqual(git("rev-parse", "--abbrev-ref", "HEAD", cwd=second), "farmbot/farm-1-item-2")
        self.assertFalse((second / "x.txt").exists())

    def test_remote_branches_existing_before_the_clone_are_not_local_branches(self):
        git("checkout", "-qb", "farmbot/farm-1", cwd=self.origin)
        (self.origin / "x.txt").write_text("x", encoding="utf-8")
        git("add", ".", cwd=self.origin)
        git("commit", "-qm", "wip", cwd=self.origin)
        git("checkout", "-q", "main", cwd=self.origin)
        clone = self.trees.ensure_clone("Farm-Client")
        self.assertEqual(git("for-each-ref", "--format=%(refname:short)", "refs/heads", cwd=clone), "")
        path = self.trees.add("Farm-Client", "item-1", "farmbot/farm-1")
        self.assertEqual(git("rev-parse", "--abbrev-ref", "HEAD", cwd=path), "farmbot/farm-1")
        self.assertTrue((path / "x.txt").exists())

    def test_remove_deletes_all_worktrees_of_an_item(self):
        path = self.trees.add("Farm-Client", "item-1", "farmbot/farm-1")
        self.trees.remove("item-1")
        self.assertFalse(path.exists())
        self.assertNotIn(str(path), git("worktree", "list", cwd=self.trees.ensure_clone("Farm-Client")))

    def test_removed_same_item_resumes_saved_work_on_every_retry(self):
        for readonly in (False, True):
            item = "readonly" if readonly else "writer"
            def reopen():
                if readonly:
                    return self.trees.add_detached("Farm-Client", item)
                return self.trees.add("Farm-Client", item, "farmbot/retry", refresh=False)
            path = reopen()
            with self.subTest(readonly=readonly):
                for attempt in range(3):
                    (path / "fix.txt").write_text(f"saved work {attempt}\n")
                    saved = self.trees.preserve(item)
                    self.assertEqual(saved["errors"], {})
                    self.trees.remove_preserved(item, saved)
                    path = reopen()
                    self.assertEqual(self.trees.head(path), saved["committed"]["Farm-Client"])
                    self.assertEqual((path / "fix.txt").read_text(), f"saved work {attempt}\n")
                    branch = git("branch", "--show-current", cwd=path)
                    self.assertEqual(bool(branch), not readonly)

    def test_recovery_does_not_replace_an_existing_worktree(self):
        for readonly in (False, True):
            item = "readonly" if readonly else "writer"
            if readonly:
                path = self.trees.add_detached("Farm-Client", item)
            else:
                path = self.trees.add("Farm-Client", item, "farmbot/active")
            self.trees.preserve(item)
            (path / "pending.txt").write_text("active work")
            if readonly:
                reopened = self.trees.add_detached("Farm-Client", item)
            else:
                reopened = self.trees.add("Farm-Client", item, "farmbot/active")
            self.assertEqual(reopened, path)
            self.assertEqual((path / "pending.txt").read_text(), "active work")

    def test_invalid_recovery_ref_never_starts_from_baseline(self):
        clone = self.trees.ensure_clone("Farm-Client")
        blob = git("rev-parse", "origin/main:README.md", cwd=clone)
        for readonly in (False, True):
            for index, invalid in enumerate((blob, "f" * 40, "invalid", "ref: refs/heads/missing")):
                item = f"invalid-{readonly}-{index}"
                ref = clone / "refs" / "farmbot" / "recovery" / item
                ref.parent.mkdir(parents=True, exist_ok=True)
                ref.write_text(invalid + "\n")
                with self.subTest(readonly=readonly, invalid=invalid):
                    with self.assertRaisesRegex(WorktreeError, "recovery"):
                        if readonly:
                            self.trees.add_detached("Farm-Client", item)
                        else:
                            self.trees.add("Farm-Client", item, f"farmbot/{item}", refresh=False)
                    self.assertFalse((self.trees.worktrees_root / item / "Farm-Client").exists())
                ref.unlink()

    def test_packed_recovery_ref_restores_saved_commit(self):
        path = self.trees.add("Farm-Client", "item-1", "farmbot/packed")
        (path / "fix.txt").write_text("saved work")
        saved = self.trees.preserve("item-1")
        self.trees.remove_preserved("item-1", saved)
        clone = self.trees.clone_path("Farm-Client")
        git("pack-refs", "--all", cwd=clone)
        self.assertFalse((clone / saved["refs"]["Farm-Client"]).exists())
        path = self.trees.add("Farm-Client", "item-1", "farmbot/packed")
        self.assertEqual(self.trees.head(path), saved["committed"]["Farm-Client"])
        self.assertEqual((path / "fix.txt").read_text(), "saved work")

    WIP = "wip(item-1): worker exited without finishing"

    def test_commit_wip_commits_a_failed_items_work_to_its_branch(self):
        path = self.trees.add("Farm-Client", "item-1", "farmbot/farm-1")
        before = git("rev-parse", "HEAD", cwd=path)
        (path / "fix.txt").write_text("hive fix\n", encoding="utf-8")
        report = self.trees.commit_wip("item-1", self.WIP)
        self.assertEqual(report["errors"], {})
        self.assertEqual(list(report["committed"]), ["Farm-Client"])
        self.assertNotEqual(git("rev-parse", "HEAD", cwd=path), before)
        self.assertEqual(git("rev-parse", "--abbrev-ref", "HEAD", cwd=path), "farmbot/farm-1")
        self.assertIn(self.WIP, git("log", "-1", "--pretty=%B", cwd=path))
        self.assertIn("fix.txt", git("show", "--name-only", "--pretty=format:", "HEAD", cwd=path))
        clone = self.trees.clone_path("Farm-Client")
        self.trees.remove("item-1")  # the evidence must outlive the sweep, in FarmBot's own clone
        self.assertEqual(git("rev-parse", "farmbot/farm-1", cwd=clone), report["committed"]["Farm-Client"])

    def test_commit_wip_makes_no_commit_without_work_and_none_at_all_without_a_worktree(self):
        path = self.trees.add("Farm-Client", "item-1", "farmbot/farm-1")
        before = git("rev-parse", "HEAD", cwd=path)
        self.assertEqual(self.trees.commit_wip("item-1", self.WIP), {"committed": {}, "errors": {}})
        self.assertEqual(git("rev-parse", "HEAD", cwd=path), before)
        self.assertEqual(git("rev-list", "--count", "HEAD", cwd=path), "1")
        self.assertEqual(self.trees.commit_wip("never-launched", self.WIP), {"committed": {}, "errors": {}})

    def test_commit_wip_puts_a_detached_worktrees_work_on_a_branch_of_its_own(self):
        """A commit on a detached head is referenced by nothing once the worktree is pruned, so it would be
        swept exactly as surely as the files it was meant to preserve."""
        path = self.trees.add_detached("Farm-Client", "item-1")
        (path / "notes.md").write_text("what I found\n", encoding="utf-8")
        report = self.trees.commit_wip("item-1", self.WIP)
        clone = self.trees.clone_path("Farm-Client")
        self.trees.remove("item-1")
        self.assertEqual(git("rev-parse", "farmbot/wip/item-1", cwd=clone), report["committed"]["Farm-Client"])

    def test_commit_wip_reports_a_broken_worktree_and_still_commits_the_others(self):
        good = self.trees.add("Farm-Client", "item-1", "farmbot/farm-1")
        (good / "fix.txt").write_text("hive fix\n", encoding="utf-8")
        broken = self.trees.worktrees_root / "item-1" / "Broken"
        broken.mkdir()
        (broken / ".git").write_text("gitdir: /nonexistent\n", encoding="utf-8")
        report = self.trees.commit_wip("item-1", self.WIP)
        self.assertEqual(list(report["committed"]), ["Farm-Client"])
        self.assertIn("Broken", report["errors"])
        self.assertTrue(report["errors"]["Broken"])

    def test_detached_worktree_for_read_only_skills(self):
        path = self.trees.add_detached("Farm-Client", "item-9")
        self.assertEqual(git("rev-parse", "--abbrev-ref", "HEAD", cwd=path), "HEAD")
        self.assertEqual(self.trees.add_detached("Farm-Client", "item-9"), path)

    def test_git_calls_skip_lfs_smudge_and_never_prompt(self):
        from unittest.mock import patch
        from agent.worktrees import _git
        with patch("agent.worktrees.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "ok\n"
            self.assertEqual(_git("status", cwd=self.tmp.name), "ok")
        env = run.call_args.kwargs["env"]
        self.assertEqual((env["GIT_LFS_SKIP_SMUDGE"], env["GIT_TERMINAL_PROMPT"]), ("1", "0"))
        self.assertIn("PATH", env)  # the real environment is kept, only extended

    def test_a_clone_can_be_seeded_from_a_local_checkout_before_its_first_origin_fetch(self):
        root = Path(self.tmp.name)
        checkout = root / "checkout"
        git("clone", "-q", str(self.origin), str(checkout), cwd=root)
        (self.origin / "later.txt").write_text("later", encoding="utf-8")
        git("add", ".", cwd=self.origin)
        git("commit", "-qm", "after the checkout was made", cwd=self.origin)
        clone = self.trees.ensure_clone("Farm-Client", seed_from=checkout)
        refs = git("for-each-ref", "--format=%(refname:short)", "refs/remotes/origin", cwd=clone)
        self.assertIn("origin/main", refs)
        # the origin fetch still runs, so the commit made after the checkout is present too
        self.assertEqual(git("rev-parse", "origin/main", cwd=clone), git("rev-parse", "HEAD", cwd=self.origin))

    def test_seeding_from_a_path_that_is_not_a_repository_is_ignored(self):
        missing = Path(self.tmp.name) / "nowhere"
        clone = self.trees.ensure_clone("Farm-Client", seed_from=missing)
        self.assertEqual(git("rev-parse", "--is-bare-repository", cwd=clone), "true")

    def test_the_seed_fetch_runs_before_the_origin_fetch_and_names_the_checkout(self):
        """The end state cannot distinguish a seeded clone from a plain one, so observe the calls."""
        root = Path(self.tmp.name)
        checkout = root / "checkout"
        git("clone", "-q", str(self.origin), str(checkout), cwd=root)
        calls = []
        real = agent.worktrees._git

        def recording(*args, cwd):
            calls.append(args)
            return real(*args, cwd=cwd)

        with patch("agent.worktrees._git", recording):
            self.trees.ensure_clone("Farm-Client", seed_from=checkout)
        fetches = [a for a in calls if a[0] == "fetch"]
        self.assertEqual(len(fetches), 2)
        self.assertIn(str(checkout.resolve()), fetches[0])  # resolved: /var/folders is a symlink on macOS
        self.assertIn("+refs/remotes/origin/*:refs/remotes/origin/*", fetches[0])
        self.assertIn("origin", fetches[1])

    def test_no_seed_fetch_happens_without_a_seed_path(self):
        calls = []
        real = agent.worktrees._git

        def recording(*args, cwd):
            calls.append(args)
            return real(*args, cwd=cwd)

        with patch("agent.worktrees._git", recording):
            self.trees.ensure_clone("Farm-Client")
        self.assertEqual([a for a in calls if a[0] == "fetch"], [("fetch", "--quiet", "--prune", "origin")])

    def test_a_failed_fetch_leaves_nothing_a_later_run_would_mistake_for_a_clone(self):
        root = Path(self.tmp.name)
        repos = root / "repos-broken"
        trees = Worktrees(repos, root / "wt-broken", {"Farm-Client": str(root / "no-such-origin.git")})
        with self.assertRaises(WorktreeError):
            trees.ensure_clone("Farm-Client")
        self.assertFalse(trees.clone_path("Farm-Client").exists())
        self.assertEqual(sorted(p.name for p in repos.iterdir()), [])  # not even a staging directory

    def test_a_relative_seed_path_is_resolved_before_git_sees_it(self):
        root = Path(self.tmp.name)
        checkout = root / "checkout"
        git("clone", "-q", str(self.origin), str(checkout), cwd=root)
        calls = []
        real = agent.worktrees._git

        def recording(*args, cwd):
            calls.append(args)
            return real(*args, cwd=cwd)

        with contextlib.chdir(root), patch("agent.worktrees._git", recording):
            # git would resolve a relative path against the clone directory, not against our cwd.
            # Use the fixture's drive: Windows may place temp state on C: and the checkout on D:.
            self.trees.ensure_clone("Farm-Client", seed_from=os.path.relpath(checkout))
        seed_fetch = next(a for a in calls if a[0] == "fetch" and "origin" not in a)
        self.assertIn(str(checkout.resolve()), seed_fetch)

    def test_resolve_commit_returns_the_remote_default_head_as_forty_hex(self):
        commit = self.trees.resolve_commit("Farm-Client")
        self.assertRegex(commit, r"^[0-9a-f]{40}$")
        self.assertEqual(commit, git("rev-parse", "HEAD", cwd=self.origin))

    def test_resolve_commit_refuses_a_ref_that_does_not_exist(self):
        with self.assertRaises(WorktreeError):
            self.trees.resolve_commit("Farm-Client", "origin/no-such-branch")

    def test_remote_head_resolves_without_cloning_or_fetching(self):
        trees = Worktrees(Path(self.tmp.name) / "empty-repos", self.trees.worktrees_root,
                          {"Farm-Client": str(self.origin)})
        self.assertEqual(trees.remote_head("Farm-Client"), git("rev-parse", "HEAD", cwd=self.origin))
        self.assertFalse((Path(self.tmp.name) / "empty-repos" / "Farm-Client.git" / "HEAD").exists())

    def test_a_slot_worktree_is_detached_at_the_commit_and_lives_where_it_is_told(self):
        # Unity rewrites .vscode/settings.json with the *folder* name on every Editor run (Task 0 Step 3), so
        # the slot must stop tracking it or every switch fails its clean check. The file is created on the
        # origin here because the fixture's repository does not carry one.
        (self.origin / ".vscode").mkdir()
        (self.origin / ".vscode" / "settings.json").write_text(
            '{"dotnet.defaultSolution": "Farm-Client.slnx"}\n', encoding="utf-8")
        git("add", ".", cwd=self.origin)
        git("commit", "-qm", "vscode", cwd=self.origin)
        commit = self.trees.resolve_commit("Farm-Client")
        slot = Path(self.tmp.name) / "editors" / "slot-1"
        self.assertEqual(self.trees.add_slot("Farm-Client", slot, commit), slot)
        self.assertEqual(git("rev-parse", "HEAD", cwd=slot), commit)
        self.assertEqual(git("symbolic-ref", "-q", "HEAD", cwd=slot, allow_failure=True), "")
        self.assertTrue(self.trees.slot_clean(slot))
        # 'S' in ls-files -v is the skip-worktree bit; the lowercase letters are assume-unchanged (-v) and
        # fsmonitor-clean (-f), which are different bits this task does not set.
        self.assertIn("S .vscode/settings.json", git("ls-files", "-v", ".vscode/settings.json", cwd=slot))
        (slot / ".vscode" / "settings.json").write_text('{"dotnet.defaultSolution": "slot-1.slnx"}\n',
                                                        encoding="utf-8")
        self.assertTrue(self.trees.slot_clean(slot))   # the Editor's rewrite no longer dirties the slot

    def test_slot_git_runs_without_the_pointer_preserving_environment(self):
        seen = []
        real = subprocess.run

        def record(args, **kwargs):
            seen.append((args[1] if args[0] == "git" else args[0], dict(kwargs.get("env") or {})))
            return real(args, **kwargs)

        commit = self.trees.resolve_commit("Farm-Client")
        # Both calls must be inside the patch, or the second list is empty and the comparison is [] == ['1'].
        with patch("agent.worktrees.subprocess.run", record):
            self.trees.add_slot("Farm-Client", Path(self.tmp.name) / "editors" / "slot-2", commit)
            slot_calls = [env for name, env in seen if name == "worktree"]
            self.trees.add("Farm-Client", "item-1", "farmbot/x")
            task_calls = [env for name, env in seen if name == "worktree"][len(slot_calls):]
        self.assertTrue(slot_calls and task_calls)
        # _git merges os.environ, so the slot map must set the value to "0" rather than leave the key out:
        # an operator shell exporting GIT_LFS_SKIP_SMUDGE=1 would otherwise win and Unity would get pointers.
        self.assertEqual({env.get("GIT_LFS_SKIP_SMUDGE") for env in slot_calls}, {"0"})
        self.assertEqual({env.get("GIT_LFS_SKIP_SMUDGE") for env in task_calls}, {"1"})

    def test_a_slot_that_cannot_be_built_tells_credentials_from_reachability(self):
        """With smudge on the LFS download happens inside `git worktree add`, so that is where a 401 or an
        unreachable origin surfaces on a fresh host — not inside materialize's `git lfs fetch`."""
        real = agent.worktrees._git
        slot = Path(self.tmp.name) / "editors" / "slot-3"
        commit = self.trees.resolve_commit("Farm-Client")

        def failing(message):
            def fake(*args, cwd, **kwargs):
                if args[0] == "worktree":
                    raise WorktreeError(f"git worktree failed: {message}")
                return real(*args, cwd=cwd, **kwargs)
            return fake

        for message, kind in (("HTTP 401 Authorization required", "credentials"),
                              ("Failed to connect to git.kuaiwa.com port 443: Connection refused", "reachability")):
            with self.subTest(kind=kind), patch("agent.worktrees._git", failing(message)):
                with self.assertRaises(WorktreeError) as caught:
                    self.trees.add_slot("Farm-Client", slot, commit)
                self.assertIn(f"({kind})", str(caught.exception))
                self.assertIn(message, str(caught.exception))
        # A failure that is neither is not dressed up as one: a bad reference is the operator's third problem.
        with patch("agent.worktrees._git", failing("fatal: invalid reference")):
            with self.assertRaises(WorktreeError) as caught:
                self.trees.add_slot("Farm-Client", slot, commit)
        self.assertNotIn("(credentials)", str(caught.exception))
        self.assertNotIn("(reachability)", str(caught.exception))

    def test_remote_head_caches_a_failure_so_a_burst_of_events_pays_one_timeout(self):
        """The receiver drains events serially: an unreachable origin must cost one ls-remote, not one each."""
        root = Path(self.tmp.name)
        trees = Worktrees(root / "repos-unreachable", self.trees.worktrees_root,
                          {"Farm-Client": str(root / "no-such-origin.git")})
        calls = []
        real = agent.worktrees._git

        def recording(*args, **kwargs):
            calls.append(args)
            return real(*args, **kwargs)

        with patch("agent.worktrees._git", recording):
            for _ in range(3):
                with self.assertRaises(WorktreeError):
                    trees.remote_head("Farm-Client")
        self.assertEqual([a[0] for a in calls], ["ls-remote"])

    def test_a_slot_that_cannot_be_moved_tells_credentials_from_reachability_too(self):
        """checkout_commit is smudge-on exactly as add_slot is, so a 401 or an unreachable origin surfaces
        here on every switch after the first — and Task 4's switch wraps it in a SlotError the operator
        reads. Unlabelled, it says only "git checkout failed", which is the one message that does not tell
        the operator which of their two problems they have."""
        slot = Path(self.tmp.name) / "editors" / "slot-4"
        commit = self.trees.resolve_commit("Farm-Client")
        self.trees.add_slot("Farm-Client", slot, commit)
        real = agent.worktrees._git

        def failing(message):
            def fake(*args, cwd, **kwargs):
                if args[0] == "checkout":
                    raise WorktreeError(f"git checkout failed: {message}")
                return real(*args, cwd=cwd, **kwargs)
            return fake

        for message, kind in (("HTTP 401 Authorization required", "credentials"),
                              ("Failed to connect to git.kuaiwa.com port 443: Connection refused", "reachability")):
            with self.subTest(kind=kind), patch("agent.worktrees._git", failing(message)):
                with self.assertRaises(WorktreeError) as caught:
                    self.trees.checkout_commit(slot, commit)
                self.assertIn(f"({kind})", str(caught.exception))
                self.assertIn(message, str(caught.exception))
        # A bad reference is the operator's third problem and is re-raised exactly as git worded it.
        with patch("agent.worktrees._git", failing("fatal: reference is not a tree: deadbeef")):
            with self.assertRaises(WorktreeError) as caught:
                self.trees.checkout_commit(slot, commit)
        self.assertNotIn("(credentials)", str(caught.exception))
        self.assertNotIn("(reachability)", str(caught.exception))


class ReattachTests(unittest.TestCase):
    """A job with an initial root goes back to the issue branch its plan records (spec §5.7 "Re-attachment")."""
    setUp = WorktreeTests.setUp

    def commit(self, path, name, text, message):
        (path / name).write_text(text, encoding="utf-8")
        git("add", ".", cwd=path)
        git("commit", "-qm", message, cwd=path)
        return git("rev-parse", "HEAD", cwd=path)

    def pushed_by_someone_else(self, branch, name, text):
        """A commit a person pushes to FarmBot's branch, made in the origin itself."""
        git("checkout", "-q", branch, cwd=self.origin)
        commit = self.commit(self.origin, name, text, f"add {name}")
        git("checkout", "-q", "main", cwd=self.origin)
        return commit

    def ended(self, item, trees=None):
        """What cleanup leaves of an item: its work as recovery refs, no worktrees, its local branches kept."""
        trees = trees or self.trees
        saved = trees.preserve(item)
        self.assertEqual(saved["errors"], {})
        trees.remove_preserved(item, saved)
        return saved

    def pushed_and_ended(self):
        """farmbot/farm-1 pushed by item-1, then moved on by someone else's push, and item-1 cleaned up."""
        first = self.trees.add("Farm-Client", "item-1", "farmbot/farm-1")
        self.commit(first, "contract.md", "contract change\n", "contract change")
        git("push", "-q", "origin", "HEAD:refs/heads/farmbot/farm-1", cwd=first)
        theirs = self.pushed_by_someone_else("farmbot/farm-1", "review.md", "reviewer fix\n")
        self.ended("item-1")
        return theirs

    def test_a_successor_goes_back_to_the_pushed_branch_with_what_others_pushed_on_it(self):
        theirs = self.pushed_and_ended()
        # Without re-attachment a later item gets a copy of the name, from the default branch (fix keeps this).
        copy = self.trees.add("Farm-Client", "item-2", "farmbot/farm-1")
        self.assertEqual(git("branch", "--show-current", cwd=copy), "farmbot/farm-1-item-2")
        self.assertFalse((copy / "contract.md").exists())
        path = self.trees.add("Farm-Client", "item-3", "farmbot/farm-1", attach=True)
        self.assertEqual(git("branch", "--show-current", cwd=path), "farmbot/farm-1")
        self.assertEqual(self.trees.head(path), theirs)  # moved forward to the remote, others' commit included
        self.assertEqual((path / "contract.md").read_text(encoding="utf-8"), "contract change\n")
        self.assertEqual(git("rev-parse", "--abbrev-ref", "@{upstream}", cwd=path), "origin/farmbot/farm-1")

    def test_a_branch_with_commits_of_its_own_is_kept_for_the_worker_to_integrate(self):
        first = self.trees.add("Farm-Client", "item-1", "farmbot/farm-1")
        self.commit(first, "pushed.md", "pushed\n", "pushed")
        git("push", "-q", "origin", "HEAD:refs/heads/farmbot/farm-1", cwd=first)
        ours = self.commit(first, "unpushed.md", "not pushed yet\n", "unpushed")
        theirs = self.pushed_by_someone_else("farmbot/farm-1", "review.md", "reviewer fix\n")
        self.ended("item-1")
        path = self.trees.add("Farm-Client", "item-2", "farmbot/farm-1", attach=True)
        self.assertEqual((git("branch", "--show-current", cwd=path), self.trees.head(path)),
                         ("farmbot/farm-1", ours))
        self.assertEqual(git("rev-list", "--left-right", "--count", "HEAD...@{upstream}", cwd=path), "1\t1")
        self.assertEqual(git("rev-parse", "farmbot/farm-1", cwd=self.origin), theirs)  # nothing was pushed or reset

    def test_a_cleaned_up_continuation_goes_back_to_its_branch_at_the_preserved_work(self):
        path = self.trees.add("Farm-Client", "item-1", "farmbot/farm-1")
        self.commit(path, "pushed.md", "pushed\n", "pushed")
        git("push", "-q", "origin", "HEAD:refs/heads/farmbot/farm-1", cwd=path)
        (path / "draft.md").write_text("uncommitted draft\n", encoding="utf-8")
        saved = self.ended("item-1")
        path = self.trees.add("Farm-Client", "item-1", "farmbot/farm-1", attach=True)
        self.assertEqual(git("branch", "--show-current", cwd=path), "farmbot/farm-1")  # not farmbot/farm-1-item-1
        self.assertEqual(self.trees.head(path), saved["committed"]["Farm-Client"])
        self.assertEqual((path / "draft.md").read_text(encoding="utf-8"), "uncommitted draft\n")
        self.assertEqual(git("rev-parse", "--abbrev-ref", "@{upstream}", cwd=path), "origin/farmbot/farm-1")
        self.assertEqual(self.trees.add("Farm-Client", "item-1", "farmbot/farm-1", attach=True), path)  # kept as it is

    def test_a_branch_only_the_remote_has_is_tracked_and_one_found_nowhere_starts_from_main(self):
        git("checkout", "-qb", "farmbot/farm-1", cwd=self.origin)
        remote = self.commit(self.origin, "x.md", "x\n", "on the remote only")
        git("checkout", "-q", "main", cwd=self.origin)
        path = self.trees.add("Farm-Client", "item-1", "farmbot/farm-1", attach=True)
        self.assertEqual((git("branch", "--show-current", cwd=path), self.trees.head(path)),
                         ("farmbot/farm-1", remote))
        self.assertEqual(git("rev-parse", "--abbrev-ref", "@{upstream}", cwd=path), "origin/farmbot/farm-1")
        fresh = self.trees.add("Farm-Client", "item-2", "farmbot/farm-1-config", attach=True)
        self.assertEqual(git("branch", "--show-current", cwd=fresh), "farmbot/farm-1-config")
        self.assertEqual(self.trees.head(fresh), git("rev-parse", "main", cwd=self.origin))

    def test_a_branch_another_worktree_has_checked_out_is_refused(self):
        self.trees.add("Farm-Client", "item-1", "farmbot/farm-1")
        with self.assertRaisesRegex(WorktreeError, "checked out in another worktree"):
            self.trees.add("Farm-Client", "item-2", "farmbot/farm-1", attach=True)
        self.assertFalse((self.trees.worktrees_root / "item-2").exists())

    def test_re_attachment_under_paths_with_spaces_and_chinese_characters(self):
        root = Path(self.tmp.name)
        trees = Worktrees(root / "克隆 repos", root / "工作 worktrees", {"Farm-Client": str(self.origin)})
        first = trees.add("Farm-Client", "item-1", "farmbot/farm-1-材料商店")
        pushed = self.commit(first, "说明.md", "改动\n", "中文提交")
        git("push", "-q", "origin", "HEAD:refs/heads/farmbot/farm-1-材料商店", cwd=first)
        self.ended("item-1", trees)
        path = trees.add("Farm-Client", "item-2", "farmbot/farm-1-材料商店", attach=True)
        self.assertEqual((git("branch", "--show-current", cwd=path), trees.head(path)),
                         ("farmbot/farm-1-材料商店", pushed))

    def test_every_git_call_of_re_attachment_runs_with_hooks_and_fsmonitor_off(self):
        """Plan P10, on every platform: each call re-attachment makes in the clone carries HOOKS_OFF, in all three
        cases (the clone's own branch moved forward, a branch only the remote has, and one found nowhere)."""
        self.pushed_and_ended()
        git("branch", "farmbot/farm-1-remote", cwd=self.origin)
        calls = []
        real = agent.worktrees._git

        def recording(*args, cwd, **kwargs):
            calls.append((args[0], kwargs.get("config", ())))
            return real(*args, cwd=cwd, **kwargs)

        with patch("agent.worktrees._git", recording):
            for item, branch in (("item-2", "farmbot/farm-1"), ("item-3", "farmbot/farm-1-remote"),
                                 ("item-4", "farmbot/farm-1-new")):
                self.trees.add("Farm-Client", item, branch, attach=True)
        self.assertEqual({config for _, config in calls}, {agent.worktrees.HOOKS_OFF})
        self.assertEqual({name for name, _ in calls},
                         {"fetch", "for-each-ref", "worktree", "rev-list", "update-ref", "branch", "ls-remote"})

    @unittest.skipIf(os.name == "nt", "the planted hooks and fsmonitor are shell scripts")
    def test_no_hook_or_fsmonitor_planted_in_the_clone_runs_while_re_attaching(self):
        """Plan P10. The control shows the planted scripts do run in today's add, which Phase B leaves as it is
        (the plan's Known Risks)."""
        marker = Path(self.tmp.name) / "ran.txt"
        self.pushed_and_ended()
        clone = self.trees.ensure_clone("Farm-Client")
        # What a worker rooted in this repository can write into FarmBot's clone of it.
        (clone / "hooks").mkdir(exist_ok=True)
        for name in ("post-checkout", "reference-transaction"):
            (clone / "hooks" / name).write_text(f"#!/bin/sh\necho {name} >> '{marker}'\n", encoding="utf-8")
            (clone / "hooks" / name).chmod(0o755)
        fsmonitor = Path(self.tmp.name) / "fsmonitor.sh"
        fsmonitor.write_text(f"#!/bin/sh\necho fsmonitor >> '{marker}'\nexit 1\n", encoding="utf-8")
        fsmonitor.chmod(0o755)
        git("config", "core.fsmonitor", str(fsmonitor), cwd=clone)
        self.trees.add("Farm-Client", "control", "farmbot/control")
        self.assertIn("post-checkout", marker.read_text(encoding="utf-8"))
        marker.unlink()
        path = self.trees.add("Farm-Client", "item-2", "farmbot/farm-1", attach=True)
        self.assertFalse(marker.exists())
        self.assertEqual(git("branch", "--show-current", cwd=path), "farmbot/farm-1")


class ReadCheckoutTests(unittest.TestCase):
    """Read-only default-branch checkouts for a manifest's `reads` (spec §8.3, §9.6), here of two repositories."""
    POINTER = "version https://git-lfs.github.com/spec/v1\noid sha256:" + "4" * 64 + "\nsize 12\n"
    # Commits made here keep a pointer a pointer, whatever LFS filter the machine running the tests has.
    NO_LFS = ("-c", "filter.lfs.process=", "-c", "filter.lfs.clean=", "-c", "filter.lfs.required=false")

    def setUp(self):
        WorktreeTests.setUp(self)
        root = Path(self.tmp.name)
        # Farm-Client tracks binaries through LFS: one file, committed as its pointer.
        (self.origin / ".gitattributes").write_text("*.bytes filter=lfs diff=lfs merge=lfs -text\n", encoding="utf-8")
        (self.origin / "config.bytes").write_text(self.POINTER, encoding="utf-8")
        git(*self.NO_LFS, "add", ".", cwd=self.origin)
        git("commit", "-qm", "track config data through LFS", cwd=self.origin)
        self.contract = root / "contract origin"
        self.contract.mkdir()
        git("init", "-q", "-b", "main", ".", cwd=self.contract)
        (self.contract / "farm.proto").write_text('syntax = "proto3";\n', encoding="utf-8")
        git("add", ".", cwd=self.contract)
        git("commit", "-qm", "init", cwd=self.contract)
        self.trees = Worktrees(root / "repos", root / "worktrees",
                               {"Farm-Client": str(self.origin), "Farm-Contract": str(self.contract)})

    def main(self, origin):
        return git("rev-parse", "main", cwd=origin)

    def contract_moves_on(self):
        (self.contract / "farm.proto").unlink()
        (self.contract / "later.proto").write_text('syntax = "proto3";\n', encoding="utf-8")
        git("add", "-A", cwd=self.contract)
        git("commit", "-qm", "main moves on", cwd=self.contract)
        return self.main(self.contract)

    def alternates(self, path):
        return (path / ".git" / "objects" / "info" / "alternates").read_text(encoding="utf-8")

    def test_made_beside_the_item_directory_refreshed_at_each_call_and_removed_on_request(self):
        work = self.trees.add("Farm-Client", "item-1", "farmbot/farm-1")
        root = self.trees.worktrees_root / "item-1.reads"
        paths = {repo: self.trees.read_checkout(repo, "item-1") for repo in ("Farm-Contract", "Farm-Client")}
        self.assertEqual(paths, {"Farm-Contract": root / "Farm-Contract@main", "Farm-Client": root / "Farm-Client@main"})
        self.assertFalse(root.is_relative_to(work.parent))
        for repo, origin in (("Farm-Contract", self.contract), ("Farm-Client", self.origin)):
            with self.subTest(repo=repo):
                path = paths[repo]
                self.assertEqual(self.trees.head(path), self.main(origin))
                self.assertEqual(git("symbolic-ref", "-q", "HEAD", cwd=path, allow_failure=True), "")  # detached
                # What farm-hive's proto sync asks of a contract checkout: origin/main, and HEAD on it.
                self.assertEqual(git("rev-parse", "origin/main", cwd=path), self.main(origin))
                self.assertEqual(Path(git("rev-parse", "--absolute-git-dir", cwd=path)).resolve(),
                                 (path / ".git").resolve())
        self.assertEqual((paths["Farm-Client"] / "config.bytes").read_text(encoding="utf-8"), self.POINTER)
        later = self.contract_moves_on()
        self.assertEqual(self.trees.read_checkout("Farm-Contract", "item-1"), paths["Farm-Contract"])
        self.assertEqual(self.trees.head(paths["Farm-Contract"]), later)
        self.assertEqual(sorted(p.name for p in paths["Farm-Contract"].iterdir()), [".git", "later.proto"])
        # The item's own cleanup neither sees them nor refuses them: every entry under item-1/ is a clone's worktree.
        saved = self.trees.preserve("item-1")
        self.assertEqual((saved["errors"], list(saved["refs"])), ({}, ["Farm-Client"]))
        self.trees.remove_preserved("item-1", saved)
        self.assertEqual(git("for-each-ref", "refs/farmbot", cwd=paths["Farm-Client"]), "")  # no recovery ref
        self.trees.remove_reads("item-1")
        self.assertFalse(root.exists())
        self.trees.remove_reads("item-1")  # nothing left: nothing to do

    def test_each_checkout_borrows_its_clones_objects_and_fetches_what_the_clone_lacks(self):
        for repo in ("Farm-Contract", "Farm-Client"):
            with self.subTest(repo=repo):
                path = self.trees.read_checkout(repo, "item-1")
                self.assertEqual(self.alternates(path), f"{self.trees.clone_path(repo) / 'objects'}\n")
                counts = git("count-objects", "-v", cwd=path).splitlines()
                self.assertLessEqual({"count: 0", "in-pack: 0"}, set(counts))  # nothing copied from the remote
                self.assertEqual(git("config", "--local", "--get", "remote.origin.url", cwd=path),
                                 self.trees.remotes[repo])
        later = self.contract_moves_on()
        path = self.trees.read_checkout("Farm-Contract", "item-1")
        self.assertEqual(self.trees.head(path), later)
        clone = self.trees.clone_path("Farm-Contract")
        self.assertNotEqual(git("rev-parse", "refs/remotes/origin/main", cwd=clone), later)  # the clone never fetched

    def test_every_git_call_runs_with_hooks_fsmonitor_and_the_lfs_filter_off(self):
        """The check that runs on every platform: each call carries READ_ONLY_GIT."""
        for repo in ("Farm-Contract", "Farm-Client"):
            self.trees.ensure_clone(repo)  # a missing clone is made as a first worktree makes it
        calls = []
        real = agent.worktrees._git

        def recording(*args, cwd, **kwargs):
            calls.append((args[0], kwargs.get("config", ())))
            return real(*args, cwd=cwd, **kwargs)

        with patch("agent.worktrees._git", recording):
            for repo in ("Farm-Contract", "Farm-Client"):
                self.trees.read_checkout(repo, "item-1")
                self.trees.read_checkout(repo, "item-1")
        self.assertEqual({config for _, config in calls}, {agent.worktrees.READ_ONLY_GIT})
        names = [name for name, _ in calls]
        self.assertEqual((names.count("init"), names.count("fetch")), (2, 4))  # made once each, fetched each call

    @unittest.skipIf(os.name == "nt", "the hooks, fsmonitor and filters here are shell scripts")
    def test_no_hook_fsmonitor_filter_or_setting_of_the_host_or_the_clone_runs(self):
        root = Path(self.tmp.name)
        marker = root / "ran.txt"

        def script(name, body=""):
            path = root / "scripts" / name
            path.parent.mkdir(exist_ok=True)
            path.write_text(f"#!/bin/sh\necho {name} >> '{marker}'\n{body}", encoding="utf-8")
            path.chmod(0o755)
            return path

        hooks = root / "hooks"
        hooks.mkdir()
        for name in ("post-checkout", "reference-transaction"):
            script(name).rename(hooks / name)
        host = root / "host.gitconfig"
        host.write_text(f"[core]\n\thooksPath = {hooks}\n\tfsmonitor = {script('fsmonitor', 'exit 1')}\n"
                        f"[filter \"lfs\"]\n\tsmudge = {script('smudge', 'cat >/dev/null; echo SMUDGED')} %f\n"
                        f"\tclean = {script('clean', 'cat')} %f\n\trequired = true\n", encoding="utf-8")
        clone = self.trees.ensure_clone("Farm-Client")
        # What a worker rooted in this repository can write into FarmBot's clone of it.
        git("config", f"url.{root / 'elsewhere'}.insteadOf", str(self.origin), cwd=clone)
        git("config", "core.hooksPath", str(hooks), cwd=clone)
        git("config", "core.fsmonitor", str(script("clone-fsmonitor", "exit 1")), cwd=clone)
        git("config", "filter.lfs.smudge", f"{script('clone-smudge', 'cat')} %f", cwd=clone)
        (clone / "info").mkdir(exist_ok=True)
        (clone / "info" / "attributes").write_text("* filter=lfs\n", encoding="utf-8")
        with patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(host)}):
            # The control clone reads only the host config above: a system config can enable git-lfs's process
            # filter, which git prefers to the host's smudge (CI's macOS runner has one). The read-only checkouts
            # below run with whatever the machine's system config enables as well.
            with patch.dict(os.environ, {"GIT_CONFIG_NOSYSTEM": "1"}):
                git("clone", "-q", str(self.origin), str(root / "control"), cwd=root)
            ran = set(marker.read_text(encoding="utf-8").split())
            self.assertLessEqual({"post-checkout", "smudge"}, ran)  # the host's hook and filter do run elsewhere
            marker.unlink()
            path = self.trees.read_checkout("Farm-Client", "item-1")
            self.trees.read_checkout("Farm-Client", "item-1")
        self.assertFalse(marker.exists())
        self.assertEqual(self.trees.head(path), self.main(self.origin))  # from the real origin, not "elsewhere"
        self.assertEqual((path / "config.bytes").read_text(encoding="utf-8"), self.POINTER)
        keys = set(git("config", "--local", "--name-only", "--list", cwd=path).splitlines())
        self.assertEqual({key for key in keys if not key.startswith("core.")}, {"remote.origin.url"})
        self.assertFalse(keys & {"core.hookspath", "core.fsmonitor"})

    def test_a_publication_retry_reuses_the_checkout_without_a_fetch(self):
        path = self.trees.read_checkout("Farm-Contract", "item-1")
        calls = []
        real = agent.worktrees._git

        def recording(*args, cwd, **kwargs):
            calls.append(args[0])
            return real(*args, cwd=cwd, **kwargs)

        with patch("agent.worktrees._git", recording):
            self.assertEqual(self.trees.read_checkout("Farm-Contract", "item-1", refresh=False), path)
        self.assertFalse({"ls-remote", "fetch", "checkout"} & set(calls))

    def test_paths_with_spaces_and_chinese_characters(self):
        root = Path(self.tmp.name)
        trees = Worktrees(root / "克隆 repos", root / "工作 worktrees",
                          {"Farm-Contract": str(self.contract), "Farm-Client": str(self.origin)})
        for repo, name in (("Farm-Contract", "farm.proto"), ("Farm-Client", "README.md")):
            with self.subTest(repo=repo):
                path = trees.read_checkout(repo, "item-1")
                self.assertEqual(path, root / "工作 worktrees" / "item-1.reads" / f"{repo}@main")
                self.assertTrue((path / name).is_file())
                self.assertEqual(self.alternates(path), f"{root / '克隆 repos' / f'{repo}.git' / 'objects'}\n")
        trees.remove_reads("item-1")
        self.assertFalse((root / "工作 worktrees" / "item-1.reads").exists())

    def test_anything_else_at_the_path_is_replaced_and_unsafe_requests_are_refused(self):
        path = self.trees.worktrees_root / "item-1.reads" / "Farm-Client@main"
        path.mkdir(parents=True)
        (path / "leftover.txt").write_text("half-made\n", encoding="utf-8")
        self.assertEqual(self.trees.read_checkout("Farm-Client", "item-1"), path)
        self.assertFalse((path / "leftover.txt").exists())
        git("config", "remote.origin.url", str(Path(self.tmp.name) / "another"), cwd=path)
        self.trees.read_checkout("Farm-Client", "item-1")
        self.assertEqual(git("config", "remote.origin.url", cwd=path), str(self.origin))  # rebuilt for its remote
        (path / ".git" / "objects" / "info" / "alternates").write_text(f"{self.tmp.name}/objects\n", encoding="utf-8")
        self.trees.read_checkout("Farm-Client", "item-1")
        self.assertEqual(self.alternates(path), f"{self.trees.clone_path('Farm-Client') / 'objects'}\n")
        with self.assertRaisesRegex(WorktreeError, "unknown repository"):
            self.trees.read_checkout("farmgui", "item-1")
        for item in ("", ".", "..", "a/b"):
            with self.subTest(item=item), self.assertRaisesRegex(WorktreeError, "unsafe item path"):
                self.trees.read_checkout("Farm-Client", item)
        try:
            (self.trees.worktrees_root / "item-2.reads").symlink_to(self.origin, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        with self.assertRaisesRegex(WorktreeError, "symlinked"):
            self.trees.read_checkout("Farm-Client", "item-2")
        with self.assertRaisesRegex(WorktreeError, "symlinked"):
            self.trees.remove_reads("item-2")
        self.assertTrue((self.origin / "README.md").exists())

    def test_the_alternates_file_ends_in_a_bare_newline_wherever_text_mode_translates(self):
        """git keeps a carriage return in an alternates entry and then ignores the store it names, so the checkout
        would fetch the whole history again and every git command in it would print an error. Text mode writes CRLF
        on Windows: the file is written with a bare newline everywhere, and a CRLF one found later is rebuilt."""
        real = Path.write_text

        def translating(path, data, encoding=None, errors=None, newline=None):  # text mode, as on Windows
            return real(path, data, encoding=encoding, errors=errors, newline="\r\n" if newline is None else newline)

        with patch.object(Path, "write_text", translating):
            path = self.trees.read_checkout("Farm-Contract", "item-1")
        alternates = path / ".git" / "objects" / "info" / "alternates"
        expected = f"{self.trees.clone_path('Farm-Contract') / 'objects'}\n".encode("utf-8")
        self.assertEqual(alternates.read_bytes(), expected)
        alternates.write_bytes(expected.replace(b"\n", b"\r\n"))  # as a checkout made on Windows before this fix
        self.trees.read_checkout("Farm-Contract", "item-1")
        self.assertEqual(alternates.read_bytes(), expected)
        self.assertLessEqual({"count: 0", "in-pack: 0"}, set(git("count-objects", "-v", cwd=path).splitlines()))

    @unittest.skipIf(os.name == "nt", "POSIX permissions; Windows has the junction test")
    def test_removal_never_acts_through_a_link(self):
        """rmtree never follows a link inside the tree, and its retry after a failure must not either: clearing
        read-only through a link would change whatever the link names, outside the tree. A link in place of the tree
        is refused by the helper itself, not only by its callers."""
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root ignores directory permissions")
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        victim = outside / "not FarmBot's.txt"
        victim.write_text("kept\n", encoding="utf-8")
        victim.chmod(0o644)
        locked = self.trees.worktrees_root / "item-3.reads" / "Farm-Client@main" / "locked"
        locked.mkdir(parents=True)
        (locked / "link").symlink_to(victim)
        locked.chmod(0o555)  # its entries cannot be unlinked
        self.addCleanup(locked.chmod, 0o755)
        with self.assertRaises(OSError):
            self.trees.remove_reads("item-3")
        self.assertEqual(stat.S_IMODE(victim.stat().st_mode), 0o644)
        link = self.trees.worktrees_root / "a link"
        link.symlink_to(outside, target_is_directory=True)
        mode = stat.S_IMODE(outside.stat().st_mode)
        with self.assertRaisesRegex(WorktreeError, "link"):
            agent.worktrees._remove_tree(link)
        self.assertEqual((stat.S_IMODE(outside.stat().st_mode), victim.is_file()), (mode, True))

    @unittest.skipUnless(os.name == "nt", "junctions are Windows'")
    def test_a_junction_is_refused_as_a_symlink_is(self):
        """Path.is_symlink is false for a junction, so the link checks look at reparse points too."""
        outside = Path(self.tmp.name) / "outside"
        (outside / "Farm-Client@main").mkdir(parents=True)
        kept = outside / "Farm-Client@main" / "kept.txt"
        kept.write_text("not FarmBot's\n", encoding="utf-8")
        junction = self.trees.worktrees_root / "item-2.reads"
        junction.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)], check=True, capture_output=True)
        with self.assertRaisesRegex(WorktreeError, "symlinked"):
            self.trees.read_checkout("Farm-Client", "item-2")
        with self.assertRaisesRegex(WorktreeError, "symlinked"):
            self.trees.remove_reads("item-2")
        with self.assertRaisesRegex(WorktreeError, "link"):
            agent.worktrees._remove_tree(junction)
        self.assertEqual(kept.read_text(encoding="utf-8"), "not FarmBot's\n")
