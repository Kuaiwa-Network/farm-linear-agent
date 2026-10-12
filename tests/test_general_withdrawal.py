"""General-job withdrawal preserves claimed workers and the deciding read's fences."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent.ledger import LedgerError, StaleRouting
from agent.lifecycle import Lifecycle, UNDELEGATED_STATES
from agent.workflow import await_tasks, delegate_task
import test_ledger as ledger_fixtures
import test_scheduler as scheduler_fixtures
from test_receiver import APP


HANDOFF = {"facts": [], "hypotheses": [], "checks": [], "repositories": [], "next_actions": ["continue"]}


class GeneralWithdrawalTests(unittest.TestCase):
    def setUp(self):
        self.fixture = scheduler_fixtures.SchedulerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.ledger, self.scheduler, self.launcher = self.fixture.ledger, self.fixture.scheduler, self.fixture.launcher
        self.parent = self.fixture.item(skill="work", delegate_id=APP)["id"]
        self.ledger.set_worker(self.parent, 501, "h", lease_seconds=2700)
        self.token = self.ledger.claim(self.parent, worker_id="coordinator")["token"]
        self.ledger.checkpoint(self.parent, self.token, {"plan": {"stages": {"A": "done"}}, "handoff": HANDOFF})
        self.snapshot = {"id": ledger_fixtures.ISSUE, "status": "Todo", "status_type": "unstarted", "archived": False,
                         "delegate_id": None, "updated_at": "2026-09-21T00:00:00Z"}
        api = SimpleNamespace(app_user_id=APP, issue_status=lambda _: self.snapshot)
        self.lifecycle = Lifecycle(self.ledger, api, self.scheduler, clock=lambda: self.fixture.now)

    def task(self, key, state="queued"):
        task = delegate_task(self.ledger, self.parent, self.token,
                             {"key": key, "capability": "scene", "instructions": "Use the shared spec; ask for missing art."})
        token = None
        if state != "queued":
            self.ledger.set_worker(task["id"], 502, "h", lease_seconds=2700)
            token = self.ledger.claim(task["id"], worker_id=key)["token"]
        if state == "awaiting_input":
            self.ledger.await_input(task["id"], token, "Please provide the missing icon.")
        return task["id"], token

    def confirm(self):
        self.fixture.now += 1
        self.lifecycle.refresh(ledger_fixtures.ISSUE)
        self.fixture.now += 60
        self.lifecycle.refresh(ledger_fixtures.ISSUE)

    def test_parked_coordinator_gives_running_children_grace(self):
        running, token = self.task("ui", "running")
        queued, _ = self.task("scene")
        await_tasks(self.ledger, self.parent, self.token)
        self.confirm()
        self.assertEqual([self.ledger.item(i)["state"] for i in (self.parent, running, queued)],
                         ["awaiting_input", "running", "queued"])
        deadlines = [self.ledger.item(i)["withdraw_deadline"] for i in (self.parent, running, queued)]
        self.assertEqual(deadlines, [self.fixture.now + 1200] * 3)
        self.assertEqual(self.launcher.stopped, [])
        self.ledger.checkpoint(running, token, {"plan": {"stages": {"A": "done"}}, "handoff": HANDOFF})
        with self.assertRaisesRegex(LedgerError, "delegation withdrawn"):
            self.ledger.await_input(running, token, "Another question")
        self.assertEqual(self.fixture.api.activities, [])

    def test_quick_redelegation_preserves_queued_and_human_waiting_tasks(self):
        queued, _ = self.task("scene")
        waiting, _ = self.task("ui", "awaiting_input")
        self.confirm()
        self.assertEqual([self.ledger.item(i)["state"] for i in (self.parent, queued, waiting)],
                         ["running", "queued", "awaiting_input"])
        self.snapshot["delegate_id"] = APP
        self.fixture.now += 1
        self.lifecycle.refresh(ledger_fixtures.ISSUE)
        for item_id in (self.parent, queued, waiting):
            self.assertIsNone(self.ledger.item(item_id)["withdraw_deadline"])
        self.assertEqual(self.ledger.issue_context(waiting)["pending_question"], "Please provide the missing icon.")
        self.ledger.claim(queued, worker_id="scene")
        self.ledger.renew(self.parent, self.token)
        self.assertEqual(self.launcher.stopped, [])

    def test_subsequent_reads_keep_the_original_group_grace(self):
        running, _ = self.task("ui", "running")
        waiting, _ = self.task("scene", "awaiting_input")
        await_tasks(self.ledger, self.parent, self.token)
        self.confirm()
        original = self.ledger.item(self.parent)["withdraw_deadline"]
        self.fixture.now += 120
        self.lifecycle.refresh(ledger_fixtures.ISSUE)
        self.assertEqual([self.ledger.item(i)["withdraw_deadline"] for i in (self.parent, running, waiting)], [original] * 3)
        self.assertEqual(self.ledger.item(waiting)["state"], "awaiting_input")
        self.assertEqual(self.launcher.stopped, [])

    def test_parked_coordinator_expires_the_group_and_redelegation_clears_expiry(self):
        running, _ = self.task("ui", "running")
        await_tasks(self.ledger, self.parent, self.token)
        self.confirm()
        self.fixture.now = self.ledger.item(self.parent)["withdraw_deadline"]
        self.assertIn(self.parent, [r["id"] for r in self.ledger.expired_withdrawals()])
        self.snapshot["delegate_id"] = APP
        self.lifecycle.refresh(ledger_fixtures.ISSUE)
        self.assertIsNone(self.ledger.expire_withdrawal(self.parent))
        self.assertEqual(self.ledger.item(running)["state"], "running")

    def test_grace_deadline_cancels_a_parked_coordinator_and_its_tasks(self):
        running, _ = self.task("ui", "running")
        queued, _ = self.task("scene")
        await_tasks(self.ledger, self.parent, self.token)
        self.confirm()
        self.fixture.now = self.ledger.item(self.parent)["withdraw_deadline"]
        self.assertEqual(self.ledger.expire_withdrawal(self.parent)["state"], "cancelled")
        self.assertEqual([self.ledger.item(i)["state"] for i in (running, queued)], ["cancelled", "cancelled"])

    def test_deciding_read_does_not_cancel_or_flag_a_later_child(self):
        old, _ = self.task("ui")
        before = self.fixture.now + 1
        self.fixture.now = before
        later, _ = self.task("scene")
        await_tasks(self.ledger, self.parent, self.token)
        self.lifecycle._withdraw(ledger_fixtures.ISSUE, before, "undelegated")
        self.assertEqual([self.ledger.item(i)["state"] for i in (self.parent, old, later)],
                         ["awaiting_input", "queued", "queued"])
        self.assertEqual(self.launcher.stopped, [])
        self.fixture.now += 1
        self.lifecycle._withdraw(ledger_fixtures.ISSUE, self.fixture.now, "undelegated")
        self.assertEqual([self.ledger.item(i)["state"] for i in (self.parent, old, later)], ["cancelled"] * 3)

    def test_read_guards_do_not_cascade_into_a_child_with_other_authority(self):
        child, _ = self.task("scene", "running")
        self.ledger.connection.execute("UPDATE work_items SET authority='operator' WHERE id=?", (child,))
        before = self.fixture.now + 1
        with self.assertRaises(StaleRouting):
            self.ledger.flag_withdrawal(self.parent, "undelegated", before + 1200,
                                        authority="delegation", created_before=before)
        self.assertIsNone(self.ledger.cancel(self.parent, "undelegated", authority="delegation", created_before=before))
        self.assertEqual(self.ledger.item(child)["state"], "running")
        self.assertIsNone(self.ledger.item(child)["withdraw_deadline"])

    def test_read_cutoff_protects_a_later_blocked_child_from_the_cascade(self):
        before = self.fixture.now + 1
        self.fixture.now = before
        child, token = self.task("scene", "running")
        self.ledger.finish(child, token, "blocked", {"summary": "missing art"})
        await_tasks(self.ledger, self.parent, self.token)
        self.lifecycle._withdraw(ledger_fixtures.ISSUE, before, "undelegated")
        self.assertEqual(self.ledger.item(child)["state"], "blocked")
        self.assertEqual(self.ledger.item(self.parent)["state"], "awaiting_input")
        self.assertEqual(self.launcher.stopped, [])

    def test_claim_race_between_flag_and_guarded_cancel_keeps_the_worker(self):
        child, _ = self.task("scene")
        await_tasks(self.ledger, self.parent, self.token)
        flag = self.ledger.flag_withdrawal

        def flag_then_claim(*args, **kwargs):
            try:
                return flag(*args, **kwargs)
            except StaleRouting:
                self.ledger.claim(child, worker_id="late-claim")
                raise

        self.fixture.now += 1
        with patch.object(self.ledger, "flag_withdrawal", side_effect=flag_then_claim):
            self.lifecycle._withdraw(ledger_fixtures.ISSUE, self.fixture.now, "undelegated")
        self.assertEqual(self.ledger.item(child)["state"], "running")
        self.assertEqual(self.ledger.item(self.parent)["state"], "awaiting_input")
        self.assertEqual(self.launcher.stopped, [])

    def test_voluntary_coordinator_withdraw_waits_for_running_task(self):
        child, child_token = self.task("ui", "running")
        queued, _ = self.task("scene")
        view, reason = self.ledger.withdraw(self.parent, self.token, delegated=False, closed=False, grace_seconds=90)
        self.assertEqual((view["state"], reason, view["withdraw_deadline"]), ("awaiting_input", "undelegated", self.fixture.now + 90))
        self.assertEqual(self.ledger.item(queued)["state"], "queued")
        self.assertEqual(self.ledger.expired_withdrawals(), [])
        self.ledger.checkpoint(child, child_token, {"plan": {"stages": {"A": "done"}}, "handoff": HANDOFF})
        self.ledger.withdraw(child, child_token, delegated=False, closed=False)
        self.assertIn(self.parent, [r["id"] for r in self.ledger.expired_withdrawals()])
        self.assertEqual(self.ledger.expire_withdrawal(self.parent)["state"], "cancelled")

    def test_redelegation_resumes_coordinator_that_saved_progress_during_group_grace(self):
        child, child_token = self.task("ui", "running")
        self.ledger.withdraw(self.parent, self.token, delegated=False, closed=False)
        self.snapshot["delegate_id"] = APP
        self.lifecycle.refresh(ledger_fixtures.ISSUE)
        self.assertEqual(self.ledger.item(self.parent)["state"], "queued")
        self.assertEqual(self.ledger.item(child)["state"], "running")
        self.assertIsNone(self.ledger.item(child)["withdraw_deadline"])
        self.ledger.claim(self.parent, worker_id="resumed-coordinator")
        self.ledger.renew(child, child_token)

    def test_later_human_wait_is_not_resumed_by_a_past_withdrawal_park(self):
        self.task("ui", "running")
        self.ledger.withdraw(self.parent, self.token, delegated=False, closed=False)
        self.ledger.clear_withdrawals(ledger_fixtures.ISSUE)
        token = self.ledger.claim(self.parent, worker_id="resumed-coordinator")["token"]
        self.ledger.await_input(self.parent, token, "Need gameplay design answer.")
        self.fixture.now += 1
        self.ledger.flag_withdrawal(self.parent, "undelegated", self.fixture.now + 1200)
        self.ledger.clear_withdrawals(ledger_fixtures.ISSUE)
        self.assertEqual(self.ledger.item(self.parent)["state"], "awaiting_input")
        self.assertEqual(self.ledger.issue_context(self.parent)["pending_question"], "Need gameplay design answer.")

    def test_explicit_stop_still_cancels_and_signals_the_entire_owned_job(self):
        child, _ = self.task("scene", "running")
        self.ledger.connection.execute("UPDATE work_items SET authority='operator',created_at=? WHERE id=?",
                                       (self.fixture.now + 1, child))
        await_tasks(self.ledger, self.parent, self.token)
        self.scheduler.stop(self.parent, "Linear stop")
        self.assertEqual([self.ledger.item(i)["state"] for i in (self.parent, child)], ["cancelled", "cancelled"])
        self.assertIn(child, self.launcher.stopped)
        self.assertIn(self.parent, self.launcher.stopped)

    def test_guarded_parked_cancel_refuses_running_child(self):
        child, _ = self.task("scene", "running")
        await_tasks(self.ledger, self.parent, self.token)
        self.assertIsNone(self.ledger.cancel(self.parent, "undelegated", states=UNDELEGATED_STATES))
        self.assertEqual(self.ledger.item(child)["state"], "running")
