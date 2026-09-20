import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.launcher import Launcher, RUNTIMES, write_mcp_config

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
        expected_roots = json.dumps([str(self.runs / "item-3"), "/w/item-3/Farm-Client", "/repo/.local/agent"])
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
                                                    "--add-dir", "/w/item-4/Farm-Client",
                                                    "--add-dir", "/repo/.local/agent"])
        self.assertEqual(RUNTIMES["claude"].writable_flag, "--add-dir")
        self.assertIsNone(RUNTIMES["codex"].writable_flag)
