"""The checker must reject plausible-looking but incomplete rehearsal evidence."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent.ledger import Ledger
from test_ledger import ISSUE, PIN, issue


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check-rehearsal.py"
SLOT = "unity_slot:1"


class RehearsalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.folder = self.root / "slot"
        self.folder.mkdir()
        self.git("init", "-q")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                 "commit", "--allow-empty", "-qm", "fixture")
        self.commit = self.git("rev-parse", "HEAD").strip()
        self.git("update-ref", "refs/remotes/origin/main", self.commit)
        self.db = self.root / "ledger.sqlite3"
        self.runs = self.root / "runs"
        self.now = 1000.0
        self.ledger = Ledger(self.db, clock=lambda: self.now)
        self.addCleanup(self.ledger.close)
        self.ledger.observe_issue(issue())
        self.ledger.ensure_slot(SLOT, kind="unity_slot", host="test", folder=str(self.folder))
        self.items = []

    def git(self, *args):
        result = subprocess.run(["git", "-C", str(self.folder), *args], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def item(self, mode="batch", cancelled=False, failed=26):
        self.now += 10
        session = f"session-{len(self.items)}"
        issue_id = f"10000000-0000-4000-8000-{len(self.items) + 1:012d}"
        self.ledger.observe_issue(issue(id=issue_id))
        self.ledger.ensure_session(session, issue_id, delegation=True)
        item = self.ledger.create_work_item(issue_id=issue_id, session_id=session, skill="fix", target=PIN)["id"]
        token = self.ledger.claim(item, worker_id="fixture")["token"]
        self.ledger.await_resource(item, token, "unity_slot", mode)
        self.now += 1
        res = self.ledger.acquire("unity_slot", owner="pool", host="test")
        self.now += 1
        self.ledger.set_slot_state(SLOT, f"{mode}_busy")
        if mode == "interactive":
            self.ledger.record_identity(item, res["reservation_id"], SLOT, {"aggregate": "match"})
        if cancelled:
            self.now += 1
            self.ledger.cancel(item, "operator stop")  # CLI path: no cancel requested audit row.
        self.now += 1
        self.ledger.release(res["reservation_id"], res["token"], "fixture quiet")
        self.now += 1
        self.ledger.set_slot_state(SLOT, "idle_open" if mode == "interactive" else "idle_closed",
                                   parked_commit=self.commit)
        path = self.runs / item
        path.mkdir(parents=True)
        if mode == "batch" and not cancelled:
            (path / "unity-batch.json").write_text(json.dumps({"state": "ran"}))
            (path / "unity-tests.xml").write_text(
                f'<test-run total="100" passed="{100-failed}" failed="{failed}" result="Failed"/>')
        self.items.append(item)
        return item, res["reservation_id"]

    def check(self, *extra, items=None):
        command = [sys.executable, "-B", str(CHECKER), "--db", str(self.db), "--runs", str(self.runs)]
        for item in self.items if items is None else items:
            command += ["--item", item]
        return subprocess.run([*command, *extra], cwd=self.root, capture_output=True, text=True, timeout=15)

    def test_rejects_missing_reservation_transition_even_when_final_rows_look_good(self):
        item, res = self.item()
        self.item("interactive")
        self.ledger.connection.execute("DELETE FROM audit WHERE item_id=? AND kind='reservation' AND reason='acquired'",
                                       (item,))
        result = self.check()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("acquired", result.stdout)

    def test_rejects_overlapping_acquisition_intervals(self):
        first, first_res = self.item()
        second, second_res = self.item("interactive")
        self.ledger.connection.execute("UPDATE reservations SET acquired_at=? WHERE reservation_id=?",
                                       (self.ledger.reservation(first_res)["acquired_at"] + 0.5, second_res))
        result = self.check("--pair", first, second)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("overlap", result.stdout)

    def test_rejects_missing_interactive_identity(self):
        item, _ = self.item("interactive")
        self.item("interactive")
        self.ledger.connection.execute("DELETE FROM identity_observations WHERE item_id=?", (item,))
        result = self.check()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("identity", result.stdout)

    def test_rejects_zero_tests_even_with_ran_summary(self):
        item, _ = self.item()
        (self.runs / item / "unity-tests.xml").write_text('<test-run total="0" failed="0" result="Passed"/>')
        result = self.check()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("total", result.stdout)

    def test_both_orderings_pass_and_checker_leaves_ledger_unchanged(self):
        batch1, _ = self.item()
        interactive1, _ = self.item("interactive")
        interactive2, _ = self.item("interactive")
        batch2, _ = self.item()
        before = "\n".join(self.ledger.connection.iterdump())
        result = self.check("--pair", batch1, interactive1, "--pair", interactive2, batch2)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("BASELINE", result.stdout)
        self.assertIn("PASS pair", result.stdout)
        self.assertEqual("\n".join(self.ledger.connection.iterdump()), before)

    def test_changed_failure_count_is_a_report_line_not_a_rehearsal_failure(self):
        self.item(failed=27)
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("CHANGED", result.stdout)
        self.assertIn("27", result.stdout)

    def test_cancelled_batch_without_xml_is_interrupted_with_external_timing(self):
        item, _ = self.item(cancelled=True)
        result = self.check("--cancelled-item", item)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("INTERRUPTED", result.stdout)
        self.assertIn("external stopwatch", result.stdout)

    def test_rejects_missing_busy_and_idle_audits(self):
        self.item()
        for reason in ("batch_busy", "idle_closed"):
            with self.subTest(reason=reason):
                row = dict(self.ledger.connection.execute(
                    "SELECT * FROM audit WHERE kind='slot' AND reason=?", (reason,)).fetchone())
                self.ledger.connection.execute("DELETE FROM audit WHERE id=?", (row["id"],))
                result = self.check()
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("transition", result.stdout)
                self.ledger.connection.execute("INSERT INTO audit VALUES(?,?,?,?,?,?)", tuple(row.values()))

    def test_rejects_final_parked_commit_mismatch(self):
        self.item()
        self.ledger.connection.execute("UPDATE slots SET parked_commit=?", ("b" * 40,))
        result = self.check()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("parked", result.stdout)

    def test_rejects_a_slot_whose_actual_head_drifted_from_its_parked_record(self):
        self.item()
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                 "commit", "--allow-empty", "-qm", "drift")
        result = self.check()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("HEAD", result.stdout)

    def test_rejects_idle_evidence_that_only_appears_after_the_next_acquisition(self):
        first, _ = self.item()
        first_idle = self.ledger.connection.execute(
            "SELECT id FROM audit WHERE kind='slot' AND reason='idle_closed'").fetchone()[0]
        second, _ = self.item()
        self.ledger.connection.execute("DELETE FROM audit WHERE id=?", (first_idle,))
        result = self.check(items=[first])
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("before next acquisition", result.stdout)
        # A missing audit on an unselected later item must not let its idle record stand in for ours.
        self.ledger.connection.execute("DELETE FROM audit WHERE item_id=? AND reason='acquired'", (second,))
        result = self.check(items=[first])
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("before next acquisition", result.stdout)

    def test_rejects_unknown_item_and_uncompleted_reservation(self):
        item, res = self.item()
        result = self.check(items=["unknown"])
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("no reservation", result.stdout)
        self.ledger.connection.execute("UPDATE reservations SET state='active',released_at=NULL WHERE reservation_id=?",
                                       (res,))
        result = self.check(items=[item])
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("expected released", result.stdout)

    def test_reversed_pair_is_rejected(self):
        first, _ = self.item()
        second, _ = self.item("interactive")
        result = self.check("--pair", second, first)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("pair acquisition order", result.stdout)

    def test_rejects_missing_batch_summary_and_xml(self):
        item, _ = self.item()
        for name in ("unity-batch.json", "unity-tests.xml"):
            with self.subTest(name=name):
                path = self.runs / item / name
                saved = path.read_text()
                path.unlink()
                result = self.check()
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("batch", result.stdout)
                path.write_text(saved)

    def test_missing_database_fails_without_creating_it(self):
        missing = self.root / "missing.sqlite3"
        result = self.check("--db", str(missing), items=["missing-item"])
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(missing.exists())
