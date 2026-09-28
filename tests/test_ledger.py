"""Behavioural tests on real SQLite files, in the style of the BugAgent prototype."""
import dataclasses
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from agent.ledger import MARKER, Ledger, LedgerError
from agent.skills import load_skills
from agent.stages import write_repositories
from test_skills import opt_in_skill, staged_skill, write_skill

ROOT = Path(__file__).resolve().parents[1]
SKILLS = load_skills(ROOT / "skills")

TEAM = "9676b5f9-eff3-485b-80ed-900ed137e21a"
ISSUE = "10000000-0000-4000-8000-000000000001"
OTHER = "10000000-0000-4000-8000-000000000002"
SESSION = "session-1"
SELECTED_AT = "2026-09-19T00:00:00+00:00"
# Every item the shared fixtures build carries a pin: await_resource refuses an item without one, because a
# slot cannot be switched to a commit that does not exist.
PIN = {"repository": "Farm-Client", "requested_ref": "main", "commit_sha": "a" * 40,
       "server_environment": "公共测试服", "selected_at": SELECTED_AT}
# Fictional people, as the ledger stores them: id, name and profile URL only.
DESIGNER = {"id": "20000000-0000-4000-8000-000000000001", "name": "Designer One",
            "url": "https://linear.app/example/profiles/designer-one"}
OWNER = {"id": "20000000-0000-4000-8000-000000000002", "name": "Owner Two",
         "url": "https://linear.app/example/profiles/owner-two"}
LEAD = {"id": "20000000-0000-4000-8000-000000000003", "name": "主策三号",
        "url": "https://linear.app/example/profiles/lead-three"}
THREAD = "https://linear.app/example/issue/FARM-1/harvest-duplicates-rewards"


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


