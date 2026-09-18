import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agent.worktrees
from agent.worktrees import Worktrees


def git(*args, cwd):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


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
        self.assertIn(str(checkout), fetches[0])
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
