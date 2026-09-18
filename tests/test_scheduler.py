import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from agent.launcher import Finished, Handle
from agent.ledger import Ledger
from agent.scheduler import Scheduler
from agent.skills import load_skills
from test_ledger import ISSUE, OTHER, SESSION, comment, issue

ROOT = Path(__file__).resolve().parents[1]
SKILLS = load_skills(ROOT / "skills")
FIX_LEASE = SKILLS["fix"].budget["lease_seconds"]  # the launcher records it, so claims last this long


class FakeLauncher:
    def __init__(self):
        self.spawned = []
        self.finished = []
        self.stopped = []
        self.stop_times = []
        self.next_pid = 100
        self.alive_pids = set()
        self.killed = []

    def state_dir(self, item_id):
        return Path("/fake/runs") / item_id

    def spawn(self, item_id, message, mcp_servers, budget_seconds, cwd, extra_env=None, writable=()):
        self.next_pid += 1
        self.spawned.append((item_id, message, mcp_servers, budget_seconds, str(cwd)))
        self.spawn_env = dict(extra_env or {})
        self.spawn_writable = [str(path) for path in writable]
        return Handle(item_id, self.next_pid, time.time(), time.time() + budget_seconds, Path(cwd), None, Path(cwd) / "last")

    def poll(self):
        finished, self.finished = self.finished, []
        return finished

    def stop(self, item_id, grace=5.0):
        self.stopped.append(item_id)
        self.stop_times.append(time.monotonic())
        return True

    def running(self):
        return {}

    def alive(self, pid):
        return pid in self.alive_pids

    def owned_pid(self, pid, item_id):
        return pid in self.alive_pids

    def kill_pid(self, pid, grace=5.0):
        self.killed.append(pid)
        self.alive_pids.discard(pid)
        return [pid]


class HandleAwareLauncher(FakeLauncher):
    """Like the real launcher, stop() only kills a worker whose handle spawn() has registered."""

    def __init__(self):
        super().__init__()
        self.handles = set()

    def spawn(self, item_id, message, mcp_servers, budget_seconds, cwd, extra_env=None, writable=()):
        handle = super().spawn(item_id, message, mcp_servers, budget_seconds, cwd, extra_env, writable)
        self.handles.add(item_id)
        return handle

    def stop(self, item_id, grace=5.0):
        super().stop(item_id, grace)
        found = item_id in self.handles
        self.handles.discard(item_id)
        return found


class FakeAPI:
    def __init__(self, fail=False):
        self.activities = []
        self.fail = fail

    def create_activity(self, session_id, content, activity_id=None):
        if self.fail:
            raise RuntimeError("linear down")
        self.activities.append((session_id, content["type"], content["body"]))
        return {"success": True}


