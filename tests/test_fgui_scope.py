"""UI delivery permits certified output and literal scoped dependency reasons."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from agent.fgui_scope import dependency_guard_change, verify_candidate
from agent.ledger import LedgerError
from agent.worktrees import WorktreeError
import test_fgui_client_workflow as client_fixtures


def guard(entries, suffix="RunActualChecks();"):
    return ('class Guard\n{\n        private static readonly Dictionary<string, string[]> _allowed = new()\n'
            '        {\n' + entries + '        };\n' + suffix + '\n}\n')


class DependencyScopeTests(unittest.TestCase):
    def setUp(self):
        self.before = guard('            ["One"] = new[] { "Common" },\n')

    def test_actual_new_dependency_with_reason_is_scoped(self):
        after = guard('            ["One"] = new[] { "Common", "ItemIcons" }, // Icon loader uses ItemIcons.\n')
        self.assertEqual(dependency_guard_change(self.before, after, {"One": ["Common", "ItemIcons"]}), ["One"])

    def test_new_package_entry_and_its_reason_are_scoped(self):
        after = guard('            ["One"] = new[] { "Common" },\n'
                      '            // New panel uses Common buttons.\n            ["NewPanel"] = new[] { "Common" },\n')
        self.assertEqual(dependency_guard_change(self.before, after, {"NewPanel": ["Common"]}), ["NewPanel"])

    def test_reason_is_required_for_added_edges(self):
        after = guard('            ["One"] = new[] { "Common", "ItemIcons" },\n')
        with self.assertRaisesRegex(LedgerError, "require a reason"):
            dependency_guard_change(self.before, after, {"One": ["Common", "ItemIcons"]})

    def test_guard_code_and_unrelated_package_changes_are_refused(self):
        with self.assertRaisesRegex(LedgerError, "test code"):
            dependency_guard_change(self.before, self.before.replace("RunActualChecks();", "SkipChecks();"), {"One": ["Common"]})
        after = guard('            ["One"] = new[] { "Common" },\n            ["Other"] = new[] { "Common" }, // unowned\n')
        with self.assertRaisesRegex(LedgerError, "only this export"):
            dependency_guard_change(self.before, after, {"One": ["Common"]})

    def test_descriptor_mismatch_and_executable_entries_are_refused(self):
        after = guard('            ["One"] = new[] { "Unrelated" }, // guessed\n')
        with self.assertRaisesRegex(LedgerError, "actual exported descriptor"):
            dependency_guard_change(self.before, after, {"One": ["Common"]})
        after = guard('            ["One"] = ExecuteUnknownCode(),\n')
        with self.assertRaisesRegex(LedgerError, "executable"):
            dependency_guard_change(self.before, after, {"One": ["Common"]})

    def test_duplicate_entries_and_hidden_array_tokens_are_refused(self):
        for body in ('            ["One"] = new[] { "Common", "Common" },\n',
                     '            ["One"] = new[] { "Common" + Unsafe() },\n',
                     '            ["One"] = new[] { "Common" },\n            ["One"] = new[] { "Common" },\n'):
            with self.subTest(body=body), self.assertRaises(LedgerError):
                dependency_guard_change(self.before, guard(body), {"One": ["Common"]})

    def test_native_crlf_normalization_keeps_guard_code_identity(self):
        self.assertEqual(dependency_guard_change(self.before, self.before.replace("\n", "\r\n"), {"One": ["Common"]}), [])


class ClientScopeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture = client_fixtures.UiClientWorkflowTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        self.proof = fixture.installed()

    def verify(self, **kwargs):
        return verify_candidate(self.fixture.workflow, self.fixture.exported["receipt_id"], **kwargs)

    def test_clean_committed_install_proves_exact_head_and_scoped_delta(self):
        source = self.fixture.source; source.commit(self.fixture.client)
        head = source.git(self.fixture.client, "rev-parse", "HEAD")
        proof = self.verify(expected_commit=head)
        self.assertEqual(proof["commit"], head)
        self.assertEqual(proof["baseline"], self.fixture.baseline)
        self.assertTrue(all(path.startswith("Assets/GameRes/FairyRes/One") for path in proof["changed_client_files"]))

    def test_uncommitted_install_and_wrong_requested_head_are_pending(self):
        with self.assertRaisesRegex(WorktreeError, "clean worktree"): self.verify()
        self.fixture.source.commit(self.fixture.client)
        with self.assertRaisesRegex(LedgerError, "actual committed Client HEAD"):
            self.verify(expected_commit=self.fixture.baseline)

    def test_client_gameplay_changes_cannot_be_delivered_as_ui_output(self):
        (self.fixture.client / "Gameplay.cs").write_text("unauthorized gameplay change", encoding="utf-8")
        self.fixture.source.commit(self.fixture.client)
        with self.assertRaisesRegex(LedgerError, "outside certified"): self.verify()

    def test_altered_installed_artifact_is_refused_even_if_committed(self):
        path = self.fixture.client / "Assets/GameRes/FairyRes/One/One_fui.bytes"
        path.write_bytes(b"forged generated descriptor")
        self.fixture.source.commit(self.fixture.client)
        with self.assertRaisesRegex(LedgerError, "controller-certified"): self.verify()

    def test_verify_ui_cli_checks_the_current_client_stage_and_committed_head(self):
        self.fixture.source.commit(self.fixture.client)
        head = self.fixture.source.git(self.fixture.client, "rev-parse", "HEAD")
        proof = self.fixture.source.cli("verify-ui", "--receipt-id", self.fixture.exported["receipt_id"], "--commit", head)
        self.assertEqual(proof["commit"], head)
        with self.assertRaisesRegex(LedgerError, "actual committed Client HEAD"):
            self.fixture.source.cli("verify-ui", "--receipt-id", self.fixture.exported["receipt_id"],
                                    "--commit", self.fixture.baseline)

    def test_damaged_prior_installation_cannot_supply_scope_for_authorized_deletions(self):
        self.fixture.source.commit(self.fixture.client)
        self.fixture.ledger.connection.execute("UPDATE audit SET details='{}' WHERE kind='fgui_install_complete'")
        with self.assertRaisesRegex(LedgerError, "damaged controller"): self.verify()


class IntegratedClientScopeTests(unittest.TestCase):
    setUp = ClientScopeTests.setUp
    verify = ClientScopeTests.verify
    def advance_main(self, *, name="Assets/Upstream.cs", metadata=True):
        origin = Path(self.fixture.source.config.repos["Farm-Client"])
        path = origin / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("trusted upstream change", encoding="utf-8")
        if metadata:
            Path(str(path) + ".meta").write_text("fileFormatVersion: 2\nguid: " + "d" * 32 + "\n", encoding="utf-8")
        self.fixture.source.commit(origin)
        return self.fixture.source.git(origin, "rev-parse", "HEAD")

    def integrate(self):
        self.fixture.source.commit(self.fixture.client)
        self.fixture.workflow.trees.fetch("Farm-Client")
        self.fixture.source.git(self.fixture.client, "merge", "--no-edit", "origin/main")

    def test_integrated_trusted_main_preserves_installation_and_original_baseline(self):
        main = self.advance_main(); self.integrate()
        proof = self.verify()
        self.assertEqual(proof["baseline"], self.fixture.baseline)
        self.assertEqual(proof["scope_base"], main)
        self.assertEqual(proof["main_integration"]["commit"], main)
        self.assertNotEqual(proof["global_guid_digest"], self.proof["global_guid_digest"])
        self.assertTrue(all(p.startswith("Assets/GameRes/FairyRes/One") for p in proof["changed_client_files"]))

    def test_completed_install_retry_qualifies_main_without_rewriting_assets_or_proof(self):
        self.advance_main(); self.integrate()
        with patch("agent.fgui_client.apply_install") as apply:
            self.assertEqual(self.fixture.installed(), self.proof)
        apply.assert_not_called()
        self.assertEqual(self.fixture.ledger.connection.execute(
            "SELECT COUNT(*) FROM audit WHERE kind='fgui_install_complete'").fetchone()[0], 1)

    def test_unmerged_main_and_forged_tracking_ref_do_not_qualify_upstream(self):
        main = self.advance_main(); self.integrate()
        self.advance_main(name="Assets/NewMain.cs", metadata=False)
        head = self.fixture.source.git(self.fixture.client, "rev-parse", "HEAD")
        self.fixture.source.git(self.fixture.client, "update-ref", "refs/remotes/origin/main", head)
        with self.assertRaisesRegex(WorktreeError, "integrate main"): self.verify()
        self.assertEqual(self.fixture.source.git(self.fixture.client, "rev-parse", "HEAD"), head)

    def test_integrated_main_does_not_authorize_new_worker_gameplay_changes(self):
        self.advance_main(); self.integrate()
        (self.fixture.client / "Assets/Upstream.cs").write_text("worker-authored gameplay", encoding="utf-8")
        self.fixture.source.commit(self.fixture.client)
        with self.assertRaisesRegex(LedgerError, "outside certified"): self.verify()

    def test_integrated_main_does_not_authorize_changed_certified_files(self):
        self.advance_main(); self.integrate()
        ClientScopeTests.test_altered_installed_artifact_is_refused_even_if_committed(self)

    def test_ignored_metadata_cannot_be_presented_as_inherited_main_metadata(self):
        self.advance_main(); self.integrate()
        # Exclude rules are worktree data, not a grant for untracked GUIDs.
        exclude = self.fixture.source.trees.ensure_clone("Farm-Client") / "info/exclude"
        exclude.write_text("Assets/Hidden.meta\n", encoding="utf-8")
        (self.fixture.client / "Assets/Hidden.meta").write_text("guid: " + "e" * 32 + "\n", encoding="utf-8")
        with self.assertRaisesRegex(LedgerError, "committed integrated"): self.verify()

    def test_assume_unchanged_metadata_cannot_change_the_committed_guid_inventory(self):
        self.advance_main(); self.integrate()
        path = self.fixture.client / "Assets/Upstream.cs.meta"
        self.fixture.source.git(self.fixture.client, "update-index", "--assume-unchanged", "Assets/Upstream.cs.meta")
        path.write_text("guid: " + "e" * 32 + "\n", encoding="utf-8")
        with self.assertRaisesRegex(LedgerError, "committed integrated"): self.verify()

    def test_integrated_main_does_not_authorize_duplicate_guids(self):
        self.advance_main(); self.integrate()
        (self.fixture.client / "Assets/Duplicate.meta").write_text("guid: " + "d" * 32 + "\n", encoding="utf-8")
        self.fixture.source.commit(self.fixture.client)
        with self.assertRaisesRegex(LedgerError, "globally unique"): self.verify()

    def test_integrated_main_does_not_authorize_changed_test_code(self):
        origin = Path(self.fixture.source.config.repos["Farm-Client"])
        path = origin / "tests/Farm.Tests.Unit/FguiDependencyGuardTests.cs"
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text(guard('            ["One"] = new[] { "Common" },\n'), encoding="utf-8")
        self.fixture.source.commit(origin); self.integrate()
        target = self.fixture.client / path.relative_to(origin)
        target.write_text(target.read_text(encoding="utf-8").replace("RunActualChecks();", "SkipChecks();"), encoding="utf-8")
        self.fixture.source.commit(self.fixture.client)
        with self.assertRaisesRegex(LedgerError, "test code"): self.verify()

    def test_active_reservation_uses_previously_qualified_commit_when_main_advances(self):
        self.advance_main(); self.integrate(); proof = self.verify()
        self.advance_main(name="Assets/NewMain.cs", metadata=False)
        reservation = {"state": "active", "kind": "unity_slot", "commit_sha": proof["commit"]}
        with patch.object(self.fixture.ledger, "active_reservation", return_value=reservation), \
                patch.object(self.fixture.workflow.trees, "require_main_integration", side_effect=AssertionError("must retain input")):
            self.assertEqual(self.verify(), proof)
        with self.assertRaisesRegex(WorktreeError, "integrate main"): self.verify()

    def test_active_reservation_refuses_damaged_qualification_record(self):
        self.advance_main(); self.integrate(); proof = self.verify()
        proof["scope_base"] = "0" * 40
        self.fixture.ledger.connection.execute("UPDATE audit SET details=? WHERE kind='fgui_client_scope'",
                                              (json.dumps(proof),))
        reservation = {"state": "active", "kind": "unity_slot", "commit_sha": proof["commit"]}
        with patch.object(self.fixture.ledger, "active_reservation", return_value=reservation), \
                self.assertRaisesRegex(LedgerError, "damaged controller"):
            self.verify()

    def test_prior_scene_output_is_preserved_without_granting_new_scene_writes(self):
        other = client_fixtures.UiClientWorkflowTests(); other.internal = True; other.setUp()
        self.addCleanup(other.doCleanups); self.fixture = other; self.proof = other.installed()
        self.advance_main(); self.integrate()
        proof = self.verify()
        self.assertEqual(proof["inherited_client_files"], ["scene.prefab"])
        (other.client / "scene.prefab").write_text("changed inherited scene", encoding="utf-8")
        other.source.commit(other.client)
        with self.assertRaisesRegex(LedgerError, "outside certified"): self.verify()

    def test_existing_legacy_reservation_retains_only_its_original_scope(self):
        self.fixture.source.commit(self.fixture.client)
        proof = self.verify()
        legacy = {k: v for k, v in proof.items() if k not in (
            "main_integration", "scope_base", "global_guid_digest", "inherited_client_files", "scope_sha256")}
        self.fixture.ledger.connection.execute("UPDATE audit SET details=? WHERE kind='fgui_client_scope'", (json.dumps(legacy),))
        self.advance_main()
        reservation = {"state": "active", "kind": "unity_slot", "commit_sha": proof["commit"]}
        with patch.object(self.fixture.ledger, "active_reservation", return_value=reservation):
            self.assertEqual(self.verify(), legacy)
        with self.assertRaisesRegex(WorktreeError, "integrate main"): self.verify()

    def test_prior_presentation_cannot_override_an_upstream_change(self):
        other = client_fixtures.UiClientWorkflowTests(); other.internal = True; other.setUp()
        self.addCleanup(other.doCleanups); self.fixture = other; self.proof = other.installed()
        self.advance_main(name="scene.prefab", metadata=False)
        other.source.commit(other.client); other.workflow.trees.fetch("Farm-Client")
        other.source.git(other.client, "merge", "--no-edit", "-Xours", "origin/main")
        with self.assertRaisesRegex(LedgerError, "outside certified"): self.verify()
