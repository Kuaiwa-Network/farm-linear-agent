"""Baseline-only metadata recovery retains approvals and all final UI gates."""
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent.fgui_repair import authorize_repair, baseline_descriptor, KIND
from agent.fgui_scope import GUARD, verify_candidate
from agent.ledger import LedgerError
from agent.worktrees import WorktreeError
import test_fgui_client_workflow as client_fixtures
from test_fgui_export import package_bytes
from test_fgui_scope import guard


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.make_fixture('            ["One"] = new[] { "Common" },\n')

    def make_fixture(self, entries):
        fixture = self.f = client_fixtures.UiClientWorkflowTests()
        self.extra = package_bytes("Extra", "extra001", dependencies=(("Common", "cmcommon"),))
        def prepare(root):
            g = root / GUARD; g.parent.mkdir(parents=True)
            g.write_text(guard(entries), encoding="utf-8")
            p = root / "Assets/GameRes/FairyRes/Extra/Extra_fui.bytes"
            p.parent.mkdir(); p.write_bytes(self.extra)
        fixture.prepare_baseline = prepare
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        fixture.installed(); fixture.source.commit(fixture.client)
        self.workflow = fixture.workflow
        self.receipt = fixture.exported["receipt_id"]
        self.reasons = {"Extra": "Extra/Panel.xml embeds Common buttons; held until Extra unloads."}

    def authorize(self, reasons=None):
        return authorize_repair(self.workflow, self.receipt, self.reasons if reasons is None else reasons)

    def apply(self, proof):
        data = Path(proof["proposal_path"]).read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), proof["proposal_sha256"])
        (self.f.client / GUARD).write_bytes(data)
        self.f.source.commit(self.f.client)

    def test_qualified_repair_retains_baseline_install_and_exact_scope(self):
        baseline = self.f.baseline
        old = (self.f.client / GUARD).read_bytes()
        proof = self.authorize()
        self.assertEqual((self.f.client / GUARD).read_bytes(), old)
        self.apply(proof)
        scoped = verify_candidate(self.workflow, self.receipt)
        self.assertEqual(scoped["baseline"], baseline)
        self.assertEqual(scoped["baseline_guard_repairs"], ["Extra"])
        self.assertEqual(scoped["baseline_guard_repair_sha256"], [proof["repair_sha256"]])
        self.assertEqual(scoped["guard_packages"], [])
        self.assertEqual(self.f.ledger.item(self.f.item)["target"]["commit_sha"], baseline)
        self.assertFalse(self.f.ledger.connection.execute(
            "SELECT 1 FROM audit WHERE kind='fgui_guards_complete'").fetchone())

    def test_existing_allowances_are_preserved_without_new_arbitrary_edges(self):
        self.make_fixture('            ["One"] = new[] { "Common" },\n'
                          '            ["Extra"] = new[] { "Dormant" }, // Existing retained allowance.\n')
        proof = self.authorize()
        self.assertEqual(proof["entries"]["Extra"]["dependencies"], ["Common", "Dormant"])
        self.assertEqual(proof["entries"]["Extra"]["descriptor_dependencies"], ["Common"])
        self.apply(proof)
        self.assertEqual(verify_candidate(self.workflow, self.receipt)["baseline_guard_repairs"], ["Extra"])

    def test_interrupted_before_apply_can_repeat_without_duplicate_audit(self):
        proof = self.authorize()
        self.assertEqual(self.authorize(), proof)
        self.assertEqual(verify_candidate(self.workflow, self.receipt)["baseline"], self.f.baseline)
        count = self.f.ledger.connection.execute("SELECT COUNT(*) FROM audit WHERE kind=?", (KIND,)).fetchone()[0]
        self.assertEqual(count, 1)
        self.apply(proof); self.assertEqual(self.authorize(), proof)

    def test_qualified_repair_does_not_permit_assets_gameplay_or_test_logic(self):
        self.apply(self.authorize())
        (self.f.client / GUARD).write_text((self.f.client / GUARD).read_text().replace("RunActualChecks", "SkipActualChecks"))
        self.f.source.commit(self.f.client)
        with self.assertRaisesRegex(LedgerError, "test code"):
            verify_candidate(self.workflow, self.receipt)

    def test_altered_repair_comment_or_invented_edge_is_not_authorized(self):
        self.apply(self.authorize())
        path = self.f.client / GUARD
        path.write_text(path.read_text().replace("held until Extra unloads", "different unapproved reason"))
        self.f.source.commit(self.f.client)
        with self.assertRaisesRegex(LedgerError, "exact controller authorization"):
            verify_candidate(self.workflow, self.receipt)

    def test_unrelated_edge_without_record_remains_forbidden(self):
        path = self.f.client / GUARD
        path.write_text(guard('            ["One"] = new[] { "Common" },\n'
                              '            ["Extra"] = new[] { "Common" }, // blind registration\n'))
        self.f.source.commit(self.f.client)
        with self.assertRaisesRegex(LedgerError, "only this export"):
            verify_candidate(self.workflow, self.receipt)

    def test_changed_package_and_missing_reason_are_refused(self):
        for reasons in ({"One": self.reasons["Extra"]}, {"Extra": "yes"}, {"../Extra": self.reasons["Extra"]}):
            with self.subTest(reasons=reasons), self.assertRaises(LedgerError):
                self.authorize(reasons)

    def test_changed_descriptor_cannot_be_laundered_as_baseline(self):
        p = self.f.client / "Assets/GameRes/FairyRes/Extra/Extra_fui.bytes"
        p.write_bytes(package_bytes("Extra", "extra001", dependencies=(("ItemIcons", "ii0items"),)))
        self.f.source.commit(self.f.client)
        with self.assertRaisesRegex(LedgerError, "outside certified"):
            self.authorize()

    def test_dirty_candidate_is_refused_without_authorization(self):
        (self.f.client / GUARD).write_text("dirty")
        with self.assertRaisesRegex(WorktreeError, "clean worktree"):
            self.authorize()
        self.assertFalse(self.f.ledger.connection.execute("SELECT 1 FROM audit WHERE kind=?", (KIND,)).fetchone())

    def test_stop_during_qualification_cannot_record_authority(self):
        original = self.workflow.fence
        calls = 0
        def stopped(*, refresh=False):
            nonlocal calls
            calls += 1
            if calls >= 4:
                raise LedgerError("delegation withdrawn")
            return original(refresh=refresh)
        with patch.object(self.workflow, "fence", side_effect=stopped), self.assertRaisesRegex(LedgerError, "withdrawn"):
            self.authorize()
        self.assertFalse(self.f.ledger.connection.execute("SELECT 1 FROM audit WHERE kind=?", (KIND,)).fetchone())

    def test_damaged_audit_is_not_an_allowance(self):
        self.apply(self.authorize())
        self.f.ledger.connection.execute("UPDATE audit SET details='{}' WHERE kind=?", (KIND,))
        with self.assertRaisesRegex(LedgerError, "damaged controller"):
            verify_candidate(self.workflow, self.receipt)

    def test_cli_uses_real_qualification_and_private_input(self):
        path = self.f.paths.runs / self.f.item / "reasons.json"
        path.write_text(json.dumps(self.reasons), encoding="utf-8")
        proof = self.f.source.cli("authorize-ui-guard-repair", "--receipt-id", self.receipt, "--input", str(path))
        self.apply(proof)
        self.assertEqual(verify_candidate(self.workflow, self.receipt)["baseline_guard_repairs"], ["Extra"])

    def test_private_input_cannot_read_external_file(self):
        path = self.f.source.root / "external.json"
        path.write_text(json.dumps(self.reasons), encoding="utf-8")
        with self.assertRaisesRegex(LedgerError, "private state"):
            self.f.source.cli("authorize-ui-guard-repair", "--receipt-id", self.receipt, "--input", str(path))


class DescriptorIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.client = Path(self.temp.name)
        self.path = self.client / "Assets/GameRes/FairyRes/Extra/Extra_fui.bytes"
        self.path.parent.mkdir(parents=True)
        self.data = package_bytes("Extra", "extra001", dependencies=(("Common", "cmcommon"),))
        self.path.write_bytes(self.data)
        self.workflow = SimpleNamespace(trees=object())
        self.pointer = ("version https://git-lfs.github.com/spec/v1\noid sha256:"
                        + hashlib.sha256(self.data).hexdigest() + "\nsize " + str(len(self.data)) + "\n").encode()

    def read(self, raw):
        with patch("agent.fgui_repair._git", return_value=raw) as read:
            result = baseline_descriptor(self.workflow, self.client, "a" * 40, "Extra")
            self.assertEqual(read.call_args.kwargs["repo"], "Farm-Client")
            return result

    def test_hydrated_lfs_matches_baseline_pointer_and_parsed_edges(self):
        self.assertEqual(self.read(self.pointer)[:2], ("extra001", ["Common"]))

    def test_changed_hydration_and_unhydrated_pointer_refuse_success(self):
        for data in (self.data + b"foreign", self.pointer):
            self.path.write_bytes(data)
            with self.assertRaisesRegex(LedgerError, "baseline LFS identity"):
                self.read(self.pointer)

    def test_non_lfs_input_requires_exact_raw_baseline_blob(self):
        self.assertEqual(self.read(self.data)[:2], ("extra001", ["Common"]))
        with self.assertRaisesRegex(LedgerError, "differs from its immutable baseline"):
            self.read(self.data + b"foreign")
