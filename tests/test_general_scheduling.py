"""General-job scheduling and the native UI export resource, offline."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from agent import fgui_publisher as publisher
from agent.ledger import LedgerError
from agent.scheduler import Scheduler
from agent.workflow import JobWorktrees, await_tasks, delegate_task, retry_task, wake_parent
from agent.worktrees import WorktreeError
import test_ledger as ledger_fixtures
import test_scheduler as scheduler_fixtures
import test_worktrees as git_fixtures
import test_fgui_delivery as delivery_fixtures


SKILLS = scheduler_fixtures.SKILLS


class GeneralSchedulingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = scheduler_fixtures.SchedulerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.ledger, self.scheduler = self.fixture.ledger, self.fixture.scheduler
        self.scheduler.enabled_skills = {"chat", "work", "feature", "fgui"}
        self.scheduler.max_concurrent = 6
        self.parent = self.fixture.item(skill="work")["id"]
        self.ledger.connection.execute("UPDATE work_items SET target_json=NULL,root_repo='farm-hive' WHERE id=?",
                                       (self.parent,))
        self.scheduler.tick()
        self.token = self.ledger.claim(self.parent, worker_id="server")["token"]
        self.ledger.checkpoint(self.parent, self.token, {"plan": {"stages": {"A": "done", "D": "pending"}}})

    def task(self, key="ui", capability="fgui", *, parent=None, token=None):
        return delegate_task(self.ledger, parent or self.parent, token or self.token,
            {"key": key, "capability": capability, **({"scope": {"assets": ["Assets/nursery.prefab"], "validation": "source"}} if capability == "scene" else {}), "instructions": "Use the settled shared spec."})

    def retained_client_branch(self):
        fixture = git_fixtures.WorktreeTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.trees.remotes.update({repo: str(fixture.origin) for repo in SKILLS["work"].writes})
        self.scheduler.worktrees = JobWorktrees(fixture.trees, self.ledger)
        issue = self.ledger.issue(self.ledger.item(self.parent)["issue_id"])
        branch = self.scheduler._branch(issue)
        path = fixture.trees.add("Farm-Client", "legacy-job", branch)
        (path / "retained.txt").write_text("previous job's unmerged candidate\n", encoding="utf-8")
        git_fixtures.git("add", ".", cwd=path)
        git_fixtures.git("commit", "-qm", "retain legacy candidate", cwd=path)
        return fixture, issue, branch, path

    def test_first_general_client_entry_pins_fresh_branch_without_adopting_legacy_candidate(self):
        fixture, issue, preferred, old = self.retained_client_branch()
        retained = fixture.trees.head(old)
        self.ledger.connection.execute("UPDATE work_items SET state='queued',token=NULL,worker_pid=NULL,"
                                       "next_root_repo=NULL,root_repo='Farm-Client' "
                                       "WHERE id=?", (self.parent,))
        item = self.ledger.item(self.parent)
        paths = self.scheduler._worktrees_for(SKILLS["work"], item, issue)
        target = self.ledger.item(self.parent)["target"]
        self.assertEqual(target["issue_branch"], preferred + "-" + self.parent)
        self.assertEqual(target["commit_sha"], fixture.trees.head(fixture.origin))
        self.assertEqual(fixture.trees.head(paths["Farm-Client"]), target["commit_sha"])
        self.assertFalse((paths["Farm-Client"] / "retained.txt").exists())
        self.assertEqual(fixture.trees.head(old), retained)
        with patch.object(fixture.trees, "stage_start", side_effect=AssertionError("must keep saved pin")):
            resumed = self.scheduler._worktrees_for(SKILLS["work"], self.ledger.item(self.parent), issue)
        self.assertEqual(resumed["Farm-Client"], paths["Farm-Client"])
        self.assertEqual(self.ledger.item(self.parent)["target"], target)

    def test_first_presentation_entry_uses_parent_suffix_and_shares_immutable_client_pin(self):
        fixture, issue, preferred, old = self.retained_client_branch()
        retained = fixture.trees.head(old)
        first = self.task("scene-first", "scene")
        paths = self.scheduler._worktrees_for(SKILLS["scene"], first, issue)
        parent = self.ledger.item(self.parent)
        child = self.ledger.item(first["id"])
        self.assertEqual(parent["target"]["issue_branch"], preferred + "-" + self.parent)
        self.assertEqual(child["target"], parent["target"])
        self.assertEqual(child["task_client_base"], fixture.trees.head(fixture.origin))
        self.assertEqual(paths["Farm-Client"].parent.name, self.parent)
        second = self.task("scene-second", "scene")
        with patch.object(fixture.trees, "stage_start", side_effect=AssertionError("must share parent pin")):
            with self.assertRaisesRegex(LedgerError, "another presentation task retains"):
                self.scheduler._worktrees_for(SKILLS["scene"], second, issue)
        self.assertEqual(self.ledger.item(second["id"])["target"], parent["target"])
        self.assertIsNone(self.ledger.item(second["id"])["task_client_base"])
        self.assertEqual(fixture.trees.head(old), retained)

    def test_recorded_client_branch_without_baseline_still_refuses_fresh_selection(self):
        fixture, issue, preferred, old = self.retained_client_branch()
        first = self.task("scene-first", "scene")
        with patch.object(self.scheduler, "_recorded_branches", return_value={"Farm-Client": preferred}), \
                patch.object(fixture.trees, "stage_start", side_effect=AssertionError("saved plan needs recovery")):
            with self.assertRaisesRegex(WorktreeError, "existing issue branch"):
                self.scheduler._worktrees_for(SKILLS["scene"], first, issue)
        self.assertIsNone(self.ledger.item(self.parent)["target"])
        self.assertIsNone(self.ledger.item(first["id"])["target"])
        self.assertTrue((old / "retained.txt").exists())

    def test_concurrent_ui_authoring_does_not_hold_the_legacy_exclusive_lane(self):
        first = self.task()
        other = self.fixture.item(issue_id=ledger_fixtures.OTHER, session="other-job", identifier="FARM-2", skill="work")
        self.ledger.connection.execute("UPDATE work_items SET target_json=NULL,root_repo='farm-hive' WHERE id=?", (other["id"],))
        token = self.ledger.claim(other["id"], worker_id="other-server")["token"]
        self.ledger.checkpoint(other["id"], token, {"plan": {"stages": {"A": "done", "D": "pending"}}})
        second = self.task(parent=other["id"], token=token)
        self.assertEqual(self.scheduler.tick()["launched"], 2)
        self.assertTrue({self.parent, first["id"], second["id"]}.issubset(self.scheduler.active))
        self.assertFalse(self.scheduler._exclusive_running())

        legacy = self.fixture.item(issue_id=str(uuid4()), session="legacy", identifier="FARM-3", skill="feature")
        self.assertEqual(self.scheduler.tick()["launched"], 1)
        self.assertIn(legacy["id"], self.scheduler.active)
        self.assertTrue(self.scheduler._exclusive_running())
        pending = self.fixture.item(issue_id=str(uuid4()), session="legacy-ui", identifier="FARM-4", skill="fgui")
        self.assertEqual(self.scheduler.tick()["launched"], 0)
        self.assertEqual(self.ledger.item(pending["id"])["state"], "queued")

    def test_tick_wakes_only_parked_general_coordinators_without_a_status_scan(self):
        task = self.task()
        child_token = self.ledger.claim(task["id"], worker_id="ui")["token"]
        self.ledger.await_input(task["id"], child_token, "Actual nursery art needed")
        await_tasks(self.ledger, self.parent, self.token)
        historical = self.fixture.item(issue_id=str(uuid4()), session="old-job", identifier="FARM-3", skill="work")
        self.ledger.cancel(historical["id"], "old completed session")
        human_wait = self.fixture.item(issue_id=str(uuid4()), session="human", identifier="FARM-4", skill="work")
        human_token = self.ledger.claim(human_wait["id"], worker_id="coordinator")["token"]
        self.ledger.await_input(human_wait["id"], human_token, "Which server rule?")
        with patch.object(self.scheduler, "_expire_withdrawals"), patch.object(self.scheduler, "_stop_cancelled"), \
                patch.object(self.scheduler, "_reap", return_value=0), patch.object(self.scheduler, "_recover", return_value=0), \
                patch.object(self.scheduler, "_sweep_worktrees"), patch.object(self.ledger, "queue", return_value=[]), \
                patch.object(self.ledger, "status", side_effect=AssertionError("wake scan must not decode history")), \
                patch("agent.scheduler.wake_parent", return_value=False) as wake:
            self.scheduler.tick()
        wake.assert_called_once_with(self.ledger, self.parent)

    def test_tick_wakes_a_coordinator_waiting_on_its_own_answer_for_a_later_child_question(self):
        task = self.task()
        self.ledger.await_input(self.parent, self.token, "Server Go cache is missing", reason="waiting")
        child_token = self.ledger.claim(task["id"], worker_id="ui")["token"]
        self.ledger.await_input(task["id"], child_token, "Provide the badge asset")
        with patch.object(self.scheduler, "_expire_withdrawals"), patch.object(self.scheduler, "_stop_cancelled"), \
                patch.object(self.scheduler, "_reap", return_value=0), patch.object(self.scheduler, "_recover", return_value=0), \
                patch.object(self.scheduler, "_sweep_worktrees"), patch.object(self.ledger, "queue", return_value=[]), \
                patch.object(self.ledger, "status", side_effect=AssertionError("wake scan must not decode history")), \
                patch("agent.scheduler.wake_parent", wraps=wake_parent) as wake:
            self.scheduler.tick()
        wake.assert_called_once_with(self.ledger, self.parent)
        self.assertEqual(self.ledger.item(self.parent)["state"], "queued")
        self.assertEqual(self.ledger.item(self.parent)["checkpoint"]["pending_question"], "Server Go cache is missing")
        self.assertEqual(self.ledger.item(task["id"])["state"], "awaiting_input")

    def test_crashed_scene_retains_dirty_candidate_and_retry_reopens_its_original_baseline(self):
        fixture = git_fixtures.WorktreeTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.scheduler.worktrees = JobWorktrees(fixture.trees, self.ledger)
        first, second = self.task("scene-one", "scene"), self.task("scene-two", "scene")
        paths = self.scheduler._worktrees_for(SKILLS["scene"], first, self.ledger.issue(first["issue_id"]))
        path = paths["Farm-Client"]
        baseline = self.ledger.item(first["id"])["task_client_base"]
        candidate = path / "nursery.prefab"
        candidate.write_text("uncommitted scene candidate\n", encoding="utf-8")
        self.ledger.claim(first["id"], worker_id="scene")
        self.ledger.fail(first["id"], "worker crashed before committing scene output")
        self.scheduler._retire(first["id"], "failed")
        self.assertTrue(self.ledger.cleanup_record(first["id"])["done"])
        self.assertTrue(candidate.exists())
        self.assertEqual(fixture.trees.head(path), baseline)
        self.assertIsNone(self.scheduler.launch(second))
        self.assertEqual(self.ledger.item(second["id"])["state"], "queued")

        self.ledger.push_inbox(self.parent, "Retry and revalidate the retained scene candidate.", author=ledger_fixtures.DESIGNER)
        message = self.ledger.issue_context(self.parent)["session_messages"][-1]["id"]
        self.ledger.pop_inbox(self.parent, self.token)
        retried = retry_task(self.ledger, self.parent, self.token, "scene-one", message)
        paths = self.scheduler._worktrees_for(SKILLS["scene"], retried, self.ledger.issue(first["issue_id"]))
        self.assertEqual(paths["Farm-Client"], path)
        self.assertEqual(self.ledger.item(first["id"])["task_client_base"], baseline)
        self.assertEqual(candidate.read_text(encoding="utf-8"), "uncommitted scene candidate\n")
        self.assertIn("?? nursery.prefab", fixture.trees.git_in(path, "status", "--porcelain"))
        self.assertIsNone(self.scheduler.launch(second))

    def delivered_scene(self):
        fixture = git_fixtures.WorktreeTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.scheduler.worktrees = JobWorktrees(fixture.trees, self.ledger)
        task = self.task("scene", "scene")
        self.scheduler._worktrees_for(SKILLS["scene"], task, self.ledger.issue(task["issue_id"]))
        path = fixture.trees.worktrees_root.resolve() / self.parent / "Farm-Client"
        (path / "nursery.prefab").write_text("Verified scene output\n", encoding="utf-8")
        git_fixtures.git("add", "nursery.prefab", cwd=path)
        git_fixtures.git("commit", "-qm", "Reviewed scene candidate", cwd=path)
        token = self.ledger.claim(task["id"], worker_id="scene")["token"]
        row = self.ledger._row(task["id"])
        proof = {"delivery_id": uuid4().hex, "generation": row["generation"],
                 "fingerprint": row["claimed_fingerprint"], "commit": fixture.trees.head(path),
                 "baseline": row["task_client_base"], "branch": "farmbot/farm-1",
                 "prs": ["https://github.com/example/Farm-Client/pull/1"], "no_change": None}
        # Scripted controller proof; post-exit checks below use real managed Git.
        with self.ledger._transaction():
            self.ledger._audit(task["id"], "scene_delivery_verified", details=proof)
        evidence = {"summary": "Scene ready", "verification": "Checked candidate", "prs": proof["prs"],
                    "scene_delivery_id": proof["delivery_id"]}
        self.ledger.finish(task["id"], token, "delivered", evidence)
        return fixture, task, path, proof, evidence

    def test_delivered_scene_clean_exact_head_remains_delivered_after_teardown(self):
        fixture, task, path, proof, evidence = self.delivered_scene()
        self.scheduler._retire(task["id"], "delivered")
        self.assertEqual(self.ledger.item(task["id"])["state"], "delivered")
        cleanup = self.ledger.cleanup_record(task["id"])
        self.assertTrue(cleanup["done"])
        self.assertIsNone(cleanup["error"])
        self.assertEqual(fixture.trees.head(path), proof["commit"])

    def test_temporary_git_reads_leave_delivery_and_cleanup_pending_until_recheck_succeeds(self):
        fixture, task, path, proof, evidence = self.delivered_scene()
        errors = (subprocess.TimeoutExpired(["git", "status"], .1),
                  PermissionError("temporary filesystem access denial"),
                  WorktreeError("index temporarily locked"))
        for error in errors:
            with self.subTest(error=type(error).__name__):
                original = fixture.trees.git_in
                def unavailable(checkout, *args, **kwargs):
                    if args == ("status", "--porcelain", "--untracked-files=all"):
                        raise error
                    return original(checkout, *args, **kwargs)
                with patch.object(fixture.trees, "git_in", side_effect=unavailable):
                    self.scheduler._retire(task["id"], "delivered")
                current, cleanup = self.ledger.item(task["id"]), self.ledger.cleanup_record(task["id"])
                self.assertEqual(current["state"], "delivered")
                self.assertEqual(current["evidence"], evidence)
                self.assertFalse(cleanup["done"])
                self.assertTrue(cleanup["error"])
                self.assertEqual(self.ledger.connection.execute("SELECT COUNT(*) FROM audit WHERE item_id=? "
                    "AND kind='task_delivery_invalidated'", (task["id"],)).fetchone()[0], 0)
                self.assertEqual(fixture.trees.head(path), proof["commit"])
        self.scheduler._retire(task["id"], "delivered")
        self.assertEqual(self.ledger.item(task["id"])["state"], "delivered")
        self.assertTrue(self.ledger.cleanup_record(task["id"])["done"])
        self.assertIsNone(self.ledger.cleanup_record(task["id"])["error"])

    def test_unavailable_ownership_verification_keeps_delivery_fenced_until_recheck(self):
        fixture, task, path, proof, evidence = self.delivered_scene()
        sibling = self.task("next-scene", "scene")
        with patch.object(fixture.trees, "worktree_entry", side_effect=WorktreeError("clone ownership cannot be checked")):
            self.scheduler._retire(task["id"], "delivered")
        self.assertEqual(self.ledger.item(task["id"])["state"], "delivered")
        self.assertFalse(self.ledger.cleanup_record(task["id"])["done"])
        self.assertIsNone(self.scheduler.launch(sibling))
        self.scheduler._retire(task["id"], "delivered")
        self.assertTrue(self.ledger.cleanup_record(task["id"])["done"])
        self.assertEqual(self.ledger.item(task["id"])["evidence"], evidence)

    def test_actual_post_finish_branch_change_invalidates_the_delivery_without_resetting_branch(self):
        fixture, task, path, proof, evidence = self.delivered_scene()
        git_fixtures.git("checkout", "-qb", "unverified-branch", cwd=path)
        self.scheduler._retire(task["id"], "delivered")
        self.assertEqual(self.ledger.item(task["id"])["state"], "failed")
        self.assertTrue(self.ledger.cleanup_record(task["id"])["done"])
        self.assertEqual(fixture.trees.git_in(path, "branch", "--show-current"), "unverified-branch")
        self.assertEqual(fixture.trees.head(path), proof["commit"])

    def test_detached_post_finish_head_is_a_proven_branch_mismatch(self):
        fixture, task, path, proof, evidence = self.delivered_scene()
        git_fixtures.git("checkout", "--quiet", "--detach", proof["commit"], cwd=path)
        self.scheduler._retire(task["id"], "delivered")
        self.assertEqual(self.ledger.item(task["id"])["state"], "failed")
        self.assertTrue(self.ledger.cleanup_record(task["id"])["done"])
        self.assertEqual(fixture.trees.git_in(path, "branch", "--show-current"), "")
        self.assertEqual(fixture.trees.head(path), proof["commit"])

    def test_dirty_post_finish_scene_fails_with_evidence_and_original_baseline_retained_for_retry(self):
        fixture, task, path, proof, evidence = self.delivered_scene()
        sibling = self.task("next-scene", "scene")
        await_tasks(self.ledger, self.parent, self.token)
        late = path / "late.prefab"
        late.write_text("Unverified edit after finish\n", encoding="utf-8")
        self.scheduler._retire(task["id"], "delivered")
        current, cleanup = self.ledger.item(task["id"]), self.ledger.cleanup_record(task["id"])
        self.assertEqual(current["state"], "failed")
        self.assertEqual(current["evidence"], evidence)
        self.assertEqual(current["task_client_base"], proof["baseline"])
        self.assertTrue(cleanup["done"])
        self.assertIn("clean worktree", cleanup["result"]["delivery_error"])
        self.assertTrue(late.exists())
        self.assertEqual(fixture.trees.head(path), proof["commit"])
        self.assertIsNone(self.scheduler.launch(sibling))
        self.assertTrue(wake_parent(self.ledger, self.parent))
        self.token = self.ledger.claim(self.parent, worker_id="coordinator")["token"]
        self.ledger.push_inbox(self.parent, "Retry and revalidate the late candidate.", author=ledger_fixtures.DESIGNER)
        message = self.ledger.issue_context(self.parent)["session_messages"][-1]["id"]
        self.ledger.pop_inbox(self.parent, self.token)
        retried = retry_task(self.ledger, self.parent, self.token, "scene", message)
        paths = self.scheduler._worktrees_for(SKILLS["scene"], retried, self.ledger.issue(task["issue_id"]))
        self.assertEqual(paths["Farm-Client"], path)
        self.assertEqual(self.ledger.item(task["id"])["task_client_base"], proof["baseline"])
        self.assertTrue(late.exists())

    def test_clean_but_new_post_finish_scene_commit_cannot_be_inherited_by_sibling(self):
        fixture, task, path, proof, evidence = self.delivered_scene()
        sibling = self.task("next-scene", "scene")
        (path / "nursery.prefab").write_text("Different unverified scene\n", encoding="utf-8")
        git_fixtures.git("commit", "-qam", "Late unverified scene commit", cwd=path)
        late_head = fixture.trees.head(path)
        self.assertNotEqual(late_head, proof["commit"])
        self.scheduler._retire(task["id"], "delivered")
        self.assertEqual(self.ledger.item(task["id"])["state"], "failed")
        self.assertTrue(self.ledger.cleanup_record(task["id"])["done"])
        self.assertEqual(fixture.trees.head(path), late_head)
        self.assertIsNone(self.scheduler.launch(sibling))

    def test_cancellation_during_teardown_validation_is_never_replaced_with_failure(self):
        fixture, task, path, proof, evidence = self.delivered_scene()
        def cancelled(_):
            with self.ledger._transaction():
                self.ledger._set_state(task["id"], "cancelled", "controller state changed during teardown")
            raise RuntimeError("old delivery check failed")
        with patch.object(self.scheduler, "_verify_retired_task_delivery", side_effect=cancelled):
            self.scheduler._retire(task["id"], "delivered")
        self.assertEqual(self.ledger.item(task["id"])["state"], "cancelled")
        self.assertFalse(self.ledger.cleanup_record(task["id"])["done"])
        self.scheduler._retire(task["id"], "cancelled")
        self.assertTrue(self.ledger.cleanup_record(task["id"])["done"])

    def test_unchanged_ui_proof_checks_all_certified_checkout_heads_after_quiescence(self):
        fixture = git_fixtures.WorktreeTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.trees.remotes["farmgui"] = str(fixture.origin)
        self.scheduler.worktrees = JobWorktrees(fixture.trees, self.ledger)
        task = self.task()
        paths = self.scheduler._worktrees_for(SKILLS["fgui"], task, self.ledger.issue(task["issue_id"]))
        source = paths["farmgui"]
        token = self.ledger.claim(task["id"], worker_id="ui")["token"]
        row = self.ledger._row(task["id"])
        proof = {"delivery_id": uuid4().hex, "generation": row["generation"],
                 "fingerprint": row["claimed_fingerprint"], "baseline": None,
                 "heads": {"farmgui": fixture.trees.head(source)}, "branches": {"farmgui": "farmbot/farm-1"}}
        with self.ledger._transaction():
            self.ledger._audit(task["id"], "fgui_no_change_verified", details=proof)
        evidence = {"summary": "UI unchanged", "verification": "Checked existing UI", "no_change": "Already correct",
                    "prs": [], "ui_no_change_id": proof["delivery_id"]}
        self.ledger.finish(task["id"], token, "delivered", evidence)
        (source / "late.xml").write_text("Unverified source after finish\n", encoding="utf-8")
        self.scheduler._retire(task["id"], "delivered")
        self.assertEqual(self.ledger.item(task["id"])["state"], "failed")
        self.assertEqual(self.ledger.item(task["id"])["evidence"], evidence)
        self.assertTrue(self.ledger.cleanup_record(task["id"])["done"])
        self.assertTrue((source / "late.xml").exists())

    def test_full_ui_delivered_pair_is_rechecked_after_worker_quiescence(self):
        for late_change in (None, "source", "client"):
            with self.subTest(late_change=late_change):
                fixture = delivery_fixtures.UiDeliveryTests()
                fixture.internal = True
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                proof = fixture.complete_checks()
                evidence = fixture.evidence(proof)
                evidence.pop("comment_action_id")
                fixture.ledger.finish(fixture.item, fixture.fixture.source.token, "delivered", evidence)
                scheduler = Scheduler(fixture.ledger, self.fixture.launcher, SKILLS, fixture.fixture.source.trees,
                    replay_legacy=True, skill_root=self.scheduler.skill_root, db_path=fixture.fixture.paths.ledger,
                    runtime_name="fake", host="fixture")
                if late_change == "source":
                    fixture.fixture.source.git(fixture.fixture.source.path, "commit", "--allow-empty", "-qm", "Late source commit")
                elif late_change == "client":
                    (fixture.fixture.client / "late.cs").write_text("Unverified Client file\n", encoding="utf-8")
                scheduler._retire(fixture.item, "delivered")
                self.assertEqual(fixture.ledger.item(fixture.item)["state"], "failed" if late_change else "delivered")
                self.assertTrue(fixture.ledger.cleanup_record(fixture.item)["done"])
                self.assertEqual(fixture.ledger.item(fixture.item)["evidence"], evidence)


class ExportResourceWaitTests(unittest.TestCase):
    def test_busy_resource_retries_without_starting_a_process_and_returns_the_acquired_lock(self):
        lock, fence = Mock(), Mock()
        with patch.object(publisher, "_WindowsLock", side_effect=[publisher.ExportLockBusy("busy"),
                publisher.ExportLockBusy("busy"), lock]), \
                patch.object(publisher.time, "monotonic", return_value=0), \
                patch.object(publisher.time, "sleep") as sleep, patch.object(publisher.subprocess, "Popen") as process:
            self.assertIs(publisher._acquire_export_lock(fence, 5), lock)
        self.assertEqual(fence.call_count, 2)
        self.assertEqual(sleep.call_count, 2)
        process.assert_not_called()

    def test_stop_or_stale_authority_ends_resource_wait_before_a_process_can_start(self):
        fence = Mock(side_effect=LedgerError("claim cancelled"))
        with patch.object(publisher, "_WindowsLock", side_effect=publisher.ExportLockBusy("busy")), \
                patch.object(publisher.subprocess, "Popen") as process:
            with self.assertRaisesRegex(LedgerError, "claim cancelled"):
                publisher._acquire_export_lock(fence, 5)
        fence.assert_called_once_with()
        process.assert_not_called()

    def test_resource_wait_is_bounded_and_does_not_retry_unrelated_native_errors(self):
        with patch.object(publisher, "_WindowsLock", side_effect=publisher.ExportLockBusy("busy")), \
                patch.object(publisher.time, "monotonic", side_effect=[0, 5]):
            with self.assertRaisesRegex(publisher.ExportLockBusy, "timed out waiting for the machine export lock"):
                publisher._acquire_export_lock(lambda: None, 5)
        with patch.object(publisher, "_WindowsLock", side_effect=OSError("native inventory denied")) as lock:
            with self.assertRaisesRegex(OSError, "native inventory denied"):
                publisher._acquire_export_lock(lambda: None, 5)
        lock.assert_called_once_with()


@unittest.skipUnless(os.name == "nt", "native Windows publisher mutex and Job Objects")
class NativeExportResourceWaitTests(unittest.TestCase):
    def test_export_waits_for_the_machine_mutex_then_runs_in_its_own_quiescent_job(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "publisher wait 验证"
            root.mkdir()
            ready, release, marker = root / "ready", root / "release", root / "export-started"
            name = "Local\\FarmBot-TestPublisherWait-" + uuid4().hex
            module_root = str(Path(__file__).resolve().parents[1])
            owner_script = (f'import sys,time\nfrom pathlib import Path\nsys.path.insert(0,{module_root!r})\n'
                'from agent.fgui_publisher import _WindowsLock\n'
                f'with _WindowsLock({name!r}):\n Path({str(ready)!r}).touch()\n'
                f' while not Path({str(release)!r}).exists(): time.sleep(.02)\n')
            owner = subprocess.Popen([sys.executable, "-c", owner_script])
            try:
                deadline = time.monotonic() + 5
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(ready.exists())
                run = root / "run"; run.mkdir()
                original = publisher._WindowsLock
                waits = 0
                def fence():
                    nonlocal waits
                    if not release.exists():
                        waits += 1
                        self.assertFalse(marker.exists())
                        release.touch()
                with patch.object(publisher, "_WindowsLock", side_effect=lambda: original(name)), \
                        patch.object(publisher, "_foreign_editors", return_value=False):
                    result = publisher._windows_run([sys.executable, "-c",
                        f'from pathlib import Path; Path({str(marker)!r}).touch()'], root, run, fence, 5)
                self.assertEqual(waits, 1)
                self.assertTrue(marker.exists())
                self.assertTrue(result["job_assigned_before_export"])
                self.assertTrue(result["job_empty"])
                self.assertEqual(result["exit_code"], 0)
                self.assertEqual(json.loads((run / "native-process.json").read_text(encoding="utf-8"))["state"], "complete")
                owner.wait(timeout=5)
            finally:
                if owner.poll() is None:
                    owner.terminate(); owner.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
