"""Prepared UI Client entry uses the existing controller-owned staged baseline."""
from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import Mock
import unittest

from agent.ledger import LedgerError
from agent.launcher import Finished
from agent.worktrees import Worktrees
import test_scheduler as fixtures
from test_worktrees import git


CLIENT = "Farm-Client"
BRANCH = "farmbot/farm-1"
BASE = "b" * 40


class UiClientStageTests(unittest.TestCase):
    def setUp(self):
        fixture = self.fixture = fixtures.SchedulerTests()
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        self.ledger, self.scheduler, self.trees = fixture.ledger, fixture.scheduler, fixture.trees
        # Explicitly scoped staged fixture; controller pinning is tested without
        # invoking a licensed publisher, real Client repository or Unity Editor.
        self.skill = replace(fixtures.SKILLS["fgui"], writes=("farmgui", CLIENT), resources=("unity_slot",))
        self.scheduler.skills = {**fixtures.SKILLS, "fgui": self.skill}
        self.scheduler.enabled_skills.add("fgui")
        self.ledger.observe_issue(fixtures.issue())
        self.ledger.ensure_session(fixtures.SESSION, fixtures.ISSUE, delegation=True)
        self.item = self.ledger.create_work_item(issue_id=fixtures.ISSUE, session_id=fixtures.SESSION, skill="fgui")
        self.trees.stage_base = Mock(return_value=BASE)
        self.trees.add_stage = Mock(side_effect=lambda repo, item, branch, baseline, **kw: self.trees.add(repo, item, branch))

    def at_client(self):
        self.ledger.connection.execute("UPDATE work_items SET root_repo=? WHERE id=?", (CLIENT, self.item["id"]))
        return self.ledger.item(self.item["id"])

    def pin(self, **changes):
        item = self.ledger.item(self.item["id"])
        return self.ledger.pin_staged_client(item["id"], item["generation"], {**fixtures.PIN, "commit_sha": BASE, **changes}, branch=BRANCH)

    def claim(self):
        self.ledger.set_worker(self.item["id"], 101, "h", 60)
        token = self.ledger.claim(self.item["id"], worker_id="ui-client")["token"]
        self.ledger.checkpoint(self.item["id"], token, {"summary": "UI export verification"})
        return token

    def test_authoring_never_prepares_client_or_pins_session(self):
        paths = self.scheduler._worktrees_for(self.skill, self.item, self.ledger.issue(fixtures.ISSUE))
        self.assertEqual(set(paths), {"farmgui"})
        self.trees.stage_base.assert_not_called()
        self.trees.add_stage.assert_not_called()
        self.assertIsNone(self.ledger.item(self.item["id"])["target"])
        self.assertIsNone(self.ledger.session(fixtures.SESSION)["target"])

    def test_retired_authoring_handoff_pins_only_on_client_entry(self):
        self.scheduler.tick()
        token = self.ledger.claim(self.item["id"], worker_id="ui-author")["token"]
        self.ledger.checkpoint(self.item["id"], token, {"handoff": {"facts": [], "hypotheses": [],
            "checks": [], "repositories": [], "next_actions": ["Install verified UI export"]}})
        self.ledger.handoff_repository(self.item["id"], token, CLIENT, skill=self.skill)
        self.assertIsNone(self.ledger.item(self.item["id"])["target"])
        self.assertEqual(self.scheduler.tick()["launched"], 0)
        self.fixture.launcher.finished.append(Finished(self.item["id"], 0, "", True, "stopped", None, 101))
        self.assertEqual(self.scheduler.tick()["launched"], 1)
        target = self.ledger.item(self.item["id"])["target"]
        self.assertEqual((target["commit_sha"], target["issue_branch"]), (BASE, BRANCH))
        payload = json.loads(self.fixture.launcher.spawned[-1][1].split("\n\n", 1)[1])
        self.assertEqual(payload["stage"]["write_repositories"], [CLIENT])
        self.assertIsNone(payload["resource"])
        self.assertNotIn("client_typecheck", payload["tools"])
        self.assertIsNone(self.ledger.session(fixtures.SESSION)["target"])

    def test_latest_main_is_selected_once_and_retry_retains_it(self):
        item = self.at_client()
        self.scheduler._worktrees_for(self.skill, item, self.ledger.issue(fixtures.ISSUE))
        first = self.ledger.item(item["id"])["target"]
        self.trees.stage_base.return_value = "c" * 40
        self.scheduler._worktrees_for(self.skill, self.ledger.item(item["id"]), self.ledger.issue(fixtures.ISSUE))
        self.assertEqual(self.ledger.item(item["id"])["target"], first)
        self.trees.stage_base.assert_called_once()

    def test_fresh_ui_branch_selects_corrected_main_without_touching_retained_job(self):
        root = Path(self.fixture.tmp.name) / "two UI runs 农场"
        remotes = {}
        for name in ("farmgui", CLIENT):
            origin = root / (name + " origin")
            origin.mkdir(parents=True)
            git("init", "-q", "-b", "main", cwd=origin)
            (origin / "README.md").write_text("original baseline\n", encoding="utf-8")
            git("add", ".", cwd=origin); git("commit", "-qm", "baseline", cwd=origin)
            remotes[name] = str(origin)
        trees = Worktrees(root / "repos", root / "worktrees", remotes)
        self.scheduler.worktrees = trees
        retained_source = trees.add("farmgui", "retained-ui", BRANCH)
        old_baseline = trees.stage_base(CLIENT, "retained-ui", BRANCH)
        retained_client = trees.add_stage(CLIENT, "retained-ui", BRANCH, old_baseline)
        source_head = trees.head(retained_source)
        client_origin = Path(remotes[CLIENT])
        (client_origin / "guard-fix.txt").write_text("reviewed baseline correction\n", encoding="utf-8")
        git("add", ".", cwd=client_origin); git("commit", "-qm", "baseline correction", cwd=client_origin)
        corrected = trees.head(client_origin)

        authored = self.scheduler._worktrees_for(self.skill, self.item, self.ledger.issue(fixtures.ISSUE))
        fresh_branch = git("branch", "--show-current", cwd=authored["farmgui"])
        self.assertNotEqual(fresh_branch, BRANCH)
        token = self.claim()
        self.ledger.checkpoint(self.item["id"], token, {"plan": {"prs": {"farmgui": [
            {"role": "issue", "branch": fresh_branch}]}}, "handoff": {"facts": [], "hypotheses": [],
            "checks": [], "repositories": [], "next_actions": ["Install certified UI into fresh Client"]}})
        self.ledger.handoff_repository(self.item["id"], token, CLIENT, skill=self.skill)
        self.ledger.complete_repository_handoff(self.item["id"], 101, skill=self.skill)
        paths = self.scheduler._worktrees_for(self.skill, self.ledger.item(self.item["id"]),
                                             self.ledger.issue(fixtures.ISSUE))
        target = self.ledger.item(self.item["id"])["target"]
        self.assertEqual((target["commit_sha"], target["issue_branch"]), (corrected, fresh_branch))
        self.assertEqual(trees.head(paths[CLIENT]), corrected)
        self.assertEqual(git("branch", "--show-current", cwd=paths[CLIENT]), fresh_branch)
        self.assertIsNone(self.ledger.session(fixtures.SESSION)["target"])

        (client_origin / "later.txt").write_text("main advances again\n", encoding="utf-8")
        git("add", ".", cwd=client_origin); git("commit", "-qm", "advance again", cwd=client_origin)
        again = self.scheduler._worktrees_for(self.skill, self.ledger.item(self.item["id"]),
                                             self.ledger.issue(fixtures.ISSUE))
        self.assertEqual(self.ledger.item(self.item["id"])["target"], target)
        self.assertEqual(trees.head(again[CLIENT]), corrected)
        self.assertEqual(trees.head(retained_client), old_baseline)
        self.assertEqual(trees.head(retained_source), source_head)
        self.assertEqual(git("branch", "--show-current", cwd=retained_client), BRANCH)

    def test_stop_during_selection_cannot_pin_or_create_client(self):
        item = self.at_client()
        def stopped(*args):
            self.ledger.cancel(item["id"], "Stop during trusted Git selection")
            return BASE
        self.trees.stage_base.side_effect = stopped
        with self.assertRaises(LedgerError):
            self.scheduler._worktrees_for(self.skill, item, self.ledger.issue(fixtures.ISSUE))
        self.assertIsNone(self.ledger.item(item["id"])["target"])
        self.trees.add_stage.assert_not_called()

    def test_retained_client_pin_wins_over_different_source_branch(self):
        self.at_client()
        target = self.pin()["target"]
        self.ledger.connection.execute("UPDATE work_items SET checkpoint=? WHERE id=?", (json.dumps({"plan": {
            "prs": {"farmgui": [{"role": "issue", "branch": BRANCH + "-fresh"}]}}}), self.item["id"]))
        self.scheduler._worktrees_for(self.skill, self.ledger.item(self.item["id"]), self.ledger.issue(fixtures.ISSUE))
        self.trees.stage_base.assert_not_called()
        self.trees.add_stage.assert_called_once_with(CLIENT, self.item["id"], BRANCH, BASE)
        self.assertEqual(self.ledger.item(self.item["id"])["target"], target)

    def test_foreign_or_reserved_source_branch_refuses_first_client_selection(self):
        self.at_client()
        for branch in ("main", "farmbot/farm-2", BRANCH + "-config", BRANCH + "-writeback"):
            self.ledger.connection.execute("UPDATE work_items SET checkpoint=? WHERE id=?", (json.dumps({"plan": {
                "prs": {"farmgui": [{"role": "issue", "branch": branch}]}}}), self.item["id"]))
            with self.subTest(branch=branch), self.assertRaises(LedgerError):
                self.scheduler._worktrees_for(self.skill, self.ledger.item(self.item["id"]), self.ledger.issue(fixtures.ISSUE))
        self.trees.stage_base.assert_not_called()
        self.trees.add_stage.assert_not_called()
        self.assertIsNone(self.ledger.item(self.item["id"])["target"])

    def test_source_stage_and_claimed_client_cannot_repin(self):
        with self.assertRaises(LedgerError):
            self.pin()
        self.at_client(); self.pin(); self.claim()
        with self.assertRaises(LedgerError):
            self.pin(commit_sha="c" * 40)

    def test_worker_checkpoint_branch_cannot_replace_controller_pin(self):
        self.at_client(); self.pin()
        self.ledger.connection.execute("UPDATE work_items SET checkpoint=? WHERE id=?", (json.dumps({"plan": {
            "prs": {CLIENT: [{"role": "issue", "branch": BRANCH + "-other"}]}}}), self.item["id"]))
        with self.assertRaisesRegex(LedgerError, "controller-selected"):
            self.scheduler._worktrees_for(self.skill, self.ledger.item(self.item["id"]), self.ledger.issue(fixtures.ISSUE))

    def test_ui_unity_requires_explicit_committed_head_and_pinned_branch(self):
        self.at_client(); baseline = self.pin()["target"]; token = self.claim()
        with self.assertRaisesRegex(LedgerError, "explicit committed"):
            self.ledger.await_resource(self.item["id"], token, "unity_slot", "batch", skill=self.skill)
        self.assertFalse(self.ledger.reservations())
        commit = "c" * 40
        result = self.ledger.await_resource(self.item["id"], token, "unity_slot", "batch", skill=self.skill, commit_sha=commit)
        self.assertEqual(result["target"], baseline)
        self.assertEqual(self.ledger.reservations()[0]["commit_sha"], commit)

    def test_legacy_target_or_foreign_reserved_branch_cannot_reserve(self):
        self.at_client(); token = self.claim()
        for branch in (None, "main", "farmbot/farm-2", BRANCH + "-config", BRANCH + "-writeback"):
            target = {**fixtures.PIN, **({"issue_branch": branch} if branch else {})}
            self.ledger.connection.execute("UPDATE work_items SET target_json=? WHERE id=?", (json.dumps(target), self.item["id"]))
            with self.subTest(branch=branch), self.assertRaises(LedgerError):
                self.ledger.await_resource(self.item["id"], token, "unity_slot", "batch", skill=self.skill, commit_sha=BASE)
        self.assertFalse(self.ledger.reservations())

    def test_cancelled_successor_retains_controller_baseline_and_branch(self):
        self.at_client(); target = self.pin()["target"]
        self.ledger.cancel(self.item["id"], "Stop")
        successor = self.ledger.retry(self.item["id"], "Explicit continuation")
        self.assertEqual(successor["target"], target)
        self.assertEqual(self.ledger.staged_client_branch(successor["id"]), BRANCH)
        self.assertIsNone(self.ledger.session(fixtures.SESSION)["target"])

    def test_old_code_only_entry_points_do_not_accept_ui(self):
        self.at_client()
        with self.assertRaisesRegex(LedgerError, "feature item"):
            self.ledger.pin_feature_client(self.item["id"], self.item["generation"], fixtures.PIN, branch=BRANCH)
        with self.assertRaisesRegex(LedgerError, "feature item"):
            self.ledger.feature_client_branch(self.item["id"])


if __name__ == "__main__":
    unittest.main()
