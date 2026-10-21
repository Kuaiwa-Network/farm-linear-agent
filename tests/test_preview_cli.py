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


class ImageCliFixture(unittest.TestCase):
    SKILL = "fgui"
    ENABLED = ["chat", "fgui"]
    AUTHORITY = "delegation"
    DELEGATED = True

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="preview CLI ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        conf = self.root / "dummy.json"
        conf.write_text(json.dumps({"client_id": "fixture", "client_secret": "fixture", "webhook_secret": "fixture",
                                   "local_root": str(self.root / "state"), "enabled_skills": self.ENABLED}),
                        encoding="utf-8")
        self.config = load_config(conf, secure_permissions=False)
        self.paths = Paths(self.config)
        self.paths.ledger.parent.mkdir(parents=True)
        self.ledger = Ledger(self.paths.ledger)
        self.addCleanup(self.ledger.close)
        stub = self.root / "stub"
        stub.mkdir()
        self.api = StubLinear(stub)
        self.raw = issue(delegate_id=self.api.app_user_id if self.DELEGATED else None)
        self.write_issue()
        self.ledger.observe_issue(self.raw)
        self.ledger.ensure_session("ui-session", ISSUE, delegation=self.DELEGATED)
        self.item = self.ledger.create_work_item(issue_id=ISSUE, session_id="ui-session", skill=self.SKILL,
                                                authority=self.AUTHORITY, execution_contract='staged-v1')
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


class PreviewCliTests(ImageCliFixture):
    def test_controller_and_existing_cli_import_without_optional_imaging_dependency(self):
        code = ('import sys; sys.path.insert(0, sys.argv[1]); '
                'import agent.service, agent.__main__, agent.linear_api; '
                'assert "PIL" not in sys.modules')
        result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code,
                                 str(Path(__file__).resolve().parents[1])],
                                capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

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
        from agent.fgui_records import published_preview
        proof = published_preview(self.ledger, self.item["id"], result["sha256"], result["asset_url"],
                                  before=self.ledger.clock())
        self.assertEqual(proof["sha256"], result["sha256"])
        self.assertEqual(proof["pixels"], result["pixels"])
        self.assertNotIn(self.token, json.dumps(proof))

    def test_planned_wrong_issue_or_late_preview_cannot_supply_export_review_proof(self):
        from agent.fgui_records import published_preview
        result = self.upload()
        with self.assertRaisesRegex(LedgerError, "earlier same-issue"):
            published_preview(self.ledger, self.item["id"], result["sha256"], result["asset_url"], before=0)
        with self.assertRaisesRegex(LedgerError, "earlier same-issue"):
            published_preview(self.ledger, self.item["id"], "a" * 64, result["asset_url"], before=self.ledger.clock())
        from test_ledger import OTHER
        self.ledger.observe_issue(issue(id=OTHER))
        self.ledger.ensure_session("other-ui-session", OTHER, delegation=True)
        other = self.ledger.create_work_item(issue_id=OTHER, session_id="other-ui-session", skill="fgui", execution_contract='staged-v1')
        with self.assertRaisesRegex(LedgerError, "earlier same-issue"):
            published_preview(self.ledger, other["id"], result["sha256"], result["asset_url"], before=self.ledger.clock())

    def test_transfer_evidence_must_match_the_immutable_image_before_recording(self):
        original = self.api.upload_image
        def different(*args, **kwargs):
            return {**original(*args, **kwargs), "sha256": "a" * 64}
        self.api.upload_image = different
        with self.assertRaisesRegex(LedgerError, "immutable image"):
            self.upload()
        self.assertEqual(len(self.stored_uploads()), 1)
        self.assertIsNone(self.ledger.connection.execute(
            "SELECT 1 FROM audit WHERE item_id=? AND kind='fgui_preview_uploaded'", (self.item["id"],)).fetchone())

    def test_current_work_owner_still_uploads_with_delegation_and_has_no_legacy_receipt(self):
        self.ledger.connection.execute(
            "UPDATE work_items SET skill='work',execution_contract='single-worker-v1' WHERE id=?", (self.item["id"],))
        self.config.enabled_skills = ["chat", "work"]
        result = self.upload()
        row = self.ledger.connection.execute(
            "SELECT kind,details FROM audit WHERE item_id=? AND kind LIKE '%uploaded'", (self.item["id"],)).fetchone()
        self.assertEqual((row["kind"], json.loads(row["details"])), ("scene_preview_uploaded", result))
        self.raw["delegate_id"] = None
        self.write_issue()
        with patch.object(self.api, "upload_image") as upload, self.assertRaisesRegex(LedgerError, "delegated"):
            self.upload()
        upload.assert_not_called()

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
        for skill, root in (("fix", "farmgui"), ("feature", "farmgui"), ("fgui", "Farm-Client")):
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
        self.assertIsNone(self.ledger.connection.execute(
            "SELECT 1 FROM audit WHERE item_id=? AND kind='fgui_preview_uploaded'", (self.item["id"],)).fetchone())