def comment(body="Repro on Android", kind="human", id="comment-1", **extra):
    """`extra` adds optional keys such as author, parent_id and url."""
    return {"id": id, "body": body, "author_kind": kind,
            "created_at": "2026-09-18T08:00:00Z", "updated_at": "2026-09-18T08:00:00Z", **extra}


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
        self.ledger.connection.execute("ALTER TABLE work_items DROP COLUMN root_repo")
        self.ledger.connection.execute("ALTER TABLE work_items DROP COLUMN next_root_repo")
        self.ledger.close()
        reopened = self.open_ledger()
        reopened.ensure_session(SESSION, None, delegation=True, guidance="先看日志")
        self.assertEqual(reopened.session(SESSION)["guidance"], "先看日志")
        columns = {row["name"] for row in reopened.connection.execute("PRAGMA table_info(work_items)")}
        self.assertIn("lease_seconds", columns)
        self.assertIn("root_repo", columns)
        self.assertIn("next_root_repo", columns)

    def test_an_older_ledger_opens_and_its_sessions_and_messages_name_nobody(self):
        """A file written before sessions and inbox entries recorded people: its rows read as unattributed, and its
        messages keep the time they were received."""
        item = self.new_item()
        self.ledger.push_inbox(item["id"], "先看服务端日志")
        self.ledger.connection.execute("ALTER TABLE sessions DROP COLUMN creator_json")
        self.ledger.connection.execute("ALTER TABLE inbox DROP COLUMN author_json")
        self.ledger.close()
        reopened = self.open_ledger()
        for table, column in (("sessions", "creator_json"), ("inbox", "author_json")):
            self.assertIn(column, {row["name"] for row in reopened.connection.execute(f"PRAGMA table_info({table})")})
        self.assertIsNone(reopened.session(SESSION)["creator"])
        # LedgerBase's clock reads 1000.0 seconds after the epoch.
        self.assertEqual(reopened.issue_context(item["id"])["session_messages"],
                         [{"id": 1, "body": "先看服务端日志", "author": None, "created_at": "1970-01-01T00:16:40+00:00"}])
        reopened.ensure_session(SESSION, ISSUE, delegation=True, creator=OWNER)
        self.assertEqual(reopened.session(SESSION)["creator"], OWNER)

    def test_opening_an_older_ledger_adds_the_notices_table(self):
        self.ledger.connection.execute("DROP TABLE notices")
        self.ledger.close()
        reopened = self.open_ledger()
        tables = {row["name"] for row in reopened.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("notices", tables)


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

    def test_people_label_groups_and_replies_are_stored_and_reach_issue_context(self):
        item = self.new_item(labels=["Android", "Bug", "Code"], assignee=OWNER, creator=DESIGNER,
                             label_groups=[{"group": "平台", "label": "Android"}, {"group": "功能", "label": "Code"},
                                           {"group": "功能", "label": "Code"}],
                             comments=[comment(author=DESIGNER, parent_id=None, url=f"{THREAD}#comment-1"),
                                       comment("收到", id="comment-2", author=OWNER, parent_id="comment-1", url=None),
                                       comment("已开始处理", kind="bot", id="comment-3", author=None,
                                               parent_id="comment-1", url=f"{THREAD}#comment-3")])
        stored = self.ledger.issue(ISSUE)
        self.assertEqual((stored["assignee"], stored["creator"]), (OWNER, DESIGNER))
        self.assertEqual(stored["label_groups"], [{"group": "功能", "label": "Code"}, {"group": "平台", "label": "Android"}])
        self.assertEqual([(c["author"], c["parent_id"], c["url"]) for c in stored["comments"]],
                         [(DESIGNER, None, f"{THREAD}#comment-1"), (OWNER, "comment-1", None),
                          (None, "comment-1", f"{THREAD}#comment-3")])
        self.assertEqual(self.ledger.issue_context(item["id"])["issue"]["assignee"], OWNER)

    def test_a_snapshot_without_the_new_keys_reads_as_unknown(self):
        # The shape a29d078 stores and older fetchers still send: a missing key is unknown, never an error.
        self.ledger.observe_issue(issue(comments=[comment()]))
        stored = self.ledger.issue(ISSUE)
        self.assertFalse({"label_groups", "assignee", "creator"} & stored.keys())
        self.assertFalse({"author", "parent_id", "url"} & stored["comments"][0].keys())

    def test_a_known_nobody_is_stored_as_null_not_dropped(self):
        # null means "none", a missing key means "not read yet" (references/worker-cli.md): keep them apart.
        self.ledger.observe_issue(issue(assignee=None, creator=None, label_groups=[],
                                        comments=[comment(author=None, parent_id=None, url=None),
                                                  comment(kind="unknown", id="comment-2", author=None)]))
        stored = self.ledger.issue(ISSUE)
        self.assertEqual((stored["assignee"], stored["creator"], stored["label_groups"]), (None, None, []))
        self.assertEqual([{key: c[key] for key in ("author", "parent_id", "url") if key in c} for c in stored["comments"]],
                         [{"author": None, "parent_id": None, "url": None}, {"author": None}])

    def test_malformed_people_label_groups_and_replies_are_refused(self):
        bad = {"a person with an email": {"assignee": {**OWNER, "email": "owner.two@example.com"}},
               "a person without a url": {"creator": {"id": OWNER["id"], "name": "Owner Two"}},
               "a url outside Linear": {"assignee": {**OWNER, "url": "https://example.com/profiles/owner-two"}},
               "a url without https": {"assignee": {**OWNER, "url": "http://linear.app/example/profiles/owner-two"}},
               "an id that is not a UUID": {"creator": {**OWNER, "id": "owner-two"}},
               "a blank name": {"creator": {**OWNER, "name": " "}},
               "a person as text": {"assignee": "Owner Two"},
               "label groups that are not an array": {"label_groups": {"group": "功能", "label": "Bug"}},
               "a label group with another key": {"label_groups": [{"group": "功能", "label": "Bug", "id": "x"}]},
               "a blank group": {"label_groups": [{"group": "", "label": "Bug"}]},
               "a grouped label the issue lacks": {"label_groups": [{"group": "功能", "label": "Code"}]},
               "a comment author with an email": {"comments": [comment(author={**DESIGNER, "email": "d@example.com"})]},
               "an author on a bot comment": {"comments": [comment(kind="bot", author=DESIGNER)]},
               "an author on an unknown comment": {"comments": [comment(kind="unknown", author=DESIGNER)]},
               "a blank parent id": {"comments": [comment(parent_id="")]},
               "a parent id that is not text": {"comments": [comment(parent_id=7)]},
               "a comment url outside Linear": {"comments": [comment(url="https://example.com/FARM-1#comment-1")]},
               "a comment url that is not text": {"comments": [comment(url=7)]}}
        for label, changes in bad.items():
            with self.subTest(label), self.assertRaises(LedgerError):
                self.ledger.observe_issue(issue(**changes))

    def test_people_label_groups_replies_and_comment_urls_are_not_material(self):
        plain = issue(labels=["Bug", "Code"], comments=[comment(), comment("收到", id="comment-2")])
        first = self.ledger.observe_issue(plain)["fingerprint"]
        rich = issue(labels=["Bug", "Code"], assignee=OWNER, creator=DESIGNER,
                     label_groups=[{"group": "功能", "label": "Code"}],
                     comments=[comment(author=DESIGNER, parent_id=None, url=f"{THREAD}#comment-1"),
                               comment("收到", id="comment-2", author=OWNER, parent_id="comment-1", url=None)])
        self.assertEqual(self.ledger.observe_issue(rich)["fingerprint"], first)
        reassigned = {**rich, "assignee": DESIGNER, "creator": None, "label_groups": []}
        self.assertEqual(self.ledger.observe_issue(reassigned)["fingerprint"], first)


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


class RepositoryStageTests(LedgerBase):
    def test_handoff_requires_current_checkpoint_and_controller_teardown(self):
        item = self.new_item()
        self.ledger.set_worker(item["id"], 4321, "test")
        token = self.ledger.claim(item["id"], worker_id="worker-one")["token"]
        with self.assertRaises(LedgerError):
            self.ledger.handoff_repository(item["id"], token, "Farm-Contract", skill=SKILLS["fix"])
        self.ledger.checkpoint(item["id"], token, {"handoff": {
            "facts": [], "hypotheses": [], "checks": [], "repositories": [], "next_actions": ["Check contract"]}})
        pending = self.ledger.handoff_repository(item["id"], token, "Farm-Contract", skill=SKILLS["fix"])
        self.assertEqual(pending["worker_pid"], 4321)
        self.assertEqual(self.ledger.queue(), [])
        with self.assertRaises(LedgerError):
            self.ledger.claim(item["id"], worker_id="worker-two")
        with self.assertRaises(LedgerError):
            self.ledger.complete_repository_handoff(item["id"], 9999, skill=SKILLS["fix"])
        ready = self.ledger.complete_repository_handoff(item["id"], 4321, skill=SKILLS["fix"])
        self.assertEqual((ready["root_repo"], ready["worker_pid"]), ("Farm-Contract", None))
        self.assertEqual(self.ledger.claim(item["id"], worker_id="worker-two")["state"], "running")

    def test_contract_stage_cannot_request_unity(self):
        item = self.new_item()
        self.ledger.connection.execute("UPDATE work_items SET root_repo='Farm-Contract' WHERE id=?", (item["id"],))
        token = self.ledger.claim(item["id"], worker_id="worker")['token']
        with self.assertRaisesRegex(LedgerError, "neutral or Farm-Client"):
            self.ledger.await_resource(item["id"], token, "unity_slot", "batch")

    def test_neutral_stage_requests_only_the_baseline_for_unity(self):
        item = self.new_item()
        token = self.ledger.claim(item["id"], worker_id="worker")["token"]
        with self.assertRaisesRegex(LedgerError, "only a write worker"):
            self.ledger.await_resource(item["id"], token, "unity_slot", "batch", commit_sha="b" * 40)
        waiting = self.ledger.await_resource(item["id"], token, "unity_slot", "batch")
        self.assertEqual(waiting["state"], "awaiting_resource")
        self.assertEqual([r["commit_sha"] for r in self.ledger.reservations()], [PIN["commit_sha"]])

    def test_explicit_retry_restarts_from_neutral_investigation(self):
        item = self.new_item()
        self.ledger.connection.execute("UPDATE work_items SET root_repo='Farm-Contract' WHERE id=?", (item["id"],))
        self.ledger.fail_queued(item["id"], "transient")
        retried = self.ledger.retry(item["id"], "try again")
        self.assertIsNone(retried["root_repo"])

    def test_chat_repair_keeps_findings_but_restarts_at_neutral_root(self):
        app_user = "e5a8c16d-9f85-4123-acf5-94e41c3304d5"
        fix = self.new_item(delegate_id=app_user)
        self.ledger.connection.execute("UPDATE work_items SET root_repo='Farm-Client' WHERE id=?", (fix["id"],))
        self.ledger.fail_queued(fix["id"], "earlier attempt ended")
        self.ledger.ensure_session("chat-session", ISSUE, delegation=False)
        chat = self.ledger.create_work_item(issue_id=ISSUE, session_id="chat-session", skill="chat")
        self.ledger.push_inbox(chat["id"], "请重新调查并修复")
        token = self.ledger.claim(chat["id"], worker_id="investigator")["token"]
        message_id = self.ledger.issue_context(chat["id"])["session_messages"][-1]["id"]
        resumed = self.ledger.request_repair(chat["id"], token, message_id, app_user,
                                             "Confirmed symptom; source repository still unknown.")
        self.assertEqual(resumed["id"], fix["id"])
        self.assertIsNone(resumed["root_repo"])
        context = self.ledger.issue_context(fix["id"])
        self.assertIn("请重新调查并修复", [m["body"] for m in context["session_messages"]])
        self.assertIn("Confirmed symptom; source repository still unknown.",
                      [entry["summary"] for entry in context["conversation_history"]])


class StagedHandoffTests(LedgerBase):
    """Stages come from the item's skill manifest, which the caller hands the ledger (spec §9.4, §9.5)."""

    def setUp(self):
        super().setUp()
        self.skills = Path(self.tmp.name) / "skills"
        self.feature = staged_skill(self.skills)
        self.fix = SKILLS["fix"]

    def claimed(self, skill="feature"):
        item = self.new_item(skill=skill)
        self.ledger.set_worker(item["id"], 4321, "test")
        token = self.ledger.claim(item["id"], worker_id="worker-one")["token"]
        self.ledger.checkpoint(item["id"], token, {"handoff": {
            "facts": [], "hypotheses": [], "checks": [], "repositories": [], "next_actions": ["Build the server"]}})
        return item, token

    def requested(self, item_id):
        row = self.ledger.connection.execute("SELECT details FROM audit WHERE item_id=? "
                                             "AND kind='repository_handoff_requested'", (item_id,)).fetchone()
        return json.loads(row["details"])

    def test_fix_hands_off_under_its_own_manifest_as_before(self):
        item, token = self.claimed(skill="fix")
        with self.assertRaisesRegex(LedgerError, "repository is not a fix target"):
            self.ledger.handoff_repository(item["id"], token, "farm-server", skill=self.fix)
        self.ledger.handoff_repository(item["id"], token, "Farm-Contract", skill=self.fix)
        self.assertEqual((self.requested(item["id"])["from"], self.requested(item["id"])["to"]), (None, "Farm-Contract"))
        ready = self.ledger.complete_repository_handoff(item["id"], 4321, skill=self.fix)
        self.assertEqual((ready["root_repo"], ready["next_root_repo"], ready["worker_pid"]), ("Farm-Contract", None, None))

    def test_a_staged_skill_starts_at_its_initial_root_and_moves_only_within_its_writes(self):
        item, token = self.claimed()
        with self.assertRaisesRegex(LedgerError, "already rooted"):
            self.ledger.handoff_repository(item["id"], token, "Farm-Contract", skill=self.feature)
        with self.assertRaisesRegex(LedgerError, "repository is not a feature target"):
            self.ledger.handoff_repository(item["id"], token, "farmgui", skill=self.feature)
        self.assertEqual(self.ledger.item(item["id"])["state"], "running")
        pending = self.ledger.handoff_repository(item["id"], token, "farm-hive", skill=self.feature)
        self.assertEqual((pending["root_repo"], pending["next_root_repo"]), (None, "farm-hive"))
        self.assertEqual((self.requested(item["id"])["from"], self.requested(item["id"])["to"]),
                         ("Farm-Contract", "farm-hive"))
        ready = self.ledger.complete_repository_handoff(item["id"], 4321, skill=self.feature)
        self.assertEqual(ready["root_repo"], "farm-hive")
        token = self.ledger.claim(item["id"], worker_id="worker-two")["token"]
        with self.assertRaisesRegex(LedgerError, "already rooted"):
            self.ledger.handoff_repository(item["id"], token, "farm-hive", skill=self.feature)

    def test_only_the_items_own_manifest_can_move_it(self):
        item, token = self.claimed()
        with self.assertRaisesRegex(LedgerError, "own skill manifest"):
            self.ledger.handoff_repository(item["id"], token, "farm-hive", skill=self.fix)
        self.assertEqual((self.ledger.item(item["id"])["state"], self.ledger.item(item["id"])["next_root_repo"]),
                         ("running", None))

    def test_an_unstaged_skill_cannot_hand_off(self):
        write_skill(self.skills, "wide", writes=["farm-hive", "Farm-Client"])
        item, token = self.claimed(skill="wide")
        with self.assertRaisesRegex(LedgerError, "wide is not staged"):
            self.ledger.handoff_repository(item["id"], token, "farm-hive", skill=load_skills(self.skills)["wide"])

    def test_a_target_the_manifest_no_longer_allows_stays_pending(self):
        item, token = self.claimed()
        self.ledger.handoff_repository(item["id"], token, "farm-hive", skill=self.feature)
        narrowed = dataclasses.replace(self.feature, writes=("Farm-Contract", "Farm-Client"))
        with self.assertRaisesRegex(LedgerError, "no longer matches"):
            self.ledger.complete_repository_handoff(item["id"], 4321, skill=narrowed)
        self.assertEqual(self.ledger.item(item["id"])["next_root_repo"], "farm-hive")

    def test_completion_needs_the_items_own_staged_manifest(self):
        item, token = self.claimed()
        self.ledger.handoff_repository(item["id"], token, "farm-hive", skill=self.feature)
        for other in (self.fix, dataclasses.replace(self.feature, staged=False)):  # fix also writes farm-hive
            with self.subTest(skill=other.name, staged=other.staged):
                with self.assertRaisesRegex(LedgerError, "no longer matches"):
                    self.ledger.complete_repository_handoff(item["id"], 4321, skill=other)
        self.assertEqual(self.ledger.item(item["id"])["next_root_repo"], "farm-hive")

    def test_retry_and_a_cancelled_successor_restart_at_the_initial_root(self):
        item, token = self.claimed()
        self.ledger.handoff_repository(item["id"], token, "farm-hive", skill=self.feature)
        self.ledger.complete_repository_handoff(item["id"], 4321, skill=self.feature)
        self.ledger.fail_queued(item["id"], "budget exhausted")
        retried = self.ledger.retry(item["id"], "try again")
        self.assertIsNone(retried["root_repo"])
        self.assertEqual(write_repositories(retried, self.feature), ("Farm-Contract",))
        self.ledger.connection.execute("UPDATE work_items SET root_repo='Farm-Client' WHERE id=?", (item["id"],))
        self.ledger.cancel(item["id"], "stopped")
        successor = self.ledger.retry(item["id"], "continue")
        self.assertEqual(successor["predecessor_id"], item["id"])
        self.assertEqual(write_repositories(successor, self.feature), ("Farm-Contract",))


class SuccessorTests(LedgerBase):
    """A job that starts at an initial root continues across Stop, re-delegation and conversations: its successor
    links the cancelled job, restarts at the initial root and reads its plan (spec §5.7, §9.4; plan P4)."""
    APP = "e5a8c16d-9f85-4123-acf5-94e41c3304d5"
    # The plan after stage A, in the Phase B plan's Shared Interfaces shape.
    PLAN = {"stages": {"A": "done", "B": "pending", "C": "pending", "D": "pending", "G": "pending"},
            "prs": {"Farm-Contract": [{"branch": "farmbot/farm-1", "role": "issue", "head": "a" * 40,
                                       "pr": {"url": "https://github.com/Kuaiwa-Network/Farm-Contract/pull/12",
                                              "state": "draft", "merge": None}}]},
            "started": True}

    def setUp(self):
        super().setUp()
        self.feature = opt_in_skill(Path(self.tmp.name) / "skills")

    def job(self, skill="feature", session=SESSION):
        """A job of `skill` in delegation session `session` on the issue, which stays delegated to the app. No
        target: neither the receiver nor a conversation gives a feature job one (plan P6)."""
        self.ledger.observe_issue(issue(delegate_id=self.APP))
        self.ledger.ensure_session(session, ISSUE, delegation=True)
        return self.ledger.create_work_item(issue_id=ISSUE, session_id=session, skill=skill)

    def at_second_stage(self, item):
        """Run `item` through stage A, with its first question round (`questions-1`, plan P15) and a plan, into its
        common-rooted stage, where a Stop would find it."""
        self.ledger.set_worker(item["id"], 4321, "test")
        token = self.ledger.claim(item["id"], worker_id="stage-a")["token"]
        self.ledger.prepare_notice(item["id"], token, "question", "questions-1", "契约提案有两个问题需要确认。")
        self.ledger.confirm_notice(item["id"], "questions-1", "comment-questions-1")
        self.ledger.checkpoint(item["id"], token, {"plan": self.PLAN, "handoff": {
            "facts": [], "hypotheses": [], "checks": [], "repositories": [], "next_actions": ["Declare the config"]}})
        self.ledger.handoff_repository(item["id"], token, "common", skill=self.feature)
        self.ledger.complete_repository_handoff(item["id"], 4321, skill=self.feature)
        self.assertEqual(self.ledger.item(item["id"])["root_repo"], "common")
        return item

    def conversation(self, session="mention"):
        """A claimed conversation on the issue with one message; `mention` is not a delegation session."""
        self.ledger.ensure_session(session, ISSUE, delegation=session != "mention")
        chat = self.ledger.create_work_item(issue_id=ISSUE, session_id=session, skill="chat")
        self.ledger.push_inbox(chat["id"], "继续做")
        return chat, self.ledger.claim(chat["id"], worker_id="conversation")["token"]

    def request(self, chat, token):
        message = self.ledger.issue_context(chat["id"])["session_messages"][-1]["id"]
        return self.ledger.request_repair(chat["id"], token, message, self.APP, "Continue the feature.")

    def test_a_redelegation_links_the_cancelled_job_of_its_skill_and_restarts_it_at_its_initial_root(self):
        """The successor reads the plan and the posted notices, so its next question round is `questions-2` (P15)."""
        first = self.at_second_stage(self.job())
        self.ledger.cancel(first["id"], "Stop")
        second = self.job(session="session-2")
        self.assertEqual(second["predecessor_id"], first["id"])
        self.assertIsNone(second["root_repo"])
        self.assertEqual(write_repositories(second, self.feature), ("Farm-Contract",))
        recovery = self.ledger.issue_context(second["id"])["recovery"]
        self.assertEqual((recovery["predecessor_id"], recovery["plan"]), (first["id"], self.PLAN))
        self.assertEqual([(n["item_id"], n["request_id"], n["kind"], n["remote_id"]) for n in recovery["notices"]],
                         [(first["id"], "questions-1", "question", "comment-questions-1")])

    def test_only_a_cancelled_job_of_the_same_write_skill_is_linked(self):
        fix = self.job(skill="fix")
        self.ledger.cancel(fix["id"], "Stop")
        feature = self.job()
        self.assertIsNone(feature["predecessor_id"])  # another skill's job is not this job's past
        self.ledger.fail_queued(feature["id"], "budget exhausted")
        again = self.job(session="session-2")
        self.assertIsNone(again["predecessor_id"])  # a failed job is retried, not succeeded
        self.ledger.cancel(again["id"], "Stop")
        self.assertIsNone(self.job(skill="chat", session="session-3")["predecessor_id"])  # chat continues nothing

    def test_a_conversation_continues_the_delegations_latest_write_job_whatever_its_skill(self):
        fix = self.job(skill="fix")
        self.ledger.cancel(fix["id"], "Stop")
        self.now += 1
        feature = self.at_second_stage(self.job(session="session-2"))
        self.ledger.cancel(feature["id"], "Stop")
        chat, token = self.conversation()
        self.assertEqual(self.ledger.issue_context(chat["id"])["resumable_work"]["id"], feature["id"])
        successor = self.request(chat, token)
        self.assertEqual((successor["skill"], successor["predecessor_id"], successor["session_id"]),
                         ("feature", feature["id"], "session-2"))
        self.assertEqual(write_repositories(successor, self.feature), ("Farm-Contract",))
        self.assertEqual(self.ledger.issue_context(successor["id"])["recovery"]["plan"], self.PLAN)

    def test_a_conversation_in_a_delegation_session_continues_that_sessions_job(self):
        fix = self.job(skill="fix")
        self.ledger.cancel(fix["id"], "Stop")
        self.now += 1
        self.ledger.cancel(self.job(session="session-2")["id"], "Stop")
        chat, _ = self.conversation(session=SESSION)
        self.assertEqual(self.ledger.issue_context(chat["id"])["resumable_work"]["id"], fix["id"])

    def test_a_continued_job_restarts_at_its_initial_root_and_keeps_its_plan(self):
        feature = self.at_second_stage(self.job())
        self.ledger.fail_queued(feature["id"], "budget exhausted")
        chat, token = self.conversation()
        resumed = self.request(chat, token)
        self.assertEqual((resumed["id"], resumed["state"], resumed["root_repo"]), (feature["id"], "queued", None))
        self.assertEqual(write_repositories(resumed, self.feature), ("Farm-Contract",))
        self.assertEqual(self.ledger.issue_context(feature["id"])["plan"], self.PLAN)

    def test_an_fgui_job_is_not_continued_from_a_conversation(self):
        self.ledger.cancel(self.job(skill="fgui")["id"], "Stop")
        chat, _ = self.conversation()
        self.assertIsNone(self.ledger.issue_context(chat["id"])["resumable_work"])


class StageAllowanceTests(LedgerBase):
    """Automatic-retry allowances bound one stage of a job that starts at an initial root, and the whole job of a
    fix (spec §5.8, D16): capacity and publication retries and the Unity execution and setup budgets."""
    SPENT = (2, 3, 1, 2)

    def setUp(self):
        super().setUp()
        self.feature = opt_in_skill(Path(self.tmp.name) / "skills")

    def running(self, skill="feature"):
        """A claimed job of `skill`; a feature job has no target, as the receiver and a conversation make it (P6)."""
        item = self.new_item(skill=skill, target=None if skill == "feature" else PIN)
        self.ledger.set_worker(item["id"], 4321, "test")
        return item["id"], self.ledger.claim(item["id"], worker_id="w")["token"]

    def spend(self, item_id):
        """Use part of every allowance, as delayed capacity and publication retries and Unity recoveries do."""
        self.ledger.connection.execute("UPDATE work_items SET capacity_retries=2,publication_retries=3 WHERE id=?",
                                       (item_id,))
        self.ledger.connection.execute("INSERT OR REPLACE INTO resource_job_retries(item_id,attempts,setup_attempts) "
                                       "VALUES(?,1,2)", (item_id,))

    def allowances(self, item_id):
        item, recovery = self.ledger.item(item_id), self.ledger.issue_context(item_id)["resource_recovery"]
        return item["capacity_retries"], item["publication_retries"], recovery["attempts"], recovery["setup_attempts"]

    def hand_off(self, item_id, token, to_repo, skill):
        self.ledger.checkpoint(item_id, token, {"handoff": {
            "facts": [], "hypotheses": [], "checks": [], "repositories": [], "next_actions": ["Start the next stage"]}})
        self.spend(item_id)
        self.ledger.handoff_repository(item_id, token, to_repo, skill=skill)
        return self.ledger.complete_repository_handoff(item_id, 4321, skill=skill)

    def test_a_completed_handoff_starts_the_next_stage_with_fresh_allowances(self):
        item_id, token = self.running()
        self.assertEqual(self.hand_off(item_id, token, "common", self.feature)["root_repo"], "common")
        self.assertEqual(self.allowances(item_id), (0, 0, 0, 0))

    def test_an_answer_to_a_pause_resumes_the_job_with_fresh_allowances(self):
        """Both reasons resume a new stage, such as the gap-list questions and the config-ready pause after the
        `config-needed` notice (P15), and so does an answer that arrived before the pause was parked, which requeues
        the item at once (spec §5.2)."""
        for reason, early in (("question", False), ("waiting", False), ("waiting", True)):
            with self.subTest(reason=reason, early=early):
                self.setUp()
                item_id, token = self.running()
                self.spend(item_id)
                if early:
                    self.ledger.push_inbox(item_id, "配置已经发布")
                self.ledger.await_input(item_id, token, "配置发布了吗？", reason=reason)
                if not early:
                    self.assertEqual(self.allowances(item_id), self.SPENT)  # parked: nothing has resumed yet
                    self.ledger.push_inbox(item_id, "配置已经发布", resume_waiting=True)
                self.assertEqual((self.ledger.item(item_id)["state"], self.allowances(item_id)), ("queued", (0, 0, 0, 0)))

    def test_the_same_stage_keeps_its_allowances(self):
        """A message to a running attempt, a recovered lease and a slot the pool grants continue one stage. No Phase B
        feature job waits for a slot (it has no target and its manifest lists no resource: P6, P11), but fgui will;
        the pool's grant is `Ledger.resume` of an item parked for its slot (`SlotPool.grant`), parked here directly."""
        item_id, _ = self.running()
        self.spend(item_id)
        self.ledger.push_inbox(item_id, "顺便看一下日志", resume_waiting=True)  # steering a running attempt
        self.now += 61
        self.ledger.recover(item_id, "worker exited with an expired lease")
        self.ledger.connection.execute("UPDATE work_items SET state='awaiting_resource',needs_resource=? WHERE id=?",
                                       ("unity_slot:batch", item_id))
        self.ledger.resume(item_id, "the pool granted the slot")
        self.assertEqual((self.ledger.item(item_id)["state"], self.allowances(item_id)), ("queued", self.SPENT))

    def test_fix_keeps_job_lifetime_allowances(self):
        item_id, token = self.running(skill="fix")
        self.hand_off(item_id, token, "Farm-Contract", SKILLS["fix"])
        self.assertEqual(self.allowances(item_id), self.SPENT)
        token = self.ledger.claim(item_id, worker_id="w2")["token"]
        self.ledger.await_input(item_id, token, "哪个服？")
        self.ledger.push_inbox(item_id, "公共测试服", resume_waiting=True)
        self.assertEqual((self.ledger.item(item_id)["state"], self.allowances(item_id)), ("queued", self.SPENT))

    def test_the_rule_names_every_repository_skill_with_an_initial_root_and_not_fix(self):
        """The ledger reads no manifests, so it knows these skills by name; this keeps the names and the manifests
        together when a skill with an initial root ships."""
        from agent.ledger import STAGE_ALLOWANCE_SKILLS
        self.assertLessEqual({name for name, skill in SKILLS.items() if skill.initial_root is not None},
                             set(STAGE_ALLOWANCE_SKILLS))
        self.assertIn(self.feature.name, STAGE_ALLOWANCE_SKILLS)
        self.assertNotIn("fix", STAGE_ALLOWANCE_SKILLS)


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
        self.assertEqual(self.ledger.cancel(item["id"], "again")["state"], "cancelled")
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

    def test_a_cancel_limited_to_waiting_states_never_takes_a_claimed_item(self):
        """Delegation removal (spec §9.8) cancels only work no worker holds, decided in the cancel's own transaction."""
        waiting = ("queued", "awaiting_input", "awaiting_resource")
        item = self.new_item()
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.assertIsNone(self.ledger.cancel(item["id"], "delegation removed", states=waiting))
        self.ledger.renew(item["id"], token)  # the claim is untouched
        self.ledger.await_input(item["id"], token, "Which server?")
        self.assertEqual(self.ledger.cancel(item["id"], "delegation removed", states=waiting)["state"], "cancelled")
        self.assertIsNone(self.ledger.cancel(item["id"], "delegation removed", states=waiting))  # nothing left to do
        self.assertEqual(self.ledger.cancel(item["id"], "stop")["state"], "cancelled")  # without states, as before

    def test_retry_cancelled_item_creates_fresh_generation_on_successor(self):
        item = self.new_item()
        self.ledger.cancel(item["id"], "stop")
        view = self.ledger.retry(item["id"], "human asked 重试")
        self.assertEqual((view["state"], view["generation"]), ("queued", 0))
        self.assertNotEqual(view["id"], item["id"])
        with self.assertRaises(LedgerError):
            self.ledger.retry(item["id"], "already queued")


class SessionPeopleTests(LedgerBase):
    """Who opened a session, and who wrote each message and when (spec §5.3, §9.2), in Task 2's person shape."""

    def test_a_session_keeps_its_first_creator_and_a_later_event_only_fills_a_missing_one(self):
        self.ledger.observe_issue(issue())
        self.ledger.ensure_session(SESSION, ISSUE, delegation=True)
        self.assertIsNone(self.ledger.session(SESSION)["creator"])
        self.ledger.ensure_session(SESSION, ISSUE, delegation=True, creator=OWNER)
        self.assertEqual(self.ledger.session(SESSION)["creator"], OWNER)
        self.ledger.ensure_session(SESSION, ISSUE, delegation=True, creator=DESIGNER)
        self.assertEqual(self.ledger.session(SESSION)["creator"], OWNER)

    def test_each_message_carries_its_author_and_time_and_pop_inbox_still_returns_bodies(self):
        item = self.new_item()
        self.now = 1790307000.0  # 2026-09-25T03:30:00Z
        self.ledger.push_inbox(item["id"], "先看服务端日志", author=DESIGNER)
        self.now += 90
        self.ledger.push_inbox(item["id"], "（无正文）")
        expected = [{"id": 1, "body": "先看服务端日志", "author": DESIGNER, "created_at": "2026-09-25T03:30:00+00:00"},
                    {"id": 2, "body": "（无正文）", "author": None, "created_at": "2026-09-25T03:31:30+00:00"}]
        context = self.ledger.issue_context(item["id"])
        self.assertEqual(context["session_messages"], expected)
        self.assertEqual(context["conversation_history"][0]["messages"], expected)
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.assertEqual(self.ledger.pop_inbox(item["id"], token), ["先看服务端日志", "（无正文）"])

    def test_a_message_keeps_the_time_farmbot_received_it_when_given(self):
        item = self.new_item()  # the clock reads 1000.0, long after the webhook arrived
        self.ledger.push_inbox(item["id"], "先看服务端日志", received_at=1790380500.0)  # 2026-09-25T23:55:00Z
        self.assertEqual(self.ledger.issue_context(item["id"])["session_messages"][0]["created_at"],
                         "2026-09-25T23:55:00+00:00")
        for bad in ("1790380500", float("nan"), float("inf"), True):
            with self.subTest(bad=bad), self.assertRaises(LedgerError):
                self.ledger.push_inbox(item["id"], "x", received_at=bad)
        self.assertEqual(len(self.ledger.issue_context(item["id"])["session_messages"]), 1)

    def test_anything_but_exactly_a_person_is_refused_and_never_stored(self):
        item = self.new_item()
        for bad in ("Designer One", {"id": DESIGNER["id"], "name": "Designer One"},
                    {**DESIGNER, "url": "https://example.com/designer-one"},
                    {**DESIGNER, "email": "designer.one@example.com"}):
            with self.subTest(bad=bad):
                with self.assertRaises(LedgerError):
                    self.ledger.push_inbox(item["id"], "x", author=bad)
                with self.assertRaises(LedgerError):
                    self.ledger.ensure_session("session-9", ISSUE, delegation=False, creator=bad)
        self.assertEqual(self.ledger.issue_context(item["id"])["session_messages"], [])
        self.assertIsNone(self.ledger.session("session-9"))
        self.assertNotIn("designer.one@example.com", "\n".join(self.ledger.connection.iterdump()))

    def test_a_chat_handed_to_repair_keeps_who_wrote_each_message_and_when(self):
        app_user = "e5a8c16d-9f85-4123-acf5-94e41c3304d5"
        self.ledger.observe_issue(issue(delegate_id=app_user))
        self.ledger.ensure_session(SESSION, ISSUE, delegation=True, creator=OWNER)
        chat = self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill="chat")
        self.now = 1790307000.0  # 2026-09-25T03:30:00Z
        self.ledger.push_inbox(chat["id"], "请修复，保留现有排序", author=DESIGNER)
        self.now += 1800  # the repair starts half an hour later; the copied message keeps its own time
        token = self.ledger.claim(chat["id"], worker_id="investigator")["token"]
        message_id = self.ledger.issue_context(chat["id"])["session_messages"][-1]["id"]
        fix = self.ledger.request_repair(chat["id"], token, message_id, app_user, "Sorting view confirmed.")
        self.assertEqual([(m["body"], m["author"], m["created_at"])
                          for m in self.ledger.issue_context(fix["id"])["session_messages"]],
                         [("请修复，保留现有排序", DESIGNER, "2026-09-25T03:30:00+00:00")])