class FakeWorktrees:
    def __init__(self, root):
        self.root = Path(root)
        self.added = []
        self.fail_on = None

    def clone_path(self, repo):
        return self.root / "repos" / f"{repo}.git"

    def add(self, repo, item_id, branch):
        if self.fail_on == (repo, item_id):
            raise RuntimeError("boom")
        path = self.root / item_id / repo
        path.mkdir(parents=True, exist_ok=True)
        self.added.append((repo, item_id, branch))
        return path

    def add_detached(self, repo, item_id):
        return self.add(repo, item_id, "detached")

    def remove(self, item_id):
        self.added.append(("removed", item_id, None))


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 1000.0
        self.ledger = Ledger(Path(self.tmp.name) / "ledger.sqlite3", clock=lambda: self.now, lease_seconds=60)
        self.addCleanup(self.ledger.close)
        self.launcher = FakeLauncher()
        self.trees = FakeWorktrees(Path(self.tmp.name) / "wt")
        self.api = FakeAPI()
        self.scheduler = Scheduler(self.ledger, self.launcher, SKILLS, self.trees,
                                   skill_root=ROOT / "skills", db_path=Path(self.tmp.name) / "ledger.sqlite3",
                                   runtime_name="fake", host="h", max_concurrent=1,
                                   guidance_for=lambda item: (self.ledger.session(item["session_id"]) or {}).get("guidance") or "",
                                   api=self.api)

    def item(self, issue_id=ISSUE, session=SESSION, skill="fix", **changes):
        self.ledger.observe_issue(issue(id=issue_id, **changes))
        self.ledger.ensure_session(session, issue_id, delegation=True)
        return self.ledger.create_work_item(issue_id=issue_id, session_id=session, skill=skill)

    def test_tick_launches_fix_with_write_worktrees_and_records_pid(self):
        item = self.item()
        counts = self.scheduler.tick()
        self.assertEqual(counts["launched"], 1)
        launched = self.launcher.spawned[0]
        payload = json.loads(launched[1].split("\n\n", 1)[1])
        self.assertEqual(set(payload["worktrees"]), {"Farm-Client", "farm-hive", "farmgui", "common"})
        self.assertEqual(launched[2], {})
        self.assertEqual(launched[3], 8 * 3600)
        self.assertEqual(self.ledger.item(item["id"])["worker_pid"], 101)
        self.assertIn(("Farm-Client", item["id"], "farmbot/farm-1"), self.trees.added)

    def test_launch_gives_the_worker_a_state_dir_pythonpath_and_writable_roots(self):
        item = self.item()
        self.scheduler.tick()
        payload = json.loads(self.launcher.spawned[0][1].split("\n\n", 1)[1])
        self.assertEqual(payload["state_dir"], f"/fake/runs/{item['id']}")
        self.assertTrue(self.launcher.spawn_env["PYTHONPATH"].split(":")[0] == str(ROOT))
        self.assertEqual(self.launcher.spawn_env["FARMBOT_DB"], str(Path(self.tmp.name) / "ledger.sqlite3"))
        repos = ("Farm-Client", "farm-hive", "farmgui", "common")
        worktrees = [str(self.trees.root / item["id"] / repo) for repo in repos]
        clones = [str(self.trees.root / "repos" / f"{repo}.git") for repo in repos]  # commits land in the bare clone
        self.assertEqual(self.launcher.spawn_writable[0], str(Path(self.tmp.name)))  # the ledger's directory
        self.assertEqual(sorted(self.launcher.spawn_writable[1:]), sorted(worktrees + clones))

    def test_dispatch_and_lease_follow_the_skill_budget(self):
        item = self.item()
        self.scheduler.tick()
        budget = SKILLS["fix"].budget
        payload = json.loads(self.launcher.spawned[0][1].split("\n\n", 1)[1])
        self.assertEqual(payload["lease_seconds"], budget["lease_seconds"])
        self.assertEqual(payload["renew_minutes"], budget["renew_minutes"])
        claimed = self.ledger.claim(item["id"], worker_id="w")
        self.assertEqual(claimed["lease_expires_at"], self.now + budget["lease_seconds"])

    def test_dispatch_carries_the_guidance_recorded_on_the_session(self):
        self.ledger.observe_issue(issue())
        self.ledger.ensure_session(SESSION, ISSUE, delegation=True, guidance="优先看 farm-hive 的日志")
        self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill="fix")
        self.scheduler.tick()
        payload = json.loads(self.launcher.spawned[0][1].split("\n\n", 1)[1])
        self.assertEqual(payload["guidance"], self.ledger.session(SESSION)["guidance"])
        self.assertEqual(payload["guidance"], "优先看 farm-hive 的日志")

    def test_concurrency_cap_holds_second_item_queued(self):
        self.item()
        self.now += 1  # distinct created_at: queue() order is otherwise a coin flip on the item's random id
        self.item(issue_id=OTHER, session="s2", identifier="FARM-2")
        self.scheduler.tick()
        self.assertEqual(len(self.launcher.spawned), 1)
        self.assertEqual([row["issue_id"] for row in self.ledger.queue()], [OTHER])

    def test_reaped_worker_that_never_finished_is_failed_and_worktrees_removed(self):
        item = self.item()
        self.scheduler.tick()
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.launcher.finished.append(Finished(item["id"], 3, "cli-error", False, "exited"))
        self.scheduler.tick()
        self.assertEqual(self.ledger.item(item["id"])["state"], "failed")
        self.assertIn(("removed", item["id"], None), self.trees.added)

    def test_reaped_worker_in_waiting_state_keeps_worktrees(self):
        item = self.item()
        self.scheduler.tick()
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.ledger.await_input(item["id"], token, "which server?")
        self.launcher.finished.append(Finished(item["id"], 0, "asked", False, "exited"))
        self.scheduler.tick()
        self.assertEqual(self.ledger.item(item["id"])["state"], "awaiting_input")
        self.assertNotIn(("removed", item["id"], None), self.trees.added)

    def test_expired_lease_with_dead_pid_is_recovered_and_relaunched(self):
        item = self.item()
        self.scheduler.tick()
        self.ledger.claim(item["id"], worker_id="w")
        self.launcher.finished.append(Finished(item["id"], 0, "", False, "exited"))
        self.now += FIX_LEASE + 1
        self.scheduler.tick()
        self.assertEqual(self.ledger.item(item["id"])["worker_pid"], 102)
        self.assertEqual(len(self.launcher.spawned), 2)

    def test_awaiting_resource_stays_parked_in_phase_1a(self):
        item = self.item()
        self.scheduler.tick()
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.ledger.await_resource(item["id"], token, "unity_slot", "batch")
        self.launcher.finished.append(Finished(item["id"], 0, "", False, "exited"))
        self.scheduler.tick()
        self.assertEqual(self.ledger.item(item["id"])["state"], "awaiting_resource")
        self.assertEqual(len(self.launcher.spawned), 1)

    def test_stop_kills_and_cancels(self):
        item = self.item()
        self.scheduler.tick()
        self.scheduler.stop(item["id"], "Linear stop")
        self.assertEqual(self.launcher.stopped, [item["id"]])
        self.assertEqual(self.ledger.item(item["id"])["state"], "cancelled")

    def test_item_requeued_by_finish_is_relaunched_not_failed(self):
        item = self.item()
        self.scheduler.tick()
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        action = self.ledger.prepare_comment(item["id"], token, "delivery", "已修复，见 PR。")
        self.ledger.confirm_comment(action["action_id"], "remote-delivery-1")
        self.ledger.observe_issue(issue(comments=[comment("安卓上也能复现")]))
        view = self.ledger.finish(item["id"], token, "delivered",
                                  {"summary": "done", "comment_action_id": action["action_id"],
                                   "verification": "dotnet tests", "prs": ["https://github.com/o/r/pull/3"]})
        self.assertEqual((view["state"], view["worker_pid"]), ("queued", None))
        self.launcher.finished.append(Finished(item["id"], 0, "", False, "exited"))
        self.scheduler.tick()
        after = self.ledger.item(item["id"])
        self.assertIn(after["state"], ("running", "queued"))
        self.assertEqual(after["worker_pid"], 102)
        self.assertEqual(len(self.launcher.spawned), 2)

    def test_retry_after_stop_relaunches_the_item(self):
        item = self.item()
        self.scheduler.tick()
        self.scheduler.stop(item["id"], "Linear stop")
        self.ledger.retry(item["id"], "human asked 重试")
        self.scheduler.tick()
        self.assertNotEqual(self.ledger.item(item["id"])["state"], "failed")
        self.assertEqual(len(self.launcher.spawned), 2)

    def test_stop_kills_the_worker_without_waiting_for_the_scheduler_lock(self):
        item = self.item()
        self.scheduler.tick()
        threaded = Ledger(Path(self.tmp.name) / "ledger.sqlite3", clock=lambda: self.now, lease_seconds=60,
                          check_same_thread=False)
        self.addCleanup(threaded.close)
        self.scheduler.ledger = threaded
        self.scheduler.lock.acquire()
        started = time.monotonic()
        thread = threading.Thread(target=self.scheduler.stop, args=(item["id"], "Linear stop"), daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        try:
            deadline = started + 1.0
            while not self.launcher.stopped and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(self.launcher.stopped, [item["id"]])
            self.assertLess(self.launcher.stop_times[0] - started, 1.0)
            self.assertEqual(self.ledger.item(item["id"])["state"], "queued")  # cancel still waits for the lock
        finally:
            self.scheduler.lock.release()
        thread.join(timeout=5)
        self.assertEqual(self.ledger.item(item["id"])["state"], "cancelled")

    def test_stop_during_an_in_flight_launch_still_kills_the_worker(self):
        item = self.item()
        self.launcher = self.scheduler.launcher = HandleAwareLauncher()
        threaded = Ledger(Path(self.tmp.name) / "ledger.sqlite3", clock=lambda: self.now, lease_seconds=60,
                          check_same_thread=False)
        self.addCleanup(threaded.close)
        self.scheduler.ledger = threaded
        self.scheduler.lock.acquire()  # the scheduler thread is inside tick(), about to spawn
        thread = threading.Thread(target=self.scheduler.stop, args=(item["id"], "Linear stop"), daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        try:
            deadline = time.monotonic() + 1.0
            while not self.launcher.stopped and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(self.launcher.stopped, [item["id"]])  # nothing to kill yet
            self.scheduler.launch(item)  # the tick registers the worker while Stop waits for the lock
            self.assertEqual(self.launcher.handles, {item["id"]})
        finally:
            self.scheduler.lock.release()
        thread.join(timeout=5)
        self.assertEqual(self.launcher.handles, set())  # killed once the lock was ours
        self.assertEqual(self.launcher.stopped, [item["id"], item["id"]])
        self.assertNotIn(item["id"], self.scheduler.active)
        self.assertEqual(self.ledger.item(item["id"])["state"], "cancelled")

    def test_worker_crash_after_claim_marks_the_session_errored(self):
        item = self.item()
        self.scheduler.tick()
        self.ledger.claim(item["id"], worker_id="w")
        self.launcher.finished.append(Finished(item["id"], 1, "", False, "exited"))
        self.scheduler.tick()
        self.assertEqual(self.ledger.item(item["id"])["state"], "failed")
        self.assertEqual(self.api.activities[-1][:2], (SESSION, "error"))
        self.assertIn("重试", self.api.activities[-1][2])

    def test_expired_lease_recovery_and_launch_failure_reach_the_session(self):
        item = self.item()
        self.scheduler.tick()
        self.ledger.claim(item["id"], worker_id="w")
        self.now += FIX_LEASE + 1
        self.launcher.finished.append(Finished(item["id"], 1, "", False, "exited"))
        self.scheduler.tick()
        self.assertIn(self.ledger.item(item["id"])["state"], ("queued", "running"))
        self.assertEqual([a[1] for a in self.api.activities], ["thought"])
        other = self.item(issue_id=OTHER, session="session-2")
        self.scheduler.stop(item["id"], "make room")
        self.trees.fail_on = ("Farm-Client", other["id"])
        self.scheduler.tick()
        self.assertEqual(self.ledger.item(other["id"])["state"], "failed")
        self.assertEqual(self.api.activities[-1][:2], ("session-2", "error"))

    def test_a_failing_linear_api_never_breaks_the_tick(self):
        self.scheduler.api = FakeAPI(fail=True)
        item = self.item()
        self.scheduler.tick()
        self.launcher.finished.append(Finished(item["id"], 0, "", False, "exited"))
        self.assertEqual(self.scheduler.tick()["reaped"], 1)
        self.assertEqual(self.ledger.item(item["id"])["state"], "failed")

    def test_worker_exiting_cleanly_before_claim_fails_the_item(self):
        item = self.item()
        self.scheduler.tick()
        self.launcher.finished.append(Finished(item["id"], 0, "", False, "exited"))
        self.scheduler.tick()
        self.assertEqual(self.ledger.item(item["id"])["state"], "failed")

    def test_launched_but_unclaimed_item_with_dead_pid_is_failed_on_next_tick(self):
        item = self.item()
        self.scheduler.tick()
        self.scheduler.active.clear()
        self.scheduler.tick()
        self.assertEqual(self.ledger.item(item["id"])["state"], "failed")
        self.assertEqual(len(self.launcher.spawned), 1)

    def test_restart_with_live_expired_worker_kills_it_instead_of_relaunching(self):
        item = self.item()
        self.scheduler.tick()
        pid = self.ledger.item(item["id"])["worker_pid"]
        self.ledger.claim(item["id"], worker_id="w")
        self.scheduler.active.clear()
        self.launcher.alive_pids.add(pid)
        self.now += FIX_LEASE + 1
        self.scheduler.tick()
        self.assertEqual(self.launcher.killed, [pid])
        self.assertEqual(self.ledger.item(item["id"])["state"], "failed")
        self.assertEqual(len(self.launcher.spawned), 1)

    def test_launched_worker_that_never_claims_is_stopped_after_the_timeout(self):
        item = self.item()
        self.scheduler.tick()
        pid = self.ledger.item(item["id"])["worker_pid"]
        self.launcher.alive_pids.add(pid)
        self.now += 601
        self.scheduler.tick()
        self.assertEqual(self.launcher.stopped, [item["id"]])
        self.assertEqual(self.ledger.item(item["id"])["state"], "failed")

    def test_worktree_failure_fails_only_that_item_and_the_queue_keeps_moving(self):
        bad = self.item()
        good = self.item(issue_id=OTHER, session="s2", identifier="FARM-2", priority=4)
        self.trees.fail_on = ("farm-hive", bad["id"])
        self.scheduler.tick()
        self.assertEqual(self.ledger.item(bad["id"])["state"], "failed")
        self.assertEqual([s[0] for s in self.launcher.spawned], [good["id"]])
        self.assertIn(("removed", bad["id"], None), self.trees.added)
