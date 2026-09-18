import hashlib
import hmac
import json
import sqlite3
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import Mock

from agent.ledger import Ledger
from agent.receiver import Receiver, make_server
from test_ledger import ISSUE, issue

APP = "e5a8c16d-9f85-4123-acf5-94e41c3304d5"
IDENTITY = {"oauthClientId": "client", "appUserId": APP, "organizationId": "org"}


class ReceiverBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "ledger.sqlite3"
        self.api = Mock()
        self.api.fetch_issue.return_value = issue(labels=["Bug"], delegate_id=APP)
        self.api.create_activity.return_value = {"success": True, "agentActivity": {"id": "act"}}
        self.scheduler = Mock()
        self.receiver = Receiver(self.db, "signing-secret", IDENTITY, self.api, lambda: Ledger(self.db),
                                 skills={"chat", "fix"}, scheduler=self.scheduler)
        self.addCleanup(self.receiver.close)
        self.ledger = Ledger(self.db)
        self.addCleanup(self.ledger.close)

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


class ReceiverTests(ReceiverBase):
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
        self.receive(); self.receiver.process_one()
        item = self.ledger.items_for_session("session-1")[0]
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.ledger.await_input(item["id"], token, "which server?")
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
        self.scheduler.stop.assert_called_once_with(item["id"], "Linear stop")
        self.assertEqual(self.activities()[-1]["type"], "response")
        self.assertEqual(self.receive(stop), (200, "duplicate"))

    def test_duplicates_bad_signature_stale_timestamp_and_wrong_identity(self):
        self.receive()
        self.assertEqual(self.receive(), (200, "duplicate"))
        self.assertEqual(self.receive(signature="bad")[0], 401)
        self.assertEqual(self.receive(self.event(webhookTimestamp=0))[0], 401)
        self.assertEqual(self.receive(self.event(appUserId="someone"))[0], 403)
        self.assertEqual(self.receive({"type": "Issue", "action": "update", "webhookTimestamp": 100_000}), (200, "ignored"))

    def test_delegation_without_bug_label_elicits_without_creating_an_item(self):
        self.api.fetch_issue.return_value = issue(labels=["需求"], delegate_id=APP)
        self.receive(); self.receiver.process_one()
        self.assertEqual(self.ledger.items_for_session("session-1"), [])
        self.assertEqual(self.activities()[0]["type"], "elicitation")

    def test_api_failure_marks_event_uncertain_not_done(self):
        self.api.fetch_issue.side_effect = RuntimeError("boom")
        self.receive(); self.receiver.process_one()
        self.assertEqual(self.receiver.results()[0]["status"], "uncertain")

    def test_delegation_from_a_second_session_on_an_active_issue_is_declined(self):
        self.receive(); self.receiver.process_one()
        other = self.event(agentSession={"id": "session-2", "issue": {"id": ISSUE, "identifier": "FARM-1", "url": "u"}})
        self.receive(other); self.receiver.process_one()
        self.assertEqual(self.ledger.items_for_session("session-2"), [])
        self.assertEqual(self.activities()[-1]["type"], "response")
        self.assertIn("进行中", self.activities()[-1]["body"])
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

    def test_qa_words_without_qa_skill_explain_to_the_human_and_keep_their_text_for_the_worker(self):
        self.api.fetch_issue.return_value = issue(labels=["Bug"], delegate_id=None)
        self.receive(self.event(agentSession={"id": "session-4", "issue": {"id": ISSUE, "identifier": "FARM-1", "url": "u"},
                                              "comment": {"body": "@FarmBot 帮我复现一下"}}))
        self.receiver.process_one()
        item = self.ledger.items_for_session("session-4")[0]
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.assertEqual(self.ledger.pop_inbox(item["id"], token), ["@FarmBot 帮我复现一下"])
        self.assertIn("qa", self.activities()[-1]["body"])


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