class ChatImageCliTests(ImageCliFixture):
    SKILL = "chat"
    ENABLED = ["chat"]
    AUTHORITY = "mention"
    DELEGATED = False

    def test_mention_chat_uploads_png_and_jpeg_and_embeds_both_in_its_session_answer(self):
        results = [self.upload()]
        jpeg = self.image.with_suffix(".jpg")
        with Image.open(self.image) as image:
            image.save(jpeg, format="JPEG")
        self.args.file = str(jpeg)
        results.append(self.upload())
        self.assertEqual([r["content_type"] for r in results], ["image/png", "image/jpeg"])
        self.assertEqual(len(self.stored_uploads()), 2)
        audit = self.ledger.connection.execute(
            "SELECT kind,details FROM audit WHERE item_id=? AND kind LIKE '%uploaded' ORDER BY id",
            (self.item["id"],)).fetchall()
        self.assertEqual([row["kind"] for row in audit], ["chat_image_uploaded"] * 2)
        self.assertEqual([json.loads(row["details"]) for row in audit], results)
        self.assertNotIn(self.token, json.dumps(results))
        self.assertTrue(all("?" not in r["asset_url"] for r in results))
        # Use the documented token-file/activity/finish route, without issue comments.
        token_file = self.image.parent / "token"
        token_file.write_text(self.token, encoding="utf-8")
        body = "项目素材对照图（不是运行截图）\n\n" + "\n\n".join(
            f"![素材 {n}]({result['asset_url']})" for n, result in enumerate(results, 1))
        response = self.image.parent / "response.md"
        response.write_text(body, encoding="utf-8")
        args = parser().parse_args(["--db", str(self.paths.ledger), "activity", "--item", self.item["id"],
            "--token-file", str(token_file), "--type", "response", "--body-file", str(response)])
        run(args, self.ledger, self.factory)
        finish = self.image.parent / "finish.json"
        finish.write_text(json.dumps({"summary": "Images provided", "verification": "Asset crops only",
                                      "prs": [], "comment_action_id": None}), encoding="utf-8")
        args = parser().parse_args(["--db", str(self.paths.ledger), "finish", "--item", self.item["id"],
            "--token-file", str(token_file), "--outcome", "delivered", "--input", str(finish)])
        run(args, self.ledger, self.factory)
        calls = [json.loads(s) for s in (self.api.directory / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
        responses = [call for call in calls if call["method"] == "create_activity"]
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0]["session_id"], self.item["session_id"])
        self.assertEqual(responses[0]["content"], {"type": "response", "body": body})
        self.assertFalse(set(call["method"] for call in calls) & {"create_comment", "set_issue_workflow_state"})
        self.assertEqual(self.ledger.item(self.item["id"])["state"], "delivered")

    def test_operator_chat_needs_no_write_delegation(self):
        self.ledger.connection.execute("UPDATE work_items SET authority='operator' WHERE id=?", (self.item["id"],))
        self.upload()
        self.assertEqual(len(self.stored_uploads()), 1)

    def test_delegation_authorized_chat_requires_current_delegation(self):
        self.ledger.connection.execute("UPDATE work_items SET authority='delegation' WHERE id=?", (self.item["id"],))
        with self.assertRaisesRegex(LedgerError, "delegated"):
            self.upload()
        self.assertFalse(self.stored_uploads())
        self.raw["delegate_id"] = self.api.app_user_id
        self.write_issue()
        self.upload()
        self.assertEqual(len(self.stored_uploads()), 1)

    def test_disabled_chat_profile_refuses_before_disk_or_network(self):
        self.config.enabled_skills = ["work"]
        with patch("agent.preview_upload.read_image") as read, self.assertRaisesRegex(LedgerError, "chat enablement"):
            self.upload()
        read.assert_not_called()
        self.factory.assert_not_called()

    def test_wrong_ledger_and_expired_chat_claim_refuse_before_disk_or_network(self):
        self.args.db = str(self.root / "foreign.sqlite3")
        with patch("agent.preview_upload.read_image") as read, self.assertRaisesRegex(LedgerError, "configured host ledger"):
            self.upload()
        read.assert_not_called()
        self.args.db = str(self.paths.ledger)
        self.ledger.connection.execute("UPDATE work_items SET lease_expires_at=0 WHERE id=?", (self.item["id"],))
        with patch("agent.preview_upload.read_image") as read, self.assertRaisesRegex(LedgerError, "lease expired"):
            self.upload()
        read.assert_not_called()
        self.factory.assert_not_called()

    def test_closed_or_archived_issue_never_uploads_even_for_a_mention(self):
        for change in ({"archived": True}, {"status_type": "completed"}, {"status_type": "canceled"}):
            self.raw = issue(**change)
            self.write_issue()
            with self.subTest(change=change), self.assertRaisesRegex(LedgerError, "open issue"):
                self.upload()
            self.assertFalse(self.stored_uploads())

    def test_withdrawn_chat_refuses_before_disk_or_network(self):
        self.ledger.flag_withdrawal(self.item["id"], "superseded", self.ledger.clock() + 60)
        with patch("agent.preview_upload.read_image") as read, self.assertRaises(LedgerError):
            self.upload()
        read.assert_not_called()
        self.factory.assert_not_called()

    # These same boundary checks must hold for the new chat path.
    test_wrong_or_cancelled_claim_refuses_before_disk_or_api = PreviewCliTests.test_wrong_or_cancelled_claim_refuses_before_disk_or_api
    test_other_item_state_and_invalid_image_refuse_before_api = PreviewCliTests.test_other_item_state_and_invalid_image_refuse_before_api
    test_stop_during_image_validation_prevents_api_construction = PreviewCliTests.test_stop_during_image_validation_prevents_api_construction

    def test_stop_after_put_cannot_record_a_chat_upload(self):
        PreviewCliTests.test_stop_after_put_never_returns_a_success_result_and_preserves_the_uploaded_fixture(self)
        self.assertIsNone(self.ledger.connection.execute(
            "SELECT 1 FROM audit WHERE item_id=? AND kind='chat_image_uploaded'", (self.item["id"],)).fetchone())


if __name__ == "__main__":
    unittest.main()
