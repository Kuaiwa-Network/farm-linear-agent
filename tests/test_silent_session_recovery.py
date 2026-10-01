"""Own-session recovery uses real routing, temporary ledgers and a fake Linear transport."""
import test_receiver as fixtures
from agent.ledger import Ledger
from agent.receiver import OPEN_WEBHOOK_GRACE
from agent.linear_api import LinearError, SessionCreationRefused


class SilentSessionRecoveryTests(fixtures.ReceiverBase):
    clock = fixtures.DelegationEpisodeReceiverTests.clock
    status_reads = fixtures.DelegationEpisodeReceiverTests.status_reads
    running = fixtures.SilentDelegationReceiverTests.running
    session_state = fixtures.SilentDelegationReceiverTests.session_state
    linear_says = fixtures.SilentDelegationReceiverTests.linear_says
    redelegated = fixtures.SilentDelegationReceiverTests.redelegated
    settle = fixtures.SilentDelegationReceiverTests.settle
    active_jobs = fixtures.SilentDelegationReceiverTests.active_jobs
    waiting_mention = fixtures.SilentDelegationReceiverTests.waiting_mention
    idle_thread = fixtures.SilentDelegationReceiverTests.idle_thread
    labelled = fixtures.BotRoutingReceiverTests.labelled
    delegate = fixtures.BotRoutingReceiverTests.delegate
    conversation_elsewhere = fixtures.BotRoutingReceiverTests.conversation_elsewhere
    paused = fixtures.BotRoutingReceiverTests.paused
    undelegated = fixtures.WithdrawnWorkReceiverTests.undelegated
    waiting_fix = fixtures.WithdrawnWorkReceiverTests.waiting_fix
    messages = fixtures.WithdrawnWorkReceiverTests.messages
    reply_in = fixtures.WithdrawnWorkReceiverTests.reply_in
    claimed_fix_elsewhere = fixtures.BotRoutingReceiverTests.claimed_fix_elsewhere

    def setUp(self):
        super().setUp()
        self.now = 1000.0
        self.threads = {}
        self.settled = None
        self.api.session_state.side_effect = self.session_state
        self.ledger = Ledger(self.db, clock=self.clock)
        self.addCleanup(self.ledger.close)
        self.running()
        self.remote = None
        self.api.create_session_on_issue.side_effect = self.open_remote
        self.api.find_recovery_session.side_effect = lambda *args: self.remote
        self.api.recovery_session_candidate.side_effect = lambda *args: self.remote

    def open_remote(self, issue_id, app_id, marker):
        self.remote = {"id": "recovered-session", "issue": {"id": issue_id}, "appUser": {"id": app_id},
                       "sourceComment": None, "creator": None, "archivedAt": None,
                       "externalLinks": [{"label": "Issue", "url": marker}]}
        return self.remote

    def due(self):
        self.ledger.observe_issue(self.api.fetch_issue.return_value)
        self.now = 1100.0
        self.redelegated()
        self.now += 90

    def process_opened(self):
        self.now += OPEN_WEBHOOK_GRACE
        self.assertTrue(self.receiver.process_one())
        return self.ledger.active_item_for_issue(fixtures.ISSUE)

    def created(self, session="recovered-session", **changes):
        return self.event(agentSession={"id": session, "issue": {"id": fixtures.ISSUE}}, **changes)

    def test_completed_threads_do_not_require_archiving_to_start_one_new_fix(self):
        self.idle_thread()
        self.due()
        self.assertEqual(self.settle(), "opened")
        self.assertEqual(self.active_jobs(), [])
        self.assertFalse(self.receiver.process_one())
        fix = self.process_opened()
        self.assertEqual((fix["skill"], fix["authority"], fix["session_id"]),
                         ("fix", "delegation", "recovered-session"))
        self.assertIsNone(self.settle())
        self.api.create_session_on_issue.assert_called_once()
        self.assertEqual(self.active_jobs(), [fix["id"]])

    def test_waiting_mention_moves_messages_into_fresh_delegation_without_promoting_mention(self):
        chat = self.waiting_mention()
        self.due()
        self.labelled(["Bug", "修改"], fixtures.CHANGE)
        self.assertEqual(self.settle(), "opened")
        fix = self.process_opened()
        self.assertEqual(self.ledger.item(chat["id"])["state"], "cancelled")
        self.assertFalse(self.ledger.session(chat["session_id"])["delegation"])
        self.assertEqual(fix["authority"], "delegation")
        self.assertIn("@FarmBot 这是什么问题？", self.messages(fix))
        self.assertEqual(self.active_jobs(), [fix["id"]])

    def test_stop_then_redelegation_can_open_fresh_thread(self):
        old = self.idle_thread()
        self.due()
        self.assertEqual(self.settle(), "opened")
        fix = self.process_opened()
        self.assertNotEqual(fix["id"], old["id"])
        self.assertEqual(fix["predecessor_id"], old["id"])

    def test_existing_waiting_delegation_stays_in_its_thread(self):
        fix = self.waiting_fix()
        self.due()
        self.assertEqual(self.settle(), "kept")
        self.assertEqual(self.active_jobs(), [fix["id"]])
        self.api.create_session_on_issue.assert_not_called()

    def test_lost_response_is_recovered_after_restart_without_second_mutation(self):
        self.due()
        def lost(*args):
            self.open_remote(*args)
            raise TimeoutError()
        self.api.create_session_on_issue.side_effect = lost
        self.assertEqual(self.settle(), "retry")
        self.running()
        self.now += 15
        self.assertEqual(self.settle(), "opened")
        fix = self.process_opened()
        self.api.create_session_on_issue.assert_called_once()
        self.api.find_recovery_session.assert_called_once()
        self.assertEqual(self.active_jobs(), [fix["id"]])

    def test_lost_response_then_webhook_after_withdrawal_starts_nothing(self):
        self.due()
        def lost(*args):
            self.open_remote(*args)
            raise TimeoutError()
        self.api.create_session_on_issue.side_effect = lost
        self.assertEqual(self.settle(), "retry")
        self.undelegated(["Bug", "修改"], fixtures.CHANGE)
        self.ledger.mark_undelegated(fixtures.ISSUE, self.now)
        self.receive(self.created())
        self.assertTrue(self.receiver.process_one())
        self.assertEqual(self.active_jobs(), [])
        self.api.create_session_on_issue.assert_called_once()

    def test_replies_after_creation_still_follow_normal_routing(self):
        self.due()
        self.assertEqual(self.settle(), "opened")
        fix = self.process_opened()
        self.ledger.cancel(fix["id"], "check complete")
        self.undelegated(["Bug", "修改"], fixtures.CHANGE)
        self.ledger.mark_undelegated(fixtures.ISSUE, self.now)
        self.reply_in("recovered-session", "解释一下")
        self.assertNotIn("创建会话后", self.sent()[-1][1]["body"])

    def test_interrupted_before_request_never_blindly_repeats_creation(self):
        self.due()
        self.api.create_session_on_issue.side_effect = SystemExit()
        with self.assertRaises(SystemExit):
            self.receiver.process_one()
        self.running()
        for delay in (15, 30, 60):
            self.now += delay
            self.assertEqual(self.settle(), "retry")
        self.api.create_session_on_issue.assert_called_once()
        self.assertEqual(self.active_jobs(), [])
        self.assertEqual(self.ledger.episode(fixtures.ISSUE)["state"], "waiting")

    def test_delegate_removed_during_creation_starts_nothing(self):
        self.due()
        def removed(*args):
            session = self.open_remote(*args)
            self.undelegated(["Bug", "修改"], fixtures.CHANGE)
            self.ledger.mark_undelegated(fixtures.ISSUE, self.now)
            return session
        self.api.create_session_on_issue.side_effect = removed
        self.assertEqual(self.settle(), "changed")
        self.process_opened()
        self.assertEqual(self.active_jobs(), [])
        self.assertEqual(self.sent()[-1][1]["type"], "response")

    def test_closed_card_between_creation_and_routing_starts_nothing(self):
        self.due()
        self.assertEqual(self.settle(), "opened")
        self.api.fetch_issue.return_value["status_type"] = "canceled"
        self.process_opened()
        self.assertEqual(self.active_jobs(), [])

    def test_new_episode_invalidates_old_synthetic_creation(self):
        self.due()
        self.assertEqual(self.settle(), "opened")
        self.now += 1
        self.redelegated()
        self.process_opened()
        self.assertEqual(self.active_jobs(), [])

    def test_signed_created_during_mutation_supplies_guidance_and_is_processed_once(self):
        self.due()
        def webhook(*args):
            session = self.open_remote(*args)
            self.assertEqual(self.receive(self.created(guidance="fresh guidance")), (200, "accepted"))
            return session
        self.api.create_session_on_issue.side_effect = webhook
        self.assertEqual(self.settle(), "opened")
        fix = self.process_opened()
        self.assertEqual(self.ledger.session(fix["session_id"])["guidance"], "fresh guidance")
        self.assertEqual(self.receive(self.created()), (200, "duplicate"))
        self.assertFalse(self.receiver.process_one())
        self.assertEqual(self.active_jobs(), [fix["id"]])

    def test_signed_duplicate_before_processing_replaces_synthetic_payload(self):
        self.due()
        self.assertEqual(self.settle(), "opened")
        self.assertEqual(self.receive(self.created(guidance="signed guidance")), (200, "duplicate"))
        self.assertTrue(self.receiver.process_one())
        self.assertEqual(self.ledger.session("recovered-session")["guidance"], "signed guidance")

    def test_late_duplicate_enriches_session_but_never_restarts_work(self):
        self.due()
        self.assertEqual(self.settle(), "opened")
        fix = self.process_opened()
        self.ledger.cancel(fix["id"], "finished check")
        self.assertEqual(self.receive(self.created(guidance="late signed guidance")), (200, "duplicate"))
        self.assertFalse(self.receiver.process_one())
        self.assertEqual(self.active_jobs(), [])
        self.assertEqual(self.ledger.session("recovered-session")["guidance"], "late signed guidance")

    def test_stop_arriving_before_creation_response_cancels_synthetic_event(self):
        self.due()
        def stopped(*args):
            session = self.open_remote(*args)
            event = self.created()
            event["action"] = "prompted"
            event["agentActivity"] = {"id": "stop-opening", "agentSessionId": session["id"],
                                      "signal": "stop", "content": {"type": "prompt"}}
            self.assertEqual(self.receive(event), (200, "stop received"))
            return session
        self.api.create_session_on_issue.side_effect = stopped
        self.assertEqual(self.settle(), "opened")
        self.process_opened()
        self.assertFalse(self.receiver.process_one())
        self.assertEqual(self.active_jobs(), [])
        self.assertEqual(self.receiver.results()[0]["status"], "cancelled")

    def test_created_arriving_during_issue_read_prevents_an_extra_mutation(self):
        self.due()
        card = self.api.fetch_issue.return_value
        def read(*args):
            self.receive(self.created(session="linear-session"))
            self.api.fetch_issue.side_effect = None
            return card
        self.api.fetch_issue.side_effect = read
        self.assertEqual(self.settle(), "changed")
        self.api.create_session_on_issue.assert_not_called()
        self.assertTrue(self.receiver.process_one())
        self.now += 5
        self.assertEqual(self.settle(), "heard")
        self.assertEqual(len(self.active_jobs()), 1)

    def test_wrong_app_response_is_never_queued_or_retried_as_creation(self):
        self.due()
        def wrong(*args):
            return {**self.open_remote(*args), "appUser": {"id": "another-app"}}
        self.api.create_session_on_issue.side_effect = wrong
        self.assertEqual(self.settle(), "retry")
        self.assertEqual(self.receiver.results(), [])
        self.assertEqual(self.active_jobs(), [])
        self.api.find_recovery_session.side_effect = lambda *args: None
        self.now += 15
        self.assertEqual(self.settle(), "retry")
        self.api.create_session_on_issue.assert_called_once()

    def test_explicit_refusal_keeps_manual_recovery_and_is_not_retried(self):
        self.due()
        self.api.create_session_on_issue.side_effect = SessionCreationRefused()
        self.assertEqual(self.settle(), "unseen")
        self.assertIsNone(self.settle())
        self.api.create_session_on_issue.assert_called_once()
        self.assertEqual(self.active_jobs(), [])

    def test_missing_session_never_marks_a_successfully_read_card_unreachable(self):
        self.due()
        self.api.create_session_on_issue.side_effect = LinearError("not_found")
        self.assertEqual(self.settle(), "retry")
        self.assertIsNone(self.ledger.episode(fixtures.ISSUE)["unreachable_since"])
        self.assertEqual(self.ledger.episode(fixtures.ISSUE)["state"], "waiting")

    def test_new_session_defers_changed_skill_until_claimed_worker_withdraws(self):
        old, token = self.claimed_fix_elsewhere()
        self.due()
        self.running("fgui")
        self.labelled(["UI"], fixtures.UI)
        self.assertEqual(self.settle(), "opened")
        self.process_opened()
        self.assertEqual(self.active_jobs(), [old["id"]])
        self.assertEqual(self.ledger.item(old["id"])["withdraw_reason"], "superseded")
        self.assertEqual(self.receiver.results()[-1]["status"], "deferred")
        self.assertEqual(self.ledger.items_for_session("recovered-session"), [])
        with self.assertRaisesRegex(fixtures.LedgerError, "withdraw"):
            self.ledger.await_input(old["id"], token, "A new question must not replace withdrawal")
