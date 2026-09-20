"""The model interprets language; SQLite enforces the scope of its resume action."""
from agent.ledger import LedgerError
from test_ledger import LedgerBase, ISSUE, OTHER, PIN, SESSION, issue, comment
from test_receiver import ReceiverBase, APP


class ResumeWorkTests(LedgerBase):
    def conversation(self, text="The blocker is resolved; please pick this back up."):
        fix = self.new_item(delegate_id=APP)
        self.ledger.cancel(fix["id"], "previous attempt stopped")
        self.ledger.ensure_session("mention", ISSUE, False)
        chat = self.ledger.create_work_item(issue_id=ISSUE, session_id="mention", skill="chat")
        self.ledger.push_inbox(chat["id"], text)
        token = self.ledger.claim(chat["id"], worker_id="chat")["token"]
        return fix, chat, token

    def test_resume_preserves_original_work_handoff_and_all_conversation_messages(self):
        fix, chat, token = self.conversation()
        self.ledger.push_inbox(chat["id"], "New accounts start at zero; count explicit unlocks only.")
        context = self.ledger.issue_context(chat["id"])
        self.assertEqual(context["resumable_work"]["id"], fix["id"])
        messages = context["session_messages"]
        self.ledger.pop_inbox(chat["id"], token)
        resumed = self.ledger.resume_work(chat["id"], token, messages[-1]["id"], APP)
        self.assertEqual((resumed["id"], resumed["state"], resumed["generation"]), (fix["id"], "queued", 1))
        self.assertEqual(resumed["target"], PIN)
        self.assertEqual(self.ledger.item(chat["id"])["state"], "delivered")
        fresh = self.ledger.claim(fix["id"], worker_id="new")["token"]
        self.assertEqual(self.ledger.pop_inbox(fix["id"], fresh), [m["body"] for m in messages])
        with self.assertRaises(LedgerError):
            self.ledger.resume_work(chat["id"], token, messages[-1]["id"], APP)

    def test_resume_refuses_revoked_delegation_without_closing_chat(self):
        fix, chat, token = self.conversation()
        self.ledger.observe_issue(issue(delegate_id=None))
        with self.assertRaisesRegex(LedgerError, "delegat"):
            self.ledger.resume_work(chat["id"], token, 1, APP)
        self.assertEqual(self.ledger.item(chat["id"])["state"], "running")
        self.assertEqual(self.ledger.item(fix["id"])["state"], "cancelled")

    def test_resume_requires_owned_chat_and_a_real_message(self):
        fix, chat, token = self.conversation()
        for bad_token, message_id in (("wrong", 1), (token, 999)):
            with self.assertRaises(LedgerError):
                self.ledger.resume_work(chat["id"], bad_token, message_id, APP)
        self.assertEqual(self.ledger.item(fix["id"])["state"], "cancelled")

    def test_mention_cannot_create_new_fix_authority(self):
        self.ledger.observe_issue(issue(delegate_id=APP))
        self.ledger.ensure_session("mention", ISSUE, False)
        chat = self.ledger.create_work_item(issue_id=ISSUE, session_id="mention", skill="chat")
        self.ledger.push_inbox(chat["id"], "Please fix this")
        token = self.ledger.claim(chat["id"], worker_id="chat")["token"]
        with self.assertRaisesRegex(LedgerError, "previously delegated"):
            self.ledger.resume_work(chat["id"], token, 1, APP)

    def test_context_cannot_offer_another_issues_work(self):
        fix = self.new_item(delegate_id=APP)
        self.ledger.cancel(fix["id"], "stop")
        self.ledger.observe_issue(issue(id=OTHER, delegate_id=APP))
        self.ledger.ensure_session("other", OTHER, False)
        chat = self.ledger.create_work_item(issue_id=OTHER, session_id="other", skill="chat")
        self.assertIsNone(self.ledger.issue_context(chat["id"])["resumable_work"])

    def test_original_session_prefers_its_own_fix_over_newer_other_session(self):
        first = self.new_item(delegate_id=APP)
        self.ledger.cancel(first["id"], "stop")
        self.ledger.ensure_session("later", ISSUE, True)
        later = self.ledger.create_work_item(issue_id=ISSUE, session_id="later", skill="fix")
        self.ledger.cancel(later["id"], "stop")
        chat = self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill="chat")
        self.assertEqual(self.ledger.issue_context(chat["id"])["resumable_work"]["id"], first["id"])

    def test_message_arriving_during_handoff_reaches_resumed_fix(self):
        fix, chat, token = self.conversation()
        self.ledger.resume_work(chat["id"], token, 1, APP)
        delivered = self.ledger.push_inbox(chat["id"], "One more detail: zero tables initially")
        self.assertEqual(delivered["item_id"], fix["id"])
        fresh = self.ledger.claim(fix["id"], worker_id="fresh")["token"]
        self.assertIn("One more detail: zero tables initially", self.ledger.pop_inbox(fix["id"], fresh))

    def test_observing_a_new_comment_does_not_restart_blocked_work(self):
        fix = self.new_item(delegate_id=APP)
        token = self.ledger.claim(fix["id"], worker_id="w")["token"]
        action = self.ledger.prepare_comment(fix["id"], token, "blocker", "generator missing")
        self.ledger.confirm_comment(action["action_id"], "comment")
        self.ledger.finish(fix["id"], token, "blocked", {"summary": "generator missing", "comment_action_id": action["action_id"]})
        self.ledger.observe_issue(issue(delegate_id=APP, comments=[comment("Don't restart yet")]))
        self.assertEqual(self.ledger.item(fix["id"])["state"], "blocked")

    def test_answer_arriving_before_question_is_parked_is_not_stranded(self):
        fix = self.new_item(delegate_id=APP)
        token = self.ledger.claim(fix["id"], worker_id="w")["token"]
        self.ledger.push_inbox(fix["id"], "Zero tables initially")
        result = self.ledger.await_input(fix["id"], token, "Zero or one?")
        self.assertEqual(result["state"], "queued")
        fresh = self.ledger.claim(fix["id"], worker_id="fresh")["token"]
        self.assertEqual(self.ledger.pop_inbox(fix["id"], fresh), ["Zero tables initially"])


