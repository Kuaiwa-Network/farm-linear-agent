import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from agent.launcher import Launcher, RUNTIMES, Unsandboxed
from agent.ledger import Ledger
from agent.slots import SlotError, SlotPool, slot_entry
from agent.worktrees import WorktreeError, Worktrees
from test_ledger import ISSUE, OTHER, SELECTED_AT, issue


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
        # The slot folder is a git worktree of a README, not a Unity project, so `agent.unity.editor_path`
        # would fall through to the per-host probe and reach /Applications, which no test may touch. The
        # entry's own `unity` override is the configured escape hatch and is what a real host sets when the
        # Editor is not where the probe looks; it keeps the batch argv honest without naming an install.
        self.unity_binary = self.root / "unity-binary"
        self.unity_binary.touch()
        self.entry = slot_entry({"id": "unity_slot:1", "repo": "Farm-Client", "unity": str(self.unity_binary)})

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
        # Every pool in this suite gets a runner that never starts a process: a batch hand-over now performs
        # the run itself, and a test that reached the real Launcher.run_unsandboxed would reach Unity.
        kwargs.setdefault("run_unsandboxed", FakeUnity(total=4388, passed=4362, failed=26, code=2))
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


class FakeUnity:
    """Stands in for `Launcher.run_unsandboxed`. It writes the results file the way the real Editor does —
    before the exit code, and independently of it — because the whole contract is that the file and the code
    disagree. `hang=True` reproduces the addendum's Editor: the deadline expires and nothing is written."""

    def __init__(self, total=0, passed=0, failed=0, code=0, hang=False):
        self.totals, self.code, self.hang, self.argv = (total, passed, failed), code, hang, []
        self.owners = []

    def __call__(self, argv, *, cwd, timeout, log=None, env=None, owner=None, cancelled=None):
        self.argv.append(list(argv))
        self.owners.append(owner)
        if self.hang:
            return Unsandboxed(returncode=None, timed_out=True, seconds=timeout)
        total, passed, failed = self.totals
        results = Path(argv[argv.index("-testResults") + 1])
        results.parent.mkdir(parents=True, exist_ok=True)
        results.write_text(f'<test-run result="Failed(Child)" total="{total}" passed="{passed}" '
                           f'failed="{failed}" />', encoding="utf-8")
        return Unsandboxed(returncode=self.code, timed_out=False, seconds=1.0)


class RaisingQuiescence(FakeMcp):
    def quiescent(self, slot, mode):
        raise RuntimeError("the endpoint did not answer")


class CancellingMcp(FakeMcp):
    """Cancels the item from inside the switch, which is where a Linear Stop lands in production."""

    def __init__(self, ledger, item_id):
        super().__init__()
        self.ledger, self.item_id = ledger, item_id

    def refresh(self, slot):
        super().refresh(slot)
        self.ledger.cancel_reservations(self.item_id, "Linear stop")
        self.ledger.cancel(self.item_id, "Linear stop")


class CancellingCheckout:
    """Everything the real Worktrees does, except that the git stage is where the Linear Stop lands.

    CancellingMcp cannot stand in for this: the batch branch of switch() returns before it ever touches the
    MCP client, so on a batch grant that double never fires. The checkout is also the honest place — it is
    the minutes-long part of a batch switch, a detached checkout of a 3.7 GB repository plus git lfs.

    It fires once. park_idle checks the slot back out to main through this same object, and a second cancel
    would raise LedgerError from a ledger that has already marked the item terminal.
    """

    def __init__(self, trees, ledger, item_id):
        self._trees, self.ledger, self.pending = trees, ledger, item_id

    def __getattr__(self, name):
        return getattr(self._trees, name)

    def checkout_commit(self, folder, commit):
        result = self._trees.checkout_commit(folder, commit)
        if self.pending is not None:
            item, self.pending = self.pending, None
            # Exactly what Scheduler.stop does, in its order.
            self.ledger.cancel_reservations(item, "Linear stop")
            self.ledger.cancel(item, "Linear stop")
        return result


class UnreachableOrigin:
    """Everything the real Worktrees does, except that the default branch can never be resolved."""

    def __init__(self, trees):
        self._trees = trees

    def __getattr__(self, name):
        return getattr(self._trees, name)

    def resolve_commit(self, repo, ref=None):
        raise WorktreeError("could not read refs/remotes/origin/main")


class BrokenCheckout:
    """Everything the real Worktrees does, except that moving the slot always fails at the git stage."""

    def __init__(self, trees):
        self._trees = trees

    def __getattr__(self, name):
        return getattr(self._trees, name)

    def checkout_commit(self, path, commit):
        raise WorktreeError("reference is not a tree")


