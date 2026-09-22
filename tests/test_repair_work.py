"""Conversation requests change execution mode; the ledger controls authority."""
from types import SimpleNamespace
from pathlib import Path

from agent.ledger import LedgerError
from agent.__main__ import parser, run
from test_ledger import LedgerBase, ISSUE, OTHER, PIN, SESSION, issue
from test_receiver import ReceiverBase, APP


class RepairWorkTests(LedgerBase):
    def conversation(self, *, delegated=True, session=SESSION):
        self.ledger.observe_issue(issue(delegate_id=APP, labels=[]))
        self.ledger.ensure_session(session, ISSUE, delegated)
        self.ledger.set_session_target(session, PIN)
        chat = self.ledger.create_work_item(issue_id=ISSUE, session_id=session, skill="chat")
        self.ledger.push_inbox(chat["id"], "修复，保留现有排序规则")
        token = self.ledger.claim(chat["id"], worker_id="conversation")["token"]
        return chat, token

    def request(self, chat, token, message_id=None):
        if message_id is None:
            message_id = self.ledger.issue_context(chat["id"])["session_messages"][-1]["id"]
        return self.ledger.request_repair(chat["id"], token, message_id, APP,
                                          "User requested repair. Investigated the sorting view; verify its model.")

    def test_first_repair_preserves_pin_messages_findings_and_retires_read_only_claim(self):
        chat, token = self.conversation()
        self.ledger.pop_inbox(chat["id"], token)
        self.ledger.await_input(chat["id"], token, "排序是否需要改变？")
        self.ledger.push_inbox(chat["id"], "不改变排序，修复显示", resume_waiting=True)
        token = self.ledger.claim(chat["id"], worker_id="conversation-2")["token"]
        fix = self.request(chat, token)
        self.assertEqual((fix["skill"], fix["state"], fix["session_id"]), ("fix", "queued", SESSION))
        self.assertEqual(fix["target"], PIN)
        self.assertEqual(self.ledger.item(chat["id"])["state"], "delivered")
        context = self.ledger.issue_context(fix["id"])
        self.assertEqual([m["body"] for m in context["session_messages"]],
                         ["修复，保留现有排序规则", "不改变排序，修复显示"])
        history = next(h for h in context["conversation_history"] if h["item_id"] == chat["id"])
        self.assertIn("sorting view", history["summary"])
        self.assertEqual(history["pending_question"], "排序是否需要改变？")
        with self.assertRaises(LedgerError):
            self.request(chat, token)
        self.assertEqual(len(self.ledger.queue()), 1)

    def test_mention_can_use_recorded_delegation_without_redelegating(self):
        chat, token = self.conversation(delegated=False, session="mention")
        self.ledger.ensure_session("delegated", ISSUE, True)
        self.ledger.set_session_target("delegated", PIN)
        fix = self.request(chat, token)
        self.assertEqual(fix["session_id"], "delegated")
        self.assertEqual(fix["target"], PIN)
        self.assertEqual(self.ledger.active_item_for_session("mention")["id"], fix["id"],
                         "replies and Stop in the original conversation must still reach the repair")

    def test_fresh_delegate_field_alone_does_not_grant_write_authority(self):
        chat, token = self.conversation(delegated=False)
        with self.assertRaisesRegex(LedgerError, "delegation session"):
            self.request(chat, token)
        self.assertEqual(self.ledger.item(chat["id"])["state"], "running")

    def test_foreign_delegation_session_cannot_authorize_this_issue(self):
        chat, token = self.conversation(delegated=False)
        self.ledger.observe_issue(issue(id=OTHER, delegate_id=APP))
        self.ledger.ensure_session("foreign", OTHER, True)
        with self.assertRaisesRegex(LedgerError, "delegation session"):
            self.request(chat, token)

    def test_closed_or_revoked_issue_does_not_retire_conversation(self):
        chat, token = self.conversation()
        for changes in ({"delegate_id": None}, {"status_type": "completed"}, {"archived": True}):
            self.ledger.observe_issue(issue(**({"delegate_id": APP} | changes)))
            with self.assertRaisesRegex(LedgerError, "open and delegated"):
                self.request(chat, token)
            self.assertEqual(self.ledger.item(chat["id"])["state"], "running")

    def test_newer_message_fences_a_stale_repair_decision(self):
        chat, token = self.conversation()
        message = self.ledger.issue_context(chat["id"])["session_messages"][-1]["id"]
        self.ledger.push_inbox(chat["id"], "先别修，只解释一下")
        with self.assertRaisesRegex(LedgerError, "latest message"):
            self.request(chat, token, message)
        self.assertEqual(self.ledger.item(chat["id"])["state"], "running")

    def test_invalid_message_token_and_expired_claim_cannot_start_repair(self):
        chat, token = self.conversation()
        for presented, message in (("wrong", 1), (token, 999)):
            with self.assertRaises(LedgerError):
                self.request(chat, presented, message)
        self.now += 61
        with self.assertRaisesRegex(LedgerError, "expired"):
            self.request(chat, token)

    def test_legacy_empty_prompt_is_not_an_actual_repair_request(self):
        chat, token = self.conversation()
        self.ledger.push_inbox(chat["id"], "（无正文）")
        with self.assertRaisesRegex(LedgerError, "real message"):
            self.request(chat, token)

    def test_stop_wins_over_pending_repair_request(self):
        chat, token = self.conversation()
        self.ledger.cancel(chat["id"], "Linear Stop")
        with self.assertRaises(LedgerError):
            self.request(chat, token)
        self.assertEqual(self.ledger.queue(), [])

    def test_late_message_follows_first_repair_handoff(self):
        chat, token = self.conversation()
        fix = self.request(chat, token)
        self.ledger.push_inbox(chat["id"], "Also check the animal tab")
        self.assertEqual(self.ledger.issue_context(fix["id"])["session_messages"][-1]["body"],
                         "Also check the animal tab")

    def test_completed_answer_remains_available_to_followup(self):
        chat, token = self.conversation()
        self.ledger.finish(chat["id"], token, "delivered",
                           {"summary": "Cause: missing refresh", "prs": [], "verification": "answered"})
        followup = self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill="chat")
        self.ledger.push_inbox(followup["id"], "好，修一下")
        context = self.ledger.issue_context(followup["id"])
        self.assertEqual(context["conversation_history"][0]["summary"], "Cause: missing refresh")
        self.assertEqual(len(context["session_messages"]), 1)

    def test_request_resumes_previous_cancelled_fix_instead_of_unrelated_fresh_work(self):
        previous = self.new_item(delegate_id=APP)
        self.ledger.cancel(previous["id"], "Stop")
        chat, token = self.conversation()
        fix = self.request(chat, token)
        self.assertEqual(fix["predecessor_id"], previous["id"])
        self.assertEqual(self.ledger.item(previous["id"])["state"], "cancelled")

    def cli_request(self, chat, token):
        path = Path(self.tmp.name) / "summary.md"
        path.write_text("Fix the confirmed display issue; preserve sorting.", encoding="utf-8")
        message = self.ledger.issue_context(chat["id"])["session_messages"][-1]["id"]
        return parser().parse_args(["--db", str(self.path), "request-repair", "--item", chat["id"],
                                   "--token", token, "--message-id", str(message), "--summary-file", str(path)])

    def test_cli_checks_live_delegation_before_starting_first_repair(self):
        chat, token = self.conversation()
        args = self.cli_request(chat, token)
        remote = issue(delegate_id=None)
        sent = []
        api = SimpleNamespace(app_user_id=APP, fetch_issue=lambda _: remote,
                              create_activity=lambda session, content: sent.append((session, content)))
        with self.assertRaisesRegex(LedgerError, "delegated"):
            run(args, self.ledger, lambda: api)
        self.assertEqual(sent, [])
        remote["delegate_id"] = APP
        fix = run(args, self.ledger, lambda: api)
        self.assertEqual((fix["skill"], fix["state"]), ("fix", "queued"))
        self.assertEqual(sent[0][0], SESSION)
        self.assertEqual(sent[0][1]["type"], "thought")
        self.assertIn("summary", self.ledger.item(chat["id"])["evidence"])

    def test_cli_fences_stop_while_refreshing_linear(self):
        chat, token = self.conversation()
        args = self.cli_request(chat, token)
        def stopped(_):
            other = self.open_ledger()
            other.cancel(chat["id"], "Stop while fetching Linear")
            return issue(delegate_id=APP)
        api = SimpleNamespace(app_user_id=APP, fetch_issue=stopped)
        with self.assertRaisesRegex(LedgerError, "claim"):
            run(args, self.ledger, lambda: api)
        self.assertEqual(self.ledger.queue(), [])


