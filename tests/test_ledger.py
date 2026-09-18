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
