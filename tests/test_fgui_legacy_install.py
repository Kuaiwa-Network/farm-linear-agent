"""Legacy recovery derives authority from Git and export, never private prose."""
import hashlib
import json
import unittest
from unittest.mock import patch

from agent.fgui_client import installation_record
from agent.ledger import LedgerError
import test_fgui_client_workflow as fixtures


class LegacyInstallTests(unittest.TestCase):
    def setUp(self):
        fixture = self.fixture = fixtures.UiClientWorkflowTests()

        def baseline(origin):
            root = origin / "Assets/GameRes/FairyRes/One"
            (root / "One_old.mp3").unlink()
            (root / "One_old.mp3.meta").unlink()
            (origin / ".gitattributes").write_text("*.meta text\n", encoding="utf-8")
        fixture.prepare_baseline = baseline
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        self.journal = fixture.interrupt_certification()
        fixture.ledger.connection.execute("DELETE FROM audit WHERE kind='fgui_install_prepared'")
        self.retained = {p.name: p.read_bytes() for p in self.journal.parent.iterdir()}
        self.head = fixture.source.git(fixture.client, "rev-parse", "HEAD")
        self.index = fixture.source.git(fixture.client, "ls-files", "--stage")

    def refused(self, message):
        f = self.fixture
        with patch("agent.fgui_client.apply_install") as apply, self.assertRaisesRegex(LedgerError, message):
            f.installed()
        apply.assert_not_called()
        self.assertIsNone(installation_record(f.ledger, f.item, f.exported["receipt_id"]))

    def test_complete_legacy_install_is_certified_without_rewrites_or_backdated_intent(self):
        f = self.fixture
        with patch("agent.fgui_client.apply_install") as apply:
            proof = f.installed()
        apply.assert_not_called()
        self.assertEqual(proof["baseline"], self.head)
        self.assertEqual(proof["recovery"]["method"], "legacy-baseline-existing-files-v1")
        self.assertEqual(proof["recovery"]["journal_sha256"], hashlib.sha256(self.retained["journal.json"]).hexdigest())
        self.assertEqual({p.name: p.read_bytes() for p in self.journal.parent.iterdir()}, self.retained)
        self.assertEqual(f.source.git(f.client, "rev-parse", "HEAD"), self.head)
        self.assertEqual(f.source.git(f.client, "ls-files", "--stage"), self.index)
        self.assertEqual(f.ledger.connection.execute(
            "SELECT COUNT(*) FROM audit WHERE kind='fgui_install_prepared'").fetchone()[0], 0)
        self.assertEqual(f.installed(), proof)

    def test_forged_backup_and_matching_journal_hash_do_not_override_git(self):
        value = json.loads(self.journal.read_text(encoding="utf-8"))
        backup = self.journal.parent / value["before"][0]["backup"]
        data = backup.read_bytes().replace(b"preserved importer data", b"forged importer data")
        backup.write_bytes(data); value["before"][0]["sha256"] = hashlib.sha256(data).hexdigest()
        self.journal.write_text(json.dumps(value), encoding="utf-8")
        self.refused("cannot be proven")

    def test_incomplete_journal_is_preserved(self):
        value = json.loads(self.journal.read_text(encoding="utf-8")); value["state"] = "interrupted"
        self.journal.write_text(json.dumps(value), encoding="utf-8")
        before = self.journal.read_bytes(); self.refused("cannot be proven")
        self.assertEqual(self.journal.read_bytes(), before)

    def test_lfs_baseline_requires_both_oid_and_size(self):
        from agent.fgui_source import _git
        f = self.fixture
        atlas = "Assets/GameRes/FairyRes/One/One_atlas0.png"

        def lfs(trees, client, args, *a, **kwargs):
            raw = _git(trees, client, args, *a, **kwargs)
            if args == ["show", self.head + ":" + atlas]:
                return (f"version https://git-lfs.github.com/spec/v1\noid sha256:{hashlib.sha256(raw).hexdigest()}\n"
                        f"size {len(raw) + size_offset}\n").encode("ascii")
            return raw
        size_offset = 1
        with patch("agent.fgui_legacy_install._git", side_effect=lfs):
            self.refused("cannot be proven")
        size_offset = 0
        with patch("agent.fgui_legacy_install._git", side_effect=lfs):
            self.assertEqual(f.installed()["baseline"], self.head)

    def test_windows_crlf_metadata_is_verified_without_rewriting_it(self):
        f = self.fixture
        value = json.loads(self.journal.read_text(encoding="utf-8"))
        for entry in value["before"]:
            if entry["file"].endswith(".meta"):
                backup = self.journal.parent / entry["backup"]
                data = backup.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
                backup.write_bytes(data); entry["sha256"] = hashlib.sha256(data).hexdigest()
                (f.client / "Assets/GameRes/FairyRes" / entry["file"]).write_bytes(data)
        self.journal.write_text(json.dumps(value), encoding="utf-8")
        retained = {p.name: p.read_bytes() for p in self.journal.parent.iterdir()}
        self.assertEqual(f.installed()["baseline"], self.head)
        self.assertEqual({p.name: p.read_bytes() for p in self.journal.parent.iterdir()}, retained)

    def test_missing_completed_operation_is_not_inferred_from_current_files(self):
        value = json.loads(self.journal.read_text(encoding="utf-8")); value["completed"].pop()
        self.journal.write_text(json.dumps(value), encoding="utf-8")
        self.refused("exact complete journal")

    def test_multiple_journals_do_not_select_an_arbitrary_install(self):
        (self.journal.parent.parent / ("a" * 32)).mkdir()
        self.refused("cannot be proven")

    def test_installed_bytes_unrelated_work_and_staged_changes_are_refused(self):
        f = self.fixture
        atlas = f.client / "Assets/GameRes/FairyRes/One/One_atlas0.png"
        before = atlas.read_bytes(); atlas.write_bytes(b"uncertified output")
        self.refused("controller-certified"); atlas.write_bytes(before)
        unrelated = f.client / "Gameplay.cs"; unrelated.write_text("foreign work", encoding="utf-8")
        self.refused("unrelated Client changes"); unrelated.unlink()
        f.source.git(f.client, "add", "--all"); self.refused("unchanged index")

    def test_changed_head_cannot_repin_the_legacy_baseline(self):
        f = self.fixture
        f.source.git(f.client, "commit", "--allow-empty", "-qm", "foreign head")
        self.refused("cannot be proven")

    def test_global_guid_change_cannot_be_adopted_as_the_expected_identity(self):
        f = self.fixture
        meta = f.client / "Assets/GameRes/FairyRes/Template.meta"
        meta.write_bytes(meta.read_bytes().replace(b"guid: ", b"guid: f", 1))
        self.refused("GUID inventory is invalid")

    def test_source_failure_or_stop_during_final_validation_records_no_success(self):
        f = self.fixture; retained = f.workflow.retained_export; calls = 0

        def stop(receipt):
            nonlocal calls
            calls += 1
            if calls == 2:
                f.ledger.cancel(f.item, "Stop during legacy recovery")
            return retained(receipt)
        with patch.object(f.workflow, "retained_export", side_effect=stop):
            self.refused("running claim")

    def test_files_are_rechecked_after_current_source_validation(self):
        f = self.fixture; retained = f.workflow.retained_export; calls = 0

        def changed(receipt):
            nonlocal calls
            calls += 1
            result = retained(receipt)
            if calls == 2:
                (f.client / "Assets/GameRes/FairyRes/One/One_atlas0.png").write_bytes(b"racing output")
            return result
        with patch.object(f.workflow, "retained_export", side_effect=changed):
            self.refused("controller-certified")

    def test_journal_is_rechecked_after_current_source_validation(self):
        f = self.fixture; retained = f.workflow.retained_export; calls = 0

        def changed(receipt):
            nonlocal calls
            calls += 1
            result = retained(receipt)
            if calls == 2:
                self.journal.write_bytes(self.journal.read_bytes() + b"\n")
            return result
        with patch.object(f.workflow, "retained_export", side_effect=changed):
            self.refused("journal changed")
