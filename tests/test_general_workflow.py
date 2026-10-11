"""One issue, durable presentation tasks and fenced shared repositories, offline."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from agent import router
from agent.dispatch import authority
from agent.ledger import LedgerError
from agent.skills import load_skills
from agent.workflow import (JobWorktrees, await_tasks, client_ready, delegate_task,
                            forward_answer, repository_busy, retry_task, task_views, wake_parent)
import test_ledger as fixtures
import test_scheduler as scheduler_fixtures

SKILLS = load_skills(Path(__file__).resolve().parents[1] / "skills")
HANDOFF = {"facts": [], "hypotheses": [], "checks": [], "repositories": [], "next_actions": ["continue"]}


class GeneralWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.LedgerBase()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.ledger = self.fixture.ledger
        self.parent = self.fixture.new_item("work", target=None)["id"]
        self.ledger.set_worker(self.parent, 123, "test")
        self.token = self.ledger.claim(self.parent, worker_id="general")["token"]
        self.save()

    def save(self, **stages):
        self.ledger.checkpoint(self.parent, self.token, {
            "plan": {"stages": {"A": "done", "D": "pending", **stages}}, "handoff": HANDOFF})

    def task(self, key="ui", capability="fgui"):
        return delegate_task(self.ledger, self.parent, self.token,
                             {"key": key, "capability": capability, "instructions": "Use the shared spec; ask for missing art."})

    def claim_task(self, task):
        return self.ledger.claim(task["id"], worker_id="presentation")["token"]

    def message(self, body, author=fixtures.DESIGNER):
        self.ledger.push_inbox(self.parent, body, author=author, received_at=1001)
        return self.ledger.issue_context(self.parent)["session_messages"][-1]["id"]

    def test_bot_labels_do_not_select_a_profile_in_general_mode(self):
        for labels in ([], [{"group": "Bot", "label": "UI"}], [{"group": "Bot", "label": "Code"}],
                       [{"group": "Bot", "label": "unknown"}, {"group": "功能", "label": "修改"}]):
            with self.subTest(labels=labels):
                result = router.route(action="created", is_delegation=True, text="", labels=["Bug"],
                    label_groups=labels, active_state=None, terminal_exists=False, available_skills={"chat", "work"})
                self.assertEqual(result, router.Decision("work", "work"))
                self.assertIsNone(router.start_refusal([r["label"] for r in labels], {"chat", "work"}))
        mention = router.route(action="created", is_delegation=False, text="implement this", labels=[],
            active_state=None, terminal_exists=False, available_skills={"chat", "work"})
        self.assertEqual(mention.kind, "chat")

    def test_shared_spec_settles_before_delegation_including_visual_only_skip(self):
        self.save(A="pending")
        with self.assertRaisesRegex(LedgerError, "settle the shared OpenSpec"):
            self.task()
        self.save(A="skipped: existing contract")
        self.assertEqual(self.task()["parent_id"], self.parent)

    def test_task_creation_is_idempotent_and_cannot_change_scope_or_bypass_claim(self):
        task = self.task()
        self.assertEqual(self.task()["id"], task["id"])
        with self.assertRaisesRegex(LedgerError, "different instructions"):
            delegate_task(self.ledger, self.parent, self.token, {"key": "ui", "capability": "scene", "instructions": "changed"})
        with self.assertRaisesRegex(LedgerError, "matching token"):
            delegate_task(self.ledger, self.parent, "wrong", task["task_request"])
        token = self.claim_task(task)
        with self.assertRaisesRegex(LedgerError, "only the general job"):
            delegate_task(self.ledger, task["id"], token, {"key": "other", "capability": "fgui", "instructions": "x"})
        for capability in ([], "unknown"):
            with self.subTest(capability=capability), self.assertRaises(LedgerError):
                delegate_task(self.ledger, self.parent, self.token, {"key": "bad", "capability": capability, "instructions": "x"})

    def test_changed_inputs_block_new_task_admission(self):
        self.ledger.observe_issue(fixtures.issue(description="New behavior ruling"))
        with self.assertRaisesRegex(LedgerError, "changed issue inputs"):
            self.task()

    def test_task_claims_do_not_replace_issue_or_stop_owner(self):
        ui, scene = self.task(), self.task("scene", "scene")
        self.claim_task(ui)
        self.claim_task(scene)
        self.assertEqual(self.ledger.active_item_for_issue(fixtures.ISSUE)["id"], self.parent)
        self.assertEqual(self.ledger.active_item_for_session(fixtures.SESSION)["id"], self.parent)
        self.assertEqual(self.ledger.stop_target(fixtures.SESSION)[0]["id"], self.parent)
        self.assertEqual(len(task_views(self.ledger, self.parent)), 2)
        with self.assertRaisesRegex(LedgerError, "active work item"):
            self.ledger.create_work_item(issue_id=fixtures.ISSUE, session_id=fixtures.SESSION, skill="fix")

    def test_partial_answers_keep_attribution_and_wake_only_target_task(self):
        ui, scene = self.task(), self.task("scene", "scene")
        for task in (ui, scene):
            self.ledger.await_input(task["id"], self.claim_task(task), "Which art and dimensions?")
        message_id = self.message("Use icon A; dimensions still undecided.")
        original = self.ledger.issue_context(self.parent)["session_messages"][-1]
        self.ledger.pop_inbox(self.parent, self.token)
        forward_answer(self.ledger, self.parent, self.token, "ui", message_id)
        forward_answer(self.ledger, self.parent, self.token, "ui", message_id)
        forwarded = self.ledger.issue_context(ui["id"])["session_messages"]
        self.assertEqual(len(forwarded), 1)
        self.assertEqual({k: forwarded[0][k] for k in ("body", "author", "created_at")},
                         {k: original[k] for k in ("body", "author", "created_at")})
        self.assertEqual(self.ledger.item(scene["id"])["state"], "awaiting_input")
        token = self.claim_task(ui)
        self.ledger.pop_inbox(ui["id"], token)
        self.ledger.checkpoint(ui["id"], token, {"plan": {"ui": {"answers": {"icon": "A", "message_id": message_id}}}})
        self.ledger.await_input(ui["id"], token, "Dimensions still needed")
        self.assertEqual(self.ledger.item(ui["id"])["checkpoint"]["plan"]["ui"]["answers"]["icon"], "A")
        self.assertEqual(self.ledger.item(self.parent)["state"], "running")

    def test_answer_rejects_unattributed_or_cross_job_messages(self):
        self.task()
        message = self.message("unknown", author=None)
        with self.assertRaisesRegex(LedgerError, "actual attributed"):
            forward_answer(self.ledger, self.parent, self.token, "ui", message)
        with self.assertRaisesRegex(LedgerError, "actual attributed"):
            forward_answer(self.ledger, self.parent, self.token, "ui", message + 100)
        with self.assertRaises(LedgerError):
            forward_answer(self.ledger, self.parent, self.token, "ui", True)

    def test_task_question_wakes_coordinator_once_and_survives_restart(self):
        ui = self.task()
        self.ledger.await_input(ui["id"], self.claim_task(ui), "Missing seed icon: provide the asset location.")
        await_tasks(self.ledger, self.parent, self.token)
        self.assertTrue(wake_parent(self.ledger, self.parent))
        self.token = self.ledger.claim(self.parent, worker_id="coordinator")["token"]
        self.save(D="done")
        await_tasks(self.ledger, self.parent, self.token)
        self.ledger.close()
        self.ledger = self.fixture.open_ledger()
        self.assertFalse(wake_parent(self.ledger, self.parent))
        self.assertIn("Missing seed icon", self.ledger.issue_context(ui["id"])["pending_question"])

    def test_pending_reply_cannot_be_stranded_by_internal_wait(self):
        self.task()
        self.message("Use uploaded art")
        self.assertEqual(await_tasks(self.ledger, self.parent, self.token)["state"], "queued")

    def test_same_question_in_a_new_round_wakes_parent_after_partial_answer(self):
        task = self.task()
        self.ledger.await_input(task["id"], self.claim_task(task), "Which art and dimensions?")
        await_tasks(self.ledger, self.parent, self.token)
        self.assertTrue(wake_parent(self.ledger, self.parent))
        self.token = self.ledger.claim(self.parent, worker_id="coordinator")["token"]
        message = self.message("Use icon A; dimensions still undecided.")
        self.ledger.pop_inbox(self.parent, self.token)
        forward_answer(self.ledger, self.parent, self.token, "ui", message)
        await_tasks(self.ledger, self.parent, self.token)
        task_token = self.claim_task(task)
        self.ledger.pop_inbox(task["id"], task_token)
        self.ledger.await_input(task["id"], task_token, "Which art and dimensions?")
        self.assertTrue(wake_parent(self.ledger, self.parent))
        self.token = self.ledger.claim(self.parent, worker_id="coordinator")["token"]
        await_tasks(self.ledger, self.parent, self.token)
        self.assertFalse(wake_parent(self.ledger, self.parent))

    def test_repeated_failure_after_human_retry_wakes_parent(self):
        task = self.task()
        self.claim_task(task)
        self.ledger.fail(task["id"], "missing tool")
        self.ledger.record_cleanup(task["id"], {}, done=True)
        await_tasks(self.ledger, self.parent, self.token)
        self.assertTrue(wake_parent(self.ledger, self.parent))
        self.token = self.ledger.claim(self.parent, worker_id="coordinator")["token"]
        message = self.message("Retry UI after fixing the tool.")
        self.ledger.pop_inbox(self.parent, self.token)
        retry_task(self.ledger, self.parent, self.token, "ui", message)
        await_tasks(self.ledger, self.parent, self.token)
        self.claim_task(task)
        self.ledger.fail(task["id"], "missing tool")
        self.assertTrue(wake_parent(self.ledger, self.parent))
        self.token = self.ledger.claim(self.parent, worker_id="coordinator")["token"]
        await_tasks(self.ledger, self.parent, self.token)
        self.ledger.record_cleanup(task["id"], {}, done=True)
        self.assertFalse(wake_parent(self.ledger, self.parent))

    def test_certified_completion_wakes_parent_and_failure_cancels_siblings(self):
        task = self.task()
        self.ledger.finish(task["id"], self.claim_task(task), "delivered", {"summary": "unchanged",
            "verification": "checked existing assets", "no_change": "No asset delta"})
        self.ledger.record_cleanup(task["id"], {}, done=True)
        await_tasks(self.ledger, self.parent, self.token)
        self.assertTrue(wake_parent(self.ledger, self.parent))
        self.token = self.ledger.claim(self.parent, worker_id="coordinator")["token"]
        sibling = self.task("scene", "scene")
        self.ledger.fail(self.parent, "server failed")
        self.assertEqual(self.ledger.item(sibling["id"])["state"], "cancelled")

    def test_child_is_owned_work_and_is_not_an_independent_resumable_job(self):
        from agent.foreign_work import _lineage
        task = self.task()
        self.assertEqual({r["id"] for r in _lineage(self.ledger, self.parent)}, {self.parent, task["id"]})
        self.ledger.cancel(self.parent, "Stop")
        self.assertEqual(self.ledger._resumable_work(fixtures.ISSUE, fixtures.SESSION)["id"], self.parent)
        with self.assertRaisesRegex(LedgerError, "active general job"):
            self.ledger.retry(task["id"], "retry only child")

    def test_scene_cannot_be_started_outside_general_job(self):
        with self.assertRaisesRegex(LedgerError, "internal presentation capability"):
            self.ledger.create_work_item(issue_id=fixtures.ISSUE, session_id=fixtures.SESSION, skill="scene")

    def test_client_gameplay_waits_for_server_and_certified_presentation(self):
        ui = self.task()
        with self.assertRaisesRegex(LedgerError, "server/config"):
            client_ready(self.ledger, self.ledger._row(self.parent))
        self.save(D="done")
        with self.assertRaisesRegex(LedgerError, "presentation delivery"):
            self.ledger.handoff_repository(self.parent, self.token, "Farm-Client", skill=SKILLS["work"])
        self.ledger.finish(ui["id"], self.claim_task(ui), "delivered", {
            "summary": "Already correct", "verification": "Checked existing resources", "no_change": "No presentation delta"})
        with self.assertRaisesRegex(LedgerError, "certified task teardown"):
            client_ready(self.ledger, self.ledger._row(self.parent))
        self.ledger.record_cleanup(ui["id"], {}, done=True)
        handoff = self.ledger.handoff_repository(self.parent, self.token, "Farm-Client", skill=SKILLS["work"])
        self.assertEqual(handoff["next_root_repo"], "Farm-Client")

    def test_fgui_full_delivery_still_requires_controller_proofs(self):
        ui = self.task()
        with self.assertRaisesRegex(LedgerError, "current controller verification"):
            self.ledger.finish(ui["id"], self.claim_task(ui), "delivered", {"summary": "UI ready",
                "verification": "claimed pass", "prs": ["https://github.com/example/farmgui/pull/1"]})

    def test_parent_cannot_deliver_while_tasks_are_active_or_omit_their_prs(self):
        task = self.task("scene", "scene")
        with self.assertRaisesRegex(LedgerError, "all presentation tasks"):
            self.ledger.finish(self.parent, self.token, "delivered", {"summary": "done"})
        self.ledger.finish(task["id"], self.claim_task(task), "delivered", {"summary": "Scene ready",
            "verification": "Checked scene", "prs": ["https://github.com/example/Farm-Client/pull/1"]})
        with self.assertRaisesRegex(LedgerError, "certified presentation teardown"):
            self.ledger.finish(self.parent, self.token, "delivered", {"summary": "done", "prs": []})
        self.ledger.record_cleanup(task["id"], {}, done=True)
        with self.assertRaisesRegex(LedgerError, "every presentation task's draft"):
            self.ledger.finish(self.parent, self.token, "delivered", {"summary": "done", "prs": []})

    def test_tasks_cannot_post_outcome_comments(self):
        task = self.task()
        with self.assertRaisesRegex(LedgerError, "only the general job"):
            self.ledger.prepare_comment(task["id"], self.claim_task(task), "delivery", "Ready")

    def test_stop_and_failure_revoke_all_task_claims_and_retain_questions(self):
        ui, scene = self.task(), self.task("scene", "scene")
        ui_token, scene_token = self.claim_task(ui), self.claim_task(scene)
        self.ledger.await_input(ui["id"], ui_token, "Missing asset")
        self.ledger.cancel(self.parent, "human Stop")
        for task in (ui, scene):
            self.assertEqual(self.ledger.item(task["id"])["state"], "cancelled")
        self.assertEqual(self.ledger.issue_context(ui["id"])["pending_question"], "Missing asset")
        with self.assertRaises(LedgerError):
            self.ledger.renew(scene["id"], scene_token)

    def test_withdrawal_fences_tasks_before_grace_but_allows_checkpoint(self):
        task = self.task()
        token = self.claim_task(task)
        self.ledger.flag_withdrawal(self.parent, "undelegated", 1010)
        self.ledger.checkpoint(task["id"], token, {"summary": "saved partial source"})
        with self.assertRaisesRegex(LedgerError, "delegation withdrawn"):
            self.ledger.await_input(task["id"], token, "Need icon")
        self.assertEqual(self.ledger.item(task["id"])["withdraw_deadline"], 1010)

    def test_retry_requires_human_message_and_certified_teardown(self):
        task = self.task()
        self.claim_task(task)
        self.ledger.fail(task["id"], "publisher missing")
        message = self.message("Publisher prepared; retry UI.")
        with self.assertRaisesRegex(LedgerError, "certified teardown"):
            retry_task(self.ledger, self.parent, self.token, "ui", message)
        self.ledger.record_cleanup(task["id"], {}, done=True)
        self.assertEqual(retry_task(self.ledger, self.parent, self.token, "ui", message)["state"], "queued")

    def test_migration_keeps_one_root_slot_while_allowing_durable_tasks(self):
        self.ledger.connection.execute("DROP INDEX one_active_item_per_issue")
        self.ledger.connection.execute("CREATE UNIQUE INDEX one_active_item_per_issue ON work_items(issue_id) "
            "WHERE state IN ('queued','running','awaiting_input','awaiting_resource')")
        self.ledger.close()
        self.ledger = self.fixture.open_ledger()
        self.assertEqual(self.task()["parent_id"], self.parent)
        self.assertEqual(self.ledger.active_item_for_issue(fixtures.ISSUE)["id"], self.parent)

    def test_task_client_entry_is_immutable_and_separate_from_job_baseline(self):
        task = self.task("scene", "scene")
        target = {**fixtures.PIN, "requested_ref": "default"}
        view = self.ledger.pin_staged_client(task["id"], task["generation"], target, branch="farmbot/farm-1")
        self.assertEqual(self.ledger.item(self.parent)["target"], view["target"])
        self.ledger.pin_task_client_base(task["id"], task["generation"], "b" * 40)
        with self.assertRaisesRegex(LedgerError, "immutable"):
            self.ledger.pin_task_client_base(task["id"], task["generation"], "c" * 40)
        self.assertEqual(self.ledger.item(self.parent)["target"]["commit_sha"], "a" * 40)
        other = self.task("other", "scene")
        self.ledger.pin_staged_client(other["id"], other["generation"], view["target"], branch="farmbot/farm-1")
        with self.assertRaisesRegex(LedgerError, "retains this job's Client stage"):
            self.ledger.pin_task_client_base(other["id"], other["generation"], "b" * 40)

    def test_authority_carries_shared_spec_assets_and_existing_export_gates(self):
        general, ui = authority("work"), authority("fgui")
        self.assertIn("including UI-only behavior", general)
        self.assertIn("Missing assets are human questions", general)
        self.assertNotIn("no standing MCP or kw_ops grant exists", general)
        self.assertIn("guard/loading proofs", ui)
        self.assertFalse(SKILLS["work"].exclusive)

    def test_successor_ui_cannot_hide_interrupted_candidate_with_a_new_task_key(self):
        previous = self.task()
        self.ledger.connection.execute("UPDATE work_items SET root_repo='Farm-Client' WHERE id=?", (previous["id"],))
        pin = self.ledger.pin_staged_client(previous["id"], previous["generation"], fixtures.PIN, branch="farmbot/farm-1")["target"]
        self.ledger.pin_task_client_base(previous["id"], previous["generation"], "b" * 40)
        self.ledger.cancel(self.parent, "Stop")
        self.parent = self.ledger.create_work_item(issue_id=fixtures.ISSUE, session_id=fixtures.SESSION,
                                                  skill="work", target=pin)["id"]
        self.token = self.ledger.claim(self.parent, worker_id="successor")["token"]
        self.save()
        task = self.task("new-ui-key")
        self.ledger.connection.execute("UPDATE work_items SET root_repo='Farm-Client' WHERE id=?", (task["id"],))
        self.ledger.pin_staged_client(task["id"], task["generation"], pin, branch="farmbot/farm-1")
        with self.assertRaisesRegex(LedgerError, "uncertified predecessor"):
            self.ledger.pin_task_client_base(task["id"], task["generation"], "c" * 40)
        self.assertIsNone(self.ledger.item(task["id"])["task_client_base"])
        self.ledger.pin_task_client_base(task["id"], task["generation"], "b" * 40)


class GeneralSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = scheduler_fixtures.SchedulerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.ledger, self.scheduler = self.fixture.ledger, self.fixture.scheduler
        self.scheduler.enabled_skills = {"chat", "work"}
        self.scheduler.max_concurrent = 3
        self.parent = self.fixture.item(skill="work")["id"]
        self.ledger.connection.execute("UPDATE work_items SET target_json=NULL,root_repo='farm-hive' WHERE id=?", (self.parent,))
        self.scheduler.tick()
        self.token = self.ledger.claim(self.parent, worker_id="server")["token"]
        self.ledger.checkpoint(self.parent, self.token, {"plan": {"stages": {"A": "done", "D": "pending"}}})

    def task(self, key="ui", capability="fgui"):
        return delegate_task(self.ledger, self.parent, self.token, {"key": key, "capability": capability, "instructions": "Shared spec"})

    def test_presentation_and_server_launch_together_with_separate_scope(self):
        task = self.task()
        self.assertEqual(self.scheduler.tick()["launched"], 1)
        self.assertEqual(set(self.scheduler.active), {self.parent, task["id"]})
        launch = self.fixture.launcher.spawned[-1]
        payload = json.loads(launch[1].split("\n\n", 1)[1])
        self.assertEqual(payload["task"]["parent_id"], self.parent)
        self.assertEqual(payload["stage"]["write_repositories"], ["farmgui"])
        self.assertEqual(Path(launch[4]), self.fixture.trees.root / self.parent / "farmgui")
        self.assertIn(("Farm-Client", task["id"], True), self.fixture.trees.reads)
        self.assertIn(("Farm-Contract", task["id"], True), self.fixture.trees.reads)

    def test_restart_without_handles_still_blocks_same_repository_writer(self):
        first, second = self.task(), self.task("ui-other")
        self.scheduler.tick()
        launched = set(self.scheduler.active) - {self.parent}
        self.assertEqual(len(launched), 1)
        if second["id"] in launched:
            first, second = second, first
        self.ledger.claim(first["id"], worker_id="old-ui")
        self.scheduler.active.clear()
        self.fixture.launcher.alive_pids.add(self.ledger.item(first["id"])["worker_pid"])
        self.assertTrue(repository_busy(self.ledger, self.ledger.item(second["id"]), SKILLS))
        self.assertEqual(self.scheduler.tick()["launched"], 0)

    def test_waiting_client_task_retains_stage_across_human_gate(self):
        first, second = self.task("scene-one", "scene"), self.task("scene-two", "scene")
        self.ledger.pin_staged_client(first["id"], first["generation"], fixtures.PIN, branch="farmbot/farm-1")
        self.ledger.pin_task_client_base(first["id"], first["generation"], "a" * 40)
        token = self.ledger.claim(first["id"], worker_id="scene")["token"]
        self.ledger.await_input(first["id"], token, "Need scene art")
        self.assertTrue(repository_busy(self.ledger, self.ledger.item(second["id"]), SKILLS))
        self.assertFalse(repository_busy(self.ledger, self.ledger.item(self.parent), SKILLS))

    def test_ui_retains_source_through_human_wait_and_client_verification(self):
        first, second = self.task(), self.task("other-ui")
        token = self.ledger.claim(first["id"], worker_id="ui")["token"]
        self.ledger.await_input(first["id"], token, "Approve this visual round")
        self.assertTrue(repository_busy(self.ledger, self.ledger.item(second["id"]), SKILLS))
        self.assertFalse(repository_busy(self.ledger, self.ledger.item(self.parent), SKILLS))
        self.ledger.connection.execute("UPDATE work_items SET root_repo='Farm-Client' WHERE id=?", (first["id"],))
        self.assertTrue(repository_busy(self.ledger, self.ledger.item(second["id"]), SKILLS))
        self.ledger.connection.execute("UPDATE work_items SET state='delivered' WHERE id=?", (first["id"],))
        self.assertTrue(repository_busy(self.ledger, self.ledger.item(second["id"]), SKILLS))
        self.ledger.record_cleanup(first["id"], {}, done=True)
        self.assertFalse(repository_busy(self.ledger, self.ledger.item(second["id"]), SKILLS))

    def test_failed_client_task_retains_candidate_for_attributed_retry(self):
        first, second = self.task("scene-one", "scene"), self.task("scene-two", "scene")
        self.ledger.pin_staged_client(first["id"], first["generation"], fixtures.PIN, branch="farmbot/farm-1")
        self.ledger.pin_task_client_base(first["id"], first["generation"], "a" * 40)
        self.ledger.claim(first["id"], worker_id="scene")
        self.ledger.fail(first["id"], "Missing host tool")
        self.ledger.record_cleanup(first["id"], {}, done=True)
        self.assertTrue(repository_busy(self.ledger, self.ledger.item(second["id"]), SKILLS))
        self.assertFalse(repository_busy(self.ledger, self.ledger.item(self.parent), SKILLS))

    def test_retired_claim_after_restart_still_requires_old_writer_quiescence(self):
        first = self.task()
        self.scheduler.tick()
        token = self.ledger.claim(first["id"], worker_id="ui")["token"]
        self.ledger.await_input(first["id"], token, "Need art")
        second = self.task("other-ui")
        self.scheduler.active.clear()
        with patch.object(self.fixture.launcher, "assert_quiescent", side_effect=RuntimeError("old writer still alive")):
            self.assertEqual(self.scheduler.tick()["launched"], 0)
        self.assertEqual(self.ledger.item(second["id"])["state"], "queued")

    def test_child_teardown_never_removes_shared_repository_and_parent_waits(self):
        task = self.task()
        self.scheduler.tick()
        self.ledger.cancel(self.parent, "Stop")
        self.scheduler.tick()
        self.assertTrue(self.ledger.cleanup_record(task["id"])["done"])
        self.scheduler.tick()
        self.assertTrue(self.ledger.cleanup_record(self.parent)["done"])
        self.assertNotIn(("removed", task["id"], None), self.fixture.trees.added)
        self.assertIn(("removed", self.parent, None), self.fixture.trees.added)
        self.assertIn(task["id"], self.fixture.trees.reads_removed)

    def test_stop_signals_children_immediately_after_fencing_the_whole_job(self):
        task = self.task()
        self.scheduler.tick()
        self.ledger.claim(task["id"], worker_id="ui")
        states = []
        self.fixture.launcher.on_stop = lambda item_id: states.append(
            (self.ledger.item(self.parent)["state"], self.ledger.item(task["id"])["state"]))
        self.scheduler.stop(self.parent, "Stop the entire issue")
        self.assertEqual(set(self.fixture.launcher.stopped), {self.parent, task["id"]})
        self.assertEqual(set(states), {("cancelled", "cancelled")})

    def test_worktree_adapter_never_redirects_task_reads_or_unrelated_jobs(self):
        task = self.task()
        trees = JobWorktrees(self.fixture.trees, self.ledger)
        self.assertEqual(trees.add("farmgui", task["id"], "farmbot/farm-1"),
                         self.fixture.trees.root / self.parent / "farmgui")
        trees.read_checkout("Farm-Client", task["id"])
        self.assertEqual(self.fixture.trees.reads[-1][1], task["id"])


if __name__ == "__main__":
    unittest.main()