class OwnerTests(LedgerBase):
    """issue-context's owner and creator (spec §5.3, D5, D17), from Task 2's issue metadata and Task 3's sessions."""

    def context_for(self, issue_id=ISSUE, session=SESSION, *, delegator=None, **changes):
        """issue-context of a fix item on an issue with these fields, delegated in a session `delegator` opened."""
        self.ledger.observe_issue(issue(id=issue_id, **changes))
        self.ledger.ensure_session(session, issue_id, delegation=True, creator=delegator)
        item = self.ledger.create_work_item(issue_id=issue_id, session_id=session, skill="fix", target=PIN)
        return self.ledger.issue_context(item["id"])

    def test_the_assignee_owns_the_issue_and_its_creator_is_named_beside_them(self):
        context = self.context_for(assignee=OWNER, creator=DESIGNER, delegator=LEAD)
        self.assertEqual(context["owner"], {"person": OWNER, "source": "assignee"})
        self.assertEqual(context["creator"], DESIGNER)

    def test_an_unassigned_issue_is_owned_by_whoever_delegated_it(self):
        context = self.context_for(assignee=None, creator=DESIGNER, delegator=LEAD)
        self.assertEqual(context["owner"], {"person": LEAD, "source": "delegator"})
        self.assertEqual(context["creator"], DESIGNER)

    def test_without_an_assignee_or_a_recorded_delegator_nobody_owns_the_issue(self):
        # An operator enqueue, or a session whose creator Linear did not report: FarmBot asks without a mention.
        context = self.context_for(assignee=None, creator=DESIGNER)
        self.assertIsNone(context["owner"])
        self.assertEqual(context["creator"], DESIGNER)

    def test_the_creator_is_left_out_when_it_is_the_owner_or_unknown(self):
        third = "10000000-0000-4000-8000-000000000003"
        cases = {"the assignee": self.context_for(assignee=OWNER, creator=OWNER),
                 "the delegator": self.context_for(OTHER, "session-2", identifier="FARM-2", assignee=None,
                                                   creator=LEAD, delegator=LEAD),
                 "unknown": self.context_for(third, "session-3", identifier="FARM-3", assignee=OWNER,
                                             creator=None)}
        for case, context in cases.items():
            with self.subTest(case):
                self.assertIsNotNone(context["owner"])
                self.assertIsNone(context["creator"])

    def test_an_issue_and_session_recorded_before_people_were_kept_name_nobody(self):
        self.ledger.observe_issue(issue())
        metadata = self.ledger.issue(ISSUE)
        for key in ("assignee", "creator"):
            metadata.pop(key, None)
        self.ledger.connection.execute("UPDATE issues SET metadata=? WHERE id=?", (json.dumps(metadata), ISSUE))
        self.ledger.ensure_session(SESSION, ISSUE, delegation=True)
        item = self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill="fix", target=PIN)
        context = self.ledger.issue_context(item["id"])
        self.assertEqual((context["owner"], context["creator"]), (None, None))

    def test_a_mention_does_not_make_its_author_the_owner(self):
        self.ledger.observe_issue(issue(assignee=None, creator=DESIGNER))
        self.ledger.ensure_session("mention", ISSUE, delegation=False, creator=OWNER)
        chat = self.ledger.create_work_item(issue_id=ISSUE, session_id="mention", skill="chat")
        self.assertIsNone(self.ledger.issue_context(chat["id"])["owner"])
        self.ledger.ensure_session(SESSION, ISSUE, delegation=True, creator=LEAD)
        context = self.ledger.issue_context(chat["id"])
        self.assertEqual(context["owner"], {"person": LEAD, "source": "delegator"})
        self.assertEqual(context["delegation_session"], SESSION)

    def test_a_redelegation_hands_the_issue_to_whoever_delegated_it_last(self):
        """spec §4.2: whoever takes an unassigned card over delegates it again; an item started earlier follows."""
        item_id = self.context_for(assignee=None, creator=DESIGNER, delegator=LEAD)["coordination"]["id"]
        self.now += 60
        self.ledger.ensure_session("session-2", ISSUE, delegation=True, creator=OWNER)
        context = self.ledger.issue_context(item_id)
        self.assertEqual(context["owner"], {"person": OWNER, "source": "delegator"})
        self.assertEqual(context["delegation_session"], SESSION)  # the item's own authority is unchanged
        self.assertEqual(context["creator"], DESIGNER)
        self.now += 60
        self.ledger.ensure_session("session-3", ISSUE, delegation=True)  # the latest delegator is unknown
        self.assertIsNone(self.ledger.issue_context(item_id)["owner"])

    def test_an_operator_enqueue_hands_the_issue_to_nobody_and_takes_it_from_nobody(self):
        """`service enqueue` records a `local-` delegation session in the ledger, not in Linear, even when the
        enqueue is then refused because an item is active. Nobody delegated anything, so the latest human
        delegator keeps the issue, and an issue only ever enqueued has no owner (spec §5.3)."""
        item_id = self.context_for(assignee=None, creator=DESIGNER, delegator=LEAD)["coordination"]["id"]
        self.now += 60
        self.ledger.ensure_session(f"local-{ISSUE}", ISSUE, delegation=True)
        context = self.ledger.issue_context(item_id)
        self.assertEqual(context["owner"], {"person": LEAD, "source": "delegator"})
        self.assertEqual(context["delegation_session"], SESSION)
        self.assertIsNone(self.context_for(OTHER, f"local-{OTHER}", identifier="FARM-2", assignee=None,
                                           creator=DESIGNER)["owner"])


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


