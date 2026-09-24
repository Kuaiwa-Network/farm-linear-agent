"""snapshot_connection reads a ledger without creating, migrating or writing it."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent.ledger import Ledger
from agent.readonly_db import snapshot_connection


class SnapshotConnectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # Spaces and non-ASCII: the URI form must percent-encode them on every OS.
        self.path = Path(self.tmp.name) / "state 状态" / "ledger.sqlite3"

    def test_a_missing_ledger_is_not_created(self):
        with self.assertRaises(sqlite3.OperationalError):
            with snapshot_connection(self.path):
                pass
        self.assertFalse(self.path.exists())

    def test_rows_read_by_name_and_writes_are_refused(self):
        Ledger(self.path).close()
        before = self.path.read_bytes()
        with snapshot_connection(self.path) as db:
            tables = {row["name"] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("work_items", tables)
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("DELETE FROM work_items")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.path.with_name(self.path.name + "-journal").exists())

    def test_the_connection_is_closed_after_the_block(self):
        Ledger(self.path).close()
        with snapshot_connection(self.path) as db:
            pass
        with self.assertRaises(sqlite3.ProgrammingError):
            db.execute("SELECT 1")
