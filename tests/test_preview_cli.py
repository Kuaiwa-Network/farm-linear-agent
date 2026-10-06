"""Preview publication authenticates the intended item and fences the allocation/PUT."""
import json
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from agent.__main__ import parser, run
from agent.config import Paths, StubLinear, load_config
from agent.ledger import Ledger, LedgerError
from test_ledger import ISSUE, issue


class PreviewCliTests(unittest.TestCase):
    def test_controller_and_existing_cli_import_without_optional_imaging_dependency(self):
        code = ('import sys; sys.path.insert(0, sys.argv[1]); '
                'import agent.service, agent.__main__, agent.linear_api; '
                'assert "PIL" not in sys.modules')
        result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code,
                                 str(Path(__file__).resolve().parents[1])],
                                capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="preview CLI ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        conf = self.root / "dummy.json"
        conf.write_text(json.dumps({"client_id": "fixture", "client_secret": "fixture", "webhook_secret": "fixture",
                                   "local_root": str(self.root / "state"), "enabled_skills": ["chat", "fgui"]}),
                        encoding="utf-8")
        self.config = load_config(conf, secure_permissions=False)
        self.paths = Paths(self.config)
        self.paths.ledger.parent.mkdir(parents=True)
        self.ledger = Ledger(self.paths.ledger)
        self.addCleanup(self.ledger.close)
        stub = self.root / "stub"
        stub.mkdir()
        self.api = StubLinear(stub)
        self.raw = issue(delegate_id=self.api.app_user_id)
        self.write_issue()
        self.ledger.observe_issue(self.raw)
        self.ledger.ensure_session("ui-session", ISSUE, delegation=True)
        self.item = self.ledger.create_work_item(issue_id=ISSUE, session_id="ui-session", skill="fgui")
        self.token = self.ledger.claim(self.item["id"], worker_id="ui-fixture")["token"]
        state = self.paths.runs / self.item["id"]
        state.mkdir(parents=True)
        self.image = state / "预览 图.png"
        with Image.new("RGB", (4, 3), "blue") as image:
            image.save(self.image)
        self.factory = Mock(return_value=self.api)
        self.args = parser().parse_args(["--db", str(self.paths.ledger), "upload-image", "--item", self.item["id"],
                                        "--token", self.token, "--file", str(self.image)])
        self.enterContext(patch("agent.__main__.load_config", return_value=self.config))

    def write_issue(self):
        (self.api.directory / "issue.json").write_text(json.dumps(self.raw), encoding="utf-8")

    def upload(self):
        return run(self.args, self.ledger, self.factory)

    def stored_uploads(self):
        return list(self.api.directory.glob("preview-*.bin"))

    def test_valid_ui_claim_uploads_only_its_snapshot_and_returns_unsigned_evidence(self):
        result = self.upload()
        self.assertEqual(result["pixels"], {"width": 4, "height": 3})
        self.assertTrue(result["asset_url"].startswith("https://uploads.linear.app/stub/"))
        self.assertNotIn("?", result["asset_url"])
        self.assertEqual(self.stored_uploads()[0].read_bytes(), self.image.read_bytes())
        self.assertEqual(self.ledger.item(self.item["id"])["state"], "running")
        calls = [json.loads(s) for s in (self.api.directory / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([s["method"] for s in calls], ["fetch_issue", "upload_image"])
        self.assertNotIn(self.token, json.dumps(result))

    def test_wrong_or_cancelled_claim_refuses_before_disk_or_api(self):
        self.args.token = "not-this-claim"
        with patch("agent.preview_upload.read_image") as read, self.assertRaises(LedgerError):
            self.upload()
        read.assert_not_called()
        self.factory.assert_not_called()
        self.args.token = self.token
        self.ledger.cancel(self.item["id"], "operator Stop")
        with patch("agent.preview_upload.read_image") as read, self.assertRaises(LedgerError):
            self.upload()
        read.assert_not_called()
        self.factory.assert_not_called()

    def test_wrong_ledger_other_skills_wrong_root_and_disabled_ui_refuse_before_api(self):
        original = self.args.db
        self.args.db = str(self.root / "foreign.sqlite3")
        with self.assertRaisesRegex(LedgerError, "configured host ledger"):
            self.upload()
        self.args.db = original
        for skill, root in (("fix", "farmgui"), ("feature", "farmgui"), ("chat", None), ("fgui", "Farm-Client")):
            self.ledger.connection.execute("UPDATE work_items SET skill=?,root_repo=? WHERE id=?",
                                           (skill, root, self.item["id"]))
            with self.subTest(skill=skill, root=root), self.assertRaisesRegex(LedgerError, "authoring stage"):
                self.upload()
        self.ledger.connection.execute("UPDATE work_items SET skill='fgui',root_repo=NULL WHERE id=?", (self.item["id"],))
        self.config.enabled_skills = ["chat", "fix"]
        with self.assertRaisesRegex(LedgerError, "explicit fgui enablement"):
            self.upload()
        self.factory.assert_not_called()

    def test_other_item_state_and_invalid_image_refuse_before_api(self):
        outside = self.paths.runs / "other-item"
        outside.mkdir()
        other = outside / self.image.name
        other.write_bytes(self.image.read_bytes())
        self.args.file = str(other)
        with self.assertRaisesRegex(LedgerError, "this item's state"):
            self.upload()
        self.args.file = str(self.image)
        self.image.write_bytes(b"not an image")
        with self.assertRaises(LedgerError):
            self.upload()
        self.factory.assert_not_called()

    def test_lost_delegation_closed_card_and_stop_during_fetch_never_allocate(self):
        for change in ({"delegate_id": None}, {"archived": True}, {"status_type": "completed"}):
            self.raw = issue(delegate_id=self.api.app_user_id, **change) if "delegate_id" not in change else issue(**change)
            self.write_issue()
            with self.subTest(change=change), self.assertRaises(LedgerError):
                self.upload()
            self.assertFalse(self.stored_uploads())
        self.raw = issue(delegate_id=self.api.app_user_id)
        self.write_issue()
        original = self.api.fetch_issue
        def stopped(ref):
            self.ledger.cancel(self.item["id"], "operator Stop during fetch")
            return original(ref)
        self.api.fetch_issue = stopped
        with self.assertRaises(LedgerError):
            self.upload()
        self.assertFalse(self.stored_uploads())

    def test_stop_during_image_validation_prevents_api_construction(self):
        from agent.preview_upload import read_image
        def stopped(*args):
            result = read_image(*args)
            self.ledger.cancel(self.item["id"], "Stop while decoding")
            return result
        with patch("agent.preview_upload.read_image", side_effect=stopped), self.assertRaises(LedgerError):
            self.upload()
        self.factory.assert_not_called()

    def test_stop_after_put_never_returns_a_success_result_and_preserves_the_uploaded_fixture(self):
        original = self.api.upload_image
        def stopped(*args, **kwargs):
            result = original(*args, **kwargs)
            self.ledger.cancel(self.item["id"], "Stop after PUT")
            return result
        self.api.upload_image = stopped
        with self.assertRaises(LedgerError):
            self.upload()
        self.assertEqual(len(self.stored_uploads()), 1)
        self.assertEqual(self.ledger.item(self.item["id"])["state"], "cancelled")


if __name__ == "__main__":
    unittest.main()