class PauseTests(LedgerBase):
    def running(self):
        item = self.new_item()
        return item["id"], self.ledger.claim(item["id"], worker_id="w")["token"]

    def running_with_slot(self):
        item_id, token = self.running()
        self.ledger.await_resource(item_id, token, "unity_slot", "interactive")
        self.ledger.ensure_slot("unity_slot:1", kind="unity_slot", host="h", folder=str(self.path.parent / "slot-1"))
        granted = self.ledger.acquire("unity_slot", owner="pool", host="h")
        self.ledger.resume(item_id, "the pool granted the slot")
        return item_id, self.ledger.claim(item_id, worker_id="w2")["token"], granted

    def test_await_input_records_its_reason_beside_the_question(self):
        item_id, token = self.running()
        with self.assertRaisesRegex(LedgerError, "question or waiting"):
            self.ledger.await_input(item_id, token, "需要哪个环境？", reason="later")
        self.assertEqual(self.ledger.item(item_id)["state"], "running")
        view = self.ledger.await_input(item_id, token, "需要哪个环境？")
        self.assertEqual((view["checkpoint"]["pending_question"], view["checkpoint"]["pending_reason"]),
                         ("需要哪个环境？", "question"))
        self.ledger.resume(item_id, "human replied")
        token = self.ledger.claim(item_id, worker_id="w2")["token"]
        self.ledger.await_input(item_id, token, "等待策划发布配置。", reason="waiting")
        context = self.ledger.issue_context(item_id)
        self.assertEqual((context["pending_question"], context["pending_reason"]), ("等待策划发布配置。", "waiting"))

    def test_await_input_refuses_while_any_reservation_is_open(self):
        """A pause holds no process and no Unity slot (spec §5.2), like a repository handoff."""
        item_id, token, granted = self.running_with_slot()
        for state in ("active", "cancel_requested", "queued"):
            with self.subTest(state=state):
                if state == "cancel_requested":
                    self.ledger.cancel_reservations(item_id, "operator withdrew the slot")
                if state == "queued":
                    self.ledger.release(granted["reservation_id"], granted["token"], "probe settled")
                    self.ledger.requeue_reservation(granted["reservation_id"], "retryable switch failure")
                self.assertEqual([r["state"] for r in self.ledger.reservations(Ledger.RESERVATION_OPEN)], [state])
                for reason in ("question", "waiting"):
                    with self.assertRaisesRegex(LedgerError, "release the resource reservation"):
                        self.ledger.await_input(item_id, token, "需要哪个环境？", reason=reason)
                self.assertEqual(self.ledger.item(item_id)["state"], "running")
        self.ledger.cancel_reservations(item_id, "request withdrawn")
        self.assertEqual(self.ledger.await_input(item_id, token, "需要哪个环境？")["state"], "awaiting_input")

    def test_only_the_items_own_reservation_blocks_its_pause(self):
        item_id, token, granted = self.running_with_slot()
        self.ledger.observe_issue(issue(id=OTHER, identifier="FARM-2"))
        self.ledger.ensure_session("session-2", OTHER, delegation=True)
        other = self.ledger.create_work_item(issue_id=OTHER, session_id="session-2", skill="fix", target=PIN)["id"]
        other_token = self.ledger.claim(other, worker_id="w3")["token"]
        self.assertEqual(self.ledger.await_input(other, other_token, "需要哪个环境？")["state"], "awaiting_input")
        with self.assertRaisesRegex(LedgerError, "release the resource reservation"):
            self.ledger.await_input(item_id, token, "需要哪个环境？")

    def test_either_pause_resumes_on_a_reply_and_keeps_a_reply_that_came_first(self):
        """The receiver resumes both reasons through push_inbox, and an answer that lands between the elicitation
        and the pause requeues the item at once (spec §5.2) whatever the reason."""
        for reason in ("question", "waiting"):
            with self.subTest(reason=reason):
                self.setUp()
                item_id, token = self.running()
                self.ledger.await_input(item_id, token, "等待策划发布配置。", reason=reason)
                delivered = self.ledger.push_inbox(item_id, "发布好了", resume_waiting=True)
                self.assertEqual((delivered["state"], self.ledger.item(item_id)["state"]), ("queued", "queued"))
                token = self.ledger.claim(item_id, worker_id="w2")["token"]
                self.ledger.push_inbox(item_id, "先回答了")
                view = self.ledger.await_input(item_id, token, "需要哪个环境？", reason=reason)
                self.assertEqual((view["state"], view["resume_authorized"]), ("queued", True))