class ConversationReceiverTests(ReceiverBase):
    def mention(self, body):
        return self.event(agentSession={"id": "mention", "issue": {"id": ISSUE}, "comment": {"body": body}})

    def test_mention_on_delegated_issue_is_interpreted_before_restarting(self):
        self.receive(); self.receiver.process_one()
        fix = self.ledger.items_for_session("session-1")[0]
        self.ledger.cancel(fix["id"], "stop")
        self.receive(self.mention("@FarmBot don't restart yet, what did you find?"))
        self.receiver.process_one()
        self.assertEqual(self.ledger.item(fix["id"])["state"], "cancelled")
        self.assertEqual(self.ledger.items_for_session("mention")[0]["skill"], "chat")

    def test_mention_answer_resumes_waiting_worker_and_keeps_answer(self):
        self.receive(); self.receiver.process_one()
        fix = self.ledger.items_for_session("session-1")[0]
        token = self.ledger.claim(fix["id"], worker_id="w")["token"]
        self.ledger.await_input(fix["id"], token, "Zero or one?")
        self.receive(self.mention("@FarmBot 从零开始，只统计主动解锁，继续吧"))
        self.receiver.process_one()
        self.assertEqual(self.ledger.item(fix["id"])["state"], "queued")
        token = self.ledger.claim(fix["id"], worker_id="fresh")["token"]
        self.assertIn("从零开始", self.ledger.pop_inbox(fix["id"], token)[0])

    def test_undelegated_chat_question_can_be_answered_via_new_mention(self):
        self.api.fetch_issue.return_value = issue(delegate_id=None)
        self.receive(self.mention("Which environment?")); self.receiver.process_one()
        chat = self.ledger.items_for_session("mention")[0]
        token = self.ledger.claim(chat["id"], worker_id="chat")["token"]
        self.ledger.await_input(chat["id"], token, "Which build?")
        event = self.mention("Android test build")
        event["agentSession"]["id"] = "answer"
        self.receive(event); self.receiver.process_one()
        self.assertEqual(self.ledger.item(chat["id"])["state"], "queued")