class RepairReceiverTests(ReceiverBase):
    def test_farm_1261_delegation_question_reply_can_start_first_repair(self):
        self.api.fetch_issue.return_value = issue(labels=[], delegate_id=APP)
        self.receive(); self.receiver.process_one()
        items = self.ledger.items_for_session("session-1")
        self.assertEqual(len(items), 1, "delegation must retain a conversation, not a one-off elicitation")
        chat = items[0]
        self.assertEqual(chat["skill"], "chat")
        self.assertEqual(self.ledger.issue_context(chat["id"])["session_messages"], [])
        token = self.ledger.claim(chat["id"], worker_id="conversation")["token"]
        self.ledger.pop_inbox(chat["id"], token)
        self.ledger.await_input(chat["id"], token, "需要修复图鉴表现吗？")
        self.api.fetch_issue.return_value = issue(labels=["Bug"], delegate_id=APP)
        self.receive(self.event("prompted", body="修复")); self.receiver.process_one()
        token = self.ledger.claim(chat["id"], worker_id="conversation-2")["token"]
        message = self.ledger.issue_context(chat["id"])["session_messages"][-1]["id"]
        fix = self.ledger.request_repair(chat["id"], token, message, APP, "Repair the atlas display.")
        self.assertEqual((fix["skill"], fix["state"], fix["session_id"]), ("fix", "queued", "session-1"))
        self.assertEqual(self.ledger.issue_context(fix["id"])["session_messages"][-1]["body"], "修复")
