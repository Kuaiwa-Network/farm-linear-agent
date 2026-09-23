from contextlib import redirect_stdout
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.launcher import _PINNED_EXIT, Handle, Launcher, RUNTIMES, write_mcp_config

ROOT = Path(__file__).resolve().parents[1]


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runs = Path(self.tmp.name) / "runs"
        self.launcher = Launcher(self.runs, RUNTIMES["fake"], host="test-host")
        self.message = "authority\n\n" + json.dumps({"item_id": "item-1", "database": "x"})

    def wait_finished(self, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            finished = self.launcher.poll()
            if finished:
                return finished
            time.sleep(0.05)
        self.fail("worker did not finish")

    def wait_exited(self, handle, timeout=10):
        deadline = time.time() + timeout
        while not Launcher.exited(handle.process) and time.time() < deadline:
            time.sleep(0.02)
        self.assertTrue(Launcher.exited(handle.process), "worker did not exit")

    def failing_for(self, method, item_id, exc):
        """Patch one launcher step to raise for a single item and run as usual for every other."""
        real = getattr(self.launcher, method)

        def step(handle, *args, **kwargs):
            if handle.item_id == item_id:
                raise exc
            return real(handle, *args, **kwargs)
        return patch.object(self.launcher, method, side_effect=step)

    def test_interrupted_launch_without_pid_cannot_be_declared_quiescent(self):
        attempt = self.launcher.state_dir("item-1") / "attempt"
        attempt.mkdir(parents=True)
        (attempt / "process.json").write_text(json.dumps({"state": "preparing"}))
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            self.launcher.assert_quiescent("item-1", None)

    def test_cancelled_launch_never_spawns(self):
        with patch("agent.launcher.subprocess.Popen") as spawn:
            with self.assertRaises(RuntimeError):
                self.launcher.spawn("item-1", self.message, {}, 30, self.tmp.name, cancelled=lambda: True)
        spawn.assert_not_called()

    def test_same_second_attempts_keep_both_logs(self):
        self.launcher.clock = lambda: 1000
        first = self.launcher.spawn("item-1", self.message, {}, 30, self.tmp.name)
        self.wait_finished()
        (first.run_dir / "stdout.log").write_text("first attempt")
        second = self.launcher.spawn("item-1", self.message, {}, 30, self.tmp.name)
        self.wait_finished()
        self.assertNotEqual(first.run_dir, second.run_dir)
        self.assertEqual((first.run_dir / "stdout.log").read_text(), "first attempt")

    def test_native_memory_is_disabled_and_personal_homes_are_not_imported(self):
        import tomllib
        personal = Path(self.tmp.name) / "personal"
        (personal / "memories").mkdir(parents=True)
        (personal / "memories" / "private.md").write_text("PRIVATE MEMORY")
        script = ("import json,os,pathlib,sys;sys.stdin.read();"
                  "pathlib.Path(sys.argv[1]).write_text(json.dumps({{k:os.environ.get(k) for k in "
                  "['CODEX_HOME','CLAUDE_CONFIG_DIR','CLAUDE_CODE_DISABLE_AUTO_MEMORY']}}))")
        homes = []
        for name in ("codex", "claude"):
            runtime = RUNTIMES[name]._replace(command=[sys.executable, "-c", script, "{last_message}"], seed_files={})
            launcher = Launcher(self.runs, runtime, host="test")
            with patch.dict(os.environ, {"CODEX_HOME": str(personal), "CLAUDE_CONFIG_DIR": str(personal),
                                         "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "0"}):
                handle = launcher.spawn("memory-" + name, self.message, {}, 30, self.tmp.name,
                                        extra_env={"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "0"})
            self.addCleanup(launcher.stop, "memory-" + name)
            deadline = time.monotonic() + 10
            finished = []
            while time.monotonic() < deadline and not finished:
                finished = launcher.poll()
                if not finished: time.sleep(0.02)
            self.assertTrue(finished)
            env = json.loads(finished[0].last_message)
            home = handle.run_dir / "home"
            homes.append(home)
            self.assertEqual(env[runtime.home_env], str(home))
            self.assertFalse((home / "memories" / "private.md").exists())
            if name == "codex":
                config = tomllib.loads((home / "config.toml").read_text())
                self.assertFalse(config["features"]["memories"])
                self.assertIn("features.memories=false", RUNTIMES[name].command)
            else:
                self.assertEqual(env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"], "1")
        self.assertNotEqual(*homes)
        self.assertEqual((personal / "memories" / "private.md").read_text(), "PRIVATE MEMORY")

    def test_spawn_uses_isolated_home_and_captures_last_message(self):
        with patch.dict(os.environ, {"CODEX_HOME": "/decoy/codex", "CLAUDE_CONFIG_DIR": "/decoy/claude"}):
            handle = self.launcher.spawn("item-1", self.message, {}, budget_seconds=60, cwd=self.tmp.name,
                                         extra_env={"FAKE_CLI_MODE": "echo", "FAKE_HOME_MARKER": "set"})
        self.assertTrue((handle.run_dir / "home").is_dir())
        finished = self.wait_finished()[0]
        self.assertEqual((finished.item_id, finished.returncode, finished.killed), ("item-1", 0, False))
        self.assertEqual(finished.last_message, f"echo:item-1:home=set:codex_home={handle.run_dir / 'home'}:claude_home=")
        self.assertIn("echo:item-1", (handle.run_dir / "stdout.log").read_text(encoding="utf-8"))

    def test_mcp_servers_are_written_into_the_home_in_the_runtime_format(self):
        codex = Launcher(self.runs, RUNTIMES["codex"]._replace(command=RUNTIMES["fake"].command, seed_files={}), host="h")
        handle = codex.spawn("item-2", self.message, {"unity": {"url": "http://127.0.0.1:8080/mcp"}}, budget_seconds=60,
                             cwd=self.tmp.name, extra_env={"FAKE_CLI_MODE": "echo"})
        config = (handle.run_dir / "home" / "config.toml").read_text(encoding="utf-8")
        self.assertIn("[mcp_servers.unity]", config)
        self.assertIn('url = "http://127.0.0.1:8080/mcp"', config)
        claude = Launcher(self.runs, RUNTIMES["claude"]._replace(command=RUNTIMES["fake"].command, seed_files={}), host="h")
        handle = claude.spawn("item-3", self.message, {"probe": {"command": "python3", "args": ["p.py"]}}, budget_seconds=60,
                              cwd=self.tmp.name, extra_env={"FAKE_CLI_MODE": "echo"})
        mcp = json.loads((handle.run_dir / "home" / "mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(mcp["mcpServers"]["probe"]["command"], "python3")
        for pending in (codex, claude):
            deadline = time.time() + 10
            while time.time() < deadline and not pending.poll():
                time.sleep(0.05)

    def test_sandbox_roots_and_network_access_precede_mcp_servers_in_the_home_config(self):
        handle = self.launcher.spawn("item-3", self.message, {"unity": {"url": "http://127.0.0.1:8080/mcp"}},
                                     budget_seconds=60, cwd=self.tmp.name, extra_env={"FAKE_CLI_MODE": "echo"},
                                     writable=[Path("/w/item-3/Farm-Client"), Path("/repo/.local/agent")])
        self.wait_finished()
        config = (handle.run_dir / "home" / "config.toml").read_text(encoding="utf-8")
        expected_roots = json.dumps([str(self.runs / "item-3"), str(Path("/w/item-3/Farm-Client")),
                                     str(Path("/repo/.local/agent"))])
        self.assertIn(f"[sandbox_workspace_write]\nwritable_roots = {expected_roots}\nnetwork_access = true\n", config)
        self.assertLess(config.index("[sandbox_workspace_write]"), config.index("[mcp_servers.unity]"))
        self.assertEqual(self.launcher.state_dir("item-3"), self.runs / "item-3")

    def test_stop_kills_a_sleeping_worker_within_grace(self):
        self.launcher.spawn("item-4", self.message, {}, budget_seconds=60, cwd=self.tmp.name, extra_env={"FAKE_CLI_MODE": "sleep"})
        started = time.time()
        self.assertTrue(self.launcher.stop("item-4", grace=2.0))
        self.assertLess(time.time() - started, 5.0)
        finished = self.wait_finished()[0]
        self.assertTrue(finished.killed)
        self.assertEqual(finished.reason, "stopped")

    def test_budget_kill_and_crash_are_reported(self):
        self.launcher.spawn("item-5", self.message, {}, budget_seconds=1, cwd=self.tmp.name, extra_env={"FAKE_CLI_MODE": "sleep"})
        self.launcher.spawn("item-6", self.message, {}, budget_seconds=60, cwd=self.tmp.name, extra_env={"FAKE_CLI_MODE": "crash"})
        time.sleep(1.2)
        finished = {f.item_id: f for f in self.wait_finished()}
        deadline = time.time() + 10
        while len(finished) < 2 and time.time() < deadline:
            finished.update({f.item_id: f for f in self.launcher.poll()})
            time.sleep(0.05)
        self.assertEqual(finished["item-5"].reason, "budget")
        self.assertTrue(finished["item-5"].killed)
        self.assertEqual(finished["item-6"].returncode, 3)

    def test_a_budget_kill_that_raises_keeps_the_live_worker_and_reports_the_others(self):
        # An exception escaping poll() once discarded the records of workers it had already removed, so the
        # scheduler never freed their slots, and it left every worker later in the loop unvisited.
        for index, order in enumerate((("stuck", "done"), ("done", "stuck"))):
            with self.subTest(order=order):
                ids = {name: f"{name}-{index}" for name in order}
                handles = {}
                for name in order:
                    mode, budget = ("sleep", 0) if name == "stuck" else ("crash", 60)
                    handles[name] = self.launcher.spawn(ids[name], self.message, {}, budget_seconds=budget,
                                                        cwd=self.tmp.name, extra_env={"FAKE_CLI_MODE": mode})
                self.addCleanup(self.launcher.stop, ids["stuck"], 1.0)
                self.wait_exited(handles["done"])
                log = io.StringIO()
                with self.failing_for("_kill", ids["stuck"], subprocess.TimeoutExpired("worker", 5)), \
                        redirect_stdout(log):
                    finished = self.launcher.poll()
                self.assertEqual([(f.item_id, f.returncode) for f in finished], [(ids["done"], 3)])
                self.assertIn(ids["stuck"], self.launcher.running())
                self.assertTrue(Launcher.alive(handles["stuck"].pid))
                self.assertFalse((handles["stuck"].run_dir / "killed.json").exists())
                self.assertEqual(json.loads(log.getvalue()),
                                 {"event": "worker_poll_error", "item_id": ids["stuck"], "error": "TimeoutExpired"})
                # The kept worker is reported, as a budget kill, once that kill does complete.
                self.launcher._kill(handles["stuck"], grace=2.0)
                finished = self.wait_finished()
                self.assertEqual([(f.item_id, f.reason, f.killed) for f in finished], [(ids["stuck"], "budget", True)])

    def test_a_worker_whose_report_fails_keeps_its_whole_record_for_the_next_poll(self):
        self.launcher.spawn("stopped", self.message, {}, budget_seconds=60, cwd=self.tmp.name,
                            extra_env={"FAKE_CLI_MODE": "sleep"})
        done = self.launcher.spawn("done", self.message, {}, budget_seconds=60, cwd=self.tmp.name,
                                   extra_env={"FAKE_CLI_MODE": "crash"})
        self.assertTrue(self.launcher.stop("stopped", grace=2.0))
        self.wait_exited(done)
        with self.failing_for("_read_last_message", "stopped", PermissionError("last_message.txt")), \
                redirect_stdout(io.StringIO()):
            self.assertEqual([f.item_id for f in self.launcher.poll()], ["done"])
        self.assertIn("stopped", self.launcher.running())
        finished = self.launcher.poll()
        self.assertEqual([(f.item_id, f.reason, f.killed) for f in finished], [("stopped", "stopped", True)])

    def test_an_unwritable_service_log_does_not_lose_a_polls_records(self):
        done = self.launcher.spawn("done", self.message, {}, budget_seconds=60, cwd=self.tmp.name,
                                   extra_env={"FAKE_CLI_MODE": "crash"})
        failing = self.launcher.spawn("failing", self.message, {}, budget_seconds=60, cwd=self.tmp.name,
                                      extra_env={"FAKE_CLI_MODE": "crash"})
        self.wait_exited(done)
        self.wait_exited(failing)
        closed = io.StringIO()
        closed.close()
        with self.failing_for("_read_last_message", "failing", OSError("disk")), redirect_stdout(closed):
            self.assertEqual([f.item_id for f in self.launcher.poll()], ["done"])
        self.assertEqual([f.item_id for f in self.launcher.poll()], ["failing"])

    def test_stop_also_kills_descendants_in_other_sessions(self):
        handle = self.launcher.spawn("item-7", self.message, {}, budget_seconds=60, cwd=self.tmp.name,
                                     extra_env={"FAKE_CLI_MODE": "detached-sleep"})
        deadline = time.time() + 5
        descendants = []
        while time.time() < deadline and not descendants:
            descendants = self.launcher.descendants(handle.pid)
            time.sleep(0.05)
        self.assertTrue(descendants, "fake worker did not spawn its detached child")
        self.assertTrue(self.launcher.stop("item-7", grace=2.0))
        self.wait_finished()
        deadline = time.time() + 3
        while time.time() < deadline and any(self.launcher.alive(pid) for pid in descendants):
            time.sleep(0.05)
        for pid in descendants:
            self.assertFalse(self.launcher.alive(pid), f"descendant {pid} survived stop")
        self.assertTrue((handle.run_dir / "killed.json").exists())

    def test_child_exiting_before_reading_stdin_does_not_break_spawn(self):
        big = self.message + "\n" + ("x" * 2_000_000)
        handle = self.launcher.spawn("item-8", big, {}, budget_seconds=60, cwd=self.tmp.name,
                                     extra_env={"FAKE_CLI_MODE": "exit-immediately"})
        self.assertIn("item-8", self.launcher.running())
        finished = self.wait_finished()[0]
        self.assertEqual((finished.item_id, finished.returncode), ("item-8", 0))
        self.assertTrue((handle.run_dir / "stdin-error.txt").exists())

    def test_owned_pid_requires_the_item_id_on_the_command_line(self):
        handle = self.launcher.spawn("item-9", self.message, {}, budget_seconds=60, cwd=self.tmp.name,
                                     extra_env={"FAKE_CLI_MODE": "sleep"})
        self.assertTrue(self.launcher.owned_pid(handle.pid, "item-9"))
        self.assertFalse(self.launcher.owned_pid(handle.pid, "item-other"))
        self.assertTrue(self.launcher.stop("item-9", grace=2.0))
        self.wait_finished()
        self.assertFalse(self.launcher.owned_pid(handle.pid, "item-9"))

    def test_an_unsandboxed_run_is_the_only_way_out_of_the_seatbelt_and_carries_no_sandbox_config(self):
        """Task 0's addendum: `Unity -batchmode -runTests` inside sandbox_workspace_write hangs for ever on a
        denied Mach lookup — 25 min at 0.0% CPU, no results file — with zero file-permission denials, while
        the same command unsandboxed exits 2 in 19 s. So the batch Editor is started here, not by the worker.
        This asserts the two halves of that: the run really happens, and it writes none of the isolated home,
        CODEX_HOME or sandbox_workspace_write machinery that would put it back inside the seatbelt."""
        launcher = Launcher(Path(self.tmp.name) / "unsandboxed-runs", RUNTIMES["fake"], host="test")
        log = Path(self.tmp.name) / "unity-editor.log"
        result = launcher.run_unsandboxed([sys.executable, "-c", "import sys; sys.stderr.write('hi'); "
                                           "sys.exit(2)"], cwd=self.tmp.name, timeout=30, log=log)
        self.assertEqual((result.returncode, result.timed_out), (2, False))
        self.assertIn("hi", log.read_text(encoding="utf-8"))
        # Nothing under the runs root at all: no isolated home, so no config.toml and no writable_roots.
        self.assertEqual(list((Path(self.tmp.name) / "unsandboxed-runs").rglob("*")), [])
        # The 25-minute hang must end at a deadline, not at an operator — and the deadline has to KILL, not
        # merely return: a run_unsandboxed that gave up waiting and walked away would leave the Editor on the
        # slot folder for ever, which is the orphan this whole task exists to prevent.
        marker = Path(self.tmp.name) / "slow.pid"
        slow = launcher.run_unsandboxed(
            [sys.executable, "-c", "import os, pathlib, sys, time; "
             "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)", str(marker)],
            cwd=self.tmp.name, timeout=1.5)
        self.assertEqual((slow.timed_out, slow.returncode), (True, None))
        self.assertLess(slow.seconds, 20)
        self.assertTrue(marker.exists(), "the slow child never started")
        self.assertFalse(Launcher.alive(int(marker.read_text())),
                         "the deadline expired and the process was left running")

    def test_an_owned_unsandboxed_run_can_be_killed_by_item_and_takes_its_children_with_it(self):
        """The reachability hole the redesign opened. The batch Editor is a direct child of `serve`, not a
        descendant of any worker — the worker asked for the reservation and exited — so `stop()`'s handle
        lookup and its `descendants` walk both miss it, and `run_batch` is meanwhile blocked in `wait()` for
        up to `batch_timeout`. This asserts the two things that close it: the run is reachable by item id,
        and the kill takes the process GROUP, so a child that outlived its parent dies too. That child is
        the case that matters — it is what would still be holding the slot folder open."""
        launcher = Launcher(Path(self.tmp.name) / "runs", RUNTIMES["fake"], host="test")
        marker = Path(self.tmp.name) / "child.pid"
        # The parent spawns a grandchild that survives it, then sleeps; killing only the parent would leave
        # the grandchild alive, which is precisely the wedged-Editor shape Task 0 measured.
        script = ("import subprocess, sys, time, pathlib;"
                  "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
                  "pathlib.Path(sys.argv[1]).write_text(str(c.pid));"
                  "time.sleep(60)")
        result = {}

        def run():
            result["run"] = launcher.run_unsandboxed([sys.executable, "-c", script, str(marker)],
                                                     cwd=self.tmp.name, timeout=60, owner="itm_batch")

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 20)                       # never leave the run thread behind
        self.addCleanup(launcher.stop_unsandboxed, "itm_batch")
        deadline = time.monotonic() + 15
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(marker.exists(), "the owned run never reached its grandchild")
        grandchild = int(marker.read_text())

        self.assertTrue(launcher.stop_unsandboxed("itm_batch"))
        thread.join(15)
        self.assertFalse(thread.is_alive())
        gone = time.monotonic() + 10
        while Launcher.alive(grandchild) and time.monotonic() < gone:
            time.sleep(0.05)
        self.assertFalse(Launcher.alive(grandchild))               # the group, not just the pid
        self.assertFalse(launcher.stop_unsandboxed("itm_batch"))   # deregistered; idempotent for Stop
        self.assertEqual(launcher.stop_all_unsandboxed(), [])      # and nothing is left for shutdown to find

    def test_an_injected_http_server_is_written_in_each_runtime_s_own_shape(self):
        """§19's first risk is that the default runtime changes after Task 0, so the one server the scheduler
        injects has to survive both writers. write_mcp_config's JSON writer emits `mcpServers`, where an
        entry carrying a bare url and no type is not a valid HTTP server definition."""
        home = Path(self.tmp.name) / "home"
        home.mkdir()
        toml = write_mcp_config(home, "toml", {"unity": {"url": "http://127.0.0.1:8080/mcp"}}).read_text(encoding="utf-8")
        self.assertIn("[mcp_servers.unity]", toml)
        self.assertIn('url = "http://127.0.0.1:8080/mcp"', toml)
        written = json.loads(write_mcp_config(
            home, "json", {"unity": {"type": "http", "url": "http://127.0.0.1:8080/mcp"}}).read_text(encoding="utf-8"))
        self.assertEqual(written["mcpServers"]["unity"], {"type": "http", "url": "http://127.0.0.1:8080/mcp"})

    def test_stop_before_registration_prevents_a_late_batch_start(self):
        marker = Path(self.tmp.name) / "should-not-start"
        self.launcher.stop_unsandboxed("cancelled-item")
        result = self.launcher.run_unsandboxed(
            [sys.executable, "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).touch()", str(marker)],
            cwd=self.tmp.name, timeout=5, owner="cancelled-item")
        self.assertFalse(marker.exists(), "Stop lost the race before process registration")
        self.assertNotEqual(result.returncode, 0)

    def test_shutdown_prevents_late_owned_and_unowned_batch_starts(self):
        self.launcher.stop_all_unsandboxed()
        for owner in ("late-item", None):
            with self.subTest(owner=owner):
                marker = Path(self.tmp.name) / str(owner)
                result = self.launcher.run_unsandboxed(
                    [sys.executable, "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).touch()", str(marker)],
                    cwd=self.tmp.name, timeout=5, owner=owner)
                self.assertFalse(marker.exists(), "shutdown allowed a new batch process")
                self.assertNotEqual(result.returncode, 0)

    @unittest.skipIf(os.name == "nt", "POSIX process group semantics")
    def test_group_kill_escalates_when_only_the_child_ignores_term(self):
        marker = Path(self.tmp.name) / "stubborn.pid"
        child = ("import os,pathlib,signal,sys,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                 "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)")
        parent = subprocess.Popen([sys.executable, "-c",
                                   "import subprocess,sys,time; subprocess.Popen(sys.argv[1:]); time.sleep(60)",
                                   sys.executable, "-c", child, str(marker)], start_new_session=True)
        def cleanup():
            try:
                os.killpg(parent.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            parent.wait(timeout=10)
        self.addCleanup(cleanup)
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(marker.exists(), "child did not install its signal handler")
        pid = int(marker.read_text())
        self.launcher.kill_group(parent.pid, grace=0.2, reap=parent)
        deadline = time.monotonic() + 5
        while Launcher.alive(pid) and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(Launcher.alive(pid), "group leader died but its TERM-ignoring child survived")

    def test_a_runtime_with_a_writable_flag_gets_one_flag_per_root(self):
        runtime = RUNTIMES["claude"]._replace(command=RUNTIMES["fake"].command)
        launcher = Launcher(self.runs, runtime, host="h")

        def reap():  # never leave a child unreaped, even if an assertion below fails first
            launcher.stop("item-4")
            launcher.poll()

        self.addCleanup(reap)
        handle = launcher.spawn("item-4", self.message, {}, budget_seconds=60, cwd=self.tmp.name,
                                extra_env={"FAKE_CLI_MODE": "echo"},
                                writable=[Path("/w/item-4/Farm-Client"), Path("/repo/.local/agent")])
        # The command line is fixed at spawn, so nothing here needs to wait for the worker to finish.
        self.assertEqual(handle.process.args[-6:], ["--add-dir", str(self.runs / "item-4"),
                                                    "--add-dir", str(Path("/w/item-4/Farm-Client")),
                                                    "--add-dir", str(Path("/repo/.local/agent"))])
        self.assertEqual(RUNTIMES["claude"].writable_flag, "--add-dir")
        self.assertIsNone(RUNTIMES["codex"].writable_flag)


# The child a leaking worker leaves behind: it announces its pid only once `setup` has run, so a
# SIGTERM-ignoring child cannot be signalled before it installs its handler.
CHILD = ("import os, signal, sys, time\n"
         "{setup}"
         "tmp = sys.argv[1] + '.tmp'\n"
         "open(tmp, 'w').write(str(os.getpid()))\n"
         "os.replace(tmp, sys.argv[1])\n"
         "time.sleep(60)\n")
# A middle process that starts CHILD and exits at once, so CHILD is re-parented while still in the group.
ORPHANING = ("import subprocess, sys\n"
             "subprocess.Popen([sys.executable, '-c', {child!r}, sys.argv[1]], stdin=subprocess.DEVNULL)\n")
# A worker that starts one child with `kwargs` and waits until it is ready. It then waits for `release`
# (when given), sleeps `linger` seconds, and exits 0 on its own.
LEAKING_WORKER = ("import os, subprocess, sys, time\n"
                  "sys.stdin.read()\n"
                  "ready, release = sys.argv[1], sys.argv[2]\n"
                  "subprocess.Popen([sys.executable, '-c', {child!r}, ready], stdin=subprocess.DEVNULL,"
                  " {kwargs}).wait() if {orphan} else "
                  "subprocess.Popen([sys.executable, '-c', {child!r}, ready], stdin=subprocess.DEVNULL, {kwargs})\n"
                  "deadline = time.time() + 30\n"
                  "while not os.path.exists(ready) and time.time() < deadline:\n"
                  "    time.sleep(0.02)\n"
                  "while release and not os.path.exists(release) and time.time() < deadline:\n"
                  "    time.sleep(0.02)\n"
                  "time.sleep({linger})\n")
TERM_IGNORED = "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"


def exited_unreaped(pid):
    return os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None


@unittest.skipIf(os.name == "nt", "POSIX sessions; Windows workers are proved by Job Objects in test_windows_workers.py")
class PosixSelfExitTeardownTests(unittest.TestCase):
    """A worker that exits by itself has no killed.json from Stop. Until it is reaped it still pins its session
    and process group IDs (both its pid), so the reap is where its evidence is made."""

    # Runs on every POSIX interpreter: without os.waitid the designed outcome is the old hold.
    RUNS_WITHOUT_WAITID = {"test_without_waitid_a_self_exit_keeps_holding_cleanup"}

    def setUp(self):
        if not _PINNED_EXIT and self._testMethodName not in self.RUNS_WITHOUT_WAITID:
            self.skipTest("needs os.waitid (CPython 3.13+ on macOS)")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.launcher = Launcher(self.root / "runs", RUNTIMES["fake"], host="test-host")
        self.launcher.exit_grace = 0.5
        self.addCleanup(self.stop_all)

    def stop_all(self):
        self.launcher._killing.clear()
        for item_id in list(self.launcher.running()):
            self.launcher.stop(item_id, grace=1.0)
        deadline = time.monotonic() + 10
        while self.launcher.running() and time.monotonic() < deadline:
            self.launcher.poll()
            time.sleep(0.02)

    def finished(self, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = self.launcher.poll()
            if result:
                return result[0]
            time.sleep(0.03)
        self.fail("worker did not finish")

    def wait_exited(self, handle):
        deadline = time.monotonic() + 10
        while not exited_unreaped(handle.pid) and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(exited_unreaped(handle.pid))

    def leaking_worker(self, item_id, kwargs="", setup="", orphan=False, release="", linger=0):
        """Spawn a worker that leaves one child behind; returns (handle, child_pid)."""
        ready = self.root / f"{item_id}-child.pid"

        def kill_child():  # never leak the child, even when an assertion (or the RED run) fails first
            if ready.exists():
                pid = int(ready.read_text())
                if Launcher.alive(pid) and str(ready) in Launcher._command_line(pid):
                    os.kill(pid, signal.SIGKILL)

        self.addCleanup(kill_child)
        child = CHILD.format(setup=setup)
        if orphan:
            child = ORPHANING.format(child=child)
        script = self.root / f"{item_id}-worker.py"
        script.write_text(LEAKING_WORKER.format(child=child, kwargs=kwargs, orphan=orphan, linger=linger),
                          encoding="utf-8")
        self.launcher.runtime = RUNTIMES["fake"]._replace(command=[sys.executable, str(script), str(ready),
                                                                   str(release)])
        handle = self.launcher.spawn(item_id, "prompt", {}, 30, self.root)
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(ready.exists(), "worker did not start its child")
        return handle, int(ready.read_text())

    def evidence(self, handle, name="killed.json"):
        return json.loads((handle.run_dir / name).read_text(encoding="utf-8"))

    def gone(self, pid, timeout=3):
        deadline = time.monotonic() + timeout
        while Launcher.alive(pid) and time.monotonic() < deadline:
            time.sleep(0.02)
        return not Launcher.alive(pid)

    def test_normal_exit_is_proven_quiescent(self):
        # FARM-1300 on the TestBot Mac: a chat worker answered, exited 0 by itself, and its cleanup was held
        # for ever with "worker exited without verified descendant teardown".
        handle = self.launcher.spawn("normal", "prompt", {}, 30, self.root)
        self.assertEqual(self.finished().reason, "exited")
        self.launcher.assert_quiescent("normal", handle.pid)
        self.assertEqual(self.evidence(handle), {"pid": handle.pid, "descendants": [], "terminated": [],
                                                 "posix_session": handle.pid, "exited": True, "empty": True})

    def test_same_group_survivor_is_terminated_and_recorded(self):
        unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(lambda: (unrelated.kill(), unrelated.wait()))
        # SIGTERM is ignored, so this also proves the SIGKILL escalation.
        handle, child = self.leaking_worker("group", setup=TERM_IGNORED)
        self.assertEqual(os.getpgid(child), handle.pid)
        self.assertEqual(self.finished().returncode, 0)
        self.assertTrue(self.gone(child), "same-group child survived its worker's reap")
        self.assertIsNone(unrelated.poll(), "a process outside the worker's session was signalled")
        proof = self.evidence(handle)
        self.assertEqual((proof["terminated"], proof["descendants"], proof["empty"]), ([child], [], True))
        self.launcher.assert_quiescent("group", handle.pid)

    def test_other_group_in_the_worker_session_is_terminated_and_recorded(self):
        # A job-control shell puts each job in its own process group, still inside the worker's session.
        handle, child = self.leaking_worker("subgroup", kwargs="process_group=0")
        self.assertNotEqual(os.getpgid(child), handle.pid)
        self.assertEqual(os.getsid(child), handle.pid)
        self.finished()
        self.assertTrue(self.gone(child), "same-session child survived its worker's reap")
        self.assertEqual(self.evidence(handle)["terminated"], [child])
        self.launcher.assert_quiescent("subgroup", handle.pid)

    def test_a_member_that_cannot_be_terminated_holds_cleanup(self):
        handle, child = self.leaking_worker("stubborn")
        with patch.object(Launcher, "_signal_session", lambda *args: None):
            self.finished()
        self.assertTrue(Launcher.alive(child))
        self.assertFalse((handle.run_dir / "killed.json").exists())
        unverified = self.evidence(handle, "teardown-unverified.json")
        self.assertEqual((unverified["remaining"], unverified["empty"]), ([child], False))
        with self.assertRaisesRegex(RuntimeError, "teardown"):
            self.launcher.assert_quiescent("stubborn", handle.pid)

    def test_an_unverified_self_exit_supersedes_an_interrupted_stop_record(self):
        # A Stop whose kill raised (a worker stuck past SIGKILL) leaves its early killed.json behind. When
        # that worker later exits and its session cannot be verified, the early record must not certify it.
        handle, child = self.leaking_worker("interrupted")
        early = {"pid": handle.pid, "descendants": []}
        (handle.run_dir / "killed.json").write_text(json.dumps(early), encoding="utf-8")
        with patch.object(Launcher, "_signal_session", lambda *args: None):
            self.finished()
        self.assertFalse((handle.run_dir / "killed.json").exists())
        self.assertEqual(self.evidence(handle, "killed.superseded.json"), early)
        with self.assertRaisesRegex(RuntimeError, "teardown"):
            self.launcher.assert_quiescent("interrupted", handle.pid)

    def test_a_verified_self_exit_keeps_an_interrupted_stops_recorded_descendants(self):
        # A Stop whose kill raised had recorded a setsid() child it found by parent walk. The later self-exit
        # check cannot see that child, so the verified record must still carry it for assert_quiescent.
        handle, child = self.leaking_worker("kept", kwargs="start_new_session=True")
        (handle.run_dir / "killed.json").write_text(json.dumps({"pid": handle.pid, "descendants": [child]}),
                                                    encoding="utf-8")
        self.finished()
        proof = self.evidence(handle)
        self.assertEqual((proof["descendants"], proof["empty"]), ([child], True))
        with self.assertRaisesRegex(RuntimeError, "descendants have not exited"):
            self.launcher.assert_quiescent("kept", handle.pid)

    def test_a_teardown_record_naming_another_pid_holds_cleanup(self):
        handle = self.launcher.spawn("foreign", "prompt", {}, 30, self.root)
        (handle.run_dir / "killed.json").write_text(json.dumps({"pid": 1, "descendants": []}), encoding="utf-8")
        self.finished()
        self.assertFalse((handle.run_dir / "killed.json").exists())
        self.assertEqual(self.evidence(handle, "teardown-unverified.json")["reason"],
                         "earlier teardown record is unreadable or not this attempt's")
        with self.assertRaisesRegex(RuntimeError, "teardown"):
            self.launcher.assert_quiescent("foreign", handle.pid)

    def test_a_signalled_member_that_leaves_the_session_is_still_checked(self):
        # The member was verified in the pinned session and signalled, then called setsid() in its handler.
        # The group SIGTERM and the per-member one can each run the handler; a second setsid() is EPERM.
        escape = ("def _escape(*_):\n"
                  "    try:\n"
                  "        os.setsid()\n"
                  "    except PermissionError:\n"
                  "        pass\n"
                  "    open(sys.argv[1] + '.escaped', 'w').close()\n"
                  "signal.signal(signal.SIGTERM, _escape)\n")
        handle, child = self.leaking_worker("escaping", setup=escape)
        escaped = self.root / "escaping-child.pid.escaped"
        real = Launcher._signal_session

        def kill_after_the_escape(leader, members, sig):
            # The handler is Python code in another process: under load it can run after exit_grace. Hold
            # only the SIGKILL pass until it has, so the premise does not depend on scheduling.
            deadline = time.monotonic() + 10
            while sig == signal.SIGKILL and not escaped.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            real(leader, members, sig)

        with patch.object(Launcher, "_signal_session", staticmethod(kill_after_the_escape)):
            self.finished()
        self.assertTrue(escaped.exists(), "the member never ran its SIGTERM handler")
        self.assertTrue(Launcher.alive(child))
        self.assertEqual(os.getsid(child), child)
        proof = self.evidence(handle)
        self.assertEqual((proof["descendants"], proof["terminated"]), ([child], []))
        with self.assertRaisesRegex(RuntimeError, "descendants have not exited"):
            self.launcher.assert_quiescent("escaping", handle.pid)

    def test_a_group_still_reported_after_the_reap_holds_cleanup(self):
        handle = self.launcher.spawn("lingering", "prompt", {}, 30, self.root)
        with patch.object(Launcher, "_group_gone", return_value=False):
            self.finished()
        self.assertFalse((handle.run_dir / "killed.json").exists())
        self.assertEqual(self.evidence(handle, "teardown-unverified.json")["reason"],
                         "process group still present after its leader was reaped")
        with self.assertRaisesRegex(RuntimeError, "teardown"):
            self.launcher.assert_quiescent("lingering", handle.pid)

    def test_an_unreadable_process_table_is_retried_while_the_worker_stays_unreaped(self):
        handle = self.launcher.spawn("blind", "prompt", {}, 30, self.root)
        self.wait_exited(handle)
        with patch.object(Launcher, "session_members", return_value=None):
            self.assertEqual(self.launcher.poll(), [])
        self.assertTrue(exited_unreaped(handle.pid), "the retry must keep the session pinned")
        self.finished()
        self.assertIs(self.evidence(handle)["empty"], True)
        self.launcher.assert_quiescent("blind", handle.pid)

    def test_a_process_table_that_stays_unreadable_holds_cleanup(self):
        self.launcher.settle_retry_seconds = 0
        handle = self.launcher.spawn("blind", "prompt", {}, 30, self.root)
        with patch.object(Launcher, "session_members", return_value=None):
            self.finished()
        self.assertFalse((handle.run_dir / "killed.json").exists())
        self.assertIsNone(self.evidence(handle, "teardown-unverified.json")["remaining"])
        with self.assertRaisesRegex(RuntimeError, "teardown"):
            self.launcher.assert_quiescent("blind", handle.pid)

    def test_without_waitid_a_self_exit_keeps_holding_cleanup(self):
        # CPython exposes os.waitid on macOS only from 3.13. Without it the exit cannot be seen unreaped,
        # so the old fail-closed hold is the only honest outcome.
        with patch("agent.launcher._PINNED_EXIT", False):
            handle = self.launcher.spawn("old-python", "prompt", {}, 30, self.root)
            self.assertEqual(self.finished().returncode, 0)
        self.assertFalse((handle.run_dir / "killed.json").exists())
        with self.assertRaisesRegex(RuntimeError, "teardown"):
            self.launcher.assert_quiescent("old-python", handle.pid)

    def test_setsid_descendant_escapes_the_session_check(self):
        """The documented POSIX limit: a child that called setsid() left the worker's session and group, so
        neither the reap check nor any signal reaches it, and the attempt is still certified. Claude Code
        2.1.280 was observed starting its Bash tool shells this way."""
        handle, child = self.leaking_worker("setsid", kwargs="start_new_session=True")
        self.assertEqual(os.getsid(child), child)
        self.finished()
        self.assertTrue(Launcher.alive(child), "a process outside the worker's session was signalled")
        self.assertEqual(self.evidence(handle)["terminated"], [])
        self.launcher.assert_quiescent("setsid", handle.pid)

    def test_stop_after_a_self_exit_leaves_the_reap_and_its_evidence_to_poll(self):
        handle = self.launcher.spawn("raced", "prompt", {}, 30, self.root)
        self.wait_exited(handle)
        # Linear Stop arrives after the worker already exited but before the scheduler polled it.
        self.assertTrue(self.launcher.stop("raced", grace=1.0))
        self.finished()
        self.assertIs(self.evidence(handle)["empty"], True)
        self.launcher.assert_quiescent("raced", handle.pid)

    def test_stop_racing_a_self_exit_still_reaches_the_worker_group(self):
        # Stop sees the worker alive, then it exits by itself while `_kill` walks `ps`. Its child has been
        # re-parented, so only the still-pinned group and session reach it.
        release = self.root / "raced-release"
        handle, child = self.leaking_worker("raced", setup=TERM_IGNORED, release=str(release))
        real = Launcher.descendants

        def exits_during_the_walk(pid):
            release.touch()
            deadline = time.monotonic() + 10
            while not exited_unreaped(pid) and time.monotonic() < deadline:
                time.sleep(0.01)
            return real(pid)

        with patch.object(Launcher, "descendants", staticmethod(exits_during_the_walk)):
            self.assertTrue(self.launcher.stop("raced", grace=1.0))
        self.assertTrue(self.finished().killed)
        self.assertTrue(self.gone(child), "a same-group child outlived a Stop that raced its worker's exit")
        self.assertIn(child, self.evidence(handle)["descendants"])
        self.launcher.assert_quiescent("raced", handle.pid)

    def test_stop_racing_a_self_exit_signals_the_group_even_without_waitid(self):
        # No session sweep is possible without waitid, but the group signal must still reach the child:
        # macOS refuses os.getpgid() for a worker that has just become a zombie.
        release = self.root / "old-release"
        with patch("agent.launcher._PINNED_EXIT", False):
            handle, child = self.leaking_worker("old-raced", release=str(release))
            real = Launcher.descendants

            def exits_during_the_walk(pid):
                release.touch()
                deadline = time.monotonic() + 10
                while not exited_unreaped(pid) and time.monotonic() < deadline:
                    time.sleep(0.01)
                return real(pid)

            with patch.object(Launcher, "descendants", staticmethod(exits_during_the_walk)):
                self.assertTrue(self.launcher.stop("old-raced", grace=1.0))
            self.finished()
        self.assertTrue(self.gone(child), "the group SIGTERM did not reach a child of a just-exited worker")

    def test_stop_terminates_a_group_member_the_parent_walk_cannot_see(self):
        # The child's parent exited first, so the walk from the live worker misses it; it ignores SIGTERM.
        handle, child = self.leaking_worker("orphaned", setup=TERM_IGNORED, orphan=True, linger=60)
        self.assertNotIn(child, Launcher.descendants(handle.pid))
        self.assertTrue(self.launcher.stop("orphaned", grace=1.0))
        self.finished()
        self.assertTrue(self.gone(child), "Stop left a same-group member running")
        self.assertIn(child, self.evidence(handle)["descendants"])
        self.launcher.assert_quiescent("orphaned", handle.pid)

    def test_a_repeated_stop_keeps_what_an_earlier_stop_recorded(self):
        # Stop re-runs each tick on a stuck worker. The earlier Stop recorded a setsid() grandchild whose
        # parent has since died, so this Stop's fresh walk cannot find it; the proof must still hold it.
        handle, child = self.leaking_worker("restop", kwargs="start_new_session=True", orphan=True, linger=60)
        self.assertNotIn(child, Launcher.descendants(handle.pid))
        (handle.run_dir / "killed.json").write_text(json.dumps({"pid": handle.pid, "descendants": [child]}),
                                                    encoding="utf-8")
        self.assertTrue(self.launcher.stop("restop", grace=1.0))
        self.finished()
        self.assertIn(child, self.evidence(handle)["descendants"])
        self.assertTrue(Launcher.alive(child), "an old recorded pid alone must never authorize a signal")
        with self.assertRaisesRegex(RuntimeError, "descendants have not exited"):
            self.launcher.assert_quiescent("restop", handle.pid)

    def test_a_failing_teardown_check_still_reports_every_exit(self):
        first = self.launcher.spawn("first", "prompt", {}, 30, self.root)
        second = self.launcher.spawn("second", "prompt", {}, 30, self.root)
        self.wait_exited(first)
        self.wait_exited(second)
        with patch.object(Launcher, "_group_gone", side_effect=RuntimeError("boom")):
            finished = self.launcher.poll()
        self.assertEqual(sorted(f.item_id for f in finished), ["first", "second"])
        for handle in (first, second):
            self.assertFalse((handle.run_dir / "killed.json").exists())
            self.assertEqual(self.evidence(handle, "teardown-unverified.json")["reason"],
                             "teardown check failed: RuntimeError")
            with self.assertRaisesRegex(RuntimeError, "teardown"):
                self.launcher.assert_quiescent(handle.item_id, handle.pid)

    def test_poll_leaves_a_worker_being_killed_to_kill(self):
        handle = self.launcher.spawn("owned", "prompt", {}, 30, self.root)
        self.wait_exited(handle)
        # Stop's _kill has taken this worker over on another thread: it reaps and records, poll() waits.
        self.launcher._killing.add("owned")
        self.assertEqual(self.launcher.poll(), [])
        self.assertTrue(exited_unreaped(handle.pid))
        self.launcher._killing.discard("owned")
        self.assertEqual(self.finished().returncode, 0)

    def test_a_claimed_worker_is_not_reaped_by_poll_even_without_waitid(self):
        handle = self.launcher.spawn("owned-old", "prompt", {}, 30, self.root)
        self.wait_exited(handle)
        self.launcher._killing.add("owned-old")
        with patch("agent.launcher._PINNED_EXIT", False):
            self.assertEqual(self.launcher.poll(), [])
        self.assertTrue(exited_unreaped(handle.pid), "poll() reaped a worker Stop's _kill had claimed")
        self.launcher._killing.discard("owned-old")
        self.assertEqual(self.finished().returncode, 0)

    def test_a_reap_that_cannot_complete_is_retried(self):
        handle = self.launcher.spawn("contended", "prompt", {}, 30, self.root)
        self.wait_exited(handle)
        # Popen.poll() answers None without reaping while another thread holds its waitpid lock.
        with handle.process._waitpid_lock:
            self.assertEqual(self.launcher.poll(), [])
        self.assertFalse((handle.run_dir / "teardown-unverified.json").exists())
        self.assertEqual(self.finished().returncode, 0)
        self.assertIs(self.evidence(handle)["empty"], True)


@unittest.skipIf(os.name == "nt", "POSIX sessions and process groups")
class PosixSessionScanTests(unittest.TestCase):
    """The scan and signal primitives on their own: every doubt about the table means None, not []."""
    LEADER = 900

    def ps(self, rows, returncode=0):
        me = os.getpid()
        return subprocess.CompletedProcess(["ps"], returncode, stdout=f"{me} {me} S\n" + rows, stderr="")

    def getsid(self, sessions):
        def lookup(pid):
            if pid in sessions:
                value = sessions[pid]
                if isinstance(value, BaseException):
                    raise value
                return value
            return 1
        return lookup

    def scan(self, run, sessions=None):
        with patch("agent.launcher.subprocess.run", **run), \
                patch("agent.launcher.os.getsid", side_effect=self.getsid(sessions or {})):
            return Launcher.session_members(self.LEADER)

    def test_group_and_session_members_are_listed_but_not_the_leader_or_zombies(self):
        rows = "900 900 Ss\n901 900 Z\n902 555 S\n903 900 S+\n904 556 S\n"
        self.assertEqual(self.scan({"return_value": self.ps(rows)}, {902: 900, 904: 904}), [902, 903])

    def test_a_member_that_exits_before_getsid_is_skipped(self):
        rows = "902 555 S\n903 900 S\n"
        self.assertEqual(self.scan({"return_value": self.ps(rows)}, {902: ProcessLookupError()}), [903])

    def test_ps_that_fails_or_times_out_proves_nothing(self):
        for failure in (OSError("no ps"), subprocess.TimeoutExpired("ps", 5)):
            with self.subTest(failure=type(failure).__name__):
                self.assertIsNone(self.scan({"side_effect": failure}))

    def test_a_failed_ps_exit_proves_nothing(self):
        self.assertIsNone(self.scan({"return_value": self.ps("903 900 S\n", returncode=1)}))

    def test_a_table_that_does_not_list_farmbot_itself_proves_nothing(self):
        truncated = subprocess.CompletedProcess(["ps"], 0, stdout="903 900 S\n", stderr="")
        self.assertIsNone(self.scan({"return_value": truncated}))

    def test_a_session_that_cannot_be_read_proves_nothing(self):
        rows = "902 555 S\n"
        self.assertIsNone(self.scan({"return_value": self.ps(rows)}, {902: PermissionError()}))

    def test_signals_reach_the_group_and_only_members_still_in_the_session(self):
        with patch("agent.launcher.os.killpg") as killpg, patch("agent.launcher.os.kill") as kill, \
                patch("agent.launcher.os.getsid", side_effect=self.getsid({902: 900, 903: 777, 904: ProcessLookupError()})):
            Launcher._signal_session(self.LEADER, [902, 903, 904], signal.SIGTERM)
        killpg.assert_called_once_with(self.LEADER, signal.SIGTERM)
        kill.assert_called_once_with(902, signal.SIGTERM)

    def test_the_group_is_gone_only_when_the_kernel_says_so(self):
        for outcome, expected in ((ProcessLookupError(), True), (PermissionError(), False), (None, False)):
            with self.subTest(outcome=type(outcome).__name__):
                with patch("agent.launcher.os.killpg", side_effect=outcome):
                    self.assertIs(Launcher._group_gone(self.LEADER, timeout=0.05), expected)


class PriorTeardownRecordTests(unittest.TestCase):
    """killed.json lives in the worker-writable state directory, so reading it must never raise."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.handle = Handle("item", 4242, 0, 0, Path(self.tmp.name), None, None)

    def prior(self, text):
        (self.handle.run_dir / "killed.json").write_text(text, encoding="utf-8")
        return Launcher._prior_descendants(self.handle)

    def test_no_record_is_an_empty_one(self):
        self.assertEqual(Launcher._prior_descendants(self.handle), [])

    def test_the_attempts_own_record_is_read(self):
        self.assertEqual(self.prior(json.dumps({"pid": 4242, "descendants": [7, 8]})), [7, 8])

    def test_anything_else_proves_nothing(self):
        cases = {
            "unparsable": "{not json",
            "overflowing pid": '{"pid": 4242, "descendants": [1e999]}',
            "deeply nested": '{"pid": 4242, "descendants": ' + "[" * 100000 + "]" * 100000 + "}",
            "string descendants": '{"pid": 4242, "descendants": "12"}',
            "non-positive pid": '{"pid": 4242, "descendants": [0, -3]}',
            "boolean pid": '{"pid": 4242, "descendants": [true]}',
            "float pid": '{"pid": 4242, "descendants": [7.0]}',
            "not an object": "[4242]",
            "another attempt": '{"pid": 1, "descendants": []}',
        }
        for name, text in cases.items():
            with self.subTest(name):
                self.assertIsNone(self.prior(text))
