"""Heartbeat retries crossing general-job withdrawal never ask a person for input."""
import unittest
from types import SimpleNamespace

from agent.session_progress import SessionProgress
from agent.withdrawal import HEARTBEAT_WITHDRAWING
from agent.workflow import await_tasks, delegate_task, wake_parent
import test_ledger as fixtures


HANDOFF = {"facts": [], "hypotheses": [], "checks": [], "repositories": [], "next_actions": ["continue"]}


class GeneralWithdrawalProgressTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.LedgerBase()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.ledger = self.fixture.ledger
        self.parent = self.fixture.new_item("work", target=None)["id"]
        self.ledger.set_worker(self.parent, 501, "h", lease_seconds=2700)
        self.token = self.ledger.claim(self.parent, worker_id="coordinator")["token"]
        self.ledger.checkpoint(self.parent, self.token, {"plan": {"stages": {"A": "done"}}, "handoff": HANDOFF})
        self.child_tokens = {}
        self.child = self.task("scene")
        self.sent = []
        self.api = SimpleNamespace(create_activity=self.send)
        self.progress = SessionProgress(self.ledger, self.api, interval=20, retry_seconds=5)

    def task(self, key, *, waiting=False):
        task = delegate_task(self.ledger, self.parent, self.token,
                             {"key": key, "capability": "scene", "scope": {"assets": ["Assets/nursery.prefab"], "validation": "source"}, "instructions": "Follow the shared spec."})
        self.ledger.set_worker(task["id"], 502, "h", lease_seconds=2700)
        token = self.ledger.claim(task["id"], worker_id=key)["token"]
        self.child_tokens[task['id']] = token
        if waiting:
            self.ledger.await_input(task["id"], token, "Where is the missing asset?")
        return task["id"]

    def send(self, session, content, activity_id=None):
        self.sent.append((session, content, activity_id))
        return {"success": True}

    def withdraw(self):
        return self.ledger.withdraw(self.parent, self.token, delegated=False, closed=False)

    def test_reserved_heartbeat_becomes_withdrawal_thought(self):
        self.progress.queue_current(self.parent)
        self.withdraw()
        self.assertTrue(self.progress.tick())
        self.assertEqual([entry[1] for entry in self.sent], [{"type": "thought", "body": HEARTBEAT_WITHDRAWING}])
        self.assertFalse(self.progress.tick())

    def test_parked_parent_reports_live_child_without_renewing_either_claim(self):
        child_lease = self.ledger.item(self.child)['lease_expires_at']
        await_tasks(self.ledger, self.parent, self.token)
        self.fixture.now += 20
        self.assertTrue(self.progress.tick())
        self.assertEqual(self.sent[-1][1]['type'], 'thought')
        self.assertIn('scene', self.sent[-1][1]['body'])
        self.assertIn('正在执行', self.sent[-1][1]['body'])
        self.assertEqual(self.ledger.item(self.child)['lease_expires_at'], child_lease)
        self.assertEqual(self.ledger.item(self.parent)['state'], 'awaiting_input')

    def test_child_infrastructure_failure_is_reported_without_closing_parent(self):
        from agent.resource_recovery import RecoveryController, RecoveryStore
        await_tasks(self.ledger, self.parent, self.token)
        self.ledger._set_state(self.child, 'awaiting_resource', 'fixture', token=None,
                               lease_expires_at=None, worker_pid=None)
        RecoveryStore(self.ledger)._fail_job(self.child, 'Unity recovery exhausted')
        controller = RecoveryController(self.ledger, None, host='h', evidence_root=self.fixture.path.parent,
                                        inspect=lambda slot: {}, repair=lambda r: None,
                                        fence=lambda r: None, api=self.api)
        controller.report()
        self.assertEqual(self.sent[-1][1]['type'], 'thought')
        self.assertIn('scene', self.sent[-1][1]['body'])
        self.assertIn('Unity recovery exhausted', self.sent[-1][1]['body'])
        self.assertEqual(self.ledger.item(self.parent)['state'], 'awaiting_input')

    def test_child_progress_preserves_parent_question_and_stops_after_child_waits(self):
        self.ledger.await_input(self.parent, self.token, 'Which environment?')
        self.fixture.now += 20
        self.assertTrue(self.progress.tick())
        self.assertEqual(self.sent[-1][1]['type'], 'elicitation')
        self.assertIn('Which environment?', self.sent[-1][1]['body'])
        child_token = self.child_tokens[self.child]
        self.ledger.await_input(self.child, child_token, 'Missing asset?')
        self.fixture.now += 20
        self.assertFalse(self.progress.tick())

    def test_inflight_heartbeat_is_corrected_with_a_thought_after_withdrawal_park(self):
        self.progress.queue_current(self.parent)

        def withdraw_during_send(session, content, activity_id=None):
            self.send(session, content, activity_id)
            if len(self.sent) == 1:
                self.withdraw()

        self.api.create_activity = withdraw_during_send
        self.assertTrue(self.progress.tick())
        self.assertEqual([entry[1]["type"] for entry in self.sent], ["thought", "thought"])
        self.assertEqual(self.sent[-1][1]["body"], HEARTBEAT_WITHDRAWING)
        self.assertEqual(self.ledger.item(self.parent)["state"], "awaiting_input")

    def test_reserved_old_question_is_not_retried_when_the_same_state_is_withdrawing(self):
        self.ledger.await_input(self.parent, self.token, "Which environment?")
        self.progress.queue_current(self.parent)
        self.ledger.push_inbox(self.parent, "Use staging.", author=fixtures.DESIGNER, resume_waiting=True)
        self.token = self.ledger.claim(self.parent, worker_id="resumed-coordinator")["token"]
        self.ledger.pop_inbox(self.parent, self.token)
        view, _ = self.withdraw()
        self.assertNotIn("pending_question", view["checkpoint"])
        self.assertTrue(self.progress.tick())
        self.assertEqual([entry[1] for entry in self.sent], [{"type": "thought", "body": HEARTBEAT_WITHDRAWING}])

    def test_question_already_inflight_is_corrected_after_coordinator_withdraws(self):
        self.ledger.await_input(self.parent, self.token, "Which environment?")
        self.progress.queue_current(self.parent)

        def resume_and_withdraw_during_send(session, content, activity_id=None):
            if not self.sent:
                self.ledger.push_inbox(self.parent, "Use staging.", author=fixtures.DESIGNER, resume_waiting=True)
                self.token = self.ledger.claim(self.parent, worker_id="resumed-coordinator")["token"]
                self.ledger.pop_inbox(self.parent, self.token)
                self.withdraw()
            self.send(session, content, activity_id)

        self.api.create_activity = resume_and_withdraw_during_send
        self.assertTrue(self.progress.tick())
        self.assertEqual([entry[1]["type"] for entry in self.sent], ["elicitation", "thought"])
        self.assertEqual(self.sent[-1][1]["body"], HEARTBEAT_WITHDRAWING)
        self.fixture.now += 20
        self.assertFalse(self.progress.tick())

    def test_flagging_an_existing_task_wait_rechecks_the_reserved_content(self):
        await_tasks(self.ledger, self.parent, self.token)
        self.progress.queue_current(self.parent)
        self.ledger.flag_withdrawal(self.parent, "undelegated", self.fixture.now + 1200)
        self.assertTrue(self.progress.tick())
        self.assertEqual(self.sent[-1][1], {"type": "thought", "body": HEARTBEAT_WITHDRAWING})

    def test_clearing_a_flag_restores_a_genuine_human_question(self):
        self.ledger.await_input(self.parent, self.token, "Which environment?")
        self.ledger.flag_withdrawal(self.parent, "undelegated", self.fixture.now + 1200)
        self.progress.queue_current(self.parent)
        self.ledger.clear_withdrawals(fixtures.ISSUE)
        self.assertTrue(self.progress.tick())
        self.assertEqual(self.sent[-1][1], {"type": "elicitation", "body": "Which environment?"})

    def test_failed_withdrawal_correction_survives_restart_without_a_question(self):
        self.progress.queue_current(self.parent)

        def unavailable(session, content, activity_id=None):
            self.send(session, content, activity_id)
            if len(self.sent) == 1:
                self.withdraw()
            else:
                raise OSError("temporary outage")

        self.api.create_activity = unavailable
        self.assertTrue(self.progress.tick())
        self.api.create_activity = self.send
        self.fixture.now += 5
        restarted = SessionProgress(self.fixture.open_ledger(), self.api, interval=20, retry_seconds=5)
        self.assertTrue(restarted.tick())
        self.assertEqual([entry[1]["type"] for entry in self.sent], ["thought", "thought", "thought"])
        self.assertEqual(self.sent[-1][1]["body"], HEARTBEAT_WITHDRAWING)
        self.assertEqual(self.sent[-1][2], self.sent[-2][2])

    def test_withdrawal_park_does_not_relaunch_for_an_existing_child_question(self):
        self.task("ui", waiting=True)
        await_tasks(self.ledger, self.parent, self.token)
        self.ledger.push_inbox(self.parent, "Save progress.", author=fixtures.DESIGNER, resume_waiting=True)
        self.token = self.ledger.claim(self.parent, worker_id="resumed-coordinator")["token"]
        self.ledger.pop_inbox(self.parent, self.token)
        view, _ = self.withdraw()
        self.assertNotIn("waiting_tasks", view["checkpoint"])
        for _ in range(3):
            self.assertFalse(wake_parent(self.ledger, self.parent))
            self.assertEqual(self.ledger.item(self.parent)["state"], "awaiting_input")
