import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from agent.launcher import Launcher, RUNTIMES, Finished
from agent.ledger import Ledger, LedgerError
from agent.workflow import delegate_task
import test_scheduler

CAPACITY = "ERROR: Selected model is at capacity. Please try a different model.\n"
ROLLOUT_NOT_FOUND = ("2026-10-12T13:00:49.179447Z ERROR codex_core::session: "
                     "failed to record rollout items: thread 01a1302b-0ccd-7421-97b4-756cf1e1e7ce not found\n")
UNKNOWN_PROCESS_ID = ("2026-10-26T14:02:45.302374Z ERROR codex_core::tools::router: "
                      "error=exec_command failed: UnknownProcessId { process_id: 92907 }\n")


class CapacitySchedulerTests(unittest.TestCase):
    setUp = test_scheduler.SchedulerTests.setUp
    item = test_scheduler.SchedulerTests.item
    # Reuse the scheduler fixture, not its unrelated platform integration cases.
    def capacity_exit(self, item):
        self.launcher.finished.append(Finished(item["id"], 1, "", False, "exited", "model_capacity",
                                               self.ledger.item(item["id"])["worker_pid"]))
        self.scheduler._reap()

    def test_capacity_requeues_with_delay_and_preserves_claim_checkpoint_and_worktrees(self):
        item = self.item()
        self.scheduler.tick()
        token = self.ledger.claim(item["id"], worker_id="first")["token"]
        self.capacity_exit(item)
        row = self.ledger.item(item["id"])
        self.assertEqual(row["state"], "queued")
        self.assertEqual(row["capacity_retries"], 1)
        self.assertEqual(row["retry_not_before"], self.now + 60)
        self.assertIsNone(row["worker_pid"])
        self.assertEqual(row["checkpoint"]["worker_id"], "first")
        self.assertFalse(any(r[0] == "removed" for r in self.trees.added))
        with self.assertRaises(LedgerError):
            self.ledger.renew(item["id"], token)
        self.assertEqual(self.scheduler.tick()["launched"], 0)
        self.now += 60
        self.assertEqual(self.scheduler.tick()["launched"], 1)
        self.assertEqual(len(self.launcher.spawned), 2)
        self.assertEqual(self.launcher.spawned[-1][0], item["id"])

    def test_preclaim_capacity_retry_survives_ledger_reopen_and_stops_after_three(self):
        item = self.item()
        for attempt, delay in enumerate((60, 180, 600), 1):
            self.scheduler.tick()
            self.capacity_exit(item)
            other = Ledger(self.scheduler.db_path, clock=lambda: self.now)
            try:
                self.assertEqual(other.item(item["id"])["capacity_retries"], attempt)
                self.assertEqual(other.queue(), [])
            finally:
                other.close()
            self.now += delay
        self.scheduler.tick()
        self.capacity_exit(item)
        self.assertEqual(self.ledger.item(item["id"])["state"], "failed")

    def test_parent_capacity_retry_keeps_running_presentation_task_and_claim(self):
        parent = self.item(skill="work")
        self.ledger.set_worker(parent["id"], 101, "h")
        token = self.ledger.claim(parent["id"], worker_id="general")["token"]
        self.ledger.checkpoint(parent["id"], token, {"plan": {"stages": {"A": "done"}}})
        child = delegate_task(self.ledger, parent["id"], token,
                              {"key": "ui", "capability": "fgui", "instructions": "Continue approved UI."})
        self.ledger.set_worker(child["id"], 102, "h")
        child_token = self.ledger.claim(child["id"], worker_id="presentation")["token"]
        self.ledger.checkpoint(child["id"], child_token, {"stage": "client-handoff"})
        checkpoint = self.ledger.item(child["id"])["checkpoint"]

        self.capacity_exit(parent)

        self.assertEqual(self.ledger.item(parent["id"])["state"], "queued")
        self.assertEqual(self.ledger.item(child["id"])["state"], "running")
        self.assertEqual(self.ledger.item(child["id"])["checkpoint"], checkpoint)
        self.ledger.renew(child["id"], child_token)
        self.assertIsNone(self.ledger.cleanup_record(child["id"]))
        self.assertFalse(any(r[0] == "removed" for r in self.trees.added))

    def test_cancelled_capacity_exit_is_not_requeued(self):
        item = self.item()
        self.scheduler.tick()
        self.ledger.cancel(item["id"], "human stopped")
        self.capacity_exit(item)
        self.assertEqual(self.ledger.item(item["id"])["state"], "cancelled")

    def test_stale_capacity_exit_cannot_requeue_a_different_worker(self):
        item = self.item()
        self.scheduler.tick()
        self.assertIsNone(self.ledger.defer_capacity_retry(item["id"], 999999))
        self.assertIsNotNone(self.ledger.item(item["id"])["worker_pid"])

    def test_delayed_job_cannot_be_claimed_early(self):
        item = self.item()
        self.scheduler.tick()
        self.capacity_exit(item)
        with self.assertRaisesRegex(LedgerError, "delay"):
            self.ledger.claim(item["id"], worker_id="too-early")

    def test_human_chat_retry_resets_exhausted_allowance(self):
        from test_ledger import ISSUE, SESSION
        from test_receiver import APP
        item = self.item(delegate_id=APP)
        for delay in (60, 180, 600):
            self.scheduler.tick()
            self.capacity_exit(item)
            self.now += delay
        self.scheduler.tick()
        self.capacity_exit(item)
        chat = self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill="chat", execution_contract='staged-v1')
        self.ledger.push_inbox(chat["id"], "retry")
        token = self.ledger.claim(chat["id"], worker_id="chat")["token"]
        message = self.ledger.issue_context(chat["id"])["session_messages"][-1]["id"]
        resumed = self.ledger.resume_work(chat["id"], token, message, APP)
        self.assertEqual(resumed["id"], item["id"])
        self.assertEqual(resumed["capacity_retries"], 0)
        self.assertEqual(resumed["retry_not_before"], 0)

    def test_other_errors_keep_failure_behavior(self):
        item = self.item()
        self.scheduler.tick()
        self.launcher.finished.append(Finished(item["id"], 1, "", False, "exited"))
        self.scheduler._reap()
        self.assertEqual(self.ledger.item(item["id"])["state"], "failed")

    def test_delayed_retry_frees_capacity_for_another_job(self):
        from test_ledger import OTHER
        item = self.item()
        self.scheduler.tick()
        other = self.item(issue_id=OTHER, session="second")
        self.capacity_exit(item)
        self.assertEqual(self.scheduler.tick()["launched"], 1)
        self.assertEqual(self.launcher.spawned[-1][0], other["id"])


