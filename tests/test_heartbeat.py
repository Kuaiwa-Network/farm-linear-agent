"""The heartbeat serve writes and the status monitor reads: recorded honestly, read defensively."""
from collections import namedtuple
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent import heartbeat
from agent.heartbeat import Heartbeat, read, source_revision, webhook_outcome


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class HeartbeatRecordTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.beat = Heartbeat(runtime="codex", revision=("a3f9f77c1d2e", False), clock=self.clock)

    def test_a_new_heartbeat_is_starting_with_no_loops(self):
        payload = self.beat.payload()
        self.assertEqual((payload["phase"], payload["started_at"], payload["stopped_at"]), ("starting", 1000.0, None))
        self.assertEqual(payload["loops"], {})
        self.assertEqual(payload["webhooks"]["counts"], dict.fromkeys(heartbeat.OUTCOMES, 0))

    def test_loops_record_work_and_errors_reset_after_a_success(self):
        self.beat.loop_started("schedule")
        self.clock.now = 1002.0
        self.beat.loop_failed("schedule", RuntimeError("private detail"))
        self.beat.loop_started("schedule")
        self.clock.now = 1003.0
        self.beat.loop_failed("schedule", TimeoutError())
        record = self.beat.payload()["loops"]["schedule"]
        self.assertEqual((record["consecutive_errors"], record["error_type"], record["error_at"]),
                         (2, "TimeoutError", 1003.0))
        self.clock.now = 1004.0
        self.beat.loop_started("schedule")
        self.clock.now = 1005.0
        self.beat.loop_finished("schedule")
        record = self.beat.payload()["loops"]["schedule"]
        self.assertEqual((record["started_at"], record["finished_at"], record["consecutive_errors"]),
                         (1004.0, 1005.0, 0))
        self.assertEqual(record["error_type"], "TimeoutError")  # the last error stays visible after recovery
        self.assertNotIn("private detail", json.dumps(self.beat.payload()))

    def test_webhook_outcomes_are_classified_and_rejections_timestamped(self):
        cases = [(200, "accepted", "accepted"), (200, "stop received", "accepted"), (200, "duplicate", "duplicate"),
                 (200, "ignored", "ignored"), (401, "invalid signature", "rejected"),
                 (403, "identity mismatch", "rejected"), (400, "invalid json", "malformed"),
                 (408, "body timeout", "malformed"), (413, "invalid body size", "malformed"),
                 (500, "receiver error", "failed")]
        for status, result, outcome in cases:
            with self.subTest(status=status, result=result):
                self.assertEqual(webhook_outcome(status, result), outcome)
        self.clock.now = 1010.0
        self.beat.webhook("Issue", 200, "ignored")
        self.clock.now = 1020.0
        self.beat.webhook("<script>", 401, "invalid signature")
        hooks = self.beat.payload()["webhooks"]
        self.assertEqual((hooks["last_at"], hooks["last_rejected_at"], hooks["last_type"]), (1020.0, 1020.0, None))
        self.assertEqual((hooks["counts"]["ignored"], hooks["counts"]["rejected"]), (1, 1))

    def test_workers_are_published_as_times_only(self):
        Handle = namedtuple("Handle", "item_id pid started_at deadline")
        item = "0f8fad5b-d9cb-469f-a165-70867728950e"
        beat = Heartbeat(runtime="codex", clock=self.clock,
                         workers=lambda: {item: Handle(item, 424242, 990.0, 29790.0)})
        payload = beat.payload()
        self.assertEqual(payload["workers"], {item: {"started_at": 990.0, "deadline": 29790.0}})
        self.assertNotIn("424242", json.dumps(payload))

    def test_an_unreadable_worker_source_leaves_workers_unknown(self):
        def broken():
            raise RuntimeError("launcher busy")

        self.assertIsNone(Heartbeat(runtime="codex", clock=self.clock, workers=broken).payload()["workers"])
        self.assertIsNone(Heartbeat(runtime="codex", clock=self.clock).payload()["workers"])

    def test_stopping_records_when_and_unknown_phases_are_refused(self):
        self.clock.now = 1100.0
        self.beat.set_phase("stopped")
        payload = self.beat.payload()
        self.assertEqual((payload["phase"], payload["stopped_at"]), ("stopped", 1100.0))
        with self.assertRaises(ValueError):
            self.beat.set_phase("paused")


class HeartbeatFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "state 状态" / "service-heartbeat.json"
        self.path.parent.mkdir()
        self.clock = Clock()

    def payload(self, **changes):
        return Heartbeat(runtime="codex", clock=self.clock).payload() | changes

    def write(self, value):
        self.path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")

    def test_a_written_beat_reads_back_fresh_then_stale(self):
        beat = Heartbeat(runtime="codex", revision=("a3f9f77c1d2e", True), clock=self.clock)
        beat.loop_started("receive")
        beat.webhook("AgentSessionEvent", 200, "accepted")
        beat.write(self.path)
        state, read_back = read(self.path, now=1030.0)
        self.assertEqual(state, "fresh")
        self.assertEqual((read_back["revision"], read_back["dirty"], read_back["runtime"]),
                         ("a3f9f77c1d2e", True, "codex"))
        self.assertEqual(read_back["loops"]["receive"]["started_at"], 1000.0)
        self.assertEqual(read_back["webhooks"]["counts"]["accepted"], 1)
        self.assertEqual(read(self.path, now=1000.0 + heartbeat.STALE_AFTER)[0], "stale")

    def test_a_beat_without_a_revision_reads_back_with_neither_known(self):
        Heartbeat(runtime="codex", clock=self.clock).write(self.path)
        state, read_back = read(self.path, now=1000.0)
        self.assertEqual((state, read_back["revision"], read_back["dirty"]), ("fresh", None, None))

    def test_missing_and_hostile_files_are_never_healthy(self):
        self.assertEqual(read(self.path, now=1000.0), ("missing", None))
        nan = json.dumps(self.payload()).replace('"written_at": 1000.0', '"written_at": NaN')
        hostile = ["not json", "[]", nan, "[" * 20_000, self.payload(schema_version=2),
                   self.payload(phase="paused"), self.payload(written_at=True), self.payload(written_at=1e300),
                   self.payload(written_at=10 ** 400),  # an int too large for a float: math.isfinite overflows
                   self.payload(revision="HEAD~1"), self.payload(runtime="<b>codex</b>"), self.payload(dirty="no"),
                   self.payload(dirty=0),
                   self.payload(loops={"schedule": {"consecutive_errors": -1}}),
                   self.payload(loops={"schedule": {"consecutive_errors": 0, "error_type": "</script>"}}),
                   self.payload(webhooks={"counts": {"rejected": "many"}})]
        for value in hostile:
            with self.subTest(value=str(value)[:60]):
                self.write(value)
                self.assertEqual(read(self.path, now=1000.0), ("unreadable", None))
        self.path.write_bytes(b"\xff\xfe")
        self.assertEqual(read(self.path, now=1000.0), ("unreadable", None))

    def test_worker_records_are_validated(self):
        item = "0f8fad5b-d9cb-469f-a165-70867728950e"
        self.write(self.payload(workers={item: {"started_at": 990.0, "deadline": 29790.0}}))
        self.assertEqual(read(self.path, now=1000.0)[1]["workers"], {item: {"started_at": 990.0, "deadline": 29790.0}})
        too_many = {f"0f8fad5b-d9cb-469f-a165-{n:012d}": {"started_at": 1.0, "deadline": 2.0}
                    for n in range(heartbeat.MAX_WORKERS + 1)}
        for workers in ({"not-a-uuid": {"started_at": 1.0, "deadline": 2.0}}, {item: {"started_at": 1.0}},
                        {item: []}, [], too_many):
            with self.subTest(workers=str(workers)[:40]):
                self.write(self.payload(workers=workers))
                self.assertEqual(read(self.path, now=1000.0), ("unreadable", None))

    def test_an_oversized_file_is_unreadable(self):
        self.write(self.payload(padding="x" * heartbeat.MAX_BYTES))
        self.assertEqual(read(self.path, now=1000.0), ("unreadable", None))

    def test_unknown_keys_and_loops_are_ignored_for_forward_compatibility(self):
        payload = self.payload(extra={"nested": True})
        payload["loops"] = {"future_loop": {"anything": 1},
                            "receive": {"started_at": 999.0, "finished_at": None, "consecutive_errors": 0,
                                        "error_type": None, "error_at": None}}
        self.write(payload)
        state, beat = read(self.path, now=1000.0)
        self.assertEqual(state, "fresh")
        self.assertEqual(list(beat["loops"]), ["receive"])

    @unittest.skipIf(os.name == "nt", "FIFOs and O_NOFOLLOW symlink refusal are POSIX")
    def test_a_fifo_or_symlink_is_unreadable_without_waiting(self):
        os.mkfifo(self.path)
        self.assertEqual(read(self.path, now=1000.0), ("unreadable", None))
        self.path.unlink()
        target = Path(self.tmp.name) / "elsewhere.json"
        target.write_text(json.dumps(self.payload()), encoding="utf-8")
        self.path.symlink_to(target)
        self.assertEqual(read(self.path, now=1000.0), ("unreadable", None))

    @unittest.skipIf(os.name == "nt", "FIFOs are POSIX")
    def test_write_replaces_a_symlink_or_fifo_instead_of_opening_it(self):
        target = Path(self.tmp.name) / "elsewhere.json"
        target.write_text("untouched", encoding="utf-8")
        self.path.symlink_to(target)
        Heartbeat(runtime="codex", clock=self.clock).write(self.path)
        self.assertFalse(self.path.is_symlink())
        self.assertEqual(target.read_text(encoding="utf-8"), "untouched")
        self.path.unlink()
        os.mkfifo(self.path)
        Heartbeat(runtime="codex", clock=self.clock).write(self.path)  # opening the FIFO would block forever
        self.assertEqual(read(self.path, now=1000.0)[0], "fresh")

    def test_a_failed_replace_leaves_no_temporary_file(self):
        with patch("agent.launcher.os.replace", side_effect=PermissionError("sharing violation")):
            with self.assertRaises(PermissionError):
                Heartbeat(runtime="codex", clock=self.clock).write(self.path)
        self.assertEqual(list(self.path.parent.iterdir()), [])