class StageTests(unittest.TestCase):
    def test_a_slot_error_refuses_a_stage_that_is_not_one_of_the_four(self):
        """`STAGES` was declared in Task 4 and read by nothing, and Task 6 is the first code to branch on
        `exc.stage`. That branch is string equality: `stage="prob"` at any raise site would fall through the
        probe arm and *release* a slot spec §7 says to hold, silently, on the only slot there is. Validating
        at construction is the one place that catches it, and it is why STAGES exists."""
        for stage in SlotError.STAGES:
            self.assertEqual(SlotError("fine", stage=stage).stage, stage)
        with self.assertRaises(ValueError):
            SlotError("typo", stage="prob")


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
        class NotesTheLock(FakeMcp):
            """Unity's own start is what would meet a surviving lock, so the fake records what it saw."""
            lock_at_start = None

            def start(self, slot, entry):
                self.lock_at_start = self._lock(slot).exists()
                return super().start(slot, entry)

        pool = self.pool(mcp=NotesTheLock())
        pool.switch("unity_slot:1", self.commit("fix"), "interactive")
        self.assertEqual([name for name, _ in pool.mcp.calls][0], "start")
        # And the litter is gone *before* the Editor meets it: a surviving lock blocks the start, which
        # would be a probe-stage hold on the only slot after any crash. (start() writes a fresh one, so
        # asserting on the file after the switch would prove nothing.)
        self.assertIs(pool.mcp.lock_at_start, False)

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

    def test_an_editor_that_vanishes_from_the_instance_list_is_waited_for_rather_than_abandoned(self):
        """`wait_quiet` is a *listing* call on a fresh slot, and catching only TimeoutError abandons the
        switch on the one thing this loop exists to wait out.

        `switch` writes slots.instance only after the console read, so on a fresh slot's first interactive
        switch `UnityIdentity._client` re-discovers the instance from `mcpforunity://instances` on every
        sample — and `refresh_unity` is precisely what triggers the domain reload during which an Editor is
        briefly absent from that listing. `discover_instance` raises SlotError(stage="editor") there, which
        used to come straight out of wait_for_quiet, abort the switch and cost the single-slot host a requeue
        (or a failed item on the second try) per domain reload.

        The second half is the guard on the first: a bug in FarmBot's own code answers the same way every
        second, so it must not be polled for until the deadline.
        """
        class VanishesThenReturns(FakeMcp):
            def wait_quiet(self, slot, timeout):
                super().wait_quiet(slot, timeout)
                if sum(name == "wait_quiet" for name, _ in self.calls) < 3:
                    raise SlotError(f"no connected Editor instance reports {slot['folder']}", stage="editor")

        self.pool().ensure()
        pool = self.pool(mcp=VanishesThenReturns())
        slot = pool.switch("unity_slot:1", self.commit("fix"), "interactive")
        self.assertEqual(slot["state"], "interactive_busy")
        self.assertEqual(sum(name == "wait_quiet" for name, _ in pool.mcp.calls), 3)

        class Buggy(FakeMcp):
            def wait_quiet(self, slot, timeout):
                super().wait_quiet(slot, timeout)
                raise AttributeError("'NoneType' object has no attribute 'read_resource'")

        pool = self.pool(mcp=Buggy())
        with self.assertRaises(SlotError) as caught:
            pool.switch("unity_slot:1", self.commit("more"), "interactive")
        self.assertEqual((caught.exception.stage, caught.exception.fault), ("probe", "farmbot"))
        self.assertEqual(sum(name == "wait_quiet" for name, _ in pool.mcp.calls), 1)

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

    def test_a_live_editors_lock_is_never_deleted_by_a_park_or_a_batch_switch(self):
        """spec §7 line 353: the lock is removed "only after confirming the process is gone". `lambda: False`
        asserts that rather than checking, and the lock file is what enforces Unity's one-Editor-per-folder
        guarantee — deleting a live one risks two Editors on one folder. Two ways in: a departing mode of
        "batch" says nothing about the host, and `was_open` is guarded by `self.mcp is not None`, so a pool
        with no MCP client never asks the process question at all."""
        self.pool().ensure()
        lock = self.root / "editors" / "slot-1" / "Temp" / "UnityLockfile"
        pool = self.pool(mcp=FakeMcp())
        pool.switch("unity_slot:1", self.commit("one"), "interactive")   # the Editor is open and holds it
        self.assertTrue(lock.exists())
        slot = pool.park("unity_slot:1", "batch")     # the departing mode disagrees with the process listing
        self.assertEqual(slot["state"], "idle_closed")
        self.assertTrue(lock.exists(), "park deleted the lock of an Editor that is still running")

        # The second way in: no MCP client at all, so was_open is False however alive the Editor is.
        headless = self.pool(editor_pid=lambda folder: 4242)
        headless.switch("unity_slot:1", self.commit("two"), "batch")
        self.assertTrue(lock.exists(), "the batch branch deleted the lock of an Editor that is still running")

    def test_a_terminate_that_kills_nothing_fails_the_close_instead_of_freeing_the_lock(self):
        """The third way `lambda: False` gets in: close_editor used to treat its own terminate() as proof.
        A SIGTERM that returns without killing anything would then have the pool delete a live Editor's
        lock and hand the folder to `Unity -batchmode`. The process listing is asked again, so the lock
        survives and the close fails loudly at the editor stage instead."""
        self.pool().ensure()

        class LyingTerminate(FakeMcp):
            def terminate(self, slot, timeout):
                self.calls.append(("terminate", slot["slot_id"]))   # claims success, kills nothing

        pool = self.pool(mcp=LyingTerminate())
        pool.switch("unity_slot:1", self.commit("one"), "interactive")
        pool.park("unity_slot:1", "interactive")
        lock = self.root / "editors" / "slot-1" / "Temp" / "UnityLockfile"
        with self.assertRaises(SlotError) as caught:
            pool.switch("unity_slot:1", self.commit("two"), "batch")
        self.assertEqual(caught.exception.stage, "editor")
        self.assertTrue(lock.exists(), "close_editor deleted the lock of an Editor it never killed")

    def test_a_farmbot_bug_still_holds_the_slot_but_is_not_blamed_on_unity(self):
        """Ruling B: keep holding — releasing a slot whose state is unknown risks a second Editor on the
        folder — but an AttributeError in FarmBot's own code must not reach the operator as "the Editor
        never went quiet". `recover-slot` cannot fix a bug in this file, and the operator reading the hold
        has to know which of the two misbehaved."""
        self.pool().ensure()

        class Buggy(FakeMcp):
            def refresh(self, slot):
                raise AttributeError("'NoneType' object has no attribute 'call_tool'")

        pool = self.pool(mcp=Buggy())
        with self.assertRaises(SlotError) as caught:
            pool.switch("unity_slot:1", self.commit("fix"), "interactive")
        self.assertEqual(caught.exception.stage, "probe")        # still held, which is the safe direction
        self.assertEqual(caught.exception.fault, "farmbot")      # but not Unity's fault
        self.assertIn("AttributeError", str(caught.exception))
        self.assertIn("recover-slot will not fix it", str(caught.exception))

        # The distinction is only worth anything if a genuine Editor failure keeps the other label.
        class Unreachable(FakeMcp):
            def refresh(self, slot):
                raise ConnectionRefusedError("nothing is listening on 127.0.0.1:8080")

        pool = self.pool(mcp=Unreachable())
        with self.assertRaises(SlotError) as caught:
            pool.switch("unity_slot:1", self.commit("more"), "interactive")
        self.assertEqual((caught.exception.stage, caught.exception.fault), ("probe", "external"))
        self.assertNotIn("FarmBot bug", str(caught.exception))

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