class CapacityLauncherTests(unittest.TestCase):
    def run_cli(self, text, code=1):
        with tempfile.TemporaryDirectory() as tmp:
            script = "import sys;sys.stdin.read();sys.stderr.write(%r);sys.exit(%d)" % (text, code)
            # Runtime argv is a format template; diagnostics may contain literal braces.
            script = script.replace("{", "{{").replace("}", "}}")
            runtime = RUNTIMES["codex"]._replace(command=[sys.executable, "-c", script], seed_files={})
            launcher = Launcher(tmp, runtime, "test")
            launcher.spawn("item", "hello", {}, 30, tmp)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                result = launcher.poll()
                if result:
                    return result[0]
                time.sleep(0.02)
            self.fail("fake runtime did not exit")

    def test_new_attempt_archives_protected_old_claim_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher = Launcher(tmp, RUNTIMES["fake"], "test")
            state = launcher.state_dir("item")
            state.mkdir()
            old = state / "token"
            old.write_text("obsolete-test-token")
            old.chmod(0o400)
            handle = launcher.spawn("item", "{}", {}, 30, tmp)
            try:
                self.assertFalse(old.exists())
                saved = handle.run_dir / "previous-claim.token"
                self.assertEqual(saved.read_text(), "obsolete-test-token")
                old.write_text("new-test-token")
            finally:
                handle.process.wait(timeout=10)
                for token in state.rglob("*token*"):
                    token.chmod(0o600)

    def test_terminal_capacity_error_is_classified(self):
        result = self.run_cli("ERROR: Selected model is at capacity. Please try a different model.\n\ntokens used\n245,179\n")
        self.assertEqual(result.failure_kind, "model_capacity")
        self.assertFalse(result.killed)

    def test_capacity_with_shutdown_recording_error_is_classified(self):
        for footer in ("", "\ntokens used\n163,110\n"):
            with self.subTest(footer=footer):
                result = self.run_cli(CAPACITY * 2 + ROLLOUT_NOT_FOUND + footer)
                self.assertEqual(result.failure_kind, "model_capacity")
                self.assertFalse(result.killed)

    def test_capacity_with_shutdown_process_error_is_classified(self):
        # FARM-1917: a missing-process diagnostic between rollout errors masked
        # the capacity exit, bypassing the scheduler's bounded retry path.
        shutdown = ROLLOUT_NOT_FOUND + UNKNOWN_PROCESS_ID + ROLLOUT_NOT_FOUND
        rate_limit = "ERROR: exceeded retry limit, last status: 429 Too Many Requests\n"
        for diagnostic in (CAPACITY * 2, rate_limit):
            for suffix in (UNKNOWN_PROCESS_ID, shutdown + "tokens used\n273,004\n"):
                with self.subTest(diagnostic=diagnostic, suffix=suffix):
                    result = self.run_cli(diagnostic + suffix)
                    self.assertEqual(result.failure_kind, "model_capacity")
                    self.assertFalse(result.killed)

    def test_shutdown_process_error_does_not_hide_other_failures(self):
        for text, code in (
                (UNKNOWN_PROCESS_ID, 1),
                (CAPACITY + UNKNOWN_PROCESS_ID, 0),
                ("docs: " + CAPACITY + UNKNOWN_PROCESS_ID, 1),
                (CAPACITY + "ERROR: Unauthorized\n" + UNKNOWN_PROCESS_ID, 1),
                (CAPACITY + UNKNOWN_PROCESS_ID + "ERROR: Unauthorized\n", 1),
                (CAPACITY + "tool output: " + UNKNOWN_PROCESS_ID, 1),
                (CAPACITY + UNKNOWN_PROCESS_ID.replace("exec_command", "write_stdin"), 1),
                (CAPACITY + UNKNOWN_PROCESS_ID.replace("92907", "unknown"), 1),
                (CAPACITY + UNKNOWN_PROCESS_ID.replace("UnknownProcessId", "PermissionDenied"), 1)):
            with self.subTest(text=text, code=code):
                self.assertIsNone(self.run_cli(text, code).failure_kind)

    def test_shutdown_recording_error_does_not_hide_other_failures(self):
        for text, code in (
                (ROLLOUT_NOT_FOUND, 1),
                (CAPACITY + ROLLOUT_NOT_FOUND, 0),
                ("docs: " + CAPACITY + ROLLOUT_NOT_FOUND, 1),
                (CAPACITY + "ERROR: Unauthorized\n" + ROLLOUT_NOT_FOUND, 1),
                (CAPACITY + ROLLOUT_NOT_FOUND + "ERROR: Unauthorized\n", 1),
                (CAPACITY + ROLLOUT_NOT_FOUND.replace("not found", "permission denied"), 1)):
            with self.subTest(text=text, code=code):
                self.assertIsNone(self.run_cli(text, code).failure_kind)

    def test_quoted_or_nonterminal_message_and_success_are_not_capacity(self):
        msg = "ERROR: Selected model is at capacity. Please try a different model.\n"
        for text, code in ((msg, 0), ("docs: " + msg, 1), (msg + "ERROR: Unauthorized\n", 1)):
            with self.subTest(text=text, code=code):
                self.assertIsNone(self.run_cli(text, code).failure_kind)
