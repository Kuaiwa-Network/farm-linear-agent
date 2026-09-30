import contextlib
import hashlib
import hmac
import io
import json
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent.heartbeat import OUTCOMES, Heartbeat
from agent.ledger import Ledger, LedgerError
from agent.lifecycle import Lifecycle
from agent.monitor import probe_health
from agent.receiver import MAX_BODY, Receiver, make_server
from agent.router import Decision
from agent.scheduler import Scheduler
from agent.session_progress import SessionProgress
from agent.withdrawal import (DEFER_ACK, DEFER_STILL, DEFER_UNDELEGATED, FORWARD_PARKED_UNDELEGATED,
                              FORWARD_WITHDRAWING, IN_PLACE_NOTE, MOVED_THREAD, QUESTION_WITHDRAWN,
                              REDELEGATED_RUNNING, REDELEGATED_WAITING, RESUME_UNDELEGATED, RESUMED_ELSEWHERE,
                              SILENT_BUSY, SILENT_ENDED, SILENT_WAITING, SILENT_WAITING_CHAT, STOP_ALREADY,
                              STOP_ELSEWHERE, STOP_MOVED, STOP_MOVED_THREAD, STOPPED_ELSEWHERE, STOPPED_ELSEWHERE_CHAT,
                              SUPERSEDE_SUFFIX, SUPERSEDED, UNDELEGATED_CHAT)
from agent.worktrees import WorktreeError
from test_ledger import DESIGNER, ISSUE, OTHER, OWNER, PIN, issue
from test_scheduler import ROOT, SKILLS, FakeLauncher, FakeWorktrees

APP = "e5a8c16d-9f85-4123-acf5-94e41c3304d5"
IDENTITY = {"oauthClientId": "client", "appUserId": APP, "organizationId": "org"}


def _raise(exc):
    def raiser(*args, **kwargs):
        raise exc
    return raiser


class ReceiverBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "ledger.sqlite3"
        self.api = Mock()
        self.api.session_has_artificial_root.return_value = False
        # What Linear says of a thread's state when a settle asks (silent-delegation design §3.6): a response closed it.
        self.api.session_state.return_value = {"status": "complete", "archived": False}
        # Bot/修改 is what makes a bare delegation here a fix item (D18); Bug alone no longer routes.
        self.api.fetch_issue.return_value = issue(labels=["Bug", "修改"], delegate_id=APP,
                                                  label_groups=[{"group": "Bot", "label": "修改"}])
        self.api.create_activity.return_value = {"success": True, "agentActivity": {"id": "act"}}
        self.scheduler = Mock()
        # A Stop's reply depends on the job its cancel ended, which the scheduler returns: the double answers with
        # the view of the job it was asked to stop, and cancels nothing.
        self.scheduler.stop.side_effect = lambda item_id, *args, **kwargs: self.ledger.item(item_id)
        self.receiver = Receiver(self.db, "signing-secret", IDENTITY, self.api, lambda: Ledger(self.db),
                                 skills={"chat", "fix"}, scheduler=self.scheduler)
        self.addCleanup(self.receiver.close)
        self.ledger = Ledger(self.db)
        self.addCleanup(self.ledger.close)

    def assert_stopped(self, item, notice=None):
        """The scheduler was asked once to stop `item`, as a Linear Stop asks: only while the job is still one a Stop
        ends, and with `notice` for the job's own thread, None when the Stop was pressed there (silent-delegation
        design A1)."""
        [call] = self.scheduler.stop.call_args_list
        self.assertEqual(call.args, (item["id"], "Linear stop"))
        self.assertEqual(call.kwargs["states"], ("queued", "running", "awaiting_input", "awaiting_resource", "blocked"))
        self.assertEqual(call.kwargs["notice"](self.ledger.item(item["id"])), notice)

    def event(self, action="created", **changes):
        result = {"type": "AgentSessionEvent", "action": action, "webhookTimestamp": 100_000,
                  "organizationId": "org", "oauthClientId": "client", "appUserId": APP,
                  "agentSession": {"id": "session-1", "issue": {"id": ISSUE, "identifier": "FARM-1", "url": "https://linear.app/k/issue/FARM-1"}},
                  "promptContext": "Issue FARM-1 ...", "guidance": "prefer hive"}
        if action == "prompted":
            result["agentActivity"] = {"id": "act-1", "agentSessionId": "session-1", "createdAt": "2026-09-18T00:01:40.000Z",
                                       "content": {"type": "prompt", "body": changes.pop("body", "hello")}}
        result.update(changes)
        return result

    def receive(self, event=None, signature=None):
        body = json.dumps(event or self.event()).encode()
        signature = signature if signature is not None else hmac.new(b"signing-secret", body, hashlib.sha256).hexdigest()
        return self.receiver.receive(body, signature, now_ms=100_000)

    def activities(self):
        return [call.args[1] for call in self.api.create_activity.call_args_list]

    def sent(self):
        """(session, content) of every activity posted through the API double, in order."""
        return [(call.args[0], call.args[1]) for call in self.api.create_activity.call_args_list]

    def said_in(self, session, kind="response"):
        """The bodies of the `kind` activities posted in `session`, in order."""
        return [content["body"] for posted_in, content in self.sent()
                if posted_in == session and content["type"] == kind]


class ReceiverTests(ReceiverBase):
    def test_placeholder_comment_on_delegated_bug_starts_fix_without_placeholder_prompt(self):
        event = self.event()
        event["agentSession"]["comment"] = {
            "id": "root", "body": "This thread is for an agent session with farmbot."}
        self.api.session_has_artificial_root.return_value = True
        self.receive(event)
        self.receiver.process_one()
        item = self.ledger.items_for_session("session-1")[0]
        self.assertEqual((item["skill"], item["state"]), ("fix", "queued"))
        self.assertTrue(self.ledger.session("session-1")["delegation"])
        self.assertEqual(self.ledger.issue_context(item["id"])["inbox_pending"], 0)
        self.api.session_has_artificial_root.assert_called_once_with("session-1", ISSUE, APP)

    def test_real_mention_on_already_delegated_issue_does_not_start_fix(self):
        event = self.event()
        event["agentSession"]["comment"] = {"id": "human", "body": "@FarmBot explain this"}
        self.receive(event)
        self.receiver.process_one()
        item = self.ledger.items_for_session("session-1")[0]
        self.assertEqual(item["skill"], "chat")
        self.assertFalse(self.ledger.session("session-1")["delegation"])
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.assertEqual(self.ledger.pop_inbox(item["id"], token), ["@FarmBot explain this"])

    def test_source_comment_is_the_prompt_instead_of_the_placeholder(self):
        event = self.event()
        event["agentSession"].update({
            "comment": {"id": "root", "body": "This thread is for an agent session with farmbot."},
            "sourceComment": {"id": "human", "body": "@FarmBot explain this"}})
        self.receive(event)
        self.receiver.process_one()
        item = self.ledger.items_for_session("session-1")[0]
        self.assertEqual(item["skill"], "chat")
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.assertEqual(self.ledger.pop_inbox(item["id"], token), ["@FarmBot explain this"])

    def test_unverifiable_comment_origin_does_not_start_work(self):
        event = self.event()
        event["agentSession"]["commentId"] = "root"
        self.api.session_has_artificial_root.side_effect = RuntimeError("unavailable")
        self.receive(event)
        self.receiver.process_one()
        self.assertEqual(self.ledger.items_for_session("session-1"), [])
        self.assertEqual(self.receiver.results()[0]["status"], "uncertain")

    def test_delegated_bug_creates_fix_item_and_acknowledges(self):
        self.assertEqual(self.receive(), (200, "accepted"))
        self.assertTrue(self.receiver.process_one())
        items = self.ledger.items_for_session("session-1")
        self.assertEqual((items[0]["skill"], items[0]["state"]), ("fix", "queued"))
        self.assertTrue(self.ledger.session("session-1")["delegation"])
        self.assertEqual(self.activities()[0]["type"], "thought")
        self.assertEqual(self.receiver.results()[0]["status"], "done")

    def test_session_keeps_the_webhook_guidance_for_the_worker(self):
        self.receive(); self.receiver.process_one()
        self.assertEqual(self.ledger.session("session-1")["guidance"], "prefer hive")

    def test_mention_creates_chat_item_with_the_prompt_in_its_inbox(self):
        self.api.fetch_issue.return_value = issue(labels=["Bug"], delegate_id=None)
        self.receive(self.event(agentSession={"id": "session-2", "issue": {"id": ISSUE, "identifier": "FARM-1", "url": "u"},
                                              "comment": {"body": "@FarmBot 这个 bug 是客户端还是服务端的？"}}))
        self.receiver.process_one()
        item = self.ledger.items_for_session("session-2")[0]
        self.assertEqual(item["skill"], "chat")
        self.assertEqual(self.ledger.issue_context(item["id"])["inbox_pending"], 1)

    def test_prompt_into_running_item_is_steering(self):
        self.receive(); self.receiver.process_one()
        item = self.ledger.items_for_session("session-1")[0]
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.receive(self.event("prompted", body="先看服务端日志")); self.receiver.process_one()
        self.assertEqual(self.ledger.pop_inbox(item["id"], token), ["先看服务端日志"])

    def test_prompt_into_waiting_item_resumes(self):
        """A pause for a question and a pause on a human step elsewhere (`--reason waiting`) resume the same way."""
        for reason in ("question", "waiting"):
            with self.subTest(reason=reason):
                self.setUp()
                self.receive(); self.receiver.process_one()
                item = self.ledger.items_for_session("session-1")[0]
                token = self.ledger.claim(item["id"], worker_id="w")["token"]
                self.ledger.await_input(item["id"], token, "which server?", reason=reason)
                self.receive(self.event("prompted", body="公共测试服")); self.receiver.process_one()
                self.assertEqual(self.ledger.item(item["id"])["state"], "queued")

    def test_stop_cancels_through_the_scheduler_and_replies(self):
        self.receive(); self.receiver.process_one()
        item = self.ledger.items_for_session("session-1")[0]
        stop = self.event("prompted")
        stop["agentActivity"]["signal"] = "stop"
        stop["agentActivity"]["content"] = {"type": "prompt"}
        self.assertEqual(self.receive(stop), (200, "stop received"))
        self.assertTrue(self.receiver.process_one())
        self.assert_stopped(item)
        self.assertEqual(self.activities()[-1]["type"], "response")
        self.assertEqual(self.receive(stop), (200, "duplicate"))

    def test_duplicates_bad_signature_stale_timestamp_and_wrong_identity(self):
        self.receive()
        self.assertEqual(self.receive(), (200, "duplicate"))
        self.assertEqual(self.receive(signature="bad")[0], 401)
        self.assertEqual(self.receive(self.event(webhookTimestamp=0))[0], 401)
        self.assertEqual(self.receive(self.event(appUserId="someone"))[0], 403)
        self.assertEqual(self.receive({"type": "Issue", "action": "update", "webhookTimestamp": 100_000}), (403, "identity mismatch"))

    def test_delegation_without_bug_label_creates_read_only_conversation(self):
        self.api.fetch_issue.return_value = issue(labels=["需求"], delegate_id=APP)
        self.receive(); self.receiver.process_one()
        self.assertEqual(self.ledger.items_for_session("session-1")[0]["skill"], "chat")
        self.assertEqual(self.activities()[0]["type"], "thought")

    def test_api_failure_marks_event_uncertain_not_done(self):
        self.api.fetch_issue.side_effect = RuntimeError("boom")
        self.receive(); self.receiver.process_one()
        self.assertEqual(self.receiver.results()[0]["status"], "uncertain")

    def test_a_delegation_from_a_second_session_takes_the_waiting_work_over(self):
        """Withdrawn-work design P4: the newer delegation session owns the card. Its queued fix is cancelled and
        continued there by a linked fix, the new session is acknowledged first, and the old one is told where its
        work went. Nothing is refused."""
        self.receive(); self.receiver.process_one()
        [old] = self.ledger.items_for_session("session-1")
        other = self.event(agentSession={"id": "session-2", "issue": {"id": ISSUE, "identifier": "FARM-1", "url": "u"}})
        self.receive(other); self.receiver.process_one()
        [new] = self.ledger.items_for_session("session-2")
        self.assertEqual((self.ledger.item(old["id"])["state"], new["skill"], new["state"], new["predecessor_id"]),
                         ("cancelled", "fix", "queued", old["id"]))
        self.assertEqual([(call.args[0], call.args[1]) for call in self.api.create_activity.call_args_list[-2:]],
                         [("session-2", {"type": "thought", "body": FIX_ACK + "\n" + SUPERSEDE_SUFFIX}),
                          ("session-1", {"type": "response", "body": SUPERSEDED})])
        self.assertFalse([a for a in self.activities() if "进行中的工作" in a["body"]])
        self.assertEqual(self.receiver.results()[-1]["status"], "done")

    def test_mention_from_a_second_session_on_an_active_issue_steers_the_worker(self):
        self.receive(); self.receiver.process_one()
        item = self.ledger.items_for_session("session-1")[0]
        self.api.fetch_issue.return_value = issue(labels=["Bug"], delegate_id=None)
        other = self.event(agentSession={"id": "session-3", "issue": {"id": ISSUE, "identifier": "FARM-1", "url": "u"},
                                         "comment": {"body": "@FarmBot 安卓上也能复现"}})
        self.receive(other); self.receiver.process_one()
        self.assertEqual(self.ledger.items_for_session("session-3"), [])
        self.assertEqual(self.ledger.issue_context(item["id"])["inbox_pending"], 1)
        self.assertEqual(self.activities()[-1]["type"], "thought")
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.assertEqual(self.ledger.pop_inbox(item["id"], token), ["@FarmBot 安卓上也能复现"])

    def test_qa_request_keeps_original_intent_for_the_worker(self):
        self.api.fetch_issue.return_value = issue(labels=["Bug"], delegate_id=None)
        self.receive(self.event(agentSession={"id": "session-4", "issue": {"id": ISSUE, "identifier": "FARM-1", "url": "u"},
                                              "comment": {"body": "@FarmBot 帮我复现一下"}}))
        self.receiver.process_one()
        item = self.ledger.items_for_session("session-4")[0]
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.assertEqual(self.ledger.pop_inbox(item["id"], token), ["@FarmBot 帮我复现一下"])
        self.assertEqual(self.activities()[-1]["type"], "thought")

    def test_a_new_session_pins_the_client_head_and_says_so_in_the_one_acknowledgment(self):
        self.receiver.worktrees = SimpleNamespace(remote_head=lambda repo, timeout=8: "c" * 40)
        self.receive()
        self.assertTrue(self.receiver.process_one())
        target = self.ledger.session("session-1")["target"]
        self.assertEqual((target["commit_sha"], target["server_environment"]), ("c" * 40, "公共测试服"))
        self.assertEqual(self.ledger.items_for_session("session-1")[0]["target"]["commit_sha"], "c" * 40)
        # One event, one activity: create_activity treats activity_id as the activity's identity.
        self.assertEqual(len(self.activities()), 1)
        self.assertIn("c" * 7, self.activities()[0]["body"])

    def test_a_session_whose_client_head_cannot_be_resolved_still_starts_without_a_pin(self):
        self.receiver.worktrees = SimpleNamespace(remote_head=_raise(WorktreeError("origin unreachable")))
        self.receive()
        self.assertTrue(self.receiver.process_one())
        self.assertIsNone(self.ledger.session("session-1")["target"])
        self.assertEqual(self.ledger.items_for_session("session-1")[0]["target"], None)
        self.assertEqual(len(self.activities()), 1)
        # The human is told the pin is missing, and the event is a success, not a swallowed fault.
        self.assertIn("暂时无法锁定", self.activities()[0]["body"])
        self.assertEqual(self.receiver.results()[-1]["status"], "done")

    def test_a_read_only_delegation_announces_its_pin_for_later_repair(self):
        self.api.fetch_issue.return_value = issue(labels=["需求"], delegate_id=APP)
        self.receiver.worktrees = SimpleNamespace(remote_head=lambda repo, timeout=8: "c" * 40)
        self.receive()
        self.assertTrue(self.receiver.process_one())
        self.assertEqual(self.ledger.items_for_session("session-1")[0]["skill"], "chat")
        self.assertEqual(self.activities()[0]["type"], "thought")
        self.assertIn("c" * 7, self.activities()[0]["body"])
        self.assertEqual(self.ledger.session("session-1")["target"]["commit_sha"], "c" * 40)

    def test_a_ledger_fault_while_pinning_is_not_disguised_as_an_unreachable_origin(self):
        """Only an unreachable origin is absorbed: a fault in our own store must reach the event's status."""
        self.receiver.worktrees = SimpleNamespace(remote_head=lambda repo, timeout=8: "not-a-commit")
        self.receive()
        self.assertTrue(self.receiver.process_one())
        result = self.receiver.results()[-1]
        self.assertEqual((result["status"], result["error"]), ("uncertain", "LedgerError"))
        self.assertEqual(self.activities()[-1]["type"], "error")
        self.assertNotIn("暂时无法锁定", self.activities()[-1]["body"])
        self.assertEqual(self.ledger.items_for_session("session-1"), [])

    def test_the_receiver_never_clones_or_fetches_to_resolve_a_pin(self):
        """spec §17 criterion 1 gives the first activity ten seconds; ensure_clone is minutes (Plan 1a's
        seed-clones exists for exactly this)."""
        self.receiver.worktrees = SimpleNamespace(remote_head=lambda repo, timeout=8: "c" * 40,
                                                  ensure_clone=_raise(AssertionError("cloned on the ack path")),
                                                  fetch=_raise(AssertionError("fetched on the ack path")),
                                                  resolve_commit=_raise(AssertionError("resolved the slow way")))
        self.receive()
        self.assertTrue(self.receiver.process_one())
        self.assertEqual(self.ledger.session("session-1")["target"]["commit_sha"], "c" * 40)

    def test_the_decision_follows_what_changed_while_the_pin_was_resolved(self):
        """Plan P6 routes before the pin, and the pin's ls-remote can take seconds. Work that ends meanwhile in another
        session must not be steered: the event is routed again once the pin returns, on what is true then."""
        self.receive()
        self.receiver.process_one()  # a delegated fix in session-1; this receiver has no worktrees, so no pin
        [fix] = self.ledger.items_for_session("session-1")
        self.ledger.claim(fix["id"], worker_id="w")

        def remote_head(repo, timeout=8):
            self.ledger.fail(fix["id"], "worker exited")  # the fix ends while ls-remote runs
            return "c" * 40

        self.receiver.worktrees = SimpleNamespace(remote_head=remote_head)
        self.receive(self.event(agentSession={"id": "session-2", "issue": {"id": ISSUE, "identifier": "FARM-1",
                                                                             "url": "u"},
                                              "comment": {"body": "@FarmBot 这个问题现在怎样了？"}}))
        self.assertTrue(self.receiver.process_one())
        self.assertEqual(self.receiver.results()[-1]["status"], "done")
        self.assertEqual([(item["skill"], item["state"]) for item in self.ledger.items_for_session("session-2")],
                         [("chat", "queued")])
        self.assertNotEqual(self.activities()[-1]["type"], "error")


class SessionPeopleTests(ReceiverBase):
    """Who opened each session and who wrote each message (spec §5.3, §9.2). Linear's webhook users also carry an
    email and an avatar; FarmBot keeps only id, name and profile URL."""
    EMAILS = {"Owner Two": "owner.two@example.com", "Designer One": "designer.one@example.com"}

    def user(self, person):
        """A webhook user payload: the person plus the fields FarmBot must not keep."""
        return {**person, "email": self.EMAILS[person["name"]], "avatarUrl": "https://example.com/avatar.png"}

    def delegation(self, creator=None):
        event = self.event()
        if creator is not None:
            event["agentSession"]["creator"] = self.user(creator)
        return event

    def reply(self, body, author, activity="act-1"):
        event = self.event("prompted", body=body)
        event["agentSession"]["creator"] = self.user(OWNER)
        event["agentActivity"].update(id=activity, user=self.user(author))
        return event

    def mention(self, session, body, creator):
        return self.event(agentSession={"id": session, "issue": {"id": ISSUE, "identifier": "FARM-1", "url": "u"},
                                        "comment": {"body": body}, "creator": self.user(creator)})

    def messages(self, item_id):
        return [(m["body"], m["author"]) for m in self.ledger.issue_context(item_id)["session_messages"]]

    def assert_no_email_stored(self):
        stored = "\n".join(self.ledger.connection.iterdump())  # every table in the file, the receiver's included
        for email in self.EMAILS.values():
            self.assertNotIn(email, stored)

    def test_a_delegation_records_who_opened_the_session_and_never_their_email(self):
        self.receive(self.delegation(creator=OWNER))
        self.assert_no_email_stored()  # the prepared event waits in webhook_events until it is processed
        self.receiver.process_one()
        self.assertEqual(self.ledger.session("session-1")["creator"], OWNER)
        self.assertEqual(self.ledger.items_for_session("session-1")[0]["skill"], "fix")
        self.assert_no_email_stored()

    def test_a_mention_records_its_creator_as_the_author_of_the_message_that_opened_it(self):
        self.api.fetch_issue.return_value = issue(labels=["Bug"], delegate_id=None)
        self.receive(self.mention("session-2", "@FarmBot 这个 bug 是客户端还是服务端的？", DESIGNER))
        self.receiver.process_one()
        item = self.ledger.items_for_session("session-2")[0]
        self.assertEqual(item["skill"], "chat")
        self.assertEqual(self.ledger.session("session-2")["creator"], DESIGNER)
        self.assertEqual(self.messages(item["id"]), [("@FarmBot 这个 bug 是客户端还是服务端的？", DESIGNER)])
        self.assert_no_email_stored()

    def test_session_replies_record_the_prompting_user_as_their_author(self):
        self.receive(self.delegation(creator=OWNER)); self.receiver.process_one()
        item = self.ledger.items_for_session("session-1")[0]
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.receive(self.reply("先看服务端日志", DESIGNER)); self.receiver.process_one()     # steers
        self.ledger.pop_inbox(item["id"], token)  # read, so that await_input parks instead of requeueing at once
        self.ledger.await_input(item["id"], token, "which server?")
        self.receive(self.reply("公共测试服", OWNER, activity="act-2")); self.receiver.process_one()  # resumes
        self.assertEqual(self.ledger.item(item["id"])["state"], "queued")
        self.assertEqual(self.messages(item["id"]), [("先看服务端日志", DESIGNER), ("公共测试服", OWNER)])
        self.assertEqual(self.ledger.session("session-1")["creator"], OWNER)
        self.assert_no_email_stored()

    def test_a_mention_forwarded_to_another_sessions_worker_keeps_its_author(self):
        self.receive(); self.receiver.process_one()
        item = self.ledger.items_for_session("session-1")[0]
        self.api.fetch_issue.return_value = issue(labels=["Bug"], delegate_id=None)
        self.receive(self.mention("session-3", "@FarmBot 安卓上也能复现", DESIGNER)); self.receiver.process_one()
        self.assertEqual(self.messages(item["id"]), [("@FarmBot 安卓上也能复现", DESIGNER)])

    def test_a_session_recorded_without_a_creator_gets_it_from_a_later_event(self):
        self.receive(self.delegation()); self.receiver.process_one()
        self.assertIsNone(self.ledger.session("session-1")["creator"])
        self.receive(self.reply("先看服务端日志", DESIGNER)); self.receiver.process_one()
        self.assertEqual(self.ledger.session("session-1")["creator"], OWNER)

    def test_a_user_without_a_linear_profile_url_is_not_recorded(self):
        event = self.event()
        event["agentSession"]["creator"] = {**OWNER, "url": "https://example.com/owner-two"}
        self.receive(event); self.receiver.process_one()
        self.assertIsNone(self.ledger.session("session-1")["creator"])
        self.assertEqual(self.ledger.items_for_session("session-1")[0]["skill"], "fix")

    def test_an_event_accepted_before_the_upgrade_is_processed_with_nobody_recorded(self):
        """A pending event prepared by the previous revision has neither key; it must still be processed."""
        prepared = {"action": "created", "session_id": "session-1", "issue_id": ISSUE, "text": "",
                    "is_mention": False, "guidance": ""}
        with self.receiver.db:
            self.receiver.db.execute("INSERT INTO webhook_events VALUES (?,?,?,'pending',?,?,NULL,NULL)",
                                     ("org:created:session-1", "session-1", "ack-1", json.dumps(prepared), 1.0))
        self.assertTrue(self.receiver.process_one())
        self.assertEqual(self.receiver.results()[-1]["status"], "done")
        self.assertIsNone(self.ledger.session("session-1")["creator"])
        self.assertEqual(self.ledger.items_for_session("session-1")[0]["skill"], "fix")

    def test_a_message_keeps_the_time_its_event_arrived_not_when_it_was_processed(self):
        """A pending event survives a restart; the ruling in it must keep the day it was sent."""
        self.api.fetch_issue.return_value = issue(labels=["Bug"], delegate_id=None)
        self.receiver.clock = lambda: 1790380500.0  # 2026-09-25T23:55:00Z; the ledger's clock reads now
        self.receive(self.mention("session-2", "@FarmBot 按服务端的做", DESIGNER))
        self.receiver.process_one()
        item = self.ledger.items_for_session("session-2")[0]
        self.assertEqual([(m["author"], m["created_at"]) for m in self.ledger.issue_context(item["id"])["session_messages"]],
                         [(DESIGNER, "2026-09-25T23:55:00+00:00")])

    def test_a_steer_a_resume_and_a_forwarded_mention_keep_the_time_they_arrived_too(self):
        """The same for every way a message reaches a fix item: a reply that steers it, a reply that resumes it
        after await-input (where answers to a worker's question arrive), and a mention forwarded from another
        session. Each arrives a minute apart, all processed with the ledger's clock reading now."""
        def received(event, at):
            self.receiver.clock = lambda: at
            self.receive(event); self.receiver.process_one()

        self.receive(self.delegation(creator=OWNER)); self.receiver.process_one()
        item = self.ledger.items_for_session("session-1")[0]
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        received(self.reply("先看服务端日志", DESIGNER), 1790380500.0)                           # steers
        self.ledger.pop_inbox(item["id"], token)
        self.ledger.await_input(item["id"], token, "which server?")
        self.assertEqual(self.ledger.item(item["id"])["state"], "awaiting_input")
        received(self.reply("公共测试服", OWNER, activity="act-2"), 1790380560.0)                # resumes
        self.assertEqual(self.ledger.item(item["id"])["state"], "queued")
        self.api.fetch_issue.return_value = issue(labels=["Bug"], delegate_id=None)
        received(self.mention("session-3", "@FarmBot 安卓上也能复现", DESIGNER), 1790380620.0)  # forwarded
        self.assertEqual([(m["body"], m["created_at"]) for m in self.ledger.issue_context(item["id"])["session_messages"]],
                         [("先看服务端日志", "2026-09-25T23:55:00+00:00"), ("公共测试服", "2026-09-25T23:56:00+00:00"),
                          ("@FarmBot 安卓上也能复现", "2026-09-25T23:57:00+00:00")])


