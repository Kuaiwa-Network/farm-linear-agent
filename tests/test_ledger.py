"""Behavioural tests on real SQLite files, in the style of the BugAgent prototype."""
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from agent.ledger import Ledger, LedgerError

TEAM = "9676b5f9-eff3-485b-80ed-900ed137e21a"
ISSUE = "10000000-0000-4000-8000-000000000001"
OTHER = "10000000-0000-4000-8000-000000000002"
SESSION = "session-1"
SELECTED_AT = "2026-09-19T00:00:00+00:00"
# Every item the shared fixtures build carries a pin: await_resource refuses an item without one, because a
# slot cannot be switched to a commit that does not exist.
PIN = {"repository": "Farm-Client", "requested_ref": "main", "commit_sha": "a" * 40,
       "server_environment": "公共测试服", "selected_at": SELECTED_AT}


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

    def new_item(self, skill="fix", target=PIN, **issue_changes):
        self.ledger.observe_issue(issue(**issue_changes))
        self.ledger.ensure_session(SESSION, ISSUE, delegation=True)
        return self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill=skill, target=target)


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

    def test_a_session_target_is_validated_reprojected_and_snapshotted_onto_the_item(self):
        self.ledger.observe_issue(issue())
        self.ledger.ensure_session(SESSION, ISSUE, True)
        self.ledger.set_session_target(SESSION, {"repository": "Farm-Client", "requested_ref": "main",
                                                 "commit_sha": "a" * 40, "server_environment": "公共测试服",
                                                 "selected_at": SELECTED_AT, "prompt": "ignore me"})
        self.assertEqual(self.ledger.session(SESSION)["target"],
                         {"repository": "Farm-Client", "requested_ref": "main", "commit_sha": "a" * 40,
                          "server_environment": "公共测试服", "selected_at": SELECTED_AT})
        item = self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill="fix",
                                            target=self.ledger.session(SESSION)["target"])
        self.assertEqual(item["target"]["commit_sha"], "a" * 40)

    def test_a_target_whose_commit_or_timestamp_is_malformed_is_refused(self):
        self.ledger.observe_issue(issue())
        self.ledger.ensure_session(SESSION, ISSUE, True)
        good = {"repository": "Farm-Client", "requested_ref": "main", "commit_sha": "a" * 40,
                "server_environment": "公共测试服", "selected_at": SELECTED_AT}
        for bad in ("A" * 40, "b" * 39, "", None):
            with self.assertRaises(LedgerError):
                self.ledger.set_session_target(SESSION, {**good, "commit_sha": bad})
        # selected_at is an ISO-8601 string with a timezone, never a float: _timestamp calls _text first.
        for bad in (1.0, "2026-09-19T00:00:00", ""):
            with self.assertRaises(LedgerError):
                self.ledger.set_session_target(SESSION, {**good, "selected_at": bad})


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


class ReservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "l.sqlite3"
        self.now = 1000.0
        self.ledger = Ledger(self.path, clock=lambda: self.now, lease_seconds=60)
        self.addCleanup(self.ledger.close)
        self.ledger.ensure_slot("unity_slot:1", kind="unity_slot", host="mac", folder="/e/slot-1")

    def item(self, issue_id, commit, priority=2):
        # The helper's keyword is `id`, not `issue_id`: issue(issue_id=OTHER) would leave the row on ISSUE and
        # the create would then fail with "unknown issue". The two items must be on two issue rows because
        # create_work_item refuses a second active item on one issue.
        self.ledger.observe_issue(issue(id=issue_id, priority=priority))
        session = f"session-{issue_id}"
        self.ledger.ensure_session(session, issue_id, True)
        target = {**PIN, "commit_sha": commit}
        return self.ledger.create_work_item(issue_id=issue_id, session_id=session, skill="fix", target=target)

    def waiting(self, issue_id, commit, mode, priority=2):
        item = self.item(issue_id, commit, priority=priority)
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.ledger.await_resource(item["id"], token, "unity_slot", mode)
        return item["id"]

    def park(self, slot_id="unity_slot:1"):
        """Stand in for Task 3's park_idle. acquire and release both leave the slot 'switching' on purpose:
        the folder is then the pool's to switch, park or close, and only the pool may call it free again."""
        self.ledger.set_slot_state(slot_id, "idle_closed")

    def test_two_requests_are_granted_in_arrival_order_not_priority_order(self):
        # The second issue is *more* urgent, so an implementation that ordered by priority would grant it
        # first and this test would fail. With both at the same priority the assertion proves nothing.
        first = self.waiting(ISSUE, "a" * 40, "batch")
        second = self.waiting(OTHER, "b" * 40, "interactive", priority=1)
        granted = self.ledger.acquire("unity_slot", owner="pool", host="mac")
        self.assertEqual((granted["item_id"], granted["mode"], granted["commit_sha"]), (first, "batch", "a" * 40))
        self.assertIsNone(self.ledger.acquire("unity_slot", owner="pool", host="mac"))
        self.assertEqual(self.ledger.release(granted["reservation_id"], granted["token"], "batch finished"), "released")
        self.park()
        self.assertEqual(self.ledger.acquire("unity_slot", owner="pool", host="mac")["item_id"], second)

    def test_the_unique_index_refuses_a_second_active_owner_of_one_slot(self):
        self.waiting(ISSUE, "a" * 40, "batch")
        granted = self.ledger.acquire("unity_slot", owner="pool", host="mac")
        # The rival row belongs to an item with no reservation of its own. Reusing granted["item_id"] would
        # also violate one_open_reservation_per_item, so the IntegrityError would not prove which index fired.
        rival = self.item(OTHER, "b" * 40)["id"]
        with self.assertRaises(sqlite3.IntegrityError) as caught:
            self.ledger.connection.execute(
                """INSERT INTO reservations(reservation_id,item_id,generation,kind,mode,resource,host,commit_sha,
                   state,owner,token_hash,created_at,acquired_at)
                   VALUES('r2',?,0,'unity_slot','batch','unity_slot:1','mac',?,'active','x','y',?,?)""",
                (rival, "b" * 40, self.now, self.now))
        self.assertIn("reservations.resource", str(caught.exception))
        self.assertEqual(granted["resource"], "unity_slot:1")

    def test_a_worker_may_not_stack_a_second_request_while_it_holds_one(self):
        item = self.item(ISSUE, "a" * 40)
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.ledger.await_resource(item["id"], token, "unity_slot", "batch")
        self.ledger.resume(item["id"], "granted")
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        with self.assertRaises(LedgerError):
            self.ledger.await_resource(item["id"], token, "unity_slot", "interactive")

    def test_an_item_without_a_pinned_commit_cannot_request_a_slot(self):
        self.ledger.observe_issue(issue())
        self.ledger.ensure_session(SESSION, ISSUE, True)
        item = self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill="fix")
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        with self.assertRaises(LedgerError):
            self.ledger.await_resource(item["id"], token, "unity_slot", "batch")

    def test_a_wrong_token_can_neither_read_nor_release_a_reservation(self):
        self.waiting(ISSUE, "a" * 40, "batch")
        granted = self.ledger.acquire("unity_slot", owner="pool", host="mac")
        with self.assertRaises(LedgerError):
            self.ledger.release(granted["reservation_id"], "not-the-token", "nope")
        self.assertEqual(self.ledger.reservation(granted["reservation_id"])["state"], "active")
        self.assertNotIn("token", self.ledger.reservation(granted["reservation_id"]))

    def test_stop_cancels_a_queued_reservation_and_only_marks_an_active_one(self):
        first = self.waiting(ISSUE, "a" * 40, "batch")
        second = self.waiting(OTHER, "b" * 40, "interactive")
        granted = self.ledger.acquire("unity_slot", owner="pool", host="mac")
        self.ledger.cancel_reservations(first, "Linear stop")
        self.ledger.cancel_reservations(second, "Linear stop")
        self.assertEqual(self.ledger.reservation(granted["reservation_id"])["state"], "cancel_requested")
        self.assertEqual([r["state"] for r in self.ledger.reservations() if r["item_id"] == second], ["cancelled"])
        self.assertEqual(self.ledger.release(granted["reservation_id"], granted["token"], "stopped"), "cancelled")

    def test_cancelling_a_work_item_cancels_its_reservations_in_the_same_transaction(self):
        item = self.waiting(ISSUE, "a" * 40, "batch")
        self.ledger.cancel(item, "operator")
        self.assertEqual([r["state"] for r in self.ledger.reservations() if r["item_id"] == item], ["cancelled"])

    def test_a_failing_probe_holds_the_slot_until_the_operator_recovers_it(self):
        self.waiting(ISSUE, "a" * 40, "batch")
        granted = self.ledger.acquire("unity_slot", owner="pool", host="mac")
        self.ledger.hold(granted["reservation_id"], "quiescence probe failed")
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "held")
        self.waiting(OTHER, "b" * 40, "batch")
        self.assertIsNone(self.ledger.acquire("unity_slot", owner="pool", host="mac"))
        self.ledger.recover_slot("unity_slot:1", "operator closed Unity by hand")
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "idle_closed")
        self.assertIsNone(self.ledger.slot("unity_slot:1")["parked_commit"])
        self.assertIsNotNone(self.ledger.acquire("unity_slot", owner="pool", host="mac"))

    def test_a_reservation_is_listed_for_settlement_once_its_item_is_no_longer_running(self):
        item = self.waiting(ISSUE, "a" * 40, "batch")
        granted = self.ledger.acquire("unity_slot", owner="pool", host="mac")
        self.ledger.resume(item, "granted")
        self.assertEqual(self.ledger.reservations_to_settle(), [])
        token = self.ledger.claim(item, worker_id="w")["token"]
        self.ledger.fail(item, "worker died")
        self.assertEqual([r["reservation_id"] for r in self.ledger.reservations_to_settle()],
                         [granted["reservation_id"]])
        self.assertTrue(token)

    def test_a_held_slot_is_not_offered_for_settlement_on_every_tick(self):
        item = self.waiting(ISSUE, "a" * 40, "batch")
        granted = self.ledger.acquire("unity_slot", owner="pool", host="mac")
        self.ledger.hold(granted["reservation_id"], "quiescence probe failed")
        self.ledger.resume(item, "granted")
        self.ledger.claim(item, worker_id="w")
        self.ledger.fail(item, "worker died")
        self.assertEqual(self.ledger.reservations_to_settle(), [])

    def test_an_identity_observation_is_appended_per_item(self):
        item = self.waiting(ISSUE, "a" * 40, "batch")
        granted = self.ledger.acquire("unity_slot", owner="pool", host="mac")
        self.ledger.record_identity(item, granted["reservation_id"], "unity_slot:1",
                                    {"aggregate": "match", "checks": {"source_commit": "match"}})
        rows = self.ledger.identity_observations(item)
        self.assertEqual(rows[0]["result"]["aggregate"], "match")

    def test_one_hosts_active_reservation_does_not_starve_another_host(self):
        """The Windows plan inherits this file. A pre-check without a host predicate would make one host's
        busy slot block the other host's pool forever."""
        self.ledger.ensure_slot("unity_slot:2", kind="unity_slot", host="win", folder="/e/slot-2")
        self.waiting(ISSUE, "a" * 40, "batch")
        self.waiting(OTHER, "b" * 40, "batch")
        self.assertIsNotNone(self.ledger.acquire("unity_slot", owner="pool", host="mac"))
        second = self.ledger.acquire("unity_slot", owner="pool", host="win")
        self.assertEqual(second["resource"], "unity_slot:2")

    def test_interactive_prefers_an_open_editor_and_batch_prefers_a_closed_one(self):
        """Spec §7: an interactive request wants an Editor already open, a batch request wants none.

        Both halves expect unity_slot:2, the alphabetically *later* slot, so neither can pass off the
        slot_id tiebreak — only off the state preference. Reversing `order` in acquire fails both.
        """
        self.ledger.ensure_slot("unity_slot:2", kind="unity_slot", host="mac", folder="/e/slot-2")
        self.ledger.set_slot_state("unity_slot:1", "idle_closed")
        self.ledger.set_slot_state("unity_slot:2", "idle_open")
        self.waiting(ISSUE, "a" * 40, "interactive")
        granted = self.ledger.acquire("unity_slot", owner="pool", host="mac")
        self.assertEqual(granted["resource"], "unity_slot:2")
        self.ledger.release(granted["reservation_id"], granted["token"], "interactive finished")
        self.ledger.set_slot_state("unity_slot:1", "idle_open")
        self.ledger.set_slot_state("unity_slot:2", "idle_closed")
        self.waiting(OTHER, "b" * 40, "batch")
        self.assertEqual(self.ledger.acquire("unity_slot", owner="pool", host="mac")["resource"], "unity_slot:2")

    def test_ensure_slot_never_clears_a_discovered_instance(self):
        self.ledger.ensure_slot("unity_slot:1", kind="unity_slot", host="mac", folder="/e/slot-1",
                                instance="pid-9", mcp_address="127.0.0.1:7777")
        again = self.ledger.ensure_slot("unity_slot:1", kind="unity_slot", host="mac", folder="/e/slot-1")
        self.assertEqual((again["instance"], again["mcp_address"]), ("pid-9", "127.0.0.1:7777"))
        self.assertEqual([s["slot_id"] for s in self.ledger.slots(kind="unity_slot", host="mac")], ["unity_slot:1"])
        self.assertIsNone(self.ledger.slot("unity_slot:9"))
        with self.assertRaises(LedgerError):
            self.ledger.set_slot_state("unity_slot:1", "running")

    def test_a_mode_preference_is_a_sort_and_never_a_filter(self):
        """Spec §7 states a preference, not a requirement, and with one slot in the pool the difference is
        the whole system: an interactive request that refused a closed slot — or a batch request that refused
        an open one — would never run at all on this Mac. The sibling test above pins the preference when
        both states are available; this one pins what happens when only the unwanted one is."""
        for state, mode in (("idle_closed", "interactive"), ("idle_open", "batch")):
            with self.subTest(state=state, mode=mode):
                self.ledger.set_slot_state("unity_slot:1", state)
                item = self.waiting(ISSUE if mode == "interactive" else OTHER, "a" * 40, mode)
                granted = self.ledger.acquire("unity_slot", owner="pool", host="mac")
                self.assertEqual(granted["resource"], "unity_slot:1")
                self.ledger.release(granted["reservation_id"], granted["token"], "done")
                self.ledger.fail_queued(item, "finished with the fixture")

    def test_acquiring_a_slot_a_reservation_still_holds_refuses_as_a_ledger_error(self):
        """The partial unique index is the fence and it fires correctly; what was unclean was the surfacing.
        A slot returned to the pool while a reservation still held it made the next acquire raise a raw
        sqlite3.IntegrityError out of the index rather than the ledger's own error, which no caller can tell
        apart from a corrupt database. SlotPool.park_idle refuses to create this state; the ledger refuses
        to act on it."""
        self.waiting(ISSUE, "a" * 40, "batch")
        self.waiting(OTHER, "b" * 40, "batch")
        first = self.ledger.acquire("unity_slot", owner="pool", host="mac")
        self.park()   # the bug this guards: a slot called free while a reservation still holds it
        with self.assertRaises(LedgerError):
            self.ledger.acquire("unity_slot", owner="pool", host="mac")
        # And the rollback left the first reservation alone: it still owns the slot it never gave up.
        self.assertEqual(self.ledger.active_reservation_on("unity_slot:1")["reservation_id"],
                         first["reservation_id"])
        self.assertEqual([r["state"] for r in self.ledger.reservations()], ["active", "queued"])

    def test_active_reservation_on_reads_the_holder_of_a_slot_rather_than_of_an_item(self):
        """park_idle asks the question the other way round from active_reservation: it has a slot in hand and
        needs to know whether anyone still holds it, because Ledger.release leaves the slot 'switching' and
        so does acquire."""
        self.assertIsNone(self.ledger.active_reservation_on("unity_slot:1"))
        self.waiting(ISSUE, "a" * 40, "batch")
        granted = self.ledger.acquire("unity_slot", owner="pool", host="mac")
        self.assertEqual(self.ledger.active_reservation_on("unity_slot:1")["reservation_id"],
                         granted["reservation_id"])
        self.ledger.cancel_reservations(granted["item_id"], "Linear stop")
        self.assertIsNotNone(self.ledger.active_reservation_on("unity_slot:1"))   # cancel_requested still holds
        self.ledger.release(granted["reservation_id"], granted["token"], "settled")
        self.assertIsNone(self.ledger.active_reservation_on("unity_slot:1"))

    def test_requeue_reservation_puts_one_more_attempt_at_the_tail_and_refuses_an_open_one(self):
        """A retryable switch failure gets one more chance behind everything already waiting, never a third
        and never a queue-jump. The new row carries the same item, generation, kind, mode and commit."""
        first = self.waiting(ISSUE, "a" * 40, "batch")
        granted = self.ledger.acquire("unity_slot", owner="pool", host="mac")
        with self.assertRaises(LedgerError):
            self.ledger.requeue_reservation(granted["reservation_id"], "still active")
        with self.assertRaises(LedgerError):
            self.ledger.requeue_reservation("no-such-reservation", "unknown")
        self.ledger.release(granted["reservation_id"], granted["token"], "git stage failed")
        self.park()
        second = self.waiting(OTHER, "b" * 40, "batch")   # arrived while the first was failing
        fresh = self.ledger.requeue_reservation(granted["reservation_id"], "git stage failed")
        self.assertEqual([(r["item_id"], r["attempts"], r["state"]) for r in self.ledger.reservations()],
                         [(first, 0, "released"), (second, 0, "queued"), (first, 1, "queued")])
        original, again = self.ledger.reservation(granted["reservation_id"]), self.ledger.reservation(fresh)
        self.assertEqual([again[key] for key in ("item_id", "generation", "kind", "mode", "commit_sha")],
                         [original[key] for key in ("item_id", "generation", "kind", "mode", "commit_sha")])
        # The queue is FIFO by sequence, so the later arrival is served before the second attempt.
        self.assertEqual(self.ledger.acquire("unity_slot", owner="pool", host="mac")["item_id"], second)

    def test_fail_queued_accepts_a_waiting_item_and_still_refuses_a_claimed_one(self):
        """A failed switch has to fail an item that is 'awaiting_resource', not 'queued' — it never reached
        resume(). The guard's two existing callers in agent/scheduler.py pass queued items and are
        unaffected, and a running item still belongs to fail()."""
        item = self.waiting(ISSUE, "a" * 40, "batch")
        self.assertEqual(self.ledger.item(item)["state"], "awaiting_resource")
        self.assertEqual(self.ledger.fail_queued(item, "slot held after a failed probe")["state"], "failed")
        running = self.item(OTHER, "b" * 40)["id"]
        self.ledger.claim(running, worker_id="w")
        with self.assertRaises(LedgerError):
            self.ledger.fail_queued(running, "a running item is fail()'s, not fail_queued()'s")

    def test_two_connections_acquiring_at_once_produce_exactly_one_grant(self):
        """The 'across processes rather than by convention' claim, tested across two connections rather than
        two calls on one. BEGIN IMMEDIATE takes the write lock for the whole transaction, so the loser reads a
        slot that is already 'switching' and returns None — the unique index never has to fire.

        Two queued requests, not one: with a single request the loser would find no queued row and return None
        without ever reaching the slot check the docstring above claims to exercise. Each thread gets its own
        connection, so check_same_thread stays honest — one sqlite3.Connection per thread.
        """
        self.waiting(ISSUE, "a" * 40, "batch")
        self.waiting(OTHER, "b" * 40, "batch")
        barrier, results = threading.Barrier(2), []
        lock = threading.Lock()

        def run():
            ledger = Ledger(self.path, clock=lambda: self.now, lease_seconds=60, check_same_thread=False)
            try:
                barrier.wait()
                granted = ledger.acquire("unity_slot", owner="pool", host="mac")
            finally:
                ledger.close()
            with lock:
                results.append(granted)

        threads = [threading.Thread(target=run) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
        self.assertEqual(len([r for r in results if r is not None]), 1, results)
        self.assertEqual(len(self.ledger.reservations(("active",))), 1)
