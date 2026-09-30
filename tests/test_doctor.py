"""Doctor exercises real ledgers and the public CLI without repairing either."""
import contextlib
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent.config import Config, Paths
from agent.dispatch import SKILL_AUTHORITY
from agent.doctor import diagnose, probe_process
from agent.ledger import Ledger
from agent.service import main
from agent.skills import load_skills
from test_ledger import ISSUE, OTHER, SESSION, PIN, issue
from test_skills import opt_in_skill, staged_skill

ROOT = Path(__file__).resolve().parents[1]
THIRD = "10000000-0000-4000-8000-000000000003"


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Config("client-secret", "oauth-secret", "webhook-secret",
                             local_root=Path(self.tmp.name), host="test-host")
        self.paths = Paths(self.config)
        self.ledger = Ledger(self.paths.ledger, clock=lambda: 1000, lease_seconds=60)
        self.addCleanup(self.ledger.close)
        self.ledger.observe_issue(issue(description="private issue prose"))
        self.ledger.ensure_session(SESSION, ISSUE, delegation=True)
        self.item = self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill="fix", target=PIN)
        self.config_path = Path(self.tmp.name) / "config.json"
        self.config_path.write_text(json.dumps({"client_id": "client-secret", "client_secret": "oauth-secret",
                                               "webhook_secret": "webhook-secret", "host": "test-host",
                                               "local_root": str(self.config.local_root)}))

    def report(self, **kwargs):
        return diagnose(self.config, now=1001, **kwargs)

    def codes(self, report):
        return {f["code"] for f in report["findings"]}

    def test_recovery_diagnostics_expose_progress_without_private_errors(self):
        from agent.resource_recovery import RecoveryStore
        self.ledger.ensure_slot('unity_slot:1', kind='unity_slot', host='test-host', folder=str(self.paths.editors / 'slot-1'))
        self.ledger.set_slot_state('unity_slot:1', 'held')
        store = RecoveryStore(self.ledger)
        store.discover('test-host')
        recovery = store.begin(store.pending('test-host')[0]['id'])
        store.failed(recovery['id'], recovery['attempts'], 'private error detail')
        report = self.report()
        self.assertEqual(report['resource_recoveries'][0]['attempts'], 1)
        self.assertEqual(report['resource_recoveries'][0]['state'], 'pending')
        self.assertNotIn('private error detail', json.dumps(report))

    def running(self, pid=4242, host="test-host"):
        self.ledger.set_worker(self.item["id"], pid, host)
        return self.ledger.claim(self.item["id"], worker_id="test")

    def test_waiting_for_an_answer_is_visible_without_being_reported_as_broken(self):
        claimed = self.running()
        self.ledger.await_input(self.item["id"], claimed["token"], "private question")
        report = self.report()
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["jobs"][0]["state"], "awaiting_input")
        self.assertEqual(report["jobs"][0]["identifier"], "FARM-1")
        self.assertEqual(report["findings"], [])

    def test_expired_lease_and_dead_worker_have_job_and_log_evidence(self):
        self.running()
        attempt = self.paths.runs / self.item["id"] / "20260921T000000Z-attempt"
        attempt.mkdir(parents=True)
        (attempt / "stderr.log").write_text("sensitive log content")
        with patch("agent.doctor.probe_process", return_value={"state": "dead", "reason": "not_found"}):
            report = diagnose(self.config, now=1061)
        self.assertEqual(report["status"], "attention")
        self.assertTrue({"lease_expired", "worker_dead"} <= self.codes(report))
        dead = next(f for f in report["findings"] if f["code"] == "worker_dead")
        self.assertEqual(dead["item_id"], self.item["id"])
        self.assertIn(str(attempt / "stderr.log"), dead["logs"]["files"])
        self.assertNotIn("sensitive log content", json.dumps(report))

    def test_foreign_host_pid_is_not_probed_on_this_machine(self):
        self.running(host="another-host")
        with patch("agent.doctor.probe_process", side_effect=AssertionError("foreign PID probed")):
            report = self.report()
        self.assertEqual(report["status"], "incomplete")
        self.assertIn("worker_unverified", self.codes(report))

    def test_process_inspection_failure_is_not_a_dead_worker(self):
        self.running()
        with patch("agent.doctor.probe_process", return_value={"state": "unknown", "reason": "permission_denied"}):
            report = self.report()
        self.assertEqual(report["status"], "incomplete")
        self.assertNotIn("worker_dead", self.codes(report))

    def test_cleanup_pid_is_checked_even_after_the_job_has_cleared_its_pid(self):
        self.ledger.connection.execute("INSERT INTO job_cleanup(item_id,worker_pid,updated_at) VALUES(?,?,?)",
                                       (self.item["id"], 4242, 1000))
        self.ledger.connection.execute("UPDATE work_items SET host='test-host',state='cancelled'")
        with patch("agent.doctor.probe_process", return_value={"state": "unknown", "reason": "ownership_unverified"}):
            report = self.report()
        self.assertEqual(report["status"], "incomplete")
        self.assertIn("cleanup_worker_unverified", self.codes(report))
        self.assertEqual(report["cleanup_pending"][0]["process"]["state"], "unknown")

    def test_job_counts_include_history_but_finished_jobs_do_not_hide_current_work(self):
        self.ledger.connection.execute("UPDATE work_items SET state='delivered'")
        report = self.report()
        self.assertEqual(report["counts"], {"delivered": 1, "total": 1})
        self.assertEqual(report["jobs"], [])
        self.assertEqual(report["status"], "ok")
        self.ledger.connection.execute("UPDATE work_items SET state='failed'")
        self.assertIn("job_failed", self.codes(self.report()))

    def test_valid_running_claim_and_log_listing_do_not_expose_tokens(self):
        claim = self.running()
        token_hash = self.ledger.connection.execute("SELECT token FROM work_items").fetchone()[0]
        with patch("agent.doctor.probe_process", return_value={"state": "alive", "reason": "job_marker_matches"}):
            report = self.report()
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["jobs"][0]["process"]["state"], "alive")
        self.assertNotIn(claim["token"], json.dumps(report))
        self.assertNotIn(token_hash, json.dumps(report))

    def test_running_without_pid_is_reported_but_unlaunched_queue_is_normal(self):
        self.assertEqual(self.report()["status"], "ok")
        self.ledger.claim(self.item["id"], worker_id="test")
        self.assertIn("worker_pid_missing", self.codes(self.report()))

    def test_log_directory_permission_failure_is_incomplete_not_empty_and_healthy(self):
        directory = self.paths.runs / self.item["id"]
        directory.mkdir(parents=True)
        with patch("os.scandir", side_effect=PermissionError("sensitive filesystem error")):
            report = self.report()
        self.assertEqual(report["status"], "incomplete")
        self.assertIn("logs_unreadable", self.codes(report))
        self.assertNotIn("sensitive filesystem error", json.dumps(report))

    def test_released_slot_waiting_to_be_parked_is_not_an_orphan(self):
        self.ledger.connection.execute(
            "INSERT INTO slots(slot_id,kind,host,folder,state,updated_at) VALUES(?,?,?,?,?,?)",
            ("unity-1", "unity", "test-host", "/tmp/editor", "idle_closed", 1000))
        claim = self.ledger.claim(self.item["id"], worker_id="test")
        self.ledger.await_resource(self.item["id"], claim["token"], "unity", "batch")
        granted = self.ledger.acquire(kind="unity", host="test-host", owner="pool")
        self.ledger.release(granted["reservation_id"], granted["token"], "finished")
        report = self.report()
        self.assertEqual(report["slots"][0]["state"], "switching")
        self.assertNotIn("slot_without_reservation", self.codes(report))

    def test_cleanup_status_errors_and_held_slots_are_collected_without_raw_secrets(self):
        db = self.ledger.connection
        db.execute("INSERT INTO job_cleanup(item_id,error,updated_at) VALUES(?,?,?)",
                   (self.item["id"], "sensitive error", 1000))
        db.execute("UPDATE issue_checks SET failures=2,error='sensitive error',checked_at=1000")
        db.execute("INSERT INTO slots(slot_id,kind,host,folder,state,updated_at) VALUES(?,?,?,?,?,?)",
                   ("unity-1", "unity", "test-host", "/tmp/editor", "held", 1000))
        report = self.report()
        self.assertTrue({"cleanup_pending", "issue_status_error", "slot_held"} <= self.codes(report))
        self.assertEqual(report["cleanup_pending"][0]["item_id"], self.item["id"])
        self.assertEqual(report["issue_status_errors"][0]["failures"], 2)
        encoded = json.dumps(report)
        for secret in ("sensitive error", "oauth-secret", "webhook-secret", "private issue prose", "token_hash"):
            self.assertNotIn(secret, encoded)

    def test_busy_slot_without_reservation_and_cancel_requested_are_actionable(self):
        db = self.ledger.connection
        db.execute("INSERT INTO slots(slot_id,kind,host,folder,state,updated_at) VALUES(?,?,?,?,?,?)",
                   ("unity-1", "unity", "test-host", "/tmp/editor", "batch_busy", 1000))
        self.assertIn("slot_without_reservation", self.codes(self.report()))
        db.execute("""INSERT INTO reservations(reservation_id,item_id,generation,kind,mode,resource,
                   host,commit_sha,state,owner,token_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                   ("r1", self.item["id"], 0, "unity", "batch", "unity-1", "test-host", "a" * 40,
                    "cancel_requested", "worker", "secret-hash", 1000))
        report = self.report()
        self.assertIn("reservation_cancel_pending", self.codes(report))
        self.assertNotIn("slot_without_reservation", self.codes(report))
        self.assertNotIn("secret-hash", json.dumps(report))

    def test_cli_is_json_and_does_not_change_config_permissions_or_ledger_contents(self):
        self.config_path.chmod(0o640)
        # Windows exposes only a subset of POSIX chmod bits. Compare the actual
        # starting mode so both platforms still prove doctor leaves it unchanged.
        before_mode = self.config_path.stat().st_mode
        before = list(self.ledger.connection.iterdump())
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(["doctor", "--config", str(self.config_path)])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "ok")
        self.assertEqual(self.config_path.stat().st_mode, before_mode)
        self.assertEqual(list(self.ledger.connection.iterdump()), before)

    def test_missing_database_is_not_created_and_cli_returns_incomplete(self):
        self.ledger.close()
        self.paths.ledger.unlink()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(["doctor", "--config", str(self.config_path)])
        self.assertEqual(result, 2)
        report = json.loads(output.getvalue())
        self.assertIn("ledger_unreadable", self.codes(report))
        self.assertFalse(self.paths.ledger.exists())

    def test_old_schema_is_reported_without_migration(self):
        self.ledger.connection.execute("DROP TABLE issue_checks")
        report = self.report()
        self.assertEqual(report["status"], "incomplete")
        self.assertIn("ledger_unreadable", self.codes(report))
        self.assertIn("issue_checks", report["findings"][0]["missing_schema"])
        with self.assertRaises(sqlite3.OperationalError):
            self.ledger.connection.execute("SELECT * FROM issue_checks")

    def test_missing_or_malformed_config_still_returns_json(self):
        for value in (None, "{", "[]"):
            if value is None:
                self.config_path.unlink()
            else:
                self.config_path.write_text(value)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = main(["doctor", "--config", str(self.config_path)])
            self.assertEqual(result, 2)
            self.assertIn("config_unreadable", self.codes(json.loads(output.getvalue())))

    def test_cli_attention_exit_code(self):
        self.ledger.claim(self.item["id"], worker_id="test")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["doctor", "--config", str(self.config_path)]), 1)

    def findings(self, report, code):
        return [f for f in report["findings"] if f["code"] == code]

    def test_e4_item_outside_issue_prefix_is_a_finding(self):
        """Withdrawn-work design E4: FarmBot does not notice a card moved to another team, so an active job whose
        identifier left the host's issue_prefix is listed."""
        self.ledger.observe_issue(issue(id=OTHER, identifier="OPS-7"))
        self.ledger.ensure_session("session-2", OTHER, delegation=False)
        moved = self.ledger.create_work_item(issue_id=OTHER, session_id="session-2", skill="chat")
        found = self.findings(self.report(), "outside_prefix")
        self.assertEqual([(f["item_id"], f["identifier"], f["issue_prefix"]) for f in found],
                         [(moved["id"], "OPS-7", "FARM")])
        self.assertEqual(self.findings(diagnose(replace(self.config, issue_prefix="OPS"), now=1001), "outside_prefix")[0]
                         ["item_id"], self.item["id"])

    def test_h4_long_parked_item_is_a_finding(self):
        """Design H4, B1: a job that has waited more than 7 days for an answer is listed; a reply may be lost, or its
        session archived while the card stayed delegated, and nothing else would resume it."""
        claimed = self.running()
        self.ledger.await_input(self.item["id"], claimed["token"], "private question")
        self.assertEqual(self.findings(diagnose(self.config, now=1000 + 7 * 86400), "long_parked"), [])
        found = self.findings(diagnose(self.config, now=1001 + 7 * 86400), "long_parked")
        self.assertEqual([(f["item_id"], f["identifier"], f["parked_seconds"]) for f in found],
                         [(self.item["id"], "FARM-1", 7 * 86400 + 1)])
        self.assertNotIn("private question", json.dumps(found))

    def test_undelegated_work_is_listed_once_the_withdrawal_is_three_intervals_late(self):
        """Design §5.1 commit 6: two status reads an interval apart withdraw the delegation's work, so a job still
        active three intervals after the first read found the card undelegated is stuck. A mention's is not."""
        self.ledger.mark_undelegated(ISSUE, 1000)
        self.ledger.observe_issue(issue(id=OTHER))
        self.ledger.ensure_session("mention", OTHER, delegation=False)
        self.ledger.create_work_item(issue_id=OTHER, session_id="mention", skill="chat")
        self.ledger.mark_undelegated(OTHER, 1000)
        self.assertEqual(self.findings(diagnose(self.config, now=1180), "undelegated_work"), [])
        found = self.findings(diagnose(self.config, now=1181), "undelegated_work")
        self.assertEqual([(f["item_id"], f["state"], f["undelegated_since"]) for f in found],
                         [(self.item["id"], "queued", 1000)])

    def test_a_flagged_worker_inside_its_grace_is_not_undelegated_work(self):
        """Design P2: the second read flags a claimed worker rather than cancelling it, and the worker may run until
        its deadline, 20 minutes on for a fix; withdrawal_overdue lists it if it outlives that. Flagged work that went
        back to the queue is never launched again and only a read ends it, so it is still listed."""
        self.running()
        self.ledger.mark_undelegated(ISSUE, 1000)
        self.ledger.flag_withdrawal(self.item["id"], "undelegated", 1060 + 1200)
        with patch("agent.doctor.probe_process", return_value={"state": "alive", "reason": "job_marker_matches"}):
            report = diagnose(self.config, now=1300)
        self.assertEqual(self.findings(report, "undelegated_work"), [])
        self.assertEqual(self.findings(report, "withdrawal_overdue"), [])
        self.ledger.clock = lambda: 1100
        self.ledger.recover(self.item["id"], "lease expired")
        found = self.findings(diagnose(self.config, now=1300), "undelegated_work")
        self.assertEqual([(f["item_id"], f["state"]) for f in found], [(self.item["id"], "queued")])

    def test_a_worker_past_its_withdrawal_deadline_is_overdue(self):
        """Design P2: the controller stops a flagged worker at its deadline; one still running 5 minutes later is
        listed."""
        self.running()
        self.ledger.flag_withdrawal(self.item["id"], "superseded", 1100)
        with patch("agent.doctor.probe_process", return_value={"state": "alive", "reason": "job_marker_matches"}):
            self.assertEqual(self.findings(diagnose(self.config, now=1400), "withdrawal_overdue"), [])
            found = self.findings(diagnose(self.config, now=1401), "withdrawal_overdue")
        self.assertEqual([(f["item_id"], f["withdraw_reason"], f["withdraw_deadline"]) for f in found],
                         [(self.item["id"], "superseded", 1100)])

    def test_a_delegation_deferred_for_more_than_45_minutes_is_listed(self):
        """Design C2: a delegation waits for another session's claimed worker, at most its grace and 5 minutes; one
        still deferred after 45 minutes is listed with the job it waits for."""
        self.ledger.connection.execute("""CREATE TABLE webhook_events (
            event_key TEXT PRIMARY KEY, session_id TEXT NOT NULL, ack_id TEXT NOT NULL, status TEXT NOT NULL,
            payload TEXT, received_at REAL NOT NULL, completed_at REAL, error TEXT)""")
        self.ledger.connection.execute("INSERT INTO webhook_events VALUES('k','session-2','a','deferred','{}',1000,NULL,?)",
                                       (self.item["id"],))
        self.ledger.connection.execute("INSERT INTO webhook_events VALUES('j','session-3','b','done',NULL,0,1,NULL)")
        self.assertEqual(self.findings(diagnose(self.config, now=1000 + 2700), "deferred_delegation"), [])
        found = self.findings(diagnose(self.config, now=1001 + 2700), "deferred_delegation")
        self.assertEqual([(f["session_id"], f["waits_for"], f["deferred_seconds"]) for f in found],
                         [("session-2", self.item["id"], 2701)])
        self.assertNotIn("payload", found[0])

    def test_unclosed_session_is_a_finding(self):
        """Silent-delegation design P10, A4: a closing activity Linear refused six times is listed, since its thread
        may still show FarmBot waiting and block the card's next delegation. One still being retried is not listed,
        nor one given up more than 7 days ago, nor one whose thread newer work has spoken in since. The evidence names
        the thread and its times, never the activity's text. A ledger older than the table reports nothing."""
        self.ledger.cancel(self.item["id"], "Linear stop")
        self.ledger.owe_closure(SESSION, item_id=self.item["id"], kind="response", body="private notice text")
        self.ledger.owe_closure("session-9", kind="error", body="private error text")  # a thread with no job
        for _ in range(4):
            self.ledger.closure_result(SESSION, sent=False, error="LinearError")
            self.ledger.closure_result("session-9", sent=False, error="LinearError")
        self.assertEqual(self.findings(self.report(), "unclosed_session"), [])  # five refusals: one more try is due
        self.ledger.clock = lambda: 2000
        self.ledger.closure_result(SESSION, sent=False, error="LinearError")
        self.ledger.closure_result("session-9", sent=False, error="OSError")
        found = self.findings(diagnose(self.config, now=2001), "unclosed_session")
        self.assertEqual([(f["session_id"], f["issue_id"], f["identifier"], f["item_id"], f["kind"], f["attempts"],
                           f["owed_at"], f["given_up_at"], f["last_error"]) for f in found],
                         [(SESSION, ISSUE, "FARM-1", self.item["id"], "response", 6, 1000, 2000, "LinearError"),
                          ("session-9", None, None, None, "error", 6, 1000, 2000, "OSError")])
        self.assertIn("archive it if it waits", found[0]["hint"])
        self.assertNotIn("private", json.dumps(found))
        self.assertEqual(len(self.findings(diagnose(self.config, now=2000 + 7 * 86400), "unclosed_session")), 2)
        self.assertEqual(self.findings(diagnose(self.config, now=2001 + 7 * 86400), "unclosed_session"), [])
        # The job was retried since: its successor speaks in the thread now, so nothing there waits on the old words.
        self.ledger.retry(self.item["id"], "重试")
        self.assertEqual([f["session_id"] for f in self.findings(diagnose(self.config, now=2001), "unclosed_session")],
                         ["session-9"])
        self.ledger.connection.execute("DROP TABLE session_closures")
        report = diagnose(self.config, now=2001)
        self.assertNotEqual(report["status"], "incomplete")
        self.assertEqual(self.findings(report, "unclosed_session"), [])

    def test_unclosed_session_is_not_a_finding_once_its_job_moved_on(self):
        """P10: a job whose state changed has spoken in its thread since, so nothing there waits on the closing words
        Linear refused. The ledger drops the row at that change. A row whose job is in another state than the one
        recorded, which a revision without that drop can leave on a ledger it ran on in between, is not listed."""
        def unclosed():
            return [(f["session_id"], f["item_id"])
                    for f in self.findings(diagnose(self.config, now=2001), "unclosed_session")]
        self.ledger.fail_queued(self.item["id"], "launch failed")
        self.ledger.owe_closure(SESSION, item_id=self.item["id"], kind="error", body="private error text")
        self.ledger.clock = lambda: 2000
        for _ in range(5):
            self.ledger.closure_result(SESSION, sent=False, error="LinearError")
        self.assertEqual(unclosed(), [(SESSION, self.item["id"])])
        self.ledger.connection.execute("UPDATE work_items SET state='queued' WHERE id=?", (self.item["id"],))
        self.assertEqual(unclosed(), [])
        self.ledger.connection.execute("UPDATE work_items SET state='failed' WHERE id=?", (self.item["id"],))
        self.assertEqual(unclosed(), [(SESSION, self.item["id"])])
        self.ledger.retry(self.item["id"], "重试")  # this revision: the row goes with the job's state
        self.assertEqual(unclosed(), [])
        self.assertIsNone(self.ledger.connection.execute("SELECT 1 FROM session_closures").fetchone())

    def test_stored_undelegated_lists_the_delegations_work_only_when_the_app_is_pinned(self):
        """Design §5.3: before deploying, doctor lists the work the stored snapshot shows on a card not delegated to
        the pinned app. A conversation a delegation opened before authorities were recorded is listed too, though the
        new code keeps it; a mention's is not."""
        app = "e5a8c16d-9f85-4123-acf5-94e41c3304d5"
        self.assertEqual(self.findings(self.report(), "stored_undelegated"), [])
        self.ledger.observe_issue(issue(id=OTHER, identifier="FARM-2"))
        self.ledger.ensure_session("delegated-chat", OTHER, delegation=True)
        old_chat = self.ledger.create_work_item(issue_id=OTHER, session_id="delegated-chat", skill="chat")
        self.ledger.connection.execute("UPDATE work_items SET authority=NULL WHERE id=?", (old_chat["id"],))
        self.ledger.observe_issue(issue(id=THIRD, identifier="FARM-3"))
        self.ledger.ensure_session("mention", THIRD, delegation=False)
        self.ledger.create_work_item(issue_id=THIRD, session_id="mention", skill="chat")
        pinned = replace(self.config, expected_app_user_id=app)
        found = self.findings(diagnose(pinned, now=1001), "stored_undelegated")
        self.assertEqual({(f["item_id"], f["skill"], f["authority_recorded"]) for f in found},
                         {(self.item["id"], "fix", True), (old_chat["id"], "chat", False)})
        self.ledger.observe_issue(issue(delegate_id=app, updated_at="2026-09-30T00:00:00Z"))
        self.assertEqual({f["item_id"] for f in self.findings(diagnose(pinned, now=1001), "stored_undelegated")},
                         {old_chat["id"]})

    def test_a_ledger_without_the_withdrawal_columns_reports_none_of_their_findings(self):
        """Each withdrawn-work finding is read only when its column exists; doctor never migrates."""
        claimed = self.running()
        self.ledger.await_input(self.item["id"], claimed["token"], "private question")
        for table, column in (("work_items", "authority"), ("work_items", "withdraw_deadline"),
                              ("work_items", "withdraw_reason"), ("issue_checks", "undelegated_since")):
            self.ledger.connection.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        report = diagnose(replace(self.config, expected_app_user_id="e5a8c16d-9f85-4123-acf5-94e41c3304d5"),
                          now=1001 + 7 * 86400)
        self.assertNotEqual(report["status"], "incomplete")
        self.assertEqual(self.codes(report), {"long_parked", "stored_undelegated"})
        columns = {row[1] for row in self.ledger.connection.execute("PRAGMA table_info(work_items)")}
        self.assertNotIn("authority", columns)

    def test_kw_ops_configuration_is_reported_without_its_token(self):
        self.config.kw_ops = {"url": "https://gm.test/mcp", "token_env": "KW_OPS_TOKEN"}
        with patch.dict(os.environ, {"KW_OPS_TOKEN": "dummy-token-value"}):
            report = self.report()
        self.assertEqual(report["tools"]["kw_ops"], {"configured": True, "token_env": "KW_OPS_TOKEN",
                                                     "token_set_in_doctor_environment": True})
        self.assertNotIn("dummy-token-value", json.dumps(report))
        with patch.dict(os.environ, {"KW_OPS_TOKEN": ""}):
            self.assertFalse(self.report()["tools"]["kw_ops"]["token_set_in_doctor_environment"])
        # Whitespace is unset too, as it is when the controller resolves the grant.
        with patch.dict(os.environ, {"KW_OPS_TOKEN": "   "}):
            self.assertFalse(self.report()["tools"]["kw_ops"]["token_set_in_doctor_environment"])
        with patch.dict(os.environ):
            os.environ.pop("KW_OPS_TOKEN", None)
            self.assertFalse(self.report()["tools"]["kw_ops"]["token_set_in_doctor_environment"])

    def test_an_unconfigured_kw_ops_is_reported_as_such(self):
        self.assertEqual(self.report()["tools"], {"kw_ops": {"configured": False}})

    def test_the_enabled_skills_are_reported(self):
        self.assertEqual(self.report()["skills"], {"loaded": ["chat", "fix"], "enabled": ["chat", "fix"],
                                                   "configured": False})
        self.config.enabled_skills = ["chat"]
        report = self.report()
        self.assertEqual(report["skills"], {"loaded": ["chat", "fix"], "enabled": ["chat"], "configured": True})
        self.assertEqual(report["status"], "ok")

    def test_an_enabled_skill_the_checkout_lacks_is_the_finding_serve_would_refuse(self):
        self.config.enabled_skills = ["chat", "feature"]
        report = self.report()
        self.assertIsNone(report["skills"]["enabled"])
        finding = next(f for f in report["findings"] if f["code"] == "enabled_skills_invalid")
        self.assertEqual((finding["unknown"], finding["unbriefed"]), (["feature"], []))
        self.assertEqual(report["status"], "attention")

    def test_an_enabled_skill_the_dispatch_cannot_brief_is_the_finding_serve_would_refuse(self):
        with patch("agent.doctor.SKILL_AUTHORITY", {"chat": "the chat part"}):
            report = self.report()
        finding = next(f for f in report["findings"] if f["code"] == "enabled_skills_invalid")
        self.assertEqual((finding["unknown"], finding["unbriefed"]), ([], ["fix"]))
        self.assertIsNone(report["skills"]["enabled"])

    def test_an_enabled_skill_the_runtime_cannot_launch_is_a_warning(self):
        """serve starts on a claude host with fix enabled and accepts fix delegations; the scheduler then refuses
        every launch. Doctor names fix by the same rule, whether enabled_skills lists it or is absent."""
        self.config.runtime = "claude"
        for enabled in (None, ["chat", "fix"]):
            with self.subTest(enabled_skills=enabled):
                self.config.enabled_skills = enabled
                report = self.report()
                finding = next(f for f in report["findings"] if f["code"] == "skill_runtime_unsupported")
                self.assertEqual((finding["severity"], finding["runtime"], finding["skills"]),
                                 ("warning", "claude", ["fix"]))
                self.assertEqual(report["skills"]["enabled"], ["chat", "fix"])
                self.assertEqual(report["status"], "attention")

    def test_a_runtime_that_can_launch_every_enabled_skill_is_not_a_finding(self):
        for runtime, enabled in (("codex", None), ("fake", None), ("claude", ["chat"])):
            with self.subTest(runtime=runtime, enabled_skills=enabled):
                self.config.runtime, self.config.enabled_skills = runtime, enabled
                report = self.report()
                self.assertEqual(report["findings"], [])
                self.assertEqual(report["status"], "ok")

    def test_each_enabled_staged_skill_is_named_and_only_those(self):
        staged = staged_skill(Path(self.tmp.name) / "fixture-skills")
        skills = {**load_skills(ROOT / "skills"), staged.name: staged}
        self.config.runtime = "claude"
        with patch("agent.doctor.load_skills", return_value=skills), \
                patch.dict(SKILL_AUTHORITY, {staged.name: "Fixture staged-skill grants. "}):
            for enabled, named in ((None, ["feature", "fix"]), (["chat", "feature"], ["feature"])):
                with self.subTest(enabled_skills=enabled):
                    self.config.enabled_skills = enabled
                    finding = next(f for f in self.report()["findings"] if f["code"] == "skill_runtime_unsupported")
                    self.assertEqual(finding["skills"], named)

    def test_an_opt_in_skill_is_loaded_but_not_enabled_until_the_config_names_it(self):
        """P1: an opt-in skill no list names is loaded, not enabled; doctor names it in no finding, as serve runs
        none of its jobs. The fixture is staged, so on a claude host doctor would name it if it ran."""
        fixture = opt_in_skill(Path(self.tmp.name) / "fixture-skills")
        skills = {**load_skills(ROOT / "skills"), fixture.name: fixture}
        self.config.runtime = "claude"
        with patch("agent.doctor.load_skills", return_value=skills):
            report = self.report()
            self.assertEqual(report["skills"], {"loaded": ["chat", "feature", "fix"], "enabled": ["chat", "fix"],
                                                "configured": False})
            finding = next(f for f in report["findings"] if f["code"] == "skill_runtime_unsupported")
            self.assertEqual(finding["skills"], ["fix"])
            # The evidence of a refused list names only what serve would run: never the unnamed opt-in skill.
            with patch("agent.doctor.SKILL_AUTHORITY", {"chat": "the chat part"}):
                finding = next(f for f in self.report()["findings"] if f["code"] == "enabled_skills_invalid")
            self.assertEqual((finding["unknown"], finding["unbriefed"], finding["missing_chat"]), ([], ["fix"], False))
            self.config.enabled_skills = ["chat", "feature"]
            with patch.dict(SKILL_AUTHORITY, {fixture.name: "Fixture feature grants. "}):
                report = self.report()
            self.assertEqual(report["skills"], {"loaded": ["chat", "feature", "fix"], "enabled": ["chat", "feature"],
                                                "configured": True})
            finding = next(f for f in report["findings"] if f["code"] == "skill_runtime_unsupported")
            self.assertEqual(finding["skills"], ["feature"])

    def test_an_unfinished_job_with_an_initial_root_shows_its_root_stages_pause_and_prs(self):
        """spec §9.11: where a long job stands, with nothing out of its plan but known words and PR links."""
        feature = opt_in_skill(Path(self.tmp.name) / "fixture-skills")
        skills = {**load_skills(ROOT / "skills"), feature.name: feature}

        def job(issue_id, identifier, session):
            self.ledger.observe_issue(issue(id=issue_id, identifier=identifier, description="private issue prose"))
            self.ledger.ensure_session(session, issue_id, delegation=True)
            return self.ledger.create_work_item(issue_id=issue_id, session_id=session, skill=feature.name)

        parked, queued = job(OTHER, "FARM-2", "session-2"), job(THIRD, "FARM-3", "session-3")
        token = self.ledger.claim(parked["id"], worker_id="w")["token"]
        self.ledger.checkpoint(parked["id"], token, {"plan": {
            "stages": {"A": "done", "B": "skipped: no config in this feature", "C": "pending", "Z": "done",
                       "D": ["done"]},
            "pause": {"kind": "config_ready", "reason": "waiting", "notice": "config-needed",
                      "since": "2026-09-28T00:00:00Z"},
            "prs": {"Farm-Contract": [{"branch": "farmbot/farm-2", "role": "issue", "head": "b" * 40,
                                       "pr": {"url": "https://github.com/Kuaiwa-Network/Farm-Contract/pull/12",
                                              "state": "draft", "merge": None}}],
                    "common": [{"branch": "farmbot/farm-2", "role": "issue", "head": "c" * 40, "pr": None}]}}})
        self.ledger.connection.execute("UPDATE work_items SET root_repo='common' WHERE id=?", (parked["id"],))
        self.ledger.await_input(parked["id"], token, "private question text", reason="waiting")  # at 1000
        with patch("agent.doctor.load_skills", return_value=skills):
            report = diagnose(self.config, now=1090)
        entries = {entry["item_id"]: entry for entry in report["jobs"]}
        self.assertEqual(entries[parked["id"]]["plan"], {
            "root": "common", "stages": {"A": "done", "B": "skipped", "C": "pending"},
            "pause": {"kind": "config_ready", "reason": "waiting", "age_seconds": 90},
            "prs": ["https://github.com/Kuaiwa-Network/Farm-Contract/pull/12"]})
        self.assertEqual(entries[queued["id"]]["plan"], {"root": "Farm-Contract", "stages": {}, "pause": None,
                                                         "prs": []})
        self.assertNotIn("plan", entries[self.item["id"]])  # fix has no initial root: its entry is unchanged
        for entry in entries.values():
            self.assertFalse({"_root_repo", "_checkpoint"} & set(entry))
        encoded = json.dumps(report, ensure_ascii=False)
        for private in ("private question text", "farmbot/farm-2", "no config in this feature", "config-needed",
                        "private issue prose"):
            self.assertNotIn(private, encoded)
        self.assertEqual(report["status"], "ok")

    def test_a_job_stopped_at_a_stage_limit_shows_that_pause(self):
        """Plan P14: a job a person stopped after a stage waits like any other pause, and doctor names it."""
        feature = opt_in_skill(Path(self.tmp.name) / "fixture-skills")
        skills = {**load_skills(ROOT / "skills"), feature.name: feature}
        self.ledger.observe_issue(issue(id=OTHER, identifier="FARM-2"))
        self.ledger.ensure_session("session-2", OTHER, delegation=True)
        item = self.ledger.create_work_item(issue_id=OTHER, session_id="session-2", skill=feature.name)
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.ledger.checkpoint(item["id"], token, {"plan": {
            "stages": {"A": "done", "B": "pending"},
            "pause": {"kind": "stage_limit", "reason": "waiting", "notice": "merge-contract",
                      "since": "2026-09-28T00:00:00Z"}}})
        self.ledger.await_input(item["id"], token, "stopped after stage A as asked", reason="waiting")
        with patch("agent.doctor.load_skills", return_value=skills):
            report = diagnose(self.config, now=1090)
        entry = next(entry for entry in report["jobs"] if entry["item_id"] == item["id"])
        self.assertEqual(entry["plan"]["pause"], {"kind": "stage_limit", "reason": "waiting", "age_seconds": 90})

    def test_a_plan_nested_past_the_recursion_limit_leaves_the_report_whole(self):
        """checkpoint bounds a plan's size and its lists' lengths, not its depth, and doctor reads what workers
        wrote: a plan nested deeper than Python recurses shows no PR links instead of failing the whole report."""
        feature = opt_in_skill(Path(self.tmp.name) / "fixture-skills")
        skills = {**load_skills(ROOT / "skills"), feature.name: feature}
        self.ledger.observe_issue(issue(id=OTHER, identifier="FARM-2"))
        self.ledger.ensure_session("session-2", OTHER, delegation=True)
        item = self.ledger.create_work_item(issue_id=OTHER, session_id="session-2", skill=feature.name)
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        nested = "x"
        for _ in range(sys.getrecursionlimit() + 10):
            nested = [nested]
        self.ledger.checkpoint(item["id"], token, {"plan": {"stages": {"A": "done"}, "prs": {"common": nested}}})
        with patch("agent.doctor.load_skills", return_value=skills):
            report = diagnose(self.config, now=1090)
        entry = next(entry for entry in report["jobs"] if entry["item_id"] == item["id"])
        self.assertEqual((entry["plan"]["stages"], entry["plan"]["prs"]), ({"A": "done"}, []))

    def test_a_clone_holding_what_farmbot_did_not_write_is_reported_by_name(self):
        """Plan P10: FarmBot's git refuses such a clone; doctor says which clone and what it holds, never a value."""
        remote = "https://github.com/Kuaiwa-Network/Farm-Client.git"
        config = replace(self.config, repos={"Farm-Client": remote})
        clone = self.paths.repos / "Farm-Client.git"
        clone.parent.mkdir(parents=True, exist_ok=True)
        for args in (["init", "-q", "--bare", str(clone)], ["--git-dir", str(clone), "remote", "add", "origin", remote],
                     ["--git-dir", str(clone), "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"]):
            subprocess.run(["git", *args], check=True, capture_output=True)
        self.assertNotIn("clone_unexpected", self.codes(diagnose(config, now=1001)))
        subprocess.run(["git", "--git-dir", str(clone), "config", "core.sshCommand", "secret-command-value"], check=True)
        report = diagnose(config, now=1001)
        finding = next(f for f in report["findings"] if f["code"] == "clone_unexpected")
        self.assertEqual(finding["clones"], {"Farm-Client": ["config key core.sshcommand"]})
        self.assertNotIn("secret-command-value", json.dumps(report, ensure_ascii=False))

    def test_the_runtime_finding_needs_no_ledger_and_changes_none(self):
        """The finding comes from the config and the manifests alone, so a host's doctor shows it before any job
        has run, and before the host's first initialization too."""
        data = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.config_path.write_text(json.dumps({**data, "runtime": "claude"}), encoding="utf-8")
        before = list(self.ledger.connection.iterdump())

        def doctor():
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = main(["doctor", "--config", str(self.config_path)])
            return result, self.codes(json.loads(output.getvalue()))
        self.assertEqual(doctor(), (1, {"skill_runtime_unsupported"}))
        self.assertEqual(list(self.ledger.connection.iterdump()), before)
        self.ledger.close()
        self.paths.ledger.unlink()
        self.assertEqual(doctor(), (2, {"ledger_unreadable", "skill_runtime_unsupported"}))
        self.assertFalse(self.paths.ledger.exists())


@unittest.skipIf(os.name == "nt", "POSIX process inspection")
class ProcessProbeTests(unittest.TestCase):
    def test_real_worker_is_recognized_and_reaped_worker_is_dead(self):
        with subprocess.Popen(["sleep", "30"]) as process:
            try:
                self.assertEqual(probe_process(process.pid, "sleep")["state"], "alive")
                self.assertEqual(probe_process(process.pid, "unrelated-job")["state"], "unknown")
            finally:
                process.terminate()
                process.wait(timeout=5)
            self.assertEqual(probe_process(process.pid, "sleep")["state"], "dead")

    def test_permission_denied_and_ps_failure_are_unknown_not_dead(self):
        with patch("agent.doctor.os.kill", side_effect=PermissionError()):
            self.assertEqual(probe_process(os.getpid(), "job")["state"], "unknown")
        with patch("agent.doctor.subprocess.run", side_effect=subprocess.TimeoutExpired("ps", 5)):
            self.assertEqual(probe_process(os.getpid(), "job")["state"], "unknown")

    def test_invalid_pids_are_never_passed_to_os_kill(self):
        with patch("agent.doctor.os.kill", side_effect=AssertionError("invalid process group probe")):
            for pid in (0, -1, None, "42"):
                self.assertEqual(probe_process(pid, "job")["state"], "unknown")
