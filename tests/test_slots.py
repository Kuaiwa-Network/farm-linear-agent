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
        # editor_scan and editor_pid are both stubbed: a developer with their own Unity Editor open would
        # otherwise fail every switch test on their machine, and neither test may shell out to pgrep.
        # editor_pid answers from the fake's open_folders, which is what makes it liveness rather than the
        # lock file — the whole point of Task 0 Step 5's +32 s finding.
        mcp = kwargs.get("mcp")
        kwargs.setdefault("editor_scan", lambda folder: None)
        kwargs.setdefault("editor_pid",
                          lambda folder: 4242 if mcp is not None and str(folder) in mcp.open_folders else None)
        return SlotPool(self.ledger, worktrees or self.trees, [self.entry], host="test",
                        editors_root=self.root / "editors", clock=lambda: self.now,
                        sleep=self.advance, **kwargs)

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


INSTANCE = "slot-1@0123456789abcdef"


class FakeMcp:
    """Stands in for the Unity MCP client: records what the pool asked the Editor to do, and stands in for
    the Editor *process* by keeping `open_folders` — which is what the injected `editor_pid` reads, exactly
    as `agent.unity.editor_holds_project` reads the real process listing.

    It also writes the lock file on start and deliberately does **not** remove it on terminate, because the
    real Editor does not either (Task 0 Step 5: still present at +32 s after the process was gone). The two
    are kept apart on purpose: `open_folders` is liveness, the lock file is the litter Unity leaves. The
    pool's own removal of that litter is what the close test proves, and a double that tidied up after
    itself would hide the bug the spike found."""

    def __init__(self, ready=True, quiet=True, console_errors=(), instance=INSTANCE):
        self.ready = ready
        self.quiet = quiet
        self.console_errors = list(console_errors)
        self.instance = instance
        self.calls = []
        self.open_folders = set()

    def _lock(self, slot):
        return Path(slot["folder"]) / "Temp" / "UnityLockfile"

    def start(self, slot, entry):
        self.calls.append(("start", slot["slot_id"]))
        self._lock(slot).parent.mkdir(parents=True, exist_ok=True)
        self._lock(slot).write_text("1", encoding="utf-8")
        self.open_folders.add(slot["folder"])
        return self.instance

    def terminate(self, slot, timeout):
        """SIGTERM and confirm the pid is gone. The lock file is left exactly where Unity leaves it."""
        self.calls.append(("terminate", slot["slot_id"]))
        self.open_folders.discard(slot["folder"])

    def reap_server(self, slot):
        """The uvx MCP server outlives the Editor and keeps port 8080 bound; the pool kills it by pidfile."""
        self.calls.append(("reap_server", slot["slot_id"]))

    def refresh(self, slot):
        self.calls.append(("refresh", slot["slot_id"]))

    def wait_quiet(self, slot, timeout):
        self.calls.append(("wait_quiet", slot["slot_id"]))
        if not self.quiet:
            raise TimeoutError("still compiling")

    def console_errors_since(self, slot, marker):
        self.calls.append(("console", slot["slot_id"]))
        return list(self.console_errors)

    def probe(self, slot, target):
        self.calls.append(("probe", slot["slot_id"]))
        return {"aggregate": "match" if self.ready else "mismatch",
                "checks": {"source_commit": "match" if self.ready else "mismatch"}}

    def quiescent(self, slot, mode):
        self.calls.append(("quiescent", slot["slot_id"]))
        return self.ready


