import contextlib
import dataclasses
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

from agent.launcher import Finished, Handle, Launcher, RUNTIMES
from agent.ledger import Ledger, LedgerError
from agent.scheduler import Scheduler
from agent.session_progress import SessionProgress
from agent.skills import load_skills
from agent.slots import SlotPool, slot_entry
from agent import dispatch, kw_ops, withdrawal
from test_ledger import DESIGNER, ISSUE, OTHER, PIN, SESSION, comment, issue
from test_skills import opt_in_skill, staged_skill

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
        self.attached = []  # the add calls that re-attach to a plan's issue branch (spec §5.7)
        self.reads = []  # (repo, item_id, refresh) for each read-only checkout made or refreshed (spec §9.6)
        self.reads_removed = []
        self.fail_on = None
        self.commit_fails = False

    def clone_path(self, repo):
        return self.root / "repos" / f"{repo}.git"

    def writable_parts(self, repo, path):
        clone = self.clone_path(repo)
        return [clone / "objects", clone / "refs", clone / "logs", clone / "lfs", clone / "worktrees" / Path(path).name]

    def add(self, repo, item_id, branch, attach=False):
        if self.fail_on == (repo, item_id):
            raise RuntimeError("boom")
        path = self.root / item_id / repo
        path.mkdir(parents=True, exist_ok=True)
        self.added.append((repo, item_id, branch))
        if attach:
            self.attached.append((repo, item_id, branch))
        return path

    def add_detached(self, repo, item_id):
        return self.add(repo, item_id, "detached")

    def read_checkout(self, repo, item_id, *, refresh=True):
        self.reads.append((repo, item_id, refresh))
        return self.root / f"{item_id}.reads" / f"{repo}@main"

    def remove_reads(self, item_id):
        self.reads_removed.append(item_id)

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
        self.assertEqual(payload["prior_context"], {
            "source": "investigator_summary", "summary": "Confirmed display refresh issue.",
            "revalidation_required": True})
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

    def test_the_launch_scope_applies_the_suffix_rules_only_to_a_job_with_an_initial_root(self):
        scopes = []

        class Verifier:
            def scope(inner, **kwargs):
                scopes.append((kwargs["item"]["skill"], kwargs.get("suffix_roles")))
                return {"repositories": {}}
        self.scheduler.publication = Verifier()
        staged = self.use_staged_skill()
        self.scheduler.max_concurrent = 2
        self.item()
        self.item(issue_id=OTHER, session="session-2", skill=staged.name)
        self.assertEqual(self.scheduler.tick()["launched"], 2)
        self.assertEqual(sorted(scopes), [("feature", True), ("fix", False)])

    def test_a_job_with_an_initial_root_never_takes_a_suffix_name_as_its_issue_branch(self):
        """A card titled "config" can have Linear's suggestion farmbot/<key>-config, and one titled "config 3"
        farmbot/<key>-config-3. A job with an initial root keeps those names for its Jenkins branches (spec §6.4; P12)
        and works on farmbot/<key>; a fix keeps the suggestion."""
        staged = self.use_staged_skill()
        self.scheduler.max_concurrent = 4
        third, fourth = "10000000-0000-4000-8000-000000000003", "10000000-0000-4000-8000-000000000004"
        expected = {"farmbot/farm-1-config": self.item(branch_name="farmbot/farm-1-config"),
                    "farmbot/farm-2": self.item(issue_id=OTHER, session="session-2", skill=staged.name,
                                                identifier="FARM-2", branch_name="farmbot/farm-2-config"),
                    "farmbot/farm-3-config-3": self.item(issue_id=third, session="session-3", identifier="FARM-3",
                                                         branch_name="farmbot/farm-3-config-3"),
                    "farmbot/farm-4": self.item(issue_id=fourth, session="session-4", skill=staged.name,
                                                identifier="FARM-4", branch_name="farmbot/farm-4-config-3")}
        self.assertEqual(self.scheduler.tick()["launched"], 4)
        branches = {item["id"]: {branch for _, owner, branch in self.trees.added if owner == item["id"]}
                    for item in expected.values()}
        self.assertEqual(branches, {item["id"]: {branch} for branch, item in expected.items()})

    def test_a_resumed_worker_is_told_who_wrote_each_reply_and_when(self):
        item = self.item()
        token = self.ledger.claim(item['id'], worker_id='old')['token']
        self.ledger.await_input(item['id'], token, 'Which server?')
        self.ledger.push_inbox(item['id'], '公共测试服', resume_waiting=True, author=DESIGNER)
        self.scheduler.tick()
        payload = json.loads(self.launcher.spawned[-1][1].split('\n\n', 1)[1])
        # The fixture's clock reads 1000.0 seconds after the epoch.
        self.assertEqual([(r['body'], r['author'], r['created_at']) for r in payload['user_requests']],
                         [('公共测试服', DESIGNER, '1970-01-01T00:16:40+00:00')])

    def waiting_item(self, mode="batch", issue_id=ISSUE, session=SESSION):
        """An item whose slot request is still queued: nothing has been acquired, so no slot is held."""
        item = self.item(issue_id=issue_id, session=session)
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.ledger.await_resource(item["id"], token, "unity_slot", mode, skill=SKILLS["fix"])
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
        self.assertEqual(payload["stage"]["root_repository"], None)
        self.assertEqual(payload["stage"]["write_repositories"], [])
        self.assertEqual(self.launcher.spawned[0][4], str(self.launcher.state_dir(item["id"])))
        self.assertEqual(self.launcher.spawn_writable[0], str(Path(self.tmp.name)))  # the ledger's directory
        self.assertEqual(self.launcher.spawn_writable[1:], [])

    def test_repository_handoff_waits_for_teardown_then_roots_one_repository(self):
        item = self.item()
        self.scheduler.tick()
        token = self.ledger.claim(item["id"], worker_id="first")["token"]
        self.ledger.checkpoint(item["id"], token, {"handoff": {
            "facts": [], "hypotheses": [], "checks": [], "repositories": [],
            "next_actions": ["Inspect Farm-Contract rules in its own worker"]}})
        pending = self.ledger.handoff_repository(item["id"], token, "Farm-Contract", skill=SKILLS["fix"])
        self.assertEqual(pending["next_root_repo"], "Farm-Contract")
        self.assertEqual(self.ledger.queue(), [])
        self.assertEqual(self.scheduler.tick()["launched"], 0)
        self.assertIn(item["id"], self.launcher.stopped)
        self.launcher.finished.append(Finished(item["id"], 0, "", True, "stopped", None, 101))
        self.assertEqual(self.scheduler.tick()["launched"], 1)
        current = self.ledger.item(item["id"])
        self.assertEqual(current["root_repo"], "Farm-Contract")
        payload = json.loads(self.launcher.spawned[-1][1].split("\n\n", 1)[1])
        self.assertEqual(payload["stage"]["write_repositories"], ["Farm-Contract"])
        self.assertEqual(payload["stage"]["root_repository"], "Farm-Contract")  # what the worker follows
        self.assertEqual(payload["prior_context"]["source"], "previous_worker_checkpoint")
        self.assertEqual(payload["prior_context"]["content"]["next_actions"],
                         ["Inspect Farm-Contract rules in its own worker"])
        self.assertEqual(self.launcher.spawned[-1][4], str(self.trees.root / item["id"] / "Farm-Contract"))
        worktree = self.trees.root / item["id"] / "Farm-Contract"
        self.assertEqual(self.launcher.spawn_writable[1:], [
            str(worktree), *(str(part) for part in self.trees.writable_parts("Farm-Contract", worktree))])

    def test_handoff_stays_pending_when_teardown_evidence_is_missing(self):
        item = self.item()
        self.scheduler.tick()
        token = self.ledger.claim(item["id"], worker_id="first")["token"]
        self.ledger.checkpoint(item["id"], token, {"handoff": {
            "facts": [], "hypotheses": [], "checks": [], "repositories": [], "next_actions": ["Check server"]}})
        self.ledger.handoff_repository(item["id"], token, "farm-hive", skill=SKILLS["fix"])
        self.launcher.finished.append(Finished(item["id"], 0, "", True, "stopped", None, 101))
        self.launcher.assert_quiescent = lambda *args: (_ for _ in ()).throw(RuntimeError("descendant alive"))
        self.assertEqual(self.scheduler.tick()["launched"], 0)
        self.assertEqual(self.ledger.item(item["id"])["next_root_repo"], "farm-hive")
        self.assertEqual(len(self.launcher.spawned), 1)

    def test_publication_scope_contains_only_the_current_repository(self):
        item = self.item()
        self.ledger.connection.execute("UPDATE work_items SET root_repo='farm-hive' WHERE id=?", (item["id"],))
        verifier = Mock()
        verifier.scope.return_value = {"repositories": {}}
        self.scheduler.publication = verifier
        self.scheduler.tick()
        self.assertEqual(set(verifier.scope.call_args.kwargs["paths"]), {"farm-hive"})

    def test_fix_refuses_runtime_without_repository_sandbox(self):
        item = self.item()
        self.scheduler.runtime_name = "claude"
        with self.assertRaisesRegex(RuntimeError, "Codex workspace-write"):
            self.scheduler.launch(item)
        self.assertEqual(self.trees.added, [])

    def use_staged_skill(self):
        """Serve and enable the fixture staged skill (initial root Farm-Contract) beside the repository's own
        skills, with the dispatch AUTHORITY entry every dispatched skill needs."""
        staged = staged_skill(Path(self.tmp.name) / "fixture-skills")
        self.scheduler.skills = {**SKILLS, staged.name: staged}
        self.scheduler.enabled_skills.add(staged.name)
        authority = patch.dict(dispatch.SKILL_AUTHORITY, {staged.name: "Fixture staged-skill grants. "})
        authority.start()
        self.addCleanup(authority.stop)
        return staged

    def test_a_skill_without_dispatch_authority_fails_at_launch_and_spawns_nothing(self):
        staged = staged_skill(Path(self.tmp.name) / "fixture-skills", name="unbriefed")
        self.scheduler.skills = {**SKILLS, staged.name: staged}
        self.scheduler.enabled_skills.add(staged.name)  # enabled, but with no AUTHORITY entry
        item = self.item(skill=staged.name)
        self.assertEqual(self.scheduler.tick()["launched"], 0)
        self.assertEqual(self.launcher.spawned, [])
        self.assertEqual(self.ledger.item(item["id"])["state"], "failed")
        reason = self.ledger.connection.execute("SELECT reason FROM audit WHERE item_id=? AND kind='failed'",
                                                (item["id"],)).fetchone()["reason"]
        self.assertIn("no dispatch AUTHORITY", reason)

    def payload(self, launch=-1):
        return json.loads(self.launcher.spawned[launch][1].split("\n\n", 1)[1])

    def test_a_staged_skill_starts_at_its_initial_root_and_restarts_there_after_retry(self):
        staged = self.use_staged_skill()
        item = self.item(skill=staged.name)
        self.scheduler.tick()
        contract = self.trees.root / item["id"] / "Farm-Contract"
        self.assertEqual(self.payload()["stage"], {"root_repository": "Farm-Contract",
                                                   "write_repositories": ["Farm-Contract"],
                                               "read_only_worktrees": ["common", "farm-hive"]})
        self.assertEqual(self.launcher.spawned[-1][4], str(contract))
        self.assertEqual(self.launcher.spawn_writable[1:],
                         [str(contract), *(str(part) for part in self.trees.writable_parts("Farm-Contract", contract))])
        self.assertIsNone(self.ledger.item(item["id"])["root_repo"])  # NULL is stored; the manifest names the root
        token = self.ledger.claim(item["id"], worker_id="first")["token"]
        self.ledger.checkpoint(item["id"], token, {"handoff": {
            "facts": [], "hypotheses": [], "checks": [], "repositories": [], "next_actions": ["Build the server"]}})
        self.ledger.handoff_repository(item["id"], token, "farm-hive", skill=staged)
        self.assertEqual(self.scheduler.tick()["launched"], 0)
        self.launcher.finished.append(Finished(item["id"], 0, "", True, "stopped", None, 101))
        self.assertEqual(self.scheduler.tick()["launched"], 1)
        self.assertEqual(self.payload()["stage"]["write_repositories"], ["farm-hive"])
        self.assertEqual(self.payload()["stage"]["root_repository"], "farm-hive")
        self.assertEqual(self.payload()["prior_context"]["content"]["next_actions"], ["Build the server"])
        # A budget kill fails the job; retry restarts it at the initial root, not at farm-hive or neutral.
        self.ledger.claim(item["id"], worker_id="second")
        self.launcher.finished.append(Finished(item["id"], -9, "", True, "budget", None, 102))
        self.scheduler.tick()
        self.assertEqual(self.ledger.item(item["id"])["state"], "failed")
        self.ledger.retry(item["id"], "operator retry")
        self.assertEqual(self.scheduler.tick()["launched"], 1)
        self.assertEqual(self.payload()["stage"]["root_repository"], "Farm-Contract")
        self.assertEqual(self.launcher.spawned[-1][4], str(contract))

    def test_the_controller_completes_a_handoff_only_as_the_manifest_allows(self):
        staged = self.use_staged_skill()
        item = self.item(skill=staged.name)
        self.scheduler.tick()
        token = self.ledger.claim(item["id"], worker_id="first")["token"]
        self.ledger.checkpoint(item["id"], token, {"handoff": {
            "facts": [], "hypotheses": [], "checks": [], "repositories": [], "next_actions": ["Declare the config"]}})
        self.ledger.handoff_repository(item["id"], token, "common", skill=staged)
        # A manifest that no longer writes the target, as after a deploy mid-handoff, leaves it pending at
        # teardown and on the recovery pass; so does a host that no longer loads the skill.
        self.scheduler.skills = {**SKILLS, staged.name: dataclasses.replace(staged, writes=("Farm-Contract",))}
        self.launcher.finished.append(Finished(item["id"], 0, "", True, "stopped", None, 101))
        self.assertEqual(self.scheduler.tick()["launched"], 0)
        self.scheduler.skills = {name: skill for name, skill in SKILLS.items() if name != staged.name}
        self.assertEqual(self.scheduler.tick()["launched"], 0)
        self.assertEqual(self.ledger.item(item["id"])["next_root_repo"], "common")
        self.scheduler.skills = {**SKILLS, staged.name: staged}
        self.assertEqual(self.scheduler.tick()["launched"], 1)
        self.assertEqual(self.ledger.item(item["id"])["root_repo"], "common")
        self.assertEqual(self.payload()["stage"]["write_repositories"], ["common"])

    def test_reap_completes_the_handoff_itself_under_the_items_own_manifest(self):
        """The teardown pass completes a handoff on its own, under the item's manifest; the recovery pass is not
        the only path that does."""
        staged = self.use_staged_skill()
        item = self.item(skill=staged.name)
        self.scheduler.tick()
        token = self.ledger.claim(item["id"], worker_id="first")["token"]
        self.ledger.checkpoint(item["id"], token, {"handoff": {
            "facts": [], "hypotheses": [], "checks": [], "repositories": [], "next_actions": ["Build the server"]}})
        self.ledger.handoff_repository(item["id"], token, "farm-hive", skill=staged)
        self.assertEqual(self.scheduler.tick()["launched"], 0)  # the worker is asked to stop
        self.launcher.finished.append(Finished(item["id"], 0, "", True, "stopped", None, 101))
        self.scheduler._reap()  # alone: no recovery pass follows it here
        self.assertEqual(self.ledger.item(item["id"])["root_repo"], "farm-hive")

    def test_a_rooted_first_attempt_takes_no_investigator_summary(self):
        """A delivered chat's summary is the prior context of a neutral first attempt only (spec §9.6): a skill with
        an initial root starts on its own handoff, not the investigator's."""
        staged = self.use_staged_skill()
        chat = self.item(skill="chat")
        self.ledger.connection.execute("UPDATE work_items SET state='delivered', evidence=? WHERE id=?",
                                       (json.dumps({"summary": "Investigated the card."}), chat["id"]))
        self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill=staged.name, target=PIN)
        self.assertEqual(self.scheduler.tick()["launched"], 1)
        self.assertIsNone(self.payload()["prior_context"])

    def test_a_staged_skill_refuses_a_runtime_without_the_repository_sandbox(self):
        staged = self.use_staged_skill()
        item = self.item(skill=staged.name)
        self.scheduler.runtime_name = "claude"
        with self.assertRaisesRegex(RuntimeError, "repository-staged feature requires the Codex workspace-write"):
            self.scheduler.launch(item)
        self.assertEqual(self.trees.added, [])

    # Spec §5.7 "Re-attachment" (P4): a job with an initial root goes back to the issue branches its plan records.
    def use_feature_skill(self, **manifest):
        """Serve and enable Task 1's `opt_in_skill`, the plan's `feature` manifest (initial root Farm-Contract; writes
        Farm-Contract, common and farm-hive; opt-in and exclusive), beside the repository's own skills, with the
        dispatch AUTHORITY entry every dispatched skill needs. `manifest` overrides its keys."""
        feature = opt_in_skill(Path(self.tmp.name) / "fixture-skills", **manifest)
        self.scheduler.skills = {**SKILLS, feature.name: feature}
        self.scheduler.enabled_skills.add(feature.name)
        authority = patch.dict(dispatch.SKILL_AUTHORITY, {feature.name: "Fixture feature grants. "})
        authority.start()
        self.addCleanup(authority.stop)
        return feature

    PLAN = {"prs": {"Farm-Contract": [{"branch": "farmbot/farm-1", "role": "issue", "head": "b" * 40, "pr": None}],
                    "farm-hive": [{"branch": "farmbot/farm-1-followup", "role": "followup", "head": "c" * 40,
                                   "pr": None}]}}

    def planned_and_stopped(self, skill, plan):
        """An item of `skill` whose worker saved `plan` and was then stopped, as Linear's Stop does."""
        item = self.item(skill=skill)
        self.scheduler.tick()
        token = self.ledger.claim(item["id"], worker_id="first")["token"]
        self.ledger.checkpoint(item["id"], token, {"plan": plan})
        self.scheduler.stop(item["id"], "Linear stop")
        return item

    def test_a_successor_goes_back_to_the_issue_branches_its_predecessors_plan_records(self):
        feature = self.use_feature_skill()
        first = self.planned_and_stopped(feature.name, self.PLAN)
        self.assertEqual(self.trees.attached, [])  # nothing was recorded at the first launch
        successor = self.ledger.retry(first["id"], "continue the job")
        self.assertEqual(self.scheduler.tick()["launched"], 1)  # after the predecessor's cleanup
        self.assertEqual(self.launcher.spawned[-1][0], successor["id"])
        self.assertEqual(self.trees.attached, [("Farm-Contract", successor["id"], "farmbot/farm-1")])
        # A repository whose plan entry is no issue branch, and one with none, keep today's call and branch.
        self.assertEqual(sorted((repo, branch) for repo, item_id, branch in self.trees.added if item_id == successor["id"]),
                         [("Farm-Contract", "farmbot/farm-1"), ("common", "farmbot/farm-1"),
                          ("farm-hive", "farmbot/farm-1")])

    def test_a_cleaned_up_continuation_goes_back_to_the_issue_branches_of_its_own_plan(self):
        feature = self.use_feature_skill()
        item = self.item(skill=feature.name)
        self.scheduler.tick()
        token = self.ledger.claim(item["id"], worker_id="first")["token"]
        self.ledger.checkpoint(item["id"], token, {"plan": self.PLAN})
        self.launcher.finished.append(Finished(item["id"], 1, "", False, "exited"))
        self.scheduler.tick()
        self.assertEqual(self.ledger.item(item["id"])["state"], "failed")
        self.assertIn(("removed", item["id"], None), self.trees.added)  # its worktrees are gone
        self.ledger.retry(item["id"], "operator retry")
        self.assertEqual(self.scheduler.tick()["launched"], 1)
        self.assertEqual(self.trees.attached, [("Farm-Contract", item["id"], "farmbot/farm-1")])

    def test_a_plan_written_around_the_checkpoint_fails_the_launch_before_any_worktree(self):
        """P9's rules hold at launch too, because the ledger file is in a directory every worker can write. The
        failure is any launch failure: the job fails and its session says so."""
        feature = self.use_feature_skill()
        items = []
        for plan, error in (({"prs": {"common": [{"branch": "main", "role": "issue"}]}}, "farmbot/farm-1 or"),
                            ({"prs": {"common": [{"branch": "farmbot/farm-1", "role": "issue"},
                                                 {"branch": "farmbot/farm-1-2", "role": "issue"}]}},
                             "more than one issue entry")):
            item = self.item(issue_id=str(uuid4()), session=str(uuid4()), skill=feature.name)
            self.ledger.connection.execute("UPDATE work_items SET checkpoint=? WHERE id=?",
                                           (json.dumps({"plan": plan}), item["id"]))
            with self.subTest(error=error), self.assertRaisesRegex(LedgerError, error):
                self.scheduler.launch(self.ledger.item(item["id"]))
            items.append(item["id"])
        self.assertEqual([entry for entry in self.trees.added if entry[1] in items], [])
        self.assertEqual(self.scheduler.tick()["launched"], 0)
        self.assertEqual([self.ledger.item(item_id)["state"] for item_id in items], ["failed", "failed"])
        self.assertEqual({kind for _, kind, _ in self.api.activities}, {"error"})

    def test_a_fix_successor_keeps_todays_branches_whatever_its_plan_records(self):
        first = self.planned_and_stopped("fix", self.PLAN)
        successor = self.ledger.retry(first["id"], "continue the fix")
        self.assertEqual(self.scheduler.tick()["launched"], 1)
        self.assertEqual(self.launcher.spawned[-1][0], successor["id"])
        self.assertEqual(self.trees.attached, [])
        self.assertIn(("Farm-Contract", successor["id"], "farmbot/farm-1"), self.trees.added)

    READS = ["Farm-Contract", "Farm-Client", "farmgui"]  # the plan's feature manifest (P2)

    def test_a_skill_with_reads_gets_its_read_only_checkouts_at_every_launch_and_they_go_with_its_worktrees(self):
        feature = self.use_feature_skill(reads=self.READS)  # Farm-Contract is also written
        item = self.item(skill=feature.name)
        self.scheduler.tick()
        checkouts = {repo: str(self.trees.root / f"{item['id']}.reads" / f"{repo}@main") for repo in self.READS}
        self.assertEqual(self.payload()["reads"], checkouts)
        self.assertEqual(self.trees.reads, [(repo, item["id"], True) for repo in self.READS])
        # Kept apart from the item's own Farm-Contract worktree, and never a writable root.
        self.assertEqual(self.payload()["worktrees"]["Farm-Contract"], str(self.trees.root / item["id"] / "Farm-Contract"))
        self.assertEqual(self.payload()["stage"]["read_only_worktrees"], ["common", "farm-hive"])
        self.assertFalse(set(checkouts.values()) & set(self.launcher.spawn_writable))
        token = self.ledger.claim(item["id"], worker_id="first")["token"]
        self.ledger.await_input(item["id"], token, "配置好了请回复。", reason="waiting")
        self.launcher.finished.append(Finished(item["id"], 0, "", False, "exited"))
        self.scheduler.tick()
        self.ledger.resume(item["id"], "human replied")
        self.assertEqual(self.scheduler.tick()["launched"], 1)
        # Refreshed for the new attempt.
        self.assertEqual(self.trees.reads, [(repo, item["id"], True) for repo in self.READS] * 2)
        self.scheduler.stop(item["id"], "Linear stop")
        self.scheduler.tick()
        self.assertEqual(self.trees.reads_removed, [item["id"]])

    def test_a_publication_retry_reuses_the_read_only_checkouts_without_a_fetch(self):
        feature = self.use_feature_skill(reads=self.READS)
        self.assertEqual(self.scheduler._reads_for(feature, {"id": "item-9", "publication_retries": 1}),
                         {repo: self.trees.root / "item-9.reads" / f"{repo}@main" for repo in self.READS})
        self.assertEqual(self.trees.reads, [(repo, "item-9", False) for repo in self.READS])

    def test_a_skill_without_reads_gets_no_reads_key_and_no_checkout(self):
        self.item()
        self.scheduler.tick()
        self.assertNotIn("reads", self.payload())
        self.assertEqual(self.trees.reads, [])

    def test_one_exclusive_attempt_runs_at_a_time_and_fix_launches_beside_it(self):
        exclusive = self.use_feature_skill()  # exclusive, as P8 makes feature
        self.assertTrue(exclusive.exclusive)
        self.scheduler.max_concurrent = 2
        first = self.item(skill=exclusive.name)
        self.now += 1  # distinct created_at: queue() order is otherwise a coin flip on the item's random id
        second = self.item(issue_id=OTHER, session="s2", identifier="FARM-2", skill=exclusive.name)
        self.now += 1
        fix = self.item(issue_id=str(uuid4()), session="s3", identifier="FARM-3")
        self.assertEqual(self.scheduler.tick()["launched"], 2)
        self.assertEqual([spawned[0] for spawned in self.launcher.spawned], [first["id"], fix["id"]])
        self.assertEqual([row["id"] for row in self.ledger.queue()], [second["id"]])
        self.assertEqual(self.scheduler.tick()["launched"], 0)  # still waiting while the first attempt runs
        self.now += 1
        newer = self.item(issue_id=str(uuid4()), session="s4", identifier="FARM-4")
        self.scheduler.stop(first["id"], "Linear stop")
        self.assertEqual(self.scheduler.tick()["launched"], 1)
        self.assertEqual(self.launcher.spawned[-1][0], second["id"])  # its turn came before newer work
        self.assertEqual([row["id"] for row in self.ledger.queue()], [newer["id"]])  # which the cap now holds

    def test_ui_and_code_share_the_exclusive_lane_while_a_fix_can_run_beside_them(self):
        code = self.use_feature_skill()
        self.scheduler.enabled_skills.add("fgui")
        self.scheduler.max_concurrent = 2
        ui = self.item(skill="fgui")
        self.now += 1
        pending = self.item(issue_id=OTHER, session="s2", identifier="FARM-2", skill=code.name)
        self.now += 1
        fix = self.item(issue_id=str(uuid4()), session="s3", identifier="FARM-3")
        self.assertEqual(self.scheduler.tick()["launched"], 2)
        self.assertEqual([spawned[0] for spawned in self.launcher.spawned], [ui["id"], fix["id"]])
        self.assertEqual([row["id"] for row in self.ledger.queue()], [pending["id"]])
        self.assertEqual(self.scheduler.tick()["launched"], 0)
        self.scheduler.stop(ui["id"], "Linear stop")
        self.assertEqual(self.scheduler.tick()["launched"], 1)
        self.assertEqual(self.launcher.spawned[-1][0], pending["id"])

    def test_an_exclusive_job_between_two_stages_keeps_its_turn(self):
        exclusive = self.use_feature_skill()
        self.scheduler.max_concurrent = 2
        first = self.item(skill=exclusive.name)
        self.now += 1
        second = self.item(issue_id=OTHER, session="s2", identifier="FARM-2", skill=exclusive.name)
        self.assertEqual(self.scheduler.tick()["launched"], 1)
        token = self.ledger.claim(first["id"], worker_id="contract")["token"]
        self.ledger.checkpoint(first["id"], token, {"handoff": {
            "facts": [], "hypotheses": [], "checks": [], "repositories": [], "next_actions": ["Declare the config"]}})
        self.ledger.handoff_repository(first["id"], token, "common", skill=exclusive)
        self.assertEqual(self.scheduler.tick()["launched"], 0)  # the retiring attempt still holds the turn
        self.launcher.finished.append(Finished(first["id"], 0, "", True, "stopped", None, 101))
        self.assertEqual(self.scheduler.tick()["launched"], 1)
        self.assertEqual(self.launcher.spawned[-1][0], first["id"])
        self.assertEqual(self.payload()["stage"]["root_repository"], "common")
        self.assertEqual([row["id"] for row in self.ledger.queue()], [second["id"]])

    def test_attempts_of_a_skill_that_is_not_exclusive_run_side_by_side(self):
        feature = self.use_feature_skill(exclusive=False)
        self.scheduler.max_concurrent = 2
        self.item(skill=feature.name)
        self.now += 1
        self.item(issue_id=OTHER, session="s2", identifier="FARM-2", skill=feature.name)
        self.assertEqual(self.scheduler.tick()["launched"], 2)

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

    def test_ten_worker_limit_launches_ten_and_keeps_the_eleventh_queued(self):
        self.scheduler.max_concurrent = 10
        for n in range(11):
            self.now += 1
            self.item(issue_id=f"00000000-0000-4000-8000-{n + 1:012d}",
                      session=f"session-{n}", identifier=f"FARM-{n + 1}")
        self.assertEqual(self.scheduler.tick()["launched"], 10)
        self.assertEqual(len(self.scheduler.active), 10)
        self.assertEqual(len(self.launcher.spawned), 10)
        self.assertEqual(len(self.ledger.queue()), 1)
        self.assertEqual(self.scheduler.tick()["launched"], 0)

    def test_a_loaded_skill_this_host_does_not_enable_fails_with_a_session_error(self):
        self.scheduler.enabled_skills = {"chat"}
        item = self.item()
        self.assertEqual(self.scheduler.tick()["launched"], 0)
        self.assertEqual(self.launcher.spawned, [])
        self.assertEqual(self.ledger.item(item["id"])["state"], "failed")
        session, kind, body = self.api.activities[-1]
        self.assertEqual((session, kind), (SESSION, "error"))
        for words in ("本实例没有启用 fix", "本实例运行：chat", "「重试」"):
            self.assertIn(words, body)
        self.assertIsNone(self.ledger.active_item_for_issue(ISSUE))  # the issue is free for other work
        self.assertIn(("removed", item["id"], None), self.trees.added)  # retired like any other failure

    def test_a_refused_item_takes_no_worker_slot_and_blocks_no_other_work(self):
        self.scheduler.enabled_skills = {"chat"}
        refused = self.item(priority=1)
        chat = self.item(issue_id=OTHER, session="s2", identifier="FARM-2", skill="chat", priority=4)
        self.scheduler.tick()
        self.assertEqual(self.ledger.item(refused["id"])["state"], "failed")
        self.assertEqual([spawned[0] for spawned in self.launcher.spawned], [chat["id"]])

    def test_retry_after_the_skill_is_enabled_launches_the_refused_item(self):
        self.scheduler.enabled_skills = {"chat"}
        item = self.item()
        self.scheduler.tick()
        self.scheduler.enabled_skills = {"chat", "fix"}
        self.ledger.retry(item["id"], "operator enabled fix")
        self.scheduler.tick()
        self.assertEqual(self.launcher.spawned[-1][0], item["id"])

    def test_without_an_enabled_set_the_scheduler_leaves_out_opt_in_skills(self):
        """P1 in the scheduler's own default, which service.build overrides with the host's list: an opt-in skill
        is loaded, so its queued items fail with the session error above, but never launched by omission."""
        fixture = opt_in_skill(Path(self.tmp.name) / "fixture-skills")
        scheduler = Scheduler(self.ledger, self.launcher, {**SKILLS, fixture.name: fixture}, self.trees,
                              skill_root=ROOT / "skills", db_path=Path(self.tmp.name) / "ledger.sqlite3",
                              runtime_name="fake", host="h", api=self.api)
        self.assertEqual(scheduler.enabled_skills, {"chat", "fix"})
        item = self.item(skill=fixture.name)
        scheduler.tick()
        self.assertEqual((self.launcher.spawned, self.ledger.item(item["id"])["state"]), ([], "failed"))

    def test_an_item_whose_skill_the_checkout_lacks_still_waits(self):
        """Only a rollback leaves one behind; the operating contract says to settle those items first."""
        item = self.item(skill="qa")
        self.scheduler.tick()
        self.assertEqual(self.ledger.item(item["id"])["state"], "queued")
        self.assertEqual((self.launcher.spawned, self.api.activities), ([], []))

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
        self.ledger.await_resource(item["id"], token, "unity_slot", "batch", skill=SKILLS["fix"])
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

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFOs in a directory are POSIX")
    def test_a_fifo_left_for_the_batch_summary_is_no_evidence_and_cannot_stall_the_launch(self):
        # The summary is in the worker-writable state directory; opening a FIFO there stopped the scheduler loop.
        from test_launcher import unblocked
        item = self.granted_item(mode="batch")
        summary = Path(self.launcher.state_dir(item)) / "unity-batch.json"
        summary.unlink()
        os.mkfifo(summary)
        unblocked(self, self.scheduler.tick, summary)
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

    def test_a_codex_worker_home_distrusts_the_cwd_the_scheduler_chose(self):
        """A repository's own .codex/config.toml is never a source of worker tools (spec §7). codex exec trusts
        an undecided cwd and then loads that file, so the home must carry an explicit decision for the directory
        handed over as cwd: the private state directory for neutral fix and chat workers."""
        import tomllib
        runtime = RUNTIMES["codex"]._replace(command=RUNTIMES["fake"].command, seed_files={})
        launcher = Launcher(Path(self.tmp.name) / "real-runs", runtime, "h")
        self.scheduler.launcher = launcher
        self.scheduler.runtime_name = "codex"
        fix = self.item()
        chat = self.item(issue_id=OTHER, session="chat-session", skill="chat")
        for item, cwd in ((fix, launcher.state_dir(fix["id"])), (chat, launcher.state_dir(chat["id"]))):
            with self.subTest(skill=item["skill"]):
                handle = self.scheduler.launch(self.ledger.item(item["id"]))
                self.addCleanup(launcher.stop, item["id"])
                handle.process.wait(timeout=10)
                config = tomllib.loads((handle.run_dir / "home" / "config.toml").read_text(encoding="utf-8"))
                self.assertEqual(config["projects"], {str(cwd): {"trust_level": "untrusted"},
                                                      str(cwd.resolve()): {"trust_level": "untrusted"}})
        launcher.poll()

    KW_OPS = {"url": "https://gm.test/mcp", "token_env": "KW_OPS_TOKEN"}

    def launched(self, launcher, item):
        """Launch through a real Launcher and return (home config, launch payload, handle)."""
        import tomllib
        handle = self.scheduler.launch(self.ledger.item(item["id"]))
        self.addCleanup(launcher.stop, item["id"])
        handle.process.wait(timeout=10)
        launcher.poll()
        home = handle.run_dir / "home"
        config = (tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
                  if (home / "config.toml").exists() else None)
        payload = json.loads((handle.run_dir / "prompt.md").read_text(encoding="utf-8").split("\n\n", 1)[1])
        return config, payload, handle

    def codex_with_kw_ops(self):
        runtime = RUNTIMES["codex"]._replace(command=RUNTIMES["fake"].command, seed_files={})
        launcher = Launcher(Path(self.tmp.name) / "real-runs", runtime, "h")
        self.scheduler.launcher = launcher
        self.scheduler.runtime_name = "codex"
        self.scheduler.kw_ops_config = dict(self.KW_OPS)
        return launcher

    def test_a_codex_fix_worker_gets_every_kw_ops_tool_and_the_token_only_by_name(self):
        launcher = self.codex_with_kw_ops()
        with patch.dict(os.environ, {"KW_OPS_TOKEN": "dummy-token-value"}):
            config, payload, handle = self.launched(launcher, self.item())
        self.assertEqual(config["mcp_servers"]["kw_ops"],
                         {"url": "https://gm.test/mcp", "bearer_token_env_var": "KW_OPS_TOKEN"})
        self.assertEqual(payload["tools"], {"kw_ops": {"access": "full"}})
        self.assertEqual(config["shell_environment_policy"], {"exclude": ["KW_OPS_TOKEN"]})
        for path in handle.run_dir.rglob("*"):
            if path.is_file():
                self.assertNotIn("dummy-token-value", path.read_text(encoding="utf-8", errors="replace"), path)

    def test_a_codex_chat_worker_gets_only_the_kw_ops_query_tools(self):
        launcher = self.codex_with_kw_ops()
        chat = self.item(issue_id=OTHER, session="chat-session", skill="chat")
        with patch.dict(os.environ, {"KW_OPS_TOKEN": "dummy-token-value"}):
            config, payload, _ = self.launched(launcher, chat)
        self.assertEqual(config["mcp_servers"]["kw_ops"]["enabled_tools"], list(kw_ops.READ_TOOLS))
        self.assertEqual(payload["tools"], {"kw_ops": {"access": "read"}})

    def test_without_the_token_variable_no_server_is_injected_and_the_worker_is_told_why(self):
        launcher = self.codex_with_kw_ops()
        with patch.dict(os.environ, {"KW_OPS_TOKEN": ""}):
            config, payload, _ = self.launched(launcher, self.item())
        self.assertNotIn("kw_ops", config.get("mcp_servers", {}))
        self.assertEqual(payload["tools"]["kw_ops"]["status"], "unavailable")
        self.assertIn("KW_OPS_TOKEN", payload["tools"]["kw_ops"]["reason"])

    def test_a_worker_denied_kw_ops_does_not_inherit_its_token(self):
        # Braces are doubled: spawn formats every command part.
        script = ("import json,os,pathlib,sys;sys.stdin.read();"
                  "pathlib.Path(sys.argv[1]).write_text(json.dumps({{'token': os.environ.get('KW_OPS_TOKEN')}}))")
        runtime = RUNTIMES["claude"]._replace(command=[sys.executable, "-c", script, "{last_message}"])
        launcher = Launcher(Path(self.tmp.name) / "real-runs", runtime, "h")
        self.scheduler.launcher = launcher
        self.scheduler.runtime_name = "claude"
        self.scheduler.kw_ops_config = dict(self.KW_OPS)
        chat = self.item(skill="chat")
        with patch.dict(os.environ, {"KW_OPS_TOKEN": "dummy-token-value"}):
            _, payload, handle = self.launched(launcher, chat)
        self.assertEqual(json.loads((handle.run_dir / "last_message.txt").read_text(encoding="utf-8")),
                         {"token": None})
        self.assertIn("claude", payload["tools"]["kw_ops"]["reason"])
        servers = json.loads((handle.run_dir / "home" / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
        self.assertNotIn("kw_ops", servers)

    def test_a_codex_kw_ops_worker_keeps_the_token_variable_for_its_cli(self):
        # Braces are doubled: spawn formats every command part. Only the variable's presence is recorded.
        script = ("import json,os,pathlib,sys;sys.stdin.read();"
                  "pathlib.Path(sys.argv[1]).write_text(json.dumps({{'present': bool(os.environ.get('KW_OPS_TOKEN'))}}))")
        runtime = RUNTIMES["codex"]._replace(command=[sys.executable, "-c", script, "{last_message}"], seed_files={})
        launcher = Launcher(Path(self.tmp.name) / "real-runs", runtime, "h")
        self.scheduler.launcher = launcher
        self.scheduler.runtime_name = "codex"
        self.scheduler.kw_ops_config = dict(self.KW_OPS)
        with patch.dict(os.environ, {"KW_OPS_TOKEN": "dummy-token-value"}):
            _, payload, handle = self.launched(launcher, self.item())
        self.assertEqual(payload["tools"], {"kw_ops": {"access": "full"}})
        self.assertEqual(json.loads((handle.run_dir / "last_message.txt").read_text(encoding="utf-8")),
                         {"present": True})

    def test_an_unconfigured_host_injects_nothing_and_says_so(self):
        launcher = self.codex_with_kw_ops()
        self.scheduler.kw_ops_config = {}
        with patch.dict(os.environ, {"KW_OPS_TOKEN": "dummy-token-value"}):
            config, payload, _ = self.launched(launcher, self.item())
        self.assertNotIn("kw_ops", config.get("mcp_servers", {}))
        self.assertIn("not configured", payload["tools"]["kw_ops"]["reason"])

    def test_only_a_skill_that_reads_the_design_doc_is_told_the_lark_cli_profile(self):
        """P5: tools.lark_cli is exactly the host's block, the profile and the FarmBot-only lark-cli home; fix and chat
        get none."""
        staged = self.use_staged_skill()
        self.scheduler.max_concurrent = 3
        self.scheduler.lark_cli = {"profile": "farmbot", "home": "/srv/farmbot/lark-cli"}
        fix = self.item()
        feature = self.item(issue_id=OTHER, session="session-2", skill=staged.name)
        chat = self.item(issue_id="10000000-0000-4000-8000-000000000003", session="session-3", skill="chat")
        self.assertEqual(self.scheduler.tick()["launched"], 3)
        payloads = {launch[0]: json.loads(launch[1].split("\n\n", 1)[1]) for launch in self.launcher.spawned}
        self.assertEqual(payloads[feature["id"]]["tools"],
                         {"lark_cli": {"profile": "farmbot", "home": "/srv/farmbot/lark-cli"}})
        self.assertNotIn("lark_cli", payloads[fix["id"]]["tools"])
        self.assertNotIn("lark_cli", payloads[chat["id"]]["tools"])

    def test_a_host_without_lark_cli_tells_a_feature_worker_why(self):
        staged = self.use_staged_skill()
        self.item(skill=staged.name)
        self.scheduler.tick()
        self.assertEqual(self.payload()["tools"],
                         {"lark_cli": {"status": "unavailable", "reason": "lark_cli is not configured on this host"}})

    def test_no_worker_inherits_lark_cli_credentials_whatever_its_skill(self):
        """Profile Code/UI workers learn only their selected profile; no skill inherits ambient credentials."""
        # Braces are doubled: spawn formats every command part. Only the names are recorded.
        script = ("import json,os,pathlib,sys;sys.stdin.read();"
                  "pathlib.Path(sys.argv[1]).write_text(json.dumps(sorted(k for k in os.environ "
                  "if k.upper().startswith('LARKSUITE_CLI_'))))")
        runtime = RUNTIMES["codex"]._replace(command=[sys.executable, "-c", script, "{last_message}"], seed_files={})
        launcher = Launcher(Path(self.tmp.name) / "real-runs", runtime, "h")
        self.scheduler.launcher = launcher
        self.scheduler.runtime_name = "codex"
        staged = self.use_staged_skill()
        self.scheduler.enabled_skills.add("fgui")
        self.scheduler.lark_cli = {"profile": "farmbot"}
        items = [self.item(), self.item(issue_id=OTHER, session="session-2", skill="chat"),
                 self.item(issue_id="10000000-0000-4000-8000-000000000003", session="session-3", skill=staged.name),
                 self.item(issue_id="10000000-0000-4000-8000-000000000004", session="session-4", skill="fgui")]
        credentials = {name: "dummy-lark-value" for name in (
            "LARKSUITE_CLI_APP_ID", "LARKSUITE_CLI_APP_SECRET", "LARKSUITE_CLI_PROXY_KEY",
            "LARKSUITE_CLI_USER_ACCESS_TOKEN", "LARKSUITE_CLI_TENANT_ACCESS_TOKEN")}
        with patch.dict(os.environ, {**credentials, "LARKSUITE_CLI_REMOTE_META": "off"}):
            launched = {item["skill"]: self.launched(launcher, item) for item in items}
        for skill, (_, payload, handle) in launched.items():
            with self.subTest(skill=skill):
                self.assertEqual(json.loads((handle.run_dir / "last_message.txt").read_text(encoding="utf-8")),
                                 ["LARKSUITE_CLI_REMOTE_META"])
                self.assertEqual(payload["tools"].get("lark_cli"),
                                 {"profile": "farmbot"} if skill in (staged.name,"fgui") else None)


    def test_configured_environment_credentials_are_granted_only_to_code_and_ui_document_readers(self):
        from test_lark_cli import BLOCK, SECRET
        script = ("import json,os,pathlib,sys;sys.stdin.read();"
                  "pathlib.Path(sys.argv[1]).write_text(json.dumps({{k:os.environ.get(k) for k in "
                  "['FEATURE_FEISHU_SECRET','LARKSUITE_CLI_APP_ID','LARKSUITE_CLI_APP_SECRET',"
                  "'LARKSUITE_CLI_STRICT_MODE']}}))")
        runtime = RUNTIMES["codex"]._replace(command=[sys.executable, "-c", script, "{last_message}"], seed_files={})
        launcher = Launcher(Path(self.tmp.name) / "real-runs", runtime, "h", lark_cli=BLOCK)
        self.scheduler.launcher = launcher
        self.scheduler.runtime_name = "codex"
        self.scheduler.lark_cli = BLOCK
        staged = self.use_staged_skill()
        self.scheduler.enabled_skills.add("fgui")
        items = [self.item(), self.item(issue_id=OTHER, session="s2", skill="chat"),
                 self.item(issue_id="10000000-0000-4000-8000-000000000003", session="s3", skill=staged.name),
                 self.item(issue_id="10000000-0000-4000-8000-000000000004", session="s4", skill="fgui")]
        with patch.dict(os.environ, {BLOCK["secret_env"]: SECRET, "LARKSUITE_CLI_APP_SECRET": "ambient-secret"}):
            launched = {item["skill"]: self.launched(launcher, item) for item in items}
        for skill, (config, payload, handle) in launched.items():
            with self.subTest(skill=skill):
                reader = skill in (staged.name, "fgui")
                values = json.loads((handle.run_dir / "last_message.txt").read_text(encoding="utf-8"))
                self.assertIsNone(values[BLOCK["secret_env"]])
                self.assertEqual(values["LARKSUITE_CLI_APP_SECRET"], SECRET if reader else None)
                self.assertEqual(values["LARKSUITE_CLI_APP_ID"], BLOCK["app_id"] if reader else None)
                self.assertEqual(values["LARKSUITE_CLI_STRICT_MODE"], "bot" if reader else None)
                self.assertEqual(payload["tools"].get("lark_cli"),
                                 {"authentication": "environment"} if reader else None)
                for value in (SECRET, BLOCK["app_id"]):
                    self.assertNotIn(value, json.dumps(payload))
                    self.assertNotIn(value, json.dumps(config))

    def test_missing_environment_source_is_an_unavailable_feature_tool(self):
        from test_lark_cli import BLOCK
        staged = self.use_staged_skill()
        self.scheduler.lark_cli = BLOCK
        self.scheduler.runtime_name = "codex"
        runtime = RUNTIMES["codex"]._replace(command=RUNTIMES["fake"].command, seed_files={})
        launcher = Launcher(Path(self.tmp.name) / "real-runs", runtime, "h", lark_cli=BLOCK)
        self.scheduler.launcher = launcher
        item = self.item(skill=staged.name)
        with patch.dict(os.environ, {BLOCK["secret_env"]: "", "FAKE_CLI_MODE": "echo"}):
            _, payload, _ = self.launched(launcher, item)
        self.assertEqual(payload["tools"]["lark_cli"], {
            "status": "unavailable", "reason": "lark_cli secret is not set in the controller environment"})

    def test_an_item_with_no_reservation_is_launched_with_no_tools_and_no_resource_block(self):
        """Enforcement is tool injection (spec §7): a fix worker that holds no slot must not reach the Unity
        MCP, and for a reservation-bound server such as that one the manifest's own `resources`/`mcp`
        fields must never be what decides that."""
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

    def test_a_stop_notice_needs_states(self):
        """Only a stop limited to states knows it was this call that cancelled the item (spec §9.8)."""
        item = self.item()
        with self.assertRaisesRegex(ValueError, "needs states"):
            self.scheduler.stop(item["id"], "Linear stop", notice="已取消。")
        self.assertEqual((self.ledger.item(item["id"])["state"], self.launcher.stopped), ("queued", []))

    def test_a_stop_notice_is_chosen_for_the_job_the_cancel_ends(self):
        """A stop that finds a conversation has just handed over to a fix cancels the fix instead. A caller that
        listed the conversation passes its notice as a function of the job the cancel ended, so the fix's session
        gets a write job's text, which says the branches are kept, not the conversation's (withdrawn-work design P7)."""
        app = str(uuid4())
        chat = self.item(skill="chat")
        self.ledger.push_inbox(chat["id"], "请修复")
        token = self.ledger.claim(chat["id"], worker_id="w")["token"]
        message = self.ledger.issue_context(chat["id"])["session_messages"][-1]["id"]
        fix = self.ledger.request_repair(chat["id"], token, message, app, "Confirmed repair.", delegate_id=app)
        cancelled = self.scheduler.stop(chat["id"], "Linear issue closed", states=("queued",),
                                        notice=lambda job: withdrawal.notice(job["skill"], "closed", "FarmBot"))
        self.assertEqual((cancelled["id"], self.ledger.item(fix["id"])["state"]), (fix["id"], "cancelled"))
        self.assertEqual(self.api.activities,
                         [(fix["session_id"], "response", withdrawal.CLOSED.format(bot="FarmBot"))])

    def test_a_stop_notice_function_may_decline_and_the_heartbeat_still_goes(self):
        """Silent-delegation design A1: a Stop pressed in the job's own thread is answered there by the receiver, so
        its notice function returns None. Nothing is posted for it, not a response with no body, and the job's pending
        heartbeat still goes with the cancel: the Stop's reply is the thread's last word."""
        item = self.item()
        SessionProgress(self.ledger, None)
        self.ledger.connection.execute(
            "INSERT INTO session_progress(item_id,due_at,activity_id,content,status_key) VALUES(?,?,?,?,?)",
            (item["id"], self.now, "activity-1", json.dumps({"type": "thought", "body": "工作仍在排队。"}),
             f"{item['id']}:queued:0"))
        cancelled = self.scheduler.stop(item["id"], "Linear stop", states=("queued",), notice=lambda job: None)
        self.assertEqual((cancelled["id"], cancelled["state"]), (item["id"], "cancelled"))
        self.assertEqual((self.api.activities, self.api.comments), ([], []))
        self.assertIsNone(self.ledger.connection.execute("SELECT 1 FROM session_progress WHERE item_id=?",
                                                         (item["id"],)).fetchone())
        self.assertEqual(self.launcher.stopped, [item["id"]])

    def closures(self):
        return [dict(row) for row in self.ledger.connection.execute(
            "SELECT * FROM session_closures ORDER BY created_at,rowid")]

    def test_a_stop_notice_linear_refuses_is_owed(self):
        """Silent-delegation design A4, P10: the cancel is done whatever becomes of its notice, and a notice Linear
        refuses is owed to the job's thread, for the progress loop to post again. One Linear took is not."""
        self.scheduler.api = FakeAPI(fail=True)
        item = self.item()
        cancelled = self.scheduler.stop(item["id"], "Linear delegation removed", states=("queued",),
                                        notice="这项工作已取消。")
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertEqual([(row["session_id"], row["issue_id"], row["item_id"], row["item_state"], row["kind"],
                           row["body"], row["attempts"], row["due_at"], row["last_error"]) for row in self.closures()],
                         [(SESSION, ISSUE, item["id"], "cancelled", "response", "这项工作已取消。", 1, self.now + 60,
                           "RuntimeError")])
        self.scheduler.api = self.api
        other = self.item(issue_id=OTHER, session="session-2")
        self.scheduler.stop(other["id"], "Linear delegation removed", states=("queued",), notice="这项工作已取消。")
        self.assertEqual(self.api.activities, [("session-2", "response", "这项工作已取消。")])
        self.assertEqual([row["session_id"] for row in self.closures()], [SESSION])

    def test_a_refused_notice_is_owed_through_the_control_connection(self):
        """A stop runs on the receiver's and the lifecycle's threads, which must not use the scheduler's own
        connection: the owed notice is recorded on a control connection, closed again, as the cancel is."""
        opened = []

        def control():
            opened.append(Ledger(Path(self.tmp.name) / "ledger.sqlite3", clock=lambda: self.now, lease_seconds=60))
            return opened[-1]
        self.scheduler.control_ledger_factory = control
        self.scheduler.api = FakeAPI(fail=True)
        item = self.item()
        self.scheduler.ledger = None  # any use of the scheduler's own connection would fail here
        self.scheduler.stop(item["id"], "Linear delegation removed", states=("queued",), notice="这项工作已取消。")
        self.assertEqual([(row["session_id"], row["body"]) for row in self.closures()], [(SESSION, "这项工作已取消。")])
        self.assertEqual(len(opened), 2)
        for ledger in opened:
            with self.assertRaises(sqlite3.ProgrammingError):
                ledger.connection.execute("SELECT 1")  # closed

    def test_a_refused_error_is_owed_and_a_refused_thought_or_card_comment_is_not(self):
        """A4: what closes a thread is a response or an error. A thought closes nothing, and an operator's `local-`
        job has no Linear thread that could be left waiting."""
        self.scheduler.api = FakeAPI(fail=True)
        requeued = self.item()
        self.scheduler.tick()
        self.ledger.claim(requeued["id"], worker_id="w")
        self.now += FIX_LEASE + 1
        self.launcher.finished.append(Finished(requeued["id"], 1, "", False, "exited"))
        self.scheduler.tick()  # "requeued": a thought
        self.assertEqual((self.ledger.item(requeued["id"])["state"], self.closures()), ("queued", []))
        self.scheduler.stop(requeued["id"], "make room")
        failed = self.item(issue_id=OTHER, session="session-2")
        self.trees.fail_on = ("Farm-Client", failed["id"])
        self.scheduler.tick()  # the launch failure: an error
        self.assertEqual(self.ledger.item(failed["id"])["state"], "failed")
        [owed] = self.closures()
        self.assertEqual((owed["session_id"], owed["item_id"], owed["item_state"], owed["kind"]),
                         ("session-2", failed["id"], "failed", "error"))
        self.assertIn("无法启动工作进程", owed["body"])
        third = str(uuid4())
        local = self.item(issue_id=third, session=f"local-{third}")
        self.trees.fail_on = ("Farm-Client", local["id"])
        self.scheduler.tick()
        self.assertEqual(self.ledger.item(local["id"])["state"], "failed")
        self.assertEqual([row["session_id"] for row in self.closures()], ["session-2"])

    def test_notify_says_whether_linear_took_the_activity(self):
        """A4: `_notify` returns whether Linear took the activity, or for an operator's `local-` job the card comment;
        with no API configured nothing is posted, and nothing was taken."""
        item = self.item()
        self.scheduler.api = None
        self.assertIs(self.scheduler._notify(item["id"], "thought", "工作仍在排队。"), False)
        for fail, taken in ((False, True), (True, False)):
            with self.subTest(fail=fail):
                self.scheduler.api = FakeAPI(fail=fail)
                self.assertIs(self.scheduler._notify(item["id"], "thought", "工作仍在排队。"), taken)
                self.assertEqual(self.scheduler.api.activities, [(SESSION, "thought", "工作仍在排队。")] if taken else [])
        third = str(uuid4())
        local = self.item(issue_id=third, session=f"local-{third}")
        for fail, taken in ((False, True), (True, False)):
            with self.subTest(local=True, fail=fail):
                self.scheduler.api = FakeAPI(fail=fail)
                self.assertIs(self.scheduler._notify(local["id"], "response", "工作已停止。"), taken)
                self.assertEqual(self.scheduler.api.comments, [(third, "工作已停止。")] if taken else [])
        self.assertEqual(self.closures(), [])  # a thought closes nothing, and a `local-` job has no thread

    def test_a_withdrawn_worker_is_stopped_when_its_grace_ends(self):
        """Withdrawn-work design P2: the controller cancels and kills a flagged worker that has not run `withdraw` by
        its deadline, with the one response its reason has; an issue out of reach gets none. A claim a worker
        withdrew itself, or that ended meanwhile, is left alone."""
        for reason, said in (("superseded", [("response", "这张卡有了新的委派会话，这里的工作已转到那里继续。")]),
                             ("unreachable", [])):
            with self.subTest(reason=reason):
                session = str(uuid4())
                item = self.item(issue_id=str(uuid4()), session=session)
                token = self.ledger.claim(item["id"], worker_id="w")["token"]
                self.ledger.flag_withdrawal(item["id"], reason, self.now + 30)
                self.scheduler.tick()
                self.assertEqual(self.ledger.item(item["id"])["state"], "running")
                self.now += 30
                self.scheduler.tick()
                self.assertEqual(self.ledger.item(item["id"])["state"], "cancelled")
                with self.assertRaises(LedgerError):
                    self.ledger.renew(item["id"], token)
                self.assertIn(item["id"], self.launcher.stopped)
                self.assertEqual([(kind, body) for sid, kind, body in self.api.activities if sid == session], said)
                audited = self.ledger.connection.execute("SELECT reason FROM audit WHERE item_id=? AND kind='cancelled'",
                                                         (item["id"],)).fetchone()
                self.assertEqual(audited["reason"], f"withdrawal grace expired ({reason})")

    def test_a_withdrawal_cleared_after_the_expiry_listing_keeps_its_worker(self):
        """A read that finds the delegation back clears the flag after the tick listed the expired withdrawals but
        before it cancelled this one, as the lifecycle's own connection can: the cancel rechecks the flag and its
        deadline in its transaction, and the worker keeps its claim."""
        issue_id = str(uuid4())
        item = self.item(issue_id=issue_id, session=str(uuid4()))
        self.ledger.claim(item["id"], worker_id="w")
        self.ledger.flag_withdrawal(item["id"], "undelegated", self.now + 30)
        self.now += 30
        listing = self.ledger.expired_withdrawals

        def cleared_meanwhile():
            rows = listing()
            self.ledger.clear_undelegated(issue_id)
            return rows
        with patch.object(self.ledger, "expired_withdrawals", cleared_meanwhile):
            self.scheduler.tick()
        current = self.ledger.item(item["id"])
        self.assertEqual((current["state"], current["withdraw_deadline"]), ("running", None))
        self.assertNotIn(item["id"], self.launcher.stopped)
        self.assertEqual(self.api.activities, [])

    def cancel_with_cli(self, item_id):
        # `cancel` posts the operator's note (withdrawn-work design I2): to a stub, never to the Linear app whatever
        # private config this checkout holds.
        stub = Path(self.tmp.name) / "stub"
        stub.mkdir(exist_ok=True)
        result = subprocess.run(
            [sys.executable, "-B", "-W", "error", "-m", "agent", "--db", str(self.scheduler.db_path),
             "cancel", "--item", item_id, "--reason", "operator cancellation"],
            env={**os.environ, "FARMBOT_LINEAR_STUB_DIR": str(stub)},
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

    def test_a_disabled_skill_is_refused_without_waiting_for_a_free_worker_slot(self):
        """The refusal is a state change, not a launch: it must not queue behind the concurrency cap, or the item
        would hold its issue's one active slot until a worker frees up."""
        from test_ledger import OTHER
        self.scheduler.enabled_skills = {"chat"}
        self.item(issue_id=OTHER, session="s2", identifier="FARM-2", skill="chat")
        self.scheduler.tick()  # max_concurrent is 1: the chat now holds the only worker slot
        self.assertEqual(len(self.scheduler.active), 1)
        refused = self.item()
        self.scheduler.tick()
        self.assertEqual(self.ledger.item(refused["id"])["state"], "failed")

    def test_worktree_failure_fails_only_that_item_and_the_queue_keeps_moving(self):
        bad = self.item()
        good = self.item(issue_id=OTHER, session="s2", identifier="FARM-2", priority=4)
        self.trees.fail_on = ("farm-hive", bad["id"])
        self.scheduler.tick()
        self.assertEqual(self.ledger.item(bad["id"])["state"], "failed")
        self.assertEqual([s[0] for s in self.launcher.spawned], [good["id"]])
        self.assertIn(("removed", bad["id"], None), self.trees.added)
