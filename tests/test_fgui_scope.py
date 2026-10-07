"""UI delivery permits certified output and literal scoped dependency reasons."""
import unittest

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
