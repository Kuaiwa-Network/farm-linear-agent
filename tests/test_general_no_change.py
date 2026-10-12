"""UI no-change tasks cannot release dirty or changed Client candidates."""
import unittest

from agent.fgui_delivery import verify_no_change
from agent.ledger import LedgerError
from agent.worktrees import WorktreeError
import test_fgui_client_workflow as fixtures


class GeneralUiNoChangeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture = fixtures.UiClientWorkflowTests()
        fixture.internal = True
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.source = fixture.source
        # Represent source delivered before this no-change task's first entry.
        # Later source commits must remain visible against this controller pin.
        self.source.ledger.note(fixture.item, "task_source_entry", details={
            "commit": self.source.head, "branch": self.source.branch, "predecessor_tasks": []})

    def verify(self):
        f = self.fixture
        return verify_no_change(f.ledger, f.item, f.source.token, f.source.config,
                                lambda: f.source.api, "Existing presentation is sufficient")

    def test_clean_owned_source_and_unchanged_client_record_exact_heads(self):
        proof = self.verify()
        f = self.fixture
        self.assertEqual(proof["heads"]["Farm-Client"], f.ledger.item(f.item)["task_client_base"])
        self.assertEqual(set(proof["heads"]), {"farmgui", "Farm-Client"})
        self.assertEqual(proof["branches"]["Farm-Client"], f.source.branch)

    def test_dirty_source_or_client_cannot_certify_no_change(self):
        for path in (self.source.path / "untracked.xml", self.fixture.client / "untracked.prefab"):
            with self.subTest(path=path):
                path.write_text("Uncommitted task output", encoding="utf-8")
                with self.assertRaises(WorktreeError):
                    self.verify()
                path.unlink()

    def test_committed_installed_ui_output_is_a_real_change(self):
        self.fixture.installed()
        self.source.commit(self.fixture.client)
        with self.assertRaisesRegex(LedgerError, "zero Client diff"):
            self.verify()

    def test_clean_committed_source_edit_cannot_be_called_no_change(self):
        panel = self.source.path / "assets/One/Panel.xml"
        panel.write_text('<component size="7,2"/>', encoding="utf-8")
        self.source.commit(self.source.path)
        with self.assertRaisesRegex(LedgerError, "zero source diff"):
            self.verify()

    def test_alternate_issue_branch_cannot_replace_source_entry_branch(self):
        self.source.git(self.source.path, "switch", "-c", self.source.branch + "-alternate")
        with self.assertRaisesRegex(WorktreeError, "branch"):
            self.verify()

    def test_recorded_source_branch_disagreement_is_refused(self):
        self.source.plan["prs"]["farmgui"][0]["branch"] = self.source.branch + "-alternate"
        self.source.save_plan()
        with self.assertRaisesRegex(LedgerError, "recorded source branch"):
            self.verify()

    def test_missing_controller_source_entry_cannot_certify_no_change(self):
        self.source.ledger.connection.execute("DELETE FROM audit WHERE item_id=? AND kind='task_source_entry'",
                                             (self.fixture.item,))
        with self.assertRaisesRegex(LedgerError, "controller-recorded source entry"):
            self.verify()
