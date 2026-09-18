"""Behavioural tests on real SQLite files, in the style of the BugAgent prototype."""
import tempfile
import unittest
from pathlib import Path

from agent.ledger import Ledger, LedgerError

TEAM = "9676b5f9-eff3-485b-80ed-900ed137e21a"
ISSUE = "10000000-0000-4000-8000-000000000001"
OTHER = "10000000-0000-4000-8000-000000000002"
SESSION = "session-1"


def issue(id=ISSUE, **changes):
    value = {
        "id": id, "identifier": "FARM-1", "team_id": TEAM, "url": "https://linear.app/x/issue/FARM-1",
        "branch_name": "farmbot/farm-1", "title": "Harvest duplicates rewards",
        "description": "Tap harvest twice.", "status": "Todo", "status_type": "unstarted",
        "labels": ["Bug", "程序"], "priority": 2, "archived": False, "delegate_id": None,
        "attachments": [], "comments": [], "detail_complete": True, "comments_complete": True,
    }
    value.update(changes)
    return value


def comment(body="Repro on Android", kind="human", id="comment-1"):
    return {"id": id, "body": body, "author_kind": kind,
            "created_at": "2026-09-18T08:00:00Z", "updated_at": "2026-09-18T08:00:00Z"}


class LedgerBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "ledger.sqlite3"
        self.now = 1000.0
        self.ledger = self.open_ledger()

    def open_ledger(self):
        ledger = Ledger(self.path, clock=lambda: self.now, lease_seconds=60)
        self.addCleanup(ledger.close)
        return ledger

    def new_item(self, skill="fix", **issue_changes):
        self.ledger.observe_issue(issue(**issue_changes))
        self.ledger.ensure_session(SESSION, ISSUE, delegation=True)
        return self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill=skill)


class SchemaTests(LedgerBase):
    def test_opening_an_older_ledger_adds_the_columns_later_waves_introduced(self):
        self.ledger.connection.execute("ALTER TABLE sessions DROP COLUMN guidance")
        self.ledger.connection.execute("ALTER TABLE work_items DROP COLUMN lease_seconds")
        self.ledger.close()
        reopened = self.open_ledger()
        reopened.ensure_session(SESSION, None, delegation=True, guidance="先看日志")
        self.assertEqual(reopened.session(SESSION)["guidance"], "先看日志")
        columns = {row["name"] for row in reopened.connection.execute("PRAGMA table_info(work_items)")}
        self.assertIn("lease_seconds", columns)


class SnapshotTests(LedgerBase):
    def test_observe_stores_normalized_issue_and_fingerprint(self):
        view = self.ledger.observe_issue(issue())
        self.assertEqual(view["identifier"], "FARM-1")
        self.assertEqual(len(view["fingerprint"]), 64)
        self.assertEqual(self.ledger.issue(ISSUE)["labels"], ["Bug", "程序"])

    def test_incomplete_observation_is_rejected(self):
        with self.assertRaises(LedgerError):
            self.ledger.observe_issue(issue(comments_complete=False))

    def test_timestamps_and_bot_comments_are_not_material(self):
        first = self.ledger.observe_issue(issue())["fingerprint"]
        second = self.ledger.observe_issue(issue(comments=[comment(kind="bot")]))["fingerprint"]
        self.assertEqual(first, second)
        third = self.ledger.observe_issue(issue(comments=[comment(kind="human")]))["fingerprint"]
        self.assertNotEqual(first, third)


