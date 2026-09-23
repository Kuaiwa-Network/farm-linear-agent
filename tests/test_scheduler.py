import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from agent.launcher import Finished, Handle, Launcher, RUNTIMES
from agent.ledger import Ledger
from agent.scheduler import Scheduler
from agent.skills import load_skills
from agent.slots import SlotPool, slot_entry
from test_ledger import ISSUE, OTHER, PIN, SESSION, comment, issue

ROOT = Path(__file__).resolve().parents[1]
SKILLS = load_skills(ROOT / "skills")
FIX_LEASE = SKILLS["fix"].budget["lease_seconds"]  # the launcher records it, so claims last this long


class FakeLauncher:
    # The scheduler builds an injected MCP server entry in the runtime's own shape, so a double that did not
    # carry a runtime would make the codex/claude distinction untestable here.
    runtime = RUNTIMES["fake"]

    def __init__(self, runs="/fake/runs"):
        self.runs = Path(runs)
        self.spawned = []
        self.finished = []
        self.stopped = []
        self.stop_times = []
        self.next_pid = 100
        self.alive_pids = set()
        self.killed = []
        # The ordering mechanism. Both orders leave the same end state, so the only way to assert that
        # Stop cancels before it kills is to sample the ledger at the instant stop() reaches the fake.
        self.on_stop = None
        self.unsandboxed_stopped = []
        self.order = []

    def state_dir(self, item_id):
        return self.runs / item_id

    def spawn(self, item_id, message, mcp_servers, budget_seconds, cwd, extra_env=None, writable=(), cancelled=None):
        self.next_pid += 1
        self.spawned.append((item_id, message, mcp_servers, budget_seconds, str(cwd)))
        self.spawn_env = dict(extra_env or {})
        self.spawn_writable = [str(path) for path in writable]
        return Handle(item_id, self.next_pid, time.time(), time.time() + budget_seconds, Path(cwd), None, Path(cwd) / "last")

    def poll(self):
        finished, self.finished = self.finished, []
        return finished

    def stop(self, item_id, grace=5.0):
        self.on_stop and self.on_stop(item_id)
        self.order.append(("worker", item_id))
        self.stopped.append(item_id)
        self.stop_times.append(time.monotonic())
        return True

    def stop_unsandboxed(self, owner):
        """The batch Editor's kill. Present on the real Launcher since Task 7; without it here the double
        stops matching the collaborator and Scheduler.stop raises AttributeError in every Stop test."""
        self.unsandboxed_stopped.append(owner)
        self.order.append(("unsandboxed", owner))
        return True

    def running(self):
        return {}

    def alive(self, pid):
        return pid in self.alive_pids

    def owned_pid(self, pid, item_id):
        return pid in self.alive_pids

    def kill_owned_attempt(self, item_id, pid):
        return self.kill_pid(pid)

    def assert_quiescent(self, item_id, pid, recorded_processes=()):
        if any(self.alive(p) for p in recorded_processes):
            raise RuntimeError("worker processes have not exited")
        for path in self.state_dir(item_id).glob("*/killed.json"):
            if any(self.alive(p) for p in json.loads(path.read_text())["descendants"]):
                raise RuntimeError("worker descendants have not exited")

    def descendants(self, pid):
        return []

    def kill_pid(self, pid, grace=5.0):
        self.killed.append(pid)
        self.alive_pids.discard(pid)
        return [pid]


class HandleAwareLauncher(FakeLauncher):
    """Like the real launcher, stop() only kills a worker whose handle spawn() has registered."""

    def __init__(self):
        super().__init__()
        self.handles = set()

    def spawn(self, item_id, message, mcp_servers, budget_seconds, cwd, extra_env=None, writable=(), cancelled=None):
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
        self.comments = []
        self.fail = fail

    def create_activity(self, session_id, content, activity_id=None):
        if self.fail:
            raise RuntimeError("linear down")
        self.activities.append((session_id, content["type"], content["body"]))
        return {"success": True}

    def create_comment(self, issue_id, body):
        if self.fail:
            raise RuntimeError("linear down")
        self.comments.append((issue_id, body))
        return f"stub-comment-{len(self.comments)}"


