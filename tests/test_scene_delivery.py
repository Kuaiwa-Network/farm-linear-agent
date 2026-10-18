"""Real managed Git and scripted GitHub metadata bind scene task outcomes."""
import copy
import json
import unittest
from unittest.mock import Mock, patch

from agent.__main__ import parser, run
from agent.ledger import LedgerError
from agent.publication import PublicationError
from agent.scene_delivery import verify_delivery
from agent.workflow import delegate_task
from agent.worktrees import WorktreeError
import test_cli as cli_fixtures
from test_ledger import OTHER, PIN
from test_worktrees import git


class SceneDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture = cli_fixtures.CliTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        args, self.ledger, self.config, self.api, self.github = fixture.publication_fixture()
        self.api.create_activity = Mock(return_value="progress")
        self.parent, self.parent_token = args.item, args.token
        self.client = self.config.local_root / "worktrees" / self.parent / "Farm-Client"
        # Keep the existing owned clone/branch; convert its root to a general job.
        self.ledger.connection.execute("UPDATE work_items SET skill='work',root_repo='farm-hive',target_json=NULL WHERE id=?",
                                       (self.parent,))
        self.ledger.checkpoint(self.parent, self.parent_token, {"plan": {"stages": {"A": "done"}}})
        task = delegate_task(self.ledger, self.parent, self.parent_token, {
            "key": "scene", "capability": "scene", "instructions": "Use the nursery contract.",
            'scope': {'assets': ['Assets/nursery.prefab'], 'validation': 'source'}})
        self.task = task["id"]
        baseline = git("rev-parse", "HEAD", cwd=self.client)
        self.ledger.pin_staged_client(self.task, task["generation"], {**PIN, "commit_sha": baseline}, branch="farmbot/farm-1")
        self.ledger.pin_task_client_base(self.task, task["generation"], baseline)
        self.ledger.connection.execute("UPDATE work_items SET root_repo='Farm-Client' WHERE id=?", (self.task,))
        self.token = self.ledger.claim(self.task, worker_id="scene")["token"]
        self.url = "https://github.com/Kuaiwa-Network/Farm-Client/pull/1331"
        self.draft = {"html_url": self.url, "state": "open", "draft": True,
                      "head": {"ref": "farmbot/farm-1", "sha": baseline,
                               "repo": {"full_name": "Kuaiwa-Network/Farm-Client"}},
                      "base": {"ref": "main", "repo": {"full_name": "Kuaiwa-Network/Farm-Client"}}}
        self.evidence = {"summary": "Scene ready", "verification": "Checked nursery scene", "prs": [self.url]}

    def changed(self):
        (self.client / 'Assets').mkdir(exist_ok=True)
        (self.client / "Assets/nursery.prefab").write_text("Nursery presentation", encoding="utf-8")
        (self.client / 'Assets/nursery.prefab.meta').write_text('fileFormatVersion: 2\nguid: ' + 'e' * 32 + '\n', encoding='utf-8')
        git("add", "Assets/nursery.prefab", 'Assets/nursery.prefab.meta', cwd=self.client)
        git("commit", "-qm", "Nursery presentation", cwd=self.client)
        self.draft["head"]["sha"] = git("rev-parse", "HEAD", cwd=self.client)

    def metadata(self, endpoint, **options):
        if "/pulls/" in endpoint:
            return copy.deepcopy(self.draft)
        return self.github(endpoint, **options)

    def proof(self, evidence=None):
        with patch("agent.publication.github_api", side_effect=self.metadata):
            return verify_delivery(self.ledger, self.task, self.token, self.config, lambda: self.api,
                                   self.evidence if evidence is None else evidence)

    def test_exact_owned_draft_and_clean_head_certify_cli_delivery(self):
        self.changed()
        path = self.fixture.root / "scene-finish.json"
        path.write_text(json.dumps(self.evidence), encoding="utf-8")
        args = parser().parse_args(["--db", str(self.fixture.db), "finish", "--item", self.task,
                                   "--token", self.token, "--outcome", "delivered", "--input", str(path)])
        with patch("agent.__main__.load_config", return_value=self.config), \
                patch("agent.publication.github_api", side_effect=self.metadata):
            result = run(args, self.ledger, lambda: self.api)
        self.assertEqual(result["state"], "delivered")
        self.assertTrue(result["evidence"]["scene_delivery_id"])

    def test_unrelated_or_stale_or_nondraft_pr_is_refused(self):
        self.changed()
        for changed in ({"ref": "someone-elses-branch"}, {"sha": "f" * 40}):
            original = copy.deepcopy(self.draft)
            self.draft["head"].update(changed)
            with self.subTest(changed=changed), self.assertRaisesRegex(PublicationError, "exact repository, branch and HEAD"):
                self.proof()
            self.draft = original
        self.draft["draft"] = False
        with self.assertRaises(PublicationError):
            self.proof()
        self.assertEqual(self.ledger.item(self.task)["state"], "running")

    def test_dirty_checkout_cannot_be_delivered_or_called_no_change(self):
        (self.client / "untracked.prefab").write_text("Uncommitted", encoding="utf-8")
        for evidence in (self.evidence, {"no_change": "Already correct", "prs": []}):
            with self.subTest(evidence=evidence), self.assertRaises(WorktreeError):
                self.proof(evidence)

    def test_no_change_requires_no_delta_from_original_task_entry(self):
        evidence = {"no_change": "Already correct", "prs": None}
        self.assertEqual(self.proof(evidence)["prs"], [])
        self.changed()
        with self.assertRaisesRegex(LedgerError, "no changes from its original entry"):
            self.proof(evidence)

    def test_changed_issue_or_claim_cannot_certify_old_output(self):
        self.changed()
        original = self.api.fetch_issue
        changed = original(None)
        changed["description"] = "New requirement"
        self.api.fetch_issue = lambda _: changed
        with self.assertRaisesRegex(LedgerError, "changed issue inputs"):
            self.proof()

    def test_fresh_withdrawal_blocks_delivery_before_recording_proof(self):
        changed = self.api.fetch_issue(None)
        changed["delegate_id"] = OTHER
        self.api.fetch_issue = lambda _: changed
        with self.assertRaisesRegex(LedgerError, "current open delegated issue"):
            self.proof({"no_change": "Already correct", "prs": []})
        self.assertEqual(self.ledger.connection.execute(
            "SELECT COUNT(*) FROM audit WHERE item_id=? AND kind='scene_delivery_verified'", (self.task,)).fetchone()[0], 0)

    def test_old_scene_proof_cannot_be_reused_after_generation_or_pr_change(self):
        self.changed()
        proof = self.proof()
        evidence = {**self.evidence, "scene_delivery_id": proof["delivery_id"]}
        evidence["prs"] = ["https://github.com/Kuaiwa-Network/Farm-Client/pull/1332"]
        with self.assertRaisesRegex(LedgerError, "current controller verification"):
            self.ledger.finish(self.task, self.token, "delivered", evidence)
        evidence["prs"] = self.evidence["prs"]
        self.ledger.connection.execute("UPDATE work_items SET generation=generation+1 WHERE id=?", (self.task,))
        with self.assertRaisesRegex(LedgerError, "current controller verification"):
            self.ledger.finish(self.task, self.token, "delivered", evidence)
