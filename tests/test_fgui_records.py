"""Actual ledger transfer/export proof survives attempts without trusting plans."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from agent.fgui_approval import ExportAuthority, source_digest
from agent.fgui_export import snapshot_export
from agent.fgui_publisher import PublishResult
from agent.fgui_records import export_record, record_export
from agent.ledger import Ledger, LedgerError
from test_fgui_export import package_bytes
from test_ledger import ISSUE, OTHER, issue


class UiRecordTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.ledger = Ledger(self.root / "ledger.sqlite3"); self.addCleanup(self.ledger.close)
        self.ledger.observe_issue(issue()); self.ledger.ensure_session("ui-record", ISSUE, delegation=True)
        self.item = self.ledger.create_work_item(issue_id=ISSUE, session_id="ui-record", skill="fgui")["id"]
        self.token = self.ledger.claim(self.item, worker_id="fixture")["token"]
        stage = self.root / "stage"; stage.mkdir()
        (stage / "One_fui.bytes").write_bytes(package_bytes())
        Image.new("RGBA", (2, 2), "red").save(stage / "One_atlas0.png")
        digest = source_digest({"assets/One/package.xml": "a" * 64})
        event = tuple(sorted({"author_id": "fixture-person", "author_name": "Fixture Person",
                              "message_id": 1, "created_at": "2026-10-07T00:00:00Z", "body_sha256": "f" * 64}.items()))
        self.authority = ExportAuthority("a" * 40, digest, 1, "b" * 64, (("One", "pack0001"),), event, event)
        self.result = PublishResult(snapshot_export(stage, {"One": "pack0001"}), "a" * 40, digest,
                                    (("fixture", "c" * 64),), tuple(sorted({"exit_code": 0, "job_empty": True,
                                    "job_assigned_before_startup": True, "job_assigned_before_export": True}.items())), "d" * 64)
        self.identity = "1" * 32

    def save(self, **kwargs):
        values = {"source_pr": "https://github.com/example/farmgui/pull/1", "main": "e" * 40, **kwargs}
        return record_export(self.ledger, self.item, self.token, self.identity, self.authority, self.result, **values)

    def test_reopen_and_client_stage_retain_the_controller_record(self):
        proof = self.save()
        self.ledger.connection.execute("UPDATE work_items SET root_repo='Farm-Client', checkpoint=? WHERE id=?",
                                       (json.dumps({"plan": {"ui": {"receipt_id": "2" * 32}}}), self.item))
        other = Ledger(self.root / "ledger.sqlite3")
        try:
            stored = export_record(other, self.item, self.identity)
            self.assertEqual(stored["receipt_sha256"], proof["receipt_sha256"])
            self.assertEqual(stored["publisher"]["export"], self.result.snapshot.evidence())
            with self.assertRaisesRegex(LedgerError, "exactly one"):
                export_record(other, self.item, "2" * 32)
        finally:
            other.close()

    def test_duplicate_identity_cannot_replace_a_completed_export(self):
        proof = self.save()
        with self.assertRaisesRegex(LedgerError, "immutable"):
            self.save(source_pr="https://github.com/example/farmgui/pull/2")
        self.assertEqual(export_record(self.ledger, self.item, self.identity)["source_pr"], proof["source_pr"])

    def test_other_issue_and_worker_plan_cannot_create_export_proof(self):
        self.save(); self.ledger.observe_issue(issue(id=OTHER))
        self.ledger.ensure_session("foreign-record", OTHER, delegation=True)
        foreign = self.ledger.create_work_item(issue_id=OTHER, session_id="foreign-record", skill="fgui")["id"]
        self.ledger.connection.execute("UPDATE work_items SET checkpoint=? WHERE id=?",
                                       (json.dumps({"plan": {"ui": {"receipt_id": self.identity}}}), foreign))
        with self.assertRaisesRegex(LedgerError, "exactly one"):
            export_record(self.ledger, foreign, self.identity)

    def test_wrong_claim_stop_and_wrong_root_never_certify_completed_helper(self):
        token = self.token; self.token = "foreign"
        with self.assertRaises(LedgerError): self.save()
        self.token = token
        self.ledger.connection.execute("UPDATE work_items SET root_repo='Farm-Client' WHERE id=?", (self.item,))
        with self.assertRaisesRegex(LedgerError, "rooted stage"): self.save()
        self.ledger.connection.execute("UPDATE work_items SET root_repo=NULL WHERE id=?", (self.item,))
        self.ledger.cancel(self.item, "Stop after publisher")
        with self.assertRaises(LedgerError): self.save()
        with self.assertRaisesRegex(LedgerError, "exactly one"): export_record(self.ledger, self.item, self.identity)

    def test_withdrawn_work_never_certifies_completed_helper(self):
        self.ledger.connection.execute("UPDATE work_items SET withdraw_deadline=0,withdraw_reason='withdrawn' WHERE id=?", (self.item,))
        with self.assertRaisesRegex(LedgerError, "withdrawn"): self.save()

    def test_stale_source_and_uncontained_results_are_refused(self):
        original = self.result
        cases = [replace(original, source_head="f" * 40), replace(original, source_digest="f" * 64)]
        for field in ("job_empty", "job_assigned_before_startup", "job_assigned_before_export"):
            process = dict(original.process); process[field] = False
            cases.append(replace(original, process=tuple(sorted(process.items()))))
        for result in cases:
            with self.subTest(result=result):
                self.result = result
                with self.assertRaisesRegex(LedgerError, "stale source|uncertain process"): self.save()
        with self.assertRaisesRegex(LedgerError, "exactly one"): export_record(self.ledger, self.item, self.identity)

    def test_changed_package_omission_refuses_receipt(self):
        self.authority = replace(self.authority, changed_packages=(("Missing", "missing1"),))
        with self.assertRaisesRegex(LedgerError, "changed package"): self.save()

    def test_worker_dictionary_and_unsafe_receipt_identity_are_refused(self):
        self.result = self.result.evidence()
        with self.assertRaisesRegex(LedgerError, "immutable helper"): self.save()
        self.identity = "../foreign"
        with self.assertRaisesRegex(LedgerError, "identity is invalid"): export_record(self.ledger, self.item, self.identity)

    def test_damaged_controller_record_is_reported_without_cleanup(self):
        self.save()
        row = self.ledger.connection.execute("SELECT id,details FROM audit WHERE kind='fgui_export_complete'").fetchone()
        corrupted = json.loads(row["details"]); corrupted["main"] = "f" * 40
        self.ledger.connection.execute("UPDATE audit SET details=? WHERE id=?", (json.dumps(corrupted), row["id"]))
        with self.assertRaisesRegex(LedgerError, "damaged immutable"): export_record(self.ledger, self.item, self.identity)
        self.assertIsNotNone(self.ledger.connection.execute("SELECT 1 FROM audit WHERE id=?", (row["id"],)).fetchone())
