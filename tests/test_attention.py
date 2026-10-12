"""Human attention survives parallel work, partial replies and controller restarts."""
from types import SimpleNamespace
import unittest

from agent.attention import QUESTION_KEY, question_notice, questions, reconcile_reply_label
from agent.ledger import LedgerError
from agent.lifecycle import Lifecycle
from agent.linear_api import LinearAPI
from agent.workflow import await_tasks, forward_answer, wake_parent
import test_general_workflow as workflow_fixtures
from test_ledger import LedgerBase, ISSUE, OWNER
from test_linear_api import APP, FakeHTTP


class QuestionTests(LedgerBase):
    def ask(self):
        self.item = self.new_item()["id"]
        token = self.ledger.claim(self.item, worker_id="worker")["token"]
        self.ledger.await_input(self.item, token, "Q1 asset? Q2 composition?")
        return self.ledger.issue_context(self.item)["human_question"]

    def reply(self, body="Use asset A; Q2 undecided", author=OWNER):
        self.ledger.push_inbox(self.item, body, author=author, resume_waiting=True)
        mid = self.ledger.issue_context(self.item)["session_messages"][-1]["id"]
        token = self.ledger.claim(self.item, worker_id="resumed")["token"]
        return token, mid

    def test_reply_checkpoint_and_restart_do_not_clear_partial_answers(self):
        original = self.ask()
        token, _ = self.reply()
        self.ledger.pop_inbox(self.item, token)
        self.ledger.checkpoint(self.item, token, {"stage": "server", QUESTION_KEY: None})
        self.ledger.close()
        self.ledger = self.open_ledger()
        self.assertEqual(questions(self.ledger, ISSUE)[0]["id"], original["id"])
        self.assertEqual(self.ledger.issue_context(self.item)["human_question"], original)

    def test_acknowledgment_requires_current_round_and_attributed_own_reply(self):
        question = self.ask()
        token, mid = self.reply("Both decided")
        with self.assertRaisesRegex(LedgerError, "current human_question"):
            self.ledger.resolve_question(self.item, token, "old-round", [mid])
        for mids in ([], [0], [True], [mid + 10]):
            with self.subTest(mids=mids), self.assertRaises(LedgerError):
                self.ledger.resolve_question(self.item, token, question["id"], mids)
        self.ledger.resolve_question(self.item, token, question["id"], [mid])
        self.ledger.checkpoint(self.item, token, {})
        self.assertEqual(questions(self.ledger, ISSUE), [])
        self.assertIsNone(self.ledger.issue_context(self.item)["human_question"])

    def test_unattributed_or_previous_round_reply_cannot_acknowledge(self):
        self.ask()
        token, old = self.reply()
        self.ledger.pop_inbox(self.item, token)
        self.ledger.await_input(self.item, token, "Only Q2 remains")
        question = self.ledger.issue_context(self.item)["human_question"]
        token, unknown = self.reply(author=None)
        for mid in (old, unknown):
            with self.subTest(mid=mid), self.assertRaisesRegex(LedgerError, "attributed human replies"):
                self.ledger.resolve_question(self.item, token, question["id"], [mid])
        with self.assertRaises(LedgerError):
            self.ledger.resolve_question(self.item, "stale-token", question["id"], [unknown])

    def test_pre_upgrade_question_survives_first_resumed_checkpoint(self):
        self.ask()
        self.ledger.connection.execute("UPDATE work_items SET checkpoint=json_remove(checkpoint,'$.human_question') WHERE id=?",
                                       (self.item,))
        legacy = self.ledger.issue_context(self.item)["human_question"]
        token, mid = self.reply("Both decided")
        self.ledger.checkpoint(self.item, token, {})
        self.assertEqual(self.ledger.issue_context(self.item)["human_question"], legacy)
        self.ledger.resolve_question(self.item, token, legacy["id"], [mid])
        self.assertEqual(questions(self.ledger, ISSUE), [])

    def test_waiting_pause_is_not_a_question_and_terminal_work_retires_questions(self):
        self.ask()
        self.ledger.cancel(self.item, "Stop")
        self.assertEqual(questions(self.ledger, ISSUE), [])
        other = self.new_item()["id"]
        token = self.ledger.claim(other, worker_id="worker")["token"]
        self.ledger.await_input(other, token, "Please merge the PR", reason="waiting")
        self.assertEqual(questions(self.ledger, ISSUE), [])

    def test_question_notice_names_bot_owner_issue_and_reply_routes(self):
        self.ask()
        context = SimpleNamespace(issue_context=lambda _: {"owner": {"person": OWNER}})
        body = question_notice(context, self.ledger.item(self.item), "Q6: badge?", "TestBot")
        self.assertIn("🔴 FARM-1 待回复", body)
        self.assertIn(OWNER["url"], body)
        self.assertIn("Q6: badge?", body)
        self.assertIn("TestBot 的会话", body)
        self.assertIn("@TestBot", body)
        self.assertIn("普通评论不自动唤醒", body)
        self.assertEqual(question_notice(context, self.ledger.item(self.item), OWNER["url"] + " Q6?", "TestBot")
                         .count(OWNER["url"]), 1)


class ParallelAttentionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = workflow_fixtures.GeneralWorkflowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.ledger = self.fixture.ledger

    def test_child_question_stays_visible_while_parent_runs_or_joins(self):
        f = self.fixture
        child = f.task()
        self.ledger.await_input(child["id"], f.claim_task(child), "Q6 badge composition?")
        self.assertEqual(self.ledger.item(f.parent)["state"], "running")
        self.assertEqual([q["task_key"] for q in questions(self.ledger, ISSUE)], ["ui"])
        await_tasks(self.ledger, f.parent, f.token)
        self.assertTrue(wake_parent(self.ledger, f.parent))
        self.assertEqual(len(questions(self.ledger, ISSUE)), 1)
        f.token = self.ledger.claim(f.parent, worker_id="coordinator")["token"]
        message_id = f.message("Badge independent; use existing VIP visibility")
        forward_answer(self.ledger, f.parent, f.token, "ui", message_id)
        child_token = f.claim_task(child)
        context = self.ledger.issue_context(child["id"])
        self.assertEqual(len(questions(self.ledger, ISSUE)), 1)
        self.ledger.resolve_question(child["id"], child_token, context["human_question"]["id"],
                                     [context["session_messages"][-1]["id"]])
        self.assertEqual(questions(self.ledger, ISSUE), [])

    def test_resolving_parent_does_not_clear_child_or_other_issue(self):
        f = self.fixture
        child = f.task()
        self.ledger.await_input(child["id"], f.claim_task(child), "UI art?")
        self.ledger.await_input(f.parent, f.token, "Server decision?")
        self.ledger.push_inbox(f.parent, "Server approved", author=OWNER, resume_waiting=True)
        f.token = self.ledger.claim(f.parent, worker_id="coordinator")["token"]
        context = self.ledger.issue_context(f.parent)
        self.ledger.resolve_question(f.parent, f.token, context["human_question"]["id"],
                                     [context["session_messages"][-1]["id"]])
        self.assertEqual([q["task_key"] for q in questions(self.ledger, ISSUE)], ["ui"])
        self.ledger.flag_withdrawal(f.parent, "undelegated", 2000)
        self.assertEqual(questions(self.ledger, ISSUE), [])

    def test_controller_reconciles_without_waiting_for_parent_and_retries_label_errors(self):
        f = self.fixture
        child = f.task()
        self.ledger.await_input(child["id"], f.claim_task(child), "UI art?")
        calls = []
        def sync(iid, needed):
            calls.append((iid, needed))
            if len(calls) == 1:
                raise RuntimeError("transient label failure")
        api = SimpleNamespace(app_user_id=APP, awaiting_reply=sync,
            issue_status=lambda iid: dict(id=iid, status="Todo", status_type="unstarted", archived=False,
                delegate_id=APP, updated_at="2026-10-12T00:00:00Z"))
        controller = Lifecycle(self.ledger, api, SimpleNamespace(), clock=lambda: f.fixture.now)
        self.assertIsNotNone(controller.refresh(ISSUE))
        self.assertIsNone(self.ledger.status_check(ISSUE)["error"])
        self.assertIsNotNone(controller.refresh(ISSUE))
        self.assertEqual(calls, [(ISSUE, True), (ISSUE, True)])
        self.ledger.cancel(child["id"], "Stop")
        self.assertIsNotNone(controller.refresh(ISSUE))
        self.assertEqual(calls[-1], (ISSUE, False))

    def test_no_sweep_of_unowned_labels_on_historical_issues(self):
        calls = []
        api = SimpleNamespace(awaiting_reply=lambda *args: calls.append(args))
        reconcile_reply_label(self.ledger, api, ISSUE)
        self.assertEqual(calls, [])