class HardeningTests(ReceiverBase):
    def test_oversized_guidance_is_rejected_like_oversized_prompt_text(self):
        self.assertEqual(self.receive(self.event(guidance="指" * 32001)), (400, "invalid prompt"))
        self.assertEqual(self.receiver.results(), [])

    def test_a_database_failure_marks_the_event_uncertain_and_tells_the_session(self):
        self.receive()
        # receiver.close() closes whatever self.ledger holds, so release the real connection before
        # swapping in the Mock; otherwise it leaks and -W error turns the ResourceWarning into a failure.
        self.addCleanup(self.receiver.ledger.close)
        self.receiver.ledger = Mock()
        self.receiver.ledger.observe_issue.side_effect = sqlite3.OperationalError(
            "table sessions has no column named guidance")
        self.assertTrue(self.receiver.process_one())
        result = self.receiver.results()[-1]
        self.assertEqual((result["status"], result["error"]), ("uncertain", "OperationalError"))
        self.assertEqual(self.activities()[-1]["type"], "error")


class HttpTests(ReceiverBase):
    def test_loopback_listener_starts_and_serves_health_without_reverse_dns(self):
        import threading

        with patch("socket.getfqdn", side_effect=AssertionError("reverse DNS must not gate startup")) as resolve:
            server = make_server(self.receiver, port=0)
            self.addCleanup(server.server_close)
            self.assertEqual(server.server_address[0], "127.0.0.1")
            self.assertEqual(server.server_name, "127.0.0.1")
            self.assertEqual(server.server_port, server.server_address[1])
            self.assertGreater(server.server_port, 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(thread.join, 5)
            self.addCleanup(server.shutdown)
            with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/health", timeout=5) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(json.load(response)["status"], "FarmBot ready")
            resolve.assert_not_called()

    def test_route_accepts_signed_and_rejects_unsigned(self):
        server = make_server(self.receiver, port=0)
        import threading
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        body = json.dumps(self.event(webhookTimestamp=int(__import__("time").time() * 1000))).encode()
        signature = hmac.new(b"signing-secret", body, hashlib.sha256).hexdigest()
        request = urllib.request.Request(f"http://127.0.0.1:{port}/webhook", data=body, headers={"Linear-Signature": signature})
        import contextlib, io
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            with urllib.request.urlopen(request, timeout=5) as response:
                self.assertEqual(json.load(response)["status"], "accepted")
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/webhook", data=body), timeout=5)
        self.assertEqual(ctx.exception.code, 401)
        lines = [json.loads(line) for line in log.getvalue().splitlines()]
        self.assertEqual([(l["status"], l["result"], l["type"], l["action"]) for l in lines],
                         [(200, "accepted", "AgentSessionEvent", "created"), (401, "invalid signature", "AgentSessionEvent", "created")])
        self.assertNotIn("body", json.dumps(lines))  # outcomes only, never the payload
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as response:
            self.assertEqual(json.load(response)["status"], "FarmBot ready")

    def serve_http(self, heartbeat):
        server = make_server(self.receiver, port=0)
        server.heartbeat = heartbeat
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}/webhook"

    def signed(self, body):
        return {"Linear-Signature": hmac.new(b"signing-secret", body, hashlib.sha256).hexdigest()}

    def test_every_webhook_outcome_is_counted_on_the_server_heartbeat(self):
        from agent.heartbeat import Heartbeat
        beat = Heartbeat(runtime="fake")
        url = self.serve_http(beat)
        body = json.dumps(self.event(webhookTimestamp=int(time.time() * 1000))).encode()
        with contextlib.redirect_stdout(io.StringIO()):
            with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=self.signed(body)),
                                        timeout=5) as response:
                self.assertEqual(json.load(response)["status"], "accepted")
            for request, code in ((urllib.request.Request(url, data=body), 401),
                                  (urllib.request.Request(url, data=b"{", headers=self.signed(b"{")), 400)):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(request, timeout=5)
                self.assertEqual(caught.exception.code, code)
                caught.exception.close()
        hooks = beat.payload()["webhooks"]
        self.assertEqual((hooks["counts"]["accepted"], hooks["counts"]["rejected"], hooks["counts"]["malformed"]),
                         (1, 1, 1))
        self.assertIsNotNone(hooks["last_rejected_at"])

    def test_a_failing_counter_never_changes_a_webhook_answer(self):
        heartbeat = Mock(webhook=Mock(side_effect=RuntimeError("counter broke")))
        url = self.serve_http(heartbeat)
        body = json.dumps(self.event(webhookTimestamp=int(time.time() * 1000))).encode()
        with contextlib.redirect_stdout(io.StringIO()):
            with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=self.signed(body)),
                                        timeout=5) as response:
                self.assertEqual(json.load(response)["status"], "accepted")
        self.assertEqual(heartbeat.webhook.call_count, 1)  # the guarded call ran, and raised

    def test_answers_the_handler_gives_itself_are_counted_too(self):
        """Every /webhook POST counts (spec), including a size rejection before the receiver runs and the 500
        for a receiver that raises."""
        from agent.heartbeat import Heartbeat
        beat = Heartbeat(runtime="fake")
        url = self.serve_http(beat)
        with patch.object(self.receiver, "receive", side_effect=RuntimeError("receiver broke")):
            for data, code in ((b"", 413), (b"{}", 500)):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=5)
                self.assertEqual(caught.exception.code, code)
                caught.exception.close()
        counts = beat.payload()["webhooks"]["counts"]
        self.assertEqual((counts["malformed"], counts["failed"]), (1, 1))

    def raw_webhook(self, url, head, body, *, finish=True):
        """(status, result) for one /webhook POST sent over a plain socket, for the answers urllib cannot provoke.
        `finish` ends the request body with a half-close; otherwise the connection is held open."""
        port = urllib.parse.urlsplit(url).port
        with socket.create_connection(("127.0.0.1", port), timeout=10) as connection:
            connection.sendall(b"POST /webhook HTTP/1.0\r\nHost: 127.0.0.1\r\n" + head + b"\r\n" + body)
            if finish:
                connection.shutdown(socket.SHUT_WR)
            response = b""
            while chunk := connection.recv(65536):
                response += chunk
        self.assertTrue(response, "the connection closed without an answer")
        status_line, _, rest = response.partition(b"\r\n")
        return int(status_line.split()[1]), json.loads(rest.partition(b"\r\n\r\n")[2])["status"]

    def assert_counted_as_malformed(self, beat):
        self.assertEqual(beat.payload()["webhooks"]["counts"], {**dict.fromkeys(OUTCOMES, 0), "malformed": 1})

    def test_a_length_that_is_not_a_number_is_answered_400_and_counted(self):
        beat = Heartbeat(runtime="fake")
        url = self.serve_http(beat)
        self.assertEqual(self.raw_webhook(url, b"Content-Length: x\r\n", b""), (400, "invalid length"))
        self.assert_counted_as_malformed(beat)

    def test_a_body_shorter_than_its_length_is_answered_400_and_counted(self):
        beat = Heartbeat(runtime="fake")
        url = self.serve_http(beat)
        self.assertEqual(self.raw_webhook(url, b"Content-Length: 10\r\n", b"{}"), (400, "incomplete body"))
        self.assert_counted_as_malformed(beat)

    def test_a_body_held_past_the_read_timeout_is_answered_408_and_counted(self):
        beat = Heartbeat(runtime="fake")
        url = self.serve_http(beat)
        # The handler reads the body with a 3 s timeout; the connection stays open with 8 bytes still to come.
        self.assertEqual(self.raw_webhook(url, b"Content-Length: 10\r\n", b"{}", finish=False), (408, "body timeout"))
        self.assert_counted_as_malformed(beat)

    def post_nested_too_deeply(self, signed):
        """(answer, log lines, stderr, counts) for a /webhook POST whose body nests further than json can parse.

        100000 brackets are well under MAX_BODY, and parsing them raises RecursionError."""
        beat = Heartbeat(runtime="fake")
        url = self.serve_http(beat)
        body = b"[" * 100_000
        head = b"Content-Length: %d\r\n" % len(body)
        if signed:
            head += b"Linear-Signature: %s\r\n" % self.signed(body)["Linear-Signature"].encode()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            answer = self.raw_webhook(url, head, body)
        lines = [json.loads(line) for line in out.getvalue().splitlines()]
        return answer, lines, err.getvalue(), beat.payload()["webhooks"]["counts"]

    def test_an_unsigned_body_nested_too_deeply_to_parse_is_answered_logged_and_counted(self):
        # Anyone who reaches the tunnel can send it. The log line's parse must survive it like any other bad body.
        answer, lines, err, counts = self.post_nested_too_deeply(signed=False)
        self.assertEqual(answer, (401, "invalid signature"))
        self.assertEqual(lines, [{"event": "webhook", "status": 401, "result": "invalid signature", "type": None,
                                  "action": None}])
        self.assertEqual(err, "")
        self.assertEqual(counts, {**dict.fromkeys(OUTCOMES, 0), "rejected": 1})

    def test_a_signed_body_nested_too_deeply_to_parse_is_invalid_json_not_a_receiver_error(self):
        # Only a holder of the signing secret can send it. Like any other body that is not JSON, it is malformed.
        answer, lines, err, counts = self.post_nested_too_deeply(signed=True)
        self.assertEqual(answer, (400, "invalid json"))
        self.assertEqual(lines, [{"event": "webhook", "status": 400, "result": "invalid json", "type": None,
                                  "action": None}])
        self.assertEqual(err, "")
        self.assertEqual(counts, {**dict.fromkeys(OUTCOMES, 0), "malformed": 1})
        self.assertEqual(self.receiver.results(), [])

    def test_probes_that_gave_up_before_serving_began_leave_no_traceback(self):
        """While pool.ensure() runs, serve's socket is bound and listening but nothing accepts. Each status build's
        /health probe then waits in the accept queue, times out and closes, and serve_forever answers it later, into
        a closed connection. Under launchd stderr is a log that is never rotated, so no traceback may follow."""
        server = make_server(self.receiver, port=0)
        self.addCleanup(server.server_close)
        # server_close joins only request threads that are not daemons: this way every handle_error call has
        # returned, and printed whatever it prints, before stderr is read.
        server.daemon_threads = False
        port = server.server_address[1]
        handler, errors, err = server.RequestHandlerClass, [], io.StringIO()
        send_headers, handle_error = handler.end_headers, server.handle_error

        def headers_then_a_pause(request):
            send_headers(request)
            # A closed probe's end answers this first write with a reset, and only a write after the reset has
            # arrived fails. The body follows microseconds later, so whether it fails is a race; the pause settles it.
            time.sleep(0.1)

        def recording(request, client_address):
            errors.append(sys.exc_info()[1])
            handle_error(request, client_address)

        server.handle_error = recording
        with patch.object(handler, "end_headers", headers_then_a_pause), contextlib.redirect_stderr(err):
            probes = [probe_health(port, timeout=0.2) for _ in range(4)]
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            thread.start()
            self.addCleanup(thread.join, 5)
            self.addCleanup(server.shutdown)
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as response:
                self.assertEqual(json.load(response)["status"], "FarmBot ready")
            server.shutdown()
            server.server_close()
        self.assertEqual([probe["error_type"] for probe in probes], ["TimeoutError"] * 4)
        # The probes' replies did meet their closed connections, and nothing was printed about it.
        self.assertTrue(errors)
        for error in errors:
            self.assertIsInstance(error, ConnectionError)
        self.assertEqual(err.getvalue(), "")

    def test_a_lost_clients_errors_print_nothing_and_any_other_error_its_traceback(self):
        server = make_server(self.receiver, port=0)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def printed(error):
            """What reaches stderr when answering a GET raises `error` before any reply."""
            err, reply = io.StringIO(), b""
            with patch.object(server.RequestHandlerClass, "do_GET", side_effect=error), \
                    contextlib.redirect_stderr(err), \
                    socket.create_connection(server.server_address, timeout=10) as client:
                client.sendall(b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
                # The server closes the connection only after handle_error has returned, so its output is complete.
                while chunk := client.recv(65536):
                    reply += chunk
            self.assertEqual(reply, b"")
            return err.getvalue()

        # A lost client, whichever of these errors the platform raises for it.
        for error in (ConnectionError(), BrokenPipeError(), ConnectionResetError(), ConnectionAbortedError()):
            with self.subTest(error=type(error).__name__):
                self.assertEqual(printed(error), "")
        # Any other error, OSError included, still reaches socketserver's default handle_error and its traceback.
        for error in (RuntimeError("a handler fault"), OSError("a handler fault")):
            with self.subTest(error=type(error).__name__):
                output = printed(error)
                self.assertIn("Exception occurred during processing of request from", output)
                self.assertIn(f"{type(error).__name__}: a handler fault", output)
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_address[1]}/health", timeout=5) as response:
            self.assertEqual(json.load(response)["status"], "FarmBot ready")

    def serve(self):
        """The receiver's listener on a free loopback port until the test ends; returns the port."""
        server = make_server(self.receiver, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def deliver(self, port, event, signed):
        """POST event to /webhook; returns the answer's status and result, and the one log line it wrote."""
        body = json.dumps(event).encode()
        self.assertLessEqual(len(body), MAX_BODY)
        headers = {"Linear-Signature": hmac.new(b"signing-secret", body, hashlib.sha256).hexdigest()} if signed else {}
        request = urllib.request.Request(f"http://127.0.0.1:{port}/webhook", data=body, headers=headers)
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    status, result = response.status, json.load(response)["status"]
            except urllib.error.HTTPError as error:
                with error:
                    status, result = error.code, json.load(error)["status"]
        [line] = log.getvalue().splitlines()
        return status, result, line

    def test_an_unsigned_body_cannot_put_arbitrary_values_into_the_log(self):
        """Unsigned deliveries are logged too, so type and action appear only as plain words of at most 40
        letters: anyone who reaches the endpoint could otherwise add megabytes to the service log with each
        request. The answer is unchanged."""
        port = self.serve()
        now = int(time.time() * 1000)
        largest = {**self.event(webhookTimestamp=now), "type": "", "action": {"created": ["x" * 1000]}}
        largest["type"] = "A" * (MAX_BODY - len(json.dumps(largest)))  # letters only, filling the body to MAX_BODY
        cases = {"the largest body the endpoint reads": largest,
                 "41 letters, and a list": {"type": "A" * 41, "action": ["created"]},
                 "a number and a boolean": {"type": 7, "action": True},
                 "a trailing newline and a trailing space": {"type": "Issue\n", "action": "update "},
                 "a space and a non-ASCII letter": {"type": "Agent Session", "action": "créated"}}
        for case, fields in cases.items():
            with self.subTest(case):
                status, result, line = self.deliver(port, {**self.event(webhookTimestamp=now), **fields}, signed=False)
                self.assertEqual((status, result), (401, "invalid signature"))
                self.assertEqual(json.loads(line), {"event": "webhook", "status": 401, "result": "invalid signature",
                                                    "type": None, "action": None})
                self.assertLess(len(line), 200)
        self.assertEqual(self.receiver.results(), [])

    def test_a_signed_delivery_still_logs_its_type_and_action(self):
        """The plain-word filter keeps the receiver's real vocabulary, up to a word of exactly 40 letters."""
        port = self.serve()
        now = int(time.time() * 1000)
        deliveries = [
            (self.event(webhookTimestamp=now), (200, "accepted", "AgentSessionEvent", "created")),
            (self.event("prompted", webhookTimestamp=now), (200, "accepted", "AgentSessionEvent", "prompted")),
            ({"type": "Issue", "action": "update", "organizationId": "org", "webhookTimestamp": now, "data": {"id": ISSUE}},
             (200, "ignored", "Issue", "update")),
            ({"type": "A" * 40, "action": "b" * 40, "webhookTimestamp": now}, (200, "ignored", "A" * 40, "b" * 40))]
        for event, expected in deliveries:
            with self.subTest(expected[2:]):
                status, result, line = self.deliver(port, event, signed=True)
                logged = json.loads(line)
                self.assertEqual((status, result), expected[:2])
                self.assertEqual((logged["status"], logged["result"], logged["type"], logged["action"]), expected)


class IssueNotificationTests(ReceiverBase):
    def notification(self, **changes):
        return {'type': 'Issue', 'action': 'update', 'organizationId': 'org',
                'webhookTimestamp': 100_000, 'data': {'id': ISSUE}, **changes}

    def test_issue_notification_uses_organization_identity_and_queues_tracked_only(self):
        self.assertEqual(self.receive(self.notification()), (200, 'ignored'))
        self.ledger.observe_issue(issue())
        self.assertEqual(self.receive(self.notification()), (200, 'accepted'))
        self.assertEqual(self.receive(self.notification()), (200, 'accepted'))
        self.assertTrue(self.ledger.status_check(ISSUE)['requested'])
        self.api.fetch_issue.assert_not_called()
        self.scheduler.stop.assert_not_called()

    def test_issue_notification_rejects_foreign_invalid_and_unsigned_inputs(self):
        self.assertEqual(self.receive(self.notification(organizationId='foreign'))[0], 403)
        self.assertEqual(self.receive(self.notification(data={'id': '../unsafe'}))[0], 400)
        self.assertEqual(self.receive(self.notification(), signature='bad')[0], 401)


class BotNameTests(ReceiverBase):
    """TestBot shares the Linear workspace with production, so every word the receiver says in a session
    names the instance's configured app. An acknowledgement that said FarmBot from TestBot made the
    operator stop the wrong bot."""

    def receiver_named(self, name):
        self.receiver = Receiver(self.db, "signing-secret", IDENTITY, self.api, lambda: Ledger(self.db),
                                 skills={"chat", "fix"}, scheduler=self.scheduler, bot_name=name)
        self.addCleanup(self.receiver.close)

    def mention(self, session, body):
        self.api.fetch_issue.return_value = issue(labels=["Bug"], delegate_id=None)
        return self.event(agentSession={"id": session, "issue": {"id": ISSUE, "identifier": "FARM-1", "url": "u"},
                                        "comment": {"body": body}})

    def answer_after_undelegation(self):
        """The fix item waits for input, the human removes the delegation, then replies in the session."""
        self.receive(); self.receiver.process_one()
        item = self.ledger.items_for_session("session-1")[0]
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.ledger.await_input(item["id"], token, "which server?")
        self.api.fetch_issue.return_value = issue(labels=["Bug"], delegate_id=None)
        self.receive(self.event("prompted", body="公共测试服")); self.receiver.process_one()
        return self.activities()[-1]["body"]

    def test_default_receiver_keeps_the_production_acknowledgements_byte_for_byte(self):
        self.receive(); self.receiver.process_one()
        self.assertEqual(self.activities()[-1]["body"], "FarmBot 已收到委派，正在排队处理这张修改卡。进展和草稿 PR 会更新在这里。")
        self.ledger.cancel(self.ledger.items_for_session("session-1")[0]["id"], "test")
        self.receive(self.mention("session-2", "@FarmBot 这个 bug 是客户端还是服务端的？")); self.receiver.process_one()
        self.assertEqual(self.activities()[-1]["body"], "FarmBot 已收到，正在查看。")

    def test_default_receiver_keeps_the_production_resume_and_error_text(self):
        self.assertEqual(self.answer_after_undelegation(),
                         "已保存回复；这张卡已不再委派给 FarmBot，这项工作即将停止。重新委派给 FarmBot 会从已有进度接着做。")
        self.api.fetch_issue.side_effect = KeyError("labels")
        self.receive(self.event(agentSession={"id": "session-9", "issue": {"id": ISSUE}})); self.receiver.process_one()
        self.assertEqual(self.activities()[-1], {"type": "error", "body": "FarmBot 处理这条消息时出错（KeyError），请稍后重试或联系维护者。"})

    def test_a_named_instance_acknowledges_as_itself(self):
        self.receiver_named("TestBot")
        self.receive(); self.receiver.process_one()
        self.assertEqual(self.activities()[-1]["body"], "TestBot 已收到委派，正在排队处理这张修改卡。进展和草稿 PR 会更新在这里。")
        self.ledger.cancel(self.ledger.items_for_session("session-1")[0]["id"], "test")
        self.receive(self.mention("session-2", "@TestBot 这个 bug 是客户端还是服务端的？")); self.receiver.process_one()
        self.assertEqual(self.activities()[-1]["body"], "TestBot 已收到，正在查看。")

    def test_a_named_instance_never_says_farmbot_in_resume_or_error_text(self):
        self.receiver_named("TestBot")
        self.assertEqual(self.answer_after_undelegation(),
                         "已保存回复；这张卡已不再委派给 TestBot，这项工作即将停止。重新委派给 TestBot 会从已有进度接着做。")
        self.api.fetch_issue.side_effect = KeyError("labels")
        self.receive(self.event(agentSession={"id": "session-9", "issue": {"id": ISSUE}})); self.receiver.process_one()
        self.assertEqual(self.activities()[-1]["body"], "TestBot 处理这条消息时出错（KeyError），请稍后重试或联系维护者。")
        self.assertFalse([a for a in self.activities() if "FarmBot" in a["body"]])


CHANGE = [{"group": "Bot", "label": "修改"}]
UI = [{"group": "Bot", "label": "UI"}]
CODE = [{"group": "Bot", "label": "Code"}]
FIX_ACK = "FarmBot 已收到委派，正在排队处理这张修改卡。进展和草稿 PR 会更新在这里。"
NO_BOT_LABEL = ("FarmBot 已收到。这张卡没有 Bot 标签，先以只读对话查看。需要修复或修改，请在这里回复（例如「修复」）；"
                "以后委派前先加上 Bot/修改 标签，就会直接开始处理。")


class BotRoutingReceiverTests(ReceiverBase):
    """D18 at the receiver, which reads the labels afresh for every event."""

    def running(self, *skills, **options):
        self.receiver = Receiver(self.db, "signing-secret", IDENTITY, self.api, lambda: Ledger(self.db),
                                 skills={"chat", "fix", *skills}, scheduler=self.scheduler, **options)
        self.addCleanup(self.receiver.close)

    def labelled(self, labels, groups, **changes):
        self.api.fetch_issue.return_value = issue(labels=labels, delegate_id=APP, label_groups=groups, **changes)

    def delegate(self, session="session-1"):
        self.receive(self.event(agentSession={"id": session, "issue": {"id": ISSUE, "identifier": "FARM-1", "url": "u"}}))
        self.receiver.process_one()
        return self.ledger.items_for_session(session)

    def conversation_elsewhere(self, session="session-0"):
        """A mention's conversation on the same issue: its item is the issue's active work."""
        self.receive(self.event(agentSession={"id": session, "issue": {"id": ISSUE, "identifier": "FARM-1", "url": "u"},
                                              "comment": {"body": "@FarmBot 这是什么问题？"}}))
        self.receiver.process_one()
        [item] = self.ledger.items_for_session(session)
        return item

    def finish(self, item):
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.ledger.finish(item["id"], token, "delivered", {"summary": "answered", "comment_action_id": None,
                                                            "verification": "answered in session", "prs": []})

    def test_a_bot_child_starts_its_worker_whatever_the_labels_for_people(self):
        self.running("fgui", "feature")
        for number, (labels, groups, skill, ack) in enumerate((
                (["Bug", "修改"], CHANGE, "fix", FIX_ACK),
                (["Improvement", "修改"], CHANGE, "fix", FIX_ACK),
                (["Bug", "UI"], UI, "fgui", "FarmBot 已收到委派，正在排队处理这张 UI 卡。进展、预览和草稿 PR 会更新在这里。"),
                (["Bug", "Code"], CODE, "feature",
                 "FarmBot 已收到委派，正在排队处理这张功能卡。进展、问题和草稿 PR 会更新在这里。"))):
            with self.subTest(labels=labels):
                self.labelled(labels, groups)
                [item] = self.delegate(f"session-{number}")
                self.assertEqual((item["skill"], item["state"]), (skill, "queued"))
                self.assertEqual(self.activities()[-1], {"type": "thought", "body": ack})
                self.ledger.cancel(item["id"], "next case")
        self.api.needs_more_info.assert_not_called()

    def test_a_card_without_a_bot_label_gets_a_first_message_that_says_how_to_get_a_fix(self):
        for number, labels in enumerate((["Bug"], ["Improvement"], ["Bug", "UI"], [])):
            with self.subTest(labels=labels):
                self.labelled(labels, [])
                [item] = self.delegate(f"session-{number}")
                self.assertEqual(item["skill"], "chat")
                self.assertEqual(self.activities()[-1], {"type": "thought", "body": NO_BOT_LABEL})
                self.assertEqual(self.ledger.issue_context(item["id"])["session_messages"], [])
                self.ledger.cancel(item["id"], "next case")
        self.api.needs_more_info.assert_not_called()

    def test_the_first_message_names_the_instance(self):
        self.running(bot_name="TestBot")
        self.labelled(["Bug"], [])
        self.delegate()
        self.assertEqual(self.activities()[-1]["body"], NO_BOT_LABEL.replace("FarmBot", "TestBot"))

    def test_without_fix_a_card_without_a_bot_label_gets_the_ordinary_acknowledgement(self):
        self.receiver = Receiver(self.db, "signing-secret", IDENTITY, self.api, lambda: Ledger(self.db),
                                 skills={"chat"}, scheduler=self.scheduler)
        self.addCleanup(self.receiver.close)
        self.labelled(["Bug"], [])
        self.assertEqual(self.delegate()[0]["skill"], "chat")
        self.assertEqual(self.activities()[-1]["body"], "FarmBot 已收到，正在查看。")

    def test_a_bot_child_this_instance_does_not_run_starts_an_explaining_conversation(self):
        self.labelled(["UI"], UI)
        [item] = self.delegate()
        self.assertEqual(item["skill"], "chat")
        body = self.activities()[-1]["body"]
        self.assertIn("Bot/UI，由 fgui 处理，但本实例没有启用 fgui", body)
        self.assertIn("本实例运行：chat、fix", body)

    def test_an_unknown_bot_child_starts_an_explaining_conversation(self):
        self.running("fgui", "feature")
        self.labelled(["Art"], [{"group": "Bot", "label": "Art"}])
        self.assertEqual(self.delegate()[0]["skill"], "chat")
        self.assertIn("这张卡带有 Bot/Art，无法对应到一项工作", self.activities()[-1]["body"])

    def test_the_group_is_still_read_under_its_old_name(self):
        self.running("feature")
        self.labelled(["Code"], [{"group": "功能", "label": "Code"}])
        self.assertEqual(self.delegate()[0]["skill"], "feature")

    def test_a_mention_on_a_bot_card_never_starts_bot_work(self):
        self.running("fgui", "feature")
        for number, (label, groups) in enumerate((("修改", CHANGE), ("UI", UI), ("Code", CODE))):
            with self.subTest(label=label):
                self.api.fetch_issue.return_value = issue(labels=[label], delegate_id=None, label_groups=groups)
                [item] = [self.conversation_elsewhere(f"session-{number}")]
                self.assertEqual(item["skill"], "chat")
                self.assertEqual(self.activities()[-1]["body"], "FarmBot 已收到，正在查看。")
                self.ledger.cancel(item["id"], "next case")

    def test_the_receiver_refuses_write_work_the_router_gives_a_mention(self):
        """The receiver's own guard, independent of the router: a mention on a Bot/修改 card that the router
        wrongly sent to fix creates no work, and the event is recorded as uncertain."""
        self.labelled(["修改"], CHANGE)
        with patch("agent.receiver.route", return_value=Decision("work", "fix")):
            self.receive(self.event(agentSession={"id": "session-2", "issue": {"id": ISSUE, "identifier": "FARM-1",
                                                                                "url": "u"},
                                                  "comment": {"body": "@FarmBot 修一下"}}))
            self.receiver.process_one()
        self.assertEqual(self.ledger.items_for_session("session-2"), [])
        result = self.receiver.results()[-1]
        self.assertEqual((result["status"], result["error"]), ("uncertain", "RuntimeError"))

    def test_an_unlabelled_delegation_takes_a_waiting_conversation_over_with_its_first_message(self):
        """Withdrawn-work design P4, C3: an unlabelled delegation while another session's conversation waits for an
        answer moves that conversation, with its messages, into the delegation's session. The delegator reads the
        card's first message, and the old session is told where its conversation went."""
        chat = self.conversation_elsewhere()
        token = self.ledger.claim(chat["id"], worker_id="w")["token"]
        self.ledger.pop_inbox(chat["id"], token)  # a message still unread would requeue the pause at once
        self.ledger.await_input(chat["id"], token, "哪个服？")
        self.assertEqual(self.ledger.item(chat["id"])["state"], "awaiting_input")
        self.labelled(["Bug"], [])
        [moved] = self.delegate()
        self.assertEqual((moved["skill"], moved["state"], moved["authority"]), ("chat", "queued", "delegation"))
        self.assertEqual(self.ledger.item(chat["id"])["state"], "cancelled")
        self.assertEqual([m["body"] for m in self.ledger.issue_context(moved["id"])["session_messages"]],
                         ["@FarmBot 这是什么问题？"])
        self.assertEqual([(call.args[0], call.args[1]) for call in self.api.create_activity.call_args_list[-2:]],
                         [("session-1", {"type": "thought", "body": NO_BOT_LABEL + "\n" + SUPERSEDE_SUFFIX}),
                          ("session-0", {"type": "response", "body": SUPERSEDED})])

    def claimed_fix_elsewhere(self, session="session-0"):
        """A fix a delegation of the Bot/修改 card started in `session`, claimed by its worker: (item, token)."""
        self.labelled(["修改"], CHANGE)
        [fix] = self.delegate(session)
        return fix, self.ledger.claim(fix["id"], worker_id="w")["token"]

    def past_the_grace(self):
        """The receiver's clock passes the flagged worker's deadline and five minutes more, and the deferred
        delegation is processed again (withdrawn-work design C2)."""
        later = time.time() + 1200 + 301
        self.receiver.clock = lambda: later
        self.assertTrue(self.receiver.process_one())

    def test_a_delegation_for_other_work_takes_a_waiting_conversation_over(self):
        """Withdrawn-work design P4, C3: the delegation starts its own work at once; the conversation it outranks
        ends, and its messages go to the new job."""
        self.running("feature")
        chat = self.conversation_elsewhere()
        self.labelled(["Code"], CODE)
        [item] = self.delegate()
        self.assertEqual((item["skill"], item["state"]), ("feature", "queued"))
        self.assertEqual(self.ledger.item(chat["id"])["state"], "cancelled")
        self.assertEqual([m["body"] for m in self.ledger.issue_context(item["id"])["session_messages"]],
                         ["@FarmBot 这是什么问题？"])

    def test_a_delegation_deferred_behind_a_worker_that_never_stops_starts_when_a_reply_reroutes_it(self):
        """D16 after a deferral (withdrawn-work design C2): the old worker outlives its grace, so the delegation's
        event ends saying so, and a reply once the worker has stopped routes the delegation as it would have."""
        self.running("feature")
        fix, token = self.claimed_fix_elsewhere()
        self.labelled(["Code"], CODE)
        self.assertEqual(self.delegate(), [])
        self.assertEqual(self.activities()[-1], {"type": "thought",
                                                 "body": DEFER_ACK.format(bot="FarmBot", skill="fix", minutes=20)})
        self.past_the_grace()
        self.assertEqual(self.activities()[-1], {"type": "response", "body": DEFER_STILL})
        self.assertEqual(self.ledger.items_for_session("session-1"), [])
        self.ledger.withdraw(fix["id"], token, delegated=True, closed=False)
        self.receive(self.event("prompted", body="现在开始")); self.receiver.process_one()
        [item] = self.ledger.items_for_session("session-1")
        self.assertEqual((item["skill"], item["state"]), ("feature", "queued"))
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.assertEqual(self.ledger.pop_inbox(item["id"], token), ["现在开始"])

    def test_a_reply_that_reroutes_a_card_without_a_bot_label_opens_the_conversation(self):
        fix, token = self.claimed_fix_elsewhere()
        self.labelled(["Bug"], [])
        self.assertEqual(self.delegate(), [])  # deferred behind the fix's worker
        self.past_the_grace()
        self.ledger.withdraw(fix["id"], token, delegated=True, closed=False)
        self.receive(self.event("prompted", body="请修复")); self.receiver.process_one()
        [item] = self.ledger.items_for_session("session-1")
        self.assertEqual(item["skill"], "chat")
        self.assertEqual(self.activities()[-1]["body"], "FarmBot 已收到，正在查看。")
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.assertEqual(self.ledger.pop_inbox(item["id"], token), ["请修复"])

    def test_a_reply_reroutes_only_while_the_issue_is_still_delegated(self):
        self.running("feature")
        fix, token = self.claimed_fix_elsewhere()
        self.labelled(["Code"], CODE)
        self.delegate()
        self.past_the_grace()
        self.ledger.withdraw(fix["id"], token, delegated=False, closed=False)
        self.api.fetch_issue.return_value = issue(labels=["Code"], delegate_id=None, label_groups=CODE)
        self.receive(self.event("prompted", body="现在开始")); self.receiver.process_one()
        self.assertEqual([item["skill"] for item in self.ledger.items_for_session("session-1")], ["chat"])

    def test_label_changes_after_a_job_exists_do_not_reroute(self):
        self.running("feature")
        self.labelled(["需求"], [])
        [chat] = self.delegate()
        self.finish(chat)
        self.labelled(["Code"], CODE)
        self.receive(self.event("prompted", body="那就做吧")); self.receiver.process_one()
        self.assertEqual([item["skill"] for item in self.ledger.items_for_session("session-1")], ["chat", "chat"])

    def test_a_reply_saved_after_undelegation_says_the_work_will_stop(self):
        self.running("feature")
        self.labelled(["Code"], CODE)
        [item] = self.delegate()
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.ledger.await_input(item["id"], token, "哪个服？")
        self.api.fetch_issue.return_value = issue(labels=["Code"], delegate_id=None, label_groups=CODE)
        self.receive(self.event("prompted", body="公共测试服")); self.receiver.process_one()
        self.assertEqual(self.activities()[-1]["body"], RESUME_UNDELEGATED.format(bot="FarmBot"))
        self.assertEqual(self.ledger.item(item["id"])["state"], "awaiting_input")

    def resolving_heads(self):
        """A client head that resolves, recording each read: a session the receiver pins reads it once."""
        heads = []
        self.receiver.worktrees = SimpleNamespace(remote_head=lambda repo, timeout=8: heads.append(repo) or "c" * 40)
        return heads

    def paused(self, item, question, reason="question"):
        """Claim `item`, read its messages as a worker does (an unread one would requeue the pause at once), and
        pause it on a human step."""
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.ledger.pop_inbox(item["id"], token)
        self.assertEqual(self.ledger.await_input(item["id"], token, question, reason=reason)["state"],
                         "awaiting_input")

    def mention_in(self, session, body):
        """A mention that opens `session` on the issue, processed."""
        self.receive(self.event(agentSession={"id": session, "issue": {"id": ISSUE, "identifier": "FARM-1", "url": "u"},
                                              "comment": {"body": body}}))
        self.receiver.process_one()

    def test_a_code_delegation_gets_its_own_acknowledgement_and_no_client_target(self):
        """Plan P6: the Farm-Client pin is a fix's reproduction baseline. A feature session gets none and its
        acknowledgement no target line, even where the client head resolves; a fix session still gets both."""
        self.running("feature")
        heads = self.resolving_heads()
        self.labelled(["Code"], CODE)
        [item] = self.delegate()
        self.assertEqual((item["skill"], item["target"], self.ledger.session("session-1")["target"]),
                         ("feature", None, None))
        self.assertEqual(self.activities()[-1], {"type": "thought", "body": "FarmBot 已收到委派，正在排队处理这张功能卡。"
                                                                          "进展、问题和草稿 PR 会更新在这里。"})
        self.assertEqual(heads, [])
        self.ledger.cancel(item["id"], "next case")
        self.labelled(["修改"], CHANGE)
        [fix] = self.delegate("session-2")
        self.assertEqual((fix["skill"], fix["target"]["commit_sha"]), ("fix", "c" * 40))
        self.assertEqual(self.activities()[-1]["body"], FIX_ACK + "\n目标已锁定：Farm-Client@ccccccc（公共测试服）。")
        self.assertEqual(heads, ["Farm-Client"])

    def test_a_code_delegation_that_takes_a_conversation_over_pins_nothing(self):
        self.running("feature")
        self.conversation_elsewhere()
        heads = self.resolving_heads()
        self.labelled(["Code"], CODE)
        [item] = self.delegate()
        self.assertEqual((item["skill"], item["target"], self.ledger.session("session-1")["target"]),
                         ("feature", None, None))
        self.assertEqual(self.activities()[-2]["body"], "FarmBot 已收到委派，正在排队处理这张功能卡。"
                                                        "进展、问题和草稿 PR 会更新在这里。\n" + SUPERSEDE_SUFFIX)
        self.assertEqual(heads, [])

    def test_a_code_delegation_deferred_behind_a_fix_pins_nothing_before_its_reroute(self):
        self.running("feature")
        fix, token = self.claimed_fix_elsewhere()
        heads = self.resolving_heads()
        self.labelled(["Code"], CODE)
        self.assertEqual(self.delegate(), [])
        self.assertEqual(self.activities()[-1]["body"], DEFER_ACK.format(bot="FarmBot", skill="fix", minutes=20))
        self.past_the_grace()
        self.ledger.withdraw(fix["id"], token, delegated=True, closed=False)
        self.receive(self.event("prompted", body="现在开始")); self.receiver.process_one()
        [item] = self.ledger.items_for_session("session-1")
        self.assertEqual((item["skill"], item["target"], self.ledger.session("session-1")["target"]),
                         ("feature", None, None))
        self.assertEqual(heads, [])

    def test_a_reply_to_feature_work_pins_nothing_either(self):
        """Plan P6 holds for the whole session: a reply that steers the feature job, or resumes it from a pause,
        reads no client head and adds no target line, although the session still has no target."""
        self.running("feature")
        heads = self.resolving_heads()
        self.labelled(["Code"], CODE)
        [item] = self.delegate()
        self.receive(self.event("prompted", body="先看协议")); self.receiver.process_one()
        self.assertEqual(self.activities()[-1]["body"], "已转给正在处理的 worker，会在下一次检查点读取。")
        self.paused(item, "配置发布了吗？", reason="waiting")
        answer = self.event("prompted", body="配置已经发布")
        answer["agentActivity"]["id"] = "act-2"  # a second reply is a second activity
        self.receive(answer); self.receiver.process_one()
        self.assertEqual(self.activities()[-1]["body"], "收到回复，继续处理。")
        self.assertEqual((heads, self.ledger.session("session-1")["target"]), ([], None))

    def test_a_mention_forwarded_to_paused_feature_work_pins_nothing(self):
        """Plan P6 for a mention in another session that resumes the paused feature job: its acknowledgement is
        about that job, so it carries no target line and the mention's session stores none. A mention that resumes
        a paused fix is pinned as before."""
        self.running("feature")
        heads = self.resolving_heads()
        self.labelled(["Code"], CODE)
        [feature] = self.delegate()
        self.paused(feature, "配置发布了吗？", reason="waiting")
        self.mention_in("session-9", "@FarmBot 配置已经发布")
        # The mention's acknowledgement, then the note in the resumed job's own thread (silent-delegation design A2).
        resumed = {"type": "thought", "body": RESUMED_ELSEWHERE}
        self.assertEqual(self.sent()[-2:], [
            ("session-9", {"type": "thought", "body": "收到回复，原工作项已恢复，worker 会先读取你的回答。"}),
            ("session-1", resumed)])
        self.assertEqual((heads, self.ledger.session("session-9")["target"]), ([], None))
        self.ledger.cancel(feature["id"], "next case")
        self.labelled(["修改"], CHANGE)
        [fix] = self.delegate("session-2")
        self.paused(fix, "哪个服？")
        self.mention_in("session-8", "@FarmBot 公共测试服")
        # The target line belongs to the session that was pinned, the mention's: the note carries none.
        self.assertEqual(self.sent()[-2:], [
            ("session-8", {"type": "thought", "body": "收到回复，原工作项已恢复，worker 会先读取你的回答。"
                                                     "\n目标已锁定：Farm-Client@ccccccc（公共测试服）。"}),
            ("session-2", resumed)])
        self.assertEqual(heads, ["Farm-Client", "Farm-Client"])

    def spend(self, item):
        """Use part of every automatic-retry allowance of `item`, as delayed retries and Unity recoveries do."""
        self.ledger.connection.execute("UPDATE work_items SET capacity_retries=2,publication_retries=3 WHERE id=?",
                                       (item["id"],))
        self.ledger.connection.execute("INSERT OR REPLACE INTO resource_job_retries(item_id,attempts,setup_attempts) "
                                       "VALUES(?,1,2)", (item["id"],))

    def allowances(self, item_id):
        item, recovery = self.ledger.item(item_id), self.ledger.issue_context(item_id)["resource_recovery"]
        return item["state"], (item["capacity_retries"], item["publication_retries"], recovery["attempts"],
                               recovery["setup_attempts"])

    def test_a_reply_or_a_mention_that_resumes_a_paused_feature_job_gives_it_fresh_allowances(self):
        """Spec §5.8: both receiver paths that resume a human gate, a reply in the session and a mention in another
        session forwarded to the paused job, start a new stage of a job that begins at an initial root."""
        self.running("feature")
        self.labelled(["Code"], CODE)
        [item] = self.delegate()
        self.spend(item)
        self.paused(item, "配置发布了吗？", reason="waiting")
        self.receive(self.event("prompted", body="配置已经发布")); self.receiver.process_one()
        self.assertEqual(self.activities()[-1]["body"], "收到回复，继续处理。")
        self.assertEqual(self.allowances(item["id"]), ("queued", (0, 0, 0, 0)))
        self.spend(item)
        self.paused(item, "配置发布了吗？", reason="waiting")
        self.mention_in("session-9", "@FarmBot 配置已经发布")
        self.assertEqual(self.sent()[-2:], [
            ("session-9", {"type": "thought", "body": "收到回复，原工作项已恢复，worker 会先读取你的回答。"}),
            ("session-1", {"type": "thought", "body": RESUMED_ELSEWHERE})])
        self.assertEqual(self.allowances(item["id"]), ("queued", (0, 0, 0, 0)))

    def test_a_reply_that_resumes_a_paused_fix_keeps_its_allowances(self):
        [item] = self.delegate()  # the base card, Bot/修改: a fix
        self.spend(item)
        self.paused(item, "哪个服？")
        self.receive(self.event("prompted", body="公共测试服")); self.receiver.process_one()
        self.assertEqual(self.allowances(item["id"]), ("queued", (2, 3, 1, 2)))


class WithdrawnWorkReceiverTests(ReceiverBase):
    """The withdrawn-work design at the receiver (P4, P5; tests under their §6 IDs): a new delegation session takes
    the card over, a waiting conversation moves to the thread that answers it, and a Stop reaches the work a session
    talked to."""
    labelled = BotRoutingReceiverTests.labelled
    delegate = BotRoutingReceiverTests.delegate
    conversation_elsewhere = BotRoutingReceiverTests.conversation_elsewhere
    paused = BotRoutingReceiverTests.paused
    mention_in = BotRoutingReceiverTests.mention_in
    claimed_fix_elsewhere = BotRoutingReceiverTests.claimed_fix_elsewhere
    past_the_grace = BotRoutingReceiverTests.past_the_grace

    def undelegated(self, labels=("Bug",), groups=()):
        """Every later read finds the card delegated to nobody."""
        self.api.fetch_issue.return_value = issue(labels=list(labels), delegate_id=None, label_groups=list(groups))

    def reply_in(self, session, body, activity="act-1", author=None):
        """A person's reply in `session`, processed."""
        event = self.event("prompted", body=body,
                           agentSession={"id": session, "issue": {"id": ISSUE, "identifier": "FARM-1", "url": "u"}})
        event["agentActivity"].update(id=activity, agentSessionId=session)
        if author is not None:
            event["agentActivity"]["user"] = author
        self.receive(event)
        self.receiver.process_one()

    def stop_in(self, session, activity="stop-1"):
        """A Stop pressed in `session`, processed; the reply it got."""
        event = self.event("prompted",
                           agentSession={"id": session, "issue": {"id": ISSUE, "identifier": "FARM-1", "url": "u"}})
        event["agentActivity"].update(id=activity, agentSessionId=session, signal="stop", content={"type": "prompt"})
        self.assertEqual(self.receive(event), (200, "stop received"))
        self.assertTrue(self.receiver.process_one())
        return self.activities()[-1]

    def waiting_chat(self, session="session-0", said=None):
        """The conversation a delegation of the unlabelled card opened in `session`, paused for an answer after reading
        `said`, a reply, when given."""
        self.labelled(["Bug"], [])
        [chat] = self.delegate(session)
        if said is not None:
            self.reply_in(session, said, activity=f"{session}-said")
        self.paused(chat, "哪个服？")
        return self.ledger.item(chat["id"])

    def waiting_fix(self, session="session-1"):
        """The fix a delegation of the Bot/修改 card started in `session`, paused for an answer."""
        self.labelled(["修改"], CHANGE)
        [fix] = self.delegate(session)
        self.paused(fix, "哪个服？")
        return self.ledger.item(fix["id"])

    def messages(self, item):
        return [message["body"] for message in self.ledger.issue_context(item["id"])["session_messages"]]

    def test_a_session_event_is_a_status_read_for_the_card(self):
        """Design DT1, P3: the receiver's fresh read of the card is a status read too. Finding the delegation clears
        the issue's mark; not finding it, while the delegation's work is active, asks for a status read now."""
        fix = self.waiting_fix()
        self.ledger.mark_undelegated(ISSUE, 1.0)
        before = time.time()
        self.reply_in("session-1", "公共测试服")
        self.assertIsNone(self.ledger.status_check(ISSUE)["undelegated_since"])
        # The read also records that the delegation is back, from its own start (silent-delegation design §3.1).
        episode = self.ledger.episode(ISSUE)
        self.assertEqual((episode["state"], episode["mark"]), ("waiting", 1.0))
        self.assertTrue(before <= episode["since"] <= time.time())
        self.ledger.connection.execute("UPDATE issue_checks SET requested=0,due_at=? WHERE issue_id=?",
                                       (time.time() + 60, ISSUE))
        self.undelegated(("Bug", "修改"), CHANGE)
        self.reply_in("session-1", "还有一点", activity="act-2")
        check = self.ledger.status_check(ISSUE)
        self.assertEqual((check["requested"], check["due_at"]), (1, 0))
        self.assertEqual(self.ledger.item(fix["id"])["state"], "queued")

    def test_a2_reply_in_the_open_session_after_cancel_starts_a_mention_chat(self):
        fix = self.waiting_fix()
        self.ledger.cancel(fix["id"], "Linear delegation removed")  # what the lifecycle's confirming read does
        self.undelegated()
        self.reply_in("session-1", "还在吗？")
        chat = self.ledger.active_item_for_session("session-1")
        self.assertEqual((chat["skill"], chat["state"], chat["authority"]), ("chat", "queued", "mention"))
        self.assertEqual(self.activities()[-1], {"type": "thought", "body": "FarmBot 已收到，正在查看。"})
        self.assertEqual(self.messages(chat), ["还在吗？"])

    def test_a2_stop_in_the_open_session_after_cancel_says_it_already_stopped(self):
        fix = self.waiting_fix()
        self.ledger.cancel(fix["id"], "Linear delegation removed")
        self.undelegated()
        self.assertEqual(self.stop_in("session-1"), {"type": "response", "body": STOP_ALREADY})
        self.scheduler.stop.assert_not_called()

    def test_a2_reply_to_a_waiting_chat_during_the_window_resumes_it_as_mention(self):
        chat = self.waiting_chat("session-1")
        self.undelegated()
        self.reply_in("session-1", "公共测试服")
        current = self.ledger.item(chat["id"])
        self.assertEqual((current["state"], current["authority"]), ("queued", "mention"))
        self.assertEqual(self.activities()[-1], {"type": "thought", "body": "收到回复，继续处理。"})
        # The person now talks to FarmBot without a delegation: a confirmed removal leaves the answer alone.
        now = [time.time()]
        reads = SimpleNamespace(app_user_id=APP, issue_status=lambda issue_id: {
            "id": issue_id, "status": "Todo", "status_type": "unstarted", "archived": False, "delegate_id": None,
            "updated_at": "2026-09-21T00:00:00Z"})
        lifecycle = Lifecycle(self.ledger, reads, self.scheduler, clock=lambda: now[0])
        lifecycle.refresh(ISSUE)
        now[0] += 60
        lifecycle.refresh(ISSUE)
        self.assertEqual(self.ledger.item(chat["id"])["state"], "queued")
        self.scheduler.stop.assert_not_called()

    def test_a6_redelegation_supersedes_without_any_status_read(self):
        chat = self.waiting_chat("session-0", said="按钮点了没反应")
        self.labelled(["修改"], CHANGE)
        [fix] = self.delegate("session-1")
        self.assertEqual(self.ledger.item(chat["id"])["state"], "cancelled")
        self.assertEqual((fix["skill"], fix["state"], fix["authority"]), ("fix", "queued", "delegation"))
        self.assertEqual(self.messages(fix), ["按钮点了没反应"])
        self.assertEqual(self.sent()[-2:], [("session-1", {"type": "thought", "body": FIX_ACK + "\n" + SUPERSEDE_SUFFIX}),
                                            ("session-0", {"type": "response", "body": SUPERSEDED})])
        self.api.issue_status.assert_not_called()

    def test_b1_mention_reaches_a_fix_parked_in_an_unreachable_session_and_stop_follows_it(self):
        fix = self.waiting_fix("session-1")
        self.mention_in("session-9", "@FarmBot 公共测试服")
        self.assertEqual(self.ledger.item(fix["id"])["state"], "queued")
        self.assertEqual(self.said_in("session-9", "thought"), ["收到回复，原工作项已恢复，worker 会先读取你的回答。"])
        self.assertEqual(self.ledger.connection.execute("SELECT forwarded_item FROM sessions WHERE session_id=?",
                                                        ("session-9",)).fetchone()[0], fix["id"])
        self.assertEqual(self.stop_in("session-9"),
                         {"type": "response", "body": STOP_ELSEWHERE.format(identifier="FARM-1")})
        self.assert_stopped(fix, notice=STOPPED_ELSEWHERE)

    def test_c1_new_delegation_supersedes_a_waiting_item_in_one_transaction(self):
        self.labelled(["修改"], CHANGE)
        [old] = self.delegate("session-0")
        self.reply_in("session-0", "安卓上也有", activity="a-1", author=DESIGNER)
        self.reply_in("session-0", "iOS 没有", activity="a-2", author=OWNER)
        self.paused(old, "哪个服？")
        [new] = self.delegate("session-1")
        self.assertEqual(self.ledger.item(old["id"])["state"], "cancelled")
        self.assertEqual((new["state"], new["predecessor_id"]), ("queued", old["id"]))
        self.assertEqual([(m["body"], m["author"]) for m in self.ledger.issue_context(new["id"])["session_messages"]],
                         [("安卓上也有", DESIGNER), ("iOS 没有", OWNER)])
        self.assertEqual(self.sent()[-2:], [("session-1", {"type": "thought", "body": FIX_ACK + "\n" + SUPERSEDE_SUFFIX}),
                                            ("session-0", {"type": "response", "body": SUPERSEDED})])
        self.assertEqual(self.receiver.results()[-1]["status"], "done")

    def test_c2_new_delegation_defers_behind_a_claimed_fix_then_continues_it(self):
        fix, token = self.claimed_fix_elsewhere()
        self.assertEqual(self.delegate("session-1"), [])
        flagged = self.ledger.item(fix["id"])
        self.assertEqual((flagged["state"], flagged["withdraw_reason"]), ("running", "superseded"))
        self.assertEqual(self.sent()[-1], ("session-1", {"type": "thought",
                                                         "body": DEFER_ACK.format(bot="FarmBot", skill="fix", minutes=20)}))
        self.assertEqual(self.receiver.results()[-1]["status"], "deferred")
        deferred_ack = self.api.create_activity.call_args_list[-1].kwargs["activity_id"]
        self.assertFalse(self.receiver.process_one())  # nothing to do while the old worker runs
        self.ledger.withdraw(fix["id"], token, delegated=True, closed=False)
        self.assertTrue(self.receiver.process_one())
        [new] = self.ledger.items_for_session("session-1")
        self.assertEqual((new["skill"], new["state"], new["predecessor_id"]), ("fix", "queued", fix["id"]))
        answer = self.api.create_activity.call_args_list[-1]
        self.assertEqual((answer.args[0], answer.args[1]), ("session-1", {"type": "thought", "body": FIX_ACK}))
        self.assertNotEqual(answer.kwargs["activity_id"], deferred_ack)
        self.assertEqual(self.receiver.results()[-1]["status"], "done")

    def test_c2_deferred_event_reports_once_if_the_old_worker_is_still_running(self):
        fix, _ = self.claimed_fix_elsewhere()
        self.delegate("session-1")
        self.past_the_grace()
        self.assertEqual(self.sent()[-1], ("session-1", {"type": "response", "body": DEFER_STILL}))
        self.assertEqual(self.ledger.items_for_session("session-1"), [])
        self.assertEqual(self.receiver.results()[-1]["status"], "done")
        self.assertEqual(self.ledger.item(fix["id"])["state"], "running")
        count = len(self.sent())
        self.assertFalse(self.receiver.process_one())
        self.assertEqual(len(self.sent()), count)

    def test_c2_deferred_delegation_starts_nothing_once_the_card_is_no_longer_delegated(self):
        """Design C2 runs a deferred delegation as the delegation it was, which only a card still delegated here
        allows. When the person removed the delegation while the old worker stopped, the event answers once and
        starts nothing: no write job without a delegation, and no conversation nobody asked for."""
        fix, token = self.claimed_fix_elsewhere()
        self.assertEqual(self.delegate("session-1"), [])  # deferred behind the claimed fix
        self.ledger.withdraw(fix["id"], token, delegated=False, closed=False)
        self.undelegated(("Bug", "修改"), CHANGE)
        self.assertTrue(self.receiver.process_one())
        self.assertEqual(self.ledger.items_for_session("session-1"), [])
        self.assertIsNone(self.ledger.active_item_for_issue(ISSUE))
        self.assertEqual(self.sent()[-1],
                         ("session-1", {"type": "response", "body": DEFER_UNDELEGATED.format(bot="FarmBot")}))
        self.assertEqual(self.receiver.results()[-1]["status"], "done")
        self.assertFalse(self.receiver.process_one())

    def test_c2_deferred_delegation_past_the_grace_on_an_undelegated_card_forwards_nothing(self):
        """The same when the old worker outlives its grace: the event forwards no empty message to that worker and
        does not report on its stopping; it says that nothing will start."""
        fix, token = self.claimed_fix_elsewhere()
        self.delegate("session-1")
        self.undelegated(("Bug", "修改"), CHANGE)
        self.past_the_grace()
        self.assertEqual(self.sent()[-1],
                         ("session-1", {"type": "response", "body": DEFER_UNDELEGATED.format(bot="FarmBot")}))
        self.assertEqual(self.ledger.items_for_session("session-1"), [])
        self.assertEqual(self.ledger.pop_inbox(fix["id"], token), [])
        self.assertEqual(self.receiver.results()[-1]["status"], "done")

    def test_c2_running_chat_is_superseded_at_once(self):
        chat = self.conversation_elsewhere()
        token = self.ledger.claim(chat["id"], worker_id="w")["token"]
        [fix] = self.delegate("session-1")
        self.assertEqual(self.ledger.item(chat["id"])["state"], "cancelled")
        with self.assertRaises(LedgerError):
            self.ledger.renew(chat["id"], token)
        self.assertEqual((fix["skill"], fix["state"]), ("fix", "queued"))
        # The receiver only writes the ledger: the scheduler's next tick kills the worker, after the acknowledgement.
        self.scheduler.stop.assert_not_called()

    def test_c3_delegation_outranks_a_waiting_mention_chat(self):
        chat = self.conversation_elsewhere()
        self.paused(chat, "哪个服？")
        [fix] = self.delegate("session-1")
        self.assertEqual(self.ledger.item(chat["id"])["state"], "cancelled")
        self.assertEqual((fix["skill"], fix["state"], fix["predecessor_id"]), ("fix", "queued", None))
        self.assertEqual(self.messages(fix), ["@FarmBot 这是什么问题？"])
        self.assertEqual(self.sent()[-2:], [("session-1", {"type": "thought", "body": FIX_ACK + "\n" + SUPERSEDE_SUFFIX}),
                                            ("session-0", {"type": "response", "body": SUPERSEDED})])

    def test_c4_local_item_is_superseded_with_an_issue_comment(self):
        self.ledger.observe_issue(issue(labels=["Bug", "修改"], delegate_id=APP, label_groups=CHANGE))
        self.ledger.ensure_session(f"local-{ISSUE}", ISSUE, True)
        local = self.ledger.create_work_item(issue_id=ISSUE, session_id=f"local-{ISSUE}", skill="fix",
                                             authority="delegation")
        [fix] = self.delegate("session-1")
        self.assertEqual(self.ledger.item(local["id"])["state"], "cancelled")
        self.assertEqual(fix["predecessor_id"], local["id"])
        self.api.create_comment.assert_called_once_with(ISSUE, SUPERSEDED)
        self.assertEqual([session for session, _ in self.sent()], ["session-1"])

    def operators_conversation_taken_over(self, claimed):
        """Design K6: a conversation the operator enqueued (a `local-` session, operator authority), queued or
        `claimed` by its worker, with one message, and then a Bot/修改 delegation in session-1. Returns the
        conversation and the fix the delegation started."""
        self.ledger.observe_issue(issue(labels=["Bug", "修改"], delegate_id=APP, label_groups=CHANGE))
        self.ledger.ensure_session(f"local-{ISSUE}", ISSUE, True)
        local = self.ledger.create_work_item(issue_id=ISSUE, session_id=f"local-{ISSUE}", skill="chat",
                                             authority="operator")
        self.ledger.push_inbox(local["id"], "维护者：请看一下这张卡")
        token = self.ledger.claim(local["id"], worker_id="w")["token"] if claimed else None
        [fix] = self.delegate("session-1")
        if token is not None:
            with self.assertRaises(LedgerError):
                self.ledger.renew(local["id"], token)
        return self.ledger.item(local["id"]), fix

    def assert_taken_over_with_one_card_note(self, local, fix):
        """The conversation ended, the fix carries its message and the delegation's authority, and the card, which is
        where a `local-` job reports, got the one note; the new session was acknowledged as a takeover."""
        self.assertEqual(local["state"], "cancelled")
        self.assertEqual((fix["skill"], fix["state"], fix["authority"], fix["predecessor_id"]),
                         ("fix", "queued", "delegation", None))
        self.assertEqual(self.messages(fix), ["维护者：请看一下这张卡"])
        self.api.create_comment.assert_called_once_with(ISSUE, SUPERSEDED)
        self.assertEqual(self.sent(), [("session-1", {"type": "thought", "body": FIX_ACK + "\n" + SUPERSEDE_SUFFIX})])
        self.assertEqual(self.receiver.results()[-1]["status"], "done")

    def test_k6_delegation_takes_a_queued_operator_conversation_over(self):
        self.assert_taken_over_with_one_card_note(*self.operators_conversation_taken_over(claimed=False))

    def test_k6_delegation_takes_a_running_operator_conversation_over(self):
        """A claimed conversation is superseded at once too (C2); the scheduler's next tick kills its worker."""
        self.assert_taken_over_with_one_card_note(*self.operators_conversation_taken_over(claimed=True))
        self.scheduler.stop.assert_not_called()

    def test_c5_mention_supersedes_a_waiting_chat_into_its_own_session(self):
        chat = self.waiting_chat("session-1")
        self.mention_in("session-9", "@FarmBot 公共测试服")
        self.assertEqual(self.ledger.item(chat["id"])["state"], "cancelled")
        moved = self.ledger.active_item_for_session("session-9")
        self.assertEqual((moved["skill"], moved["state"], moved["authority"]), ("chat", "queued", "delegation"))
        self.assertEqual(self.messages(moved), ["@FarmBot 公共测试服"])
        # A mention moved it, not a new delegation: the old thread's note names none (MOVED_THREAD, not SUPERSEDED).
        self.assertEqual(self.sent()[-2:], [("session-9", {"type": "thought", "body": "FarmBot 已收到，正在查看。"}),
                                            ("session-1", {"type": "response", "body": MOVED_THREAD})])

    def test_c5_stop_where_a_mention_moved_the_conversation_from_names_no_delegation(self):
        """A mention answers a mention's waiting conversation, which moves to the mention's thread. The old thread
        is told it moved, and a Stop there says where it went, without naming a delegation session: none exists."""
        chat = self.conversation_elsewhere()
        self.paused(chat, "哪个服？")
        self.mention_in("session-9", "@FarmBot 公共测试服")
        moved = self.ledger.active_item_for_session("session-9")
        self.assertEqual((moved["skill"], moved["state"], moved["authority"]), ("chat", "queued", "mention"))
        self.assertEqual(self.sent()[-1], ("session-0", {"type": "response", "body": MOVED_THREAD}))
        self.assertEqual(self.stop_in("session-0"), {"type": "response", "body": STOP_MOVED_THREAD})
        self.scheduler.stop.assert_not_called()

    def test_c5_an_operators_conversation_a_mention_moved_is_noted_on_the_card(self):
        self.ledger.observe_issue(issue(labels=["Bug"], delegate_id=APP))
        self.ledger.ensure_session(f"local-{ISSUE}", ISSUE, True)
        local = self.ledger.create_work_item(issue_id=ISSUE, session_id=f"local-{ISSUE}", skill="chat",
                                             authority="operator")
        self.mention_in("session-9", "@FarmBot 看一下")
        self.assertEqual(self.ledger.item(local["id"])["state"], "cancelled")
        self.assertEqual(self.ledger.active_item_for_session("session-9")["authority"], "mention")
        self.api.create_comment.assert_called_once_with(ISSUE, MOVED_THREAD)

    def test_a_reply_that_moves_a_conversation_back_to_an_older_thread_names_no_delegation(self):
        """A delegation took a waiting conversation over (C3), and a reply in the older thread then answers it there,
        which moves it back. The newer thread is told the conversation moved on, not that a delegation took it."""
        self.waiting_chat("session-0")
        [taken] = self.delegate("session-1")
        self.assertEqual(self.sent()[-1], ("session-0", {"type": "response", "body": SUPERSEDED}))
        self.paused(taken, "哪个服？")
        self.reply_in("session-0", "公共测试服")
        self.assertEqual(self.ledger.active_item_for_session("session-0")["skill"], "chat")
        self.assertEqual(self.ledger.item(taken["id"])["state"], "cancelled")
        self.assertEqual(self.sent()[-1], ("session-1", {"type": "response", "body": MOVED_THREAD}))

    def test_c5_mention_to_a_parked_undelegated_fix_says_it_will_not_continue(self):
        fix = self.waiting_fix("session-1")
        self.undelegated(("Bug", "修改"), CHANGE)
        self.mention_in("session-9", "@FarmBot 公共测试服")
        self.assertEqual(self.ledger.item(fix["id"])["state"], "awaiting_input")
        self.assertEqual(self.ledger.issue_context(fix["id"])["inbox_pending"], 1)
        self.assertEqual(self.activities()[-1],
                         {"type": "thought", "body": FORWARD_PARKED_UNDELEGATED.format(bot="FarmBot", skill="fix")})

    def test_a_mention_to_a_withdrawing_worker_says_the_work_is_stopping(self):
        fix, token = self.claimed_fix_elsewhere("session-1")
        self.ledger.flag_withdrawal(fix["id"], "undelegated", time.time() + 1200)
        self.undelegated(("Bug", "修改"), CHANGE)
        self.mention_in("session-9", "@FarmBot 还要改吗？")
        self.assertEqual(self.activities()[-1], {"type": "thought", "body": FORWARD_WITHDRAWING})
        self.assertEqual(self.ledger.pop_inbox(fix["id"], token), ["@FarmBot 还要改吗？"])

    def test_d2_stop_in_the_latest_delegation_session_stops_work_elsewhere(self):
        fix, _ = self.claimed_fix_elsewhere()
        self.delegate("session-1")  # deferred behind the claimed fix
        self.assertEqual(self.stop_in("session-1"),
                         {"type": "response", "body": STOP_ELSEWHERE.format(identifier="FARM-1")})
        self.assert_stopped(fix, notice=STOPPED_ELSEWHERE)
        self.assertEqual([row["status"] for row in self.receiver.results() if row["session_id"] == "session-1"],
                         ["cancelled"])
        self.assertFalse(self.receiver.process_one())

    def test_d2_stop_in_a_superseded_session_points_to_the_new_one(self):
        self.waiting_fix("session-0")
        self.delegate("session-1")
        self.assertEqual(self.stop_in("session-0"), {"type": "response", "body": STOP_MOVED})
        self.scheduler.stop.assert_not_called()

    def test_d3_a_stop_whose_reply_fails_has_still_stopped_the_work(self):
        """Design D3, unchanged by P5: the work is stopped before the reply is sent, so a reply Linear refuses leaves
        the Stop recorded as uncertain, not the work running."""
        fix = self.waiting_fix("session-1")
        self.api.create_activity.side_effect = RuntimeError("linear down")
        event = self.event("prompted",
                           agentSession={"id": "session-1", "issue": {"id": ISSUE, "identifier": "FARM-1", "url": "u"}})
        event["agentActivity"].update(id="stop-1", signal="stop", content={"type": "prompt"})
        self.assertEqual(self.receive(event), (200, "stop received"))
        self.assertTrue(self.receiver.process_one())
        self.assert_stopped(fix)
        with self.receiver.lock:
            recorded = self.receiver.db.execute("SELECT status,error FROM stop_requests").fetchall()
        self.assertEqual([tuple(row) for row in recorded], [("uncertain", "RuntimeError")])

    def test_k4_removing_the_bot_label_from_a_delegated_card_stops_nothing(self):
        """Design K4: a label is not a stop signal. With the Bot label gone from the card, which is still delegated, a
        reply resumes the waiting fix as before, and nothing asks for a status read."""
        fix = self.waiting_fix("session-1")
        self.labelled(["Bug"], [])
        self.reply_in("session-1", "公共测试服")
        self.assertEqual(self.ledger.item(fix["id"])["state"], "queued")
        self.assertEqual(self.activities()[-1], {"type": "thought", "body": "收到回复，继续处理。"})
        self.assertEqual(self.ledger.status_check(ISSUE)["requested"], 0)

    def test_j5_forward_that_meets_a_terminal_item_reroutes_to_a_new_chat(self):
        fix = self.waiting_fix("session-1")
        push = self.receiver.ledger.push_inbox

        def cancelled_first(item_id, *args, **kwargs):
            self.receiver.ledger.push_inbox = push
            self.ledger.cancel(fix["id"], "Linear stop")
            return push(item_id, *args, **kwargs)
        self.receiver.ledger.push_inbox = cancelled_first
        self.mention_in("session-9", "@FarmBot 进展如何？")
        chat = self.ledger.active_item_for_session("session-9")
        self.assertEqual((chat["skill"], chat["state"]), ("chat", "queued"))
        self.assertEqual(self.messages(chat), ["@FarmBot 进展如何？"])
        self.assertEqual(self.receiver.results()[-1]["status"], "done")

    def test_supersede_race_with_request_repair_reroutes_once(self):
        """Design X1: the delegation chat's worker hands over to a fix between the receiver's routing and its
        supersede. The supersede finds the chat gone and routes again, and the new session takes the fix over."""
        self.labelled(["Bug"], [])
        [chat] = self.delegate("session-0")
        self.reply_in("session-0", "请修复", activity="r-1")
        token = self.ledger.claim(chat["id"], worker_id="w")["token"]
        message = self.ledger.issue_context(chat["id"])["session_messages"][-1]["id"]
        self.labelled(["修改"], CHANGE)
        supersede = self.receiver.ledger.supersede
        repaired = []

        def repair_first(*args, **kwargs):
            self.receiver.ledger.supersede = supersede
            repaired.append(self.ledger.request_repair(chat["id"], token, message, APP, "Confirmed repair.",
                                                       delegate_id=APP))
            return supersede(*args, **kwargs)
        self.receiver.ledger.supersede = repair_first
        [fix] = self.delegate("session-1")
        active = [row["id"] for row in self.ledger.status()["items"]
                  if row["state"] in ("queued", "running", "awaiting_input", "awaiting_resource")]
        self.assertEqual(active, [fix["id"]])
        self.assertEqual(self.ledger.item(repaired[0]["id"])["state"], "cancelled")
        self.assertEqual(fix["predecessor_id"], repaired[0]["id"])
        self.assertEqual(self.receiver.results()[-1]["status"], "done")


class OwnThreadReceiverTests(ReceiverBase):
    """Silent-delegation design Part A at the receiver (§4.1; tests TA1-TA9): FarmBot does not leave its own thread
    waiting. Work a Stop ends, or a message resumes, from another thread gets a last word in its own thread, and the
    thread a takeover or a move leaves is told even when the new session's acknowledgement fails."""
    labelled = BotRoutingReceiverTests.labelled
    delegate = BotRoutingReceiverTests.delegate
    conversation_elsewhere = BotRoutingReceiverTests.conversation_elsewhere
    finish = BotRoutingReceiverTests.finish
    paused = BotRoutingReceiverTests.paused
    mention_in = BotRoutingReceiverTests.mention_in
    claimed_fix_elsewhere = BotRoutingReceiverTests.claimed_fix_elsewhere
    reply_in = WithdrawnWorkReceiverTests.reply_in
    stop_in = WithdrawnWorkReceiverTests.stop_in
    undelegated = WithdrawnWorkReceiverTests.undelegated
    waiting_fix = WithdrawnWorkReceiverTests.waiting_fix

    STOPPED_HERE = "已停止 FARM-1 上的工作，worker 已终止，占用的资源在静默检查后释放。"
    STOPPED_THERE = STOP_ELSEWHERE.format(identifier="FARM-1")
    RESUMED = "收到回复，原工作项已恢复，worker 会先读取你的回答。"
    FAILED_MESSAGE = "FarmBot 处理这条消息时出错（RuntimeError），请稍后重试或联系维护者。"

    def real_scheduler(self):
        """The receiver stops work through a real scheduler that posts to the receiver's API double, so sent() shows
        the scheduler's notices among the receiver's replies, in order. Returns its launcher double."""
        launcher = FakeLauncher(Path(self.tmp.name) / "runs")
        self.scheduler = Scheduler(self.ledger, launcher, SKILLS, FakeWorktrees(Path(self.tmp.name) / "wt"),
                                   skill_root=ROOT / "skills", db_path=self.db, runtime_name="fake", host="h",
                                   api=self.api)
        self.receiver.scheduler = self.scheduler
        return launcher

    def pending_heartbeat(self, item):
        """A session heartbeat reserved for `item` and not sent yet, which a cancel turns into a closing response of
        its own unless the cancel drops it. Returns the reporter that would send it."""
        progress = SessionProgress(self.ledger, self.api)
        item = self.ledger.item(item["id"])
        self.ledger.connection.execute(
            "INSERT INTO session_progress(item_id,due_at,activity_id,content,status_key) VALUES(?,0,?,?,?)",
            (item["id"], "heartbeat-1", json.dumps({"type": "thought", "body": "工作仍在排队。"}),
             f"{item['id']}:{item['state']}:{item['generation']}"))
        return progress

    def heartbeat(self, item):
        return self.ledger.connection.execute("SELECT 1 FROM session_progress WHERE item_id=?",
                                              (item["id"],)).fetchone()

    def conversation_about_to_hand_over(self, thread="session-9"):
        """A mention's conversation in `thread`, claimed, on a card whose delegation's fix in session-1 was stopped
        earlier: (conversation, token, the id of its request). `hand_over` continues that fix from it."""
        stopped = self.waiting_fix("session-1")
        self.ledger.cancel(stopped["id"], "an earlier stop")
        self.mention_in(thread, "@FarmBot 请接着修")
        [chat] = self.ledger.items_for_session(thread)
        token = self.ledger.claim(chat["id"], worker_id="chat")["token"]
        return chat, token, self.ledger.issue_context(chat["id"])["session_messages"][-1]["id"]

    def hand_over(self, chat, token, message):
        """The conversation hands over to a fix, which runs in the delegation's thread, not the conversation's."""
        fix = self.ledger.resume_work(chat["id"], token, message, APP)
        self.assertEqual((fix["skill"], fix["state"], fix["session_id"]), ("fix", "queued", "session-1"))
        self.assertEqual(self.ledger.active_item_for_session(chat["session_id"])["id"], fix["id"])
        return fix

    def refusing(self, session):
        """Linear refuses every activity in `session` from now on, and takes the others."""
        def create_activity(session_id, content, activity_id=None):
            if session_id == session:
                raise RuntimeError("linear down")
            return {"success": True}
        self.api.create_activity.side_effect = create_activity

    def answered_slowly(self, meanwhile):
        """Linear takes a while over each activity in session-9, and `meanwhile` happens before it answers."""
        def create_activity(session_id, content, activity_id=None):
            if session_id == "session-9":
                meanwhile()
            return {"success": True}
        self.api.create_activity.side_effect = create_activity

    def test_stop_in_a_forwarding_thread_closes_the_stopped_works_own_thread(self):
        """A1, S23, the incident replayed: a fix waits in its delegation thread, a mention in another thread answers
        it, and Stop is pressed in the mention's thread. The fix's own thread is closed by one response, and the
        heartbeat it had pending is dropped, so nothing follows that response."""
        self.real_scheduler()
        fix = self.waiting_fix("session-1")
        self.mention_in("session-9", "@FarmBot 公共测试服")
        progress = self.pending_heartbeat(fix)
        self.assertEqual(self.stop_in("session-9"), {"type": "response", "body": self.STOPPED_THERE})
        self.assertEqual(self.ledger.item(fix["id"])["state"], "cancelled")
        self.assertEqual(self.sent()[-2:], [("session-1", {"type": "response", "body": STOPPED_ELSEWHERE}),
                                            ("session-9", {"type": "response", "body": self.STOPPED_THERE})])
        self.assertIsNone(self.heartbeat(fix))
        self.assertFalse(progress.tick())
        self.assertEqual(self.said_in("session-1"), [STOPPED_ELSEWHERE])
        self.api.create_comment.assert_not_called()

    def test_stop_in_the_latest_delegation_thread_closes_the_works_own_thread(self):
        """A1: a Stop in the card's latest delegation thread, whose delegation waits behind a claimed fix of an older
        thread, stops that fix. Its worker is signalled and its own thread is closed."""
        launcher = self.real_scheduler()
        fix, token = self.claimed_fix_elsewhere("session-0")
        self.assertEqual(self.delegate("session-1"), [])  # deferred behind the claimed fix
        self.assertEqual(self.stop_in("session-1"), {"type": "response", "body": self.STOPPED_THERE})
        self.assertEqual(self.ledger.item(fix["id"])["state"], "cancelled")
        with self.assertRaises(LedgerError):
            self.ledger.renew(fix["id"], token)
        self.assertEqual(launcher.stopped, [fix["id"]])
        self.assertEqual(self.sent()[-2:], [("session-0", {"type": "response", "body": STOPPED_ELSEWHERE}),
                                            ("session-1", {"type": "response", "body": self.STOPPED_THERE})])
        self.assertEqual(self.said_in("session-0"), [STOPPED_ELSEWHERE])

    def test_stop_in_the_own_thread_posts_one_reply_and_no_notice(self):
        """A1: a Stop pressed in the job's own thread is answered there once. The job's pending heartbeat goes with
        the cancel, so the reply is the thread's last word."""
        launcher = self.real_scheduler()
        fix = self.waiting_fix("session-1")
        progress = self.pending_heartbeat(fix)
        self.assertEqual(self.stop_in("session-1"), {"type": "response", "body": self.STOPPED_HERE})
        self.assertEqual((self.ledger.item(fix["id"])["state"], launcher.stopped), ("cancelled", [fix["id"]]))
        self.assertIsNone(self.heartbeat(fix))
        self.assertFalse(progress.tick())
        self.assertEqual(self.said_in("session-1"), [self.STOPPED_HERE])
        self.api.create_comment.assert_not_called()

    def test_stop_whose_work_ended_meanwhile_says_it_already_stopped(self):
        """A1: the conversation a Stop found finished before the Stop's cancel. Nothing was stopped, so the reply
        claims no stop and no process is signalled; the scheduler's own cleanup owns a finished job's processes."""
        launcher = self.real_scheduler()
        chat = self.conversation_elsewhere("session-1")
        target = self.receiver.ledger.stop_target

        def finished_first(session_id):
            found = target(session_id)
            self.finish(chat)
            return found
        self.receiver.ledger.stop_target = finished_first
        self.assertEqual(self.stop_in("session-1"), {"type": "response", "body": STOP_ALREADY})
        self.assertEqual(self.ledger.item(chat["id"])["state"], "delivered")
        self.assertEqual((launcher.stopped, launcher.unsandboxed_stopped), ([], []))
        self.assertEqual(self.said_in("session-1"), [STOP_ALREADY])

    def test_stop_that_reaches_a_handed_over_job_closes_the_jobs_thread(self):
        """A1: a Stop in a conversation's thread reaches the fix the conversation handed over to, which lives in the
        delegation's thread. The conversation's thread is told the work was elsewhere; the fix's thread is closed."""
        self.real_scheduler()
        fix = self.hand_over(*self.conversation_about_to_hand_over("session-9"))
        self.assertEqual(self.stop_in("session-9"), {"type": "response", "body": self.STOPPED_THERE})
        self.assertEqual(self.ledger.item(fix["id"])["state"], "cancelled")
        self.assertEqual(self.sent()[-2:], [("session-1", {"type": "response", "body": STOPPED_ELSEWHERE}),
                                            ("session-9", {"type": "response", "body": self.STOPPED_THERE})])

    def test_stop_that_meets_a_handover_answers_for_the_job_it_ended(self):
        """A1: the Stop found the conversation in its own thread, and the conversation handed over before the cancel,
        which follows the handover. The job the cancel ended decides both texts, not the job that was looked up."""
        self.real_scheduler()
        chat, token, message = self.conversation_about_to_hand_over("session-9")
        target = self.receiver.ledger.stop_target
        handed = []

        def handed_over_first(session_id):
            found = target(session_id)
            handed.append(self.hand_over(chat, token, message))
            return found
        self.receiver.ledger.stop_target = handed_over_first
        self.assertEqual(self.stop_in("session-9"), {"type": "response", "body": self.STOPPED_THERE})
        self.assertEqual(self.ledger.item(handed[0]["id"])["state"], "cancelled")
        self.assertEqual(self.said_in("session-1")[-1], STOPPED_ELSEWHERE)
        self.assertEqual(self.said_in("session-9"), [self.STOPPED_THERE])

    def test_a_stopped_conversation_elsewhere_gets_the_conversations_text(self):
        """A1: a conversation's closing text names no branch or PR, whichever thread the Stop came from."""
        self.real_scheduler()
        self.labelled(["Bug"], [])
        [chat] = self.delegate("session-1")
        self.ledger.claim(chat["id"], worker_id="w")
        self.mention_in("session-9", "@FarmBot 进展如何？")  # forwarded to the running conversation
        self.assertEqual(self.stop_in("session-9"), {"type": "response", "body": self.STOPPED_THERE})
        self.assertEqual(self.ledger.item(chat["id"])["state"], "cancelled")
        self.assertEqual(self.said_in("session-1"), [STOPPED_ELSEWHERE_CHAT])

    def test_stop_that_reaches_an_operators_job_notes_the_card(self):
        """A1: an operator's `local-` job has no Linear thread. A Stop in the card's latest delegation thread that
        ends it is noted where such a job reports, on the card, once."""
        self.real_scheduler()
        [first] = self.delegate("session-1")
        self.ledger.cancel(first["id"], "an earlier stop")
        self.ledger.ensure_session(f"local-{ISSUE}", ISSUE, True)
        local = self.ledger.create_work_item(issue_id=ISSUE, session_id=f"local-{ISSUE}", skill="fix",
                                             authority="delegation")
        self.assertEqual(self.stop_in("session-1"), {"type": "response", "body": self.STOPPED_THERE})
        self.assertEqual(self.ledger.item(local["id"])["state"], "cancelled")
        self.api.create_comment.assert_called_once_with(ISSUE, STOPPED_ELSEWHERE)
        self.assertEqual(self.said_in("session-1"), [self.STOPPED_THERE])

    def test_a_forward_that_resumes_parked_work_notes_its_own_thread(self):
        """A2: a mention in another thread answers a parked fix. After the mention's acknowledgement, the fix's own
        thread, whose last activity was the question, is told that the work goes on. A later message that is only
        forwarded to the work resumes nothing and adds no note."""
        fix = self.waiting_fix("session-1")
        self.mention_in("session-9", "@FarmBot 公共测试服")
        self.assertEqual(self.ledger.item(fix["id"])["state"], "queued")
        self.assertEqual(self.sent()[-2:], [("session-9", {"type": "thought", "body": self.RESUMED}),
                                            ("session-1", {"type": "thought", "body": RESUMED_ELSEWHERE})])
        self.mention_in("session-8", "@FarmBot 还有一点")
        self.assertEqual(self.sent()[-1], ("session-8", {"type": "thought",
                                                         "body": "该 issue 正在处理中，你的消息已转给正在处理的 worker。"}))
        self.assertEqual(self.said_in("session-1", "thought").count(RESUMED_ELSEWHERE), 1)

    def test_a_resume_note_linear_refuses_changes_nothing_else(self):
        """A2: the note is best effort. The answer is delivered and its event is done whatever becomes of the note."""
        fix = self.waiting_fix("session-1")
        self.refusing("session-1")
        self.mention_in("session-9", "@FarmBot 公共测试服")
        self.assertEqual(self.ledger.item(fix["id"])["state"], "queued")
        self.assertEqual(self.sent()[-2:], [("session-9", {"type": "thought", "body": self.RESUMED}),
                                            ("session-1", {"type": "thought", "body": RESUMED_ELSEWHERE})])
        self.assertEqual(self.receiver.results()[-1]["status"], "done")

    def test_a_forward_that_resumes_an_operators_job_posts_no_note_for_it(self):
        """A2: an operator's `local-` job has no Linear thread, so nothing waits there and nothing is posted for it."""
        self.ledger.observe_issue(issue(labels=["Bug", "修改"], delegate_id=APP, label_groups=CHANGE))
        self.ledger.ensure_session(f"local-{ISSUE}", ISSUE, True)
        local = self.ledger.create_work_item(issue_id=ISSUE, session_id=f"local-{ISSUE}", skill="fix",
                                             authority="delegation")
        self.paused(local, "哪个服？")
        self.mention_in("session-9", "@FarmBot 公共测试服")
        self.assertEqual(self.ledger.item(local["id"])["state"], "queued")
        self.assertEqual(self.sent(), [("session-9", {"type": "thought", "body": self.RESUMED})])
        self.api.create_comment.assert_not_called()

    def test_a_reply_that_resumes_a_handed_over_job_notes_the_jobs_thread(self):
        """A3: a reply in a conversation's thread answers the fix the conversation handed over to, which asked its
        question in the delegation's thread. That thread is told the work goes on. A reply in the job's own thread
        needs no note."""
        fix = self.hand_over(*self.conversation_about_to_hand_over("session-9"))
        self.paused(fix, "哪个服？")
        self.reply_in("session-9", "公共测试服")
        self.assertEqual(self.ledger.item(fix["id"])["state"], "queued")
        self.assertEqual(self.sent()[-2:], [("session-9", {"type": "thought", "body": "收到回复，继续处理。"}),
                                            ("session-1", {"type": "thought", "body": RESUMED_ELSEWHERE})])
        self.paused(fix, "哪个包？")
        self.reply_in("session-1", "安卓包", activity="act-2")
        self.assertEqual(self.ledger.item(fix["id"])["state"], "queued")
        self.assertEqual(self.sent()[-1], ("session-1", {"type": "thought", "body": "收到回复，继续处理。"}))
        self.assertEqual(self.said_in("session-1", "thought").count(RESUMED_ELSEWHERE), 1)

    def test_a_reply_that_resumes_nothing_adds_no_note_in_the_jobs_thread(self):
        """A3: a reply in a conversation's thread answers the fix it handed over to, on a card no longer delegated
        here. The fix stays parked, to be withdrawn, so its thread is not told that the work goes on."""
        fix = self.hand_over(*self.conversation_about_to_hand_over("session-9"))
        self.paused(fix, "哪个服？")
        self.undelegated(["Bug", "修改"], CHANGE)
        self.reply_in("session-9", "公共测试服")
        self.assertEqual(self.ledger.item(fix["id"])["state"], "awaiting_input")
        self.assertEqual(self.sent()[-1], ("session-9", {"type": "thought",
                                                         "body": RESUME_UNDELEGATED.format(bot="FarmBot")}))
        self.assertEqual(self.said_in("session-1", "thought").count(RESUMED_ELSEWHERE), 0)

    def test_a_resume_note_is_not_posted_for_work_that_ended_during_the_acknowledgement(self):
        """A2, P9: while Linear takes the forwarding thread's acknowledgement, the scheduler's next tick launches the
        resumed fix and the launch fails, which closes the fix's thread with an error. No note that the work goes on
        follows that error."""
        self.real_scheduler()
        fix = self.waiting_fix("session-1")

        def launch_fails():
            self.scheduler.worktrees.fail_on = ("Farm-Client", fix["id"])
            self.scheduler.tick()
        self.answered_slowly(launch_fails)
        self.mention_in("session-9", "@FarmBot 公共测试服")
        self.assertEqual(self.ledger.item(fix["id"])["state"], "failed")
        self.assertEqual(self.sent()[-2:], [
            ("session-9", {"type": "thought", "body": self.RESUMED}),
            ("session-1", {"type": "error",
                           "body": "FarmBot 无法启动工作进程（RuntimeError），工作项已标记失败；可回复「重试」。"})])
        self.assertEqual(self.receiver.results()[-1]["status"], "done")

    def test_a_resume_note_is_not_posted_for_work_that_asked_again_during_the_acknowledgement(self):
        """A2: while Linear takes the forwarding thread's acknowledgement, the resumed fix runs, reads the answer and
        asks again in its own thread. That new question stays its thread's last activity: the note would say the
        work will read an answer it has already read."""
        fix = self.waiting_fix("session-1")

        def asks_again():
            self.paused(fix, "哪个包？")
            self.api.create_activity("session-1", {"type": "elicitation", "body": "哪个包？"})
        self.answered_slowly(asks_again)
        self.mention_in("session-9", "@FarmBot 公共测试服")
        self.assertEqual(self.ledger.item(fix["id"])["state"], "awaiting_input")
        self.assertEqual(self.sent()[-2:], [("session-9", {"type": "thought", "body": self.RESUMED}),
                                            ("session-1", {"type": "elicitation", "body": "哪个包？"})])

    def test_the_old_thread_is_told_even_when_the_acknowledgement_fails(self):
        """A5: a new delegation session takes a waiting fix over, and Linear refuses the new session's
        acknowledgement. The takeover is done, so the old thread, whose last activity is a question nobody can
        answer there any more, is still told where its work went."""
        old = self.waiting_fix("session-0")
        self.refusing("session-1")
        [new] = self.delegate("session-1")
        self.assertEqual((self.ledger.item(old["id"])["state"], new["state"], new["predecessor_id"]),
                         ("cancelled", "queued", old["id"]))
        self.assertEqual(self.said_in("session-0"), [SUPERSEDED])
        self.assertEqual(self.receiver.results()[-1]["status"], "uncertain")

    def test_the_thread_a_conversation_moved_from_is_told_even_when_the_acknowledgement_fails(self):
        """A5 for a waiting conversation a mention moved to its own thread (withdrawn-work design C5)."""
        chat = self.conversation_elsewhere("session-0")
        self.paused(chat, "哪个服？")
        self.refusing("session-9")
        self.mention_in("session-9", "@FarmBot 公共测试服")
        self.assertEqual(self.ledger.item(chat["id"])["state"], "cancelled")
        self.assertEqual(self.ledger.active_item_for_session("session-9")["skill"], "chat")
        self.assertEqual(self.said_in("session-0"), [MOVED_THREAD])
        self.assertEqual(self.receiver.results()[-1]["status"], "uncertain")

    def owed(self):
        """(session, job, the job's state, kind, body) of every closing activity owed to a thread, oldest first."""
        return [(row["session_id"], row["item_id"], row["item_state"], row["kind"], row["body"])
                for row in self.ledger.connection.execute("SELECT * FROM session_closures ORDER BY created_at,rowid")]

    def test_a_refused_move_note_stop_reply_or_error_reply_is_owed(self):
        """A4, P10: what closes a thread is owed to it when Linear refuses it: the note a takeover leaves in the old
        thread, a Stop's reply, and the error an event that failed is answered with. The work itself is done, stopped
        or failed as before, and the Stop is still recorded as uncertain."""
        self.real_scheduler()
        old = self.waiting_fix("session-0")
        self.refusing("session-0")
        [new] = self.delegate("session-1")
        self.assertEqual(self.receiver.results()[-1]["status"], "done")
        self.assertEqual(self.owed(), [("session-0", old["id"], "cancelled", "response", SUPERSEDED)])
        self.refusing("session-1")
        self.stop_in("session-1")
        with self.receiver.lock:
            recorded = self.receiver.db.execute("SELECT status,error FROM stop_requests").fetchall()
        self.assertEqual([tuple(row) for row in recorded], [("uncertain", "RuntimeError")])
        self.assertEqual(self.owed()[1:], [("session-1", new["id"], "cancelled", "response", self.STOPPED_HERE)])
        self.refusing("session-7")
        self.api.fetch_issue.side_effect = RuntimeError("boom")
        self.mention_in("session-7", "@FarmBot 进展如何？")
        self.assertEqual(self.receiver.results()[-1]["status"], "uncertain")
        self.assertEqual(self.owed()[2:], [("session-7", None, None, "error",
                                            "FarmBot 处理这条消息时出错（RuntimeError），请稍后重试或联系维护者。")])
        [row] = self.ledger.connection.execute("SELECT * FROM session_closures WHERE session_id='session-7'").fetchall()
        self.assertEqual((row["issue_id"], row["attempts"], row["last_error"], row["given_up_at"]),
                         (ISSUE, 1, "RuntimeError", None))
        self.assertAlmostEqual(row["due_at"] - row["created_at"], 60, places=3)

    def test_an_owed_stop_reply_names_the_job_it_stopped_in_another_thread(self):
        """A4: a Stop's reply speaks of the job the Stop ended, wherever that job lives. When the job's own thread
        takes its closing response and the Stop's thread refuses its reply, only the reply is owed."""
        self.real_scheduler()
        fix = self.waiting_fix("session-1")
        self.mention_in("session-9", "@FarmBot 公共测试服")
        self.refusing("session-9")
        self.stop_in("session-9")
        self.assertEqual(self.said_in("session-1"), [STOPPED_ELSEWHERE])
        self.assertEqual(self.owed(), [("session-9", fix["id"], "cancelled", "response", self.STOPPED_THERE)])

    def test_an_owed_error_reply_goes_once_the_threads_job_moves_on(self):
        """A4, P10: an error reply is owed for the thread's job as it stood. A later reply that resumes the job is
        acknowledged in the thread, and the older error would follow that acknowledgement: it is dropped."""
        fix = self.waiting_fix("session-1")
        self.refusing("session-1")
        self.api.fetch_issue.side_effect = RuntimeError("boom")
        self.reply_in("session-1", "公共测试服")
        [row] = [dict(row) for row in self.ledger.connection.execute("SELECT * FROM session_closures")]
        self.assertEqual((row["session_id"], row["item_id"], row["item_state"], row["kind"]),
                         ("session-1", fix["id"], "awaiting_input", "error"))
        self.assertFalse(self.ledger.closure_superseded(row))
        self.api.fetch_issue.side_effect = None
        self.api.create_activity.side_effect = None
        self.reply_in("session-1", "公共测试服", activity="act-2")
        self.assertEqual(self.ledger.item(fix["id"])["state"], "queued")
        self.assertTrue(self.ledger.closure_superseded(row))
        self.assertEqual(self.owed(), [])  # gone at the answer: a job parked again later does not bring it back

    def failed_message_in(self, session, activity="act-2"):
        """A person's message in `session` fails while Linear is down, and its error reply is refused with it; Linear
        is back afterwards. The error is then owed to the thread."""
        self.refusing(session)
        self.api.fetch_issue.side_effect = RuntimeError("boom")
        self.reply_in(session, "版本 1.2", activity=activity)
        self.assertEqual(self.receiver.results()[-1]["status"], "uncertain")
        self.api.fetch_issue.side_effect = None
        self.api.create_activity.side_effect = None

    def test_an_owed_error_reply_goes_once_a_later_message_is_forwarded_from_the_thread(self):
        """P10: a thread whose messages are forwarded has no job of its own, so the error owed for a message that
        failed there names none, and neither a state change nor a newer job drops it. A later message there that
        goes through is acknowledged in the thread; the older error would follow that acknowledgement and ask for
        a message again that was delivered, so it goes. The progress loop then posts nothing."""
        fix = self.waiting_fix("session-1")
        self.mention_in("session-9", "@FarmBot 公共测试服")
        self.paused(self.ledger.item(fix["id"]), "哪个包？")
        self.failed_message_in("session-9")
        self.assertEqual(self.owed(), [("session-9", None, None, "error", self.FAILED_MESSAGE)])
        self.reply_in("session-9", "版本 1.2", activity="act-3")
        self.assertEqual(self.receiver.results()[-1]["status"], "done")
        self.assertEqual(self.ledger.item(fix["id"])["state"], "queued")
        self.assertEqual(self.owed(), [])
        posted = len(self.sent())
        self.ledger.clock = lambda: time.time() + 61
        self.assertFalse(SessionProgress(self.ledger, self.api).tick())
        self.assertEqual(self.sent()[posted:], [])

    def test_an_owed_error_reply_goes_once_a_later_message_is_steered_into_the_running_job(self):
        """P10: the error owed in a thread whose job is claimed names that job as it runs. A later message there is
        steered into the job and acknowledged, and no state changes: the older error goes with that acknowledgement
        all the same."""
        fix = self.waiting_fix("session-1")
        self.reply_in("session-1", "公共测试服")
        self.ledger.claim(fix["id"], worker_id="w")
        self.failed_message_in("session-1")
        self.assertEqual(self.owed(), [("session-1", fix["id"], "running", "error", self.FAILED_MESSAGE)])
        self.reply_in("session-1", "版本 1.2", activity="act-3")
        self.assertEqual(self.sent()[-1], ("session-1", {"type": "thought",
                                                         "body": "已转给正在处理的 worker，会在下一次检查点读取。"}))
        self.assertEqual(self.ledger.item(fix["id"])["state"], "running")
        self.assertEqual(self.owed(), [])

    def test_an_owed_error_reply_goes_once_a_later_delegation_event_is_deferred(self):
        """P10: an event that waits behind another session's worker was acknowledged in its thread too, so the error
        owed for an older message of the thread goes as it does when the event is done."""
        self.claimed_fix_elsewhere("session-0")
        self.assertEqual(self.delegate("session-1"), [])
        self.failed_message_in("session-1")
        self.assertEqual(self.owed(), [("session-1", None, None, "error", self.FAILED_MESSAGE)])
        self.reply_in("session-1", "现在开始", activity="act-3")
        self.assertEqual(self.sent()[-1], ("session-1", {"type": "thought", "body": DEFER_ACK.format(
            bot="FarmBot", skill="fix", minutes=20)}))
        self.assertEqual(sorted(result["status"] for result in self.receiver.results()),
                         ["deferred", "deferred", "done", "uncertain"])
        self.assertEqual(self.owed(), [])

    def test_an_owed_error_reply_goes_once_a_stop_in_the_thread_is_answered(self):
        """P10: a Stop is an event of its thread, and its reply the thread's newer word. The error owed there for an
        older message names no job in a forwarding thread, so the cancel does not drop it: the answered Stop does."""
        self.real_scheduler()
        fix = self.waiting_fix("session-1")
        self.mention_in("session-9", "@FarmBot 公共测试服")
        self.failed_message_in("session-9")
        self.assertEqual(self.owed(), [("session-9", None, None, "error", self.FAILED_MESSAGE)])
        self.assertEqual(self.stop_in("session-9"), {"type": "response", "body": self.STOPPED_THERE})
        self.assertEqual(self.ledger.item(fix["id"])["state"], "cancelled")
        self.assertEqual(self.owed(), [])

    def test_an_owed_error_reply_goes_once_linear_takes_a_later_error_in_the_thread(self):
        """P10: a later message of the thread that fails too is answered with its own error. When Linear takes that
        one, it is the thread's last word, and the older error would only repeat it a minute later; when Linear
        refuses it as well, the thread is owed the newer one alone."""
        fix = self.waiting_fix("session-1")
        self.reply_in("session-1", "公共测试服")
        self.ledger.claim(fix["id"], worker_id="w")
        self.failed_message_in("session-1")
        self.api.fetch_issue.side_effect = OSError("down again")
        self.reply_in("session-1", "版本 1.2", activity="act-3")
        self.assertEqual(self.sent()[-1], ("session-1", {
            "type": "error", "body": "FarmBot 处理这条消息时出错（OSError），请稍后重试或联系维护者。"}))
        self.assertEqual(self.owed(), [])
        self.refusing("session-1")
        self.reply_in("session-1", "版本 1.2", activity="act-4")
        self.assertEqual(self.owed(), [("session-1", fix["id"], "running", "error",
                                        "FarmBot 处理这条消息时出错（OSError），请稍后重试或联系维护者。")])

    def test_a_later_message_leaves_what_is_owed_for_work_that_ended_in_the_thread(self):
        """P10: only the error a failed event was answered with goes at the thread's next answered event. What closes
        the thread for work that ended there is still owed after a later message in it is forwarded to the card's
        work in another thread: that starts no work in this thread, which still lacks its last word."""
        old = self.waiting_fix("session-0")
        self.refusing("session-0")
        self.delegate("session-1")
        self.api.create_activity.side_effect = None
        closing = [("session-0", old["id"], "cancelled", "response", SUPERSEDED)]
        self.assertEqual(self.owed(), closing)
        self.reply_in("session-0", "进展如何？", activity="act-2")
        self.assertEqual(self.receiver.results()[-1]["status"], "done")
        self.assertEqual(self.ledger.items_for_session("session-0"), [self.ledger.item(old["id"])])
        self.assertEqual(self.owed(), closing)
        # The same for an error that closes the thread for the ended job, as a failed launch's does.
        self.ledger.owe_closure("session-0", item_id=old["id"], kind="error", body="工作项已标记失败。")
        self.reply_in("session-0", "还在吗？", activity="act-3")
        self.assertEqual(self.receiver.results()[-1]["status"], "done")
        self.assertEqual(self.owed(), [("session-0", old["id"], "cancelled", "error", "工作项已标记失败。")])

    def test_an_owed_already_stopped_reply_names_the_job_the_stop_found_ended(self):
        """A4: a Stop that finds its job ended before the cancel answers STOP_ALREADY for that job. The reply is owed
        for that job as it ended, as a Stop's reply is owed for the job it stopped: it goes if the job is retried."""
        self.real_scheduler()
        chat = self.conversation_elsewhere("session-1")
        target = self.receiver.ledger.stop_target

        def finished_first(session_id):
            found = target(session_id)
            self.finish(chat)
            return found
        self.receiver.ledger.stop_target = finished_first
        self.refusing("session-1")
        self.stop_in("session-1")
        self.assertEqual(self.owed(), [("session-1", chat["id"], "delivered", "response", STOP_ALREADY)])

    def test_a_refused_thought_or_card_note_is_not_owed(self):
        """A4: only a response or an error closes a thread. The note that a job was resumed is a thought, and an
        operator's `local-` job is told on the card, where no thread waits."""
        self.waiting_fix("session-1")
        self.refusing("session-1")
        self.mention_in("session-9", "@FarmBot 公共测试服")
        self.assertEqual(self.sent()[-1], ("session-1", {"type": "thought", "body": RESUMED_ELSEWHERE}))
        self.assertEqual(self.owed(), [])
        self.api.create_activity.side_effect = None
        self.ledger.cancel(self.ledger.active_item_for_issue(ISSUE)["id"], "an earlier stop")
        self.ledger.ensure_session(f"local-{ISSUE}", ISSUE, True)
        local = self.ledger.create_work_item(issue_id=ISSUE, session_id=f"local-{ISSUE}", skill="fix",
                                             authority="delegation")
        self.api.create_comment.side_effect = RuntimeError("linear down")
        [new] = self.delegate("session-2")
        self.assertEqual((self.ledger.item(local["id"])["state"], new["predecessor_id"]), ("cancelled", local["id"]))
        self.api.create_comment.assert_called_once_with(ISSUE, SUPERSEDED)
        self.assertEqual(self.owed(), [])


class DelegationEpisodeReceiverTests(ReceiverBase):
    """Silent-delegation design P11 at the receiver (§3.1, §3.2): its fresh read of the card is one of the reads that
    can find a delegation back, and a delegation session Linear did open is what makes the episode heard."""
    labelled = BotRoutingReceiverTests.labelled
    delegate = BotRoutingReceiverTests.delegate
    paused = BotRoutingReceiverTests.paused
    mention_in = BotRoutingReceiverTests.mention_in
    claimed_fix_elsewhere = BotRoutingReceiverTests.claimed_fix_elsewhere
    reply_in = WithdrawnWorkReceiverTests.reply_in
    stop_in = WithdrawnWorkReceiverTests.stop_in
    undelegated = WithdrawnWorkReceiverTests.undelegated
    waiting_fix = WithdrawnWorkReceiverTests.waiting_fix

    def setUp(self):
        super().setUp()
        # One clock for the receiver, its ledger and the status reads: an episode compares the start of a read with
        # a mark and with the time a session was recorded.
        self.now = 1000.0
        self.receiver = Receiver(self.db, "signing-secret", IDENTITY, self.api,
                                 lambda: Ledger(self.db, clock=self.clock), skills={"chat", "fix"},
                                 scheduler=self.scheduler, clock=self.clock)
        self.addCleanup(self.receiver.close)
        self.ledger = Ledger(self.db, clock=self.clock)
        self.addCleanup(self.ledger.close)

    def clock(self):
        return self.now

    def status_reads(self, delegate=None):
        """A lifecycle on the same ledger and clock whose status reads find the card open and delegated to
        `self.read_delegate`, first `delegate`."""
        self.read_delegate = delegate
        reads = SimpleNamespace(app_user_id=APP, issue_status=lambda issue_id: {
            "id": issue_id, "status": "Todo", "status_type": "unstarted", "archived": False,
            "delegate_id": self.read_delegate, "updated_at": "2026-09-21T00:00:00Z"})
        return Lifecycle(self.ledger, reads, self.scheduler, clock=self.clock)

    def episode(self):
        episode = self.ledger.episode(ISSUE)
        return episode and (episode["state"], episode["since"], episode["mark"], episode["due_at"])

    def test_the_receivers_read_of_the_card_records_a_delegation_that_is_back(self):
        """DT1, §3.1: whatever the event, the receiver's read of the card clears the undelegated mark when it finds
        the delegation, and opens the episode from the moment that read began. A read that finds the card not
        delegated is left to the status read it asks for, which marks the card and ends the episode (S11)."""
        fix = self.waiting_fix()
        self.now = 1100.0
        self.ledger.mark_undelegated(ISSUE, 1100.0)
        self.now = 1130.0
        card = self.api.fetch_issue.return_value

        def slow(issue_id):
            self.now += 3
            return card
        self.api.fetch_issue.side_effect = slow
        self.reply_in("session-1", "公共测试服")
        self.assertEqual(self.now, 1133.0)
        self.assertIsNone(self.ledger.status_check(ISSUE)["undelegated_since"])
        self.assertEqual(self.episode(), ("waiting", 1130.0, 1100.0, 1220.0))
        self.assertEqual(self.ledger.item(fix["id"])["state"], "queued")
        self.api.fetch_issue.side_effect = None
        self.undelegated(("Bug", "修改"), CHANGE)
        self.now = 1140.0
        self.reply_in("session-1", "还有一点", activity="act-2")
        self.assertEqual(self.episode(), ("waiting", 1130.0, 1100.0, 1220.0))
        self.assertEqual(self.ledger.status_check(ISSUE)["requested"], 1)
        self.now = 1141.0
        self.assertEqual(self.status_reads().tick(), {"checked": ISSUE, "ok": True})
        self.assertEqual(self.episode()[0], "dropped")
        self.assertEqual(self.ledger.status_check(ISSUE)["undelegated_since"], 1141.0)
        self.assertEqual(self.ledger.item(fix["id"])["state"], "queued")  # one read only marks (P3)

    def test_a_delegation_session_linear_opened_is_heard_whichever_read_came_first(self):
        """S9, §3.2: when Linear does open a session, its `created` and the Issue update race. Either read may be
        the one that clears the mark and opens the episode; the session recorded for the delegation, at or after the
        mark, is what explains it."""
        self.ledger.observe_issue(issue())
        self.now = 1100.0
        self.ledger.mark_undelegated(ISSUE, 1100.0)
        self.now = 1102.0
        [fix] = self.delegate("session-1")  # the session event's own read comes first
        self.assertEqual(self.episode(), ("waiting", 1102.0, 1100.0, 1192.0))
        self.assertTrue(self.ledger.delegation_heard(ISSUE, 1100.0))
        self.assertTrue(self.ledger.finish_episode(ISSUE, 1102.0, "heard"))
        lifecycle = self.status_reads()
        self.now = 1200.0
        lifecycle.refresh(ISSUE)
        self.assertEqual(self.ledger.status_check(ISSUE)["undelegated_since"], 1200.0)
        self.read_delegate = APP
        self.now = 1230.0
        lifecycle.refresh(ISSUE)  # the status read comes first
        self.assertEqual(self.episode(), ("waiting", 1230.0, 1200.0, 1320.0))
        self.assertFalse(self.ledger.delegation_heard(ISSUE, 1200.0))  # session-1 is older than this mark
        self.now = 1232.0
        [new] = self.delegate("session-2")
        self.assertEqual((self.ledger.item(fix["id"])["state"], new["state"]), ("cancelled", "queued"))
        self.assertTrue(self.ledger.delegation_heard(ISSUE, 1200.0))
        self.assertEqual(self.episode(), ("waiting", 1230.0, 1200.0, 1320.0))  # the later read restarts nothing

    def test_a_mentions_session_is_not_heard_and_a_delegation_event_that_failed_is(self):
        """§3.2: a mention opens a session too, and its read finds the delegation, but it is not the delegation's
        session. A delegation `created` whose handling failed had its session recorded before it was routed, so the
        delegation was heard: the person got the event's error in that thread."""
        self.ledger.observe_issue(issue())
        self.now = 1100.0
        self.ledger.mark_undelegated(ISSUE, 1100.0)
        self.now = 1110.0
        self.mention_in("session-9", "@FarmBot 这是什么问题？")
        self.assertEqual(self.episode(), ("waiting", 1110.0, 1100.0, 1200.0))
        self.assertFalse(self.ledger.delegation_heard(ISSUE, 1100.0))
        self.api.create_activity.side_effect = RuntimeError("linear down")
        self.now = 1120.0
        self.delegate("session-1")
        self.assertEqual(self.receiver.results()[-1]["status"], "uncertain")
        self.assertTrue(self.ledger.delegation_heard(ISSUE, 1100.0))

    def test_a_created_that_waits_or_that_a_stop_cancelled_counts_as_heard(self):
        """§3.2: a delegation `created` deferred behind another session's worker has not been handled yet, and one a
        Stop cancelled never will be, but Linear opened a session for each. Both keep their payload, which names the
        card. A cancelled reply is no delegation, and a Stop from before the mark explains nothing after it."""
        self.claimed_fix_elsewhere("session-0")
        self.now = 1010.0
        self.assertEqual(self.delegate("session-1"), [])
        self.assertEqual(self.receiver.results()[-1]["status"], "deferred")
        # session-1 was recorded at 1010, before these marks: only its waiting event explains the delegation.
        self.assertTrue(self.ledger.delegation_heard(ISSUE, 1100.0))
        self.assertFalse(self.ledger.delegation_heard(OTHER, 1100.0))
        # A row whose payload cannot be read names no card, and does not stop the question being answered.
        with self.receiver.db:
            self.receiver.db.execute("INSERT INTO webhook_events VALUES ('org:created:session-x','session-x','ack',"
                                     "'deferred','{not json',1000.0,NULL,NULL)")
        self.assertFalse(self.ledger.delegation_heard(OTHER, 1100.0))
        self.assertTrue(self.ledger.delegation_heard(ISSUE, 1100.0))
        with self.receiver.db:
            self.receiver.db.execute("DELETE FROM webhook_events WHERE session_id='session-x'")
        self.now = 1150.0
        self.stop_in("session-1")
        self.assertEqual([row["status"] for row in self.receiver.results() if row["session_id"] == "session-1"],
                         ["cancelled"])
        self.assertTrue(self.ledger.delegation_heard(ISSUE, 1100.0))
        self.assertTrue(self.ledger.delegation_heard(ISSUE, 1150.0))
        self.assertFalse(self.ledger.delegation_heard(ISSUE, 1151.0))
        # A `created` a Stop cancelled before it was processed recorded no session at all.
        self.now = 1200.0
        self.receive(self.event(agentSession={"id": "session-3",
                                              "issue": {"id": ISSUE, "identifier": "FARM-1", "url": "u"}}))
        self.now = 1210.0
        self.stop_in("session-3", activity="stop-2")
        self.assertIsNone(self.ledger.session("session-3"))
        self.assertTrue(self.ledger.delegation_heard(ISSUE, 1205.0))
        self.assertFalse(self.ledger.delegation_heard(ISSUE, 1211.0))
        self.now = 1300.0
        reply = self.event("prompted", body="还要改吗？",
                           agentSession={"id": "session-0", "issue": {"id": ISSUE, "identifier": "FARM-1", "url": "u"}})
        reply["agentActivity"].update(id="act-9", agentSessionId="session-0")
        self.assertEqual(self.receive(reply), (200, "accepted"))
        self.now = 1310.0
        self.stop_in("session-0", activity="stop-3")
        self.assertEqual(self.receiver.results()[-1]["status"], "cancelled")
        self.assertFalse(self.ledger.delegation_heard(ISSUE, 1305.0))


class SilentDelegationReceiverTests(ReceiverBase):
    """Silent-delegation design P12 at the receiver (§3.4-§3.6; the scenarios S1-S23 of §4.2 under the test IDs of
    §8.2): a delegation Linear opened no session for is settled 90 seconds after the read that found it, where the
    card's work is. It is kept, taken over in its own delegation thread, or told; FarmBot opens no session and
    changes nothing on the card. One clock serves the receiver, its ledgers and the status reads: a settle compares a
    job's creation and a session's recording with the start of a read."""
    labelled = BotRoutingReceiverTests.labelled
    delegate = BotRoutingReceiverTests.delegate
    conversation_elsewhere = BotRoutingReceiverTests.conversation_elsewhere
    finish = BotRoutingReceiverTests.finish
    paused = BotRoutingReceiverTests.paused
    mention_in = BotRoutingReceiverTests.mention_in
    claimed_fix_elsewhere = BotRoutingReceiverTests.claimed_fix_elsewhere
    reply_in = WithdrawnWorkReceiverTests.reply_in
    stop_in = WithdrawnWorkReceiverTests.stop_in
    undelegated = WithdrawnWorkReceiverTests.undelegated
    waiting_chat = WithdrawnWorkReceiverTests.waiting_chat
    waiting_fix = WithdrawnWorkReceiverTests.waiting_fix
    messages = WithdrawnWorkReceiverTests.messages
    real_scheduler = OwnThreadReceiverTests.real_scheduler
    refusing = OwnThreadReceiverTests.refusing
    owed = OwnThreadReceiverTests.owed
    conversation_about_to_hand_over = OwnThreadReceiverTests.conversation_about_to_hand_over
    hand_over = OwnThreadReceiverTests.hand_over
    clock = DelegationEpisodeReceiverTests.clock
    status_reads = DelegationEpisodeReceiverTests.status_reads

    IN_PLACE = IN_PLACE_NOTE.format(bot="FarmBot")
    ASKED_AGAIN = REDELEGATED_WAITING.format(bot="FarmBot", question="哪个服？")
    GOES_ON = REDELEGATED_RUNNING.format(bot="FarmBot")
    WAITING = SILENT_WAITING.format(bot="FarmBot")
    WAITING_CHAT = SILENT_WAITING_CHAT.format(bot="FarmBot")
    BUSY = SILENT_BUSY.format(bot="FarmBot")
    ENDED = SILENT_ENDED.format(bot="FarmBot")
    FEATURE_ACK = "FarmBot 已收到委派，正在排队处理这张功能卡。进展、问题和草稿 PR 会更新在这里。"
    # What a settle may ask of Linear: it reads, and posts activities. It opens no session and writes no card (P6).
    READS_AND_ACTIVITIES = {"fetch_issue", "session_state", "create_activity"}

    def setUp(self):
        super().setUp()
        self.now = 1000.0
        self.threads = {}
        self.settled = None
        self.api.session_state.side_effect = self.session_state
        self.ledger = Ledger(self.db, clock=self.clock)
        self.addCleanup(self.ledger.close)
        self.running()

    def running(self, *skills, **options):
        """The receiver on this test's clock, running `skills` beside chat and fix. Built again on the same ledger,
        it is the controller after a restart."""
        self.receiver.close()
        self.receiver = Receiver(self.db, "signing-secret", IDENTITY, self.api,
                                 lambda: Ledger(self.db, clock=self.clock), skills={"chat", "fix", *skills},
                                 scheduler=self.scheduler, clock=self.clock, **options)
        self.addCleanup(self.receiver.close)

    def linear_says(self, session, status="awaitingInput", archived=False):
        """What Linear answers from now on for the state of the thread `session`. A thread nobody set reads as
        complete and not archived: one a response closed."""
        self.threads[session] = {"status": status, "archived": archived}

    def unreadable(self, session):
        """Linear refuses to say what state the thread `session` is in."""
        self.threads[session] = RuntimeError("linear down")

    def session_state(self, session_id, issue_id, app_user_id):
        self.assertEqual((issue_id, app_user_id), (ISSUE, APP))
        state = self.threads.get(session_id, {"status": "complete", "archived": False})
        if isinstance(state, Exception):
            raise state
        return dict(state)

    def states_read(self):
        """The threads whose state the receiver asked Linear for, in order."""
        return [call.args[0] for call in self.api.session_state.call_args_list]

    def redelegated(self, back_after=30):
        """The card's delegation to this app comes back, or comes, with no session: a status read that starts now
        finds the card not delegated here, and one `back_after` seconds later finds the delegation. Linear sent
        only Issue updates. Returns when the second read began, the episode's `since`: the settle is due 90 seconds
        after it."""
        lifecycle = self.status_reads(None)
        lifecycle.refresh(ISSUE)
        self.now += back_after
        self.read_delegate = APP
        lifecycle.refresh(ISSUE)
        return self.now

    def settle(self):
        """One pass of the receiver's loop with no event pending. The outcome it logged for the episode it settled,
        kept whole in `self.settled`; None when no episode was due, and the pass then did nothing."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            handled = self.receiver.process_one()
        lines = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(len(lines), int(handled))
        self.settled = lines[0] if lines else None
        if self.settled is None:
            return None
        self.assertEqual(self.settled["event"], "delegation_episode")
        return self.settled["outcome"]

    def episode(self):
        episode = self.ledger.episode(ISSUE)
        return episode and (episode["state"], episode["session_id"])

    def active_jobs(self):
        """The ids of the card's active jobs: there is never more than one."""
        return [row["id"] for row in self.ledger.status()["items"]
                if row["state"] in ("queued", "running", "awaiting_input", "awaiting_resource")]

    def waiting_mention(self, session="session-0"):
        """An @mention's conversation on a card delegated to nobody, waiting for an answer in its mention thread,
        which Linear shows as waiting."""
        self.undelegated(("Bug", "修改"), CHANGE)
        chat = self.conversation_elsewhere(session)
        self.paused(chat, "哪个服？")
        self.linear_says(session, "awaitingInput")
        return self.ledger.item(chat["id"])

    def idle_thread(self, session="session-1"):
        """A delegation thread of the Bot/修改 card with no work left: its fix was stopped earlier."""
        self.labelled(["Bug", "修改"], CHANGE)
        [fix] = self.delegate(session)
        self.ledger.cancel(fix["id"], "an earlier stop")
        return fix

    def taken_in_place(self):
        """S1 up to its settle: the conversation a delegation of the unlabelled card opened waits in session-0; the
        delegation goes and comes back with Bot/修改 and no session; the settle continues it there as a fix. Returns
        (the conversation, the fix)."""
        chat = self.waiting_chat("session-0", said="按钮点了没反应")
        self.linear_says("session-0", "awaitingInput")
        self.now = 1100.0
        self.redelegated()
        self.labelled(["Bug", "修改"], CHANGE)
        self.now = 1220.0
        self.assertEqual(self.settle(), "in_place")
        return chat, self.ledger.active_item_for_issue(ISSUE)

    # --- In place: the work continues in its own delegation thread (P12, D1) ---

    def test_s1_a_redelegation_with_no_session_continues_in_the_waiting_delegation_thread(self):
        """S1, the incident: a delegation's conversation waits in its thread; "No agent" without archiving; Bot/修改
        added; delegated again within a minute. Linear opens no session. Nothing happens during the grace. Then the
        conversation is superseded, in one transaction, by the fix the labels name, in the same thread, with its
        messages and the thread's target: P4 with the new session equal to the old one. The thread gets one thought
        and no SUPERSEDED, and the card has exactly one active job."""
        chat = self.waiting_chat("session-0", said="按钮点了没反应")
        self.ledger.set_session_target("session-0", PIN)
        self.linear_says("session-0", "awaitingInput")
        self.now = 1100.0
        self.assertEqual(self.redelegated(), 1130.0)
        self.labelled(["Bug", "修改"], CHANGE)
        self.api.reset_mock()
        self.now = 1219.0
        self.assertIsNone(self.settle())
        self.assertEqual(self.api.method_calls, [])  # before the grace ends FarmBot asks Linear nothing
        self.assertEqual(self.episode(), ("waiting", None))
        self.now = 1220.0
        self.assertEqual(self.settle(), "in_place")
        fix = self.ledger.active_item_for_issue(ISSUE)
        self.assertEqual((fix["skill"], fix["state"], fix["session_id"], fix["authority"], fix["target"],
                          fix["predecessor_id"]), ("fix", "queued", "session-0", "delegation", PIN, None))
        self.assertEqual(self.ledger.item(chat["id"])["state"], "cancelled")
        self.assertEqual(self.messages(fix), ["按钮点了没反应"])
        self.assertEqual(self.sent(), [("session-0", {"type": "thought", "body": self.IN_PLACE + "\n" + FIX_ACK})])
        self.assertEqual(self.active_jobs(), [fix["id"]])
        self.assertEqual(self.episode(), ("in_place", "session-0"))
        self.assertEqual(self.states_read(), ["session-0"])
        self.assertEqual({call[0] for call in self.api.method_calls}, self.READS_AND_ACTIVITIES)
        self.assertEqual(self.settled, {"event": "delegation_episode", "issue_id": ISSUE, "outcome": "in_place",
                                        "threads": [{"session_id": "session-0", "status": "awaitingInput",
                                                     "archived": False}]})
        self.assertIsNone(self.settle())  # settled once
        self.assertEqual(len(self.sent()), 1)

    def test_in_place_needs_a_thread_linear_says_is_not_archived(self):
        """S19, U3, P8: work is never moved into a thread nobody sees, nor on a state FarmBot could not read. An
        archived thread gets no note either, so the episode is only recorded; a thread whose state cannot be read
        is told from what the ledger knows, and its conversation stays answerable."""
        for state, outcome in (("archived", "unseen"), ("unreadable", "told")):
            with self.subTest(state=state):
                self.setUp()
                chat = self.waiting_chat("session-0")
                if state == "archived":
                    self.linear_says("session-0", "awaitingInput", archived=True)
                else:
                    self.unreadable("session-0")
                self.now = 1100.0
                self.redelegated()
                self.labelled(["Bug", "修改"], CHANGE)
                posted, self.now = len(self.sent()), 1220.0
                self.assertEqual(self.settle(), outcome)
                self.assertEqual((self.ledger.item(chat["id"])["state"], self.active_jobs()),
                                 ("awaiting_input", [chat["id"]]))
                self.assertEqual(self.sent()[posted:], [] if state == "archived" else [
                    ("session-0", {"type": "response", "body": self.WAITING_CHAT})])
                self.assertEqual(self.episode(), (outcome, None))
                self.assertEqual(self.states_read(), ["session-0"])

    def test_in_place_never_takes_work_newer_than_the_delegation(self):
        """S17, P3: only a job older than the read that found the delegation is superseded in place. The
        conversation here began with that very read, a reply in the card's old delegation thread, so its thread is
        told and nothing is cancelled."""
        self.idle_thread("session-1")
        self.linear_says("session-1", "active")
        self.now = 1100.0
        self.status_reads(None).refresh(ISSUE)  # the card is marked: not delegated here
        self.now = 1130.0
        self.reply_in("session-1", "还在吗？")  # this event's read finds the delegation: the episode starts with it
        chat = self.ledger.active_item_for_session("session-1")
        self.assertEqual((chat["skill"], chat["authority"], chat["created_at"], self.ledger.episode(ISSUE)["since"]),
                         ("chat", "delegation", 1130.0, 1130.0))
        posted, self.now = len(self.sent()), 1220.0
        self.assertEqual(self.settle(), "told")
        self.assertEqual((self.ledger.item(chat["id"])["state"], self.active_jobs()), ("queued", [chat["id"]]))
        self.assertEqual(self.sent()[posted:], [("session-1", {"type": "thought", "body": self.BUSY})])
        self.assertEqual(self.episode(), ("told", None))

    def test_a_claimed_write_job_with_other_labels_is_told_and_keeps_running(self):
        """S6: a worker holds the delegation's fix while the labels now name other work. A claimed write job is
        never restarted by a delegation that opened no session: it keeps its claim, nothing is flagged, and its
        thread is told how to restart on the new labels."""
        self.running("feature")
        fix, token = self.claimed_fix_elsewhere("session-0")
        self.linear_says("session-0", "active")
        self.now = 1100.0
        self.redelegated()
        self.labelled(["Code"], CODE)
        posted, self.now = len(self.sent()), 1220.0
        self.assertEqual(self.settle(), "told")
        current = self.ledger.item(fix["id"])
        self.assertEqual((current["state"], current["withdraw_deadline"], current["withdraw_reason"]),
                         ("running", None, None))
        self.ledger.renew(fix["id"], token)
        self.assertEqual(self.active_jobs(), [fix["id"]])
        self.assertEqual(self.sent()[posted:], [("session-0", {"type": "thought", "body": self.BUSY})])
        self.assertEqual((self.episode(), self.owed()), (("told", None), []))

    def test_a_running_delegation_conversation_is_superseded_in_place(self):
        """S6: a conversation is taken over whatever its state, as a new delegation session takes one over (WW
        C2): its worker's claim ends with it, and the scheduler's next tick stops the worker."""
        self.labelled(["Bug"], [])
        [chat] = self.delegate("session-0")
        token = self.ledger.claim(chat["id"], worker_id="w")["token"]
        self.linear_says("session-0", "active")
        self.now = 1100.0
        self.redelegated()
        self.labelled(["Bug", "修改"], CHANGE)
        self.now = 1220.0
        self.assertEqual(self.settle(), "in_place")
        with self.assertRaises(LedgerError):
            self.ledger.renew(chat["id"], token)
        fix = self.ledger.active_item_for_issue(ISSUE)
        self.assertEqual((fix["skill"], fix["state"], fix["session_id"], self.active_jobs()),
                         ("fix", "queued", "session-0", [fix["id"]]))
        self.scheduler.stop.assert_not_called()

    def test_s6_unclaimed_work_of_another_kind_is_taken_over_in_its_delegation_thread(self):
        """S6: the delegation's fix waits for an answer while the card now carries Bot/Code. No worker holds it, so
        the feature job the labels name continues in the same thread, without the client target a fix needs."""
        self.running("feature")
        old = self.waiting_fix("session-0")
        self.ledger.set_session_target("session-0", PIN)
        self.linear_says("session-0", "awaitingInput")
        self.now = 1100.0
        self.redelegated()
        self.labelled(["Code"], CODE)
        posted, self.now = len(self.sent()), 1220.0
        self.assertEqual(self.settle(), "in_place")
        job = self.ledger.active_item_for_issue(ISSUE)
        self.assertEqual((job["skill"], job["state"], job["session_id"], job["authority"], job["target"]),
                         ("feature", "queued", "session-0", "delegation", None))
        self.assertEqual((self.ledger.item(old["id"])["state"], self.active_jobs()), ("cancelled", [job["id"]]))
        self.assertEqual(self.sent()[posted:],
                         [("session-0", {"type": "thought", "body": self.IN_PLACE + "\n" + self.FEATURE_ACK})])

    def test_a_conversation_the_delegation_no_longer_covered_is_taken_back_in_its_delegation_thread(self):
        """WW A2 then S1: a delegation's conversation was answered while the card was delegated to nobody, which
        made it a mention's. The delegation comes back with no session and no Bot label: the conversation continues
        in its delegation thread as the delegation's again, with the card's first message and no target."""
        chat = self.waiting_chat("session-0")
        self.ledger.set_session_target("session-0", PIN)
        self.undelegated()
        self.reply_in("session-0", "公共测试服")
        answered = self.ledger.item(chat["id"])
        self.assertEqual((answered["state"], answered["authority"]), ("queued", "mention"))
        self.linear_says("session-0", "active")
        self.now = 1100.0
        self.redelegated()
        self.labelled(["Bug"], [])
        posted, self.now = len(self.sent()), 1220.0
        self.assertEqual(self.settle(), "in_place")
        new = self.ledger.active_item_for_issue(ISSUE)
        self.assertEqual((new["skill"], new["state"], new["session_id"], new["authority"], new["target"]),
                         ("chat", "queued", "session-0", "delegation", None))
        self.assertEqual((self.ledger.item(chat["id"])["state"], self.messages(new)), ("cancelled", ["公共测试服"]))
        self.assertEqual(self.sent()[posted:],
                         [("session-0", {"type": "thought", "body": self.IN_PLACE + "\n" + NO_BOT_LABEL})])

    def test_s8_a_created_after_in_place_takes_the_card_over(self):
        """S8, U1: Linear delivers the delegation's `created` after all, an hour late. It is P4 as ever: the fix
        that continued in place is superseded into the new session, which links it as its predecessor."""
        _, taken = self.taken_in_place()
        self.now = 4830.0
        [fix] = self.delegate("session-2")
        self.assertEqual(self.ledger.item(taken["id"])["state"], "cancelled")
        self.assertEqual((fix["skill"], fix["state"], fix["predecessor_id"]), ("fix", "queued", taken["id"]))
        self.assertEqual(self.messages(fix), ["按钮点了没反应"])
        self.assertEqual(self.sent()[-2:], [
            ("session-2", {"type": "thought", "body": FIX_ACK + "\n" + SUPERSEDE_SUFFIX}),
            ("session-0", {"type": "response", "body": SUPERSEDED})])
        self.assertEqual(self.active_jobs(), [fix["id"]])
        self.assertEqual(self.episode(), ("in_place", "session-0"))

    def test_s8_a_created_after_in_place_waits_for_a_worker_that_holds_the_fix(self):
        """S8: by the time the late `created` arrives, a worker holds the fix that continued in place. That is P4 as
        ever (WW C2): the worker is told to withdraw, and the new session's delegation waits for it."""
        _, taken = self.taken_in_place()
        self.ledger.claim(taken["id"], worker_id="w")
        self.now = 4830.0
        self.assertEqual(self.delegate("session-2"), [])
        current = self.ledger.item(taken["id"])
        self.assertEqual((current["state"], current["withdraw_reason"], self.active_jobs()),
                         ("running", "superseded", [taken["id"]]))
        self.assertEqual(self.receiver.results()[-1]["status"], "deferred")
        self.assertEqual(self.sent()[-1], ("session-2", {"type": "thought", "body": DEFER_ACK.format(
            bot="FarmBot", skill="fix", minutes=20)}))
        self.assertEqual(self.episode(), ("in_place", "session-0"))

    def test_s21_stop_after_in_place_is_the_threads_own(self):
        """S21, P5: the job that continued in place is its thread's own, so a Stop there stops it and answers as for
        any job of the thread."""
        _, fix = self.taken_in_place()
        self.assertEqual(self.ledger.stop_target("session-0"), (fix, "own"))
        self.assertEqual(self.stop_in("session-0"), {
            "type": "response", "body": "已停止 FARM-1 上的工作，worker 已终止，占用的资源在静默检查后释放。"})
        self.assert_stopped(fix)

    def test_s16_a_reply_during_the_grace_is_handled_first(self):
        """S16, §3.4: a pending event goes before a due episode, in the one loop that handles both. The reply resumes
        the conversation as ever; the settle then decides on what is true, and the fix that continues in place has
        the reply among its messages."""
        chat = self.waiting_chat("session-0", said="按钮点了没反应")
        self.linear_says("session-0", "active")
        self.now = 1100.0
        self.redelegated()
        self.labelled(["Bug", "修改"], CHANGE)
        posted, self.now = len(self.sent()), 1221.0  # the episode is due
        self.reply_in("session-0", "在公共测试服", activity="act-9")  # one pass: the reply, and no settle
        self.assertEqual((self.ledger.item(chat["id"])["state"], self.episode()), ("queued", ("waiting", None)))
        self.assertEqual(self.settle(), "in_place")
        fix = self.ledger.active_item_for_issue(ISSUE)
        self.assertEqual((fix["skill"], self.messages(fix)), ("fix", ["按钮点了没反应", "在公共测试服"]))
        self.assertEqual(self.sent()[posted:], [
            ("session-0", {"type": "thought", "body": "收到回复，继续处理。"}),
            ("session-0", {"type": "thought", "body": self.IN_PLACE + "\n" + FIX_ACK})])

    def test_work_that_changed_under_the_settle_is_looked_at_again(self):
        """§3.5: the conversation ended between the settle's look at it and the takeover. The supersede refuses, as
        for any stale routing; nothing is posted, and the episode is due again 5 seconds later, when the settle
        decides on what is true then: no work is left, and the thread that still waits is closed."""
        chat = self.waiting_chat("session-0")
        self.now = 1100.0
        self.redelegated()
        self.labelled(["Bug", "修改"], CHANGE)

        def stopped_meanwhile(session_id, issue_id, app_user_id):
            self.ledger.cancel(chat["id"], "operator cancelled")
            return {"status": "awaitingInput", "archived": False}
        self.api.session_state.side_effect = stopped_meanwhile
        posted, self.now = len(self.sent()), 1220.0
        self.assertEqual(self.settle(), "changed")
        episode = self.ledger.episode(ISSUE)
        self.assertEqual((episode["state"], episode["due_at"], episode["attempts"], episode["error"]),
                         ("waiting", 1225.0, 0, None))
        self.assertEqual((self.sent()[posted:], self.active_jobs()), ([], []))
        self.now = 1224.0
        self.assertIsNone(self.settle())
        self.now = 1225.0
        self.api.session_state.side_effect = self.session_state
        self.linear_says("session-0", "awaitingInput")
        self.assertEqual(self.settle(), "told")
        self.assertEqual(self.sent()[posted:], [("session-0", {"type": "response", "body": self.ENDED})])
        self.assertEqual(self.active_jobs(), [])

    def test_an_episode_that_ended_under_the_settle_takes_nothing_in_place(self):
        """P11, P3: a status read that began after the settle's own found the delegation gone again, and dropped the
        episode. The takeover checks the episode in its own transaction: the conversation is left as it was."""
        chat = self.waiting_chat("session-0")
        self.now = 1100.0
        self.redelegated()
        self.labelled(["Bug", "修改"], CHANGE)

        def gone_meanwhile(session_id, issue_id, app_user_id):
            self.ledger.mark_undelegated(ISSUE, self.now)
            return {"status": "awaitingInput", "archived": False}
        self.api.session_state.side_effect = gone_meanwhile
        posted, self.now = len(self.sent()), 1220.0
        self.assertEqual(self.settle(), "changed")
        self.assertEqual((self.ledger.item(chat["id"])["state"], self.active_jobs()), ("awaiting_input", [chat["id"]]))
        self.assertEqual((self.episode(), self.sent()[posted:]), (("dropped", None), []))
        self.now = 1300.0
        self.assertIsNone(self.settle())

    def test_an_episode_that_ended_under_the_settle_posts_nothing(self):
        """P11: kept work and a told thread get their line only from the settle that ended the episode it read. One
        a later read dropped meanwhile posts nothing."""
        for kind in ("kept", "told"):
            with self.subTest(kind=kind):
                self.setUp()
                job = self.waiting_fix("session-1") if kind == "kept" else self.waiting_mention("session-0")
                self.now = 1100.0
                self.redelegated()
                self.labelled(["Bug", "修改"], CHANGE)
                heard = self.receiver.ledger.delegation_heard

                def gone_first(*args):
                    answer = heard(*args)
                    self.ledger.mark_undelegated(ISSUE, self.now)
                    return answer
                self.receiver.ledger.delegation_heard = gone_first
                posted, self.now = len(self.sent()), 1220.0
                self.assertEqual(self.settle(), "changed")
                self.assertEqual((self.sent()[posted:], self.episode()), ([], ("dropped", None)))
                self.assertEqual(self.ledger.item(job["id"])["state"], "awaiting_input")

    def test_a_write_job_claimed_under_the_settle_is_not_taken_in_place(self):
        """P4: a write job a worker holds is never superseded in place. The fix waited when the settle looked, and
        was answered and claimed before the takeover: the supersede refuses, and the settle looks again. The fix
        keeps its claim, and its thread is then told."""
        self.running("feature")
        fix = self.waiting_fix("session-0")
        self.linear_says("session-0", "active")
        self.now = 1100.0
        self.redelegated()
        self.labelled(["Code"], CODE)
        claims = []

        def claimed_meanwhile(session_id, issue_id, app_user_id):
            if not claims:
                self.ledger.push_inbox(fix["id"], "公共测试服", resume_waiting=True)
                claims.append(self.ledger.claim(fix["id"], worker_id="w")["token"])
            return {"status": "active", "archived": False}
        self.api.session_state.side_effect = claimed_meanwhile
        posted, self.now = len(self.sent()), 1220.0
        self.assertEqual(self.settle(), "changed")
        self.ledger.renew(fix["id"], claims[0])
        self.assertEqual((self.sent()[posted:], self.active_jobs()), ([], [fix["id"]]))
        self.now = 1225.0
        self.assertEqual(self.settle(), "told")
        self.assertEqual(self.sent()[posted:], [("session-0", {"type": "thought", "body": self.BUSY})])
        self.ledger.renew(fix["id"], claims[0])

    def test_a_delegation_that_flapped_under_the_settle_starts_a_new_episode(self):
        """P11: a status read that began just before the settle's own found the delegation gone and dropped the
        episode, and the settle's read finds it back. Its read is one of the reads that can find a delegation back:
        it opens a new episode from its own start, and settles nothing of the old one."""
        fix = self.waiting_fix("session-1")
        self.now = 1100.0
        self.redelegated()
        card = self.api.fetch_issue.return_value

        def gone_just_before(issue_id):
            self.ledger.mark_undelegated(ISSUE, 1219.0)
            return card
        self.api.fetch_issue.side_effect = gone_just_before
        posted, self.now = len(self.sent()), 1220.0
        self.assertEqual(self.settle(), "changed")
        episode = self.ledger.episode(ISSUE)
        self.assertEqual((episode["state"], episode["since"], episode["mark"], episode["due_at"]),
                         ("waiting", 1220.0, 1219.0, 1310.0))
        self.assertEqual((self.sent()[posted:], self.ledger.status_check(ISSUE)["undelegated_since"]), ([], None))
        self.api.fetch_issue.side_effect = None
        self.now = 1310.0
        self.assertEqual(self.settle(), "kept")
        self.assertEqual((len(self.sent()), self.ledger.item(fix["id"])["state"]), (posted + 1, "awaiting_input"))

    def test_the_settles_read_is_a_status_read_too(self):
        """DT1: the settle's read of the card finds the delegation, so it clears a mark that an older read, which
        returned late, left behind. The episode it settles is not restarted by that."""
        self.waiting_fix("session-1")
        self.now = 1100.0
        self.redelegated()
        self.ledger.mark_undelegated(ISSUE, 1125.0)  # began before the read that found the delegation: drops nothing
        self.assertEqual((self.ledger.status_check(ISSUE)["undelegated_since"], self.episode()),
                         (1125.0, ("waiting", None)))
        self.now = 1220.0
        self.assertEqual(self.settle(), "kept")
        self.assertIsNone(self.ledger.status_check(ISSUE)["undelegated_since"])
        episode = self.ledger.episode(ISSUE)
        self.assertEqual((episode["state"], episode["since"]), ("served", 1130.0))
        self.assertIsNone(self.settle())

    # --- Kept: delegation work of the kind the labels name is not restarted (P12, D2) ---

    def test_s4_a_waiting_fix_of_the_same_kind_asks_again_once(self):
        """S4, S15: the delegation's fix waits for an answer; the delegation goes and comes back with the labels
        unchanged and no session. Nothing moves: the fix asks its question again in its thread, which stays a
        waiting thread over waiting work. An automation that flaps the delegate costs one such line in 30 minutes."""
        fix = self.waiting_fix("session-1")
        self.now = 1100.0
        self.redelegated()
        posted, self.now = len(self.sent()), 1220.0
        self.assertEqual(self.settle(), "kept")
        asked = ("session-1", {"type": "elicitation", "body": self.ASKED_AGAIN})
        self.assertEqual(self.sent()[posted:], [asked])
        self.assertEqual((self.ledger.item(fix["id"])["state"], self.active_jobs()), ("awaiting_input", [fix["id"]]))
        self.assertEqual(self.episode(), ("served", "session-1"))
        self.assertEqual(self.states_read(), [])  # kept work needs no look at Linear's threads
        self.assertEqual(self.settled["threads"], [])
        self.now = 2900.0
        self.assertEqual(self.redelegated(), 2930.0)
        self.now = 3020.0  # 30 minutes after the line
        self.assertEqual(self.settle(), "kept")
        self.assertEqual((self.sent()[posted:], self.episode()), ([asked], ("served", "session-1")))
        self.now = 3100.0
        self.redelegated()
        self.now = 3220.0
        self.assertEqual(self.settle(), "kept")
        self.assertEqual(self.sent()[posted:], [asked, asked])
        self.assertEqual((self.ledger.item(fix["id"])["state"], self.active_jobs()), ("awaiting_input", [fix["id"]]))
        self.reply_in("session-1", "公共测试服")  # an answer resumes the fix, as ever
        self.assertEqual(self.ledger.item(fix["id"])["state"], "queued")

    def test_s5_running_work_of_the_same_kind_is_kept_with_one_line(self):
        """S5: the same while the fix runs or is queued. It goes on, and its thread is told so in one thought."""
        for claimed in (True, False):
            with self.subTest(claimed=claimed):
                self.setUp()
                self.labelled(["Bug", "修改"], CHANGE)
                [fix] = self.delegate("session-1")
                token = self.ledger.claim(fix["id"], worker_id="w")["token"] if claimed else None
                self.now = 1100.0
                self.redelegated()
                posted, self.now = len(self.sent()), 1220.0
                self.assertEqual(self.settle(), "kept")
                self.assertEqual(self.sent()[posted:], [("session-1", {"type": "thought", "body": self.GOES_ON})])
                self.assertEqual((self.ledger.item(fix["id"])["state"], self.active_jobs()),
                                 ("running" if claimed else "queued", [fix["id"]]))
                if claimed:
                    self.ledger.renew(fix["id"], token)
                self.assertEqual(self.episode(), ("served", "session-1"))

    def test_s19_kept_work_in_an_archived_thread_still_gets_its_line(self):
        """S19: the job's thread was archived while the card stayed delegated, and the `created` of the delegation
        was lost. Work of the labels' kind is kept whatever Linear shows for its thread, which the settle does not
        even read: the line lands in the archived thread, and the late `created` then takes the card over (S8)."""
        fix = self.waiting_fix("session-1")
        self.linear_says("session-1", "awaitingInput", archived=True)
        self.now = 1100.0
        self.redelegated()
        posted, self.now = len(self.sent()), 1220.0
        self.assertEqual(self.settle(), "kept")
        self.assertEqual(self.sent()[posted:], [("session-1", {"type": "elicitation", "body": self.ASKED_AGAIN})])
        self.assertEqual((self.states_read(), self.episode()), ([], ("served", "session-1")))
        self.now = 4830.0
        [new] = self.delegate("session-2")
        self.assertEqual((self.ledger.item(fix["id"])["state"], new["predecessor_id"], self.active_jobs()),
                         ("cancelled", fix["id"], [new["id"]]))

    def test_a_waiting_delegation_conversation_on_a_card_still_without_a_bot_label_asks_again(self):
        """S1 with the labels unchanged: the delegation's conversation is the work an unlabelled card routes to, so
        it is kept and asks again."""
        chat = self.waiting_chat("session-0")
        self.now = 1100.0
        self.redelegated()
        posted, self.now = len(self.sent()), 1220.0
        self.assertEqual(self.settle(), "kept")
        self.assertEqual(self.sent()[posted:], [("session-0", {"type": "elicitation", "body": self.ASKED_AGAIN})])
        self.assertEqual((self.ledger.item(chat["id"])["state"], self.episode()),
                         ("awaiting_input", ("served", "session-0")))

    def test_a_kept_question_is_not_asked_again_for_work_that_ended_meanwhile(self):
        """P9: the line is for the job as the settle found it. A job that ended in the meantime, its thread closed
        by its own last word, is asked nothing."""
        fix = self.waiting_fix("session-1")
        self.now = 1100.0
        self.redelegated()
        finish = self.receiver.ledger.finish_episode

        def ended_meanwhile(*args, **kwargs):
            done = finish(*args, **kwargs)
            self.ledger.cancel(fix["id"], "operator cancelled")
            return done
        self.receiver.ledger.finish_episode = ended_meanwhile
        posted, self.now = len(self.sent()), 1220.0
        self.assertEqual(self.settle(), "kept")
        self.assertEqual((self.sent()[posted:], self.episode()), ([], ("served", "session-1")))

    def test_a_kept_question_that_crosses_the_jobs_end_is_withdrawn(self):
        """P9, as A6: the job ended while Linear took the question. A question is then the thread's last activity
        with no job to read an answer, so it is withdrawn by a response."""
        fix = self.waiting_fix("session-1")
        self.now = 1100.0
        self.redelegated()

        def create_activity(session_id, content, activity_id=None):
            if content["type"] == "elicitation":
                self.ledger.cancel(fix["id"], "operator cancelled")
            return {"success": True}
        self.api.create_activity.side_effect = create_activity
        posted, self.now = len(self.sent()), 1220.0
        self.assertEqual(self.settle(), "kept")
        self.assertEqual(self.sent()[posted:], [("session-1", {"type": "elicitation", "body": self.ASKED_AGAIN}),
                                                ("session-1", {"type": "response", "body": QUESTION_WITHDRAWN})])
        self.assertEqual(self.owed(), [])
        # The same when Linear refuses the withdrawal: it closes the thread, so it is owed (P10).
        self.setUp()
        fix = self.waiting_fix("session-1")
        self.now = 1100.0
        self.redelegated()

        def refused_withdrawal(session_id, content, activity_id=None):
            if content["type"] == "response":
                raise RuntimeError("linear down")
            return create_activity(session_id, content, activity_id)
        self.api.create_activity.side_effect = refused_withdrawal
        self.now = 1220.0
        self.assertEqual(self.settle(), "kept")
        self.assertEqual(self.owed(), [("session-1", fix["id"], "cancelled", "response", QUESTION_WITHDRAWN)])

    def test_s18_an_operators_local_job_gets_no_note(self):
        """S18: an operator's `local-` session is no Linear thread. Its delegation work of the labels' kind is kept
        with no line; its conversation is neither taken in place nor told, and only another open thread of the card
        is."""
        self.ledger.observe_issue(issue(labels=["Bug", "修改"], delegate_id=APP, label_groups=CHANGE))
        self.ledger.ensure_session(f"local-{ISSUE}", ISSUE, True)
        local = self.ledger.create_work_item(issue_id=ISSUE, session_id=f"local-{ISSUE}", skill="fix",
                                             authority="delegation")
        self.now = 1100.0
        self.redelegated()
        self.now = 1220.0
        self.assertEqual(self.settle(), "kept")
        self.assertEqual((self.sent(), self.episode()), ([], ("served", f"local-{ISSUE}")))
        self.ledger.cancel(local["id"], "next case")
        chat = self.ledger.create_work_item(issue_id=ISSUE, session_id=f"local-{ISSUE}", skill="chat",
                                            authority="operator")
        self.now = 2000.0
        self.redelegated()
        self.now = 2120.0
        self.assertEqual(self.settle(), "unseen")
        self.assertEqual((self.sent(), self.states_read(), self.episode()), ([], [], ("unseen", None)))
        self.ledger.ensure_session("session-7", ISSUE, True)
        self.linear_says("session-7", "awaitingInput")
        self.now = 3000.0
        self.redelegated()
        self.now = 3120.0
        self.assertEqual(self.settle(), "told")
        self.assertEqual(self.sent(), [("session-7", {"type": "response", "body": self.ENDED})])
        self.assertEqual(self.states_read(), ["session-7"])
        self.assertEqual((self.ledger.item(chat["id"])["state"], self.active_jobs()), ("queued", [chat["id"]]))
        self.api.create_comment.assert_not_called()

    # --- Told: each open thread of the card gets one note (P12, D3) ---

    def test_s2_a_waiting_mention_conversation_is_told_and_kept(self):
        """S2: a card is delegated while an @mention's conversation waits in its mention thread, which can hold no
        delegation work (P1). The thread gets one response that ends the wait and says what to do; the conversation
        stays parked and answerable. An older thread that a response closed gets nothing."""
        self.undelegated(("Bug", "修改"), CHANGE)
        self.finish(self.conversation_elsewhere("session-8"))
        chat = self.waiting_mention("session-0")
        self.now = 1100.0
        self.redelegated()
        self.labelled(["Bug", "修改"], CHANGE)
        self.api.reset_mock()
        self.now = 1220.0
        self.assertEqual(self.settle(), "told")
        self.assertEqual(self.sent(), [("session-0", {"type": "response", "body": self.WAITING_CHAT})])
        current = self.ledger.item(chat["id"])
        self.assertEqual((current["state"], current["authority"], self.active_jobs()),
                         ("awaiting_input", "mention", [chat["id"]]))
        self.assertEqual((self.episode(), self.owed()), (("told", None), []))
        self.assertEqual(self.states_read(), ["session-0", "session-8"])
        self.assertEqual({call[0] for call in self.api.method_calls}, self.READS_AND_ACTIVITIES)
        self.assertEqual(self.settled["threads"], [
            {"session_id": "session-0", "status": "awaitingInput", "archived": False},
            {"session_id": "session-8", "status": "complete", "archived": False}])
        self.assertIsNone(self.settle())
        self.assertEqual(len(self.sent()), 1)

    def test_after_the_note_a_reply_resumes_and_a_new_delegation_takes_over(self):
        """S22: the note keeps the conversation answerable, and the person can follow it. A reply in the noted
        thread resumes the conversation. "No agent" and delegating again then opens a session, since the noted
        thread is complete, and its `created` takes the conversation over (WW C3); the noted thread is told."""
        chat = self.waiting_mention("session-0")
        self.now = 1100.0
        self.redelegated()
        self.labelled(["Bug", "修改"], CHANGE)
        self.now = 1220.0
        self.assertEqual(self.settle(), "told")
        self.now = 1300.0
        self.reply_in("session-0", "公共测试服")
        self.assertEqual(self.ledger.item(chat["id"])["state"], "queued")
        self.assertEqual(self.sent()[-1], ("session-0", {"type": "thought", "body": "收到回复，继续处理。"}))
        self.now = 1400.0
        [fix] = self.delegate("session-2")
        self.assertEqual(self.ledger.item(chat["id"])["state"], "cancelled")
        self.assertEqual((fix["skill"], fix["state"], fix["authority"]), ("fix", "queued", "delegation"))
        self.assertEqual(self.messages(fix), ["@FarmBot 这是什么问题？", "公共测试服"])
        self.assertEqual(self.said_in("session-0"), [self.WAITING_CHAT, SUPERSEDED])
        self.assertEqual(self.active_jobs(), [fix["id"]])

    def test_s3_a_running_mention_conversation_gets_a_thought(self):
        """S3, U2: a card is delegated while an @mention's conversation runs, and no session follows. Nothing is
        cancelled; the mention thread is told in a thought, which ends nothing."""
        self.undelegated(("Bug", "修改"), CHANGE)
        chat = self.conversation_elsewhere("session-0")
        token = self.ledger.claim(chat["id"], worker_id="w")["token"]
        self.linear_says("session-0", "active")
        self.now = 1100.0
        self.redelegated()
        self.labelled(["Bug", "修改"], CHANGE)
        posted, self.now = len(self.sent()), 1220.0
        self.assertEqual(self.settle(), "told")
        self.assertEqual(self.sent()[posted:], [("session-0", {"type": "thought", "body": self.BUSY})])
        self.ledger.renew(chat["id"], token)
        self.assertEqual((self.ledger.item(chat["id"])["state"], self.active_jobs()), ("running", [chat["id"]]))
        self.assertEqual(self.episode(), ("told", None))

    def test_the_active_jobs_thread_is_told_by_what_linear_says_of_it(self):
        """§3.5: the thread of a waiting job gets the response unless Linear says the thread is closed or archived:
        it then blocks nothing, or nobody sees it. The thread of a job that goes on gets the thought unless it is
        archived. A thread whose state cannot be read is told from what the ledger knows: a note there closes no
        thread that holds no work."""
        response = {"type": "response", "body": self.WAITING_CHAT}
        thought = {"type": "thought", "body": self.BUSY}
        for waiting, status, archived, note in (
                (True, "awaitingInput", False, response), (True, "active", False, response),
                (True, None, False, response), (True, "complete", False, None), (True, "error", False, None),
                (True, "awaitingInput", True, None),
                (False, "active", False, thought), (False, "complete", False, thought), (False, None, False, thought),
                (False, "active", True, None)):
            with self.subTest(waiting=waiting, status=status, archived=archived):
                self.setUp()
                self.undelegated(("Bug", "修改"), CHANGE)
                chat = self.conversation_elsewhere("session-0")
                if waiting:
                    self.paused(chat, "哪个服？")
                else:
                    self.ledger.claim(chat["id"], worker_id="w")
                if status is None:
                    self.unreadable("session-0")
                else:
                    self.linear_says("session-0", status, archived)
                self.now = 1100.0
                self.redelegated()
                self.labelled(["Bug", "修改"], CHANGE)
                posted, self.now = len(self.sent()), 1220.0
                self.assertEqual(self.settle(), "told" if note else "unseen")
                self.assertEqual(self.sent()[posted:], [("session-0", note)] if note else [])
                self.assertEqual(self.ledger.item(chat["id"])["state"], "awaiting_input" if waiting else "running")
                self.assertEqual(self.settled["threads"], [{
                    "session_id": "session-0", "status": status, "archived": None if status is None else archived}])

    def test_a_waiting_write_job_whose_thread_cannot_be_read_is_told_and_kept(self):
        """U3: a delegation's fix waits while the labels name other work, and Linear does not say what state its
        thread is in. It is not taken in place; the thread gets the write job's note, and the fix stays parked."""
        self.running("feature")
        fix = self.waiting_fix("session-1")
        self.unreadable("session-1")
        self.now = 1100.0
        self.redelegated()
        self.labelled(["Code"], CODE)
        posted, self.now = len(self.sent()), 1220.0
        self.assertEqual(self.settle(), "told")
        self.assertEqual(self.sent()[posted:], [("session-1", {"type": "response", "body": self.WAITING})])
        self.assertEqual((self.ledger.item(fix["id"])["state"], self.active_jobs()), ("awaiting_input", [fix["id"]]))

    def test_a_busy_thread_is_told_once_in_thirty_minutes(self):
        """S15 for work that is not kept: the thought a busy thread gets ends nothing, so an automation that flaps
        the delegate gets one per job in 30 minutes. A response that ends a wait is never held back."""
        self.undelegated(("Bug", "修改"), CHANGE)
        chat = self.conversation_elsewhere("session-0")
        token = self.ledger.claim(chat["id"], worker_id="w")["token"]
        self.linear_says("session-0", "active")
        self.now = 1100.0
        self.redelegated()
        self.labelled(["Bug", "修改"], CHANGE)
        posted, self.now = len(self.sent()), 1220.0
        self.assertEqual(self.settle(), "told")
        busy = ("session-0", {"type": "thought", "body": self.BUSY})
        self.assertEqual(self.sent()[posted:], [busy])
        self.now = 2900.0
        self.redelegated()
        self.now = 3020.0  # 30 minutes after the thought
        self.assertEqual(self.settle(), "told")
        self.assertEqual((self.sent()[posted:], self.episode()), ([busy], ("told", None)))
        # The conversation then asks a question. The response that ends its wait is posted, however recent the thought.
        self.ledger.pop_inbox(chat["id"], token)
        self.ledger.await_input(chat["id"], token, "哪个服？")
        self.linear_says("session-0", "awaitingInput")
        self.now = 3030.0
        self.redelegated()
        self.now = 3150.0
        self.assertEqual(self.settle(), "told")
        told = [busy, ("session-0", {"type": "response", "body": self.WAITING_CHAT})]
        self.assertEqual(self.sent()[posted:], told)
        # That response was a line of its own for the job: answered and busy again, it gets no thought on top of it
        # within 30 minutes, and one once they are over.
        self.reply_in("session-0", "公共测试服")
        self.ledger.claim(chat["id"], worker_id="w")
        self.linear_says("session-0", "active")
        posted, self.now = len(self.sent()), 3200.0
        self.redelegated()
        self.now = 3320.0
        self.assertEqual(self.settle(), "told")
        self.assertEqual(self.sent()[posted:], [])
        self.now = 5000.0
        self.redelegated()
        self.now = 5120.0
        self.assertEqual(self.settle(), "told")
        self.assertEqual(self.sent()[posted:], [busy])

    def test_s7_an_open_thread_with_no_work_is_closed_and_an_unread_one_is_not(self):
        """S7, U2: a thread of the card that holds no work and that Linear shows as anything but complete or error
        is closed by one response, so that the next delegation can open a session. Linear adds status values, so
        any other value counts as open. A thread that is complete, archived or could not be read gets nothing, and
        the episode is then only recorded, for `doctor`. Nothing starts either way."""
        for status, archived, outcome in (
                ("awaitingInput", False, "told"), ("active", False, "told"), ("pending", False, "told"),
                ("stale", False, "told"), ("stopping", False, "told"), ("somethingNew", False, "told"),
                ("complete", False, "unseen"), ("error", False, "unseen"), ("awaitingInput", True, "unseen"),
                (None, False, "unseen")):
            with self.subTest(status=status, archived=archived):
                self.setUp()
                self.idle_thread("session-1")
                if status is None:
                    self.unreadable("session-1")
                else:
                    self.linear_says("session-1", status, archived)
                self.now = 1100.0
                self.redelegated()
                posted, self.now = len(self.sent()), 1220.0
                self.assertEqual(self.settle(), outcome)
                self.assertEqual(self.sent()[posted:], [("session-1", {"type": "response", "body": self.ENDED})]
                                 if outcome == "told" else [])
                self.assertEqual((self.episode(), self.active_jobs()), ((outcome, None), []))
                self.assertEqual(self.states_read(), ["session-1"])
                self.assertEqual(self.owed(), [])

    def test_a_thread_that_forwards_to_the_work_or_has_an_event_waiting_is_left_open(self):
        """§3.5, P5: a thread with no job of its own is not closed while its messages are forwarded to the card's
        work, since a Stop there must keep reaching that work, nor while an event of it is still to be handled. Such
        a thread is not even read. A thread with neither is closed."""
        self.running("feature")
        fix, _ = self.claimed_fix_elsewhere("session-0")
        self.mention_in("session-9", "@FarmBot 进展如何？")  # forwarded to the running fix
        self.assertEqual(self.ledger.stop_target("session-9")[1], "forwarded")
        self.ledger.ensure_session("session-7", ISSUE, False)
        self.ledger.ensure_session("session-6", ISSUE, False)
        for session in ("session-9", "session-7", "session-6"):
            self.linear_says(session, "awaitingInput")
        self.linear_says("session-0", "active")
        self.now = 1100.0
        self.redelegated()
        card = issue(labels=["Code"], delegate_id=APP, label_groups=CODE)

        def a_message_arrives(issue_id):
            # A person writes in session-7 while the settle reads the card: its event is pending when the settle acts.
            event = self.event("prompted", body="进展如何？",
                               agentSession={"id": "session-7", "issue": {"id": ISSUE, "identifier": "FARM-1",
                                                                          "url": "u"}})
            event["agentActivity"].update(id="act-7", agentSessionId="session-7")
            self.assertEqual(self.receive(event), (200, "accepted"))
            self.api.fetch_issue.side_effect = None
            return card
        self.api.fetch_issue.return_value = card
        self.api.fetch_issue.side_effect = a_message_arrives
        posted, self.now = len(self.sent()), 1220.0
        self.assertEqual(self.settle(), "told")
        self.assertEqual(self.sent()[posted:], [("session-0", {"type": "thought", "body": self.BUSY}),
                                                ("session-6", {"type": "response", "body": self.ENDED})])
        self.assertEqual(self.states_read(), ["session-0", "session-6"])
        self.assertEqual(self.ledger.item(fix["id"])["state"], "running")

    def test_the_thread_of_a_conversation_that_handed_its_work_over_is_left_open_while_that_work_goes_on(self):
        """§3.5, P5: a conversation that handed over to a job in the delegation's thread has no job of its own, but a
        reply or a Stop in its thread still reaches that job, as from a thread that forwards to it. While the job is
        active the thread is left open and not even read; once the job has ended it is a thread with no work, and
        one Linear shows as open is closed."""
        self.running("feature")
        fix = self.hand_over(*self.conversation_about_to_hand_over("session-9"))
        self.assertEqual(self.ledger.stop_target("session-9"), (fix, "own"))
        self.linear_says("session-9", "active")
        self.linear_says("session-1", "active", archived=True)  # so the fix is neither taken in place nor told
        self.now = 1100.0
        self.redelegated()
        self.labelled(["Code"], CODE)
        posted, self.now = len(self.sent()), 1220.0
        self.assertEqual(self.settle(), "unseen")
        self.assertEqual((self.sent()[posted:], self.states_read()), ([], ["session-1"]))
        self.assertEqual((self.ledger.item(fix["id"])["state"], self.active_jobs()), ("queued", [fix["id"]]))
        self.ledger.cancel(fix["id"], "operator cancelled")
        self.now = 1300.0
        self.redelegated()
        self.now = 1420.0
        self.assertEqual(self.settle(), "told")
        self.assertEqual(self.sent()[posted:], [("session-9", {"type": "response", "body": self.ENDED})])
        self.assertEqual((self.active_jobs(), self.owed()), ([], []))

    def test_work_that_is_being_withdrawn_gets_no_line_and_a_thread_that_waits_behind_it_stays_open(self):
        """A claimed fix that a newer delegation session is taking over is stopping: a line that says it goes on
        would be false, so its thread gets none, kept or told. The thread whose event waits behind that worker is
        left open, as any thread with an event still to be handled."""
        for labels, groups, outcome in ((["Bug", "修改"], CHANGE, "kept"), (["Code"], CODE, "told")):
            with self.subTest(outcome=outcome):
                self.setUp()
                self.running("feature")
                fix, _ = self.claimed_fix_elsewhere("session-0")
                self.ledger.flag_withdrawal(fix["id"], "superseded", self.now + 1200)
                self.ledger.ensure_session("session-7", ISSUE, True)
                self.ledger.ensure_session("session-6", ISSUE, False)
                with self.receiver.lock, self.receiver.db:
                    # The reply that re-routed session-7's delegation waits behind the claimed fix (WW C2, D16).
                    self.receiver.db.execute(
                        "INSERT INTO webhook_events VALUES (?,?,?,'deferred',?,?,NULL,?)",
                        ("org:prompted:act-7", "session-7", "ack-7",
                         json.dumps({"action": "prompted", "session_id": "session-7", "issue_id": ISSUE,
                                     "text": "开始吧", "is_mention": False, "guidance": ""}), self.now + 1,
                         fix["id"]))
                for session in ("session-0", "session-7", "session-6"):
                    self.linear_says(session, "awaitingInput")
                self.now = 1100.0
                self.redelegated()
                self.labelled(labels, groups)
                posted, self.now = len(self.sent()), 1220.0
                self.assertEqual(self.settle(), outcome)
                self.assertEqual(self.sent()[posted:], [] if outcome == "kept" else [
                    ("session-6", {"type": "response", "body": self.ENDED})])
                self.assertEqual(self.states_read(), [] if outcome == "kept" else ["session-6"])
                current = self.ledger.item(fix["id"])
                self.assertEqual((current["state"], current["withdraw_reason"]), ("running", "superseded"))
                self.assertEqual(self.receiver.results()[-1]["status"], "deferred")

    def test_at_most_ten_threads_are_read_and_a_failed_read_ends_the_reads(self):
        """§3.6, P8: a settle reads the card's ten newest threads at most. The first read Linear refuses ends the
        reads of that pass, and a thread that was not read is never closed."""
        def card_with_twelve_open_threads():
            self.setUp()
            self.ledger.observe_issue(issue(labels=["Bug", "修改"], delegate_id=APP, label_groups=CHANGE))
            for number in range(12):
                self.now = 1000.0 + number
                self.ledger.ensure_session(f"session-{number:02d}", ISSUE, number % 2 == 0)
                self.linear_says(f"session-{number:02d}", "awaitingInput")
            self.now = 1100.0
            self.redelegated()
            self.now = 1220.0
        card_with_twelve_open_threads()
        newest = [f"session-{number:02d}" for number in range(11, 1, -1)]
        self.assertEqual(self.settle(), "told")
        self.assertEqual(self.states_read(), newest)
        self.assertEqual(self.sent(), [(session, {"type": "response", "body": self.ENDED}) for session in newest])
        card_with_twelve_open_threads()
        self.unreadable("session-09")
        self.assertEqual(self.settle(), "told")
        self.assertEqual(self.states_read(), ["session-11", "session-10", "session-09"])
        self.assertEqual([session for session, _ in self.sent()], ["session-11", "session-10"])
        self.assertEqual(self.settled["threads"][2:], [{"session_id": "session-09", "status": None, "archived": None}])
        # The thread of the card's active job is read first and is one of the ten, however old it is.
        card_with_twelve_open_threads()
        chat = self.ledger.create_work_item(issue_id=ISSUE, session_id="session-01", skill="chat")
        self.ledger.claim(chat["id"], worker_id="w")
        self.assertEqual(self.settle(), "told")
        self.assertEqual(self.states_read(), ["session-01", *newest[:-1]])
        ended = [(session, {"type": "response", "body": self.ENDED}) for session in newest[:-1]]
        self.assertEqual(self.sent(), [("session-01", {"type": "thought", "body": self.BUSY}), *ended])

    def test_a_note_is_not_posted_into_a_thread_whose_work_changed_under_the_settle(self):
        """P9: each note is for the thread as the settle found it. A conversation that a message resumed while the
        settle read the other threads does not get the response that says it waits; a thread that got work in the
        meantime is not told that it holds none."""
        self.undelegated(("Bug", "修改"), CHANGE)
        self.finish(self.conversation_elsewhere("session-8"))
        chat = self.waiting_mention("session-0")
        self.now = 1100.0
        self.redelegated()
        self.labelled(["Bug", "修改"], CHANGE)

        def answered_meanwhile(session_id, issue_id, app_user_id):
            if session_id == "session-8":
                self.ledger.push_inbox(chat["id"], "公共测试服", resume_waiting=True)
            return {"status": "awaitingInput", "archived": False}
        self.api.session_state.side_effect = answered_meanwhile
        posted, self.now = len(self.sent()), 1220.0
        self.assertEqual(self.settle(), "told")
        self.assertEqual(self.ledger.item(chat["id"])["state"], "queued")
        self.assertEqual(self.sent()[posted:], [("session-8", {"type": "response", "body": self.ENDED})])
        # A thread with no work when the settle looked, which has work by the time its note would be posted.
        self.setUp()
        self.idle_thread("session-1")
        self.ledger.ensure_session("session-2", ISSUE, False)
        for session in ("session-1", "session-2"):
            self.linear_says(session, "awaitingInput")
        self.now = 1100.0
        self.redelegated()

        def work_starts(session_id, content, activity_id=None):
            if session_id == "session-2":
                self.ledger.create_work_item(issue_id=ISSUE, session_id="session-1", skill="chat")
            return {"success": True}
        self.api.create_activity.side_effect = work_starts
        posted, self.now = len(self.sent()), 1220.0
        self.assertEqual(self.settle(), "told")
        self.assertEqual(self.sent()[posted:], [("session-2", {"type": "response", "body": self.ENDED})])
        # A conversation that ended while the settle read its thread: the cancel closed the thread with its own last
        # word, so the thread gets neither the note that says it waits nor, as a thread left with no work, the one
        # that closes it.
        self.setUp()
        chat = self.waiting_mention("session-0")
        self.now = 1100.0
        self.redelegated()
        self.labelled(["Bug", "修改"], CHANGE)

        def ended_meanwhile(session_id, issue_id, app_user_id):
            self.ledger.cancel(chat["id"], "operator cancelled")
            return {"status": "awaitingInput", "archived": False}
        self.api.session_state.side_effect = ended_meanwhile
        posted, self.now = len(self.sent()), 1220.0
        self.assertEqual(self.settle(), "told")
        self.assertEqual((self.sent()[posted:], self.states_read(), self.owed()), ([], ["session-0"], []))

    def test_a_refused_note_is_owed(self):
        """P10 (TS21): the response that ends a thread's wait is a closing activity. When Linear refuses it, it is
        owed to the thread: for the waiting job, so that it goes once the job moves on, or for no job in a thread
        that holds none."""
        chat = self.waiting_mention("session-0")
        self.now = 1100.0
        self.redelegated()
        self.labelled(["Bug", "修改"], CHANGE)
        self.refusing("session-0")
        self.now = 1220.0
        self.assertEqual(self.settle(), "told")
        self.assertEqual(self.owed(), [("session-0", chat["id"], "awaiting_input", "response", self.WAITING_CHAT)])
        self.assertEqual(self.episode(), ("told", None))
        self.api.create_activity.side_effect = None
        self.reply_in("session-0", "公共测试服")  # the conversation goes on: the note no longer holds
        self.assertEqual(self.owed(), [])
        self.setUp()
        self.idle_thread("session-1")
        self.linear_says("session-1", "awaitingInput")
        self.now = 1100.0
        self.redelegated()
        self.refusing("session-1")
        self.now = 1220.0
        self.assertEqual(self.settle(), "told")
        self.assertEqual(self.owed(), [("session-1", None, None, "response", self.ENDED)])
        [row] = self.ledger.connection.execute("SELECT * FROM session_closures").fetchall()
        self.assertEqual((row["issue_id"], row["last_error"], row["due_at"]), (ISSUE, "RuntimeError", 1280.0))
        self.api.create_activity.side_effect = None
        self.now = 1280.0
        self.assertTrue(SessionProgress(self.ledger, self.api).tick())  # the progress loop posts it
        self.assertEqual((self.said_in("session-1")[-1], self.owed()), (self.ENDED, []))

    def test_a_refused_line_that_closes_nothing_is_not_owed(self):
        """P10: a thought and a repeated question close no thread, so neither is owed. The takeover in place is done
        whatever becomes of its thought, and a kept job whose line Linear refused is asked again at the next
        episode, not 30 minutes later."""
        chat = self.waiting_chat("session-0")
        self.linear_says("session-0", "awaitingInput")
        self.now = 1100.0
        self.redelegated()
        self.labelled(["Bug", "修改"], CHANGE)
        self.refusing("session-0")
        self.now = 1220.0
        self.assertEqual(self.settle(), "in_place")
        fix = self.ledger.active_item_for_issue(ISSUE)
        self.assertEqual((fix["skill"], fix["state"], self.ledger.item(chat["id"])["state"]),
                         ("fix", "queued", "cancelled"))
        self.assertEqual((self.owed(), self.episode()), ([], ("in_place", "session-0")))
        self.paused(fix, "哪个服？")
        self.now = 1300.0
        self.redelegated()
        self.now = 1420.0
        self.assertEqual(self.settle(), "kept")
        self.assertEqual(self.sent()[-1], ("session-0", {"type": "elicitation", "body": self.ASKED_AGAIN}))
        self.assertEqual(self.owed(), [])
        self.api.create_activity.side_effect = None
        self.now = 1500.0
        self.redelegated()
        self.now = 1620.0
        posted = len(self.sent())
        self.assertEqual(self.settle(), "kept")
        self.assertEqual(self.sent()[posted:], [("session-0", {"type": "elicitation", "body": self.ASKED_AGAIN})])

    def test_the_notes_name_the_instance(self):
        self.running(bot_name="TestBot")
        self.idle_thread("session-1")
        self.linear_says("session-1", "awaitingInput")
        self.now = 1100.0
        self.redelegated()
        self.now = 1220.0
        self.assertEqual(self.settle(), "told")
        body = self.sent()[-1][1]["body"]
        self.assertEqual(body, SILENT_ENDED.format(bot="TestBot"))
        self.assertNotIn("FarmBot", body)

    # --- Heard, dropped, unseen, and the settle's own timing (P11, §3.2-§3.5) ---

    def test_s9_a_created_on_time_is_heard_whichever_read_came_first(self):
        """S9: Linear opens a session, and its `created` races the Issue update. Whichever read clears the mark
        opens the episode; the delegation session recorded at or after the mark explains it. Past the grace nothing
        is posted and no thread is read."""
        self.idle_thread("session-0")
        lifecycle = self.status_reads(None)
        self.now = 1100.0
        lifecycle.refresh(ISSUE)
        self.read_delegate, self.now = APP, 1130.0
        lifecycle.refresh(ISSUE)  # the Issue update's read comes first
        self.now = 1132.0
        self.delegate("session-1")
        posted, self.now = len(self.sent()), 1220.0
        self.assertEqual(self.settle(), "heard")
        self.assertEqual((self.episode(), self.sent()[posted:]), (("heard", None), []))
        self.read_delegate, self.now = None, 1300.0
        lifecycle.refresh(ISSUE)
        self.now = 1302.0
        [new] = self.delegate("session-2")  # the session event's own read comes first
        self.assertEqual(self.ledger.episode(ISSUE)["since"], 1302.0)
        posted, self.now = len(self.sent()), 1392.0
        self.assertEqual(self.settle(), "heard")
        self.assertEqual((self.sent()[posted:], self.states_read(), self.active_jobs()), ([], [], [new["id"]]))

    def test_s1_prime_a_redelegation_after_the_confirming_read_opens_a_session_and_is_heard(self):
        """S1': 60 seconds or more after the removal, the confirming read has cancelled the delegation's
        conversation with its one response, so its thread is complete and Linear opens a session for the new
        delegation. That is today's path, and the episode its read opens is heard."""
        self.real_scheduler()
        chat = self.waiting_chat("session-0")
        lifecycle = self.status_reads(None)
        self.now = 1100.0
        lifecycle.refresh(ISSUE)
        self.now = 1160.0
        lifecycle.refresh(ISSUE)
        self.assertEqual(self.ledger.item(chat["id"])["state"], "cancelled")
        self.assertEqual(self.said_in("session-0"), [UNDELEGATED_CHAT.format(bot="FarmBot")])
        self.now = 1170.0
        self.labelled(["Bug", "修改"], CHANGE)
        [fix] = self.delegate("session-1")
        self.assertEqual(self.ledger.episode(ISSUE)["since"], 1170.0)
        posted, self.now = len(self.sent()), 1260.0
        self.assertEqual(self.settle(), "heard")
        self.assertEqual((self.sent()[posted:], self.active_jobs()), ([], [fix["id"]]))

    def test_a_deferred_or_stopped_created_explains_the_episode(self):
        """§3.2 (TS20): a delegation `created` that waits behind another session's worker, or that a Stop cancelled
        at or after the mark, is a session Linear opened: the delegation was heard."""
        self.claimed_fix_elsewhere("session-0")
        self.now = 1010.0
        self.assertEqual(self.delegate("session-1"), [])  # deferred behind the claimed fix
        lifecycle = self.status_reads(None)
        self.now = 1100.0
        lifecycle.refresh(ISSUE)
        self.read_delegate, self.now = APP, 1130.0
        lifecycle.refresh(ISSUE)
        posted, self.now = len(self.sent()), 1220.0
        self.assertEqual(self.settle(), "heard")
        self.assertEqual((self.sent()[posted:], self.receiver.results()[-1]["status"]), ([], "deferred"))
        self.read_delegate, self.now = None, 1240.0
        lifecycle.refresh(ISSUE)
        self.now = 1250.0
        self.stop_in("session-1")
        self.assertEqual(self.receiver.results()[-1]["status"], "cancelled")
        posted = len(self.sent())
        self.read_delegate, self.now = APP, 1260.0
        lifecycle.refresh(ISSUE)
        self.now = 1350.0
        self.assertEqual(self.settle(), "heard")
        self.assertEqual((self.sent()[posted:], self.states_read(), self.episode()), ([], [], ("heard", None)))

    def test_s10_an_api_delegation_with_no_open_thread_is_only_recorded(self):
        """S10: a delegation through Linear's API opens no session at all. On a card FarmBot tracks with no thread of
        its own, nothing is posted and nothing starts: the episode is recorded for `doctor`."""
        self.ledger.observe_issue(issue(labels=["Bug", "修改"], label_groups=CHANGE))
        self.now = 1100.0
        self.redelegated()
        self.api.reset_mock()
        self.now = 1220.0
        self.assertEqual(self.settle(), "unseen")
        self.assertEqual((self.episode(), self.active_jobs()), (("unseen", None), []))
        self.assertEqual([call[0] for call in self.api.method_calls], ["fetch_issue"])
        self.assertEqual(self.settled, {"event": "delegation_episode", "issue_id": ISSUE, "outcome": "unseen",
                                        "threads": []})
        self.assertIsNone(self.settle())

    def test_s11_a_card_no_longer_delegated_at_the_settle_is_dropped(self):
        """S11: the delegation was removed during the grace and no status read has seen it yet. The settle's own
        read finds it: the episode is dropped and nothing is posted. As any read of the receiver that finds the
        delegation gone while its work is active, it asks for a status read now, so P3 runs."""
        fix = self.waiting_fix("session-1")
        self.now = 1100.0
        self.redelegated()
        self.undelegated(("Bug", "修改"), CHANGE)
        check = self.ledger.status_check(ISSUE)
        self.assertEqual((check["requested"], check["due_at"]), (0, 1190.0))
        posted, self.now = len(self.sent()), 1220.0
        self.assertEqual(self.settle(), "dropped")
        self.assertEqual((self.sent()[posted:], self.episode(), self.states_read()), ([], ("dropped", None), []))
        check = self.ledger.status_check(ISSUE)
        self.assertEqual((check["requested"], check["due_at"], check["undelegated_since"]), (1, 0, None))
        self.assertEqual(self.ledger.item(fix["id"])["state"], "awaiting_input")
        self.assertIsNone(self.settle())

    def test_a_dropped_episode_asks_for_no_status_read_when_no_delegation_work_is_active(self):
        """A mention's conversation does not depend on the delegation, so nothing needs to be read sooner."""
        chat = self.waiting_mention("session-0")
        self.now = 1100.0
        self.redelegated()
        posted, self.now = len(self.sent()), 1220.0  # every receiver read still finds the card delegated to nobody
        self.assertEqual(self.settle(), "dropped")
        self.assertEqual(self.ledger.status_check(ISSUE)["requested"], 0)
        self.assertEqual((self.ledger.item(chat["id"])["state"], self.sent()[posted:]), ("awaiting_input", []))

    def test_s11_a_status_read_that_finds_the_delegation_gone_ends_the_episode_before_the_settle(self):
        """S11, S15: a read that started after the episode's own and found the card not delegated here drops it.
        Nothing is due any more, and the receiver reads nothing."""
        self.waiting_fix("session-1")
        lifecycle = self.status_reads(None)
        self.now = 1100.0
        lifecycle.refresh(ISSUE)
        self.read_delegate, self.now = APP, 1130.0
        lifecycle.refresh(ISSUE)
        self.read_delegate, self.now = None, 1150.0
        lifecycle.refresh(ISSUE)
        self.assertEqual(self.episode(), ("dropped", None))
        reads, self.now = self.api.fetch_issue.call_count, 1300.0
        self.assertIsNone(self.settle())
        self.assertEqual(self.api.fetch_issue.call_count, reads)

    def test_s12_a_restart_during_the_grace_settles_once(self):
        """S12: the episode is in the ledger and its due time is absolute. A controller that restarts during the
        grace waits out the rest of it; one that comes back long after settles the episode in its first pass, once."""
        self.waiting_fix("session-1")
        self.now = 1100.0
        self.redelegated()
        self.now = 1150.0
        self.running()
        self.assertIsNone(self.settle())
        self.now = 5000.0
        self.running()
        posted = len(self.sent())
        self.assertEqual(self.settle(), "kept")
        self.assertEqual(self.sent()[posted:], [("session-1", {"type": "elicitation", "body": self.ASKED_AGAIN})])
        self.assertIsNone(self.settle())
        self.running()
        self.assertIsNone(self.settle())
        self.assertEqual(len(self.sent()), posted + 1)

    def test_s13_two_quick_redelegations_settle_once_against_the_last_mark(self):
        """S13: each read that finds the delegation gone drops the waiting episode, and each read that finds it
        back after a mark opens one. Only the last is settled, against its own mark: the session Linear opened for
        the first re-delegation is older than that mark, so it does not explain the second."""
        self.idle_thread("session-0")
        lifecycle = self.status_reads(None)
        self.now = 1100.0
        lifecycle.refresh(ISSUE)
        self.read_delegate, self.now = APP, 1110.0
        lifecycle.refresh(ISSUE)
        self.now = 1112.0
        [fix] = self.delegate("session-1")  # Linear opened a session for the first one
        self.read_delegate, self.now = None, 1120.0
        lifecycle.refresh(ISSUE)
        self.read_delegate, self.now = APP, 1130.0
        lifecycle.refresh(ISSUE)  # and none for the second
        episode = self.ledger.episode(ISSUE)
        self.assertEqual((episode["state"], episode["since"], episode["mark"], episode["due_at"]),
                         ("waiting", 1130.0, 1120.0, 1220.0))
        posted, self.now = len(self.sent()), 1200.0  # when the first episode would have been due
        self.assertIsNone(self.settle())
        self.now = 1220.0
        self.assertEqual(self.settle(), "kept")
        self.assertEqual(self.sent()[posted:], [("session-1", {"type": "thought", "body": self.GOES_ON})])
        self.assertEqual((self.ledger.item(fix["id"])["state"], self.episode()), ("queued", ("served", "session-1")))
        self.assertIsNone(self.settle())

    def test_s14_a_card_closed_during_the_grace_is_dropped(self):
        """S14: closure ends the card's work and posts its notices through the status reads, as ever. The settle's
        read finds the card closed or archived and drops the episode: it posts nothing and takes nothing over."""
        for closed in ({"status": "Done", "status_type": "completed"},
                       {"status": "Canceled", "status_type": "canceled"},
                       {"status": "Duplicate", "status_type": "duplicate"}, {"archived": True}):
            with self.subTest(closed=closed):
                self.setUp()
                chat = self.waiting_chat("session-0")
                self.linear_says("session-0", "awaitingInput")
                self.now = 1100.0
                self.redelegated()
                self.labelled(["Bug", "修改"], CHANGE, **closed)
                posted, self.now = len(self.sent()), 1220.0
                self.assertEqual(self.settle(), "dropped")
                self.assertEqual((self.sent()[posted:], self.episode(), self.states_read()),
                                 ([], ("dropped", None), []))
                self.assertEqual(self.active_jobs(), [chat["id"]])
                self.assertEqual(self.ledger.status_check(ISSUE)["requested"], 0)

    def test_s20_a_failing_card_read_is_retried_and_posts_nothing(self):
        """S20, P8: while the card cannot be read, nothing is posted and nothing moves. The settle is tried again
        after 15, 30, 60, 120 and 240 seconds, then every 300, and the failures are counted and named for `doctor`.
        The first read that works settles the episode."""
        fix = self.waiting_fix("session-1")
        self.now = 1100.0
        self.redelegated()
        posted, self.now = len(self.sent()), 1220.0
        self.api.fetch_issue.side_effect = RuntimeError("linear down")
        for attempt, delay in enumerate((15, 30, 60, 120, 240, 300, 300), 1):
            with self.subTest(attempt=attempt):
                self.assertEqual(self.settle(), "retry")
                episode = self.ledger.episode(ISSUE)
                self.assertEqual((episode["state"], episode["attempts"], episode["error"]),
                                 ("waiting", attempt, "RuntimeError"))
                self.assertEqual(episode["due_at"] - self.now, delay)
                self.now = episode["due_at"] - 1
                self.assertIsNone(self.settle())
                self.now += 1
        self.assertEqual((self.sent()[posted:], self.ledger.item(fix["id"])["state"]), ([], "awaiting_input"))
        self.api.fetch_issue.side_effect = None
        self.assertEqual(self.settle(), "kept")
        episode = self.ledger.episode(ISSUE)
        self.assertEqual((episode["state"], episode["attempts"], episode["error"]), ("served", 7, None))
        self.assertEqual(len(self.sent()), posted + 1)

    def test_a_card_read_that_answers_for_another_card_settles_nothing(self):
        """P8: the settle acts only on a read of the episode's own card."""
        fix = self.waiting_fix("session-1")
        self.now = 1100.0
        self.redelegated()
        self.api.fetch_issue.return_value = issue(id=OTHER, identifier="FARM-2", delegate_id=None)
        posted, self.now = len(self.sent()), 1220.0
        self.assertEqual(self.settle(), "retry")
        episode = self.ledger.episode(ISSUE)
        self.assertEqual((episode["state"], episode["error"]), ("waiting", "ValueError"))
        self.assertEqual((self.sent()[posted:], self.ledger.item(fix["id"])["state"]), ([], "awaiting_input"))
        with self.assertRaises(LedgerError):
            self.ledger.issue(OTHER)  # and nothing of that answer is stored

    def test_one_due_episode_is_settled_per_pass_the_longest_due_first(self):
        """§3.4: a pass settles one episode, so that an event that arrives meanwhile is handled before the next."""
        cards = {ISSUE: issue(delegate_id=APP), OTHER: issue(id=OTHER, identifier="FARM-2", delegate_id=APP)}
        self.api.fetch_issue.side_effect = lambda issue_id: cards[issue_id]
        for issue_id, since in ((ISSUE, 1130.0), (OTHER, 1120.0)):
            self.ledger.observe_issue(cards[issue_id])
            self.ledger.mark_undelegated(issue_id, 1100.0)
            self.ledger.clear_undelegated(issue_id, observed_at=since)
        self.now = 1300.0
        settled = []
        for _ in range(2):
            self.assertEqual(self.settle(), "unseen")
            settled.append(self.settled["issue_id"])
        self.assertEqual(settled, [OTHER, ISSUE])
        self.assertIsNone(self.settle())
        self.assertEqual(self.sent(), [])