class RevalidateTests(LedgerBase):
    URL = "https://github.com/Kuaiwa-Network/Farm-Contract/pull/12"
    HANDOFF = {"facts": [], "hypotheses": [], "checks": [], "repositories": [], "next_actions": ["Implement the client"]}

    def running(self, **issue_changes):
        item = self.new_item(**issue_changes)
        self.ledger.set_worker(item["id"], 4321, "test")
        return item["id"], self.ledger.claim(item["id"], worker_id="w")["token"]

    def test_revalidate_accepts_only_the_current_fingerprint_and_audits_both(self):
        item_id, token = self.running()
        claimed = self.ledger.observe_issue(issue())["fingerprint"]
        current = self.ledger.observe_issue(issue(comments=[comment("初始值为零")]))["fingerprint"]
        with self.assertRaisesRegex(LedgerError, "issue changed since that read"):
            self.ledger.revalidate(item_id, token, claimed)
        for malformed in ("A" * 64, "0" * 63, "", None):
            with self.subTest(fingerprint=malformed), self.assertRaisesRegex(LedgerError, "64-character"):
                self.ledger.revalidate(item_id, token, malformed)
        with self.assertRaisesRegex(LedgerError, "running claim"):
            self.ledger.revalidate(item_id, "claim_wrong", current)
        view = self.ledger.revalidate(item_id, token, current)
        self.assertEqual((view["previous_fingerprint"], view["claimed_fingerprint"]), (claimed, current))
        rows = self.ledger.connection.execute("SELECT details FROM audit WHERE item_id=? AND kind='revalidate'",
                                              (item_id,)).fetchall()
        self.assertEqual([json.loads(row["details"]) for row in rows], [{"from": claimed, "to": current}])
        self.ledger.observe_issue(issue(comments=[comment("初始值为零")], status_type="completed"))
        with self.assertRaisesRegex(LedgerError, "left scope"):
            self.ledger.revalidate(item_id, token, current)

    def test_a_human_comment_refuses_the_handoff_until_revalidate_and_a_fresh_checkpoint(self):
        item_id, token = self.running()
        self.ledger.checkpoint(item_id, token, {"handoff": self.HANDOFF})
        current = self.ledger.observe_issue(issue(comments=[comment("初始值为零")]))["fingerprint"]
        with self.assertRaisesRegex(LedgerError, "issue changed; revalidate"):
            self.ledger.handoff_repository(item_id, token, "Farm-Contract", skill=SKILLS["fix"])
        self.ledger.revalidate(item_id, token, current)
        # The saved handoff predates the re-read, so it stays stale until it is saved again.
        self.assertTrue(self.ledger.issue_context(item_id)["handoff"]["stale"])
        with self.assertRaisesRegex(LedgerError, "save a current worker checkpoint"):
            self.ledger.handoff_repository(item_id, token, "Farm-Contract", skill=SKILLS["fix"])
        self.ledger.checkpoint(item_id, token, {"handoff": self.HANDOFF})
        self.assertEqual(self.ledger.handoff_repository(item_id, token, "Farm-Contract",
                                                        skill=SKILLS["fix"])["next_root_repo"], "Farm-Contract")

    def test_a_late_pr_registration_after_a_human_comment_proceeds_after_revalidate(self):
        item_id, token = self.running()
        # Linear attached the worker's PR, and a human commented, before the worker registered the PR.
        current = self.ledger.observe_issue(issue(attachments=[self.URL], comments=[comment("初始值为零")]))["fingerprint"]
        with self.assertRaisesRegex(LedgerError, "revalidate before registering"):
            self.ledger.checkpoint(item_id, token, {"published_prs": [self.URL]}, verified_prs=[self.URL])
        self.ledger.revalidate(item_id, token, current)
        with self.assertRaisesRegex(LedgerError, "already issue input"):  # still only verified job output
            self.ledger.checkpoint(item_id, token, {"published_prs": [self.URL]})
        self.ledger.checkpoint(item_id, token, {"published_prs": [self.URL], "handoff": self.HANDOFF},
                               verified_prs=[self.URL])
        self.assertEqual(self.ledger.issue_context(item_id)["published_prs"], [self.URL])
        # Registering the echo moved the claim with the stored input, so the handoff saved with it is current.
        self.assertEqual(self.ledger.handoff_repository(item_id, token, "Farm-Contract",
                                                        skill=SKILLS["fix"])["next_root_repo"], "Farm-Contract")

    def test_a_pr_attached_before_the_claim_is_registered_only_after_this_claim_revalidates(self):
        item_id, token = self.running(attachments=[self.URL])
        refused = {"published_prs": [self.URL]}
        with self.assertRaisesRegex(LedgerError, "revalidate before registering"):
            self.ledger.checkpoint(item_id, token, refused, verified_prs=[self.URL])
        current = self.ledger.observe_issue(issue(attachments=[self.URL]))["fingerprint"]
        self.ledger.revalidate(item_id, token, current)
        self.now += 61
        self.ledger.recover(item_id, "worker exited")
        token = self.ledger.claim(item_id, worker_id="w2")["token"]  # a new claim starts a new baseline
        with self.assertRaisesRegex(LedgerError, "revalidate before registering"):
            self.ledger.checkpoint(item_id, token, refused, verified_prs=[self.URL])
        self.ledger.revalidate(item_id, token, current)
        self.ledger.checkpoint(item_id, token, refused, verified_prs=[self.URL])
        self.assertEqual(self.ledger.issue_context(item_id)["published_prs"], [self.URL])

    def revalidated_blocker(self):
        item_id, token = self.running()
        current = self.ledger.observe_issue(issue(comments=[comment("初始值为零")]))["fingerprint"]
        self.ledger.revalidate(item_id, token, current)
        blocker = self.ledger.prepare_comment(item_id, token, "blocker", "需要策划确认。")
        self.ledger.confirm_comment(blocker["action_id"], "remote-1")
        return item_id, token, {"summary": "需要确认", "comment_action_id": blocker["action_id"]}

    def test_finish_after_revalidate_settles_on_the_input_the_worker_read(self):
        item_id, token, outcome = self.revalidated_blocker()
        self.assertEqual(self.ledger.finish(item_id, token, "blocked", outcome)["state"], "blocked")

    def test_a_comment_after_revalidate_still_requeues_finish(self):
        """Spec §11: the fresh worker re-reads the later comment and finishes."""
        item_id, token, outcome = self.revalidated_blocker()
        self.ledger.observe_issue(issue(comments=[comment("初始值为零"), comment("改成一", id="comment-2")]))
        view = self.ledger.finish(item_id, token, "blocked", outcome)
        self.assertEqual((view["state"], view["generation"]), ("queued", 1))

    def test_revalidate_refuses_rather_than_clearing_a_requested_requeue(self):
        """No code path sets requeue_requested; its readers treat it as a restart request a re-read must not cancel."""
        item_id, token = self.running()
        self.ledger.connection.execute("UPDATE work_items SET requeue_requested=1 WHERE id=?", (item_id,))
        current = self.ledger.observe_issue(issue())["fingerprint"]
        with self.assertRaisesRegex(LedgerError, "requeue is already requested"):
            self.ledger.revalidate(item_id, token, current)
        self.assertEqual(self.ledger.connection.execute("SELECT requeue_requested FROM work_items WHERE id=?",
                                                        (item_id,)).fetchone()[0], 1)

    PR_URL = "https://github.com/Kuaiwa-Network/Farm-Contract/pull/12"
    HANDOFF = {"facts": [], "hypotheses": [], "checks": [], "repositories": [], "next_actions": ["Implement the client"]}

    def claim_state(self, item_id):
        row = self.ledger.connection.execute(
            "SELECT claimed_fingerprint, revalidated_fingerprint FROM work_items WHERE id=?", (item_id,)).fetchone()
        audits = self.ledger.connection.execute(
            "SELECT count(*) FROM audit WHERE item_id=? AND kind='revalidate'", (item_id,)).fetchone()[0]
        return tuple(row), audits

    def test_a_comment_after_revalidate_refuses_the_late_pr_registration(self):
        """The late-PR rule has two halves: a revalidated claim, and nothing else changed since."""
        item_id, token = self.running()
        read = self.ledger.observe_issue(issue(attachments=[self.PR_URL], comments=[comment("初始值为零")]))["fingerprint"]
        self.ledger.revalidate(item_id, token, read)
        self.ledger.observe_issue(issue(attachments=[self.PR_URL],
                                        comments=[comment("初始值为零"), comment("改成一", id="comment-2")]))
        before = self.claim_state(item_id)
        with self.assertRaisesRegex(LedgerError, "revalidate before registering"):
            self.ledger.checkpoint(item_id, token, {"published_prs": [self.PR_URL]}, verified_prs=[self.PR_URL])
        self.assertEqual(self.ledger.issue_context(item_id)["published_prs"], [])
        self.assertEqual(self.claim_state(item_id), before)

    def test_a_comment_after_revalidate_refuses_the_handoff(self):
        item_id, token = self.running()
        read = self.ledger.observe_issue(issue(comments=[comment("初始值为零")]))["fingerprint"]
        self.assertEqual(self.ledger.issue_context(item_id)["fingerprint"], read)  # what the worker read, to revalidate on
        self.ledger.revalidate(item_id, token, read)
        self.ledger.checkpoint(item_id, token, {"handoff": self.HANDOFF})
        self.ledger.observe_issue(issue(comments=[comment("初始值为零"), comment("改成一", id="comment-2")]))
        with self.assertRaisesRegex(LedgerError, "issue changed; revalidate"):
            self.ledger.handoff_repository(item_id, token, "Farm-Contract", skill=SKILLS["fix"])

    def test_a_retired_or_expired_claim_cannot_revalidate(self):
        for retire in ("cancel", "handoff", "expire"):
            with self.subTest(retire=retire):
                self.setUp()
                item_id, token = self.running()
                current = self.ledger.observe_issue(issue(comments=[comment("初始值为零")]))["fingerprint"]
                if retire == "cancel":
                    self.ledger.cancel(item_id, "stop")
                elif retire == "handoff":
                    self.ledger.checkpoint(item_id, token, {"handoff": self.HANDOFF})
                    self.ledger.revalidate(item_id, token, current)
                    self.ledger.checkpoint(item_id, token, {"handoff": self.HANDOFF})
                    self.ledger.handoff_repository(item_id, token, "Farm-Contract", skill=SKILLS["fix"])
                    current = self.ledger.observe_issue(issue(comments=[comment("x", id="c-3")]))["fingerprint"]
                else:
                    self.now += 61
                before = self.claim_state(item_id)
                with self.assertRaisesRegex(LedgerError, "running claim|lease expired"):
                    self.ledger.revalidate(item_id, token, current)
                self.assertEqual(self.claim_state(item_id), before)

    def test_the_late_pr_move_is_audited_and_stays_revalidated(self):
        item_id, token = self.running()
        claimed = self.claim_state(item_id)[0][0]
        read = self.ledger.observe_issue(issue(attachments=[self.PR_URL], comments=[comment("初始值为零")]))["fingerprint"]
        self.ledger.revalidate(item_id, token, read)
        self.ledger.checkpoint(item_id, token, {"published_prs": [self.PR_URL]}, verified_prs=[self.PR_URL])
        moved = self.ledger.connection.execute("SELECT fingerprint FROM issues").fetchone()[0]
        rows = self.ledger.connection.execute("SELECT details FROM audit WHERE item_id=? AND kind='revalidate' ORDER BY id",
                                              (item_id,)).fetchall()
        self.assertEqual([json.loads(r["details"]) for r in rows],
                         [{"from": claimed, "to": read}, {"from": read, "to": moved}])
        self.assertEqual(self.claim_state(item_id)[0], (moved, moved))