class ReplyLabelTests(unittest.TestCase):
    def api(self, issue_labels=(), candidates=(), **fields):
        issue = dict(team={"id": "team"}, delegate={"id": APP}, archivedAt=None,
                     state={"type": "unstarted"}, labels={"nodes": list(issue_labels)}, **fields)
        self.http = FakeHTTP({"FarmBotReplyLabel": [{"data": {"issue": issue,
            "issueLabels": {"nodes": list(candidates), "pageInfo": {"hasNextPage": False}}}}],
            "FarmBotAddReplyLabel": [{"data": {"issueAddLabel": {"success": True}}}],
            "FarmBotRemoveReplyLabel": [{"data": {"issueRemoveLabel": {"success": True}}}],
            "FarmBotCreateReplyLabel": [{"data": {"issueLabelCreate": {"success": True, "issueLabel": {"id": "new"}}}}]})
        api = LinearAPI("client", "secret", request=self.http)
        api.app_user_id = APP
        return api

    def test_adds_red_team_label_and_preserves_unrelated_labels(self):
        self.api(issue_labels=[{"id": "bug", "name": "Bug"}]).awaiting_reply("issue")
        self.assertEqual(self.http.calls[-2][2]["variables"]["input"],
                         {"name": "待回复", "teamId": "team", "color": "#E5484D"})
        self.assertEqual(self.http.calls[-1][2]["variables"], {"id": "issue", "label": "new"})

    def test_existing_flag_is_idempotent_and_removes_only_reply_label(self):
        labels = [{"id": "bug", "name": "Bug"}, {"id": "reply", "name": "待回复"}]
        self.api(issue_labels=labels).awaiting_reply("issue")
        self.assertEqual(len(self.http.calls), 2)
        self.api(issue_labels=labels).awaiting_reply("issue", False)
        self.assertEqual(self.http.calls[-1][2]["variables"], {"id": "issue", "label": "reply"})
        self.assertIn("issueRemoveLabel", self.http.calls[-1][2]["query"])

    def test_no_question_creates_no_label_and_other_bot_is_untouched(self):
        self.api().awaiting_reply("issue", False)
        self.assertEqual(len(self.http.calls), 2)
        api = self.api()
        self.http.answers["FarmBotReplyLabel"][0]["data"]["issue"]["delegate"] = {"id": "other-bot"}
        self.assertFalse(api.awaiting_reply("issue"))
        self.assertEqual(len(self.http.calls), 2)

    def test_unconfirmed_remove_raises_for_controller_retry(self):
        api = self.api(issue_labels=[{"id": "reply", "name": "待回复"}])
        self.http.answers["FarmBotRemoveReplyLabel"][0]["data"]["issueRemoveLabel"]["success"] = False
        with self.assertRaisesRegex(RuntimeError, "removal"):
            api.awaiting_reply("issue", False)