class WorkItemTests(LedgerBase):
    def test_create_work_item_is_queued_with_issue_priority(self):
        item = self.new_item()
        self.assertEqual(item["state"], "queued")
        self.assertEqual(item["priority"], 2)
        self.assertEqual(item["skill"], "fix")
        self.assertEqual(item["stage"], "intake")

    def test_one_active_item_per_issue(self):
        self.new_item()
        self.ledger.ensure_session("session-2", ISSUE, delegation=True)
        with self.assertRaises(LedgerError):
            self.ledger.create_work_item(issue_id=ISSUE, session_id="session-2", skill="fix")

    def test_unknown_issue_or_skill_is_rejected(self):
        self.ledger.ensure_session(SESSION, ISSUE, delegation=True)
        with self.assertRaises(LedgerError):
            self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill="fix")
        self.ledger.observe_issue(issue())
        with self.assertRaises(LedgerError):
            self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill="")

    def test_queue_orders_by_priority_then_creation(self):
        self.ledger.observe_issue(issue(id=OTHER, identifier="FARM-2", priority=1))
        self.ledger.ensure_session("s-low", ISSUE, delegation=True)
        self.ledger.ensure_session("s-high", OTHER, delegation=True)
        self.ledger.observe_issue(issue())
        low = self.ledger.create_work_item(issue_id=ISSUE, session_id="s-low", skill="fix")
        high = self.ledger.create_work_item(issue_id=OTHER, session_id="s-high", skill="fix")
        self.assertEqual([high["id"], low["id"]], [row["id"] for row in self.ledger.queue()])

    def test_session_records_delegation_and_active_item_lookup(self):
        item = self.new_item()
        self.assertTrue(self.ledger.session(SESSION)["delegation"])
        self.assertEqual(self.ledger.active_item_for_session(SESSION)["id"], item["id"])
        self.assertIsNone(self.ledger.active_item_for_session("nope"))

    def test_status_is_compact_and_survives_reopen(self):
        self.new_item()
        self.ledger.close()
        self.ledger = self.open_ledger()
        status = self.ledger.status()
        self.assertEqual(status["counts"], {"queued": 1, "total": 1})
        self.assertNotIn("Tap harvest twice.", str(status))

    def test_queue_excludes_items_with_a_launched_worker(self):
        item = self.new_item()
        self.ledger.set_worker(item["id"], 4242, "h")
        self.assertEqual(self.ledger.queue(), [])

    def test_launched_lists_spawned_but_unclaimed_items_with_their_launch_time(self):
        item = self.new_item()
        self.assertEqual(self.ledger.launched(), [])
        self.ledger.set_worker(item["id"], 4242, "h")
        launched = self.ledger.launched()
        self.assertEqual([row["id"] for row in launched], [item["id"]])
        self.assertEqual(launched[0]["updated_at"], self.now)


