"""Portable exports use real temporary ledgers, never live credentials or services."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from agent.config import Config, Paths
from agent import diagnostics
from agent.heartbeat import Heartbeat
from agent.ledger import Ledger
from agent.service import main
from test_ledger import ISSUE, OTHER, PIN, SESSION, issue


class DiagnosticExportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "诊断 with spaces"
        self.root.mkdir()
        self.config = Config("app-id", "client-secret-unique", "webhook-secret-unique", host="fixture-host",
                             local_root=self.root / "state", enabled_skills=["chat"])
        self.paths = Paths(self.config)
        self.config_path = self.root / "private-config.json"
        self.config_path.write_text(json.dumps({"client_id": self.config.client_id,
            "client_secret": self.config.client_secret, "webhook_secret": self.config.webhook_secret,
            "local_root": str(self.config.local_root), "host": self.config.host, "enabled_skills": ["chat"]}), encoding="utf-8")
        self.config.source_path = self.config_path
        self.ledger = Ledger(self.paths.ledger)
        self.addCleanup(self.ledger.close)
        self.ledger.observe_issue(issue(description="PRIVATE ISSUE BODY", title="PRIVATE ISSUE TITLE"))
        self.ledger.ensure_session(SESSION, ISSUE, delegation=True)
        self.item = self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill="fix", target=PIN,
                                                 execution_contract="staged-v1")
        self.ledger.set_worker(self.item["id"], 4242, self.config.host)
        self.claim = self.ledger.claim(self.item["id"], worker_id="fixture-worker")
        self.ledger.connection.execute("UPDATE work_items SET checkpoint=? WHERE id=?", (json.dumps({
            "summary": "Regression in scheduler", "candidates": {"Farm-Client": "a" * 40},
            "handoff": "Run affected tests", "pending_question": "PRIVATE QUESTION",
            "token": self.claim["token"]}), self.item["id"]))
        self.attempt = self.paths.runs / self.item["id"] / "20261026T000000Z-abcdef123456"
        self.attempt.mkdir(parents=True)
        for name in diagnostics.LOG_NAMES:
            (self.attempt / name).write_text("ordinary evidence 中文\n", encoding="utf-8")
        (self.attempt / "prompt.txt").write_text("PRIVATE PROMPT", encoding="utf-8")
        (self.attempt / "auth.json").write_text('{"secret":"PRIVATE AUTH"}', encoding="utf-8")
        (self.attempt.parent / "token").write_text(self.claim["token"], encoding="utf-8")
        Heartbeat(runtime="codex", revision=("b" * 12, False)).write(self.paths.heartbeat)
        self.output = self.root / "diagnostics.zip"
        self.enterContext(patch("agent.doctor.probe_process", return_value={"state": "alive", "reason": "job_marker_matches"}))
        self.enterContext(patch("agent.diagnostics._revision", return_value={"commit": "c" * 40, "dirty": True}))

    def export(self, **kwargs):
        result = diagnostics.export(self.config, self.output, **kwargs)
        with zipfile.ZipFile(self.output) as archive:
            data = {name: archive.read(name).decode("utf-8") for name in archive.namelist()}
        return result, data

    def test_cli_default_bundle_is_read_only_and_contains_portable_metadata(self):
        before = list(self.ledger.connection.iterdump())
        config_bytes = self.config_path.read_bytes()
        self.config_path.chmod(0o640)
        mode = self.config_path.stat().st_mode
        output = io.StringIO()
        with patch("agent.service.Ledger", side_effect=AssertionError("must not open Ledger")), \
                patch("agent.ledger.Ledger", side_effect=AssertionError("must not open Ledger")), \
                contextlib.redirect_stdout(output):
            code = main(["export-diagnostics", "--config", str(self.config_path), "--output", str(self.output),
                         "--issue", "farm-1"])
        self.assertEqual(code, 0, output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["status"], "complete")
        self.assertEqual(list(self.ledger.connection.iterdump()), before)
        self.assertEqual(self.config_path.read_bytes(), config_bytes)
        self.assertEqual(self.config_path.stat().st_mode, mode)
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(set(archive.namelist()), {"README.md", "manifest.json", "host.json", "heartbeat.json",
                                                      "doctor.json", "jobs.json"})
            host = json.loads(archive.read("host.json"))
            self.assertEqual(host["exporter"], {"commit": "c" * 40, "dirty": True})
            self.assertEqual(json.loads(archive.read("heartbeat.json"))["heartbeat"]["revision"], "b" * 12)
            manifest = json.loads(archive.read("manifest.json"))
            for entry in manifest["files"]:
                content = archive.read(entry["path"])
                self.assertEqual(entry["sha256"], hashlib.sha256(content).hexdigest())
                self.assertEqual(entry["bytes"], len(content))
            all_text = "\n".join(archive.read(name).decode("utf-8") for name in archive.namelist())
        for secret in (self.claim["token"], self.config.client_secret, self.config.webhook_secret,
                       "PRIVATE ISSUE BODY", "PRIVATE ISSUE TITLE", "PRIVATE QUESTION", "PRIVATE PROMPT", "PRIVATE AUTH"):
            self.assertNotIn(secret, all_text)
        self.assertNotIn(str(self.config.local_root), all_text)
        self.assertNotIn(self.root.as_posix(), all_text)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)

    def test_issue_selection_includes_terminal_history_and_excludes_other_jobs(self):
        self.ledger.connection.execute("UPDATE work_items SET state='delivered'")
        self.ledger.observe_issue(issue(id=OTHER, identifier="FARM-2"))
        self.ledger.ensure_session("other-session", OTHER, delegation=False)
        other = self.ledger.create_work_item(issue_id=OTHER, session_id="other-session", skill="chat")
        result, files = self.export(issue=ISSUE)
        jobs = json.loads(files["jobs.json"])
        self.assertEqual([job["id"] for job in jobs], [self.item["id"]])
        self.assertEqual(jobs[0]["state"], "delivered")
        self.assertEqual(jobs[0]["checkpoint"]["candidates"]["Farm-Client"], "a" * 40)
        self.assertNotIn(other["id"], files["jobs.json"])

    def test_no_selection_exports_host_diagnostics_without_job_details(self):
        _, files = self.export()
        self.assertEqual(json.loads(files["jobs.json"]), [])
        self.assertFalse(any(name.startswith("logs/") for name in files))

    def test_logs_are_selected_allowlisted_and_redacted(self):
        secrets = [self.config.client_secret, self.config.webhook_secret, self.claim["token"],
                   "res_" + "r" * 43, "ghp_" + "g" * 36, "gh-token-env-value", "a-secret-env-value"]
        text = "\n".join(secrets) + "\n" + json.dumps({
            "message": "a useful stack trace 中文", "nested": {"password": "hidden password", "apiKey": "hidden api key"},
            "url": "https://user:pass@example.com/file.zip?signature=hidden-signature#hidden-fragment",
            "state": str(self.config.local_root)}) + "\nAuthorization: Bearer unknown-auth-value\n"
        text += "--client-secret cli-value\nX-API-Key: header-value\npassword='quoted private value'\n"
        (self.attempt / "stderr.log").write_text(text, encoding="utf-8")
        with patch.dict(os.environ, {"GH_TOKEN": "gh-token-env-value", "CUSTOM_SECRET": "a-secret-env-value"}):
            _, files = self.export(item=self.item["id"], include_logs=True)
        exported = "\n".join(files.values())
        for secret in (*secrets, "hidden password", "hidden api key", "hidden-signature", "hidden-fragment",
                       "user:pass", "unknown-auth-value", "cli-value", "header-value", "quoted private value"):
            self.assertNotIn(secret, exported)
        self.assertIn("a useful stack trace 中文", exported)
        self.assertIn("https://example.com/file.zip", exported)
        self.assertIn("<STATE_ROOT>", exported)
        logs = [name for name in files if name.startswith("logs/")]
        self.assertEqual(len(logs), 3)
        self.assertTrue(all(name.rsplit("/", 1)[1] in diagnostics.LOG_NAMES for name in logs))

    def test_unknown_or_invalid_selection_does_not_create_output(self):
        for selection in ({"issue": "FARM-999"}, {"issue": "../../private"}, {"item": "../outside"},
                          {"issue": "FARM-1", "item": self.item["id"]}, {"include_logs": True}):
            with self.subTest(selection=selection), self.assertRaises(diagnostics.ExportError):
                self.export(**selection)
            self.assertFalse(self.output.exists())

    def test_output_refuses_overwrite_and_state_root(self):
        self.output.write_bytes(b"existing archive")
        with self.assertRaises(diagnostics.ExportError):
            self.export()
        self.assertEqual(self.output.read_bytes(), b"existing archive")
        with self.assertRaises(diagnostics.ExportError):
            diagnostics.export(self.config, self.config.local_root / "bundle.zip")

    def test_concurrent_output_is_not_overwritten(self):
        original = diagnostics._job_snapshot
        def competing_writer(*args):
            self.output.write_bytes(b"other export")
            return original(*args)
        with patch("agent.diagnostics._job_snapshot", side_effect=competing_writer), self.assertRaises(diagnostics.ExportError):
            self.export()
        self.assertEqual(self.output.read_bytes(), b"other export")

    def test_failed_write_removes_only_the_new_partial_output(self):
        with patch("zipfile.ZipFile.writestr", side_effect=OSError("PRIVATE WRITE ERROR")), \
                self.assertRaises(diagnostics.ExportError) as exc:
            self.export()
        self.assertNotIn("PRIVATE WRITE ERROR", str(exc.exception))
        self.assertFalse(self.output.exists())
        self.assertTrue(self.config_path.is_file())

    def test_missing_database_is_not_created_and_bundle_is_explicitly_partial(self):
        self.ledger.close()
        self.paths.ledger.unlink()
        result, files = self.export(issue="FARM-1")
        self.assertEqual(result["status"], "partial")
        self.assertFalse(self.paths.ledger.exists())
        self.assertEqual(json.loads(files["jobs.json"]), [])
        self.assertIn("ledger_unavailable_or_unsupported", files["manifest.json"])

    def test_old_schema_is_not_migrated(self):
        self.ledger.connection.execute("DROP TABLE issue_checks")
        before = list(self.ledger.connection.iterdump())
        result, files = self.export(item=self.item["id"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(list(self.ledger.connection.iterdump()), before)
        self.assertEqual(json.loads(files["jobs.json"])[0]["id"], self.item["id"])

    def test_corrupt_database_is_reported_without_repair(self):
        self.ledger.close()
        self.paths.ledger.write_bytes(b"not sqlite and not a token")
        result, _ = self.export(item=self.item["id"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(self.paths.ledger.read_bytes(), b"not sqlite and not a token")

    def test_missing_heartbeat_and_failed_doctor_do_not_leak_exception_text(self):
        self.paths.heartbeat.unlink()
        with patch("agent.diagnostics.diagnose", side_effect=RuntimeError("PRIVATE EXCEPTION")):
            result, files = self.export()
        self.assertEqual(result["status"], "partial")
        self.assertIn("RuntimeError", files["manifest.json"])
        self.assertNotIn("PRIVATE EXCEPTION", "".join(files.values()))

    def test_foreign_host_logs_are_not_read(self):
        self.ledger.connection.execute("UPDATE work_items SET host='another-host'")
        _, files = self.export(item=self.item["id"], include_logs=True)
        self.assertFalse(any(name.startswith("logs/") for name in files))
        self.assertIn("host_unverified", files["manifest.json"])

    def test_tail_limits_drop_cut_credential_line_and_report_truncation(self):
        (self.attempt / "stderr.log").write_text("secret=" + "s" * 150 + "\nlast useful line\n", encoding="utf-8")
        original = diagnostics._read
        def small_read(path, **kwargs):
            if kwargs.get("tail"):
                kwargs["limit"] = 60
            return original(path, **kwargs)
        with patch("agent.diagnostics._read", side_effect=small_read):
            result, files = self.export(item=self.item["id"], include_logs=True)
        self.assertEqual(result["status"], "partial")
        log = next(content for name, content in files.items() if name.endswith("stderr.log"))
        self.assertEqual(log, "last useful line\n")
        self.assertIn("file_tail_only", files["manifest.json"])

    def test_binary_log_is_omitted(self):
        (self.attempt / "stderr.log").write_bytes(b"private\x00binary\xff")
        _, files = self.export(item=self.item["id"], include_logs=True)
        self.assertFalse(any(name.endswith("stderr.log") for name in files))
        self.assertIn("missing_unsafe_or_non_utf8", files["manifest.json"])

    def test_maximum_length_plain_log_line_is_bounded_and_preserved(self):
        # Long identifiers with no credential key must not trigger quadratic regex backtracking.
        (self.attempt / "stderr.log").write_bytes(b"x" * diagnostics.MAX_FILE_BYTES)
        _, files = self.export(item=self.item["id"], include_logs=True)
        log = next(value for name, value in files.items() if name.endswith("stderr.log"))
        self.assertEqual(log, "x" * diagnostics.MAX_FILE_BYTES + "\n")

    def test_missing_exporter_revision_is_explicitly_partial(self):
        with patch("agent.diagnostics._revision", return_value={"commit": None, "dirty": None}):
            result, files = self.export()
        self.assertEqual(result["status"], "partial")
        self.assertIn("exporter_revision", files["manifest.json"])

    def test_recent_attempts_and_events_are_bounded(self):
        for index in range(8):
            (self.attempt.parent / f"20261026T00000{index}Z-abcdef").mkdir()
        for index in range(7):
            self.ledger._audit(self.item["id"], f"event_{index}", "PRIVATE AUDIT REASON", details={"secret": "PRIVATE DETAILS"})
        with patch("agent.diagnostics.MAX_ATTEMPTS", 2), patch("agent.diagnostics.MAX_EVENTS", 3):
            _, files = self.export(item=self.item["id"], include_logs=True)
        jobs = json.loads(files["jobs.json"])
        self.assertEqual([event["kind"] for event in jobs[0]["events"]], ["event_4", "event_5", "event_6"])
        self.assertIn("attempt_limit", files["manifest.json"])
        self.assertIn("event_limit", files["manifest.json"])
        self.assertNotIn("PRIVATE AUDIT", "".join(files.values()))

    def test_job_limit_includes_newest_jobs_and_reports_omitted_history(self):
        self.ledger.connection.execute("UPDATE work_items SET state='delivered',updated_at=1")
        newer = self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill="chat")
        with patch("agent.diagnostics.MAX_JOBS", 1):
            _, files = self.export(issue="FARM-1")
        self.assertEqual([job["id"] for job in json.loads(files["jobs.json"])], [newer["id"]])
        self.assertIn("job_limit", files["manifest.json"])

    def test_oversized_checkpoint_is_not_copied(self):
        self.ledger.connection.execute("UPDATE work_items SET checkpoint=?", (json.dumps({
            "summary": "x" * (diagnostics.MAX_FILE_BYTES + 1)}),))
        _, files = self.export(item=self.item["id"])
        self.assertNotIn("checkpoint", json.loads(files["jobs.json"])[0])
        self.assertIn("unavailable_or_oversized", files["manifest.json"])

    def test_incomplete_directory_scan_does_not_claim_newest_attempts(self):
        with patch("agent.diagnostics.MAX_DIRECTORY_ENTRIES", 0):
            _, files = self.export(item=self.item["id"], include_logs=True)
        self.assertFalse(any(name.startswith("logs/") for name in files))
        self.assertIn("directory_limit", files["manifest.json"])

    def test_payload_size_limit_is_recorded(self):
        with patch("agent.diagnostics.MAX_BUNDLE_BYTES", 10):
            result, files = self.export(item=self.item["id"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(set(files), {"manifest.json"})
        self.assertIn("bundle_size_limit", files["manifest.json"])

    def test_log_hardlink_is_refused(self):
        target = self.root / "private.txt"
        target.write_text("PRIVATE HARDLINK", encoding="utf-8")
        log = self.attempt / "stderr.log"
        log.unlink()
        os.link(target, log)
        _, files = self.export(item=self.item["id"], include_logs=True)
        self.assertNotIn("PRIVATE HARDLINK", "".join(files.values()))
        self.assertFalse(any(name.endswith("stderr.log") for name in files))

    def test_log_symlink_is_refused(self):
        target = self.root / "private.txt"
        target.write_text("PRIVATE SYMLINK", encoding="utf-8")
        log = self.attempt / "stderr.log"
        log.unlink()
        try:
            log.symlink_to(target)
        except OSError as exc:
            self.skipTest(f"symlink creation unavailable: {type(exc).__name__}")
        _, files = self.export(item=self.item["id"], include_logs=True)
        self.assertNotIn("PRIVATE SYMLINK", "".join(files.values()))
        self.assertFalse(any(name.endswith("stderr.log") for name in files))

    @unittest.skipUnless(os.name == "nt", "native Windows junction check")
    def test_windows_attempt_junction_is_refused(self):
        private = self.root / "private log folder"
        private.mkdir()
        (private / "stderr.log").write_text("PRIVATE JUNCTION", encoding="utf-8")
        junction = self.attempt.parent / "20261026T010000Z-junction"
        subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(private)],
                       check=True, capture_output=True)
        self.addCleanup(junction.rmdir)
        _, files = self.export(item=self.item["id"], include_logs=True)
        self.assertNotIn("PRIVATE JUNCTION", "".join(files.values()))
        self.assertFalse(any("010000Z-junction" in name for name in files))

    def test_changed_file_identity_is_refused(self):
        original = diagnostics._regular
        calls = 0
        def replaced(path, **kwargs):
            nonlocal calls
            if path == self.attempt / "stderr.log":
                calls += 1
                if calls == 2:
                    path.unlink()
                    path.write_text("replacement", encoding="utf-8")
            return original(path, **kwargs)
        with patch("agent.diagnostics._regular", side_effect=replaced):
            _, files = self.export(item=self.item["id"], include_logs=True)
        self.assertFalse(any(name.endswith("stderr.log") for name in files))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFO check")
    def test_fifo_log_is_refused_without_blocking(self):
        log = self.attempt / "stderr.log"
        log.unlink()
        os.mkfifo(log)
        _, files = self.export(item=self.item["id"], include_logs=True)
        self.assertFalse(any(name.endswith("stderr.log") for name in files))

    def test_cli_errors_require_explicit_config_and_export_only_flags(self):
        for args in (["export-diagnostics", "--output", str(self.output)],
                     ["export-diagnostics", "--config", str(self.config_path)],
                     ["doctor", "--output", str(self.output)], ["serve", "--include-logs"]):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                main(args)
            self.assertEqual(exc.exception.code, 2)

    def test_invalid_config_returns_safe_json_error_without_bundle(self):
        self.config_path.write_text("invalid private config text", encoding="utf-8")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(["export-diagnostics", "--config", str(self.config_path), "--output", str(self.output)])
        self.assertEqual(code, 1)
        self.assertNotIn("private config text", stdout.getvalue())
        self.assertEqual(json.loads(stdout.getvalue())["status"], "error")
        self.assertFalse(self.output.exists())

    def test_cli_partial_export_returns_two_and_names_created_bundle(self):
        self.paths.heartbeat.unlink()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(["export-diagnostics", "--config", str(self.config_path), "--output", str(self.output)])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stdout.getvalue())["output"], str(self.output))
        self.assertTrue(zipfile.is_zipfile(self.output))


class RedactionTests(unittest.TestCase):
    def setUp(self):
        self.redactor = diagnostics.Redactor(Config("app", "secret with spaces/and+symbols", "webhook-value"))

    def test_encoded_secret_json_nested_keys_and_pem(self):
        from urllib.parse import quote
        secret = quote("secret with spaces/and+symbols", safe="")
        self.assertNotIn(secret, self.redactor.text(secret))
        value = self.redactor.data({"outer": {"clientSecret": ["hidden", "value"]}})
        self.assertEqual(value["outer"]["clientSecret"], diagnostics.REDACTED)
        value = self.redactor.log("-----BEGIN RSA PRIVATE KEY-----\nPRIVATE KEY CONTENT\n-----END RSA PRIVATE KEY-----")
        self.assertNotIn("PRIVATE KEY CONTENT", value)
        value = self.redactor.log(json.dumps({"arguments": json.dumps({"accessToken": "nested-unknown-credential"})}))
        self.assertNotIn("nested-unknown-credential", value)
        value = self.redactor.log("PARTIAL PRIVATE KEY CONTENT\n-----END PRIVATE KEY-----\n")
        self.assertNotIn("PARTIAL PRIVATE KEY CONTENT", value)

    def test_url_credentials_all_queries_and_fragments_removed(self):
        value = self.redactor.text("https://user:password@example.com/a?X-Amz-Signature=secret&other=private#fragment")
        self.assertEqual(value, "https://example.com/a")

    def test_pretty_json_and_multiline_assignments(self):
        value = self.redactor.log(json.dumps({"access_token": {"nested": "pretty-json-secret"}}, indent=2))
        self.assertNotIn("pretty-json-secret", value)
        value = self.redactor.log('prefix\n"password":\n"multiline-secret"\nsuffix\n')
        self.assertNotIn("multiline-secret", value)
        self.assertIn("suffix", value)


if __name__ == "__main__":
    unittest.main()
