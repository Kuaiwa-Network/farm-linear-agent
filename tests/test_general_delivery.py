"""General-task delivery evidence, transport failures and local conversations."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from agent.__main__ import parser, run
from agent.ledger import LedgerError
from agent.workflow import await_tasks, delegate_task, wake_parent
import test_ledger as fixtures


class GeneralDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.LedgerBase()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.ledger = self.fixture.ledger
        self.parent = self.fixture.new_item("work", target=None)["id"]
        self.token = self.ledger.claim(self.parent, worker_id="parent")["token"]
        self.ledger.checkpoint(self.parent, self.token, {"plan": {"stages": {"A": "done"}}})
        self.api = Mock()
        self.api.app_user_id = "app"
        self.api.issue_status.return_value = {"archived": False, "status_type": "started", "delegate_id": "app"}

    def task(self, capability="fgui"):
        return delegate_task(self.ledger, self.parent, self.token, {
            "key": capability, "capability": capability, **({"scope": {"assets": ["Assets/nursery.prefab"], "validation": "source"}} if capability == "scene" else {}), "instructions": "Implement the shared contract."})

    def claim(self, task):
        return self.ledger.claim(task["id"], worker_id="task")["token"]

    def outcome(self, kind, **values):
        action = self.ledger.prepare_comment(self.parent, self.token, kind, "Recorded parent result")
        self.ledger.confirm_comment(action["action_id"], "confirmed-comment")
        return {"summary": "Parent result", "comment_action_id": action["action_id"], **values}

    def cli(self, command, task, token, **flags):
        argv = ["--db", str(self.fixture.path), command, "--item", task["id"], "--token", token]
        for name, value in flags.items():
            argv.extend(["--" + name.replace("_", "-"), str(value)])
        return run(parser().parse_args(argv), self.ledger, lambda: self.api)

    def test_blocked_child_null_prs_does_not_crash_parent_blocker(self):
        task = self.task()
        result = self.ledger.finish(task["id"], self.claim(task), "blocked", {"summary": "Missing art", "prs": None})
        self.assertEqual(result["evidence"]["prs"], [])
        # Old controller records may still contain null. Blocking remains possible.
        self.ledger.connection.execute("UPDATE work_items SET evidence=? WHERE id=?",
                                       (json.dumps({"summary": "Missing art", "prs": None}), task["id"]))
        result = self.ledger.finish(self.parent, self.token, "blocked", self.outcome("blocker"))
        self.assertEqual(result["state"], "blocked")

    def test_historical_no_change_null_prs_does_not_crash_parent_delivery(self):
        task = self.task()
        evidence = {"summary": "Already correct", "verification": "Checked assets", "no_change": "No delta", "prs": None}
        self.ledger.finish(task["id"], self.claim(task), "delivered", evidence)
        self.ledger.record_cleanup(task["id"], {}, done=True)
        self.ledger.connection.execute("UPDATE work_items SET evidence=? WHERE id=?", (json.dumps(evidence), task["id"]))
        result = self.ledger.finish(self.parent, self.token, "delivered", self.outcome(
            "delivery", verification="Checked full issue", no_change="Already correct", prs=[]))
        self.assertEqual(result["state"], "delivered")

    def test_docs_only_sorting_retains_inputs_across_old_wait_and_restart_then_finishes(self):
        """The new skip record survives SQLite/recovery without a migration or external task.

        Input relevance is a worker decision; this tests its durable representation
        and normal finish, not an automatic classifier or model obedience.
        """
        self.ledger.checkpoint(self.parent, self.token, {"plan": {
            "stages": {"A": "done", "F": "done", "G": "pending"},
            "closing": {"client_resynced": False},
            "pause": {"kind": "closing", "reason": "waiting", "notice": "closing"}}})
        self.ledger.await_input(self.parent, self.token, "Old plan waits for sharing compatibility", reason="waiting")
        self.ledger.resume(self.parent, "Re-evaluate incidental wait within sorting scope")
        self.token = self.ledger.claim(self.parent, worker_id="scope-review")["token"]
        network = {"required": False, "reason": "Only friend sorting specification changed; existing fields suffice",
                   "base_commit": "a" * 40, "source_commit": "b" * 40, "consumer_commit": "c" * 40,
                   "evidence": ["proto/friend.proto", "openspec/changes/friend-sort/specs/friend/spec.md"]}
        plan = {"stages": {"A": "done", "B": "skipped: no config", "C": "skipped: no config",
                           "D": "skipped: no server", "E": "skipped: no presentation", "F": "done", "G": "done"},
                "client": {"inputs": {"network": network}},
                "closing": {"waivers_removed": True, "client_resynced": True, "hive_resynced": True,
                            "pin_written": True, "writeback_opened": True,
                            "not_required": {"client_resynced": network["reason"],
                                             "hive_resynced": "No server work", "pin_written": "No config work"}}}
        self.ledger.checkpoint(self.parent, self.token, {"plan": plan, "handoff": {
            "facts": [{"claim": "Required sorting checks passed in fixture evidence", "evidence": "sorting-results.xml"},
                      {"claim": "Newer Contract main deletes sharing messages; not adopted or required for sorting",
                       "evidence": "contract-input-inventory.json"}],
            "hypotheses": [], "checks": [], "repositories": [],
            "next_actions": ["Deliver sorting and record actual retained inputs; do not wait for sharing"]}})
        self.ledger.close()
        self.ledger = self.fixture.open_ledger()
        self.assertEqual(self.ledger.issue_context(self.parent)["plan"], plan)
        self.assertFalse(self.ledger.reservations())
        self.assertEqual(self.ledger.issue_context(self.parent)["tasks"], [])
        result = self.ledger.finish(self.parent, self.token, "delivered", self.outcome(
            "delivery", verification="Fixture sorting checks passed; protocol regeneration not required",
            prs=["https://github.com/example/Farm-Client/pull/1"]))
        self.assertEqual(result["state"], "delivered")
        self.assertEqual(result["checkpoint"]["plan"]["client"]["inputs"]["network"], network)

    def test_malformed_blocked_task_prs_is_a_claim_preserving_validation_error(self):
        task = self.task()
        token = self.claim(task)
        for value in ("https://github.com/example/Client/pull/1", 42, [{}], False):
            with self.subTest(value=value), self.assertRaisesRegex(LedgerError, "canonical HTTPS"):
                self.ledger.finish(task["id"], token, "blocked", {"summary": "Blocked", "prs": value})
        self.assertEqual(self.ledger.item(task["id"])["state"], "running")

    def test_scene_prose_and_arbitrary_configured_pr_cannot_replace_controller_proof(self):
        task = self.task("scene")
        token = self.claim(task)
        for extra in ({"prs": ["https://github.com/example/Farm-Client/pull/1"]}, {"no_change": "Checked it"}):
            with self.subTest(extra=extra), self.assertRaisesRegex(LedgerError, "current controller verification"):
                self.ledger.finish(task["id"], token, "delivered", {"summary": "Done", "verification": "Claimed pass", **extra})

    def test_child_finish_transport_error_keeps_terminal_result_and_parent_wake(self):
        task = self.task()
        token = self.claim(task)
        path = self.fixture.path.parent / "result.json"
        path.write_text(json.dumps({"summary": "Missing asset", "prs": None}), encoding="utf-8")
        await_tasks(self.ledger, self.parent, self.token)
        self.api.create_activity.side_effect = RuntimeError("Linear unavailable")
        result = self.cli("finish", task, token, outcome="blocked", input=path)
        self.assertEqual(result["state"], "blocked")
        self.assertTrue(wake_parent(self.ledger, self.parent))
        self.assertTrue(self.ledger.noted_since(task["id"], "task_progress_not_posted", 0))
        self.assertEqual(self.ledger.connection.execute("SELECT COUNT(*) FROM session_closures").fetchone()[0], 0)

    def test_child_question_transport_error_parks_before_post_and_retains_question(self):
        task = self.task()
        self.api.create_activity.side_effect = RuntimeError("Linear unavailable")
        result = self.cli("await-input", task, self.claim(task), question="Provide seed art")
        self.assertEqual(result["state"], "awaiting_input")
        self.assertEqual(result["checkpoint"]["pending_question"], "Provide seed art")

    def test_local_child_uses_issue_comments_for_questions_progress_and_finish(self):
        self.ledger.ensure_session("local-issue", fixtures.ISSUE, delegation=True)
        self.ledger.connection.execute("UPDATE work_items SET session_id='local-issue' WHERE id=?", (self.parent,))
        task = self.task()
        token = self.claim(task)
        body = self.fixture.path.parent / "progress.txt"
        body.write_text("Inspected nursery", encoding="utf-8")
        self.cli("activity", task, token, type="thought", body_file=body)
        self.cli("await-input", task, token, question="Provide seed art")
        self.ledger.push_inbox(task["id"], "Asset still missing", resume_waiting=True)
        token = self.claim(task)
        self.ledger.pop_inbox(task["id"], token)
        evidence = self.fixture.path.parent / "blocked.json"
        evidence.write_text(json.dumps({"summary": "Missing art"}), encoding="utf-8")
        self.cli("finish", task, token, outcome="blocked", input=evidence)
        self.api.create_activity.assert_not_called()
        self.assertEqual(self.api.create_comment.call_count, 3)

    def test_join_transport_error_returns_parked_result(self):
        self.task()
        self.api.create_activity.side_effect = RuntimeError("Linear unavailable")
        config = SimpleNamespace(enabled_skills=("work",), issue_prefix="FARM")
        with patch("agent.__main__.load_config", return_value=config), patch("agent.__main__.Paths") as paths:
            paths.return_value.ledger = self.fixture.path
            result = self.cli("await-tasks", {"id": self.parent}, self.token)
        self.assertEqual(result["state"], "awaiting_input")
        self.assertTrue(result["checkpoint"]["waiting_tasks"])
