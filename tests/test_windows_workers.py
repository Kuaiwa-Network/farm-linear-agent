"""Real Windows process-tree lifecycle checks (no Unity/model dependencies)."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from agent.launcher import Launcher, RUNTIMES


@unittest.skipUnless(os.name == 'nt', 'Windows Job Objects')
class WindowsWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.launcher = Launcher(self.root / 'runs', RUNTIMES['fake'], 'test')

    def finished(self):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            result = self.launcher.poll()
            if result:
                return result[0]
            time.sleep(.03)
        self.fail('worker did not finish')

    def test_assignment_precedes_even_python_redirector_and_gate_startup(self):
        from agent.windows_job import WindowsJob, process_ids
        import agent.launcher
        marker = self.root / 'gate-started.txt'
        real_gate = Path(agent.launcher.__file__).with_name('windows_worker_gate.py').read_text(encoding='utf-8')
        (self.root / 'windows_worker_gate.py').write_text(
            'from pathlib import Path\n' + f'Path({str(marker)!r}).write_text("started")\n' + real_gate,
            encoding='utf-8')
        real_assign = WindowsJob.assign
        def delayed(job, process):
            # A deliberately slow host assignment must not let a venv redirector
            # or ordinary Python execute even the gate's first source statement.
            time.sleep(1)
            self.assertFalse(marker.exists())
            self.assertEqual(Launcher.descendants(process.pid), [])
            real_assign(job, process)
        with patch.object(agent.launcher, '__file__', str(self.root / 'launcher.py')), \
                patch.object(WindowsJob, 'assign', delayed):
            handle = self.launcher.spawn('suspended', 'prompt', {}, 30, self.root, extra_env={'FAKE_CLI_MODE':'sleep'})
        self.addCleanup(self.launcher.stop, 'suspended', 2)
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue(marker.exists())
        # Read ancestry first: the gate may create another sleeping descendant
        # between snapshots, and a previously read Job list would be stale.
        descendants = set(Launcher.descendants(handle.pid))
        owned = set(process_ids(self.launcher._jobs['suspended'].handle))
        self.assertTrue(descendants <= owned)
        self.launcher.stop('suspended', 2); self.finished()
        self.launcher.assert_quiescent('suspended', handle.pid)

    def test_resume_failure_starts_no_gate_and_retains_not_started_evidence(self):
        from agent.windows_job import WindowsJob
        with patch.object(WindowsJob, 'resume', side_effect=OSError('startup unavailable')), self.assertRaises(OSError):
            self.launcher.spawn('resume-failed', 'prompt', {}, 30, self.root)
        self.assertEqual(self.launcher.running(), {})
        records = list((self.root / 'runs/resume-failed').glob('*/process.json'))
        self.assertEqual(len(records), 1)
        self.assertEqual(json.loads(records[0].read_text(encoding='utf-8')), {'state':'not_started'})
        self.launcher.assert_quiescent('resume-failed', None)

    def test_unassigned_suspended_creation_object_cannot_be_resumed(self):
        from agent.windows_job import WindowsJob, CREATE_SUSPENDED
        job = WindowsJob()
        process = subprocess.Popen([sys.executable, '-c', 'raise SystemExit(0)'], creationflags=CREATE_SUSPENDED)
        try:
            with self.assertRaisesRegex(RuntimeError, 'assigned creation object'):
                job.resume(process)
            job.close()
            with self.assertRaisesRegex(RuntimeError, 'live owned Job'):
                job.resume(process)
            self.assertIsNone(process.poll())
        finally:
            process.kill(); process.wait(timeout=5); job.close()

    def test_spontaneous_exit_reaps_child_and_preserves_unrelated_process(self):
        child_file = self.root / 'child.txt'
        worker = self.root / 'worker.py'
        worker.write_text('import subprocess,sys\nfrom pathlib import Path\n'
                         'sys.stdin.read()\n'
                         'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"],'
                         'creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)\n'
                         f'Path({str(child_file)!r}).write_text(str(p.pid))\n'
                         'sys.exit(3)\n')
        self.launcher.runtime = RUNTIMES['fake']._replace(command=[sys.executable, str(worker)])
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        self.addCleanup(lambda: (unrelated.terminate(), unrelated.wait()))
        handle = self.launcher.spawn('crash', 'prompt', {}, 30, self.root)
        result = self.finished()
        child = int(child_file.read_text())
        # Always clean the intentionally leaked child on the RED run too.
        self.addCleanup(lambda: subprocess.run(['taskkill', '/F', '/PID', str(child)], capture_output=True)
                        if self.launcher.alive(child) else None)
        self.assertEqual(result.returncode, 3)
        self.assertFalse(self.launcher.alive(child), 'child survived its worker')
        self.assertIsNone(unrelated.poll())
        self.launcher.assert_quiescent('crash', handle.pid)

    def test_normal_exit_is_proven_quiescent(self):
        handle = self.launcher.spawn('normal', 'prompt', {}, 30, self.root)
        self.finished()
        self.launcher.assert_quiescent('normal', handle.pid)

    def test_stop_is_proven_quiescent(self):
        handle = self.launcher.spawn('stop', 'prompt', {}, 30, self.root,
                                     extra_env={'FAKE_CLI_MODE': 'sleep'})
        self.launcher.stop('stop')
        self.finished()
        self.launcher.assert_quiescent('stop', handle.pid)

    def test_assignment_failure_never_runs_requested_command(self):
        marker = self.root / 'should-not-exist'
        self.launcher.runtime = RUNTIMES['fake']._replace(command=[sys.executable, '-c',
            f'from pathlib import Path; Path({str(marker)!r}).touch()'])
        with patch('agent.windows_job.WindowsJob.assign', side_effect=OSError('assignment failed')):
            with self.assertRaisesRegex(OSError, 'assignment failed'):
                self.launcher.spawn('denied', 'prompt', {}, 30, self.root)
        self.assertFalse(marker.exists())
        self.launcher.assert_quiescent('denied', None)

    def test_receiver_crash_terminates_its_worker_job(self):
        launched = self.root / 'launched.json'
        host = self.root / 'host.py'
        repo = Path(__file__).resolve().parents[1]
        host.write_text('import sys,time,json\nfrom pathlib import Path\n'
                        f'sys.path.insert(0,{str(repo)!r})\n'
                        'from agent.launcher import Launcher,RUNTIMES\n'
                        f'l=Launcher({str(self.root / "orphan-runs")!r},RUNTIMES["fake"],"test")\n'
                        f'h=l.spawn("orphan","prompt",{{}},60,{str(self.root)!r},extra_env={{"FAKE_CLI_MODE":"sleep"}})\n'
                        f'Path({str(launched)!r}).write_text(json.dumps({{"pid":h.pid,"job":l._jobs["orphan"].name}}))\n'
                        'time.sleep(60)\n')
        process = subprocess.Popen([sys.executable, str(host)])
        self.addCleanup(lambda: (process.kill(), process.wait()) if process.poll() is None else None)
        deadline = time.monotonic() + 10
        while not launched.exists() and time.monotonic() < deadline:
            time.sleep(.03)
        data = json.loads(launched.read_text())
        from agent.windows_job import WindowsJob
        self.assertFalse(WindowsJob.empty(data['job']))
        process.kill(); process.wait()
        deadline = time.monotonic() + 5
        while (not WindowsJob.empty(data['job']) or self.launcher.alive(data['pid'])) and time.monotonic() < deadline:
            time.sleep(.03)
        self.assertTrue(WindowsJob.empty(data['job']))
        self.assertFalse(self.launcher.alive(data['pid']))
        restarted = Launcher(self.root / 'orphan-runs', RUNTIMES['fake'], 'test')
        restarted.assert_quiescent('orphan', data['pid'])

    def test_simultaneous_stop_and_poll_finalize_job_once(self):
        handle = self.launcher.spawn('race', 'prompt', {}, 30, self.root,
                                     extra_env={'FAKE_CLI_MODE': 'sleep'})
        self.addCleanup(self.launcher.stop, 'race')
        job = self.launcher._jobs['race']
        original = job.terminate_and_wait
        entered, release = threading.Event(), threading.Event()
        calls, errors = [], []
        def terminate():
            calls.append(1); entered.set(); release.wait(2); original()
        def finish():
            try:
                self.launcher._finish_job(handle)
            except Exception as exc:
                errors.append(exc)
        with patch.object(job, 'terminate_and_wait', side_effect=terminate):
            first = threading.Thread(target=finish); second = threading.Thread(target=finish)
            first.start(); self.assertTrue(entered.wait(2)); second.start()
            time.sleep(.05); release.set(); first.join(5); second.join(5)
        self.assertFalse(errors, repr(errors))
        self.assertEqual(len(calls), 1)
        handle.process.wait(timeout=5)

    def test_later_contained_attempt_cannot_certify_legacy_attempt_with_same_pid(self):
        from agent.windows_job import WindowsJob
        job = WindowsJob(); name = job.name; job.close()
        root = self.launcher.state_dir('reused')
        old = root / 'old'; old.mkdir(parents=True)
        new = root / 'new'; new.mkdir()
        (old / 'process.json').write_text(json.dumps({'pid':123}))
        (new / 'process.json').write_text(json.dumps({'pid':123, 'windows_job':name}))
        (new / 'killed.json').write_text(json.dumps({'pid':123, 'descendants':[], 'windows_job':name}))
        with patch.object(self.launcher, 'alive', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'unverified|without verified'):
                self.launcher.assert_quiescent('reused', 123)
