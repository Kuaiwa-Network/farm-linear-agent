import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent.ledger import Ledger
from agent.slots import SlotPool, slot_entry
from agent.unity import other_editor_project
from test_slots import FakeMcp


class MultipleSlotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ledger = Ledger(self.root / "ledger.sqlite3")
        self.addCleanup(self.ledger.close)
        self.entries = [slot_entry({"id": f"unity_slot:{n}", "folder": str(self.root / f"slot-{n}")})
                        for n in (1, 2)]
        self.mcp = FakeMcp()
        for e in self.entries:
            self.ledger.ensure_slot(e["id"], kind="unity_slot", host="h", folder=e["folder"],
                                    mcp_address=e["mcp_address"])
            self.mcp.open_folders.add(e["folder"])
        self.pool = SlotPool(self.ledger, Mock(), self.entries, host="h", editors_root=self.root,
                             mcp=self.mcp, editor_pid=lambda folder: 100 if folder in self.mcp.open_folders else None,
                             editor_scan=lambda folder: None)

    def test_external_editor_is_detected_to_preserve_its_shared_broker(self):
        self.pool.editor_scan = lambda folder: other_editor_project(
            folder, allowed_projects=[entry['folder'] for entry in self.entries])
        processes = [(100 + n, e["folder"], False) for n, e in enumerate(self.entries)]
        with patch("agent.unity._editor_processes", return_value=processes):
            self.assertIsNone(self.pool.another_editor_running(self.entries[0]["folder"]))
            self.assertIsNone(self.pool.another_editor_running(self.entries[1]["folder"]))
        processes.append((200, str(self.root / "personal-project"), False))
        with patch("agent.unity._editor_processes", return_value=processes):
            self.assertEqual(self.pool.another_editor_running(self.entries[0]["folder"]), processes[-1][1])

    def test_closing_one_slot_preserves_shared_broker_until_last_editor_closes(self):
        self.pool.close_editor(self.ledger.slot("unity_slot:1"))
        self.assertEqual(self.mcp.calls, [("terminate", "unity_slot:1")])
        self.pool.close_editor(self.ledger.slot("unity_slot:2"))
        self.assertEqual(self.mcp.calls[1], ("terminate", "unity_slot:2"))
        self.assertCountEqual(self.mcp.calls[2:], [("reap_server", "unity_slot:1"), ("reap_server", "unity_slot:2")])

    def test_separate_broker_can_be_reaped_while_other_slot_is_open(self):
        self.ledger.ensure_slot("unity_slot:2", kind="unity_slot", host="h", folder=self.entries[1]["folder"],
                                mcp_address="http://127.0.0.1:9091/mcp")
        self.pool.close_editor(self.ledger.slot("unity_slot:1"))
        self.assertEqual(self.mcp.calls, [("terminate", "unity_slot:1"), ("reap_server", "unity_slot:1")])