class SourceRevisionTests(unittest.TestCase):
    def test_a_checkout_reports_its_commit_and_whether_tracked_files_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "checkout 检出"
            root.mkdir()

            def git(*args):
                return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root,
                                      check=True, capture_output=True, text=True).stdout.strip()

            git("init", "-q", "-b", "main", ".")
            (root / "tracked.txt").write_text("one", encoding="utf-8")
            git("add", ".")
            git("commit", "-qm", "init")
            head = git("rev-parse", "HEAD")
            self.assertEqual(source_revision(root), (head[:12], False))
            (root / "untracked.txt").write_text("ignored", encoding="utf-8")
            self.assertEqual(source_revision(root), (head[:12], False))
            (root / "tracked.txt").write_text("two", encoding="utf-8")
            self.assertEqual(source_revision(root), (head[:12], True))

    def test_outside_a_checkout_or_without_git_there_is_no_revision(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"GIT_CEILING_DIRECTORIES": str(Path(tmp).resolve().parent)}):
            self.assertEqual(source_revision(Path(tmp)), (None, None))
        with patch("agent.heartbeat.subprocess.run", side_effect=FileNotFoundError("git")):
            self.assertEqual(source_revision(Path.cwd()), (None, None))
        with patch("agent.heartbeat.subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout="HEAD\n")):
            self.assertEqual(source_revision(Path.cwd()), (None, None))
