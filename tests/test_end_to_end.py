import hashlib
import hmac
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.config import Config
from agent.launcher import _PINNED_EXIT
from agent.service import build, enqueue
from test_ledger import ISSUE, OTHER, issue
from test_slots import FakeMcp, FakeUnity

ROOT = Path(__file__).resolve().parents[1]
APP = "e5a8c16d-9f85-4123-acf5-94e41c3304d5"
REPOS = ("Farm-Client", "farm-hive", "farmgui", "common", "Farm-Contract")
SLOT = "unity_slot:1"


def git(*args, cwd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True, capture_output=True)


class Fixture:
    """The environment both end-to-end classes run against: four origins, the Linear stub, one configured
    Unity slot, and a whole `build()` wired to the `fake` runtime.

    Deliberately not a `TestCase`. Subclassing the class that holds the tests would re-run its heavyweight,
    timing-sensitive tests a second time under a setUp that also builds a slot worktree and a pool —
    duplicated work for no coverage, and tests the totals would not account for. It is a mixin instead, and
    it needs a TestCase beside it only because it registers its own teardown through `self.addCleanup`.
    """

    def build_environment(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        remotes = {}
        for repo in REPOS:
            origin = root / "origins" / repo
            origin.mkdir(parents=True)
            git("init", "-q", "-b", "main", ".", cwd=origin)
            (origin / "README.md").write_text(repo, encoding="utf-8")
            git("add", ".", cwd=origin); git("commit", "-qm", "init", cwd=origin)
            remotes[repo] = str(origin)
        self.stub = root / "stub"; self.stub.mkdir()
        (self.stub / "issue.json").write_text(json.dumps(issue(labels=["Bug"], delegate_id=APP)), encoding="utf-8")
        env = {"FARMBOT_LINEAR_STUB_DIR": str(self.stub), "FARMBOT_CONFIG": str(root / "none.json"),
               "FAKE_CLI_REPO": str(ROOT), "FAKE_CLI_MODE": "cli",
               "FAKE_CLI_STEPS": json.dumps([
                   ["claim", "--item", "{item}", "--worker-id", "fake"],
                   ["prepare-comment", "--item", "{item}", "--token", "{token}", "--kind", "started", "--body-file", str(root / "started.md")],
                   ["post-comment", "--item", "{item}", "--token", "{token}", "--action-id", "{action_id}"],
                   ["prepare-comment", "--item", "{item}", "--token", "{token}", "--kind", "blocker", "--body-file", str(root / "blocker.md")],
                   ["post-comment", "--item", "{item}", "--token", "{token}", "--action-id", "{action_id}"],
                   ["finish", "--item", "{item}", "--token", "{token}", "--outcome", "blocked", "--input", str(root / "outcome.json")]])}
        (root / "started.md").write_text("👀 FarmBot 已开始处理", encoding="utf-8")
        (root / "blocker.md").write_text("缺少信息", encoding="utf-8")
        self.env_patch = patch.dict(os.environ, env)
        self.env_patch.start(); self.addCleanup(self.env_patch.stop)
        # The slot folder is a git worktree of a README, not a Unity project, so `agent.unity.editor_path`
        # would fall through to the per-host probe and reach /Applications, which no test may touch. The
        # entry's own `unity` override is the configured escape hatch a real host sets when the Editor is
        # not where the probe looks, and it keeps the batch argv honest without naming an install.
        self.unity_binary = root / "unity-binary"
        self.unity_binary.touch()
        config = Config(client_id="client", client_secret="s", webhook_secret="signing-secret", host="test", runtime="fake",
                        repos=remotes, max_concurrent=2, port=0, local_root=root / "local",
                        slots=[{"id": SLOT, "repo": "Farm-Client", "unity": str(self.unity_binary)}])
        self.c = build(config)
        self.addCleanup(self.c.lifecycle.ledger.close)
        self.addCleanup(self.c.progress.ledger.close)
        self.addCleanup(self.c.recovery.close)
        self.addCleanup(self.c.pool.close)   # build opens the pool's own connection; nothing else closes it
        self.addCleanup(self.c.receiver.close)
        self.addCleanup(self.c.ledger.close)
        self.addCleanup(self.c.server.server_close)
        self.root = root

    def signed(self, event):
        body = json.dumps(event).encode()
        return body, hmac.new(b"signing-secret", body, hashlib.sha256).hexdigest()

    def created_event(self):
        return {"type": "AgentSessionEvent", "action": "created", "webhookTimestamp": int(time.time() * 1000), "organizationId": "org",
                "oauthClientId": "client", "appUserId": APP,
                "agentSession": {"id": "session-e2e", "issue": {"id": ISSUE, "identifier": "FARM-1", "url": "u"}}}

    def calls(self):
        path = self.stub / "calls.jsonl"
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


class EndToEndTests(unittest.TestCase, Fixture):
    def setUp(self):
        self.build_environment()

    def wait_gone(self, path, timeout=20):
        """Worktree removal happens on a scheduler tick, not synchronously with the worker's finish."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.c.scheduler.tick()
            if not path.exists():
                return True
            time.sleep(0.2)
        return False

    def wait_state(self, item_id, states, timeout=40):
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.c.scheduler.tick()
            state = self.c.ledger.item(item_id)["state"]
            if state in states:
                return state
            time.sleep(0.2)
        self.fail(f"item never reached {states}; last state {state}")

    def test_delegated_bug_runs_a_worker_that_comments_and_finishes_blocked(self):
        received = time.time()
        self.assertEqual(self.c.receiver.receive(*self.signed(self.created_event())), (200, "accepted"))
        self.assertTrue(self.c.receiver.process_one())
        acked = time.time()
        self.assertLess(acked - received, 10)
        self.assertEqual(self.calls()[-1]["content"]["type"], "thought")
        item = self.c.ledger.items_for_session("session-e2e")[0]
        self.c.scheduler.tick()
        self.assertIsNotNone(self.c.ledger.item(item["id"])["worker_pid"])
        self.assertTrue((self.c.paths.worktrees / item["id"] / "Farm-Client").is_dir())
        # The worker prepares and posts both comments itself; its finish retries until outcome.json names
        # the blocker action, which only exists once the outbox row does.
        deadline = time.time() + 30
        while time.time() < deadline and len(self.c.ledger.outbox(item["id"])) < 2:
            time.sleep(0.2)
        blocker = next(a for a in self.c.ledger.outbox(item["id"]) if a["kind"] == "blocker")
        (self.root / "outcome.json").write_text(json.dumps({"summary": "缺少信息", "comment_action_id": blocker["action_id"]}), encoding="utf-8")
        self.assertEqual(self.wait_state(item["id"], {"blocked", "failed"}), "blocked")
        methods = [c["method"] for c in self.calls()]
        self.assertEqual(methods.count("create_comment"), 2)
        self.assertIn("👀 FarmBot 已开始处理", self.calls()[[i for i, m in enumerate(methods) if m == "create_comment"][0]]["body"])
        # A parent's exit alone proves nothing about its descendants. The worker exited by itself, so its
        # evidence is an empty Windows Job Object, or on POSIX its own session and process group verified
        # empty at reap time (setsid() escapees are that proof's documented limit). Without os.waitid
        # (macOS CPython before 3.13) that check cannot run and cleanup keeps holding.
        deadline = time.time() + 5
        while self.c.launcher.running() and time.time() < deadline:
            self.c.scheduler.tick()
            time.sleep(0.05)
        cleanup = self.c.ledger.cleanup_record(item["id"])
        if os.name != 'nt' and not _PINNED_EXIT:
            self.assertFalse(cleanup['done'])
            self.assertIn("teardown", cleanup["error"])
            self.assertTrue((self.c.paths.worktrees / item["id"]).exists())
            return
        self.assertTrue(cleanup['done'], cleanup['error'])
        self.assertFalse((self.c.paths.worktrees / item['id']).exists())
        records = list((self.c.paths.runs / item['id']).glob('*/killed.json'))
        self.assertTrue(records)
        for record in records:
            proof = json.loads(record.read_text(encoding='utf-8'))
            self.assertIs(proof['empty'], True)
            self.assertEqual(proof['descendants'], [])
            if os.name == 'nt':
                self.assertTrue(proof['windows_job'])
            else:
                self.assertEqual(proof['posix_session'], proof['pid'])

    def test_stop_kills_a_running_worker_within_five_seconds(self):
        with patch.dict(os.environ, {"FAKE_CLI_MODE": "sleep"}):
            self.c.receiver.receive(*self.signed(self.created_event())); self.c.receiver.process_one()
            item = self.c.ledger.items_for_session("session-e2e")[0]
            self.c.scheduler.tick()
            stop = self.created_event()
            stop["action"] = "prompted"
            stop["agentActivity"] = {"id": "stop-1", "signal": "stop", "content": {"type": "prompt"}, "createdAt": "2026-09-18T00:00:00Z"}
            started = time.time()
            self.assertEqual(self.c.receiver.receive(*self.signed(stop)), (200, "stop received"))
            self.c.receiver.process_one()
            self.assertLess(time.time() - started, 5)
            self.assertEqual(self.c.ledger.item(item["id"])["state"], "cancelled")
            self.assertEqual(self.calls()[-1]["content"]["type"], "response")
            self.c.scheduler.tick()
            self.assertEqual(self.c.launcher.running(), {})


class RecordingMcp(FakeMcp):
    """The pool's Editor collaborator for the two-phase proof: always matches and is always quiescent, and
    keeps Temp/UnityLockfile exactly as honest as the real Editor does, so the pool's open/closed reasoning
    is the real one. It records nothing the assertions rely on — the evidence is in the ledger, in the audit
    trail and in git's own answer for the slot folder."""


class TwoPhaseTests(unittest.TestCase, Fixture):
    """Phase 1 done-criterion 3: two delegated items that both need Unity serialize on the single slot
    through the two-phase flow, one in batch mode and one interactive, with the slot switched to each pinned
    commit and parked back on main afterwards.

    Entirely offline. The items are created the way a host whose webhook cannot be delivered creates them —
    `agent.service.enqueue` — and the receiver, the scheduler and the pool are driven in this thread in the
    order `serve()` starts them. No webhook, no tunnel, no Unity and no network.
    """

    def setUp(self):
        self.build_environment()
        self.items = []
        self.scripts = {}
        self.pool = self.c.pool
        self.mcp = RecordingMcp()
        self.pool.mcp = self.mcp
        # Both collaborators, not just the MCP: after Task 7 a batch grant calls run_unsandboxed, and the
        # one build() supplied is the real launcher's, which would start a real Editor from this suite.
        # FakeUnity writes the results file the way a real batch run does, so `resource.batch_result`
        # reaches the resumed fake worker exactly as it would in production.
        self.pool.run_unsandboxed = FakeUnity(total=4388, passed=4362, failed=26, code=2)
        # The process listing is stubbed for the same reason every test in tests/test_slots.py stubs it: a
        # developer with their own Unity Editor open would otherwise fail the switch preflight on their
        # machine, and no test in this suite may shell out to pgrep. `editor_pid` answers from the fake
        # Editor's own open folders, which is what makes park()'s idle_open decision liveness rather than a
        # lock file — Task 0 Step 5 found that file still present 32 s after the process was gone.
        self.pool.editor_scan = lambda folder: None
        self.pool.editor_pid = lambda folder: 4242 if str(folder) in self.mcp.open_folders else None
        self.pool.ensure()
        self.addCleanup(self.settle_workers)
        # Two pins and a main that has moved past both: `park` returns the slot to origin/main, so pinning
        # either item at the branch tip would make "parked back on main" unobservable in the slot's head.
        self.commit_a = self.new_commit("a")
        self.commit_b = self.new_commit("b")
        self.main = self.new_commit("main-moved-on")
        self.folder = Path(self.c.ledger.slot(SLOT)["folder"])
        self.heads = []

    def settle_workers(self):
        """No Popen outlives the test: a worker still running at teardown is a ResourceWarning under
        -W error, and the run directories go with the temporary root the moment this returns.

        `launcher.poll()` and not `scheduler.tick()`: this reaps the processes that have exited and cannot
        start another one, which a tick over a queue left behind by a failed test could."""
        deadline = time.time() + 20
        while self.c.launcher.running() and time.time() < deadline:
            self.c.launcher.poll()
            time.sleep(0.1)
        for item_id in list(self.c.launcher.running()):
            self.c.launcher.stop(item_id)

    def new_commit(self, name):
        origin = self.root / "origins" / "Farm-Client"
        (origin / f"{name}.txt").write_text(name, encoding="utf-8")
        git("add", ".", cwd=origin); git("commit", "-qm", name, cwd=origin)
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=origin, capture_output=True,
                              text=True, check=True).stdout.strip()

    def enqueue_item(self, issue_ref, *, mode, commit, identifier):
        """One work item created the way a host without a webhook creates them, with its worker's whole
        script decided up front. The first launch asks for the slot and exits; the fresh worker the pool
        resumes gives it back, reports its blocker and finishes."""
        (self.stub / f"issue-{issue_ref}.json").write_text(
            json.dumps(issue(id=issue_ref, identifier=identifier, branch_name=f"farmbot/{identifier.lower()}",
                             labels=["Bug"], delegate_id=APP)), encoding="utf-8")
        item_id = enqueue(self.c.config, issue_ref=issue_ref, skill="fix", commit=commit)["id"]
        self.items.append(item_id)
        self.scripts[item_id] = [
            ["claim", "--item", "{item}", "--worker-id", "fake"],
            ["await-resource", "--item", "{item}", "--token", "{token}",
             "--resource", "unity_slot", "--mode", mode]]
        self.scripts[item_id + ":resumed"] = [
            ["claim", "--item", "{item}", "--worker-id", "fake2"],
            ["release-resource", "--item", "{item}", "--token-file", "{token_file}",
             "--outcome", "quiescent"],
            ["prepare-comment", "--item", "{item}", "--token", "{token}", "--kind", "blocker",
             "--body-file", str(self.root / "blocker.md")],
            ["post-comment", "--item", "{item}", "--token", "{token}", "--action-id", "{action_id}"],
            ["finish", "--item", "{item}", "--token", "{token}", "--outcome", "blocked",
             "--input", str(self.root / f"outcome-{item_id}.json")]]
        os.environ["FAKE_CLI_STEPS"] = json.dumps(self.scripts)
        return item_id

    def report_outcomes(self):
        """The worker's own summary file, written once its blocker comment is confirmed.

        `finish` refuses a blocked fix item whose evidence does not name a confirmed blocker comment, and
        the action id does not exist until the worker has posted one — the same order the delegated-bug
        test above writes this file in, and fake_cli's finish retries until it appears."""
        for item_id in self.items:
            path = self.root / f"outcome-{item_id}.json"
            if path.exists():
                continue
            blocker = next((a for a in self.c.ledger.outbox(item_id)
                            if a["kind"] == "blocker" and a["remote_id"]), None)
            if blocker is not None:
                path.write_text(json.dumps({"summary": "缺少信息", "comment_action_id": blocker["action_id"]}),
                                encoding="utf-8")

    def drain(self, timeout=240):
        """One turn of the three loops serve() runs, in this thread, in the order serve() starts them —
        yielding *between* the scheduler and the pool, because pool.tick() settles, grants and parks in one
        call and a transient state observed only after it would never be seen."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.c.receiver.process_one()
            self.c.scheduler.tick()
            self.report_outcomes()
            yield "before_pool"
            self.pool.tick()
            self.heads.append(self.c.worktrees.head(self.folder))
            yield "after_pool"
            time.sleep(0.1)
        self.fail(f"the loops did not reach a terminal state within {timeout}s")

    def launch_payloads(self, item_id):
        """Every launch message the launcher really wrote for this item, oldest first. Each run directory
        is named for its launch time, so the last one is the worker the pool resumed once it held the
        slot — the one whose `resource` block carries what the pool did on the slot's behalf."""
        prompts = sorted(Path(self.c.launcher.state_dir(item_id)).glob("*/prompt.md"))
        return [json.loads(path.read_text(encoding="utf-8").split("\n\n", 1)[1]) for path in prompts]

    def terminal(self, item_id):
        return self.c.ledger.item(item_id)["state"] in ("blocked", "delivered", "failed", "cancelled")

    def requested(self, item_id):
        return any(r["item_id"] == item_id for r in self.c.ledger.reservations())

    def reservation_state(self, item_id):
        return [r["state"] for r in self.c.ledger.reservations() if r["item_id"] == item_id][-1]

    def holder(self):
        """The item that owns the slot right now, asked from the slot's side, or None."""
        row = self.c.ledger.active_reservation_on(SLOT)
        return row["item_id"] if row else None

    def parked(self):
        return self.c.ledger.slot(SLOT)["state"] in ("idle_open", "idle_closed")

    def pump_until(self, done, items, timeout=120):
        """The receiver and the scheduler only. The pool has not ticked yet, so nothing can be granted."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.c.receiver.process_one()
            self.c.scheduler.tick()
            if done():
                return
            for item_id in items:
                if self.terminal(item_id):
                    # Fail here and not on the deadline: an item that dies before it ever asks for the
                    # slot says what went wrong now, instead of two minutes from now with nothing to read.
                    self.fail(f"{item_id} reached {self.c.ledger.item(item_id)['state']} "
                              f"before it ever asked for the slot")
            time.sleep(0.1)
        self.fail(f"the requests were not both queued within {timeout}s")

    def queue_two_requests(self):
        """Both items ask for the one slot before the pool's first tick, and in a known order.

        Both halves matter. If the second request were only made after the first had been granted, a fast
        first item could finish and hand the slot back before the second ever asked, and the test would
        prove a queue that happened to be empty rather than a wait. And if both items were simply enqueued
        together, one scheduler tick would launch both workers and they would reach `await-resource` in
        whatever order the OS scheduled them, which would make "granted in arrival order" an assertion
        about process timing.

        Nothing here is artificial: serve() runs the pool on its own loop with a two-second wait, so any
        pair of requests arriving inside one of those windows is queued together — an operator enqueueing
        two bugs back to back is exactly that case.
        """
        first = self.enqueue_item(ISSUE, mode="batch", commit=self.commit_a, identifier="FARM-1")
        self.pump_until(lambda: self.requested(first), [first])
        second = self.enqueue_item(OTHER, mode="interactive", commit=self.commit_b, identifier="FARM-2")
        self.pump_until(lambda: self.requested(second), [first, second])
        self.assertEqual([(r["item_id"], r["mode"], r["state"]) for r in self.c.ledger.reservations()],
                         [(first, "batch", "queued"), (second, "interactive", "queued")])
        return first, second

    def test_two_items_needing_unity_serialize_on_the_single_slot(self):
        first, second = self.queue_two_requests()
        self.assertNotEqual(self.c.ledger.item(first)["issue_id"], self.c.ledger.item(second)["issue_id"])
        waited = False
        for phase in self.drain():
            if phase == "before_pool" and self.holder() == first:
                # Sampled between the scheduler and the pool, for the whole of the first item's hold: while
                # one item owns the slot the other is queued behind it and its work item is parked in
                # awaiting_resource. Nothing but the pool can move it out of that state.
                self.assertEqual(self.c.ledger.item(second)["state"], "awaiting_resource")
                self.assertEqual(self.reservation_state(second), "queued")
                waited = True
            if self.terminal(first) and self.terminal(second):
                break
        self.assertTrue(waited, "the second item never had to wait; it was never observed queued behind the first")
        self.assertEqual([self.c.ledger.item(i)["state"] for i in (first, second)], ["blocked", "blocked"])
        # Durable evidence: exactly two reservations were ever acquired, in arrival order, never overlapping.
        acquired = [r for r in self.c.ledger.reservations() if r["acquired_at"] is not None]
        self.assertEqual([(r["item_id"], r["mode"]) for r in acquired],
                         [(first, "batch"), (second, "interactive")])
        self.assertLessEqual(acquired[0]["released_at"], acquired[1]["acquired_at"])
        # Contention, not a queue that happened to be empty when the second item arrived: the second
        # request existed while the first still held the slot, and was granted only once it was given back.
        self.assertLess(acquired[1]["created_at"], acquired[0]["released_at"])
        self.assertEqual({r["resource"] for r in acquired}, {SLOT})
        # Both items really needed *Unity*, and each in the way its mode means. Criterion 3 is not satisfied
        # by two items that merely held a reservation: the batch half's whole point is that the pool runs
        # the Editor itself, outside the worker's sandbox, and hands the worker the results.
        self.assertEqual(len(self.pool.run_unsandboxed.argv), 1)   # one batch run, for the batch item only
        self.assertEqual(self.pool.run_unsandboxed.owners, [first])
        argv = self.pool.run_unsandboxed.argv[0]
        self.assertEqual(argv[argv.index("-projectPath") + 1], str(self.folder))
        batch = self.launch_payloads(first)[-1]["resource"]
        # `total`, not the exit code: Task 0 Step 4 measured exit 0 meaning *nothing ran*, so a zero-total
        # result reaching a worker as evidence is the verification gap this plan has guarded against since
        # then. Asserting the measured 4388 closes that case as well as the no-run-at-all case.
        self.assertEqual((batch["mode"], batch["batch_result"]["state"], batch["batch_result"]["exit_code"]),
                         ("batch", "ran", 2))
        self.assertEqual((batch["batch_result"]["total"], batch["batch_result"]["passed"],
                          batch["batch_result"]["failed"]), (4388, 4362, 26))
        self.assertTrue(Path(batch["batch_result"]["results_file"]).is_file())
        # No argv and no Editor path for either worker (spec §8): the run was done for them.
        self.assertNotIn("unity", batch)
        # The interactive worker gets no batch result at all — run_tests over MCP is its verify path — and
        # the Editor it addresses is the one the pool started and pinned to this slot.
        interactive = self.launch_payloads(second)[-1]["resource"]
        self.assertEqual((interactive["mode"], interactive["batch_result"]), ("interactive", None))
        self.assertEqual(interactive["slot"], SLOT)

    def test_the_slot_visits_each_pinned_commit_and_is_parked_back_on_main(self):
        first, second = self.queue_two_requests()
        for _ in self.drain():
            if self.terminal(first) and self.terminal(second) and self.parked():
                break
        self.assertEqual([self.c.ledger.item(i)["state"] for i in (first, second)], ["blocked", "blocked"])
        main = self.c.worktrees.resolve_commit("Farm-Client")
        self.assertEqual(main, self.main)
        self.assertNotIn(main, (self.commit_a, self.commit_b))
        # The batch item visited commit_a, the interactive item commit_b, and the slot went back to main
        # between them and after them. `heads` is git's own answer for the slot folder, sampled after every
        # pool tick, so no collaborator has to have been told what happened. Consecutive repeats are
        # collapsed, and the tail is asserted rather than the whole list so that the assertion does not
        # depend on whether any tick sampled the slot before the first grant — the commit ensure() parked
        # it on is not part of the claim.
        observed = [h for i, h in enumerate(self.heads) if i == 0 or h != self.heads[i - 1]]
        self.assertEqual(observed[-4:], [self.commit_a, main, self.commit_b, main])
        parked = [row["reason"] for row in self.c.ledger.connection.execute(
            "SELECT reason FROM audit WHERE item_id=? AND kind='slot' ORDER BY id", (SLOT,))]
        self.assertEqual(parked[-1], "idle_open")   # spec §7: after an interactive run the Editor stays open
        slot = self.c.ledger.slot(SLOT)
        self.assertEqual(slot["parked_commit"], main)
        self.assertEqual([o["result"]["aggregate"] for o in self.c.ledger.identity_observations(second)],
                         ["match"])
        self.assertEqual(self.c.ledger.identity_observations(first), [])   # a batch run never probes
