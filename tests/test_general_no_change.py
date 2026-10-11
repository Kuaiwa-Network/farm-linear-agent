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
