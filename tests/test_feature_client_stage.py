"""Controller-selected client baseline, independent of a worker's checkpoint prose."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from agent.ledger import LedgerError
from agent.launcher import Finished
import test_scheduler as fixtures


CLIENT = "Farm-Client"
BRANCH = "farmbot/farm-1"
BASE = "b" * 40


class FeatureClientStageTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SchedulerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        reference = tempfile.TemporaryDirectory(prefix="configured reference ")
        self.addCleanup(reference.cleanup)
        self.fixture.slot_entry = {**self.fixture.slot_entry, "folder": str(Path(reference.name).resolve())}
        self.fixture.scheduler.slot_entries = {self.fixture.slot_entry["id"]: self.fixture.slot_entry}
        self.ledger = self.fixture.ledger
        self.scheduler = self.fixture.scheduler
        self.trees = self.fixture.trees
        self.skill = self.fixture.use_feature_skill(
            writes=["Farm-Contract", "common", "farm-hive", CLIENT],
            resources=["unity_slot"], reads=["Farm-Contract", CLIENT, "farmgui"])
        self.ledger.observe_issue(fixtures.issue())
        self.ledger.ensure_session(fixtures.SESSION, fixtures.ISSUE, delegation=True)
        self.item = self.ledger.create_work_item(issue_id=fixtures.ISSUE, session_id=fixtures.SESSION,
                                                 skill="feature")
        self.trees.stage_base = Mock(return_value=BASE)
        self.trees.add_stage = Mock(side_effect=lambda repo, item_id, branch, baseline, **kw:
                                    self.trees.add(repo, item_id, branch))

    def at_client(self):
        # The actual retired-worker handoff is exercised below; this creates the queued state for boundary tests.
        self.ledger.connection.execute("UPDATE work_items SET root_repo=? WHERE id=?", (CLIENT, self.item["id"]))
        return self.ledger.item(self.item["id"])

    def pin(self, item=None, branch=BRANCH, **changes):
        item = item or self.ledger.item(self.item["id"])
        target = {**fixtures.PIN, "commit_sha": BASE, **changes}
        return self.ledger.pin_feature_client(item["id"], item["generation"], target, branch=branch)

    def payload(self):
        return json.loads(self.fixture.launcher.spawned[-1][1].split("\n\n", 1)[1])

    def test_server_stages_never_prepare_client_or_pin_session(self):
        for root in (None, "common", "farm-hive"):
            self.ledger.connection.execute("UPDATE work_items SET root_repo=? WHERE id=?", (root, self.item["id"]))
            paths = self.scheduler._worktrees_for(self.skill, self.ledger.item(self.item["id"]),
                                                   self.ledger.issue(fixtures.ISSUE))
            self.assertEqual(set(paths), {"Farm-Contract", "common", "farm-hive"})
        self.trees.stage_base.assert_not_called()
        self.trees.add_stage.assert_not_called()
        self.assertIsNone(self.ledger.item(self.item["id"])["target"])
        self.assertIsNone(self.ledger.session(fixtures.SESSION)["target"])

    def test_client_handoff_pins_then_launches_only_client_write_scope(self):
        self.scheduler.tick()
        token = self.ledger.claim(self.item["id"], worker_id="contract")["token"]
        self.ledger.checkpoint(self.item["id"], token, {"handoff": {
            "facts": [], "hypotheses": [], "checks": [], "repositories": [], "next_actions": ["Implement client"]}})
        self.ledger.handoff_repository(self.item["id"], token, CLIENT, skill=self.skill)
        self.assertIsNone(self.ledger.item(self.item["id"])["target"])
        self.assertEqual(self.scheduler.tick()["launched"], 0)
        self.fixture.launcher.finished.append(Finished(self.item["id"], 0, "", True, "stopped", None, 101))
        self.assertEqual(self.scheduler.tick()["launched"], 1)
        target = self.ledger.item(self.item["id"])["target"]
        self.assertEqual((target["commit_sha"], target["issue_branch"]), (BASE, BRANCH))
        self.assertIsNone(self.ledger.session(fixtures.SESSION)["target"])
        self.assertEqual(self.payload()["target"], target)
        self.assertEqual(self.payload()["stage"]["write_repositories"], [CLIENT])
        self.assertNotIn("unity", self.payload().get("tools", {}))
        self.assertIsNone(self.payload()["resource"])
        self.assertEqual(self.payload()["tools"]["client_typecheck"],
                         {"reference_checkout": self.fixture.slot_entry["folder"]})
        reference = Path(self.fixture.slot_entry["folder"])
        self.assertFalse(any(reference.is_relative_to(Path(p).resolve())
                             for p in self.fixture.launcher.spawn_writable))

    def test_missing_reference_configuration_is_a_tool_gap_without_reserving_unity(self):
        item = self.at_client()
        self.scheduler.slot_entries = {}
        self.scheduler.launch(item)
        self.assertEqual(self.payload()["tools"]["client_typecheck"]["status"], "unavailable")
        self.assertFalse(self.ledger.reservations())
        self.assertIsNone(self.payload()["resource"])

    def test_reference_overlapping_worker_write_roots_is_not_reported_as_readonly(self):
        item = self.at_client()
        self.scheduler.slot_entries = {"slot": {"repo": CLIENT, "folder": str(self.trees.root)}}
        self.scheduler.launch(item)
        self.assertEqual(self.payload()["tools"]["client_typecheck"]["status"], "unavailable")
        self.assertNotIn("reference_checkout", self.payload()["tools"]["client_typecheck"])
        self.assertFalse(self.ledger.reservations())

    def test_baseline_uses_main_at_client_entry_and_never_reselects_on_retry(self):
        self.trees.stage_base.return_value = "c" * 40
        item = self.at_client()
        self.scheduler._worktrees_for(self.skill, item, self.ledger.issue(fixtures.ISSUE))
        first = self.ledger.item(item["id"])["target"]
        self.trees.stage_base.return_value = "d" * 40
        self.scheduler._worktrees_for(self.skill, self.ledger.item(item["id"]), self.ledger.issue(fixtures.ISSUE))
        self.assertEqual(first["commit_sha"], "c" * 40)
        self.assertEqual(self.ledger.item(item["id"])["target"], first)
        self.trees.stage_base.assert_called_once_with(CLIENT, item["id"], BRANCH)
        self.assertEqual(self.trees.add_stage.call_args.args, (CLIENT, item["id"], BRANCH, "c" * 40))

    def test_pin_is_refused_before_handoff_or_with_stale_generation(self):
        with self.assertRaises(LedgerError):
            self.pin()
        item = self.at_client()
        with self.assertRaises(LedgerError):
            self.ledger.pin_feature_client(item["id"], item["generation"]+1,
                                           {**fixtures.PIN, "commit_sha": BASE}, branch=BRANCH)
        self.assertIsNone(self.ledger.item(item["id"])["target"])

    def test_cancelled_successor_keeps_controller_baseline_and_bounded_client_evidence(self):
        item = self.at_client()
        self.pin(item)
        token = self.ledger.claim(item["id"], worker_id="client")["token"]
        plan = {"client": {"verification": {"phase": "requesting", "head": "c" * 40}},
                "prs": {CLIENT: [{"role": "issue", "branch": BRANCH}]}}
        self.ledger.checkpoint(item["id"], token, {"plan": plan})
        with self.assertRaisesRegex(LedgerError, "2,000|2000"):
            self.ledger.checkpoint(item["id"], token, {"plan": {"client": {"log": "x" * 2001}}})
        self.ledger.cancel(item["id"], "operator Stop")
        successor = self.ledger.retry(item["id"], "operator resumed")
        self.assertEqual(successor["target"], self.ledger.item(item["id"])["target"])
        self.assertEqual(self.ledger.issue_context(successor["id"])["recovery"]["plan"], plan)
        self.assertEqual(self.ledger.feature_client_branch(successor["id"]), BRANCH)
        self.assertIsNone(self.ledger.session(fixtures.SESSION)["target"])

    def test_cancel_during_git_selection_cannot_pin_or_create_client(self):
        item = self.at_client()
        def cancelled(*args):
            self.ledger.cancel(item["id"], "cancel during fetch")
            return BASE
        self.trees.stage_base.side_effect = cancelled
        with self.assertRaises(LedgerError):
            self.scheduler._worktrees_for(self.skill, item, self.ledger.issue(fixtures.ISSUE))
        self.assertIsNone(self.ledger.item(item["id"])["target"])
        self.trees.add_stage.assert_not_called()
        self.assertFalse(self.fixture.launcher.spawned)

    def test_immutable_pin_refuses_another_baseline_or_branch(self):
        item = self.at_client()
        target = self.pin(item)["target"]
        self.assertEqual(self.pin(item)["target"], target)
        for changes in ({"commit_sha": "c"*40}, {"repository": "farm-hive"}):
            with self.subTest(changes=changes), self.assertRaises(LedgerError):
                self.pin(item, **changes)
        for branch in ("farmbot/farm-2", BRANCH+"-config", "main", BRANCH+"/../bad"):
            with self.subTest(branch=branch), self.assertRaises(LedgerError):
                self.pin(item, branch=branch)

    def test_worker_recorded_client_branch_cannot_replace_controller_pin(self):
        item = self.at_client()
        self.pin(item)
        self.ledger.connection.execute("UPDATE work_items SET checkpoint=? WHERE id=?", (
            json.dumps({"plan": {"prs": {CLIENT: [{"role": "issue", "branch": BRANCH+"-other"}]}}}), item["id"]))
        with self.assertRaises(LedgerError):
            self.scheduler._worktrees_for(self.skill, self.ledger.item(item["id"]), self.ledger.issue(fixtures.ISSUE))
        self.trees.add_stage.assert_not_called()

    def test_claimed_client_cannot_repin_controller_baseline(self):
        self.at_client()
        self.pin()
        self.ledger.set_worker(self.item["id"], 101, "h", 60)
        self.ledger.claim(self.item["id"], worker_id="client")
        with self.assertRaises(LedgerError):
            self.pin()

    def test_feature_unity_requires_explicit_owned_committed_revision(self):
        self.at_client()
        self.pin()
        self.ledger.set_worker(self.item["id"], 101, "h", 60)
        token = self.ledger.claim(self.item["id"], worker_id="client")["token"]
        self.ledger.checkpoint(self.item["id"], token, {"summary": "Committed client changes"})
        with self.assertRaisesRegex(LedgerError, "explicit.*commit"):
            self.ledger.await_resource(self.item["id"], token, "unity_slot", "batch", skill=self.skill)
        tested = "e" * 40
        self.ledger.await_resource(self.item["id"], token, "unity_slot", "batch", skill=self.skill, commit_sha=tested)
        queued = [r for r in self.ledger.reservations() if r["item_id"] == self.item["id"]]
        self.assertEqual(len(queued), 1)
        self.assertEqual((queued[0]["state"], queued[0]["commit_sha"]), ("queued", tested))
        self.assertEqual(self.ledger.item(self.item["id"])["target"]["commit_sha"], BASE)

    def test_old_feature_target_cannot_grant_client_verification(self):
        self.at_client()
        self.ledger.connection.execute("UPDATE work_items SET target_json=? WHERE id=?",
                                       (json.dumps(fixtures.PIN), self.item["id"]))
        self.ledger.set_worker(self.item["id"], 101, "h", 60)
        token = self.ledger.claim(self.item["id"], worker_id="client")["token"]
        self.ledger.checkpoint(self.item["id"], token, {"summary": "Legacy target"})
        with self.assertRaises(LedgerError):
            self.ledger.await_resource(self.item["id"], token, "unity_slot", "batch", skill=self.skill, commit_sha=BASE)

    def test_resource_revalidates_branch_namespace_and_reserved_roles(self):
        self.at_client()
        target = self.pin()["target"]
        self.ledger.set_worker(self.item["id"], 101, "h", 60)
        token = self.ledger.claim(self.item["id"], worker_id="client")["token"]
        self.ledger.checkpoint(self.item["id"], token, {"summary": "Committed client changes"})
        for branch in ("main", "farmbot/farm-2", BRANCH+"-config", BRANCH+"-waivers", BRANCH+"-writeback"):
            self.ledger.connection.execute("UPDATE work_items SET target_json=? WHERE id=?",
                                           (json.dumps({**target, "issue_branch": branch}), self.item["id"]))
            with self.subTest(branch=branch), self.assertRaises(LedgerError):
                self.ledger.await_resource(self.item["id"], token, "unity_slot", "batch", skill=self.skill, commit_sha=BASE)
        self.assertFalse(self.ledger.reservations())


if __name__ == "__main__":
    unittest.main()
