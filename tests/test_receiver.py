import hashlib
import hmac
import json
import sqlite3
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent.ledger import Ledger
from agent.receiver import Receiver, make_server
from agent.worktrees import WorktreeError
from test_ledger import ISSUE, issue

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
        self.assertEqual(self.receive({"type": "Issue", "action": "update", "webhookTimestamp": 100_000}), (403, "identity mismatch"))

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

    def test_a_first_event_that_only_elicits_still_announces_the_pin(self):
        """The pin is stored before routing and the echo is guarded on target is None, so a session that does
        not create work on its first event would otherwise pin in silence and never announce it."""
        self.api.fetch_issue.return_value = issue(labels=["需求"], delegate_id=APP)
        self.receiver.worktrees = SimpleNamespace(remote_head=lambda repo, timeout=8: "c" * 40)
        self.receive()
        self.assertTrue(self.receiver.process_one())
        self.assertEqual(self.ledger.items_for_session("session-1"), [])
        self.assertEqual(self.activities()[0]["type"], "elicitation")
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
                                 skills={"chat", "fix", "qa"}, scheduler=self.scheduler, bot_name=name)
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
        self.assertEqual(self.activities()[-1]["body"], "FarmBot 已收到委派，正在排队处理这个缺陷。进展和草稿 PR 会更新在这里。")
        self.ledger.cancel(self.ledger.items_for_session("session-1")[0]["id"], "test")
        self.receive(self.mention("session-2", "@FarmBot 这个 bug 是客户端还是服务端的？")); self.receiver.process_one()
        self.assertEqual(self.activities()[-1]["body"], "FarmBot 已收到，正在查看。")

    def test_default_receiver_keeps_the_production_resume_and_error_text(self):
        self.assertEqual(self.answer_after_undelegation(), "已保存回复；issue 已不再委派给 FarmBot，暂不继续修复。")
        self.api.fetch_issue.side_effect = KeyError("labels")
        self.receive(self.event(agentSession={"id": "session-9", "issue": {"id": ISSUE}})); self.receiver.process_one()
        self.assertEqual(self.activities()[-1], {"type": "error", "body": "FarmBot 处理这条消息时出错（KeyError），请稍后重试或联系维护者。"})

    def test_default_receiver_keeps_the_production_elicitation(self):
        self.api.fetch_issue.return_value = issue(labels=["需求"], delegate_id=APP)
        self.receive(); self.receiver.process_one()
        self.assertEqual(self.activities()[-1]["body"], "这个 issue 需要我做什么？请回复「修复」让我处理缺陷，或改为 @FarmBot 提问。"
                                                        "没有 Bug 标签的委派我不会自动开工。")

    def test_a_named_instance_acknowledges_as_itself(self):
        self.receiver_named("TestBot")
        self.receive(); self.receiver.process_one()
        self.assertEqual(self.activities()[-1]["body"], "TestBot 已收到委派，正在排队处理这个缺陷。进展和草稿 PR 会更新在这里。")
        self.ledger.cancel(self.ledger.items_for_session("session-1")[0]["id"], "test")
        self.receive(self.mention("session-2", "@TestBot 这个 bug 是客户端还是服务端的？")); self.receiver.process_one()
        self.assertEqual(self.activities()[-1]["body"], "TestBot 已收到，正在查看。")
        self.ledger.cancel(self.ledger.items_for_session("session-2")[0]["id"], "test")
        self.receive(self.mention("session-3", "@TestBot 帮我复现一下")); self.receiver.process_one()
        self.assertEqual(self.activities()[-1]["body"], "TestBot 已收到测试请求，正在排队。")

    def test_a_named_instance_never_says_farmbot_in_resume_error_or_elicitation_text(self):
        self.receiver_named("TestBot")
        self.assertEqual(self.answer_after_undelegation(), "已保存回复；issue 已不再委派给 TestBot，暂不继续修复。")
        self.api.fetch_issue.side_effect = KeyError("labels")
        self.receive(self.event(agentSession={"id": "session-9", "issue": {"id": ISSUE}})); self.receiver.process_one()
        self.assertEqual(self.activities()[-1]["body"], "TestBot 处理这条消息时出错（KeyError），请稍后重试或联系维护者。")
        self.api.fetch_issue.side_effect = None
        self.api.fetch_issue.return_value = issue(labels=["需求"], delegate_id=APP)
        self.receive(self.event(agentSession={"id": "session-10", "issue": {"id": ISSUE}})); self.receiver.process_one()
        self.assertIn("@TestBot 提问", self.activities()[-1]["body"])
        self.assertFalse([a for a in self.activities() if "FarmBot" in a["body"]])