class LeaseTests(LedgerBase):
    def test_claim_requires_queued_and_issues_cli_safe_token(self):
        item = self.new_item()
        claimed = self.ledger.claim(item["id"], worker_id="pid-42")
        self.assertEqual(claimed["state"], "running")
        self.assertTrue(claimed["token"].startswith("claim_"))
        self.assertEqual(claimed["checkpoint"]["worker_id"], "pid-42")
        with self.assertRaises(LedgerError):
            self.ledger.claim(item["id"], worker_id="pid-43")

    def test_claim_token_is_stored_only_as_a_hash(self):
        item = self.new_item()
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        stored = self.ledger.connection.execute("SELECT token FROM work_items WHERE id=?", (item["id"],)).fetchone()["token"]
        self.assertNotEqual(stored, token)
        self.assertRegex(stored, r"^[0-9a-f]{64}$")
        self.assertEqual(self.ledger.renew(item["id"], token)["state"], "running")
        with self.assertRaises(LedgerError):
            self.ledger.renew(item["id"], stored)

    def test_wrong_token_and_expired_lease_are_refused(self):
        item = self.new_item()
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        with self.assertRaises(LedgerError):
            self.ledger.renew(item["id"], "claim_wrong")
        self.now += 61
        with self.assertRaises(LedgerError):
            self.ledger.renew(item["id"], token)
        self.assertEqual(self.ledger.status()["recovery_required"], [item["id"]])

    def test_claim_and_renew_use_the_lease_recorded_at_launch(self):
        item = self.new_item()
        self.ledger.set_worker(item["id"], 4242, "h", 600)
        claimed = self.ledger.claim(item["id"], worker_id="w")
        self.assertEqual(claimed["lease_expires_at"], self.now + 600)
        self.now += 100
        self.assertEqual(self.ledger.renew(item["id"], claimed["token"])["lease_expires_at"], self.now + 600)

    def test_renew_extends_and_checkpoint_keeps_handoff_and_stage(self):
        item = self.new_item()
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.now += 30
        self.assertEqual(self.ledger.renew(item["id"], token)["lease_expires_at"], self.now + 60)
        handoff = {"facts": [{"claim": "repro on main", "evidence": "run/log.txt"}], "hypotheses": [],
                   "checks": [], "repositories": [], "next_actions": ["write the test"]}
        view = self.ledger.checkpoint(item["id"], token, {"stage": "diagnose", "handoff": handoff})
        self.assertEqual(view["stage"], "diagnose")
        self.assertEqual(view["checkpoint"]["handoff_meta"]["generation"], 0)
        with self.assertRaises(LedgerError):
            self.ledger.checkpoint(item["id"], token, {"handoff": {"facts": []}})
        with self.assertRaises(LedgerError):
            self.ledger.checkpoint(item["id"], token, {"worker_id": "someone-else"})

    def test_checkpoint_registers_published_prs_once(self):
        item = self.new_item()
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        url = "https://github.com/Kuaiwa-Network/Farm-Client/pull/1"
        self.ledger.checkpoint(item["id"], token, {"published_prs": [url]})
        self.ledger.checkpoint(item["id"], token, {"published_prs": [url]})
        with self.assertRaises(LedgerError):
            self.ledger.checkpoint(item["id"], token, {"published_prs": ["http://insecure/pull/2"]})
        self.assertEqual([r["url"] for r in self.ledger.connection.execute("SELECT url FROM published_prs WHERE issue_id=?", (ISSUE,))], [url])

    def test_await_input_releases_token_and_resume_requeues(self):
        item = self.new_item()
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        view = self.ledger.await_input(item["id"], token, "需要哪个服务器环境？")
        self.assertEqual(view["state"], "awaiting_input")
        self.assertIsNone(view["lease_expires_at"])
        self.assertEqual(view["checkpoint"]["pending_question"], "需要哪个服务器环境？")
        with self.assertRaises(LedgerError):
            self.ledger.renew(item["id"], token)
        self.assertEqual(self.ledger.resume(item["id"], "human replied")["state"], "queued")

    def test_await_resource_records_the_request(self):
        item = self.new_item()
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        view = self.ledger.await_resource(item["id"], token, "unity_slot", "batch")
        self.assertEqual((view["state"], view["needs_resource"]), ("awaiting_resource", "unity_slot:batch"))

    def test_cancel_from_any_active_state_and_fail_from_running(self):
        item = self.new_item()
        self.assertEqual(self.ledger.cancel(item["id"], "stop")["state"], "cancelled")
        with self.assertRaises(LedgerError):
            self.ledger.cancel(item["id"], "again")
        self.ledger.observe_issue(issue(id=OTHER, identifier="FARM-2"))
        self.ledger.ensure_session("s2", OTHER, delegation=True)
        other = self.ledger.create_work_item(issue_id=OTHER, session_id="s2", skill="fix")
        self.ledger.claim(other["id"], worker_id="w")
        self.assertEqual(self.ledger.fail(other["id"], "budget exceeded")["state"], "failed")

    def test_recover_requires_expiry_and_authorizes_resume(self):
        item = self.new_item()
        self.ledger.claim(item["id"], worker_id="w")
        with self.assertRaises(LedgerError):
            self.ledger.recover(item["id"], "too early")
        self.now += 61
        view = self.ledger.recover(item["id"], "process exited")
        self.assertEqual((view["state"], view["resume_authorized"]), ("queued", True))
        self.assertEqual(self.ledger.claim(item["id"], worker_id="w2")["generation"], 0)

    def test_cancel_and_retry_release_the_worker_pid(self):
        item = self.new_item()
        self.ledger.set_worker(item["id"], 4242, "h")
        self.assertIsNone(self.ledger.cancel(item["id"], "stop")["worker_pid"])
        self.assertIsNone(self.ledger.retry(item["id"], "human asked 重试")["worker_pid"])

    def test_retry_requeues_terminal_items_with_new_generation(self):
        item = self.new_item()
        self.ledger.cancel(item["id"], "stop")
        view = self.ledger.retry(item["id"], "human asked 重试")
        self.assertEqual((view["state"], view["generation"]), ("queued", 1))
        with self.assertRaises(LedgerError):
            self.ledger.retry(item["id"], "already queued")


