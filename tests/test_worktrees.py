import os
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

        with patch("agent.worktrees._git", recording):
            # git would resolve a relative path against the clone directory, not against our cwd.
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
        # _git merges os.environ, so assert the *value*: key absence would depend on the developer's shell.
        self.assertEqual({env.get("GIT_LFS_SKIP_SMUDGE") for env in slot_calls}, {None})
        self.assertEqual({env.get("GIT_LFS_SKIP_SMUDGE") for env in task_calls}, {"1"})

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
