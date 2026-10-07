"""Export proof crosses a real controller handoff into a disposable owned Client."""
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from PIL import Image

from agent.fgui_client import assert_installed, install, installation_record
from agent.fgui_install import apply_install
from agent.fgui_workflow import UiWorkflow
from agent.ledger import LedgerError
from agent.worktrees import Worktrees
import test_fgui_install as installer_fixtures
import test_fgui_workflow as workflow_fixtures


class UiClientWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.source = source = workflow_fixtures.UiWorkflowTests(); source.setUp(); self.addCleanup(source.doCleanups)
        self.ledger, self.paths, self.item = source.ledger, source.paths, source.item
        review = source.review(); source.approve(review); self.exported = source.export(review)
        source.plan["ui"]["receipt_id"] = self.exported["receipt_id"]; source.save_plan()
        fixture = installer_fixtures.FguiInstallTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        origin = source.root / "Client origin"; shutil.copytree(fixture.client, origin)
        if getattr(self, "new_package", False):
            shutil.rmtree(origin / "Assets/GameRes/FairyRes/One")
            (origin / "Assets/GameRes/FairyRes/One.meta").unlink()
        source.git(origin, "init", "-q", "-b", "main"); source.commit(origin)
        self.baseline = source.git(origin, "rev-parse", "HEAD"); source.config.repos["Farm-Client"] = str(origin)
        source.trees = Worktrees(self.paths.repos, self.paths.worktrees, source.config.repos)
        self.ledger.set_worker(self.item, 101, "fixture")
        self.ledger.checkpoint(self.item, source.token, {"plan": source.plan, "handoff": {"facts": [], "hypotheses": [],
            "checks": [], "repositories": [], "next_actions": ["Install certified UI export"]}})
        self.ledger.handoff_repository(self.item, source.token, "Farm-Client", skill=source.skill)
        self.ledger.complete_repository_handoff(self.item, 101, skill=source.skill)
        row = self.ledger.item(self.item)
        self.ledger.pin_staged_client(self.item, row["generation"], {"repository": "Farm-Client", "requested_ref": "main",
            "commit_sha": self.baseline, "server_environment": "fixture", "selected_at": "2026-10-07T00:00:00Z"}, branch=source.branch)
        self.client = source.trees.add_stage("Farm-Client", self.item, source.branch, self.baseline)
        source.token = self.ledger.claim(self.item, worker_id="ui-client-fixture")["token"]
        self.workflow = UiWorkflow(self.ledger, self.item, source.token, source.config, source.skill,
                                   lambda: source.api, stage="Farm-Client")

    def installed(self):
        return install(self.workflow, self.exported["receipt_id"])

    def handoff(self, root, pid):
        source = self.source
        self.ledger.set_worker(self.item, pid, "fixture")
        self.ledger.checkpoint(self.item, source.token, {"plan": source.plan, "handoff": {"facts": [], "hypotheses": [],
            "checks": [], "repositories": [], "next_actions": ["Continue the reviewed UI round"]}})
        self.ledger.handoff_repository(self.item, source.token, root, skill=source.skill)
        self.ledger.complete_repository_handoff(self.item, pid, skill=source.skill)
        source.token = self.ledger.claim(self.item, worker_id="ui-correction-fixture")["token"]

    def correction(self):
        self.handoff("farmgui", 102)
        (self.source.path / "assets/One/Panel.xml").write_text('<component size="4,2"/>', encoding="utf-8")
        self.source.commit(self.source.path)
        self.source.head = self.source.git(self.source.path, "rev-parse", "HEAD")
        self.source.plan["prs"]["farmgui"][0]["head"] = self.source.head; self.source.save_plan()
        review = self.source.review(2); self.source.approve(review); self.source.export_color = "red"
        self.exported = self.source.export(review)
        self.source.plan["ui"]["receipt_id"] = self.exported["receipt_id"]; self.source.save_plan()
        self.handoff("Farm-Client", 103)
        self.workflow = UiWorkflow(self.ledger, self.item, self.source.token, self.source.config, self.source.skill,
                                   lambda: self.source.api, stage="Farm-Client")

    def test_new_visual_round_updates_certified_client_without_repinning_baseline(self):
        first = self.installed(); self.source.commit(self.client)
        first_head = self.source.git(self.client, "rev-parse", "HEAD")
        self.correction(); second = self.installed()
        self.assertEqual(second["initial_head"], first_head)
        self.assertEqual(second["baseline"], self.baseline)
        self.assertNotEqual(first["receipt_id"], second["receipt_id"])
        self.assertNotEqual(first["files"], second["files"])
        self.assertEqual(len(list((self.paths.runs / self.item / "ui-installations").glob("*/journal.json"))), 2)
        self.source.commit(self.client)
        from agent.fgui_scope import verify_candidate
        self.assertEqual(verify_candidate(self.workflow, second["receipt_id"])["baseline"], self.baseline)

    def test_correction_cannot_build_on_uncertified_gameplay_commit(self):
        self.installed(); (self.client / "Gameplay.cs").write_text("foreign gameplay change", encoding="utf-8")
        self.source.commit(self.client); self.correction()
        with patch("agent.fgui_client.apply_install") as apply, self.assertRaisesRegex(LedgerError, "outside certified"):
            self.installed()
        apply.assert_not_called()

    def test_new_package_without_common_is_refused_before_any_install(self):
        fixture = UiClientWorkflowTests(); fixture.new_package = True; fixture.setUp(); self.addCleanup(fixture.doCleanups)
        with patch("agent.fgui_client.apply_install") as apply, self.assertRaisesRegex(LedgerError, "required Common"):
            fixture.installed()
        apply.assert_not_called()

    def test_controller_handoff_preserves_export_and_changed_only_install(self):
        other = self.client / "Assets/GameRes/FairyRes/Template"
        before = {p.relative_to(other).as_posix(): p.read_bytes() for p in other.rglob("*") if p.is_file()}
        proof = self.installed()
        self.assertEqual(proof["packages"], ["One"])
        self.assertEqual(proof["baseline"], self.baseline)
        self.assertEqual(proof["source_head"], self.source.head)
        self.assertEqual({p.relative_to(other).as_posix(): p.read_bytes() for p in other.rglob("*") if p.is_file()}, before)
        self.assertFalse((self.client / "Assets/GameRes/FairyRes/One/One_old.mp3").exists())
        self.assertEqual(self.installed(), proof)
        self.assertEqual(len(list((self.paths.runs / self.item / "ui-installations").glob("*/journal.json"))), 1)

    def test_dirty_first_client_is_refused_before_install(self):
        (self.client / "unrelated.txt").write_text("unrelated worker change", encoding="utf-8")
        with patch("agent.fgui_client.apply_install") as apply, self.assertRaisesRegex(LedgerError, "clean owned Client"):
            self.installed()
        apply.assert_not_called()
        self.assertIsNone(installation_record(self.ledger, self.item, self.exported["receipt_id"]))

    def test_another_branch_cannot_replace_controller_client_pin(self):
        self.source.git(self.client, "switch", "-qc", "foreign")
        with patch("agent.fgui_client.apply_install") as apply, self.assertRaisesRegex(LedgerError, "exact owned Client"):
            self.installed()
        apply.assert_not_called()

    def test_new_main_pin_cannot_be_substituted_after_handoff(self):
        row = self.ledger.item(self.item)
        self.assertEqual(row["target"]["commit_sha"], self.baseline)
        self.source.git(self.client, "commit", "--allow-empty", "-qm", "unapproved baseline")
        with self.assertRaisesRegex(LedgerError, "pinned baseline"): self.installed()

    def test_valid_but_changed_staging_image_refuses_before_client_mutation(self):
        stage = self.paths.runs / self.item / "ui-exports" / self.exported["receipt_id"] / "staging"
        Image.new("RGBA", (2, 2), "red").save(stage / "One_atlas0.png")
        with patch("agent.fgui_client.apply_install") as apply, self.assertRaisesRegex(LedgerError, "immutable controller export"):
            self.installed()
        apply.assert_not_called()

    def test_stop_between_writes_retains_interrupted_journal_and_no_success_proof(self):
        def interrupted(plan, recovery, *, fence):
            def stop_after_mutation():
                journal = recovery / "journal.json"
                if journal.exists() and json.loads(journal.read_text(encoding="utf-8"))["completed"]:
                    self.ledger.cancel(self.item, "Stop between writes")
                fence()
            return apply_install(plan, recovery, fence=stop_after_mutation)
        with patch("agent.fgui_client.apply_install", side_effect=interrupted), self.assertRaises(LedgerError):
            self.installed()
        journals = list((self.paths.runs / self.item / "ui-installations").glob("*/journal.json"))
        self.assertEqual(len(journals), 1)
        saved = json.loads(journals[0].read_text(encoding="utf-8"))
        self.assertEqual(saved["state"], "interrupted"); self.assertTrue(saved["completed"])
        self.assertTrue(any(p.name.isdigit() for p in journals[0].parent.iterdir()))
        self.assertIsNone(installation_record(self.ledger, self.item, self.exported["receipt_id"]))

    def test_recovery_checks_the_actual_installed_bytes_and_guid_identity(self):
        proof = self.installed()
        path = self.client / "Assets/GameRes/FairyRes/One/One_atlas0.png"
        Image.new("RGBA", (2, 2), "red").save(path)
        with self.assertRaisesRegex(LedgerError, "controller-certified"): assert_installed(self.workflow, proof)
        self.assertIsNotNone(installation_record(self.ledger, self.item, self.exported["receipt_id"]))

    def test_source_correction_after_export_refuses_client_install(self):
        (self.source.path / "assets/One/Panel.xml").write_text('<component size="5,2"/>', encoding="utf-8")
        with patch("agent.fgui_client.apply_install") as apply, self.assertRaisesRegex(LedgerError, "uncommitted"):
            self.installed()
        apply.assert_not_called()
