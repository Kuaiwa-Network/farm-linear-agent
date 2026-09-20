"""Shared memory on real, temporary SQLite files; no external services."""
import threading
from pathlib import Path

from agent.ledger import Ledger, LedgerError
from test_ledger import LedgerBase, ISSUE, OTHER, issue

NOTE = {"request_id": "note-1", "title": "Check the test baseline", "category": "lesson",
        "scope": "Farm-Client", "body": "Compare failures against the same pinned build.",
        "source": "report:test-run", "build_commit": "a" * 40}


def replacement(note, **changes):
    return {**{k: NOTE[k] for k in ("title", "category", "scope", "body", "source", "build_commit")},
            "id": note["id"], "expected_revision": note["revision"], **changes}


class MemoryLedgerTests(LedgerBase):
    def claim(self):
        item = self.new_item()
        return item["id"], self.ledger.claim(item["id"], worker_id="writer")["token"]

    def test_persistence_and_stale_update(self):
        item, token = self.claim()
        note = self.ledger.memory_save(item, token, NOTE)
        reopened = self.open_ledger()
        self.assertEqual(reopened.memory_read(item, token, note["id"])["body"], NOTE["body"])
        self.assertEqual(note["created_by_item"], item)
        self.assertEqual(note["created_at"], self.now)
        self.assertEqual(note["revision"], 1)
        update = replacement(note, body="Check each build.")
        self.assertEqual(reopened.memory_save(item, token, update)["revision"], 2)
        with self.assertRaisesRegex(LedgerError, "revision"):
            self.ledger.memory_save(item, token, update)
        listed = self.ledger.memory_list(item, token)
        self.assertEqual(len(listed), 1)
        self.assertNotIn("body", listed[0])
        self.assertNotIn("source", listed[0])

    def test_replay_after_update_and_forget_never_restores_content(self):
        item, token = self.claim()
        saved = self.ledger.memory_save(item, token, NOTE)
        self.assertFalse(saved["replayed"])
        edited = self.ledger.memory_save(item, token, replacement(saved, body="Current text."))
        replay = self.ledger.memory_save(item, token, NOTE)
        self.assertEqual((replay["id"], replay["body"], replay["replayed"]),
                         (saved["id"], "Current text.", True))
        with self.assertRaisesRegex(LedgerError, "request"):
            self.ledger.memory_save(item, token, {**NOTE, "body": "different"})
        forgotten = self.ledger.memory_forget(item, token, saved["id"], edited["revision"], "obsolete")
        self.assertEqual(forgotten["revision"], 3)
        self.assertEqual(self.ledger.memory_list(item, token), [])
        replay = self.ledger.memory_save(item, token, NOTE)
        self.assertEqual(replay, {"id": saved["id"], "revision": 3, "deleted": True, "replayed": True})
        with self.assertRaises(LedgerError):
            self.ledger.memory_read(item, token, saved["id"])
        row = dict(self.ledger.connection.execute("SELECT * FROM memories").fetchone())
        for k in ("title", "body", "source"):
            self.assertEqual(row[k], "")
        self.assertIsNone(row["build_commit"])
        audit = str([tuple(r) for r in self.ledger.connection.execute("SELECT * FROM audit WHERE kind LIKE 'memory%'")])
        for content in (NOTE["title"], NOTE["body"], NOTE["source"], token, "Current text."):
            self.assertNotIn(content, audit)

    def test_claim_required_for_every_operation_and_replays(self):
        item, token = self.claim()
        saved = self.ledger.memory_save(item, token, NOTE)
        def calls(presented):
            return [lambda: self.ledger.memory_list(item, presented),
                    lambda: self.ledger.memory_read(item, presented, saved["id"]),
                    lambda: self.ledger.memory_save(item, presented, NOTE),
                    lambda: self.ledger.memory_forget(item, presented, saved["id"], 1, "obsolete")]
        self.ledger.observe_issue(issue(id=OTHER))
        self.ledger.ensure_session("s2", OTHER, delegation=True)
        other = self.ledger.create_work_item(issue_id=OTHER, session_id="s2", skill="chat")
        wrong = self.ledger.claim(other["id"], worker_id="other")["token"]
        for presented in ("bad", wrong):
            for call in calls(presented):
                with self.assertRaises(LedgerError): call()
        self.now += 61
        for call in calls(token):
            with self.assertRaises(LedgerError): call()
        self.now -= 61
        self.ledger.cancel(item, "test")
        for call in calls(token):
            with self.assertRaises(LedgerError): call()
        self.ledger.retry(item, "test")
        for call in calls(token):
            with self.assertRaises(LedgerError): call()
        self.assertEqual(self.ledger.memory_admin("read", note_id=saved["id"])["revision"], 1)

    def test_operator_and_request_scope(self):
        item, token = self.claim()
        first = self.ledger.memory_save(item, token, NOTE)
        second = self.ledger.memory_admin("save", value=NOTE)
        self.assertNotEqual(first["id"], second["id"])
        self.assertIsNone(second["created_by_item"])
        self.assertEqual(second["actor_kind"], "operator")
        self.ledger.observe_issue(issue(id=OTHER))
        self.ledger.ensure_session("s2", OTHER, delegation=True)
        other = self.ledger.create_work_item(issue_id=OTHER, session_id="s2", skill="chat")
        other_token = self.ledger.claim(other["id"], worker_id="other")["token"]
        third = self.ledger.memory_save(other["id"], other_token, NOTE)
        self.assertNotEqual(third["id"], first["id"])
        edit = self.ledger.memory_admin("save", value=replacement(first, body="Corrected by operator"))
        self.assertIsNone(edit["updated_by_item"])
        self.assertEqual(edit["created_by_item"], item)
        self.ledger.memory_admin("forget", note_id=edit["id"], expected_revision=2, reason="obsolete")
        self.assertEqual(len(self.ledger.memory_admin("list")), 2)

    def test_validation_and_utf8_limits(self):
        invalid = [None, [], {**NOTE, "category": "gameplay-rule"}, {**NOTE, "scope": "elsewhere"},
                   {**NOTE, "title": "x\ny"}, {**NOTE, "title": "x" * 121},
                   {**NOTE, "title": "x\x00y"}, {**NOTE, "title": "x\u2028y"},
                   {**NOTE, "source": "中" * 342}, {**NOTE, "body": "中" * 2731},
                   {**NOTE, "build_commit": "A" * 40}, {**NOTE, "body": " "},
                   {**NOTE, "source": ""}, {**NOTE, "created_by_item": "spoof"},
                   {**NOTE, "request_id": "../outside"}, {**NOTE, "request_id": True}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(LedgerError):
                self.ledger.memory_admin("save", value=value)
        saved = self.ledger.memory_admin("save", value={**NOTE, "body": "中" * 2730 + "xx"})
        self.assertEqual(len(saved["body"].encode()), 8192)
        for value in (replacement(saved, expected_revision=True), replacement(saved, id="../../outside"),
                      replacement(saved, expected_revision=0), replacement(saved, unknown="x")):
            with self.assertRaises(LedgerError): self.ledger.memory_admin("save", value=value)
        for reason in ("", " " * 2, "x" * 501):
            with self.assertRaises(LedgerError):
                self.ledger.memory_admin("forget", note_id=saved["id"], expected_revision=1, reason=reason)
        with self.assertRaisesRegex(LedgerError, "revision"):
            self.ledger.memory_admin("forget", note_id=saved["id"], expected_revision=2, reason="obsolete")

    def concurrent(self, values):
        barrier = threading.Barrier(len(values))
        results = []
        def worker(value):
            db = None
            try:
                db = Ledger(self.path)
                barrier.wait(timeout=5)
                results.append(db.memory_admin("save", value=value))
            except BaseException as exc:
                results.append(exc)
            finally:
                if db: db.close()
        threads = [threading.Thread(target=worker, args=(v,)) for v in values]
        for t in threads: t.start()
        for t in threads:
            t.join(timeout=15)
            self.assertFalse(t.is_alive())
        return results

    def test_concurrent_creates_and_updates(self):
        results = self.concurrent([{**NOTE, "request_id": "a"}, {**NOTE, "request_id": "b"}])
        self.assertTrue(all(isinstance(r, dict) for r in results), results)
        self.assertEqual(len(self.ledger.memory_rows()), 2)
        same = results[0]
        results = self.concurrent([replacement(same, body="a"), replacement(same, body="b")])
        self.assertEqual(sum(isinstance(r, dict) for r in results), 1, results)
        self.assertEqual(sum(isinstance(r, LedgerError) for r in results), 1, results)

    def test_concurrent_capacity_and_replay_at_capacity(self):
        for i in range(199): self.ledger.memory_admin("save", value={**NOTE, "request_id": str(i)})
        results = self.concurrent([{**NOTE, "request_id": "a"}, {**NOTE, "request_id": "b"}])
        self.assertEqual(sum(isinstance(r, dict) for r in results), 1, results)
        self.assertEqual(sum(isinstance(r, LedgerError) for r in results), 1, results)
        self.assertEqual(len(self.ledger.memory_rows()), 200)
        self.assertTrue(self.ledger.memory_admin("save", value={**NOTE, "request_id": "0"})["replayed"])

    def test_old_ledger_migration_preserves_existing_rows(self):
        item, token = self.claim()
        self.ledger.connection.execute("DROP TABLE IF EXISTS memory_requests")
        self.ledger.connection.execute("DROP TABLE IF EXISTS memories")
        before = [tuple(r) for r in self.ledger.connection.execute("SELECT * FROM audit")]
        reopened = self.open_ledger()
        self.assertEqual(reopened.memory_list(item, token), [])
        self.assertEqual(reopened.item(item)["state"], "running")
        self.assertEqual(reopened.issue(ISSUE)["title"], issue()["title"])
        self.assertEqual(before, [tuple(r) for r in reopened.connection.execute("SELECT * FROM audit")])
