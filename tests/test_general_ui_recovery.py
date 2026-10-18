"""Retained certified UI installs can be freshly reviewed after a general-job Stop."""
import hashlib
import json
import shutil
import unittest
from unittest.mock import patch

from agent.fgui_client import install, installation_history, installation_records
from agent.fgui_install import apply_install
from agent.fgui_records import _canonical
from agent.fgui_scope import verify_candidate
from agent.fgui_workflow import UiWorkflow
from agent.ledger import LedgerError
from agent.workflow import delegate_task
import test_fgui_client_workflow as client_fixtures


class GeneralUiRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = f = client_fixtures.UiClientWorkflowTests()
        f.internal = True
        f.setUp()
        self.addCleanup(f.doCleanups)
        self.ledger, self.source = f.ledger, f.source
        self.old_task, self.old_parent = f.item, f.parent
        self.original_base = self.ledger.item(f.item)["task_client_base"]
        self.first = f.installed()
        self.source.commit(f.client)
        self.interrupted_head = self.source.git(f.client, "rev-parse", "HEAD")

    def successor(self, key="ui"):
        f, source = self.fixture, self.source
        old_owner = f.parent
        self.ledger.cancel(old_owner, "Stop before UI delivery")
        self.ledger.record_cleanup(f.item, {}, done=True)
        self.ledger.record_cleanup(old_owner, {}, done=True)
        parent = self.ledger.create_work_item(issue_id=self.ledger.item(f.item)["issue_id"], session_id="ui-session",
                                             skill="work", target=self.ledger.item(old_owner)["target"], execution_contract='staged-v1')
        parent_token = self.ledger.claim(parent["id"], worker_id="successor-general")["token"]
        self.ledger.checkpoint(parent["id"], parent_token, {"plan": {"stages": {"A": "done"}}})
        task = delegate_task(self.ledger, parent["id"], parent_token, {
            "key": key, "capability": "fgui", "instructions": "Revalidate the entire retained UI candidate."})
        self.assertNotIn('_contract', task['task_request'])
        self.assertEqual(delegate_task(self.ledger, parent['id'], parent_token, task['task_request'])['id'], task['id'])
        self.assertIsNotNone(self.ledger.connection.execute(
            "SELECT 1 FROM audit WHERE item_id=? AND kind='presentation_legacy_client_recovery'", (task['id'],)).fetchone())
        # Rehouse the one retained branch without resetting its interrupted HEAD.
        for repo, previous in (("farmgui", source.path), ("Farm-Client", f.client)):
            moved = source.paths.worktrees / parent["id"] / repo
            moved.parent.mkdir(parents=True, exist_ok=True)
            source.git(source.trees.clone_path(repo), "worktree", "move", str(previous), str(moved))
            if repo == "farmgui":
                source.path = moved
            else:
                f.client = moved
        f.item, f.parent = task["id"], parent["id"]
        source.item = task["id"]
        source.token = self.ledger.claim(source.item, worker_id="fresh-ui-source")["token"]
        source.plan = {"prs": {"farmgui": [{"role": "issue", "branch": source.branch, "head": source.head,
                                            "pr": {"url": source.url, "state": "draft", "merge": None}}]}}
        preview = source.paths.runs / source.item / "fresh preview.png"
        preview.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source.preview, preview)
        source.preview = preview
        source.fixture.args.item = source.item
        source.fixture.args.token = source.token
        source.fixture.args.file = str(preview)
        source.uploaded = source.fixture.upload()
        source.save_plan()
        review = source.review(2)
        source.approve(review)
        source.export_color = "red"
        f.exported = source.export(review)
        source.plan["ui"]["receipt_id"] = f.exported["receipt_id"]
        source.save_plan()
        self.ledger.set_worker(source.item, 201, "successor fixture")
        self.ledger.checkpoint(source.item, source.token, {"plan": source.plan, "handoff": {
            "facts": [], "hypotheses": [], "checks": [], "repositories": [], "next_actions": ["Fresh UI installation"]}})
        self.ledger.handoff_repository(source.item, source.token, "Farm-Client", skill=source.skill)
        self.ledger.complete_repository_handoff(source.item, 201, skill=source.skill)
        row = self.ledger.item(source.item)
        self.ledger.pin_staged_client(source.item, row["generation"], self.ledger.item(parent["id"])["target"],
                                     branch=source.branch)
        head = source.git(f.client, "rev-parse", "HEAD")
        pinned = self.ledger.pin_task_client_base(source.item, row["generation"], head)
        source.token = self.ledger.claim(source.item, worker_id="fresh-ui-client")["token"]
        f.workflow = UiWorkflow(self.ledger, source.item, source.token, source.config, source.skill,
                                lambda: source.api, stage="Farm-Client")
        return pinned

    def test_same_key_reinstalls_without_hiding_the_old_diff(self):
        self.check_reinstall("ui")

    def test_fresh_key_reinstalls_without_hiding_the_old_diff(self):
        # A different key must retain the old baseline too; the recovery audit
        # identifies the actual interrupted task independently of worker naming.
        self.check_reinstall("renamed-ui")

    def check_reinstall(self, key):
        pinned = self.successor(key)
        f = self.fixture
        self.assertEqual(pinned["task_client_base"], self.original_base)
        self.assertNotEqual(pinned["task_client_base"], self.interrupted_head)
        self.assertEqual(installation_history(self.ledger, f.item), [self.first])
        self.assertEqual(installation_records(self.ledger, f.item), [])
        with self.assertRaisesRegex(LedgerError, "completed controller-certified"):
            verify_candidate(f.workflow, f.exported["receipt_id"])
        fresh = install(f.workflow, f.exported["receipt_id"])
        self.assertEqual(fresh["baseline"], self.original_base)
        self.assertEqual(fresh["initial_head"], self.interrupted_head)
        self.assertNotEqual(fresh["receipt_id"], self.first["receipt_id"])
        self.assertNotEqual(fresh["files"], self.first["files"])
        self.source.commit(f.client)
        scope = verify_candidate(f.workflow, fresh["receipt_id"])
        self.assertEqual(scope["baseline"], self.original_base)
        self.assertIn("Assets/GameRes/FairyRes/One/One_old.mp3", scope["changed_client_files"])
        self.assertNotIn("scene.prefab", scope["changed_client_files"])
        self.assertEqual(installation_records(self.ledger, self.old_task), [self.first])
        with self.assertRaisesRegex(LedgerError, "current controller verification"):
            self.ledger.finish(f.item, self.source.token, "delivered", {"summary": "Reinstalled", "verification": "scope only",
                "prs": [self.source.url, "https://github.com/example/Farm-Client/pull/2"]})

    def test_recovery_refuses_dirty_partial_install_without_resetting_it(self):
        self.successor()
        path = self.fixture.client / "unfinished.prefab"
        path.write_text("Uncommitted interrupted content", encoding="utf-8")
        with patch("agent.fgui_client.apply_install") as apply, self.assertRaisesRegex(LedgerError, "clean owned Client"):
            install(self.fixture.workflow, self.fixture.exported["receipt_id"])
        apply.assert_not_called()
        self.assertEqual(path.read_text(encoding="utf-8"), "Uncommitted interrupted content")
        self.assertEqual(installation_records(self.ledger, self.fixture.item), [])

    def test_stop_during_successor_reinstall_preserves_the_actual_partial_journal(self):
        self.successor()
        f = self.fixture

        def interrupted(plan, recovery, *, fence):
            def stop_after_mutation():
                journal = recovery / "journal.json"
                if journal.exists() and json.loads(journal.read_text(encoding="utf-8"))["completed"]:
                    self.ledger.cancel(f.parent, "Stop during successor UI installation")
                fence()
            return apply_install(plan, recovery, fence=stop_after_mutation)

        with patch("agent.fgui_client.apply_install", side_effect=interrupted), self.assertRaises(LedgerError):
            install(f.workflow, f.exported["receipt_id"])
        journals = list((f.paths.runs / f.item / "ui-installations").glob("*/journal.json"))
        self.assertEqual(len(journals), 1)
        saved = json.loads(journals[0].read_text(encoding="utf-8"))
        self.assertEqual(saved["state"], "interrupted")
        self.assertTrue(saved["completed"])
        self.assertTrue(any(path.name.isdigit() for path in journals[0].parent.iterdir()))
        self.assertNotEqual(self.source.git(f.client, "status", "--porcelain"), "")
        self.assertEqual(installation_records(self.ledger, f.item), [])
        self.assertEqual(installation_records(self.ledger, self.old_task), [self.first])

    def test_recovery_refuses_uncertified_committed_gameplay(self):
        self.successor()
        path = self.fixture.client / "Gameplay.cs"
        path.write_text("Outside UI scope", encoding="utf-8")
        self.source.commit(self.fixture.client)
        with patch("agent.fgui_client.apply_install") as apply, self.assertRaisesRegex(LedgerError, "outside certified"):
            install(self.fixture.workflow, self.fixture.exported["receipt_id"])
        apply.assert_not_called()
        self.assertTrue(path.exists())

    def test_recovery_refuses_damaged_or_other_baseline_installation_evidence(self):
        self.successor()
        self.ledger.connection.execute("UPDATE audit SET details=? WHERE item_id=? AND kind='fgui_install_complete'",
            (json.dumps({**self.first, "installation_sha256": "0" * 64}), self.old_task))
        with patch("agent.fgui_client.apply_install") as apply, self.assertRaisesRegex(LedgerError, "damaged controller evidence"):
            install(self.fixture.workflow, self.fixture.exported["receipt_id"])
        apply.assert_not_called()
        forged = {**self.first, "baseline": "0" * 40}
        forged.pop("installation_sha256")
        forged["installation_sha256"] = hashlib.sha256(_canonical(forged).encode("utf-8")).hexdigest()
        self.ledger.connection.execute("UPDATE audit SET details=? WHERE item_id=? AND kind='fgui_install_complete'",
            (json.dumps(forged), self.old_task))
        with self.assertRaisesRegex(LedgerError, "baseline or branch"):
            install(self.fixture.workflow, self.fixture.exported["receipt_id"])

    def test_second_stop_before_reinstall_keeps_the_original_certified_install_history(self):
        self.successor("renamed-ui")
        self.assertEqual(installation_records(self.ledger, self.fixture.item), [])
        self.successor("third-ui")
        self.assertEqual(installation_history(self.ledger, self.fixture.item), [self.first])
        fresh = install(self.fixture.workflow, self.fixture.exported["receipt_id"])
        self.assertEqual(fresh["baseline"], self.original_base)


if __name__ == "__main__":
    unittest.main()
