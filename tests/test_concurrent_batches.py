import json
import sqlite3
import sys
import threading
import time
from pathlib import Path

from agent.launcher import Launcher, RUNTIMES, Unsandboxed
from agent.ledger import Ledger
from agent.slots import SlotPool, slot_entry
from test_ledger import ISSUE, OTHER, SELECTED_AT, SKILLS, issue
from test_slots import FakeMcp, FakeUnity, SlotFixture


class ConcurrentBatchTests(SlotFixture):
    def waiting(self, issue_id, commit):
        self.ledger.observe_issue(issue(id=issue_id))
        session = f"session-{issue_id}"
        self.ledger.ensure_session(session, issue_id, True)
        item = self.ledger.create_work_item(
            issue_id=issue_id, session_id=session, skill="fix",
            target={"repository": "Farm-Client", "requested_ref": "main", "commit_sha": commit,
                    "server_environment": "test", "selected_at": SELECTED_AT})
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.ledger.await_resource(item["id"], token, "unity_slot", "batch", skill=SKILLS["fix"])
        return item["id"]

    def batch_pool(self, runner, **kwargs):
        self.opened = []

        def factory():
            ledger = Ledger(self.root / "ledger.sqlite3", clock=lambda: self.now,
                            lease_seconds=60, check_same_thread=False)
            self.opened.append(ledger)
            return ledger

        entries = kwargs.pop("entries", [slot_entry({**self.entry, "id": f"unity_slot:{n}"}) for n in (1, 2)])
        kwargs.setdefault("editor_pid", lambda folder: None)
        pool = SlotPool(factory, self.trees, entries, host="test", editors_root=self.root / "editors",
                        clock=lambda: self.now, sleep=self.advance, mcp=FakeMcp(),
                        state_dir=lambda item: self.root / "runs" / item, editor_scan=lambda folder: None,
                        run_unsandboxed=runner, concurrent_batches=True, **kwargs)
        self.addCleanup(pool.close)
        pool.ensure()
        return pool

    def blocking_runner(self, items, outcomes=None):
        self.started = {item: threading.Event() for item in items}
        self.release = {item: threading.Event() for item in items}
        self.addCleanup(lambda: [event.set() for event in self.release.values()])

        def run(argv, *, owner, **kwargs):
            self.started[owner].set()
            if not self.release[owner].wait(15):
                raise TimeoutError("test did not release its fake batch")
            if outcomes and owner in outcomes:
                return outcomes[owner](argv, owner=owner, **kwargs)
            return FakeUnity(total=1, passed=1, failed=0)(argv, owner=owner, **kwargs)

        return run

    def wait_until(self, predicate):
        deadline = time.monotonic() + 15
        while not predicate():
            self.assertLess(time.monotonic(), deadline, "batch did not reach expected state")
            time.sleep(0.01)

    def start_pair(self, **kwargs):
        first_commit = self.commit("first-fix")
        second_commit = self.commit("second-fix")
        first = self.waiting(ISSUE, first_commit)
        second = self.waiting(OTHER, second_commit)
        # Register close before releases so a failed assertion still unblocks both executions.
        runner = self.blocking_runner([first, second], kwargs.pop("outcomes", None))
        pool = self.batch_pool(runner, **kwargs)
        self.addCleanup(lambda: [event.set() for event in self.release.values()])
        self.assertEqual(pool.tick()["granted"], 0)
        self.assertTrue(self.started[first].wait(15))
        self.assertTrue(self.started[second].wait(15), "slot 2 was blocked behind slot 1's batch")
        return pool, first, second, first_commit, second_commit

    def test_two_revisions_execute_together_with_separate_connections_and_evidence(self):
        pool, first, second, first_commit, second_commit = self.start_pair()
        self.assertEqual(len(self.opened), 3)
        self.assertEqual(len({id(ledger.connection) for ledger in self.opened}), 3)
        for item in (first, second):
            self.assertEqual(self.ledger.item(item)["state"], "awaiting_resource")
            self.assertFalse(pool.token_path(item).exists())
        for item in (first, second):
            self.release[item].set()
        self.assertEqual(pool.drain_batches(), 2)
        for item, commit in ((first, first_commit), (second, second_commit)):
            reservation = self.ledger.active_reservation(item)
            summary = json.loads((self.root / "runs" / item / "unity-batch.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["commit_sha"], commit)
            self.assertEqual(summary["reservation_id"], reservation["reservation_id"])
            self.assertEqual((summary["total"], summary["passed"]), (1, 1))
            self.assertEqual(self.trees.head(Path(self.ledger.slot(reservation["resource"])["folder"])), commit)
            self.assertEqual(self.ledger.item(item)["state"], "queued")
            self.assertTrue(pool.token_path(item).is_file())
        for ledger in self.opened[1:]:
            with self.assertRaises(sqlite3.ProgrammingError):
                ledger.connection.execute("SELECT 1")

    def test_cancelled_execution_is_not_settled_or_parked_before_it_exits(self):
        pool, first, second, _, second_commit = self.start_pair()
        self.ledger.cancel(first, "operator Stop")
        self.assertEqual(pool.tick(), {"settled": 0, "granted": 0, "parked": 0})
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "batch_busy")
        self.assertEqual(self.ledger.active_reservation(first)["state"], "cancel_requested")
        self.release[first].set()
        self.wait_until(lambda: pool._batches["unity_slot:1"][1].done())
        self.assertEqual(pool.tick()["parked"], 1)
        self.assertEqual(self.ledger.item(first)["state"], "cancelled")
        self.assertEqual(self.ledger.reservations()[0]["state"], "cancelled")
        self.assertFalse(pool.token_path(first).exists())
        self.assertEqual(self.ledger.slot("unity_slot:2")["state"], "batch_busy")
        self.assertEqual(self.trees.head(self.root / "editors" / "slot-2"), second_commit)
        self.assertFalse(pool._batches["unity_slot:2"][1].done())

    def test_a_third_request_waits_until_one_execution_and_its_reservation_settle(self):
        pool, first, second, _, second_commit = self.start_pair()
        third = self.waiting("00000000-0000-4000-8000-000000000003", second_commit)
        self.started[third], self.release[third] = threading.Event(), threading.Event()
        pool.tick()
        self.assertIsNone(self.ledger.active_reservation(third))
        self.release[first].set()
        self.wait_until(lambda: pool._batches["unity_slot:1"][1].done())
        pool.tick()
        self.assertIsNone(self.ledger.active_reservation(third), "completed evidence still belongs to its worker")
        self.ledger.claim(first, worker_id="finished")
        self.ledger.fail(first, "worker finished")
        pool.tick()  # verified settlement and parking precede the following grant
        pool.tick()
        self.assertTrue(self.started[third].wait(15))
        self.assertEqual(self.ledger.active_reservation(third)["resource"], "unity_slot:1")
        self.assertFalse(self.release[second].is_set())

    def test_failed_execution_holds_only_its_slot_and_preserves_peer_results(self):
        live = set()
        first_commit = self.commit("first-fix")
        first = self.waiting(ISSUE, first_commit)
        second = self.waiting(OTHER, first_commit)

        def outlived(argv, **kwargs):
            live.add(str(self.root / "editors" / "slot-1"))
            return Unsandboxed(None, True, 1)

        runner = self.blocking_runner([first, second], {first: outlived})
        pool = self.batch_pool(runner, editor_pid=lambda folder: 999 if str(folder) in live else None)
        self.addCleanup(lambda: [event.set() for event in self.release.values()])
        pool.tick()
        self.assertTrue(self.started[first].wait(15))
        self.assertTrue(self.started[second].wait(15))
        self.release[first].set()
        self.release[second].set()
        self.assertEqual(pool.drain_batches(), 1)
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "held")
        self.assertEqual(self.ledger.item(second)["state"], "queued")
        self.assertTrue(pool.token_path(second).is_file())
        pool.tick()
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "held")

    def test_shutdown_waits_for_execution_and_preserves_cancelled_items(self):
        pool, first, second, _, _ = self.start_pair()
        for item in (first, second):
            self.ledger.cancel(item, "service shutdown Stop")
        done = threading.Event()
        problems = []

        def drain():
            try:
                pool.drain_batches()
            except BaseException as exc:
                problems.append(exc)
            finally:
                done.set()

        thread = threading.Thread(target=drain)
        thread.start()
        self.addCleanup(thread.join, 15)
        self.addCleanup(lambda: [event.set() for event in self.release.values()])
        self.assertFalse(done.wait(0.05), "shutdown returned with live execution threads")
        for item in (first, second):
            self.release[item].set()
        self.assertTrue(done.wait(15))
        self.assertEqual(problems, [])
        self.assertEqual(pool._batches, {})
        self.assertEqual(pool.grant(), 0)
        self.assertTrue(all(self.ledger.item(item)["state"] == "cancelled" for item in (first, second)))

    def test_native_stop_kills_only_the_selected_concurrent_batch(self):
        commit = self.commit("native-fix")
        first, second = self.waiting(ISSUE, commit), self.waiting(OTHER, commit)
        launcher = Launcher(self.root / "runs", RUNTIMES["fake"], host="test")
        script = self.root / "batch simulator.py"
        script.write_text(
            "import pathlib,sys,time,os\n"
            "results,marker,release=map(pathlib.Path,sys.argv[1:])\n"
            "marker.write_text(str(os.getpid()))\n"
            "while not release.exists(): time.sleep(0.02)\n"
            "results.write_text('<test-run result=\"Passed\" total=\"1\" passed=\"1\" failed=\"0\" />')\n",
            encoding="utf-8")
        markers = {item: self.root / f"{item}.pid" for item in (first, second)}
        releases = {item: self.root / f"{item}.release" for item in (first, second)}

        def runner(argv, *, owner, **kwargs):
            results = argv[argv.index("-testResults") + 1]
            kwargs["timeout"] = 15
            return launcher.run_unsandboxed(
                [sys.executable, "-B", str(script), str(results), str(markers[owner]), str(releases[owner])],
                owner=owner, **kwargs)

        pool = self.batch_pool(runner)
        self.addCleanup(launcher.stop_all_unsandboxed)
        pool.tick()
        self.wait_until(lambda: all(marker.exists() for marker in markers.values()))
        first_pid, second_pid = (int(markers[item].read_text()) for item in (first, second))
        self.assertTrue(launcher.alive(first_pid))
        self.assertTrue(launcher.alive(second_pid))
        self.ledger.cancel(first, "operator Stop")
        self.assertTrue(launcher.stop_unsandboxed(first))
        self.wait_until(lambda: pool._batches["unity_slot:1"][1].done())
        pool.tick()
        self.assertFalse(launcher.alive(first_pid))
        self.assertTrue(launcher.alive(second_pid))
        self.assertEqual(self.ledger.slot("unity_slot:2")["state"], "batch_busy")
        releases[second].touch()
        self.assertEqual(pool.drain_batches(), 1)
        self.assertFalse(launcher.alive(second_pid))
        self.assertEqual(self.ledger.item(second)["state"], "queued")

    def test_concurrent_execution_refuses_a_borrowed_sqlite_connection(self):
        with self.assertRaisesRegex(ValueError, "connection factory"):
            SlotPool(self.ledger, self.trees, [self.entry], host="test", editors_root=self.root,
                     concurrent_batches=True)

    def test_one_slot_keeps_synchronous_hand_over_without_an_execution_thread(self):
        item = self.waiting(ISSUE, self.commit("single-fix"))
        pool = self.batch_pool(FakeUnity(total=1, passed=1), entries=[self.entry])
        self.assertEqual(pool.tick()["granted"], 1)
        self.assertEqual(self.ledger.item(item)["state"], "queued")
        self.assertEqual(pool._batches, {})
        self.assertEqual(len(self.opened), 1)