class SwitchTests(SlotFixture):
    def test_a_batch_switch_moves_the_slot_to_the_commit_and_marks_it_busy(self):
        self.pool().ensure()
        commit = self.commit("fix")          # made on the origin *after* ensure: the switch must fetch
        pool = self.pool(mcp=FakeMcp())
        slot = pool.switch("unity_slot:1", commit, "batch")
        self.assertEqual(slot["state"], "batch_busy")
        self.assertEqual(self.trees.head(self.root / "editors" / "slot-1"), commit)
        self.assertEqual(pool.mcp.calls, [])  # a batch run on a closed slot needs no MCP at all (spec §7)

    def test_an_interactive_switch_on_a_closed_slot_starts_the_editor_before_it_probes(self):
        self.pool().ensure()
        commit = self.commit("fix")
        pool = self.pool(mcp=FakeMcp())
        slot = pool.switch("unity_slot:1", commit, "interactive")
        self.assertEqual(slot["state"], "interactive_busy")
        self.assertEqual([name for name, _ in pool.mcp.calls],
                         ["start", "refresh", "wait_quiet", "console", "probe"])
        # The instance the Editor reported is recorded, so the worker and the next probe can address it.
        self.assertEqual(self.ledger.slot("unity_slot:1")["instance"], INSTANCE)

    def test_an_interactive_switch_on_an_open_slot_does_not_restart_the_editor(self):
        self.pool().ensure()
        pool = self.pool(mcp=FakeMcp())
        pool.switch("unity_slot:1", self.commit("one"), "interactive")
        pool.park("unity_slot:1", "interactive")
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "idle_open")
        pool.mcp.calls.clear()
        pool.switch("unity_slot:1", self.commit("two"), "interactive")
        self.assertNotIn("start", [name for name, _ in pool.mcp.calls])

    def test_a_stale_lock_file_never_makes_a_dead_editor_look_open(self):
        """The brief argues at length that editor_is_open must be a process question and never a file one,
        and then gives no test that can fail if it is a file one: everywhere else in this class the pool
        removes the lock itself, so the file and the live process agree. This is the case Task 0 Step 5
        actually measured — the lock still present 32 s after the process was gone. Read as liveness it
        would say "open", switch() would skip open_editor, refresh() would hit an endpoint with no Editor
        behind it, and the one slot would end held at the probe stage after any crash or unreaped close."""
        self.pool().ensure()
        lock = self.root / "editors" / "slot-1" / "Temp" / "UnityLockfile"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("1", encoding="utf-8")   # litter from a crashed Editor; no process holds the folder
        pool = self.pool(mcp=FakeMcp())
        pool.switch("unity_slot:1", self.commit("fix"), "interactive")
        self.assertEqual([name for name, _ in pool.mcp.calls][0], "start")

    def test_a_batch_switch_on_an_open_slot_closes_the_editor_gracefully_first(self):
        """All three parts of Task 0 Step 5's close, in one assertion: the Editor is signalled rather than
        asked (EditorApplication.Exit(0) does not close it), the lock file is removed by the *pool* because
        Unity leaves it behind, and the uvx MCP server is reaped because it outlives the Editor and would
        otherwise still be holding port 8080 when the next open_editor runs."""
        self.pool().ensure()
        pool = self.pool(mcp=FakeMcp())
        pool.switch("unity_slot:1", self.commit("one"), "interactive")
        pool.park("unity_slot:1", "interactive")
        pool.mcp.calls.clear()
        pool.switch("unity_slot:1", self.commit("two"), "batch")
        self.assertEqual([name for name, _ in pool.mcp.calls], ["terminate", "reap_server"])
        self.assertFalse((self.root / "editors" / "slot-1" / "Temp" / "UnityLockfile").exists())

    def test_a_dirty_slot_refuses_to_switch_and_says_which_stage_failed(self):
        self.pool().ensure()
        (self.root / "editors" / "slot-1" / "README.md").write_text("edited by hand", encoding="utf-8")
        with self.assertRaises(SlotError) as caught:
            self.pool(mcp=FakeMcp()).switch("unity_slot:1", self.commit("fix"), "batch")
        self.assertEqual(caught.exception.stage, "git")

    def test_a_failing_identity_probe_fails_at_the_probe_stage_not_the_git_stage(self):
        self.pool().ensure()
        with self.assertRaises(SlotError) as caught:
            self.pool(mcp=FakeMcp(ready=False)).switch("unity_slot:1", self.commit("fix"), "interactive")
        self.assertEqual(caught.exception.stage, "probe")

    def test_a_refresh_that_never_goes_quiet_fails_at_the_probe_stage_before_any_probe(self):
        self.pool().ensure()
        pool = self.pool(mcp=FakeMcp(quiet=False))
        with self.assertRaises(SlotError) as caught:
            pool.switch("unity_slot:1", self.commit("fix"), "interactive")
        self.assertEqual(caught.exception.stage, "probe")
        self.assertNotIn("probe", [name for name, _ in pool.mcp.calls])

    def test_an_editor_that_is_still_compiling_is_waited_for_rather_than_failed(self):
        """The deadline loop is why Task 0 Step 5's 5.7-9.6 s recompiles do not fail an item: the first
        sample after a refresh normally observes is_compiling. Nothing else in this class can fail if
        wait_for_quiet gives up on its first TimeoutError, and a one-shot version would turn every genuine
        recompile into a probe-stage hold on the only slot there is."""
        class SlowToSettle(FakeMcp):
            def wait_quiet(self, slot, timeout):
                super().wait_quiet(slot, timeout)          # records the sample
                if sum(name == "wait_quiet" for name, _ in self.calls) < 3:
                    raise TimeoutError("still compiling")

        self.pool().ensure()
        pool = self.pool(mcp=SlowToSettle())
        started = self.now
        slot = pool.switch("unity_slot:1", self.commit("fix"), "interactive")
        self.assertEqual(slot["state"], "interactive_busy")
        self.assertEqual(sum(name == "wait_quiet" for name, _ in pool.mcp.calls), 3)
        self.assertGreater(self.now, started)   # the injected sleep, not a real one, paced the samples

    def test_a_compile_error_fails_the_item_at_its_own_stage_and_leaves_the_slot_usable(self):
        """spec §7 asks for 'zero Console errors'. A project that does not compile is the item's problem: it
        must not take the one slot out of the pool until a human runs recover-slot."""
        self.pool().ensure()
        pool = self.pool(mcp=FakeMcp(console_errors=["Assets/A.cs(3,1): error CS1002: ; expected"]))
        with self.assertRaises(SlotError) as caught:
            pool.switch("unity_slot:1", self.commit("fix"), "interactive")
        self.assertEqual(caught.exception.stage, "compile")
        self.assertIn("CS1002", str(caught.exception))

    def test_a_stale_lockfile_is_cleared_and_a_live_one_is_kept(self):
        self.pool().ensure()
        lock = self.root / "editors" / "slot-1" / "Temp" / "UnityLockfile"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("1", encoding="utf-8")
        self.assertFalse(SlotPool.clear_stale_lock(lock.parent.parent, lambda: True))
        self.assertTrue(lock.exists())
        self.assertTrue(SlotPool.clear_stale_lock(lock.parent.parent, lambda: False))
        self.assertFalse(lock.exists())
        self.assertFalse(SlotPool.clear_stale_lock(lock.parent.parent, lambda: False))

    def test_another_editor_on_the_host_holds_the_slot_before_any_git_runs(self):
        """The contention preflight is not decoration: it must refuse *before* the fetch and the checkout,
        and it must hold the slot (probe) rather than offer a retry (git), because a second Editor losing a
        licence race reports as something that reads like project corruption."""
        class Untouchable(Worktrees):
            def fetch(self, repo):
                raise AssertionError("switch() ran git before the contention preflight")

        self.pool().ensure()
        spy = Untouchable(self.root / "repos", self.root / "worktrees", {"Farm-Client": str(self.origin)})
        pool = self.pool(spy, mcp=FakeMcp(), editor_scan=lambda folder: "/Users/x/Farm-Client")
        with self.assertRaises(SlotError) as caught:
            pool.switch("unity_slot:1", "0" * 40, "batch")
        self.assertEqual(caught.exception.stage, "probe")
        self.assertIn("/Users/x/Farm-Client", str(caught.exception))

    def test_park_returns_a_batch_slot_to_main_closed_and_an_interactive_slot_to_main_open(self):
        self.pool().ensure()
        pool = self.pool(mcp=FakeMcp())
        pool.switch("unity_slot:1", self.commit("fix"), "batch")
        main = self.trees.resolve_commit("Farm-Client")
        slot = pool.park("unity_slot:1", "batch")
        self.assertEqual((slot["state"], slot["parked_commit"]), ("idle_closed", main))
        self.assertEqual(self.trees.head(self.root / "editors" / "slot-1"), main)
        pool.switch("unity_slot:1", self.commit("more"), "interactive")
        main = self.trees.resolve_commit("Farm-Client")
        slot = pool.park("unity_slot:1", "interactive")
        # spec §7: "After an interactive run the Editor stays open, parked on main."
        self.assertEqual((slot["state"], slot["parked_commit"]), ("idle_open", main))
