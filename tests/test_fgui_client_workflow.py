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
        internal = getattr(self, "internal", False)
        if internal:
            self.parent = source.make_general_task()["id"]
        review = source.review(); source.approve(review); self.exported = source.export(review)
        source.plan["ui"]["receipt_id"] = self.exported["receipt_id"]; source.save_plan()
        fixture = installer_fixtures.FguiInstallTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        origin = source.root / "Client origin"; shutil.copytree(fixture.client, origin)
        prepare = getattr(self, "prepare_baseline", None)
        if prepare:
            prepare(origin)
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
        owner = self.parent if internal else self.item
        self.client = source.trees.add_stage("Farm-Client", owner, source.branch, self.baseline)
        if internal:
            # An earlier delivered scene contributes legitimate Client files.
            # The UI scope must exclude those while retaining the job baseline.
            (self.client / "scene.prefab").write_text("Earlier scene output", encoding="utf-8")
            source.commit(self.client)
            entry_head = source.git(self.client, "rev-parse", "HEAD")
            self.ledger.pin_task_client_base(self.item, row["generation"], entry_head)
        source.token = self.ledger.claim(self.item, worker_id="ui-client-fixture")["token"]
        self.workflow = UiWorkflow(self.ledger, self.item, source.token, source.config, source.skill,
                                   lambda: source.api, stage="Farm-Client")

    def installed(self):
        return install(self.workflow, self.exported["receipt_id"])

    def interrupt_certification(self):
        retained = self.workflow.retained_export
        calls = 0

        def unavailable(receipt):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise LedgerError("UI source Git read is unavailable or timed out")
            return retained(receipt)

        with patch.object(self.workflow, "retained_export", side_effect=unavailable):
            with self.assertRaisesRegex(LedgerError, "timed out"):
                self.installed()
        self.assertIsNone(installation_record(self.ledger, self.item, self.exported["receipt_id"]))
        prepared = [json.loads(row["details"]) for row in self.ledger.connection.execute(
            "SELECT details FROM audit WHERE kind='fgui_install_prepared'")]
        matches = [p["proof"] for p in prepared if p["proof"]["receipt_id"] == self.exported["receipt_id"]]
        self.assertEqual(len(matches), 1)
        journal = self.paths.runs / self.item / "ui-installations" / matches[0]["installation_id"] / "journal.json"
        self.assertEqual(json.loads(journal.read_text(encoding="utf-8"))["state"], "complete")
        return journal

    def test_failure_before_any_write_can_retry_from_the_unchanged_clean_before_state(self):
        with patch("agent.fgui_client.apply_install", side_effect=OSError("before recovery creation")):
            with self.assertRaises(OSError):
                self.installed()
        self.assertEqual(self.source.git(self.client, "status", "--porcelain"), "")
        proof = self.installed()
        self.assertEqual(proof["receipt_id"], self.exported["receipt_id"])
        self.assertEqual(self.ledger.connection.execute(
            "SELECT COUNT(*) FROM audit WHERE kind='fgui_install_abandoned'").fetchone()[0], 1)

    def test_retry_certifies_completed_writes_after_source_timeout_without_reapplying(self):
        journal = self.interrupt_certification()
        before = {p.name: p.read_bytes() for p in journal.parent.iterdir()}
        with patch("agent.fgui_client.apply_install") as apply:
            proof = self.installed()
        apply.assert_not_called()
        self.assertEqual(proof["receipt_id"], self.exported["receipt_id"])
        self.assertEqual(proof["baseline"], self.baseline)
        self.assertEqual({p.name: p.read_bytes() for p in journal.parent.iterdir()}, before)
        self.assertEqual(self.installed(), proof)
        self.assertEqual(self.ledger.connection.execute(
            "SELECT COUNT(*) FROM audit WHERE kind='fgui_install_complete'").fetchone()[0], 1)

    def test_correction_retry_retains_original_baseline_and_prior_committed_head(self):
        self.installed(); self.source.commit(self.client)
        head = self.source.git(self.client, "rev-parse", "HEAD")
        self.correction(); self.interrupt_certification()
        proof = self.installed()
        self.assertEqual(proof["initial_head"], head)
        self.assertEqual(proof["baseline"], self.baseline)

    def test_internal_task_recovers_with_a_fresh_claim_after_an_attributed_resume(self):
        from test_ledger import DESIGNER
        fixture = UiClientWorkflowTests(); fixture.internal = True; fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.interrupt_certification()
        old_token = fixture.source.token
        fixture.ledger.pop_inbox(fixture.item, old_token)
        fixture.ledger.checkpoint(fixture.item, old_token, {"summary": "complete writes await certification",
            "plan": fixture.source.plan})
        fixture.ledger.await_input(fixture.item, old_token, "retry source check", reason="waiting")
        fixture.ledger.push_inbox(fixture.item, "continue", author=DESIGNER, resume_waiting=True)
        token = fixture.ledger.claim(fixture.item, worker_id="fresh-ui-attempt")["token"]
        self.assertNotEqual(token, old_token)
        fixture.workflow = UiWorkflow(fixture.ledger, fixture.item, token, fixture.source.config,
            fixture.source.skill, lambda: fixture.source.api, stage="Farm-Client")
        with patch("agent.fgui_client.apply_install") as apply:
            proof = fixture.installed()
        apply.assert_not_called()
        self.assertEqual(proof["baseline"], fixture.ledger.item(fixture.item)["task_client_base"])

    def test_recovery_refuses_changed_backup_and_preserves_journal(self):
        journal = self.interrupt_certification()
        saved = journal.read_bytes()
        (journal.parent / "0").write_bytes(b"changed backup")
        with self.assertRaisesRegex(LedgerError, "backup changed"):
            self.installed()
        self.assertEqual(journal.read_bytes(), saved)
        self.assertIsNone(installation_record(self.ledger, self.item, self.exported["receipt_id"]))

    def test_recovery_refuses_journal_that_omits_a_completed_write(self):
        journal = self.interrupt_certification()
        value = json.loads(journal.read_text(encoding="utf-8")); value["completed"].pop()
        journal.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(LedgerError, "exact complete journal"):
            self.installed()

    def test_recovery_refuses_changed_installed_files(self):
        self.interrupt_certification()
        Image.new("RGBA", (2, 2), "red").save(self.client / "Assets/GameRes/FairyRes/One/One_atlas0.png")
        with self.assertRaisesRegex(LedgerError, "controller-certified"):
            self.installed()

    def test_recovery_refuses_unrelated_dirty_work(self):
        self.interrupt_certification()
        (self.client / "Gameplay.cs").write_text("unrelated work", encoding="utf-8")
        with self.assertRaisesRegex(LedgerError, "unrelated Client changes"):
            self.installed()

    def test_recovery_refuses_staged_files_or_changed_head(self):
        self.interrupt_certification()
        self.source.git(self.client, "add", "--all")
        with self.assertRaisesRegex(LedgerError, "unchanged index"):
            self.installed()
        self.source.git(self.client, "commit", "-qm", "uncertified installation")
        with self.assertRaisesRegex(LedgerError, "identity changed"):
            self.installed()

    def test_recovery_requires_unchanged_staging_and_source(self):
        self.interrupt_certification()
        stage = self.paths.runs / self.item / "ui-exports" / self.exported["receipt_id"] / "staging"
        image = stage / "One_atlas0.png"; before = image.read_bytes()
        Image.new("RGBA", (2, 2), "red").save(image)
        with self.assertRaisesRegex(LedgerError, "immutable controller export"):
            self.installed()
        image.write_bytes(before)
        (self.source.path / "assets/One/Panel.xml").write_text('<component size="5,2"/>', encoding="utf-8")
        with self.assertRaisesRegex(LedgerError, "uncommitted"):
            self.installed()

    def test_private_complete_journal_cannot_replace_missing_controller_intent(self):
        self.interrupt_certification()
        self.ledger.connection.execute("DELETE FROM audit WHERE kind='fgui_install_prepared'")
        with self.assertRaisesRegex(LedgerError, "clean owned Client"):
            self.installed()

    def test_recovery_refuses_damaged_prepared_audit(self):
        self.interrupt_certification()
        row = self.ledger.connection.execute("SELECT id,details FROM audit WHERE kind='fgui_install_prepared'").fetchone()
        value = json.loads(row["details"]); value["proof"]["baseline"] = "a" * 40
        self.ledger.connection.execute("UPDATE audit SET details=? WHERE id=?", (json.dumps(value), row["id"]))
        with self.assertRaisesRegex(LedgerError, "damaged prepared"):
            self.installed()

    def test_recovery_rechecks_files_after_final_source_validation(self):
        self.interrupt_certification()
        retained = self.workflow.retained_export
        calls = 0

        def changed_after_check(receipt):
            nonlocal calls
            calls += 1
            result = retained(receipt)
            if calls == 2:
                Image.new("RGBA", (2, 2), "red").save(self.client / "Assets/GameRes/FairyRes/One/One_atlas0.png")
            return result

        with patch.object(self.workflow, "retained_export", side_effect=changed_after_check):
            with self.assertRaisesRegex(LedgerError, "controller-certified"):
                self.installed()
        self.assertIsNone(installation_record(self.ledger, self.item, self.exported["receipt_id"]))

    def test_recovery_rechecks_head_after_the_last_metadata_inventory(self):
        from agent.fgui_install import _metadata_guids
        self.interrupt_certification()
        calls = 0

        def head_changes(client):
            nonlocal calls
            calls += 1
            result = _metadata_guids(client)
            if calls == 2:
                self.source.git(self.client, "commit", "--allow-empty", "-qm", "concurrent HEAD change")
            return result

        with patch("agent.fgui_client._metadata_guids", side_effect=head_changes):
            with self.assertRaisesRegex(LedgerError, "identity changed"):
                self.installed()
        self.assertIsNone(installation_record(self.ledger, self.item, self.exported["receipt_id"]))

    def test_partial_write_interruption_cannot_be_certified_or_reapplied(self):
        def interrupted(plan, recovery, *, fence):
            def unavailable():
                journal = recovery / "journal.json"
                if journal.exists() and json.loads(journal.read_text(encoding="utf-8"))["completed"]:
                    raise LedgerError("simulated write interruption")
                fence()
            return apply_install(plan, recovery, fence=unavailable)
        with patch("agent.fgui_client.apply_install", side_effect=interrupted), self.assertRaisesRegex(LedgerError, "write interruption"):
            self.installed()
        with patch("agent.fgui_client.apply_install") as apply, self.assertRaisesRegex(LedgerError, "exact complete journal"):
            self.installed()
        apply.assert_not_called()
        self.assertIsNone(installation_record(self.ledger, self.item, self.exported["receipt_id"]))

    def test_stop_after_writes_cannot_create_success_or_resume_without_a_live_claim(self):
        retained = self.workflow.retained_export
        calls = 0

        def stopped(receipt):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.ledger.cancel(self.item, "Stop after writes")
            return retained(receipt)

        with patch.object(self.workflow, "retained_export", side_effect=stopped), self.assertRaises(LedgerError):
            self.installed()
        with self.assertRaises(LedgerError):
            self.installed()
        self.assertIsNone(installation_record(self.ledger, self.item, self.exported["receipt_id"]))

    def test_internal_task_installs_on_prior_scene_without_expanding_ui_diff_scope(self):
        fixture = UiClientWorkflowTests(); fixture.internal = True; fixture.setUp(); self.addCleanup(fixture.doCleanups)
        proof = fixture.installed()
        self.assertEqual(proof["baseline"], fixture.ledger.item(fixture.item)["task_client_base"])
        self.assertNotEqual(proof["baseline"], fixture.ledger.item(fixture.parent)["target"]["commit_sha"])
        self.assertEqual((fixture.client / "scene.prefab").read_text(encoding="utf-8"), "Earlier scene output")
        fixture.source.commit(fixture.client)
        from agent.fgui_scope import verify_candidate
        scope = verify_candidate(fixture.workflow, fixture.exported["receipt_id"])
        self.assertNotIn("scene.prefab", scope["changed_client_files"])
        (fixture.client / "Gameplay.cs").write_text("Unauthorized gameplay", encoding="utf-8")
        fixture.source.commit(fixture.client)
        with self.assertRaisesRegex(LedgerError, "outside certified"):
            verify_candidate(fixture.workflow, fixture.exported["receipt_id"])

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
