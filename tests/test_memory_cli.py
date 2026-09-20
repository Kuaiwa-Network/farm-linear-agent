"""Memory commands execute in real subprocesses against temporary ledgers."""
import json
import unittest

import test_cli
from agent.ledger import Ledger
from test_memory import NOTE, replacement


class MemoryCliTests(unittest.TestCase):
    # Share fixture functions, not CliTests' test methods.
    setUp = test_cli.CliTests.setUp
    run_cli = test_cli.CliTests.run_cli
    json_file = test_cli.CliTests.json_file
    calls = test_cli.CliTests.calls
    seeded_item = test_cli.CliTests.seeded_item

    def claim(self):
        item = self.seeded_item(skill="chat")
        token = self.run_cli("claim", "--item", item, "--worker-id", "memory-test")["token"]
        path = self.root / "token"
        path.write_text(token)
        path.chmod(0o600)
        return item, path

    def lease(self, item):
        db = Ledger(self.db)
        try: return db.item(item)["lease_expires_at"]
        finally: db.close()

    def test_worker_round_trip_no_linear_calls(self):
        item, token = self.claim()
        scope = ("--item", item, "--token-file", str(token))
        before = self.lease(item)
        saved = self.run_cli("memory-save", *scope, "--input", self.json_file("note.json", NOTE))
        self.assertEqual(self.run_cli("memory-read", *scope, "--id", saved["id"])["body"], NOTE["body"])
        listed = self.run_cli("memory-list", *scope)
        self.assertEqual([r["id"] for r in listed], [saved["id"]])
        self.assertNotIn("body", listed[0])
        edited = self.run_cli("memory-save", *scope, "--input", self.json_file("edit.json", replacement(saved, body="updated")))
        self.assertEqual(edited["revision"], 2)
        self.run_cli("memory-forget", *scope, "--id", saved["id"], "--expected-revision", "2", "--reason", "obsolete")
        self.assertEqual(self.run_cli("memory-list", *scope), [])
        self.run_cli("memory-read", *scope, "--id", saved["id"], success=False)
        after = self.lease(item)
        self.assertEqual(before, after)
        self.assertEqual(self.calls(), [])

    def test_refusals_no_token_echo_or_external_calls(self):
        item, token = self.claim()
        scope = ("--item", item, "--token-file", str(token))
        saved = self.run_cli("memory-save", *scope, "--input", self.json_file("note.json", NOTE))
        bad = self.root / "bad-token"
        bad.write_text("private-token-do-not-print")
        malformed = self.root / "malformed.json"
        malformed.write_text("not json")
        huge = self.root / "huge.json"
        huge.write_text(" " * 16385)
        cases = [("memory-list", "--item", item),
                 ("memory-list", "--item", item, "--token-file", str(bad)),
                 ("memory-save", *scope, "--input", str(malformed)),
                 ("memory-save", *scope, "--input", str(huge)),
                 ("memory-save", *scope, "--input", self.json_file("mismatch.json", {**NOTE, "body": "other"})),
                 ("memory-save", *scope, "--input", self.json_file("stale.json", replacement(saved, expected_revision=2))),
                 ("memory-forget", *scope, "--id", saved["id"], "--expected-revision", "1"),
                 ("memory-admin", "read"), ("memory-admin", "save"), ("memory-admin", "forget")]
        for args in cases:
            with self.subTest(command=args):
                result = self.run_cli(*args, success=False)
                self.assertNotIn(bad.read_text(), result.stderr + result.stdout)
                self.assertNotIn(token.read_text(), result.stderr + result.stdout)
        db = Ledger(self.db)
        try: db.connection.execute("UPDATE work_items SET lease_expires_at=0 WHERE id=?", (item,))
        finally: db.close()
        for name, tail in (("memory-list", ()), ("memory-read", ("--id", saved["id"])),
                           ("memory-save", ("--input", str(self.root / "note.json"))),
                           ("memory-forget", ("--id", saved["id"], "--expected-revision", "1", "--reason", "obsolete"))):
            self.run_cli(name, *scope, *tail, success=False)
        self.assertEqual(self.calls(), [])

    def test_admin_can_correct_and_forget_without_claim(self):
        saved = self.run_cli("memory-admin", "save", "--input", self.json_file("note.json", NOTE))
        self.assertEqual(saved["actor_kind"], "operator")
        self.assertIsNone(saved["created_by_item"])
        self.assertEqual(self.run_cli("memory-admin", "read", "--id", saved["id"])["body"], NOTE["body"])
        edited = self.run_cli("memory-admin", "save", "--input", self.json_file("edit.json", replacement(saved, body="new")))
        self.assertEqual(edited["revision"], 2)
        self.run_cli("memory-admin", "forget", "--id", saved["id"], "--expected-revision", "2", "--reason", "obsolete")
        self.assertEqual(self.run_cli("memory-admin", "list"), [])
        self.assertEqual(self.calls(), [])

    def test_prune_requires_offline_directory_and_no_active_queue(self):
        from agent.memory import publish_snapshot
        runs = self.root / "runs"
        runs.mkdir()
        snapshot = publish_snapshot(self.root / "memory", [])
        item = self.seeded_item()
        result = self.run_cli("memory-admin", "prune-snapshots", "--runs-root", str(runs), success=False)
        self.assertIn("stop", result.stderr.lower())
        self.run_cli("cancel", "--item", item, "--reason", "maintenance")
        self.run_cli("memory-admin", "prune-snapshots", "--runs-root", "relative", success=False)
        self.run_cli("memory-admin", "prune-snapshots", "--runs-root", str(runs / "missing"), success=False)
        result = self.run_cli("memory-admin", "prune-snapshots", "--runs-root", str(runs))
        self.assertEqual(result["removed"], [snapshot["snapshot_id"]])
