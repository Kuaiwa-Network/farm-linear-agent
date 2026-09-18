import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.launcher import Launcher, RUNTIMES

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
