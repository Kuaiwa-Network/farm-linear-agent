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


class MemorySnapshotTests(LedgerBase):
    def setUp(self):
        super().setUp()
        self.root = Path(self.tmp.name) / "memory"
        self.runs = Path(self.tmp.name) / "runs"
        self.runs.mkdir()

    def publish(self):
        from agent import memory
        return memory.publish_snapshot(self.root, self.ledger.memory_rows())

    def prompt(self, name, payload):
        import json
        path = self.runs / name / "attempt" / "prompt.md"
        path.parent.mkdir(parents=True)
        path.write_text("authority\n\n" + json.dumps(payload), encoding="utf-8")
        return path

    def test_index_is_lazy_safe_and_snapshots_are_historical(self):
        import os
        note = self.ledger.memory_admin("save", value={**NOTE, "title": "桌子 [test]|<x>"})
        first = self.publish()
        index = Path(first["index"])
        self.assertTrue(index.is_absolute())
        content = index.read_text()
        self.assertNotIn(NOTE["body"], content)
        self.assertIn(note["id"] + ".md", content)
        self.assertIn("recall", content)
        self.assertEqual(sum(line.startswith("- ") for line in content.splitlines()), 1)
        topic = index.parent / (note["id"] + ".md")
        self.assertIn(NOTE["body"], topic.read_text())
        self.assertIn(NOTE["source"], topic.read_text())
        if os.name != "nt":
            self.assertEqual(index.stat().st_mode & 0o777, 0o600)
            self.assertEqual(index.parent.stat().st_mode & 0o777, 0o700)
        topic.write_text("invented rule")
        second = self.publish()
        self.assertNotEqual(first["index"], second["index"])
        self.assertIn(NOTE["body"], (Path(second["index"]).parent / topic.name).read_text())
        self.ledger.memory_admin("forget", note_id=note["id"], expected_revision=1, reason="outdated")
        third = self.publish()
        self.assertEqual(third["count"], 0)
        self.assertNotIn(note["id"], Path(third["index"]).read_text())
        self.assertTrue(topic.exists())

    def test_partial_failure_never_publishes(self):
        from unittest.mock import patch
        self.ledger.memory_admin("save", value=NOTE)
        self.ledger.memory_admin("save", value={**NOTE, "request_id": "second"})
        original = Path.write_text
        count = [0]
        def failing(path, *args, **kwargs):
            count[0] += 1
            if count[0] == 2: raise OSError("disk full")
            return original(path, *args, **kwargs)
        with patch.object(Path, "write_text", failing), self.assertRaises(OSError):
            self.publish()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_two_publishers_do_not_overwrite_each_other(self):
        from agent import memory
        self.ledger.memory_admin("save", value=NOTE)
        rows = self.ledger.memory_rows()
        results = []
        def worker():
            try: results.append(memory.publish_snapshot(self.root, rows))
            except BaseException as exc: results.append(exc)
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads: t.start()
        for t in threads:
            t.join(5)
            self.assertFalse(t.is_alive())
        self.assertTrue(all(isinstance(r, dict) for r in results), results)
        self.assertNotEqual(results[0]["index"], results[1]["index"])
        self.assertTrue(all(Path(r["index"]).is_file() for r in results))

    def test_invalid_stored_ids_and_symlink_root_are_refused(self):
        from agent import memory
        row = self.ledger.memory_admin("save", value=NOTE)
        with self.assertRaises(ValueError):
            memory.publish_snapshot(self.root, [{**row, "id": "../../outside"}])
        other = Path(self.tmp.name) / "other"
        other.mkdir()
        linked = Path(self.tmp.name) / "linked"
        linked.symlink_to(other, target_is_directory=True)
        with self.assertRaises(ValueError): memory.publish_snapshot(linked, [])
        self.assertEqual(list(other.iterdir()), [])

    def test_prune_preserves_references_and_ignores_unrelated_paths(self):
        import json
        from uuid import uuid4
        from agent import memory
        keep, remove = self.publish(), self.publish()
        self.prompt("kept", {"memory": keep})
        self.prompt("old", {"item_id": "before-memory"})
        temp = self.root / (".tmp-" + str(uuid4()))
        temp.mkdir()
        unrelated = self.root / "other"
        unrelated.mkdir()
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        link = self.root / str(uuid4())
        link.symlink_to(outside, target_is_directory=True)
        result = memory.prune_snapshots(self.root, self.runs)
        self.assertTrue(Path(keep["index"]).exists())
        self.assertFalse(Path(remove["index"]).exists())
        self.assertFalse(temp.exists())
        self.assertTrue(unrelated.exists())
        self.assertTrue(link.is_symlink())
        self.assertTrue(outside.exists())
        self.assertIn(keep["snapshot_id"], result["retained"])

    def test_prune_validates_all_prompts_before_deleting(self):
        from agent import memory
        view = self.publish()
        bad = self.prompt("bad", {})
        for text in ("not JSON", 'authority\n\n{"memory":{"status":"ready"}}',
                     'authority\n\n{"memory":{"status":"ready","index":"/outside/MEMORY.md"}}'):
            bad.write_text(text)
            with self.assertRaises(ValueError): memory.prune_snapshots(self.root, self.runs)
            self.assertTrue(Path(view["index"]).exists())
        with self.assertRaises(OSError): memory.prune_snapshots(self.root, self.runs / "missing")
