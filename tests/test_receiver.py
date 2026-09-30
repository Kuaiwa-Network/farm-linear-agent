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
                              FORWARD_WITHDRAWING, MOVED_THREAD, RESUME_UNDELEGATED, RESUMED_ELSEWHERE, STOP_ALREADY,
                              STOP_ELSEWHERE, STOP_MOVED, STOP_MOVED_THREAD, STOPPED_ELSEWHERE, STOPPED_ELSEWHERE_CHAT,
                              SUPERSEDE_SUFFIX, SUPERSEDED)
from agent.worktrees import WorktreeError
from test_ledger import DESIGNER, ISSUE, OTHER, OWNER, issue
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
        self.reply_in("session-1", "公共测试服")
        self.assertIsNone(self.ledger.status_check(ISSUE)["undelegated_since"])
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
