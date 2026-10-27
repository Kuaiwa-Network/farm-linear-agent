"""Local source-read processes, including Windows Git-wrapper-shaped children."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from agent import fgui_git
from agent.ledger import LedgerError


class GitProcessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="UI Git 中文 ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def run_script(self, code, *, data=None, timeout=5, limit=4 << 20):
        script = self.root / "read process.py"
        script.write_text(code, encoding="utf-8")
        return fgui_git.run([sys.executable, str(script)], cwd=self.root,
                            env=dict(os.environ), data=data, timeout=timeout, limit=limit)

    def test_large_input_and_output_round_trip_without_pipe_blocking(self):
        data = "路径 with spaces\n".encode("utf-8") * 40000
        result = self.run_script("import sys\nsys.stdout.buffer.write(sys.stdin.buffer.read())\n", data=data)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, data)

    def test_nonzero_status_and_stderr_are_preserved(self):
        result = self.run_script('import sys\nsys.stderr.write("failed")\nsys.exit(7)\n')
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stderr, b"failed")


@unittest.skipUnless(os.name == "nt", "Windows Job Objects")
class WindowsGitProcessTests(GitProcessTests):
    def child_script(self, *, parent_exits=False):
        self.pid_file = self.root / "child.pid"
        return ('import subprocess,sys,time\nfrom pathlib import Path\n'
                'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])\n'
                f'Path({str(self.pid_file)!r}).write_text(str(child.pid))\n'
                + ('' if parent_exits else 'time.sleep(60)\n'))

    def test_timeout_drains_launcher_and_descendants_without_waiting_for_pipe_eof(self):
        from agent.windows_job import alive
        unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(lambda: (unrelated.terminate(), unrelated.wait(timeout=5)) if unrelated.poll() is None else None)
        start = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_script(self.child_script(), timeout=2)
        self.assertLess(time.monotonic() - start, 10)
        self.assertFalse(alive(int(self.pid_file.read_text())))
        self.assertIsNone(unrelated.poll())

    def test_parent_exit_with_lingering_descendants_is_refused_and_cleaned(self):
        from agent.windows_job import alive
        with self.assertRaisesRegex(LedgerError, "running descendants"):
            self.run_script(self.child_script(parent_exits=True))
        self.assertFalse(alive(int(self.pid_file.read_text())))

    def test_assignment_failure_never_executes_source_read(self):
        marker = self.root / "must-not-exist"
        with patch("agent.windows_job.WindowsJob.assign", side_effect=OSError("denied")), self.assertRaises(OSError):
            self.run_script(f'from pathlib import Path\nPath({str(marker)!r}).touch()\n')
        self.assertFalse(marker.exists())

    def test_delayed_assignment_keeps_process_suspended(self):
        from agent.windows_job import WindowsJob, descendants
        assign = WindowsJob.assign
        marker = self.root / "started"
        def delayed(job, process):
            time.sleep(.1)
            self.assertFalse(marker.exists())
            self.assertEqual(descendants(process.pid), [])
            assign(job, process)
        with patch.object(WindowsJob, "assign", delayed):
            self.run_script(f'from pathlib import Path\nPath({str(marker)!r}).touch()\n')
        self.assertTrue(marker.exists())

    def test_output_overflow_is_refused(self):
        with self.assertRaisesRegex(LedgerError, "bounded inventory"):
            self.run_script('import sys\nsys.stdout.write("x" * 5000)\n', limit=100)

    def test_uncertain_teardown_is_not_reported_as_retryable_timeout(self):
        with patch("agent.windows_job.WindowsJob.terminate_and_wait", side_effect=RuntimeError("not drained")), \
                self.assertRaisesRegex(LedgerError, "cleanup is uncertain"):
            self.run_script('import time\ntime.sleep(60)\n', timeout=.2)


if __name__ == "__main__":
    unittest.main()