class PlanTests(LedgerBase):
    PLAN = {"stages": {"A": "done", "B": "pending"}, "pause": None, "started": True,
            "prs": {"Farm-Contract": [{"branch": "farmbot/farm-1", "role": "issue", "head": "a" * 40,
                                       "url": "https://github.com/Kuaiwa-Network/Farm-Contract/pull/12",
                                       "state": "draft"}]}}

    def running(self):
        item = self.new_item()
        self.ledger.set_worker(item["id"], 4321, "test")
        return item["id"], self.ledger.claim(item["id"], worker_id="w")["token"]

    def test_a_plan_is_carried_forward_until_a_checkpoint_replaces_it(self):
        item_id, token = self.running()
        self.assertIsNone(self.ledger.issue_context(item_id)["plan"])
        self.ledger.checkpoint(item_id, token, {"stage": "contract", "plan": self.PLAN})
        self.ledger.checkpoint(item_id, token, {"stage": "declarations"})
        self.assertEqual(self.ledger.issue_context(item_id)["plan"], self.PLAN)
        self.ledger.await_input(item_id, token, "配置发布后请回复。", reason="waiting")
        self.ledger.resume(item_id, "human replied")
        token = self.ledger.claim(item_id, worker_id="w2")["token"]
        self.assertEqual(self.ledger.issue_context(item_id)["plan"], self.PLAN)
        replaced = {"stages": {"A": "done", "B": "done"}}
        self.ledger.checkpoint(item_id, token, {"plan": replaced})
        self.assertEqual(self.ledger.issue_context(item_id)["plan"], replaced)  # replaced whole, never merged

    def test_invalid_plans_are_refused_and_the_saved_plan_stays(self):
        item_id, token = self.running()
        self.ledger.checkpoint(item_id, token, {"plan": self.PLAN})
        for label, plan in (("not an object", ["stages"]), ("null", None), ("unknown key", {"notes": "x"}),
                            ("long string", {"change": "x" * 2001}), ("long key", {"config": {"k" * 2001: 1}}),
                            ("long array", {"events": list(range(51))}),
                            ("nested long string", {"prs": {"Farm-Client": [{"url": "x" * 2001}]}}),
                            ("over 16000 characters", {"closing": {str(n): "x" * 1990 for n in range(9)}}),
                            ("not JSON", {"ui": float("nan")})):
            with self.subTest(label), self.assertRaises(LedgerError):
                self.ledger.checkpoint(item_id, token, {"plan": plan})
        self.assertEqual(self.ledger.issue_context(item_id)["plan"], self.PLAN)
        self.assertIsNone(self.ledger.checkpoint_error(item_id))  # a plan is not a handoff: nothing to repair
        edge = {"change": "x" * 2000, "events": list(range(50))}
        self.assertEqual(self.ledger.checkpoint(item_id, token, {"plan": edge})["checkpoint"]["plan"], edge)

    def test_a_plan_only_checkpoint_is_not_a_handoff(self):
        item_id, token = self.running()
        self.ledger.checkpoint(item_id, token, {"plan": self.PLAN})
        context = self.ledger.issue_context(item_id)
        self.assertEqual((context["plan"], context["handoff"]), (self.PLAN, None))
        with self.assertRaisesRegex(LedgerError, "save a current worker checkpoint"):
            self.ledger.handoff_repository(item_id, token, "Farm-Contract", skill=SKILLS["fix"])
        with self.assertRaises(LedgerError):
            self.ledger.checkpoint(item_id, token, {"handoff": {"facts": []}})
        self.ledger.checkpoint(item_id, token, {"plan": self.PLAN})
        self.assertIn("handoff", self.ledger.checkpoint_error(item_id))  # a plan-only save does not repair it
        with self.assertRaisesRegex(LedgerError, "repair the rejected checkpoint handoff"):
            self.ledger.await_input(item_id, token, "配置发布后请回复。", reason="waiting")

    def test_a_successor_reads_the_nearest_predecessor_plan_from_recovery(self):
        item_id, token = self.running()
        self.ledger.checkpoint(item_id, token, {"plan": self.PLAN})
        self.ledger.cancel(item_id, "Stop")
        successor = self.ledger.retry(item_id, "continue the feature")["id"]
        recovery = self.ledger.issue_context(successor)["recovery"]
        self.assertEqual((recovery["predecessor_id"], recovery["plan"]), (item_id, self.PLAN))
        self.assertIsNone(self.ledger.issue_context(successor)["plan"])  # recall to verify, not its own plan yet
        self.ledger.cancel(successor, "stopped before its first checkpoint")
        third = self.ledger.retry(successor, "continue again")["id"]
        recovery = self.ledger.issue_context(third)["recovery"]
        self.assertEqual((recovery["predecessor_id"], recovery["plan"]), (successor, self.PLAN))

    @staticmethod
    def serialized(value):
        return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))  # as ledger._json

    @classmethod
    def plan_of(cls, size):
        """A plan of exactly `size` serialized characters, every string at most 2,000 characters."""
        plan = {"closing": {}}
        index = 0
        while cls.serialized(plan) < size:
            plan["closing"][f"{index:02d}"] = ""
            plan["closing"][f"{index:02d}"] = "x" * min(2000, size - cls.serialized(plan))
            index += 1
        assert cls.serialized(plan) == size, cls.serialized(plan)
        return plan

    def test_the_bounds_hold_exactly_at_their_edges(self):
        item_id, token = self.running()
        full = self.plan_of(16000)
        self.assertEqual(self.ledger.checkpoint(item_id, token, {"plan": full})["checkpoint"]["plan"], full)
        with self.assertRaisesRegex(LedgerError, "16000"):
            self.ledger.checkpoint(item_id, token, {"plan": self.plan_of(16001)})
        self.assertEqual(self.ledger.issue_context(item_id)["plan"], full)
        edge = {"config": {"k" * 2000: 1}}
        self.assertEqual(self.ledger.checkpoint(item_id, token, {"plan": edge})["checkpoint"]["plan"], edge)

    def test_recovery_keeps_the_predecessor_plan_after_the_successor_saves_its_own(self):
        item_id, token = self.running()
        self.ledger.checkpoint(item_id, token, {"plan": self.PLAN})
        self.ledger.cancel(item_id, "Stop")
        successor = self.ledger.retry(item_id, "continue")["id"]
        self.ledger.set_worker(successor, 4322, "test")
        token = self.ledger.claim(successor, worker_id="w2")["token"]
        own = {"stages": {"A": "done", "B": "pending"}}
        self.ledger.checkpoint(successor, token, {"plan": own})
        context = self.ledger.issue_context(successor)
        self.assertEqual((context["plan"], context["recovery"]["plan"]), (own, self.PLAN))
        # `{}` is the one way to clear a plan (null is refused), and a cleared plan is what the next successor sees.
        self.ledger.checkpoint(successor, token, {"plan": {}})
        self.ledger.cancel(successor, "Stop")
        third = self.ledger.retry(successor, "again")["id"]
        self.assertEqual(self.ledger.issue_context(third)["recovery"]["plan"], {})

    def test_a_stale_claim_cannot_write_a_plan(self):
        """Claim fencing guards the checkpoint write itself, not only the handoff path (AGENTS.md)."""
        item_id, token = self.running()
        self.ledger.checkpoint(item_id, token, {"plan": self.PLAN})
        self.ledger.await_input(item_id, token, "配置发布后请回复。", reason="waiting")
        self.ledger.resume(item_id, "human replied")
        fresh = self.ledger.claim(item_id, worker_id="w2")["token"]
        with self.assertRaisesRegex(LedgerError, "running claim and matching token required"):
            self.ledger.checkpoint(item_id, token, {"plan": {"stages": {"A": "stale"}}})
        self.now += 61
        with self.assertRaisesRegex(LedgerError, "lease expired"):
            self.ledger.checkpoint(item_id, fresh, {"plan": {"stages": {"A": "late"}}})
        self.assertEqual(self.ledger.issue_context(item_id)["plan"], self.PLAN)

    # Plan P9: an issue entry of plan.prs decides where later attempts' worktrees start (spec §5.7), so the
    # checkpoint refuses one no launch could check out, while the worker can still correct it.
    def test_an_issue_entry_names_this_issues_own_farmbot_branch(self):
        item_id, token = self.running()
        self.ledger.checkpoint(item_id, token, {"plan": self.PLAN})
        stage = self.ledger.item(item_id)["stage"]
        for branch in ("main", "farmbot/farm-2", "farmbot/farm-10", "designer-one/farm-1-harvest", "farmbot/farm-1 x",
                       "farmbot/farm-1-a:b", "farmbot/farm-1-x\n", "farmbot/farm-1-a..b", "farmbot/farm-1-x.lock",
                       "farmbot/farm-1-x/", ["farmbot/farm-1"], None):
            with self.subTest(branch=branch):
                plan = {"prs": {"common": [{"branch": branch, "role": "issue", "head": "b" * 40}]}}
                with self.assertRaisesRegex(LedgerError, r"plan\.prs\.common: an issue entry names this issue's own "
                                                         r"branch, farmbot/farm-1 or farmbot/farm-1-<suffix>"):
                    self.ledger.checkpoint(item_id, token, {"stage": "declarations", "plan": plan})
        self.assertEqual((self.ledger.issue_context(item_id)["plan"], self.ledger.item(item_id)["stage"]),
                         (self.PLAN, stage))  # nothing of a refused checkpoint is saved
        # Any other branch may be recorded, a person's included, under another role or none.
        other = {"prs": {"common": [{"branch": "farmbot/farm-1", "role": "issue", "head": "b" * 40},
                                    {"branch": "designer-one/farm-1-harvest", "head": "c" * 40}]}}
        self.assertEqual(self.ledger.checkpoint(item_id, token, {"plan": other})["checkpoint"]["plan"], other)

    def test_a_repository_has_one_issue_entry(self):
        item_id, token = self.running()
        for second in ("farmbot/farm-1-2", "farmbot/farm-1"):
            with self.subTest(second=second), self.assertRaisesRegex(
                    LedgerError, r"plan\.prs\.common has more than one issue entry"):
                self.ledger.checkpoint(item_id, token, {"plan": {"prs": {"common": [
                    {"branch": "farmbot/farm-1", "role": "issue"}, {"branch": second, "role": "issue"}]}}})
        self.assertIsNone(self.ledger.issue_context(item_id)["plan"])

    def test_an_issue_entry_follows_the_hosts_issue_namespace(self):
        """The worker CLI passes the host's issue_prefix; FARM-1 is outside an FBTEST host's namespace."""
        item_id, token = self.running()
        with self.assertRaisesRegex(LedgerError, r"plan\.prs\.Farm-Contract: .*configured issue namespace"):
            self.ledger.checkpoint(item_id, token, {"plan": self.PLAN}, issue_prefix="FBTEST")
        self.ledger.checkpoint(item_id, token, {"plan": {"stages": {"A": "done"}}}, issue_prefix="FBTEST")

    def test_a_fix_plan_that_records_its_own_branches_is_accepted_as_before(self):
        """A fix records the branch its worktree is on: Linear's suggestion, or the -<job> copy of a successor."""
        item_id, token = self.running()
        plan = {"prs": {"Farm-Client": [{"branch": "farmbot/farm-1-harvest-duplicates-rewards", "role": "issue",
                                         "head": "a" * 40,
                                         "url": "https://github.com/Kuaiwa-Network/Farm-Client/pull/7"}],
                        "farm-hive": [{"branch": f"farmbot/farm-1-{item_id}", "role": "issue", "head": "b" * 40}]}}
        self.assertEqual(self.ledger.checkpoint(item_id, token, {"plan": plan})["checkpoint"]["plan"], plan)
        self.assertEqual(self.ledger.recorded_branches(item_id),
                         {"Farm-Client": "farmbot/farm-1-harvest-duplicates-rewards",
                          "farm-hive": f"farmbot/farm-1-{item_id}"})

    def test_a_feature_plan_records_one_issue_branch_per_repository_beside_its_suffix_branches(self):
        item = self.new_item(skill="feature", target=None)
        self.ledger.set_worker(item["id"], 4321, "test")
        token = self.ledger.claim(item["id"], worker_id="w")["token"]

        def entry(branch, role, head):
            return {"branch": branch, "role": role, "head": head * 40, "pr": None}
        plan = {"stages": {"A": "done", "B": "done", "C": "done", "D": "done", "G": "pending"},
                "prs": {"Farm-Contract": [entry("farmbot/farm-1", "issue", "a"),
                                          entry("farmbot/farm-1-waivers", "waivers", "b")],
                        "common": [entry("farmbot/farm-1", "issue", "c"), entry("farmbot/farm-1-config", "config", "d")],
                        "farm-hive": [entry("farmbot/farm-1", "issue", "e"),
                                      entry("farmbot/farm-1-followup", "followup", "f")]}}
        self.assertEqual(self.ledger.checkpoint(item["id"], token, {"plan": plan})["checkpoint"]["plan"], plan)
        self.assertEqual(self.ledger.recorded_branches(item["id"]),
                         dict.fromkeys(("Farm-Contract", "common", "farm-hive"), "farmbot/farm-1"))

    def test_recorded_branches_are_the_issue_entries_of_the_nearest_plan(self):
        """Spec §5.7 "Re-attachment": the scheduler reads only these; any other plan content is the worker's own."""
        item_id, token = self.running()
        self.assertEqual(self.ledger.recorded_branches(item_id), {})
        plan = {"prs": {"Farm-Contract": [{"branch": "farmbot/farm-1-waivers", "role": "waivers"},
                                          {"branch": "farmbot/farm-1", "role": "issue", "head": "a" * 40, "pr": None}],
                        "common": ["farmbot/farm-1", {"branch": "farmbot/farm-1", "role": "config"}],
                        "farm-hive": {"branch": "farmbot/farm-1", "role": "issue"}}}
        self.ledger.checkpoint(item_id, token, {"plan": plan})
        self.assertEqual(self.ledger.recorded_branches(item_id), {"Farm-Contract": "farmbot/farm-1"})
        self.ledger.cancel(item_id, "Stop")
        successor = self.ledger.retry(item_id, "continue")["id"]
        self.assertEqual(self.ledger.recorded_branches(successor), {"Farm-Contract": "farmbot/farm-1"})
        self.ledger.set_worker(successor, 4322, "test")
        token = self.ledger.claim(successor, worker_id="w2")["token"]
        self.ledger.checkpoint(successor, token, {"plan": {"stages": {"A": "done"}}})
        self.assertEqual(self.ledger.recorded_branches(successor), {})  # its own plan, once it saved one

    def test_a_plan_written_around_the_checkpoint_is_checked_again_when_read(self):
        """The ledger file sits in a directory every worker can write, so the scheduler's read applies P9's rules
        again."""
        item_id, _ = self.running()
        for plan, error in (({"prs": {"common": [{"branch": "main", "role": "issue"}]}}, "farmbot/farm-1 or"),
                            ({"prs": {"common": [{"branch": "farmbot/farm-1", "role": "issue"},
                                                 {"branch": "farmbot/farm-1-2", "role": "issue"}]}},
                             "more than one issue entry")):
            with self.subTest(error=error):
                self.ledger.connection.execute("UPDATE work_items SET checkpoint=? WHERE id=?",
                                               (json.dumps({"plan": plan}), item_id))
                with self.assertRaisesRegex(LedgerError, error):
                    self.ledger.recorded_branches(item_id)


