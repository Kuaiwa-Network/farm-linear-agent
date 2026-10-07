"""Actual owned source/ledger/preview proofs around a scripted native publisher."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from agent import fgui_publisher
from agent.fgui_approval import source_digest
from agent.fgui_export import snapshot_export
from agent.fgui_publisher import PublishResult, TOOL_FILES
from agent.fgui_records import export_record
from agent.fgui_workflow import UiWorkflow, read_packages
from agent.ledger import LedgerError
from agent.skills import load_skills
from agent.config import ROOT
from agent.__main__ import parser, run
from agent.worktrees import Worktrees
from test_fgui_export import package_bytes
from test_ledger import DESIGNER
import test_preview_cli as preview_fixtures


class UiWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture = preview_fixtures.PreviewCliTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        self.root, self.config, self.paths, self.ledger = fixture.root, fixture.config, fixture.paths, fixture.ledger
        self.item, self.token = fixture.item["id"], fixture.token
        self.api = fixture.api; self.origin = self.root / "origin"; self.origin.mkdir()
        self.git(self.origin, "init", "-q", "-b", "main")
        (self.origin / "assets/One").mkdir(parents=True)
        (self.origin / "assets/One/package.xml").write_text('<packageDescription id="pack0001"/>', encoding="utf-8")
        (self.origin / "assets/One/Panel.xml").write_text('<component size="2,2"/>', encoding="utf-8")
        (self.origin / ".gitignore").write_text('.objs/\n', encoding="utf-8")
        self.commit(self.origin); self.base = self.git(self.origin, "rev-parse", "HEAD")
        self.config.repos = {"farmgui": str(self.origin)}
        self.config.fgui_export = {"executable": str(self.root / "FairyGUI-Editor.exe"),
                                   "tool_sha256": dict.fromkeys(TOOL_FILES, "a" * 64)}
        self.trees = Worktrees(self.paths.repos, self.paths.worktrees, self.config.repos)
        self.branch = "farmbot/farm-1"; self.path = self.trees.add("farmgui", self.item, self.branch)
        (self.path / "assets/One/Panel.xml").write_text('<component size="3,2"/>', encoding="utf-8")
        self.commit(self.path); self.head = self.git(self.path, "rev-parse", "HEAD")
        self.url = "https://github.com/example/farmgui/pull/1"; self.packages = {"One": "pack0001"}
        self.plan = {"prs": {"farmgui": [{"role": "issue", "branch": self.branch, "head": self.head,
                                         "pr": {"url": self.url, "state": "draft", "merge": None}}]}}
        self.save_plan()
        self.skill = replace(load_skills(ROOT / "skills")["fgui"], writes=("farmgui", "Farm-Client"), resources=("unity_slot",))
        self.verifier = Mock()
        self.verifier.verify.side_effect = lambda *a, **kw: {"head": self.head, "repository": "example/farmgui", "base_branch": "main"}
        self.verifier.verify_pr.return_value = self.url
        self.enterContext(patch("agent.fgui_workflow.PublicationVerifier", return_value=self.verifier))
        self.enterContext(patch("agent.fgui_workflow.github_api", return_value={"object": {"sha": self.base}}))
        self.publisher = self.enterContext(patch("agent.fgui_workflow.publish", side_effect=self.publish))
        self.now = 1700000000.25; self.ledger.clock = lambda: self.now
        self.preview = fixture.image; self.uploaded = fixture.upload()

    def git(self, path, *args):
        result = subprocess.run(["git", "-c", "core.hooksPath=NUL", "-c", "core.fsmonitor=false",
            "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", *args], cwd=path,
            capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def commit(self, path):
        self.git(path, "add", "--all"); self.git(path, "commit", "-qm", "UI fixture")

    def save_plan(self):
        self.ledger.checkpoint(self.item, self.token, {"summary": "UI workflow fixture", "plan": self.plan})

    def workflow(self):
        return UiWorkflow(self.ledger, self.item, self.token, self.config, self.skill, lambda: self.api)

    def review(self, number=1):
        return self.workflow().review(packages=self.packages, preview=self.preview,
                                      asset_url=self.uploaded["asset_url"], round=number)

    def approve(self, review, *, post_notice=True):
        identity = {key: review[key] for key in ("round", "head", "source_digest", "preview_sha256")}
        self.plan["ui"] = {**identity, "review_id": review["review_id"], "packages": list(review["changed_packages"])}
        if post_notice:
            request = 'visual-'+str(review["round"])
            notice = self.ledger.prepare_notice(self.item, self.token, "waiting", request,
                "Approximate review: " + review["asset_url"] + "\nSource HEAD: " + review["head"])
            remote = self.api.create_comment(self.fixture.item["issue_id"], notice["body"])
            self.ledger.confirm_notice(self.item, request, remote)
        self.now += .25
        self.ledger.push_inbox(self.item, "Approved; please export this unchanged round.", author=DESIGNER)
        message = self.ledger.issue_context(self.item)["session_messages"][-1]
        self.plan["events"] = [{**identity, "kind": kind, "message_id": message["id"],
                              "created_at": message["created_at"], "author": message["author"]}
                             for kind in ("visual_approved", "export_requested")]
        self.save_plan()

    def publish(self, project, packages, executable, pins, run, **kwargs):
        kwargs["fence"](); source = kwargs["verify_source"]()
        stage = run / "staging"; stage.mkdir(parents=True)
        (stage / "One_fui.bytes").write_bytes(package_bytes())
        Image.new("RGBA", (2, 2), getattr(self, "export_color", "blue")).save(stage / "One_atlas0.png")
        return PublishResult(snapshot_export(stage, packages), source["head"], source_digest(source["files"]),
            tuple(sorted(pins.items())), tuple(sorted({"exit_code": 0, "job_empty": True,
            "job_assigned_before_startup": True, "job_assigned_before_export": True}.items())), "a" * 64)

    def export(self, review):
        return self.workflow().export(review_id=review["review_id"], preview=self.preview)

    def cli(self, command, *options):
        args = parser().parse_args(["--db", str(self.paths.ledger), command, "--item", self.item,
                                   "--token", self.token, *map(str, options)])
        with patch("agent.skills.load_skills", return_value={"fgui": self.skill}):
            return run(args, self.ledger, lambda: self.api)

    def test_review_and_export_cli_use_controller_records_and_actual_package_file(self):
        path = self.paths.runs / self.item / "packages.json"; path.write_text(json.dumps(self.packages), encoding="utf-8")
        review = self.cli("review-ui", "--preview", self.preview, "--asset-url", self.uploaded["asset_url"],
                          "--packages", path, "--round", "1")
        self.approve(review)
        result = self.cli("export-ui", "--preview", self.preview, "--review-id", review["review_id"])
        self.assertEqual(export_record(self.ledger, self.item, result["receipt_id"])["source_pr"], self.url)

    def test_real_publisher_result_certifies_the_same_reviewed_source_digest(self):
        # Keep the real publisher-to-ledger boundary; replace only the licensed
        # native process with a deterministic local export.
        (self.path / "FGUIProject.fairy").write_text('<projectDescription type="Unity"/>', encoding="utf-8")
        (self.path / "settings").mkdir()
        (self.path / "settings/Publish.json").write_text(
            '{"codeGeneration":{"allowGenCode":false}}', encoding="utf-8")
        self.commit(self.path); self.head = self.git(self.path, "rev-parse", "HEAD")
        self.plan["prs"]["farmgui"][0]["head"] = self.head; self.save_plan()
        tools = self.root / "pinned publisher space 验证"; tools.mkdir()
        for name in TOOL_FILES:
            (tools / name).write_bytes(b"local fixture " + name.encode("utf-8"))
        self.config.fgui_export = {"executable": str(tools / TOOL_FILES[0]),
            "tool_sha256": {name: hashlib.sha256((tools / name).read_bytes()).hexdigest() for name in TOOL_FILES}}
        review = self.review(); self.approve(review)
        self.publisher.side_effect = fgui_publisher.publish

        def native_export(command, cwd, run, fence, timeout):
            fence()
            output = Path(command[command.index("-o") + 1])
            (output / "One_fui.bytes").write_bytes(package_bytes())
            Image.new("RGBA", (2, 2), "blue").save(output / "One_atlas0.png")
            (run / "publisher.log").write_text("Publish completed\n", encoding="utf-8")
            return {"exit_code": 0, "job_empty": True,
                    "job_assigned_before_startup": True, "job_assigned_before_export": True}

        with patch.object(fgui_publisher, "_native_windows", return_value=True), \
                patch.object(fgui_publisher, "_windows_run", side_effect=native_export):
            proof = self.export(review)
        self.assertEqual(proof["authority"]["source_digest"], review["source_digest"])
        self.assertEqual(proof["publisher"]["source_digest"], review["source_digest"])
        self.assertEqual(proof["publisher"]["source_head"], review["head"])
        self.assertEqual(export_record(self.ledger, self.item, proof["receipt_id"])["receipt_sha256"],
                         proof["receipt_sha256"])

    def test_older_authoring_manifest_refuses_phase_e_cli_before_github(self):
        self.skill = replace(load_skills(ROOT / "skills")["fgui"], writes=("farmgui",), resources=())
        with self.assertRaisesRegex(LedgerError, "Phase E"):
            self.cli("export-ui", "--preview", self.preview, "--review-id", "a" * 32)
        self.verifier.verify.assert_not_called(); self.publisher.assert_not_called()

    def test_owned_review_real_human_export_and_durable_result(self):
        review = self.review(); self.approve(review); result = self.export(review)
        self.assertEqual(result["authority"]["head"], self.head)
        self.assertEqual(result["authority"]["changed_packages"], self.packages)
        self.assertEqual(result["source_pr"], self.url)
        self.assertEqual(export_record(self.ledger, self.item, result["receipt_id"])["receipt_sha256"], result["receipt_sha256"])
        self.assertNotIn(self.token, json.dumps(result))

    def test_unapproved_review_never_starts_publisher(self):
        review = self.review()
        with self.assertRaises(LedgerError): self.export(review)
        self.publisher.assert_not_called()

    def test_unused_upload_without_confirmed_visual_notice_cannot_authorize_export(self):
        review = self.review(); self.approve(review, post_notice=False)
        with self.assertRaisesRegex(LedgerError, "confirmed visual-round"): self.export(review)
        self.publisher.assert_not_called()

    def test_wrong_claim_disabled_manifest_or_foreign_root_refuses_before_github(self):
        token = self.token; self.token = "foreign"
        with self.assertRaises(LedgerError): self.workflow()
        self.token = token; self.skill = replace(self.skill, writes=("farmgui",), resources=())
        with self.assertRaisesRegex(LedgerError, "Phase E"): self.workflow()
        self.verifier.verify.assert_not_called(); self.publisher.assert_not_called()

    def test_changed_source_cannot_replace_same_review_round(self):
        review = self.review()
        (self.path / "assets/One/Panel.xml").write_text('<component size="4,2"/>', encoding="utf-8")
        self.commit(self.path); self.head = self.git(self.path, "rev-parse", "HEAD")
        self.plan["prs"]["farmgui"][0]["head"] = self.head; self.save_plan()
        with self.assertRaisesRegex(LedgerError, "new numbered round"): self.review()
        self.assertNotEqual(self.review(2)["review_id"], review["review_id"])

    def test_retry_of_identical_review_retains_controller_identity(self):
        first = self.review(); self.assertEqual(self.review(), first)

    def test_source_head_or_image_change_invalidates_export_before_process(self):
        review = self.review(); self.approve(review)
        Image.new("RGB", (4, 3), "red").save(self.preview)
        with self.assertRaisesRegex(LedgerError, "review image differs"): self.export(review)
        self.publisher.assert_not_called()

    def test_missing_uploaded_image_and_incomplete_package_selection_refuse_review(self):
        with self.assertRaisesRegex(LedgerError, "earlier same-issue"):
            self.workflow().review(packages=self.packages, preview=self.preview,
                                   asset_url="https://uploads.linear.app/not-uploaded", round=1)
        self.packages = {}
        with self.assertRaisesRegex(LedgerError, "actually changed"): self.review()
        self.publisher.assert_not_called()

    def test_forged_worker_review_identity_never_starts_publisher(self):
        with self.assertRaisesRegex(LedgerError, "controller record"):
            self.workflow().export(review_id="f" * 32, preview=self.preview)
        self.publisher.assert_not_called()

    def test_stop_after_publisher_preserves_staging_without_record(self):
        review = self.review(); self.approve(review); original = self.publish
        def stopped(*a, **kw):
            result = original(*a, **kw); self.ledger.cancel(self.item, "Stop after publisher"); return result
        self.publisher.side_effect = stopped
        with self.assertRaises(LedgerError): self.export(review)
        self.assertEqual(len(list((self.paths.runs / self.item / "ui-exports").glob("*/staging/One_fui.bytes"))), 1)
        self.assertIsNone(self.ledger.connection.execute("SELECT 1 FROM audit WHERE kind='fgui_export_complete'").fetchone())

    def test_new_human_input_during_publisher_requires_current_interpretation(self):
        review = self.review(); self.approve(review); original = self.publish
        def changed(*a, **kw):
            result = original(*a, **kw); self.ledger.push_inbox(self.item, "Change the art before exporting.", author=DESIGNER); return result
        self.publisher.side_effect = changed
        with self.assertRaisesRegex(LedgerError, "new UI input"): self.export(review)
        self.assertIsNone(self.ledger.connection.execute("SELECT 1 FROM audit WHERE kind='fgui_export_complete'").fetchone())

    def test_uncommitted_source_change_during_publisher_refuses_receipt(self):
        review = self.review(); self.approve(review); original = self.publish
        def changed(*a, **kw):
            result = original(*a, **kw)
            (self.path / "assets/One/Panel.xml").write_text('<component size="5,2"/>', encoding="utf-8")
            return result
        self.publisher.side_effect = changed
        with self.assertRaisesRegex(LedgerError, "uncommitted"): self.export(review)
        self.assertIsNone(self.ledger.connection.execute("SELECT 1 FROM audit WHERE kind='fgui_export_complete'").fetchone())

    def test_withdrawal_after_process_never_records_export_success(self):
        review = self.review(); self.approve(review); original = self.publish
        def withdrawn(*a, **kw):
            result = original(*a, **kw); self.fixture.raw["delegate_id"] = None; self.fixture.write_issue(); return result
        self.publisher.side_effect = withdrawn
        with self.assertRaisesRegex(LedgerError, "open and delegated"): self.export(review)
        self.assertIsNone(self.ledger.connection.execute("SELECT 1 FROM audit WHERE kind='fgui_export_complete'").fetchone())

    def test_package_file_is_bounded_local_and_duplicate_free(self):
        path = self.paths.runs / self.item / "packages.json"; path.write_text(json.dumps(self.packages), encoding="utf-8")
        self.assertEqual(read_packages(path, path.parent), self.packages)
        path.write_text('{"One":"pack0001","One":"pack0001"}', encoding="utf-8")
        with self.assertRaisesRegex(LedgerError, "duplicate"): read_packages(path, path.parent)
        with self.assertRaisesRegex(LedgerError, "private state"): read_packages(path, self.root / "other-state")