class FakeWorktrees:
    def __init__(self, root):
        self.root = Path(root)
        self.added = []
        self.fail_on = None
        self.commit_fails = False

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

    def commit_wip(self, item_id, message):
        if self.commit_fails:
            raise RuntimeError("git is unwell")
        self.added.append(("committed", item_id, message))
        return {"committed": {}, "errors": {}}

    def preserve(self, item_id):
        report = self.commit_wip(item_id, f"wip({item_id[:8]}): preserve ended work")
        return {**report, "refs": {}}

    def remove_preserved(self, item_id, evidence):
        if evidence["errors"]:
            raise RuntimeError("preservation incomplete")
        self.remove(item_id)

    def remove(self, item_id):
        self.added.append(("removed", item_id, None))


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 1000.0
        self.ledger = Ledger(Path(self.tmp.name) / "ledger.sqlite3", clock=lambda: self.now, lease_seconds=60)
        self.addCleanup(self.ledger.close)
        # A real runs root, not a fictional one: the scheduler reads the pool's batch summary out of the
        # item's state directory, so a path nothing can be written to would make that unreadable by design.
        self.launcher = FakeLauncher(Path(self.tmp.name) / "runs")
        self.trees = FakeWorktrees(Path(self.tmp.name) / "wt")
        self.api = FakeAPI()
        # A real folder and a real (empty) file: after this task the scheduler resolves no Editor at all, but
        # a slot entry that named neither would let a later change quietly reach into /Applications.
        self.slot_folder = Path(self.tmp.name) / "editors" / "slot-1"
        (self.slot_folder / "ProjectSettings").mkdir(parents=True)
        (self.slot_folder / "ProjectSettings" / "ProjectVersion.txt").write_text(
            "m_EditorVersion: 2022.3.62f1\n", encoding="utf-8")
        self.unity_binary = Path(self.tmp.name) / "unity-binary"
        self.unity_binary.touch()
        self.slot_entry = slot_entry({"id": "unity_slot:1", "repo": "Farm-Client", "unity": str(self.unity_binary),
                                      "folder": str(self.slot_folder), "build_target_argument": "OSXUniversal"})
        self.scheduler = Scheduler(self.ledger, self.launcher, SKILLS, self.trees,
                                   skill_root=ROOT / "skills", db_path=Path(self.tmp.name) / "ledger.sqlite3",
                                   runtime_name="fake", host="h", max_concurrent=1,
                                   slot_entries={self.slot_entry["id"]: self.slot_entry},
                                   guidance_for=lambda item: (self.ledger.session(item["session_id"]) or {}).get("guidance") or "",
                                   api=self.api)

    def item(self, issue_id=ISSUE, session=SESSION, skill="fix", **changes):
        self.ledger.observe_issue(issue(id=issue_id, **changes))
        self.ledger.ensure_session(session, issue_id, delegation=True)
        # The pin travels with the item: await_resource refuses a slot request from an unpinned one.
        return self.ledger.create_work_item(issue_id=issue_id, session_id=session, skill=skill, target=PIN)

    def test_conversation_launch_excludes_repository_write_roots(self):
        chat = self.item(skill="chat")
        self.scheduler.tick()
        payload = json.loads(self.launcher.spawned[-1][1].split('\n\n', 1)[1])
        self.assertEqual(self.launcher.spawned[-1][4], str(self.launcher.state_dir(chat["id"])))
        self.assertEqual(self.launcher.spawn_writable, [str(Path(self.tmp.name))])
        self.assertEqual(set(payload["worktrees"]), {"Farm-Client"})

    def test_first_repair_launch_gets_write_profile_budget_and_conversation(self):
        app = "e5a8c16d-9f85-4123-acf5-94e41c3304d5"
        chat = self.item(skill="chat", delegate_id=app)
        self.ledger.set_session_target(SESSION, PIN)
        self.ledger.push_inbox(chat["id"], "修复显示，保持排序规则")
        self.scheduler.tick()
        token = self.ledger.claim(chat["id"], worker_id="conversation")["token"]
        message = self.ledger.issue_context(chat["id"])["session_messages"][-1]["id"]
        fix = self.ledger.request_repair(chat["id"], token, message, app, "Confirmed display refresh issue.")
        self.launcher.finished.append(Finished(chat["id"], 0, "", False, "exited"))
        self.assertEqual(self.scheduler.tick()["launched"], 1)
        launched = self.launcher.spawned[-1]
        payload = json.loads(launched[1].split('\n\n', 1)[1])
        self.assertEqual(launched[0], fix["id"])
        self.assertEqual(launched[3], 8 * 3600)
        self.assertEqual(set(payload["worktrees"]), {"Farm-Client", "farm-hive", "farmgui", "common", "Farm-Contract"})
        self.assertEqual(payload["lease_seconds"], 2700)
        self.assertEqual(payload["user_requests"][-1]["body"], "修复显示，保持排序规则")
        self.assertEqual(self.ledger.issue_context(fix["id"])["conversation_history"][0]["summary"],
                         "Confirmed display refresh issue.")

    def test_stop_selected_before_handoff_cancels_and_signals_repair_destination(self):
        app = "e5a8c16d-9f85-4123-acf5-94e41c3304d5"
        chat = self.item(skill="chat", delegate_id=app)
        token = self.ledger.claim(chat["id"], worker_id="conversation")["token"]
        self.ledger.push_inbox(chat["id"], "修复")
        selected_before_handoff = self.ledger.active_item_for_session(SESSION)
        message = self.ledger.issue_context(chat["id"])["session_messages"][-1]["id"]
        fix = self.ledger.request_repair(chat["id"], token, message, app, "Fix the display.")
        self.scheduler.tick()
        states_at_stop = []
        self.launcher.on_stop = lambda item_id: states_at_stop.append((item_id, self.ledger.item(item_id)["state"]))
        self.scheduler.stop(selected_before_handoff["id"], "Linear stop")
        self.assertEqual(self.ledger.item(fix["id"])["state"], "cancelled")
        self.assertIn((fix["id"], "cancelled"), states_at_stop)
        self.assertIn(fix["id"], self.launcher.unsandboxed_stopped)

    def test_recovery_fencing_evidence_survives_restart_before_detach(self):
        item = self.item()
        launcher = Launcher(Path(self.tmp.name) / 'real-runs', RUNTIMES['fake'], 'h')
        attempt = launcher.state_dir(item['id']) / 'attempt-1'
        attempt.mkdir(parents=True)
        (attempt / 'process.json').write_text(json.dumps({'pid': 4242}), encoding='utf-8')
        alive = {4242, 4243}
        launcher.alive = lambda pid: pid in alive
        launcher.owned_pid = lambda pid, owner: pid == 4242 and owner == item['id']
        launcher.descendants = lambda pid: [4243]
        launcher.certified_pids = lambda *args: set()
        launcher._signal_pid = lambda pid, sig: alive.discard(pid)
        self.scheduler.launcher = launcher
        record = {'item_id': item['id'], 'worker_pid': 4242}
        self.scheduler.fence_resource_worker(record)
        self.assertEqual(json.loads((attempt / 'killed.json').read_text())['descendants'], [4243])
        # Same production quiescence proof, no in-memory targets on the retry.
        self.scheduler.fence_resource_worker(record)
        self.assertEqual(alive, set())

    def test_interrupted_teardown_retains_orphan_evidence_without_signalling_uncertain_pid(self):
        item = self.item()
        launcher = Launcher(Path(self.tmp.name) / 'real-runs', RUNTIMES['fake'], 'h')
        attempt = launcher.state_dir(item['id']) / 'attempt-1'
        attempt.mkdir(parents=True)
        (attempt / 'process.json').write_text(json.dumps({'pid': 4242}), encoding='utf-8')
        (attempt / 'killed.json').write_text(json.dumps({'pid': 4242, 'descendants': [4243, 4244]}), encoding='utf-8')
        alive = {4242, 4244}
        launcher.alive = lambda pid: pid in alive
        launcher.owned_pid = lambda pid, owner: pid == 4242 and owner == item['id']
        launcher.descendants = lambda pid: []
        launcher.certified_pids = lambda *args: set()
        launcher._signal_pid = lambda pid, sig: alive.discard(pid)
        self.scheduler.launcher = launcher
        with self.assertRaisesRegex(RuntimeError, 'descendants|processes'):
            self.scheduler.fence_resource_worker({'item_id': item['id'], 'worker_pid': 4242})
        self.assertEqual(alive, {4244})
        self.assertIn(4244, json.loads((attempt / 'killed.json').read_text())['descendants'])

    def test_resumed_worker_gets_fresh_publication_scope_and_user_reply(self):
        item = self.item()
        token = self.ledger.claim(item['id'], worker_id='old')['token']
        self.ledger.await_input(item['id'], token, 'May I publish?')
        self.ledger.push_inbox(item['id'], 'Create the draft PR.', resume_waiting=True)
        scopes = []
        class Verifier:
            def scope(inner, **kwargs):
                scopes.append(kwargs)
                return {'repositories': {'farmgui': {'status': 'verified', 'branch': 'farmbot/farm-1',
                        'url': 'https://github.com/Kuaiwa-Network/farmgui'}}}
        self.scheduler.publication = Verifier()
        self.scheduler.tick()
        payload = json.loads(self.launcher.spawned[-1][1].split('\n\n', 1)[1])
        self.assertEqual(payload['publication']['repositories']['farmgui']['branch'], 'farmbot/farm-1')
        self.assertEqual(payload['user_requests'][0]['body'], 'Create the draft PR.')
        self.assertTrue(scopes[0]['delegated'])

    def waiting_item(self, mode="batch", issue_id=ISSUE, session=SESSION):
        """An item whose slot request is still queued: nothing has been acquired, so no slot is held."""
        item = self.item(issue_id=issue_id, session=session)
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.ledger.await_resource(item["id"], token, "unity_slot", mode)
        return item["id"]

    def granted_item(self, mode="batch", issue_id=ISSUE, session=SESSION):
        """The state the pool leaves behind: the item is queued again, its reservation is active, and the
        slot is in the mode's busy state.

        set_slot_state stands in for SlotPool.switch, which is what writes the busy state in production —
        acquire alone leaves the slot 'switching', and no pool thread runs in this module. In batch mode it
        also leaves the summary run_batch wrote, which is the only reason a fresh batch worker has evidence.
        """
        item_id = self.waiting_item(mode, issue_id=issue_id, session=session)
        self.ledger.ensure_slot("unity_slot:1", kind="unity_slot", host="h", folder=str(self.slot_folder),
                                mcp_address="http://127.0.0.1:8080/mcp", instance="slot-1@0123456789abcdef")
        self.ledger.acquire("unity_slot", owner="pool", host="h")
        self.ledger.set_slot_state("unity_slot:1", SlotPool.BUSY_FOR[mode])
        if mode == "batch":
            state_dir = Path(self.launcher.state_dir(item_id))
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / "unity-batch.json").write_text(json.dumps(
                {"state": "ran", "exit_code": 2, "seconds": 19.0, "total": 4388, "passed": 4362, "failed": 26,
                 "result": "Failed(Child)", "results_file": str(state_dir / "unity-tests.xml"),
                 "log_file": str(state_dir / "unity-editor.log")}), encoding="utf-8")
        self.ledger.resume(item_id, "unity_slot:1 acquired")
        return item_id

    def test_new_launch_gets_current_memory_snapshot(self):
        from test_memory import NOTE, replacement
        note = self.ledger.memory_admin("save", value=NOTE)
        first = self.item()
        self.scheduler.launch(first)
        payload = json.loads(self.launcher.spawned[-1][1].split("\n\n", 1)[1])
        index = Path(payload["memory"]["index"])
        self.assertTrue(index.is_file())
        self.assertIn(NOTE["body"], (index.parent / (note["id"] + ".md")).read_text())
        self.assertNotIn(NOTE["body"], self.launcher.spawned[-1][1])
        self.ledger.memory_admin("save", value=replacement(note, body="new observation"))
        second = self.item(issue_id=OTHER, session="second")
        self.scheduler.launch(second)
        newer = json.loads(self.launcher.spawned[-1][1].split("\n\n", 1)[1])["memory"]
        self.assertNotEqual(newer["index"], str(index))
        self.assertIn("new observation", (Path(newer["index"]).parent / (note["id"] + ".md")).read_text())

    def test_memory_publication_failure_does_not_prevent_work(self):
        from unittest.mock import patch
        with patch("agent.scheduler.publish_snapshot", side_effect=OSError("private path detail")):
            self.scheduler.launch(self.item())
        message = self.launcher.spawned[-1][1]
        view = json.loads(message.split("\n\n", 1)[1])["memory"]
        self.assertEqual(view, {"status": "unavailable", "index": None, "reason": "OSError"})
        self.assertNotIn("private path detail", message)

    def test_tick_launches_fix_with_write_worktrees_and_records_pid(self):
        item = self.item()
        counts = self.scheduler.tick()
        self.assertEqual(counts["launched"], 1)
        launched = self.launcher.spawned[0]
        payload = json.loads(launched[1].split("\n\n", 1)[1])
        self.assertEqual(set(payload["worktrees"]), {"Farm-Client", "farm-hive", "farmgui", "common", "Farm-Contract"})
        self.assertEqual(launched[2], {})
        self.assertEqual(launched[3], 8 * 3600)
        self.assertEqual(self.ledger.item(item["id"])["worker_pid"], 101)
        self.assertIn(("Farm-Client", item["id"], "farmbot/farm-1"), self.trees.added)

    def test_launch_gives_the_worker_a_state_dir_pythonpath_and_writable_roots(self):
        item = self.item()
        self.scheduler.tick()
        payload = json.loads(self.launcher.spawned[0][1].split("\n\n", 1)[1])
        self.assertEqual(payload["state_dir"], str(self.launcher.state_dir(item["id"])))
        self.assertTrue(self.launcher.spawn_env["PYTHONPATH"].split(os.pathsep)[0] == str(ROOT))
        self.assertEqual(self.launcher.spawn_env["FARMBOT_DB"], str(Path(self.tmp.name) / "ledger.sqlite3"))
        repos = ("Farm-Client", "farm-hive", "farmgui", "common", "Farm-Contract")
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

    def test_launch_tells_the_worker_the_default_bot_name(self):
        self.item()
        self.scheduler.tick()
        self.assertEqual(json.loads(self.launcher.spawned[0][1].split("\n\n", 1)[1])["bot_name"], "FarmBot")

    def test_a_named_instance_signs_launches_and_launch_failures_with_its_own_name(self):
        self.scheduler.bot_name = "TestBot"
        self.item()
        self.scheduler.tick()
        self.assertEqual(json.loads(self.launcher.spawned[0][1].split("\n\n", 1)[1])["bot_name"], "TestBot")
        other = self.item(issue_id=OTHER, session="session-2")
        self.trees.fail_on = ("Farm-Client", other["id"])
        self.scheduler.max_concurrent = 2
        self.scheduler.tick()
        self.assertEqual(self.api.activities[-1], ("session-2", "error",
                         "TestBot 无法启动工作进程（RuntimeError），工作项已标记失败；可回复「重试」。"))

    def test_launch_failure_keeps_the_production_text_by_default(self):
        item = self.item()
        self.trees.fail_on = ("Farm-Client", item["id"])
        self.scheduler.tick()
        self.assertEqual(self.api.activities[-1], (SESSION, "error",
                         "FarmBot 无法启动工作进程（RuntimeError），工作项已标记失败；可回复「重试」。"))

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

    def failed_item(self):
        item = self.item()
        self.scheduler.tick()
        self.ledger.claim(item["id"], worker_id="w")
        self.launcher.finished.append(Finished(item["id"], 0, "", False, "exited"))
        self.scheduler.tick()
        return item["id"]

    def test_a_failed_items_work_is_committed_before_its_worktrees_are_swept(self):
        """The live rehearsal's Finding 3: a failure is exactly when an operator wants to see what the
        worker did, and remove() is destructive."""
        item_id = self.failed_item()
        self.assertEqual(self.ledger.item(item_id)["state"], "failed")
        self.assert_committed_before_removal(item_id)

    def test_a_delivered_item_is_preserved_before_sweeping(self):
        item = self.item()
        self.scheduler.tick()
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        action = self.ledger.prepare_comment(item["id"], token, "delivery", "已修复。")
        self.ledger.confirm_comment(action["action_id"], "remote-1")
        self.ledger.finish(item["id"], token, "delivered",
                           {"summary": "done", "comment_action_id": action["action_id"],
                            "verification": "dotnet test", "prs": ["https://github.com/o/r/pull/1"]})
        self.launcher.finished.append(Finished(item["id"], 0, "", False, "exited"))
        self.scheduler.tick()
        self.assertIn(("removed", item["id"], None), self.trees.added)
        self.assert_committed_before_removal(item["id"])

    def assert_committed_before_removal(self, item_id):
        message = f"wip({item_id[:8]}): preserve ended work"
        self.assertIn(("committed", item_id, message), self.trees.added)
        self.assertLess(self.trees.added.index(("committed", item_id, message)),
                        self.trees.added.index(("removed", item_id, None)))

    def test_a_worker_that_never_claims_has_its_work_committed_before_the_sweep(self):
        """_recover's claim-timeout branch retires the item itself; nothing else in the tick would."""
        item = self.item()
        self.scheduler.tick()
        self.launcher.alive_pids.add(self.ledger.item(item["id"])["worker_pid"])
        self.now += self.scheduler.claim_timeout + 1
        self.scheduler.tick()
        self.assertEqual(self.ledger.item(item["id"])["state"], "failed")
        self.assert_committed_before_removal(item["id"])

    def test_a_lease_that_expired_under_a_live_worker_commits_before_the_sweep(self):
        """_recover kills and fails this one but removes nothing, so _sweep_worktrees is the only retirer."""
        item = self.item()
        self.scheduler.tick()
        pid = self.ledger.item(item["id"])["worker_pid"]
        self.ledger.claim(item["id"], worker_id="w")
        self.scheduler.active.clear()
        self.launcher.alive_pids.add(pid)
        self.now += FIX_LEASE + 1
        self.scheduler.tick()
        self.assertEqual((self.ledger.item(item["id"])["state"], self.launcher.killed), ("failed", [pid]))
        self.assert_committed_before_removal(item["id"])

    def test_a_failing_work_in_progress_commit_retains_files_and_reports_error(self):
        self.trees.commit_fails = True
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            item_id = self.failed_item()
        self.assertEqual(self.ledger.item(item_id)["state"], "failed")
        self.assertNotIn(("removed", item_id, None), self.trees.added)
        self.assertIn("git is unwell", self.ledger.cleanup_record(item_id)["error"])

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

    def test_awaiting_resource_is_never_the_schedulers_to_launch(self):
        """The brief said to delete this with Task 6, on the grounds that the behaviour was Phase 1a's on
        purpose and stops being true here. It does not: the pool thread resumes a granted item to 'queued'
        and the scheduler picks it up from `ledger.queue()` exactly as before, so the scheduler still must
        not launch an item that is waiting for a slot. Deleting the test would drop that guard; only the
        name was Phase 1a's."""
        item = self.item()
        self.scheduler.tick()
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.ledger.await_resource(item["id"], token, "unity_slot", "batch")
        self.launcher.finished.append(Finished(item["id"], 0, "", False, "exited"))
        self.scheduler.tick()
        self.assertEqual(self.ledger.item(item["id"])["state"], "awaiting_resource")
        self.assertEqual(len(self.launcher.spawned), 1)

    def test_a_batch_reservation_gives_the_worker_results_and_neither_an_argv_nor_the_slot(self):
        """The worker does not run Unity: under the Codex seatbelt the Editor hangs for ever on a denied Mach
        lookup (Task 0's addendum), so the pool ran it already and this is the evidence. Two absences matter
        as much as the presence — no argv to execute, and no write access to the slot folder."""
        item = self.granted_item(mode="batch")
        self.scheduler.tick()
        item_id, message, servers, _, _ = self.launcher.spawned[-1]
        self.assertEqual((item_id, servers), (item, {}))
        self.assertNotIn(str(self.slot_folder), self.launcher.spawn_writable)
        payload = json.loads(message.split("\n\n", 1)[1])
        self.assertNotIn("batch_command", payload["resource"])
        self.assertNotIn("unity", payload["resource"])   # nor the binary it would have run
        self.assertNotIn(str(self.unity_binary), message)
        self.assertEqual(payload["resource"]["mode"], "batch")
        self.assertEqual(payload["resource"]["slot"], "unity_slot:1")
        self.assertEqual(payload["resource"]["batch_result"]["total"], 4388)
        self.assertTrue(payload["resource"]["batch_result"]["results_file"].endswith("unity-tests.xml"))

    def test_a_batch_worker_whose_run_left_no_summary_is_told_it_has_no_evidence(self):
        """Missing is itself a verification gap and has to be represented rather than dropped: a batch worker
        with no batch_result key at all reads as "no slot" instead of "no evidence", and rung 4 of the skill
        tells it to report a gap as neither a pass nor a failure."""
        item = self.granted_item(mode="batch")
        (Path(self.launcher.state_dir(item)) / "unity-batch.json").unlink()
        self.scheduler.tick()
        payload = json.loads(self.launcher.spawned[-1][1].split("\n\n", 1)[1])
        self.assertEqual(payload["resource"]["batch_result"]["state"], "gap")
        self.assertIsNone(payload["resource"]["batch_result"]["results_file"])

    def test_an_interactive_reservation_injects_the_slot_address_and_not_its_folder(self):
        item = self.granted_item(mode="interactive")
        self.scheduler.tick()
        _, message, servers, _, _ = self.launcher.spawned[-1]
        self.assertEqual(servers, {"unity": {"url": "http://127.0.0.1:8080/mcp"}})
        self.assertNotIn(str(self.slot_folder), self.launcher.spawn_writable)
        payload = json.loads(message.split("\n\n", 1)[1])
        self.assertEqual(payload["resource"]["mode"], "interactive")
        self.assertEqual(payload["resource"]["instance"], "slot-1@0123456789abcdef")
        self.assertEqual(payload["resource"]["commit"], "a" * 40)
        self.assertTrue(payload["resource"]["token_file"].endswith("reservation.token"))
        self.assertNotIn("res_", message)  # the token itself never reaches the prompt on disk
        # No run happened and none is offered: an Editor is live on that folder and run_tests over MCP is
        # the verify path (Task 0 Step 5).
        self.assertIsNone(payload["resource"]["batch_result"])
        self.assertNotIn("batch_command", payload["resource"])

    def test_the_injected_server_takes_the_shape_the_claude_runtime_needs(self):
        """§19's first risk is that the default runtime switches after Task 0. write_mcp_config's JSON writer
        emits mcpServers, where an entry with a bare url and no type is not an HTTP server at all, so an
        interactive slot under `claude -p` would silently lose its only tool."""
        self.launcher.runtime = RUNTIMES["claude"]
        self.granted_item(mode="interactive")
        self.scheduler.tick()
        self.assertEqual(self.launcher.spawned[-1][2],
                         {"unity": {"type": "http", "url": "http://127.0.0.1:8080/mcp"}})

    def test_an_item_with_no_reservation_is_launched_with_no_tools_and_no_resource_block(self):
        """Enforcement is tool injection (spec §7): a fix worker that holds no slot must not reach the Unity
        MCP, and the manifest's own `resources`/`mcp` fields must never be what decides that."""
        self.waiting_item(mode="interactive")   # queued, never acquired: this item holds nothing
        self.item(issue_id=OTHER, session="s2", identifier="FARM-2")
        self.scheduler.tick()
        item_id, message, servers, _, _ = self.launcher.spawned[-1]
        self.assertEqual(servers, {})
        self.assertIsNone(json.loads(message.split("\n\n", 1)[1])["resource"])

    def test_a_skill_whose_manifest_claims_no_slot_is_given_none_even_holding_one(self):
        """The manifest is not the authority on tools — the reservation is — but it is the second gate, and
        without it a skill that never declared `unity_slot` would still be handed the Editor by the mere
        presence of a row. `chat` declares `resources: []`, so it gets nothing whatever the ledger says."""
        item_id = self.granted_item(mode="interactive")
        self.ledger.connection.execute("UPDATE work_items SET skill='chat' WHERE id=?", (item_id,))
        self.ledger.connection.commit()
        self.assertEqual(SKILLS["chat"].resources, ())
        self.scheduler.tick()
        spawned_id, message, servers, _, _ = self.launcher.spawned[-1]
        self.assertEqual((spawned_id, servers), (item_id, {}))
        self.assertIsNone(json.loads(message.split("\n\n", 1)[1])["resource"])

    def test_stop_kills_and_cancels(self):
        item = self.item()
        self.scheduler.tick()
        self.scheduler.stop(item["id"], "Linear stop")
        self.assertEqual(self.launcher.stopped, [item["id"]])
        self.assertEqual(self.ledger.item(item["id"])["state"], "cancelled")

    def cancel_with_cli(self, item_id):
        result = subprocess.run(
            [sys.executable, "-B", "-W", "error", "-m", "agent", "--db", str(self.scheduler.db_path),
             "cancel", "--item", item_id, "--reason", "operator cancellation"],
            cwd=ROOT, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["state"], "cancelled")

    def retry_with_cli(self, item_id):
        stub = Path(self.tmp.name) / "stub"
        stub.mkdir(exist_ok=True)
        (stub / "issue.json").write_text(json.dumps({**self.ledger.issue(self.ledger.item(item_id)["issue_id"]),
                                                    "delegate_id": "e5a8c16d-9f85-4123-acf5-94e41c3304d5"}))
        result = subprocess.run(
            [sys.executable, "-B", "-m", "agent", "--db", str(self.scheduler.db_path),
             "retry", "--item", item_id, "--reason", "operator retry"],
            env={**os.environ, "FARMBOT_LINEAR_STUB_DIR": str(stub)},
            cwd=ROOT, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["state"], "queued")
        return json.loads(result.stdout)["id"]

    def test_cli_cancel_then_retry_before_tick_replaces_the_old_worker(self):
        runtime = RUNTIMES["fake"]._replace(command=[
            sys.executable, "-c", "import sys,time; sys.stdin.read(); time.sleep(120)"])
        launcher = Launcher(Path(self.tmp.name) / "runs", runtime, host="h")
        self.scheduler.launcher = launcher
        item_id = self.item()["id"]
        self.scheduler.tick()
        old = self.scheduler.active[item_id]
        self.addCleanup(launcher.poll)
        self.addCleanup(launcher.stop, item_id)
        self.cancel_with_cli(item_id)
        successor_id = self.retry_with_cli(item_id)
        self.scheduler.tick()
        self.assertIsNotNone(old.process.poll(), "retry hid the cancellation of the old worker")
        fresh = self.scheduler.active[successor_id]
        self.addCleanup(launcher.stop, successor_id)
        self.assertNotEqual(fresh.pid, old.pid)
        self.assertIsNone(fresh.process.poll())
        self.assertEqual((self.api.activities, self.api.comments), ([], []))

    def test_cli_retry_waits_for_the_cancelled_reservation_to_settle(self):
        item_id = self.waiting_item(mode="batch")
        self.ledger.ensure_slot("unity_slot:1", kind="unity_slot", host="h", folder=str(self.slot_folder))
        reservation = self.ledger.acquire("unity_slot", owner="pool", host="h")
        self.ledger.set_slot_state("unity_slot:1", "batch_busy")
        launcher = Launcher(Path(self.tmp.name) / "runs", RUNTIMES["fake"], host="h")
        self.scheduler.launcher = launcher
        marker = Path(self.tmp.name) / "retry-batch.pid"
        errors = []

        def run():
            try:
                launcher.run_unsandboxed(
                    [sys.executable, "-c", "import os,pathlib,sys,time; "
                     "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(120)", str(marker)],
                    cwd=self.tmp.name, timeout=120, owner=item_id)
            except BaseException as exc:
                errors.append(exc)

        runner = threading.Thread(target=run, daemon=True)
        runner.start()
        self.addCleanup(runner.join, 15)
        self.addCleanup(launcher.stop_unsandboxed, item_id)
        self.addCleanup(launcher.poll)
        self.addCleanup(launcher.stop, item_id)
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(marker.exists(), errors)
        pid = int(marker.read_text())
        self.cancel_with_cli(item_id)
        successor_id = self.retry_with_cli(item_id)
        self.scheduler.tick()
        runner.join(2)
        self.assertFalse(runner.is_alive(), "retry hid the old batch reservation's cancellation")
        self.assertFalse(launcher.alive(pid))
        self.assertEqual(errors, [])
        self.assertNotIn(item_id, self.scheduler.active, "retry launched before the old slot was settled")
        self.assertEqual(self.ledger.item(successor_id)["state"], "queued")
        self.ledger.release(reservation["reservation_id"], reservation["token"], "pool verified quiescence")
        self.ledger.set_slot_state("unity_slot:1", "idle_closed")
        self.scheduler.tick()
        self.assertIn(successor_id, self.scheduler.active)
        self.addCleanup(launcher.stop, successor_id)
        self.assertEqual((self.api.activities, self.api.comments), ([], []))

    def test_cli_cancel_kills_a_batch_run_before_any_worker_is_resumed(self):
        """A ledger-only cancellation must reach the service-owned batch subprocess on the next tick."""
        item_id = self.waiting_item(mode="batch")
        self.ledger.ensure_slot("unity_slot:1", kind="unity_slot", host="h", folder=str(self.slot_folder))
        reservation = self.ledger.acquire("unity_slot", owner="pool", host="h")
        self.ledger.set_slot_state("unity_slot:1", "batch_busy")
        launcher = Launcher(Path(self.tmp.name) / "runs", RUNTIMES["fake"], host="h")
        self.scheduler.launcher = launcher
        marker = Path(self.tmp.name) / "batch.pid"
        errors = []

        def run():
            try:
                launcher.run_unsandboxed(
                    [sys.executable, "-c", "import os,pathlib,sys,time; "
                     "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(120)", str(marker)],
                    cwd=self.tmp.name, timeout=120, owner=item_id)
            except BaseException as exc:
                errors.append(exc)

        runner = threading.Thread(target=run, daemon=True)
        runner.start()
        self.addCleanup(runner.join, 15)
        self.addCleanup(launcher.stop_unsandboxed, item_id)
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(marker.exists(), errors)
        pid = int(marker.read_text())
        self.assertTrue(launcher.alive(pid))
        self.assertNotIn(item_id, self.scheduler.active)
        self.cancel_with_cli(item_id)
        self.scheduler.tick()
        runner.join(2)
        self.assertFalse(runner.is_alive(), "CLI cancellation left the batch process running")
        self.assertFalse(launcher.alive(pid))
        self.assertEqual(errors, [])
        # Cancellation does not release a slot; only the pool's quiescence probe can do that.
        self.assertEqual(self.ledger.reservation(reservation["reservation_id"])["state"], "cancel_requested")
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "batch_busy")
        self.scheduler.tick()
        self.assertEqual((self.api.activities, self.api.comments), ([], []))

    def test_cli_cancel_kills_an_owned_worker_before_sweeping_its_worktrees(self):
        """Clearing worker_pid in the external CLI must not let a live worker outlast its worktree."""
        runtime = RUNTIMES["fake"]._replace(command=[
            sys.executable, "-c", "import sys,time; sys.stdin.read(); time.sleep(120)"])
        launcher = Launcher(Path(self.tmp.name) / "runs", runtime, host="h")
        self.scheduler.launcher = launcher
        item_id = self.item()["id"]
        self.scheduler.tick()
        handle = self.scheduler.active[item_id]
        self.addCleanup(launcher.poll)
        self.addCleanup(launcher.stop, item_id)
        self.ledger.claim(item_id, worker_id="test-worker")
        self.assertIsNone(handle.process.poll())
        self.cancel_with_cli(item_id)
        self.assertIsNone(self.ledger.item(item_id)["worker_pid"])
        removed = []
        original_remove = self.trees.remove

        def remove(item):
            removed.append((item, handle.process.poll()))
            original_remove(item)

        self.trees.remove = remove
        self.scheduler.tick()
        self.assertIsNotNone(handle.process.poll(), "CLI cancellation left the worker alive")
        self.assertTrue(removed)
        self.assertTrue(all(code is not None for _, code in removed), "worktrees were swept before the worker died")
        self.assertNotIn(item_id, self.scheduler.active)
        self.scheduler.tick()
        self.assertEqual((self.api.activities, self.api.comments), ([], []))

    def test_stop_cancels_the_reservation_before_it_kills_the_worker(self):
        """Ordering is asserted, not implied: both orders leave the same end state, so the fake launcher
        samples the reservation at the moment stop() reaches it. A wall-clock assertion here would prove
        nothing — FakeLauncher.stop appends to a list and returns, so it is fast whatever the real one does.
        Done-criterion 2's five-second budget is defended by the rule that stop() only touches SQLite and
        signals, which this ordering assertion is the test of."""
        item = self.granted_item(mode="interactive")
        self.scheduler.tick()
        seen = []
        self.launcher.on_stop = lambda i: seen.append(self.ledger.active_reservation(i)["state"])
        self.scheduler.stop(item, "Linear stop")
        self.assertEqual(seen, ["cancel_requested"])
        reservation = self.ledger.active_reservation(item)
        self.assertEqual(reservation["state"], "cancel_requested")
        self.assertEqual(self.ledger.item(item)["state"], "cancelled")
        # Still held: stop() never touches the slot row. granted_item leaves it in the busy state the pool's
        # switch would have written, so this asserts that stop left it alone rather than releasing it — the
        # release is the pool's, on its next tick, after a quiescence probe that does not fit five seconds.
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "interactive_busy")

    def test_stop_on_an_item_that_is_only_waiting_kills_its_queued_request(self):
        """The end state alone is not the coverage: Ledger.cancel already cancels a queued request, so the
        last two assertions passed before this task. What is new is *when* — stop() defers its cancel until
        it holds the scheduler lock, which an in-flight tick can hold for as long as a launch takes, and for
        that whole window the pool is free to acquire this request and pay for a multi-minute slot switch on
        behalf of a worker that is already dead. So the queued request must be gone by the time of the kill,
        which is what the sampled assertion, and only it, says."""
        item = self.waiting_item(mode="batch")
        states = lambda: [r["state"] for r in self.ledger.reservations() if r["item_id"] == item]
        seen = []
        self.launcher.on_stop = lambda _: seen.append(states())
        self.scheduler.stop(item, "Linear stop")
        self.assertEqual(seen, [["cancelled"]])
        self.assertEqual(states(), ["cancelled"])
        self.assertEqual(self.ledger.item(item)["state"], "cancelled")

    def test_stop_reaches_the_batch_editor_the_worker_no_longer_owns(self):
        """This task is named 'Stop and recovery never orphan a slot', and the sandbox redesign put the one
        process that can orphan one outside everything Stop used to reach. The Editor is started by the
        launcher on the pool thread, the worker that asked for it has already exited, and `launcher.stop`
        resolves a `spawn` handle that was never written for it. So Stop must kill it explicitly, and it
        must do so BEFORE the worker kill, for the same reason the cancel comes first: the pool thread is
        sitting in `wait()` and the sooner it is released the sooner the slot can settle."""
        item = self.waiting_item(mode="batch")
        self.scheduler.stop(item, "Linear stop")
        self.assertEqual(self.launcher.unsandboxed_stopped, [item])
        self.assertLess(self.launcher.order.index(("unsandboxed", item)),
                        self.launcher.order.index(("worker", item)))

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
            self.assertEqual(self.ledger.item(item["id"])["state"], "cancelled")  # claim revoked before signalling
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
            self.assertEqual(self.launcher.handles, set())
        finally:
            self.scheduler.lock.release()
        thread.join(timeout=5)
        self.assertEqual(self.launcher.handles, set())  # killed once the lock was ours
        self.assertEqual(self.launcher.stopped, [item["id"]])
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

    def test_a_locally_enqueued_item_reports_by_issue_comment_because_it_has_no_linear_session(self):
        """`agent.service enqueue` mints a synthetic `local-` session id: no Linear agent session exists for
        it, so every create_activity for such an item would fail against the real API and §9's reporting
        surface would be silently dead for exactly the items the live rehearsal creates."""
        item = self.item(session=f"local-{ISSUE}")
        self.scheduler.tick()
        self.ledger.claim(item["id"], worker_id="w")
        self.launcher.finished.append(Finished(item["id"], 1, "", False, "exited"))
        self.scheduler.tick()
        self.assertEqual(self.ledger.item(item["id"])["state"], "failed")
        self.assertEqual(self.api.activities, [])
        self.assertEqual(self.api.comments[-1][0], ISSUE)
        self.assertIn("重试", self.api.comments[-1][1])

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