class NoticeTests(LedgerBase):
    def running(self):
        item = self.new_item()
        return item["id"], self.ledger.claim(item["id"], worker_id="w")["token"]

    def test_a_notice_is_recorded_once_per_request_id(self):
        item_id, token = self.running()
        body = f"请确认：\n1. 初始值是多少？ [farmbot:{'0' * 64}]"
        first = self.ledger.prepare_notice(item_id, token, "question", "questions-1", body)
        self.assertRegex(first["marker"], r"^\[farmbot:[0-9a-f]{64}\]$")
        self.assertEqual(MARKER.findall(first["body"]), [first["marker"]])  # a copied marker is stripped
        self.assertTrue(first["body"].endswith("\n\n" + first["marker"]))
        self.assertEqual((first["item_id"], first["issue_id"], first["kind"], first["remote_id"]),
                         (item_id, ISSUE, "question", None))
        self.now += 5
        self.assertEqual(self.ledger.prepare_notice(item_id, token, "question", "questions-1", body), first)
        for kind, other in (("question", "换了措辞。"), ("waiting", body)):
            with self.subTest(kind=kind), self.assertRaisesRegex(LedgerError, "different notice"):
                self.ledger.prepare_notice(item_id, token, kind, "questions-1", other)
        second = self.ledger.prepare_notice(item_id, token, "question", "questions-2", "还有一个问题。")
        self.assertNotEqual(second["marker"], first["marker"])
        self.assertEqual([n["request_id"] for n in self.ledger.notices(item_id)], ["questions-1", "questions-2"])

    def test_kinds_request_ids_and_bodies_are_validated(self):
        item_id, token = self.running()
        for kind, request_id, body in (("greeting", "r1", "x"), ("question", "", "x"), ("question", "a" * 65, "x"),
                                       ("question", "has space", "x"), ("question", "问题-1", "x"),
                                       ("question", "r1", "   "), ("question", "r1", f"[farmbot:{'0' * 64}]")):
            with self.subTest(kind=kind, request_id=request_id, body=body), self.assertRaises(LedgerError):
                self.ledger.prepare_notice(item_id, token, kind, request_id, body)
        self.assertEqual(self.ledger.notices(item_id), [])
        longest = "A-z.0_9-" + "x" * 56
        self.assertEqual(self.ledger.prepare_notice(item_id, token, "foreign_work", longest, "发现他人分支。")["request_id"],
                         longest)

    def test_a_retried_attempt_gets_the_same_notice_back(self):
        """The outbox key moves with the claimed input and generation; a notice's does not."""
        item_id, token = self.running()
        first = self.ledger.prepare_notice(item_id, token, "waiting", "config-ready", "等待策划确认配置。")
        self.ledger.confirm_notice(item_id, "config-ready", "remote-notice-1")
        blocker = self.ledger.prepare_comment(item_id, token, "blocker", "暂停。")
        self.ledger.confirm_comment(blocker["action_id"], "remote-blocker-1")
        self.ledger.observe_issue(issue(comments=[comment("配置好了")]))
        requeued = self.ledger.finish(item_id, token, "blocked", {"summary": "x", "comment_action_id": blocker["action_id"]})
        self.assertEqual(requeued["state"], "queued")
        token = self.ledger.claim(item_id, worker_id="w2")["token"]
        again = self.ledger.prepare_notice(item_id, token, "waiting", "config-ready", "等待策划确认配置。")
        self.assertEqual((again["marker"], again["remote_id"]), (first["marker"], "remote-notice-1"))

    def test_notice_bodies_never_change_the_issue_fingerprint(self):
        item_id, token = self.running()
        claimed = self.ledger.observe_issue(issue())["fingerprint"]
        notice = self.ledger.prepare_notice(item_id, token, "question", "questions-1", "请确认初始值。")
        # Linear reports FarmBot's own comments as bot comments; the body match is the second guard, as for the outbox.
        echoed = issue(comments=[comment(notice["body"], kind="human", id="notice-comment")])
        self.assertEqual(self.ledger.observe_issue(echoed)["fingerprint"], claimed)
        self.assertNotEqual(self.ledger.observe_issue(issue(comments=[comment("人工回复")]))["fingerprint"], claimed)

    def test_a_notice_echo_does_not_refuse_a_late_pr_registration(self):
        item_id, token = self.running()
        notice = self.ledger.prepare_notice(item_id, token, "question", "questions-1", "请确认初始值。")
        url = "https://github.com/Kuaiwa-Network/Farm-Client/pull/7"
        self.ledger.observe_issue(issue(attachments=[url], comments=[comment(notice["body"], kind="human")]))
        view = self.ledger.checkpoint(item_id, token, {"published_prs": [url]}, verified_prs=[url])
        self.assertEqual(self.ledger.issue_context(view["id"])["published_prs"], [url])

    def test_notices_require_the_live_claim_and_an_open_issue(self):
        item_id, token = self.running()
        with self.assertRaisesRegex(LedgerError, "running claim"):
            self.ledger.prepare_notice(item_id, "claim_wrong", "question", "q-1", "x")
        self.ledger.observe_issue(issue(status_type="completed"))
        with self.assertRaisesRegex(LedgerError, "left scope"):
            self.ledger.prepare_notice(item_id, token, "question", "q-1", "x")
        self.ledger.observe_issue(issue())
        self.now += 61
        with self.assertRaisesRegex(LedgerError, "lease expired"):
            self.ledger.prepare_notice(item_id, token, "question", "q-1", "x")
        self.assertEqual(self.ledger.notices(item_id), [])

    def test_confirm_notice_is_idempotent_and_refuses_another_remote_id(self):
        item_id, token = self.running()
        self.ledger.prepare_notice(item_id, token, "waiting", "ui-ready", "等待 UI。")
        with self.assertRaisesRegex(LedgerError, "unknown notice"):
            self.ledger.confirm_notice(item_id, "no-such-request", "r1")
        self.assertEqual(self.ledger.confirm_notice(item_id, "ui-ready", "r1")["remote_id"], "r1")
        self.assertEqual(self.ledger.confirm_notice(item_id, "ui-ready", "r1")["remote_id"], "r1")
        with self.assertRaisesRegex(LedgerError, "different remote id"):
            self.ledger.confirm_notice(item_id, "ui-ready", "r2")
        self.assertEqual([(n["request_id"], n["kind"], n["remote_id"]) for n in self.ledger.issue_context(item_id)["notices"]],
                         [("ui-ready", "waiting", "r1")])


class NoticeReconciliationTests(LedgerBase):
    """post-notice against live comments, and what a successor of cancelled work sees of earlier rounds."""

    class LiveIssue:
        """A Linear stand-in: fetch_issue returns the issue with every comment created so far."""

        def __init__(self, comments=(), **fields):
            self.value = issue(comments=list(comments), **fields)
            self.created = []

        def fetch_issue(self, ref):
            return json.loads(json.dumps(self.value))

        def create_comment(self, issue_id, body):
            self.created.append(body)
            remote_id = f"c-{len(self.created)}"
            self.value["comments"].append(comment(body, kind="bot", id=remote_id))
            return remote_id

    def running(self):
        item = self.new_item()
        return item["id"], self.ledger.claim(item["id"], worker_id="w")["token"]

    def post(self, live, item_id, request_id, token):
        from agent.__main__ import owned_notice, post_notice
        return post_notice(self.ledger, live, owned_notice(self.ledger, item_id, request_id, token))

    def test_post_notice_reconciles_by_marker_even_when_linear_renders_the_body_differently(self):
        item_id, token = self.running()
        notice = self.ledger.prepare_notice(item_id, token, "question", "questions-1", "请确认：\n1. 初始值是多少？")
        live = self.LiveIssue([comment("请确认：\n\n1.  初始值是多少？\n\n" + notice["marker"], kind="bot", id="c-rendered")])
        posted = self.post(live, item_id, "questions-1", token)
        self.assertEqual((posted["remote_id"], live.created), ("c-rendered", []))

    def test_a_later_item_on_the_issue_posts_its_own_round_under_a_reused_request_id(self):
        first, token = self.running()
        earlier = self.ledger.prepare_notice(first, token, "question", "questions-1", "第一份工作的问题。")
        live = self.LiveIssue()
        self.post(live, first, "questions-1", token)
        blocker = self.ledger.prepare_comment(first, token, "blocker", "暂停。")
        self.ledger.confirm_comment(blocker["action_id"], "remote-blocker")
        self.assertEqual(self.ledger.finish(first, token, "blocked",
                                            {"summary": "x", "comment_action_id": blocker["action_id"]})["state"], "blocked")
        self.ledger.ensure_session("session-2", ISSUE, delegation=True)
        second = self.ledger.create_work_item(issue_id=ISSUE, session_id="session-2", skill="fix")["id"]
        token = self.ledger.claim(second, worker_id="w2")["token"]
        later = self.ledger.prepare_notice(second, token, "question", "questions-1", "第二份工作的问题。")
        self.assertNotEqual(later["marker"], earlier["marker"])
        self.post(live, second, "questions-1", token)
        self.assertEqual(len(live.created), 2)
        self.assertEqual([(n["request_id"], n["remote_id"]) for n in self.ledger.issue_context(second)["notices"]],
                         [("questions-1", "c-2")])

    def test_every_copied_marker_is_stripped_nested_ones_included(self):
        item_id, token = self.running()
        copied = [f"[farmbot:{'1' * 64}]", f"[farmbot:{'2' * 64}]"]
        notice = self.ledger.prepare_notice(item_id, token, "question", "two", f"甲 {copied[0]}\n乙 {copied[1]}")
        self.assertEqual(MARKER.findall(notice["body"]), [notice["marker"]])
        nested = self.ledger.prepare_notice(item_id, token, "question", "nested", f"问题 [farmbot:[farmbot:{'1' * 64}]{'2' * 64}]")
        self.assertEqual(MARKER.findall(nested["body"]), [nested["marker"]])
        self.assertTrue(nested["body"].startswith("问题\n\n"))

    def test_a_successor_of_cancelled_work_sees_its_predecessors_rounds_not_its_own(self):
        """A retried cancelled job is a fresh item with no notices; the rounds posted before it come back under
        recovery, nearest predecessor first, so that none of them is posted again."""
        item_id, token = self.running()
        self.ledger.prepare_notice(item_id, token, "question", "questions-1", "第一轮问题。")
        self.ledger.confirm_notice(item_id, "questions-1", "c-1")
        self.ledger.cancel(item_id, "stopped by the operator")
        successor = self.ledger.retry(item_id, "continue")["id"]
        context = self.ledger.issue_context(successor)
        self.assertEqual(context["notices"], [])
        self.assertEqual([(n["item_id"], n["request_id"], n["kind"], n["remote_id"]) for n in context["recovery"]["notices"]],
                         [(item_id, "questions-1", "question", "c-1")])
        token = self.ledger.claim(successor, worker_id="w2")["token"]
        self.ledger.prepare_notice(successor, token, "question", "questions-2", "第二轮问题。")
        self.ledger.cancel(successor, "stopped again")
        third = self.ledger.retry(successor, "continue again")["id"]
        self.assertEqual([(n["item_id"], n["request_id"], n["remote_id"]) for n in self.ledger.issue_context(third)["recovery"]["notices"]],
                         [(successor, "questions-2", None), (item_id, "questions-1", "c-1")])

    def test_post_notice_creates_nothing_on_an_issue_that_left_scope(self):
        item_id, token = self.running()
        self.ledger.prepare_notice(item_id, token, "question", "questions-1", "请确认。")
        live = self.LiveIssue(status_type="completed")
        with self.assertRaisesRegex(LedgerError, "left scope"):
            self.post(live, item_id, "questions-1", token)
        self.assertEqual(live.created, [])


