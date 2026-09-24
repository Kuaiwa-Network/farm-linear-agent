"""Doctor exercises real ledgers and the public CLI without repairing either."""
import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent.config import Config, Paths
from agent.doctor import diagnose, probe_process
from agent.ledger import Ledger
from agent.service import main
from test_ledger import ISSUE, SESSION, PIN, issue


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
