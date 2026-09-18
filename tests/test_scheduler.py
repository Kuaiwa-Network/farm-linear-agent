import json
import tempfile
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


class FakeLauncher:
    def __init__(self):
        self.spawned = []
        self.finished = []
        self.stopped = []
        self.next_pid = 100
        self.alive_pids = set()
        self.killed = []

    def spawn(self, item_id, message, mcp_servers, budget_seconds, cwd, extra_env=None):
        self.next_pid += 1
        self.spawned.append((item_id, message, mcp_servers, budget_seconds, str(cwd)))
        return Handle(item_id, self.next_pid, time.time(), time.time() + budget_seconds, Path(cwd), None, Path(cwd) / "last")

    def poll(self):
        finished, self.finished = self.finished, []
        return finished

    def stop(self, item_id, grace=5.0):
        self.stopped.append(item_id)
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


class FakeWorktrees:
    def __init__(self, root):
        self.root = Path(root)
        self.added = []
        self.fail_on = None

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
        self.scheduler = Scheduler(self.ledger, self.launcher, load_skills(ROOT / "skills"), self.trees,
                                   skill_root=ROOT / "skills", db_path=Path(self.tmp.name) / "ledger.sqlite3",
                                   runtime_name="fake", host="h", max_concurrent=1)

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
        self.now += 61
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
        self.now += 61
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
