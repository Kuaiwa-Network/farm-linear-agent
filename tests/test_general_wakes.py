"""Regression coverage for durable task wakeups and the gameplay join."""
import json
import unittest
from unittest.mock import patch

from agent.foreign_work import _lineage
from agent.ledger import LedgerError
from agent.workflow import await_tasks, client_ready, delegate_task, forward_answer, wake_parent
import test_ledger as fixtures


class GeneralWakeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.LedgerBase()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.ledger = self.fixture.ledger
        self.parent = self.fixture.new_item("work", target=None)["id"]
        self.token = self.ledger.claim(self.parent, worker_id="coordinator")["token"]
        self.save()

    def save(self, **stages):
        self.ledger.checkpoint(self.parent, self.token, {"plan": {"stages": {
            "A": "done", "B": "skipped: no configuration changes", "C": "skipped: no designer data", "D": "done",
            **stages}}})

    def task(self, key="ui", capability="fgui"):
        return delegate_task(self.ledger, self.parent, self.token, {
            "key": key, "capability": capability, **({"scope": {"assets": ["Assets/nursery.prefab"], "validation": "source"}} if capability == "scene" else {}), "instructions": "Use the shared contract."})

    def claim_task(self, task):
        return self.ledger.claim(task["id"], worker_id="presentation")["token"]

    def resume_parent(self):
        self.token = self.ledger.claim(self.parent, worker_id="coordinator")["token"]

    def deliver(self, task):
        self.ledger.finish(task["id"], self.claim_task(task), "delivered", {
            "summary": "Existing assets satisfy the contract", "verification": "Checked required presentation",
            "no_change": "No presentation delta"})

    def test_sibling_launch_does_not_repeat_an_unchanged_question_wake(self):
        ui, scene = self.task(), self.task("scene", "scene")
        self.ledger.pin_staged_client(scene["id"], scene["generation"], fixtures.PIN, branch="farmbot/farm-1")
        self.ledger.await_input(ui["id"], self.claim_task(ui), "Which icon?")
        await_tasks(self.ledger, self.parent, self.token)
        self.assertTrue(wake_parent(self.ledger, self.parent))
        self.resume_parent()
        await_tasks(self.ledger, self.parent, self.token)
        scene_token = self.claim_task(scene)
        self.assertFalse(wake_parent(self.ledger, self.parent))
        self.ledger.await_resource(scene["id"], scene_token, "unity_slot", "interactive",
                                   skill=fixtures.SKILLS["scene"], commit_sha="b" * 40)
        self.assertFalse(wake_parent(self.ledger, self.parent))
        self.ledger.close()
        self.ledger = self.fixture.open_ledger()
        self.assertFalse(wake_parent(self.ledger, self.parent))

    def test_answering_one_task_does_not_repeat_its_siblings_old_question(self):
        ui, scene = self.task(), self.task("scene", "scene")
        for task in (ui, scene):
            self.ledger.await_input(task["id"], self.claim_task(task), "Which icon?")
        await_tasks(self.ledger, self.parent, self.token)
        self.assertTrue(wake_parent(self.ledger, self.parent))
        self.resume_parent()
        self.ledger.push_inbox(self.parent, "Use icon A for UI", author=fixtures.DESIGNER)
        message_id = self.ledger.issue_context(self.parent)["session_messages"][-1]["id"]
        self.ledger.pop_inbox(self.parent, self.token)
        forward_answer(self.ledger, self.parent, self.token, "ui", message_id)
        await_tasks(self.ledger, self.parent, self.token)
        self.assertFalse(wake_parent(self.ledger, self.parent))

    def test_new_sibling_question_wakes_even_while_an_old_question_waits(self):
        ui, scene = self.task(), self.task("scene", "scene")
        self.ledger.await_input(ui["id"], self.claim_task(ui), "Which icon?")
        await_tasks(self.ledger, self.parent, self.token)
        self.assertTrue(wake_parent(self.ledger, self.parent))
        self.resume_parent()
        await_tasks(self.ledger, self.parent, self.token)
        self.ledger.await_input(scene["id"], self.claim_task(scene), "Which prefab?")
        self.assertTrue(wake_parent(self.ledger, self.parent))

    def test_later_child_question_wakes_a_server_human_wait_without_answering_it(self):
        ui, scene = self.task(), self.task("scene", "scene")
        self.save(D="pending")
        self.ledger.connection.execute("UPDATE work_items SET root_repo='farm-hive' WHERE id=?", (self.parent,))
        self.ledger.pin_staged_client(scene["id"], scene["generation"], fixtures.PIN, branch="farmbot/farm-1")
        scene_token = self.claim_task(scene)
        self.ledger.await_resource(scene["id"], scene_token, "unity_slot", "interactive",
                                   skill=fixtures.SKILLS["scene"], commit_sha="b" * 40)
        reservation = self.ledger.active_reservation(scene["id"])
        self.ledger.await_input(self.parent, self.token, "Server Go cache is missing; UI continues", reason="waiting")
        self.assertNotIn("waiting_tasks", self.ledger.item(self.parent)["checkpoint"])
        self.ledger.await_input(ui["id"], self.claim_task(ui), "Which badge asset should UI use?")
        self.assertTrue(wake_parent(self.ledger, self.parent))
        parent = self.ledger.item(self.parent)
        self.assertEqual(parent["state"], "queued")
        self.assertEqual(parent["checkpoint"]["pending_question"], "Server Go cache is missing; UI continues")
        self.assertEqual(parent["checkpoint"]["pending_reason"], "waiting")
        self.assertEqual(parent["checkpoint"]["plan"]["stages"]["D"], "pending")
        self.assertFalse(parent["resume_authorized"])
        self.assertEqual(self.ledger.item(ui["id"])["state"], "awaiting_input")
        self.assertEqual(self.ledger.active_reservation(scene["id"]), reservation)

    def test_ordinary_human_wait_deduplicates_questions_and_accepts_a_new_partial_answer_round(self):
        task = self.task()
        self.ledger.await_input(self.parent, self.token, "Prepare the server cache", reason="waiting")
        self.ledger.await_input(task["id"], self.claim_task(task), "Which art and dimensions?")
        self.assertTrue(wake_parent(self.ledger, self.parent))
        self.resume_parent()
        self.ledger.await_input(self.parent, self.token, "Cache, art and dimensions still required")
        for _ in range(3):
            self.assertFalse(wake_parent(self.ledger, self.parent))
        self.ledger.close()
        self.ledger = self.fixture.open_ledger()
        self.assertFalse(wake_parent(self.ledger, self.parent))
        self.ledger.push_inbox(self.parent, "Use icon A; dimensions still undecided.", author=fixtures.DESIGNER,
                               resume_waiting=True)
        message_id = self.ledger.issue_context(self.parent)["session_messages"][-1]["id"]
        self.resume_parent()
        self.ledger.pop_inbox(self.parent, self.token)
        forward_answer(self.ledger, self.parent, self.token, "ui", message_id)
        self.ledger.await_input(self.parent, self.token, "Server cache remains unavailable", reason="waiting")
        task_token = self.claim_task(task)
        self.ledger.pop_inbox(task["id"], task_token)
        self.ledger.await_input(task["id"], task_token, "Which art and dimensions?")
        self.assertTrue(wake_parent(self.ledger, self.parent))
        self.resume_parent()
        self.ledger.await_input(self.parent, self.token, "Cache and dimensions remain unanswered")
        self.assertFalse(wake_parent(self.ledger, self.parent))

    def test_task_failure_wakes_an_ordinary_human_wait_once(self):
        task = self.task()
        self.ledger.await_input(self.parent, self.token, "Prepare server tooling")
        self.claim_task(task)
        self.ledger.fail(task["id"], "UI publisher is unavailable")
        self.assertTrue(wake_parent(self.ledger, self.parent))
        self.resume_parent()
        self.ledger.await_input(self.parent, self.token, "Server tooling and UI publisher remain missing")
        self.ledger.record_cleanup(task["id"], {}, done=True)
        self.assertFalse(wake_parent(self.ledger, self.parent))

    def test_certified_completion_wakes_an_ordinary_human_wait_without_dropping_its_question(self):
        task = self.task()
        self.ledger.await_input(self.parent, self.token, "Which server behavior is required?")
        self.deliver(task)
        self.assertFalse(wake_parent(self.ledger, self.parent))
        self.ledger.record_cleanup(task["id"], {}, done=True)
        self.assertTrue(wake_parent(self.ledger, self.parent))
        self.assertEqual(self.ledger.item(self.parent)["checkpoint"]["pending_question"],
                         "Which server behavior is required?")
        self.resume_parent()
        self.ledger.await_input(self.parent, self.token, "Which server behavior is required?")
        self.assertFalse(wake_parent(self.ledger, self.parent))

    def test_withdrawal_defers_an_ordinary_wait_wake_until_delegation_returns(self):
        ui, scene = self.task(), self.task("scene", "scene")
        self.claim_task(scene)
        self.ledger.await_input(self.parent, self.token, "Server cache remains unavailable")
        self.ledger.await_input(ui["id"], self.claim_task(ui), "Which badge?")
        self.ledger.flag_withdrawal(self.parent, "undelegated", self.fixture.now + 1200)
        self.assertFalse(wake_parent(self.ledger, self.parent))
        self.assertEqual(self.ledger.item(self.parent)["state"], "awaiting_input")
        self.ledger.clear_withdrawals(fixtures.ISSUE)
        self.assertTrue(wake_parent(self.ledger, self.parent))

    def test_parent_resource_hold_defers_a_coordination_wake(self):
        task = self.task()
        self.ledger.await_input(self.parent, self.token, "Server input still needed")
        self.ledger.await_input(task["id"], self.claim_task(task), "Which art?")
        with patch.object(self.ledger, "active_reservation", return_value={"state": "cancel_requested"}):
            self.assertFalse(wake_parent(self.ledger, self.parent))
        self.assertEqual(self.ledger.item(self.parent)["state"], "awaiting_input")
        self.assertTrue(wake_parent(self.ledger, self.parent))

    def test_already_certified_tasks_cannot_park_the_parent_again(self):
        task = self.task()
        self.deliver(task)
        self.ledger.record_cleanup(task["id"], {}, done=True)
        with self.assertRaisesRegex(LedgerError, "already delivered"):
            await_tasks(self.ledger, self.parent, self.token)
        self.assertEqual(self.ledger.item(self.parent)["state"], "running")
        self.ledger.renew(self.parent, self.token)

    def test_completion_wakes_only_after_certified_teardown_and_once_after_restart(self):
        task = self.task()
        self.deliver(task)
        await_tasks(self.ledger, self.parent, self.token)
        self.assertFalse(wake_parent(self.ledger, self.parent))
        self.ledger.record_cleanup(task["id"], {}, done=True)
        self.assertTrue(wake_parent(self.ledger, self.parent))
        # Also handle a stale parked checkpoint from the old controller version.
        checkpoint = self.ledger.item(self.parent)["checkpoint"]
        checkpoint["waiting_tasks"] = True
        self.ledger._set_state(self.parent, "awaiting_input", "old controller wait", checkpoint=json.dumps(checkpoint))
        self.ledger.close()
        self.ledger = self.fixture.open_ledger()
        self.assertFalse(wake_parent(self.ledger, self.parent))

    def test_missing_predecessor_does_not_drop_the_parent_or_sibling_tasks(self):
        ui, scene = self.task(), self.task("scene", "scene")
        self.ledger.connection.execute("UPDATE work_items SET predecessor_id='unreadable' WHERE id=?", (ui["id"],))
        self.assertEqual({row["id"] for row in _lineage(self.ledger, ui["id"])},
                         {self.parent, ui["id"], scene["id"]})

    def test_client_waits_for_explicitly_settled_configuration_stages(self):
        task = self.task()
        self.deliver(task)
        self.ledger.record_cleanup(task["id"], {}, done=True)
        for server in ("done", "skipped: no server changes"):
            for stage in ("B", "C"):
                for value in (None, "pending", "unresolved", "skipped:", "skipped:   ", False):
                    with self.subTest(server=server, stage=stage, value=value):
                        self.save(D=server, **{stage: value})
                        with self.assertRaisesRegex(LedgerError, "configuration"):
                            client_ready(self.ledger, self.ledger._row(self.parent))
                self.save(D=server)
                checkpoint = self.ledger.item(self.parent)["checkpoint"]
                checkpoint["plan"]["stages"].pop(stage)
                self.ledger.checkpoint(self.parent, self.token, checkpoint)
                with self.subTest(server=server, stage=stage, value="missing"), \
                        self.assertRaisesRegex(LedgerError, "configuration"):
                    client_ready(self.ledger, self.ledger._row(self.parent))

    def test_client_accepts_done_or_reasoned_configuration_skips(self):
        task = self.task()
        self.deliver(task)
        self.ledger.record_cleanup(task["id"], {}, done=True)
        for stages in ({"B": "done", "C": "done"},
                       {"B": "skipped: no schema delta", "C": "skipped: no data required"}):
            self.save(**stages)
            client_ready(self.ledger, self.ledger._row(self.parent))


if __name__ == "__main__":
    unittest.main()