class SecondItemOnOneIssueTests(LedgerBase):
    """The live rehearsal's Finding 2: `agent.service enqueue` on an issue that already carried a work item.

    `one_active_item_per_issue` means the second item always starts after the first is terminal, and on an
    issue nothing has changed on it claims the same fingerprint at the same generation — which is the whole
    outbox key, so the two items collide on one outbox row.
    """

    FIRST_BODY = "缺少客户端导出产物，需要发布决策。"

    def blocked_first_item(self, body=FIRST_BODY):
        """A delegated run that went the whole way: a started marker, a blocker comment, then blocked."""
        item = self.new_item()
        token = self.ledger.claim(item["id"], worker_id="w1")["token"]
        started = self.ledger.prepare_comment(item["id"], token, "started", "👀 FarmBot 已开始处理。")
        self.ledger.confirm_comment(started["action_id"], "remote-0")
        action = self.ledger.prepare_comment(item["id"], token, "blocker", body)
        self.ledger.confirm_comment(action["action_id"], "remote-1")
        self.ledger.finish(item["id"], token, "blocked", {"summary": "阻塞", "comment_action_id": action["action_id"]})
        return item["id"], action["action_id"]

    PR = "https://github.com/o/r/pull/9"

    def delivered_first_item(self, pr=PR):
        """A delegated run that delivered, with the PR named in the comment the issue actually carries."""
        item = self.new_item()
        token = self.ledger.claim(item["id"], worker_id="w1")["token"]
        action = self.ledger.prepare_comment(item["id"], token, "delivery", f"已修复，见 {pr}")
        self.ledger.confirm_comment(action["action_id"], "remote-1")
        self.ledger.finish(item["id"], token, "delivered",
                           {"summary": "交付", "comment_action_id": action["action_id"],
                            "verification": "dotnet test", "prs": [pr]})
        return item["id"], action["action_id"]

    def second_item(self):
        item = self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill="fix", target=PIN)
        return item["id"], self.ledger.claim(item["id"], worker_id="w2")["token"]

    def audit_details(self, item_id, kind):
        return [json.loads(row["details"]) for row in self.ledger.connection.execute(
            "SELECT details FROM audit WHERE item_id=? AND kind=? ORDER BY id", (item_id, kind))]

    def test_a_second_item_reaching_the_same_verdict_finishes_instead_of_stalling(self):
        first, first_action = self.blocked_first_item()
        second, token = self.second_item()
        action = self.ledger.prepare_comment(second, token, "blocker", self.FIRST_BODY)
        self.assertEqual(action["action_id"], first_action)
        self.assertTrue(action["deduplicated"])
        self.assertEqual(action["item_id"], first)
        self.assertEqual(len(self.ledger.connection.execute(
            "SELECT action_id FROM outbox WHERE issue_id=? AND kind='blocker'", (ISSUE,)).fetchall()), 1)
        view = self.ledger.finish(second, token, "blocked", {"summary": "同一结论", "comment_action_id": first_action})
        self.assertEqual(view["state"], "blocked")
        self.assertEqual(self.ledger.item(first)["state"], "blocked")
        # the substitution is recorded against the item that made it, not left to be inferred
        cited = self.audit_details(second, "borrowed_comment")
        self.assertEqual(len(cited), 1)
        self.assertEqual(cited[0], {"action_id": first_action, "prepared_by": first})

    def test_a_suppressed_second_conclusion_is_kept_in_the_audit_trail(self):
        """`kind` is only 'started', 'blocker' or 'delivery', so two items can reach the same kind with
        materially different text. The issue must not collect both, but the ledger must not lose the second."""
        first, _ = self.blocked_first_item()
        second, token = self.second_item()
        self.ledger.prepare_comment(second, token, "blocker", "无法复现，需要设备日志。")
        details = self.audit_details(second, "deduplicated")
        self.assertEqual(len(details), 1)
        self.assertEqual(details[0]["prepared_by"], first)
        self.assertIn("无法复现", details[0]["suppressed_body"])

    def test_a_started_marker_and_an_identical_conclusion_keep_no_suppressed_body(self):
        self.blocked_first_item()
        second, token = self.second_item()
        self.ledger.prepare_comment(second, token, "started", "👀 第二个工作项开始处理。")
        self.ledger.prepare_comment(second, token, "blocker", self.FIRST_BODY)
        details = self.audit_details(second, "deduplicated")
        self.assertEqual(len(details), 2)
        self.assertNotIn("suppressed_body", details[0])  # a started marker states no conclusion
        self.assertNotIn("suppressed_body", details[1])  # and this blocker says what the first one said

    def test_an_unposted_row_from_a_dead_item_is_handed_to_the_item_that_can_still_post_it(self):
        item = self.new_item()
        token = self.ledger.claim(item["id"], worker_id="w1")["token"]
        stranded = self.ledger.prepare_comment(item["id"], token, "blocker", "第一份措辞。")
        self.ledger.fail(item["id"], "worker exited without finishing")
        second, second_token = self.second_item()
        action = self.ledger.prepare_comment(second, second_token, "blocker", "第二份措辞。")
        self.assertEqual(action["action_id"], stranded["action_id"])
        self.assertFalse(action["deduplicated"])
        self.assertEqual(action["item_id"], second)
        self.assertIn("第二份措辞。", action["body"])
        self.assertEqual(self.ledger.outbox(item["id"]), [])
        self.ledger.confirm_comment(action["action_id"], "remote-2")
        view = self.ledger.finish(second, second_token, "blocked",
                                  {"summary": "完成", "comment_action_id": action["action_id"]})
        self.assertEqual(view["state"], "blocked")

    def test_a_delivery_borrow_refuses_a_pr_the_posted_comment_never_named(self):
        """The borrowed comment is the only thing a human reads. A `prs` array it does not mention would
        put a pull request in the ledger that was announced nowhere — the shape of Finding 1, one door along.
        """
        _, first_action = self.delivered_first_item()
        second, token = self.second_item()
        action = self.ledger.prepare_comment(second, token, "delivery", "我也修复了，见另一个 PR。")
        self.assertTrue(action["deduplicated"])
        with self.assertRaises(LedgerError) as caught:
            self.ledger.finish(second, token, "delivered",
                               {"summary": "另一个 PR", "comment_action_id": first_action,
                                "verification": "dotnet test", "prs": ["https://github.com/o/r/pull/10"]})
        self.assertIn("pull/10", str(caught.exception))
        self.assertEqual(self.ledger.item(second)["state"], "running")

    def test_a_delivery_borrow_accepts_the_pr_the_posted_comment_does_name(self):
        _, first_action = self.delivered_first_item()
        second, token = self.second_item()
        self.ledger.prepare_comment(second, token, "delivery", "同一个 PR。")
        view = self.ledger.finish(second, token, "delivered",
                                  {"summary": "同一个 PR", "comment_action_id": first_action,
                                   "verification": "dotnet test", "prs": [self.PR]})
        self.assertEqual(view["state"], "delivered")

    def test_a_delivery_borrow_is_not_fooled_by_a_pr_number_that_is_only_a_prefix(self):
        _, first_action = self.delivered_first_item(pr="https://github.com/o/r/pull/99")
        second, token = self.second_item()
        self.ledger.prepare_comment(second, token, "delivery", "看起来像。")
        with self.assertRaises(LedgerError):
            self.ledger.finish(second, token, "delivered",
                               {"summary": "前缀", "comment_action_id": first_action,
                                "verification": "dotnet test", "prs": ["https://github.com/o/r/pull/9"]})

    def test_a_no_change_delivery_borrows_cleanly_because_it_carries_no_pr(self):
        _, first_action = self.delivered_first_item()
        second, token = self.second_item()
        self.ledger.prepare_comment(second, token, "delivery", "主干已修复，无需改动。")
        view = self.ledger.finish(second, token, "delivered",
                                  {"summary": "无需改动", "comment_action_id": first_action,
                                   "verification": "对比主干", "no_change": "已由第一个工作项交付", "prs": []})
        self.assertEqual(view["state"], "delivered")

    def test_an_operator_reader_shows_which_items_borrowed_a_comment_and_what_they_had_said(self):
        """`audit` had exactly one writer and no readers at all, so a borrowed conclusion was recorded
        where nothing would ever show it."""
        first, _ = self.blocked_first_item()
        second, token = self.second_item()
        self.ledger.prepare_comment(second, token, "started", "👀 第二个工作项开始处理。")
        self.ledger.prepare_comment(second, token, "blocker", "无法复现，需要设备日志。")
        borrowed = self.ledger.borrowed_comments()
        self.assertEqual(len(borrowed), 1)  # a deduplicated started marker states no conclusion
        self.assertEqual(borrowed[0]["item_id"], second)
        self.assertEqual(borrowed[0]["prepared_by"], first)
        self.assertEqual((borrowed[0]["identifier"], borrowed[0]["kind"]), ("FARM-1", "blocker"))
        self.assertIn("无法复现", borrowed[0]["suppressed_body"])
        # the scheduler calls status() twice a second; this scan stays out of it
        self.assertNotIn("borrowed_comments", self.ledger.status())

    def test_finish_still_refuses_a_confirmed_comment_from_another_issue(self):
        """Two issues can share a fingerprint — it is hashed from title, description, attachments and
        comments, never from the issue id — so dropping the item check without adding an issue check would
        let one issue's comment close another issue's work item."""
        _, first_action = self.blocked_first_item()
        self.ledger.observe_issue(issue(id=OTHER, identifier="FARM-2", url="https://linear.app/x/issue/FARM-2"))
        self.ledger.ensure_session("session-2", OTHER, delegation=True)
        other = self.ledger.create_work_item(issue_id=OTHER, session_id="session-2", skill="fix", target=PIN)
        token = self.ledger.claim(other["id"], worker_id="w3")["token"]
        with self.assertRaises(LedgerError):
            self.ledger.finish(other["id"], token, "blocked", {"summary": "借用", "comment_action_id": first_action})

    def test_finish_still_refuses_a_confirmed_comment_from_an_earlier_generation(self):
        first, first_action = self.blocked_first_item()
        self.ledger.retry(first, "operator 重试")
        token = self.ledger.claim(first, worker_id="w4")["token"]
        self.assertEqual(self.ledger.item(first)["generation"], 1)
        with self.assertRaises(LedgerError):
            self.ledger.finish(first, token, "blocked", {"summary": "旧世代", "comment_action_id": first_action})

    def test_finish_still_refuses_a_comment_of_the_wrong_kind_or_one_never_posted(self):
        item = self.new_item()
        token = self.ledger.claim(item["id"], worker_id="w1")["token"]
        unposted = self.ledger.prepare_comment(item["id"], token, "blocker", "未发出。")
        with self.assertRaises(LedgerError):
            self.ledger.finish(item["id"], token, "blocked",
                               {"summary": "未确认", "comment_action_id": unposted["action_id"]})
        self.ledger.confirm_comment(unposted["action_id"], "remote-9")
        with self.assertRaises(LedgerError):
            self.ledger.finish(item["id"], token, "delivered",
                               {"summary": "错误种类", "comment_action_id": unposted["action_id"],
                                "verification": "dotnet test", "no_change": "无需改动", "prs": []})


class ReservationTests(unittest.TestCase):
    def test_requested_fix_commit_does_not_overwrite_baseline(self):
        item = self.item(ISSUE, "a" * 40)
        self.ledger.connection.execute("UPDATE work_items SET root_repo='Farm-Client' WHERE id=?", (item["id"],))
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.ledger.await_resource(item["id"], token, "unity_slot", "batch", commit_sha="b" * 40)
        self.assertEqual(self.ledger.item(item["id"])["target"]["commit_sha"], "a" * 40)
        reservation = self.ledger.acquire("unity_slot", owner="pool", host="mac")
        self.assertEqual(reservation["commit_sha"], "b" * 40)

    def test_invalid_verification_commit_leaves_claim_and_queue_unchanged(self):
        item = self.item(ISSUE, "a" * 40)
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        for sha in ("HEAD", "", "b" * 39, 123):
            with self.subTest(sha=sha), self.assertRaises(LedgerError):
                self.ledger.await_resource(item["id"], token, "unity_slot", "batch", commit_sha=sha)
        self.assertEqual(self.ledger.item(item["id"])["state"], "running")
        self.assertEqual(self.ledger.reservations(), [])

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

    def test_a_failing_probe_holds_the_slot_until_the_controller_recovers_it(self):
        self.waiting(ISSUE, "a" * 40, "batch")
        granted = self.ledger.acquire("unity_slot", owner="pool", host="mac")
        self.ledger.hold(granted["reservation_id"], "quiescence probe failed")
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "held")
        self.waiting(OTHER, "b" * 40, "batch")
        self.assertIsNone(self.ledger.acquire("unity_slot", owner="pool", host="mac"))
        with self.assertRaisesRegex(LedgerError, 'automatic recovery'):
            self.ledger.recover_slot("unity_slot:1", "operator closed Unity by hand")
        from agent.resource_recovery import RecoveryStore
        store = RecoveryStore(self.ledger)
        recovery = store.begin(store.pending('mac')[0]['id'])
        store.detach(recovery['id'], recovery['attempts'])
        store.complete(recovery['id'], recovery['attempts'], 'a' * 40, 'verified-instance')
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "idle_open")
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
