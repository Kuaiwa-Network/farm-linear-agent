import subprocess
import tempfile
import unittest
from pathlib import Path

from agent.ledger import Ledger
from agent.slots import SlotError, SlotPool, slot_entry
from agent.worktrees import WorktreeError, Worktrees


def git(*args, cwd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=str(cwd), check=True,
                   capture_output=True)


class SlotFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.origin = self.root / "origin"
        self.origin.mkdir(parents=True)
        git("init", "-q", "-b", "main", ".", cwd=self.origin)
        (self.origin / "README.md").write_text("client", encoding="utf-8")
        git("add", ".", cwd=self.origin); git("commit", "-qm", "init", cwd=self.origin)
        self.trees = Worktrees(self.root / "repos", self.root / "worktrees", {"Farm-Client": str(self.origin)})
        self.now = 1000.0
        self.ledger = Ledger(self.root / "ledger.sqlite3", clock=lambda: self.now, lease_seconds=60)
        self.addCleanup(self.ledger.close)
        self.entry = slot_entry({"id": "unity_slot:1", "repo": "Farm-Client"})

    def advance(self, seconds):
        """The injected sleep, wired into pool() by Task 4 when SlotPool.__init__ grows a `sleep` argument.
        The fixture's clock is a variable, so a pool loop that waits on a deadline makes progress instead of
        spinning forever under a constant clock."""
        self.now += seconds

    def pool(self, worktrees=None, **kwargs):
        # Task 4 extends this with the collaborators its constructor adds (sleep, editor_scan, editor_pid);
        # here it passes only what Task 3's constructor accepts, or every case below raises TypeError.
        return SlotPool(self.ledger, worktrees or self.trees, [self.entry], host="test",
                        editors_root=self.root / "editors", clock=lambda: self.now, **kwargs)

    def commit(self, message="more"):
        (self.origin / f"{message}.txt").write_text(message, encoding="utf-8")
        git("add", ".", cwd=self.origin); git("commit", "-qm", message, cwd=self.origin)
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.origin, capture_output=True,
                              text=True, check=True).stdout.strip()


class EnsureTests(SlotFixture):
    def test_ensure_registers_the_slot_and_creates_its_folder_parked_on_main(self):
        head = self.trees.resolve_commit("Farm-Client")
        [slot] = self.pool().ensure()
        # The default folder is slot-<n>, from the id's suffix. Every other assertion in this file, the
        # Architecture paragraph and Task 11's `du -sh .local/editors/slot-1` all depend on this one name.
        self.assertEqual(slot["folder"], str(self.root / "editors" / "slot-1"))
        self.assertEqual((slot["state"], slot["parked_commit"]), ("idle_closed", head))
        self.assertTrue((self.root / "editors" / "slot-1" / "README.md").is_file())

    def test_ensure_is_idempotent_and_never_disturbs_a_busy_slot(self):
        """The unchanged state is not enough on its own — with the busy guard deleted nothing sets the state
        either, so the assertion passes anyway. The spy is what has teeth: a restart must not fetch for, nor
        create, nor materialize the folder of a slot an Editor has open."""
        class Untouchable(Worktrees):
            def resolve_commit(self, repo, ref=None):
                raise AssertionError("ensure() fetched origin for a busy slot")

            def add_slot(self, repo, path, commit):
                raise AssertionError("ensure() touched a busy slot's folder")

        for state in SlotPool.BUSY:  # held is the one where moving the folder would do the most damage
            with self.subTest(state=state):
                self.pool().ensure()
                self.ledger.set_slot_state("unity_slot:1", state)
                self.commit(f"later-{state}")
                spy = Untouchable(self.root / "repos", self.root / "worktrees", {"Farm-Client": str(self.origin)})
                [slot] = self.pool(spy).ensure()
                self.assertEqual(slot["state"], state)
                self.ledger.set_slot_state("unity_slot:1", "idle_closed")

    def test_a_restart_parks_the_slot_on_the_commit_its_folder_is_really_at(self):
        """ensure() deliberately leaves an existing folder where it is: moving it would cost a Unity reimport
        at every service start. parked_commit must therefore be the folder's own head and not the newly
        resolved origin head, or Task 4's switch would read a commit the slot is not on and skip the checkout
        it actually needs."""
        first = self.pool().ensure()[0]
        moved = self.commit("after-the-restart")
        self.assertNotEqual(moved, first["parked_commit"])
        [slot] = self.pool().ensure()
        self.assertEqual(slot["parked_commit"], first["parked_commit"])
        self.assertEqual(self.trees.head(Path(slot["folder"])), slot["parked_commit"])

    def test_a_slot_whose_binaries_are_still_pointers_is_refused_by_name(self):
        class Pointers(Worktrees):
            def pointers_remain(self, path):
                return True

        trees = Pointers(self.root / "repos", self.root / "worktrees", {"Farm-Client": str(self.origin)})
        pool = SlotPool(self.ledger, trees, [self.entry], host="test", editors_root=self.root / "editors",
                        clock=lambda: self.now)
        with self.assertRaises(SlotError) as caught:
            pool.ensure()
        self.assertIn("lfs", str(caught.exception).lower())

    def test_a_git_failure_in_any_part_of_slot_creation_names_the_slot(self):
        """Each of the four parts fails loudly, and every one of them fails as a SlotError carrying the slot
        id: at service start a bare WorktreeError would not say which folder the operator has to look at."""
        class Broken(Worktrees):
            def pointers_remain(self, path):
                raise WorktreeError("git lfs ls-files failed: not a working copy")

        with self.assertRaises(SlotError) as caught:
            self.pool(Broken(self.root / "repos", self.root / "worktrees",
                             {"Farm-Client": str(self.origin)})).ensure()
        self.assertIn("unity_slot:1", str(caught.exception))
        self.assertIn("ls-files", str(caught.exception))