class OutboxTests(LedgerBase):
    def running(self):
        item = self.new_item()
        return item["id"], self.ledger.claim(item["id"], worker_id="w")["token"]

    def confirmed(self, item_id, token, kind="blocker", body="请补充复现步骤。"):
        action = self.ledger.prepare_comment(item_id, token, kind, body)
        self.ledger.confirm_comment(action["action_id"], "remote-comment-1")
        return action["action_id"]

    def test_prepare_comment_appends_marker_and_is_idempotent(self):
        item_id, token = self.running()
        first = self.ledger.prepare_comment(item_id, token, "started", "👀 FarmBot 已开始处理，正在复现。")
        second = self.ledger.prepare_comment(item_id, token, "started", "different wording")
        self.assertEqual(first["action_id"], second["action_id"])
        self.assertTrue(first["body"].endswith(first["marker"]))
        self.assertRegex(first["marker"], r"^\[farmbot:[0-9a-f]{64}\]$")
        with self.assertRaises(LedgerError):
            self.ledger.prepare_comment(item_id, token, "greeting", "x")

    def test_confirm_rejects_unknown_and_conflicting_remote_ids(self):
        item_id, token = self.running()
        action = self.ledger.prepare_comment(item_id, token, "blocker", "缺少环境信息。")
        with self.assertRaises(LedgerError):
            self.ledger.confirm_comment("nope", "r1")
        self.ledger.confirm_comment(action["action_id"], "r1")
        with self.assertRaises(LedgerError):
            self.ledger.confirm_comment(action["action_id"], "r2")
        self.assertEqual(self.ledger.outbox(item_id)[0]["remote_id"], "r1")

    def test_finish_blocked_requires_confirmed_blocker_comment(self):
        item_id, token = self.running()
        with self.assertRaises(LedgerError):
            self.ledger.finish(item_id, token, "blocked", {"summary": "x", "comment_action_id": "missing"})
        action = self.confirmed(item_id, token)
        view = self.ledger.finish(item_id, token, "blocked", {"summary": "需要设备信息", "comment_action_id": action})
        self.assertEqual(view["state"], "blocked")
        self.assertIsNone(view["lease_expires_at"])

    def test_finish_delivered_requires_prs_verification_and_delivery_kind(self):
        item_id, token = self.running()
        blocker = self.confirmed(item_id, token, "blocker")
        with self.assertRaises(LedgerError):
            self.ledger.finish(item_id, token, "delivered", {"summary": "done", "comment_action_id": blocker,
                                                              "verification": "tests", "prs": ["https://github.com/o/r/pull/3"]})
        delivery = self.confirmed(item_id, token, "delivery", "已修复，见 PR。")
        with self.assertRaises(LedgerError):
            self.ledger.finish(item_id, token, "delivered", {"summary": "done", "comment_action_id": delivery, "verification": "tests", "prs": []})
        view = self.ledger.finish(item_id, token, "delivered", {"summary": "done", "comment_action_id": delivery,
                                                                 "verification": "typecheck and dotnet tests", "prs": ["https://github.com/o/r/pull/3"]})
        self.assertEqual(view["state"], "delivered")

    def test_finish_refuses_a_url_that_is_not_a_pull_request(self):
        item_id, token = self.running()
        delivery = self.confirmed(item_id, token, "delivery", "已修复，见 PR。")
        with self.assertRaises(LedgerError):
            self.ledger.finish(item_id, token, "delivered", {"summary": "done", "comment_action_id": delivery,
                                                             "verification": "tests", "prs": ["https://github.com/o/r"]})

    def test_material_change_during_finish_requeues_instead_of_parking(self):
        item_id, token = self.running()
        action = self.confirmed(item_id, token)
        self.ledger.observe_issue(issue(comments=[comment("new logs attached")]))
        view = self.ledger.finish(item_id, token, "blocked", {"summary": "x", "comment_action_id": action})
        self.assertEqual(view["state"], "queued")

    def test_inbox_steering_is_consumed_once_by_the_owner(self):
        item_id, token = self.running()
        self.ledger.push_inbox(item_id, "先看服务端日志")
        self.assertEqual(self.ledger.pop_inbox(item_id, token), ["先看服务端日志"])
        self.assertEqual(self.ledger.pop_inbox(item_id, token), [])
        with self.assertRaises(LedgerError):
            self.ledger.pop_inbox(item_id, "claim_wrong")

    def test_issue_context_is_scoped_and_marks_stale_handoffs(self):
        item_id, token = self.running()
        handoff = {"facts": [], "hypotheses": ["timing"], "checks": [], "repositories": [], "next_actions": ["repro"]}
        self.ledger.checkpoint(item_id, token, {"handoff": handoff})
        context = self.ledger.issue_context(item_id)
        self.assertEqual(context["issue"]["identifier"], "FARM-1")
        self.assertFalse(context["handoff"]["stale"])
        self.assertNotIn("token", str(context))
        self.ledger.observe_issue(issue(title="Harvest duplicates rewards twice"))
        self.assertTrue(self.ledger.issue_context(item_id)["handoff"]["stale"])

    def test_chat_delivery_needs_no_comment_or_pr(self):
        item = self.new_item(skill="chat")
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        view = self.ledger.finish(item["id"], token, "delivered", {"summary": "answered", "comment_action_id": None,
                                                                    "verification": "answered in session", "prs": []})
        self.assertEqual(view["state"], "delivered")

    def test_chat_blocked_needs_no_comment(self):
        item = self.new_item(skill="chat")
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        view = self.ledger.finish(item["id"], token, "blocked", {"summary": "问题不清楚", "comment_action_id": None})
        self.assertEqual(view["state"], "blocked")

    def test_chat_delivery_rejects_comments_and_prs(self):
        item = self.new_item(skill="chat")
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        with self.assertRaises(LedgerError):
            self.ledger.finish(item["id"], token, "delivered", {"summary": "x", "comment_action_id": None,
                                                                 "verification": "answered", "prs": ["https://github.com/o/r/pull/1"]})

    def test_a_fix_may_deliver_with_no_code_change(self):
        item = self.new_item()
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        action = self.ledger.prepare_comment(item["id"], token, "delivery", "主干已修复，无需改动。")
        self.ledger.confirm_comment(action["action_id"], "remote-1")
        view = self.ledger.finish(item["id"], token, "delivered",
                                  {"summary": "已确认主干修复", "comment_action_id": action["action_id"],
                                   "verification": "对比 common 主干与客户端已提交配置",
                                   "no_change": "主干提交 6bfe03e2 已修正该文案", "prs": []})
        self.assertEqual(view["state"], "delivered")

    def test_a_no_change_delivery_may_not_also_claim_a_pr(self):
        item = self.new_item()
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        action = self.ledger.prepare_comment(item["id"], token, "delivery", "已修复。")
        self.ledger.confirm_comment(action["action_id"], "remote-2")
        with self.assertRaises(LedgerError):
            self.ledger.finish(item["id"], token, "delivered",
                               {"summary": "两者都有", "comment_action_id": action["action_id"],
                                "verification": "dotnet test", "no_change": "无需改动",
                                "prs": ["https://github.com/o/r/pull/3"]})

    def test_a_delivery_without_prs_or_a_no_change_reason_is_still_refused(self):
        item = self.new_item()
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        action = self.ledger.prepare_comment(item["id"], token, "delivery", "已修复。")
        self.ledger.confirm_comment(action["action_id"], "remote-3")
        with self.assertRaises(LedgerError):
            self.ledger.finish(item["id"], token, "delivered",
                               {"summary": "空交付", "comment_action_id": action["action_id"],
                                "verification": "dotnet test", "prs": []})