class PoolTests(SlotFixture):
    """The pool thread's loop: grant a queued request, switch the slot, hand it over, settle it afterwards."""

    def waiting(self, issue_id, commit, mode):
        # issue(id=...), not issue(issue_id=...): the helper's keyword is `id` and the wrong one leaves the
        # row on ISSUE, after which create_work_item raises "unknown issue".
        self.ledger.observe_issue(issue(id=issue_id))
        session = f"session-{issue_id}"
        self.ledger.ensure_session(session, issue_id, True)
        target = {"repository": "Farm-Client", "requested_ref": "main", "commit_sha": commit,
                  "server_environment": "公共测试服", "selected_at": SELECTED_AT}
        item = self.ledger.create_work_item(issue_id=issue_id, session_id=session, skill="fix", target=target)
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.ledger.await_resource(item["id"], token, "unity_slot", mode)
        return item["id"]

    def finish_worker(self, item_id, reason="worker finished"):
        """A granted item is 'queued' again, and Ledger.fail refuses anything that is not 'running'. The
        worker's own path is claim-then-terminal, so the fixture takes it rather than inventing one."""
        self.ledger.claim(item_id, worker_id="w2")
        return self.ledger.fail(item_id, reason)

    def pool(self, **kwargs):
        kwargs.setdefault("state_dir", lambda item_id: self.root / "runs" / item_id)
        return super().pool(**kwargs)

    def audit_rows(self):
        return self.ledger.connection.execute("SELECT count(*) FROM audit").fetchone()[0]

    def test_a_queued_request_is_granted_switched_and_resumed_for_a_fresh_worker(self):
        self.pool().ensure()
        commit = self.commit("fix")
        item = self.waiting(ISSUE, commit, "batch")
        pool = self.pool(mcp=FakeMcp())
        self.assertEqual(pool.tick()["granted"], 1)
        self.assertEqual(self.ledger.item(item)["state"], "queued")
        self.assertEqual(self.ledger.item(item)["needs_resource"], None)
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "batch_busy")
        self.assertEqual(self.trees.head(self.root / "editors" / "slot-1"), commit)
        token = pool.token_path(item)
        self.assertTrue(token.is_file())
        self.assertEqual(token.stat().st_mode & 0o077, 0)
        # The token goes to a file and never onto a command line, where arguments are visible to every
        # process on the host (spec §15). It is the reservation's own secret, and the ledger is the only
        # thing that can say so: it keeps a hash, so presenting the file back is the proof.
        written = token.read_text(encoding="utf-8")
        self.assertTrue(written.startswith("res_"), written)
        reservation = self.ledger.reservations(("active",))[0]["reservation_id"]
        self.assertEqual(self.ledger.release(reservation, written, "the token on disk is the real one"),
                         "released")

    def test_fix_commit_is_loaded_and_batch_evidence_is_bound_to_its_reservation(self):
        self.pool().ensure()
        baseline = self.trees.resolve_commit("Farm-Client")
        item_id = self.waiting(ISSUE, baseline, "batch")
        # Replace the ungranted baseline request with the worker's committed fix.
        self.ledger.cancel(item_id, "replace request")
        item_id = self.ledger.retry(item_id, "verify fix")["id"]
        token = self.ledger.claim(item_id, worker_id="w")["token"]
        path = self.trees.add("Farm-Client", item_id, "farmbot/fix")
        (path / "README.md").write_text("fixed")
        git("commit", "-qam", "fix", cwd=path)
        fixed = self.trees.head(path)
        self.trees.verification_commit("Farm-Client", item_id, fixed)
        self.ledger.await_resource(item_id, token, "unity_slot", "batch", commit_sha=fixed)
        pool = self.pool(mcp=FakeMcp())
        self.assertEqual(pool.tick()["granted"], 1)
        reservation = self.ledger.active_reservation(item_id)
        self.assertEqual(self.trees.head(self.root / "editors" / "slot-1"), fixed)
        self.assertEqual(self.ledger.item(item_id)["target"]["commit_sha"], baseline)
        summary = json.loads((self.root / "runs" / item_id / "unity-batch.json").read_text())
        self.assertEqual(summary["commit_sha"], fixed)
        self.assertEqual(summary["reservation_id"], reservation["reservation_id"])
        evidence_dir = Path(summary["results_file"]).parent
        self.assertEqual(evidence_dir.name, reservation["reservation_id"])
        self.assertEqual(json.loads((evidence_dir / "unity-batch.json").read_text()), summary)

    def test_the_pool_runs_the_batch_itself_and_hands_the_worker_the_results(self):
        """The worker never starts Unity; the launcher does, outside the sandbox. What the worker gets is
        this summary, and the argv is built from the slot entry alone — nothing the worker supplied."""
        self.pool().ensure()
        item = self.waiting(ISSUE, self.commit("fix"), "batch")
        pool = self.pool(mcp=FakeMcp(), run_unsandboxed=FakeUnity(total=4388, passed=4362, failed=26, code=2))
        self.assertEqual(pool.tick()["granted"], 1)
        argv = pool.run_unsandboxed.argv[-1]
        self.assertNotIn("-quit", argv)
        self.assertNotIn("-nographics", argv)
        self.assertNotIn("-accept-apiupdate", argv)
        self.assertEqual(argv[0], str(self.unity_binary))
        self.assertEqual(argv[argv.index("-projectPath") + 1], str(self.root / "editors" / "slot-1"))
        # Composed from the slot entry and the item's own state directory; no value a worker chose.
        self.assertEqual(argv[argv.index("-assemblyNames") + 1], "HotUpdate.Tests")
        reservation_id = self.ledger.active_reservation(item)["reservation_id"]
        self.assertEqual(argv[argv.index("-testResults") + 1],
                         str(self.root / "runs" / item / "unity" / reservation_id / "unity-tests.xml"))
        self.assertEqual(pool.run_unsandboxed.owners[-1], item)   # reachable by item id for Stop and shutdown
        summary = json.loads((self.root / "runs" / item / "unity-batch.json").read_text(encoding="utf-8"))
        self.assertEqual((summary["state"], summary["exit_code"]), ("ran", 2))
        self.assertEqual((summary["total"], summary["failed"]), (4388, 26))
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "batch_busy")

    def test_a_batch_run_with_no_results_is_a_verification_gap_and_not_a_slot_failure(self):
        """Task 0 Step 4's trap: `-assemblyNames NoSuchAssembly` exits **0** and writes total="0". The slot is
        fine, so it stays in the pool and the item stays alive; the worker is told it has no evidence."""
        self.pool().ensure()
        item = self.waiting(ISSUE, self.commit("fix"), "batch")
        pool = self.pool(mcp=FakeMcp(), run_unsandboxed=FakeUnity(total=0, passed=0, failed=0, code=0))
        self.assertEqual(pool.tick()["granted"], 1)
        summary = json.loads((self.root / "runs" / item / "unity-batch.json").read_text(encoding="utf-8"))
        self.assertEqual((summary["state"], summary["exit_code"]), ("gap", 0))
        self.assertEqual(self.ledger.item(item)["state"], "queued")
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "batch_busy")

    def test_a_previous_runs_results_are_never_read_as_this_runs_evidence(self):
        """A batch reservation is retried at the tail of the queue after a git or editor failure, and a
        worker that met a gap may ask for another. If the stale XML survived, the second run would read the
        first run's totals and a 4388-test pass would be reported for a run that wrote nothing at all."""
        self.pool().ensure()
        item = self.waiting(ISSUE, self.commit("fix"), "batch")
        stale = self.root / "runs" / item / "unity-tests.xml"
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_text('<test-run result="Passed" total="4388" passed="4388" failed="0" />', encoding="utf-8")
        pool = self.pool(mcp=FakeMcp(), run_unsandboxed=FakeUnity(hang=True), editor_pid=lambda folder: None)
        self.assertEqual(pool.tick()["granted"], 1)   # a timeout is a gap, not a slot failure, once Unity is gone
        summary = json.loads((self.root / "runs" / item / "unity-batch.json").read_text(encoding="utf-8"))
        self.assertEqual((summary["state"], summary["total"]), ("timeout", None))
        self.assertTrue(stale.exists())  # historical evidence is retained, but never reused

    def test_a_pool_with_no_unsandboxed_runner_refuses_a_batch_grant_rather_than_faking_it(self):
        """SlotPool.__init__ accepts run_unsandboxed=None, so a wiring mistake in service.build is possible
        and would otherwise hand a worker a `ran` reservation for a run that never happened. The slot never
        misbehaved, so it goes back to the pool at the editor stage rather than being held."""
        self.pool().ensure()
        item = self.waiting(ISSUE, self.commit("fix"), "batch")
        pool = self.pool(mcp=FakeMcp(), run_unsandboxed=None)
        self.assertEqual(pool.tick()["granted"], 0)
        self.assertFalse((self.root / "runs" / item / "unity-batch.json").exists())
        # Still waiting, not failed: the reservation is re-queued at the tail for one more attempt, and
        # requeue_reservation deliberately leaves the item where it was.
        self.assertEqual(self.ledger.item(item)["state"], "awaiting_resource")
        self.assertEqual([r["state"] for r in self.ledger.reservations()], ["released", "queued"])
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "idle_closed")

    def test_a_batch_editor_that_outlives_its_timeout_and_still_holds_the_folder_holds_the_slot(self):
        """The 25-minute hang, as the pool would meet it. A deadline is not enough on its own: what decides
        between "gap" and "hold" is whether the process is gone afterwards, which is the same liveness
        question editor_is_open asks and never the lock file."""
        self.pool().ensure()
        item = self.waiting(ISSUE, self.commit("fix"), "batch")
        pool = self.pool(mcp=FakeMcp(), run_unsandboxed=FakeUnity(hang=True),
                         editor_pid=lambda folder: 4242)
        self.assertEqual(pool.tick()["granted"], 0)
        # The summary is written before the raise: the operator who reads a held slot needs the record of
        # what happened on it, and it is also what separates this from a switch that failed before the run.
        summary = json.loads((self.root / "runs" / item / "unity-batch.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["state"], "timeout")
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "held")
        self.assertEqual(self.ledger.item(item)["state"], "failed")

    def test_two_requests_serialize_on_the_one_slot_batch_first_then_interactive(self):
        self.pool().ensure()
        first_commit = self.commit("first")
        first = self.waiting(ISSUE, first_commit, "batch")
        second_commit = self.commit("second")
        second = self.waiting(OTHER, second_commit, "interactive")
        pool = self.pool(mcp=FakeMcp())
        pool.tick()
        self.assertEqual(self.ledger.item(second)["state"], "awaiting_resource")
        self.assertEqual(pool.tick()["granted"], 0)
        self.finish_worker(first, "batch worker finished")   # any terminal state hands the slot back
        self.assertEqual(pool.tick()["settled"], 1)
        self.assertEqual(pool.tick()["granted"], 1)
        self.assertEqual(self.ledger.item(second)["state"], "queued")
        self.assertEqual(self.trees.head(self.root / "editors" / "slot-1"), second_commit)
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "interactive_busy")
        # The identity probe's answer is filed against the item that is about to use the slot: it is the only
        # evidence that the Editor really loaded the pinned commit (Task 5), and nothing else records it.
        observations = self.ledger.identity_observations(second)
        self.assertEqual([row["result"]["aggregate"] for row in observations], ["match"])
        self.assertEqual(observations[0]["slot_id"], "unity_slot:1")

    def test_a_stop_during_the_switch_means_the_batch_editor_is_never_started(self):
        """Scheduler.stop can only kill an Editor the launcher has registered, and the window before that
        registration is the whole of switch() — minutes, on every batch grant. Without run_batch's own
        re-check a Stop there starts an Editor nothing can reach, which then holds the host's only slot for
        up to batch_timeout on an item that is already cancelled. The slot did come back afterwards, so the
        old behaviour was a wasted half hour rather than an orphan; the assertion that matters is that no
        Editor is started at all."""
        self.pool().ensure()
        item = self.waiting(ISSUE, self.commit("fix"), "batch")
        unity = FakeUnity(total=4388, passed=4362, failed=26, code=2)
        pool = self.pool(mcp=FakeMcp(), run_unsandboxed=unity,
                         worktrees=CancellingCheckout(self.trees, self.ledger, item))
        result = pool.tick()
        self.assertEqual(unity.argv, [])      # nothing was ever handed to the launcher
        self.assertEqual(unity.owners, [])    # so there was nothing for stop_unsandboxed to find, either
        # And the slot still comes back by itself on the one tick, by the existing cancelled-mid-switch path.
        self.assertEqual((result["granted"], result["parked"]), (0, 1))
        self.assertEqual(self.ledger.item(item)["state"], "cancelled")
        self.assertEqual([r["state"] for r in self.ledger.reservations()], ["cancelled"])
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "idle_closed")
        self.assertFalse(pool.token_path(item).exists())
        self.assertEqual(pool.tick(), {"settled": 0, "granted": 0, "parked": 0})

    def test_a_cancelled_reservation_cannot_start_after_the_item_is_retried(self):
        self.pool().ensure()
        item = self.waiting(ISSUE, self.commit("fix"), "batch")
        launcher = Launcher(self.root / "runs", RUNTIMES["fake"], host="test")
        marker = self.root / "stale-batch-started"

        def runner(argv, **kwargs):
            # Cancellation lands after run_batch's first active check. A retry is a
            # new item attempt, but must never revive this old reservation's invocation.
            self.ledger.cancel(item, "operator stop")
            self.ledger.retry(item, "operator retry")
            return launcher.run_unsandboxed(
                [sys.executable, "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).touch()", str(marker)],
                **kwargs)

        pool = self.pool(mcp=FakeMcp(), run_unsandboxed=runner)
        self.assertEqual(pool.tick()["granted"], 0)
        self.assertFalse(marker.exists(), "a retried item revived its cancelled reservation")
        self.assertEqual(self.ledger.item(item)["state"], "cancelled")
        self.assertEqual(self.ledger.reservations()[0]["state"], "cancelled")
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "idle_closed")

    def test_the_pool_settles_a_cancel_requested_reservation_as_cancelled_and_parks(self):
        """The pool half of a Stop that lands *after* the hand-over: the scheduler leaves the reservation
        cancel_requested and the slot busy, and it is the pool's next tick — never a timer — that probes,
        releases and parks.

        Characterisation, not new coverage: it passed before Task 9's change, because reservations_to_settle
        already selects cancel_requested and release() already resolves it to cancelled. The setup calls
        cancel_reservations for shape only — Ledger.cancel below would mark the reservation cancel_requested
        on its own, so deleting that line leaves this test green. What it pins is the contract between the
        two halves of Stop, which now live in different files and run on different threads."""
        self.pool().ensure()
        item = self.waiting(ISSUE, self.commit("fix"), "interactive")
        pool = self.pool(mcp=FakeMcp())
        pool.tick()
        self.ledger.cancel_reservations(item, "Linear stop")
        self.ledger.cancel(item, "Linear stop")
        self.assertEqual(pool.tick()["settled"], 1)
        self.assertEqual([r["state"] for r in self.ledger.reservations()], ["cancelled"])
        self.assertEqual(self.ledger.slot("unity_slot:1")["parked_commit"],
                         self.trees.resolve_commit("Farm-Client"))

    def test_two_requests_serialize_in_the_other_order_interactive_first_then_batch(self):
        """The steady state once spec §7's 'after an interactive run the Editor stays open' is real. The
        batch-first ordering never exercises the graceful close, so on its own it would sign off a system
        that is broken for every subsequent pair of items."""
        self.pool().ensure()
        first = self.waiting(ISSUE, self.commit("first"), "interactive")
        second_commit = self.commit("second")
        second = self.waiting(OTHER, second_commit, "batch")
        pool = self.pool(mcp=FakeMcp())
        pool.tick()
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "interactive_busy")
        self.finish_worker(first)
        pool.tick()
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "idle_open")
        self.assertEqual(pool.tick()["granted"], 1)
        # The close is SIGTERM then a reap of the uvx MCP server; the pool removes the lock file itself.
        self.assertEqual([("terminate", "unity_slot:1"), ("reap_server", "unity_slot:1")], pool.mcp.calls[-2:])
        self.assertFalse((self.root / "editors" / "slot-1" / "Temp" / "UnityLockfile").exists())
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "batch_busy")
        self.assertEqual(self.trees.head(self.root / "editors" / "slot-1"), second_commit)
        self.assertEqual(self.ledger.item(second)["state"], "queued")

    def test_a_failing_probe_holds_the_slot_and_fails_the_item_without_retrying(self):
        self.pool().ensure()
        item = self.waiting(ISSUE, self.commit("fix"), "interactive")
        pool = self.pool(mcp=FakeMcp(ready=False))
        pool.tick()
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "held")
        self.assertEqual(self.ledger.item(item)["state"], "failed")
        self.assertEqual([r["state"] for r in self.ledger.reservations()], ["active"])

    def test_a_held_slot_is_not_probed_again_on_the_next_tick(self):
        """hold() leaves the reservation active and only changes the slot, so without the 'held' exclusion in
        reservations_to_settle the pool would re-probe every two seconds a slot the operator was told to
        recover, and might eventually release one spec §7 says must wait."""
        self.pool().ensure()
        self.waiting(ISSUE, self.commit("fix"), "interactive")
        pool = self.pool(mcp=FakeMcp(ready=False))
        pool.tick()
        before = self.audit_rows()
        self.assertEqual(pool.tick(), {"settled": 0, "granted": 0, "parked": 0})
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "held")
        self.assertEqual([r["state"] for r in self.ledger.reservations()], ["active"])
        self.assertEqual(self.audit_rows(), before)

    def test_a_slot_is_held_when_the_release_gate_does_not_pass(self):
        """Global constraint: no slot is ever released on a timer. These are the three ways the gate fails."""
        for label, build, break_it in (
            ("not quiescent", lambda: FakeMcp(ready=False), None),
            ("probe raised", lambda: RaisingQuiescence(), None),
            ("token file gone", lambda: FakeMcp(), "token"),
        ):
            with self.subTest(label):
                self.setUp()
                self.pool().ensure()
                item = self.waiting(ISSUE, self.commit("fix"), "batch")
                pool = self.pool(mcp=build())
                pool.tick()
                if break_it == "token":
                    pool.token_path(item).unlink()
                self.finish_worker(item)
                self.assertEqual(pool.tick()["settled"], 0)
                self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "held")
                self.assertEqual([r["state"] for r in self.ledger.reservations()], ["active"])

    def test_a_grant_that_fails_gives_the_slot_back_through_park_and_never_asserts_its_state(self):
        """The same lie park_idle was fixed not to write, on the give-back path. A compile failure is
        reached *after* open_editor, so the Editor really is on the folder and the checkout really did move
        it to the failed pin. `_switch_failed` used to force the slot to idle_closed from here, which said
        no Editor was open on a folder that had one and left parked_commit naming a commit the folder was
        not on. The slot goes back through park() instead: folder to main, state decided by the departing
        mode and a live process rather than asserted."""
        self.pool().ensure()
        pinned = self.commit("does-not-build")
        self.commit("main-moved-on")   # so "back on main" is observable in the folder's own head
        item = self.waiting(ISSUE, pinned, "interactive")
        mcp = FakeMcp(console_errors=["Assets/A.cs(3,1): error CS1002: ; expected"])
        pool = self.pool(mcp=mcp)
        self.assertEqual(pool.tick()["granted"], 0)
        self.assertEqual(self.ledger.item(item)["state"], "failed")   # the commit's problem, not the slot's
        main = self.trees.resolve_commit("Farm-Client")
        self.assertNotEqual(main, pinned)
        slot = self.ledger.slot("unity_slot:1")
        self.assertEqual(slot["state"], "idle_open")        # open_editor started it and park never closes one
        self.assertEqual(slot["parked_commit"], main)       # the row agrees with the folder
        self.assertEqual(self.trees.head(self.root / "editors" / "slot-1"), main)
        # Still in the pool and still grantable: a commit that does not build is not a broken slot.
        self.assertIn(slot["state"], Ledger.FREE_SLOT_STATES)

    def test_an_interactive_slot_whose_editor_died_is_parked_closed_and_its_lock_cleared(self):
        """The other half of the same question, and the reason the state is not simply the mode: an Editor
        that crashed leaves the folder closed and its lock behind, so `editor_is_open` decides and spec §7
        removes that lock only after confirming the process is gone. Without this, a helper that answered
        idle_open for every interactive departure would pass every other test in this file."""
        self.pool().ensure()
        item = self.waiting(ISSUE, self.commit("fix"), "interactive")
        mcp = FakeMcp()
        pool = self.pool(mcp=mcp)
        self.assertEqual(pool.tick()["granted"], 1)
        lock = self.root / "editors" / "slot-1" / "Temp" / "UnityLockfile"
        self.assertTrue(lock.is_file())     # open_editor wrote it, exactly as the real Editor does
        mcp.open_folders.clear()            # the Editor died; the lock is the litter it left behind
        self.ledger.release(self.ledger.active_reservation(item)["reservation_id"],
                            pool.token_path(item).read_text(encoding="utf-8"), "worker reported quiescent")
        self.assertEqual(pool.tick()["parked"], 1)
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "idle_closed")
        self.assertFalse(lock.exists())

    def test_a_git_failure_on_an_open_interactive_slot_still_records_the_editor_that_is_there(self):
        """The give-back's fallback, reached only when park() cannot move the folder either — the git stage,
        whose own checkout is what just failed. The slot stays in the pool for the one retry spec §7 allows,
        but the state it records is asked and not asserted: an Editor an earlier interactive run left open
        is still on the folder, and writing idle_closed over it is the defect this round removed."""
        self.pool().ensure()
        mcp = FakeMcp()
        first = self.waiting(ISSUE, self.commit("first"), "interactive")
        pool = self.pool(mcp=mcp)
        self.assertEqual(pool.tick()["granted"], 1)          # opens the Editor on the slot
        self.ledger.release(self.ledger.active_reservation(first)["reservation_id"],
                            pool.token_path(first).read_text(encoding="utf-8"), "worker reported quiescent")
        pool.tick()
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "idle_open")
        self.waiting(OTHER, self.commit("second"), "interactive")
        broken = self.pool(mcp=mcp, worktrees=BrokenCheckout(self.trees))
        self.assertEqual(broken.tick()["granted"], 0)
        slot = self.ledger.slot("unity_slot:1")
        self.assertEqual(slot["state"], "idle_open")
        self.assertIn(slot["state"], Ledger.FREE_SLOT_STATES)   # still grantable, so the retry can happen
        self.assertEqual([r["state"] for r in self.ledger.reservations()],
                         ["released", "released", "queued"])

    def test_a_slot_the_worker_gave_back_itself_is_parked_by_its_own_departing_mode(self):
        """The ordinary way a slot comes back is the worker's own `release-resource`, which runs in the
        worker's process against its own connection. settle() never sees that reservation, so a note this
        object makes when *it* settles one cannot be what decides idle_open from idle_closed: park_idle
        reads the departing mode from the ledger. Without that, every interactive slot a worker released
        normally was recorded closed with its Editor still open, against spec §7."""
        self.pool().ensure()
        item = self.waiting(ISSUE, self.commit("fix"), "interactive")
        pool = self.pool(mcp=FakeMcp())
        self.assertEqual(pool.tick()["granted"], 1)
        # Exactly what `python3 -m agent release-resource --outcome quiescent` does, presenting the token
        # the pool wrote to the worker's state directory.
        reservation = self.ledger.active_reservation(item)
        self.ledger.release(reservation["reservation_id"], pool.token_path(item).read_text(encoding="utf-8"),
                            "worker reported quiescent")
        result = pool.tick()
        self.assertEqual((result["settled"], result["parked"]), (0, 1))
        slot = self.ledger.slot("unity_slot:1")
        self.assertEqual(slot["state"], "idle_open")
        self.assertEqual(slot["parked_commit"], self.trees.resolve_commit("Farm-Client"))

    def test_a_stop_between_the_grant_and_the_switch_does_not_kill_the_pool_thread(self):
        """resume() raises unless the item is awaiting; a Stop during the multi-minute switch cancels it, and
        an uncaught LedgerError there would take grant(), tick() and the pool thread down with the slot busy
        and the reservation open.

        The brief asserted the park on a *second* tick. tick() runs settle, grant and park_idle in one body,
        so the slot is already back in the pool when the first call returns and the second sees nothing to
        do — the assertion would have read 0. Asserting both counts on the one tick is what the sentence
        "the slot comes back to the pool by itself" actually means, and the no-op second tick keeps the
        rest of it.
        """
        self.pool().ensure()
        item = self.waiting(ISSUE, self.commit("fix"), "interactive")
        pool = self.pool(mcp=CancellingMcp(self.ledger, item))
        result = pool.tick()
        self.assertEqual((result["granted"], result["parked"]), (0, 1))
        self.assertEqual(self.ledger.item(item)["state"], "cancelled")
        self.assertEqual([r["state"] for r in self.ledger.reservations()], ["cancelled"])
        # idle_*open*, and that is the point rather than an incidental change: open_editor really did start
        # an Editor on this folder before the Stop landed, and park() never closes one. This used to record
        # idle_closed only because park_idle had no way to know the departing mode was interactive and
        # defaulted to batch — a row claiming no Editor was open on a folder that had one, while the very
        # next line of park() asked the process listing and was told otherwise.
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "idle_open")
        self.assertFalse(pool.token_path(item).exists())
        self.assertEqual(pool.tick(), {"settled": 0, "granted": 0, "parked": 0})

    def test_a_git_stage_failure_is_requeued_once_at_the_tail_and_fails_the_item_the_second_time(self):
        self.pool().ensure()
        first = self.waiting(ISSUE, self.commit("first"), "batch")
        pool = self.pool(mcp=FakeMcp(), worktrees=BrokenCheckout(self.trees))
        pool.tick()
        requeued = [r for r in self.ledger.reservations() if r["state"] == "queued"]
        self.assertEqual([(r["item_id"], r["attempts"]) for r in requeued], [(first, 1)])
        self.assertEqual(self.ledger.item(first)["state"], "awaiting_resource")
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "idle_closed")
        second = self.waiting(OTHER, self.commit("second"), "batch")
        self.assertEqual([r["item_id"] for r in self.ledger.reservations() if r["state"] == "queued"],
                         [first, second])   # re-queued at the tail, but still ahead of a later arrival
        pool.tick()
        self.assertEqual(self.ledger.item(first)["state"], "failed")
        reasons = [row["reason"] for row in self.ledger.connection.execute(
            "SELECT reason FROM audit WHERE item_id=?", (first,))]
        self.assertTrue(any("verification gap" in r for r in reasons), reasons)
        self.assertEqual(len([r for r in self.ledger.reservations() if r["item_id"] == first]), 2)

    def test_a_released_slot_is_parked_back_on_main_when_nothing_is_waiting(self):
        self.pool().ensure()
        item = self.waiting(ISSUE, self.commit("fix"), "batch")
        pool = self.pool(mcp=FakeMcp())
        pool.tick()
        main = self.trees.resolve_commit("Farm-Client")
        self.finish_worker(item, "done")
        # One tick settles, releases and parks: tick() computes all three counts in one call, so asserting
        # parked on a *later* tick would see 0 and the slot already idle.
        self.assertEqual(pool.tick(), {"settled": 1, "granted": 0, "parked": 1})
        slot = self.ledger.slot("unity_slot:1")
        self.assertEqual((slot["state"], slot["parked_commit"]), ("idle_closed", main))
        self.assertEqual(self.trees.head(self.root / "editors" / "slot-1"), main)
        self.assertFalse(pool.token_path(item).exists())

    def test_park_idle_leaves_a_switching_slot_alone_while_a_reservation_still_holds_it(self):
        """acquire() leaves the slot 'switching' too, so a state check on its own would return to the pool a
        slot that is at that moment being handed to a worker. The partial unique index is the fence behind
        that, and it does fire — but as a raw sqlite3.IntegrityError out of the next acquire."""
        self.pool().ensure()
        self.waiting(ISSUE, self.commit("fix"), "batch")
        pool = self.pool(mcp=FakeMcp())
        granted = self.ledger.acquire("unity_slot", owner="test", host="test")
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "switching")
        self.assertEqual(pool.park_idle(), 0)
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "switching")
        self.assertEqual(self.ledger.active_reservation_on("unity_slot:1")["reservation_id"],
                         granted["reservation_id"])
        self.assertIsNone(self.ledger.active_reservation_on("unity_slot:9"))

    def test_a_pool_built_with_a_factory_owns_and_closes_that_connection(self):
        """service.build hands the pool `lambda: Ledger(...)`, never the scheduler's connection: two threads
        on one sqlite3.Connection do not get two transactions, because _transaction() is a bare BEGIN
        IMMEDIATE/COMMIT and one thread's BEGIN can land inside the other's. The receiver already takes a
        factory for the same reason. A pool handed a Ledger *object* borrows it and must not close it."""
        opened = []

        def factory():
            ledger = Ledger(self.root / "ledger.sqlite3", clock=lambda: self.now, lease_seconds=60,
                            check_same_thread=False)
            opened.append(ledger)
            return ledger

        pool = SlotPool(factory, self.trees, [self.entry], host="test", editors_root=self.root / "editors",
                        clock=lambda: self.now)
        self.addCleanup(pool.close)
        self.assertEqual(len(opened), 1)          # once, in __init__, not once per call
        self.assertIsNot(pool.ledger, self.ledger)
        pool.ensure()
        self.assertEqual(len(opened), 1)
        pool.close()
        with self.assertRaises(sqlite3.ProgrammingError):
            pool.ledger.connection.execute("SELECT 1")
        borrowed = self.pool()
        self.assertIs(borrowed.ledger, self.ledger)
        borrowed.close()
        self.assertIsNotNone(self.ledger.slot("unity_slot:1"))   # the borrowed connection is still open

    def test_the_pool_grants_from_its_own_thread_while_another_connection_reads(self):
        """agent/service.py runs tick() on a third thread, beside the receiver's and the scheduler's, each on
        its own connection. This is that arrangement: the grant — acquire, a real git checkout, resume — runs
        off-thread under BEGIN IMMEDIATE while a second connection reads the same file throughout."""
        self.pool().ensure()
        commit = self.commit("fix")
        item = self.waiting(ISSUE, commit, "batch")
        clock = lambda: self.now
        pool = SlotPool(lambda: Ledger(self.root / "ledger.sqlite3", clock=clock, lease_seconds=60,
                                       check_same_thread=False),
                        self.trees, [self.entry], host="test", editors_root=self.root / "editors",
                        clock=clock, sleep=self.advance, mcp=FakeMcp(),
                        state_dir=lambda item_id: self.root / "runs" / item_id,
                        # Built by hand rather than through the fixture, so it needs the fixture's runner
                        # too: a batch hand-over now performs the run, and a pool without one refuses it.
                        run_unsandboxed=FakeUnity(total=4388, passed=4362, failed=26, code=2),
                        editor_scan=lambda folder: None, editor_pid=lambda folder: None)
        self.addCleanup(pool.close)
        outcome, started = {}, threading.Barrier(2)

        def run():
            started.wait(20)
            try:
                outcome["tick"] = pool.tick()
            except BaseException as exc:            # a bare failure here would otherwise read as a hang
                outcome["error"] = exc

        thread = threading.Thread(target=run)
        thread.start()
        self.addCleanup(thread.join, 20)
        started.wait(20)
        reads = 0
        while thread.is_alive():
            self.ledger.reservations()              # the other connection keeps reading throughout
            self.ledger.slots(host="test")
            reads += 1
            time.sleep(0.005)
        thread.join(timeout=20)
        self.assertFalse(thread.is_alive())
        self.assertNotIn("error", outcome, outcome.get("error"))
        self.assertGreater(reads, 0)
        self.assertEqual(outcome["tick"]["granted"], 1)
        self.assertEqual(self.ledger.item(item)["state"], "queued")
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "batch_busy")

    def test_settle_never_deletes_the_lock_of_an_editor_that_is_still_running(self):
        """The release gate looks like it has already asked the process question, and has not always: a
        pool with no MCP client treats quiescence as True without asking anything at all. `lambda: False`
        in settle's batch branch would then delete the lock of a live Editor and hand the folder to the
        next `Unity -batchmode`, which is exactly the corruption clear_stale_lock's own contract — the
        lock goes "only after confirming the process is gone" (spec §7) — exists to prevent."""
        self.pool().ensure()
        item = self.waiting(ISSUE, self.commit("fix"), "batch")
        pool = self.pool(mcp=FakeMcp())
        pool.tick()
        lock = self.root / "editors" / "slot-1" / "Temp" / "UnityLockfile"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("1", encoding="utf-8")      # a Unity process really is on the folder
        self.finish_worker(item)
        headless = self.pool(editor_pid=lambda folder: 4242)   # no mcp: the gate cannot ask, so it says yes
        self.assertEqual(headless.settle(), 1)
        self.assertTrue(lock.exists(), "settle deleted the lock of an Editor that is still running")

    def test_a_park_that_cannot_reach_main_holds_the_slot_rather_than_leaving_it_switching(self):
        """Ledger.release leaves the slot 'switching', which is not a free state. A park that failed and
        did nothing would leave it there for ever: the pool would never grant that slot again and nothing
        would ever retry it, with no held slot for the operator's recover-slot to find."""
        self.pool().ensure()
        item = self.waiting(ISSUE, self.commit("fix"), "batch")
        pool = self.pool(mcp=FakeMcp())
        pool.tick()
        self.finish_worker(item)
        stranded = self.pool(mcp=FakeMcp(), worktrees=UnreachableOrigin(self.trees))
        result = stranded.tick()
        self.assertEqual((result["settled"], result["parked"]), (1, 0))
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "held")

    def test_a_hand_over_that_fails_after_the_switch_gives_the_slot_back_instead_of_wedging_it(self):
        """The window between a successful switch and resume() is the one place a raise wedges the only
        slot for good. By then the slot is `*_busy` and the reservation `active` while the item is still
        `awaiting_resource`, and reservations_to_settle excludes that state: an exception escaping grant()
        into serve()'s guarded loop would be logged as `loop_error`, retried every two seconds, and settle
        nothing — no slot would ever go `held`, so the operator would never be told to run recover-slot.

        Both cases are real rather than monkeypatched. `state_dir=None` is what SlotPool.__init__ accepts
        today, and a state_dir under a plain file is what a mis-provisioned runs root looks like.
        """
        for label, state_dir in (("state_dir cannot be created",
                                  lambda item_id: self.root / "not-a-dir" / item_id),
                                 ("pool built without a state_dir", None)):
            with self.subTest(label):
                self.setUp()
                self.pool().ensure()
                (self.root / "not-a-dir").write_text("a file where a directory must go", encoding="utf-8")
                item = self.waiting(ISSUE, self.commit("fix"), "batch")
                pool = self.pool(mcp=FakeMcp(), state_dir=state_dir)
                self.assertEqual(pool.tick(), {"settled": 0, "granted": 0, "parked": 1})
                self.assertEqual(self.ledger.item(item)["state"], "failed")
                self.assertEqual([r["state"] for r in self.ledger.reservations()], ["released"])
                slot = self.ledger.slot("unity_slot:1")
                # Back in the pool and back on main, not held: the slot never misbehaved, the host did.
                self.assertEqual((slot["state"], slot["parked_commit"]),
                                 ("idle_closed", self.trees.resolve_commit("Farm-Client")))
                reasons = [row["reason"] for row in self.ledger.connection.execute(
                    "SELECT reason FROM audit WHERE item_id=?", (item,))]
                self.assertTrue(any("hand-over failed after the switch" in r for r in reasons), reasons)
                self.assertEqual(pool.tick(), {"settled": 0, "granted": 0, "parked": 0})
