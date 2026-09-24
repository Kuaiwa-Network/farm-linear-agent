from contextlib import redirect_stdout
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import agent.launcher
from agent.launcher import _PINNED_EXIT, Handle, Launcher, RUNTIMES, write_mcp_config

ROOT = Path(__file__).resolve().parents[1]
CAPACITY = "ERROR: Selected model is at capacity. Please try a different model."


def unblocked(test, call, fifo, timeout=5):
    """call()'s result, failing the test if it is still running after `timeout`.

    The call runs on this thread, since a ledger connection may be used only on the thread that made it. From
    then on a watchdog opens both ends of the FIFO at `fifo` (a path, or a callable giving one once the call has
    started), until the call returns: an open() waiting for a reader or a writer then proceeds, so a call that
    blocks on the FIFO, as often as it does, cannot hang the suite.
    """
    done, blocked = threading.Event(), []

    def watch():
        if done.wait(timeout):
            return
        blocked.append(fifo() if callable(fifo) else fifo)
        while not done.is_set():
            try:
                reader = os.open(blocked[0], os.O_RDONLY | os.O_NONBLOCK)  # a writer blocked opening it proceeds
            except OSError:
                done.wait(0.2)
                continue
            writer = os.open(blocked[0], os.O_WRONLY | os.O_NONBLOCK)  # as does a reader
            done.wait(1)
            os.close(writer)  # a released reader now reads end of file
            try:
                os.read(reader, 1 << 16)  # and a released writer's record never waits for room
            except BlockingIOError:
                pass
            os.close(reader)
    watchdog = threading.Thread(target=watch, daemon=True)
    watchdog.start()
    try:
        return call()
    finally:
        done.set()
        watchdog.join()
        if blocked:
            test.fail(f"blocked for {timeout} s on a FIFO at {blocked[0].name}")


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

    def finished_by(self, launcher, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            finished = launcher.poll()
            if finished:
                return finished
            time.sleep(0.02)
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

    def test_a_codex_home_records_its_cwd_as_untrusted_under_both_spellings(self):
        """codex-cli 0.155.1 `exec` writes `trust_level = "trusted"` into CODEX_HOME for a cwd it has no decision
        for, and trust loads the repository's own .codex/config.toml, whose MCP servers may carry inline
        credentials. An explicit decision stops both. The --cd spelling is the one exec was measured to honour;
        the resolved one covers canonical lookups. Paths may hold spaces, CJK and characters beyond U+FFFF."""
        import tomllib
        state = Path(self.tmp.name) / "state 农场 🐄"
        worktree = state / "worktrees" / "item 1" / "Farm-Client"
        worktree.mkdir(parents=True)
        cwd = worktree
        link = Path(self.tmp.name) / "linked state"
        try:
            link.symlink_to(state, target_is_directory=True)
            cwd = link / "worktrees" / "item 1" / "Farm-Client"
        except OSError:
            pass  # Windows without the symlink privilege
        # The fake reads only its first argument, so the real codex arguments ride along to be inspected.
        runtime = RUNTIMES["codex"]._replace(command=[*RUNTIMES["fake"].command, *RUNTIMES["codex"].command[1:]],
                                             seed_files={})
        launcher = Launcher(self.runs, runtime, host="h")
        handle = launcher.spawn("item-trust", self.message, {"unity": {"url": "http://127.0.0.1:8080/mcp"}}, 60, cwd,
                                extra_env={"FAKE_CLI_MODE": "echo"}, writable=[worktree])
        self.addCleanup(launcher.stop, "item-trust")
        deadline = time.time() + 10
        finished = []
        while time.time() < deadline and not finished:
            finished = launcher.poll()
            time.sleep(0.05)
        self.assertTrue(finished, "worker did not finish")
        config = tomllib.loads((handle.run_dir / "home" / "config.toml").read_text(encoding="utf-8"))
        argv = [str(part) for part in handle.process.args]
        self.assertEqual(argv[argv.index("--cd") + 1], str(cwd))
        if link.is_symlink():
            self.assertNotEqual(str(cwd), str(cwd.resolve()))
        self.assertEqual(config["projects"], {str(cwd): {"trust_level": "untrusted"},
                                              str(cwd.resolve()): {"trust_level": "untrusted"}})
        self.assertEqual(config["sandbox_mode"], "workspace-write")
        self.assertIn(str(worktree), config["sandbox_workspace_write"]["writable_roots"])
        self.assertEqual(config["mcp_servers"]["unity"]["url"], "http://127.0.0.1:8080/mcp")

    def test_a_claude_worker_loads_settings_only_from_its_isolated_home(self):
        """`claude -p` skips the workspace trust dialog. Measured with Claude Code 2.1.229, the cwd's
        .claude/settings.json and settings.local.json then load: their hooks ran before the first model request
        and around a tool call, their apiKeyHelper ran, and their env pointed ANTHROPIC_BASE_URL at a URL of
        their choosing, which received the worker's requests and OAuth token. That cwd may be a repository or
        the job's state directory, which the worker writes and every attempt shares; settings.json loaded from
        both. `--setting-sources user` leaves only the isolated CLAUDE_CONFIG_DIR; it also stops the skills and
        agents of --add-dir directories. --strict-mcp-config leaves only the injected mcp.json: without it, a
        repository .mcp.json server that the repository's own settings approved started. Paths may hold spaces,
        CJK and characters beyond U+FFFF."""
        state = Path(self.tmp.name) / "state 农场 🐄"
        repo = state / "worktrees" / "item 1" / "Farm-Client"
        sibling = repo.parent / "farm-hive"
        database = state / "db"
        for root in (repo, sibling, database):
            root.mkdir(parents=True)
        # The fake reads only its first argument, so the real claude arguments ride along to be inspected.
        runtime = RUNTIMES["claude"]._replace(command=[*RUNTIMES["fake"].command, *RUNTIMES["claude"].command[1:]])
        launcher = Launcher(self.runs, runtime, host="h")
        # A worker rooted in a worktree, and one started as chat is, from its job's own state directory.
        launches = {"item-root": (repo, [repo, sibling], [repo, self.runs / "item-root", repo, sibling]),
                    "item-chat": (launcher.state_dir("item-chat"), [database],
                                  [self.runs / "item-chat", self.runs / "item-chat", database])}

        def reap():  # never leave a child unreaped, even if an assertion below fails first
            for item_id in launches:
                launcher.stop(item_id)
            launcher.poll()

        self.addCleanup(reap)
        for item_id, (cwd, writable, add_dirs) in launches.items():
            with self.subTest(item_id):
                handle = launcher.spawn(item_id, self.message, {"probe": {"command": "python3"}}, 60, cwd,
                                        extra_env={"FAKE_CLI_MODE": "echo"}, writable=writable)
                argv = [str(part) for part in handle.process.args]
                sources = ([argv[index + 1] for index, part in enumerate(argv) if part == "--setting-sources"]
                           + [part.partition("=")[2] for part in argv if part.startswith("--setting-sources=")])
                self.assertEqual(sources, ["user"])
                self.assertIn("--strict-mcp-config", argv)
                self.assertEqual(argv[argv.index("--mcp-config") + 1], str(handle.run_dir / "home" / "mcp.json"))
                self.assertEqual([argv[index + 1] for index, part in enumerate(argv) if part == "--add-dir"],
                                 [str(path) for path in add_dirs])

    def test_home_config_strings_survive_any_path_character(self):
        """One TOML encoder writes every string. It reuses JSON's escapes, except that a character beyond U+FFFF
        must not become a surrogate pair. An undecodable name (a lone surrogate) still makes the file invalid,
        so Codex refuses the config instead of reading some other path."""
        import tomllib
        home = Path(self.tmp.name) / "home"
        home.mkdir()
        values = ['quote " and backslash \\', "tab\tnewline\ncr\r", "nul\x00 del\x7f", "C:\\Users\\A B\\农场",
                  "/w/state 🐄/Farm-Client"]
        servers = {f"s{index}": {"command": value} for index, value in enumerate(values)}
        config = tomllib.loads(write_mcp_config(home, "toml", servers).read_text(encoding="utf-8"))
        self.assertEqual([config["mcp_servers"][name]["command"] for name in servers], values)
        write_mcp_config(home, "toml", {"s": {"command": "/w/\udc80"}})
        with self.assertRaises(tomllib.TOMLDecodeError):
            tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))

    def test_a_withheld_variable_never_reaches_the_worker(self):
        # Braces are doubled: spawn formats every command part.
        script = ("import json,os,pathlib,sys;sys.stdin.read();"
                  "pathlib.Path(sys.argv[1]).write_text(json.dumps({{k: os.environ.get(k) for k in "
                  "['KW_OPS_TOKEN', 'KEPT_MARKER']}}))")
        runtime = RUNTIMES["codex"]._replace(command=[sys.executable, "-c", script, "{last_message}"], seed_files={})
        launcher = Launcher(self.runs, runtime, host="h")
        with patch.dict(os.environ, {"KW_OPS_TOKEN": "dummy-token-value", "KEPT_MARKER": "kept"}):
            launcher.spawn("item-withheld", self.message, {}, 30, self.tmp.name,
                           extra_env={"KW_OPS_TOKEN": "reintroduced-token"}, withheld_env=["KW_OPS_TOKEN"])
        self.addCleanup(launcher.stop, "item-withheld")
        finished = self.finished_by(launcher)
        self.assertEqual(json.loads(finished[0].last_message), {"KW_OPS_TOKEN": None, "KEPT_MARKER": "kept"})

    def test_a_codex_worker_with_a_token_server_hides_the_token_from_its_shell(self):
        import tomllib
        runtime = RUNTIMES["codex"]._replace(command=RUNTIMES["fake"].command, seed_files={})
        launcher = Launcher(self.runs, runtime, host="h")
        server = {"url": "https://gm.test/mcp", "bearer_token_env_var": "KW_OPS_TOKEN"}
        with patch.dict(os.environ, {"KW_OPS_TOKEN": "dummy-token-value"}):
            handle = launcher.spawn("item-secret", self.message, {"kw_ops": server}, 30, self.tmp.name,
                                    extra_env={"FAKE_CLI_MODE": "echo"})
        self.addCleanup(launcher.stop, "item-secret")
        self.finished_by(launcher)
        text = (handle.run_dir / "home" / "config.toml").read_text(encoding="utf-8")
        config = tomllib.loads(text)
        self.assertEqual(config["mcp_servers"]["kw_ops"], server)
        self.assertEqual(config["shell_environment_policy"], {"exclude": ["KW_OPS_TOKEN"]})
        # codex-cli 0.156.1 re-exports an excluded variable from its shell snapshot unless the snapshot is off.
        self.assertIs(config["features"]["shell_snapshot"], False)
        self.assertNotIn("dummy-token-value", text)

    def test_a_codex_worker_without_a_token_server_keeps_its_shell_settings(self):
        import tomllib
        runtime = RUNTIMES["codex"]._replace(command=RUNTIMES["fake"].command, seed_files={})
        launcher = Launcher(self.runs, runtime, host="h")
        handle = launcher.spawn("item-plain", self.message, {"unity": {"url": "http://127.0.0.1:8080/mcp"}}, 30,
                                self.tmp.name, extra_env={"FAKE_CLI_MODE": "echo"})
        self.addCleanup(launcher.stop, "item-plain")
        self.finished_by(launcher)
        config = tomllib.loads((handle.run_dir / "home" / "config.toml").read_text(encoding="utf-8"))
        self.assertNotIn("shell_environment_policy", config)
        self.assertEqual(config["features"], {"memories": False})

    def test_sandbox_roots_and_network_access_precede_mcp_servers_in_the_home_config(self):
        codex_command = RUNTIMES["codex"].command
        self.assertIn("--approve-for-me", codex_command)
        self.assertNotIn("--sandbox", codex_command)
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

    def test_a_last_message_the_worker_made_unreadable_reports_it_with_an_empty_message(self):
        # last_message.txt is in the worker-writable state directory. Reading it once raised on every poll, so the
        # reaped worker stayed registered for ever and held its max_concurrent slot until restart.
        corruptions = {"undecodable": lambda path: path.write_bytes(b"report \xff\xfe"), "directory": Path.mkdir}
        if os.name != "nt" and os.geteuid() != 0:
            # Its existence check raises too once the worker makes its run directory unsearchable.
            corruptions["unsearchable"] = lambda path: path.parent.chmod(0)
        for name, corrupt in corruptions.items():
            with self.subTest(corruption=name):
                # A launcher of its own: a worker left registered by one case must not be polled by the next.
                launcher = Launcher(self.runs / name, RUNTIMES["fake"], host="test-host")
                handle = launcher.spawn(name, self.message, {}, budget_seconds=60, cwd=self.tmp.name,
                                        extra_env={"FAKE_CLI_MODE": "crash"})
                self.addCleanup(handle.run_dir.chmod, 0o700)
                self.wait_exited(handle)
                corrupt(handle.last_message_path)
                log = io.StringIO()
                with redirect_stdout(log):
                    finished = launcher.poll()
                self.assertEqual([(f.item_id, f.returncode, f.last_message) for f in finished], [(name, 3, "")])
                self.assertNotIn(name, launcher.running())
                self.assertEqual(log.getvalue(), "")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFOs in a directory are POSIX")
    def test_a_fifo_the_worker_left_as_a_report_file_cannot_block_poll(self):
        # Opening a FIFO for reading waits for a writer, and after the reap there is none: poll(), and with it the
        # whole scheduler loop, stopped for ever without raising anything a guard could catch.
        cases = {"last_message.txt": "fake", "stdout.log": "claude", "stderr.log": "codex"}
        for name, runtime in cases.items():
            with self.subTest(file=name, runtime=runtime):
                config = RUNTIMES[runtime]._replace(command=RUNTIMES["fake"].command, seed_files={})
                launcher = Launcher(self.runs / runtime, config, host="test-host")
                handle = launcher.spawn(runtime, self.message, {}, budget_seconds=60, cwd=self.tmp.name,
                                        extra_env={"FAKE_CLI_MODE": "crash"})
                self.wait_exited(handle)
                fifo = handle.run_dir / name
                fifo.unlink(missing_ok=True)
                os.mkfifo(fifo)
                finished = unblocked(self, launcher.poll, fifo)
                self.assertEqual([(f.item_id, f.returncode, f.last_message, f.failure_kind) for f in finished],
                                 [(runtime, 3, "", None)])
                self.assertNotIn(runtime, launcher.running())

    def test_a_capacity_error_ending_a_log_larger_than_the_read_bound_is_still_classified(self):
        # Only stderr.log's tail is read: a long codex log must not be refused as oversized.
        codex = Launcher(self.runs, RUNTIMES["codex"]._replace(command=RUNTIMES["fake"].command, seed_files={}),
                         host="test-host")
        handle = codex.spawn("item-1", self.message, {}, budget_seconds=60, cwd=self.tmp.name,
                             extra_env={"FAKE_CLI_MODE": "crash"})
        self.wait_exited(handle)
        (handle.run_dir / "stderr.log").write_bytes(
            b"tool output\n" * (2 * agent.launcher._WORKER_FILE_LIMIT // 12) + CAPACITY.encode() + b"\n")
        self.assertEqual([f.failure_kind for f in codex.poll()], ["model_capacity"])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFOs in a directory are POSIX")
    def test_stop_does_not_block_on_a_fifo_teardown_record_and_holds_cleanup(self):
        handle = self.launcher.spawn("item-1", self.message, {}, budget_seconds=60, cwd=self.tmp.name,
                                     extra_env={"FAKE_CLI_MODE": "sleep"})
        fifo = handle.run_dir / "killed.json"
        os.mkfifo(fifo)
        self.assertTrue(unblocked(self, lambda: self.launcher.stop("item-1", grace=2.0), fifo))
        unverified = json.loads((handle.run_dir / "teardown-unverified.json").read_text(encoding="utf-8"))
        self.assertEqual(unverified["reason"], "earlier teardown record is unreadable or not this attempt's")
        self.assertEqual([f.reason for f in self.wait_finished()], ["stopped"])
        with self.assertRaisesRegex(RuntimeError, "teardown"):
            self.launcher.assert_quiescent("item-1", handle.pid)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFOs in a directory are POSIX")
    def test_cleanup_checks_refuse_a_fifo_launch_or_teardown_record_without_blocking(self):
        # The scheduler thread runs these before a job's worktrees and slot are released. Refusing holds cleanup.
        state = self.launcher.state_dir("item-1")

        def attempt(name, record=None):
            path = state / name
            path.mkdir(parents=True)
            if record is not None:
                (path / "process.json").write_text(json.dumps(record), encoding="utf-8")
            return path
        cases = {
            "an attempt's launch record": (lambda: attempt("a") / "process.json",
                                           lambda: self.launcher.assert_quiescent("item-1", None)),
            "an attempt's teardown record": (lambda: attempt("a", {"pid": 4242}) / "killed.json",
                                             lambda: self.launcher.assert_quiescent("item-1", 4242)),
            "a teardown record beside no attempt": (lambda: attempt("stray") / "killed.json",
                                                    lambda: self.launcher.assert_quiescent("item-1", None)),
            "another attempt's launch record, before a kill": (
                lambda: attempt("stray") / "process.json", lambda: self.launcher.kill_owned_attempt("item-1", 4242)),
        }
        for name, (place, check) in cases.items():
            with self.subTest(name):
                shutil.rmtree(state, ignore_errors=True)
                fifo = place()
                os.mkfifo(fifo)
                with self.assertRaises(OSError):
                    unblocked(self, check, fifo)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFOs in a directory are POSIX")
    def test_a_kill_after_a_restart_refuses_a_fifo_teardown_record_before_signalling(self):
        handle = self.launcher.spawn("item-1", self.message, {}, budget_seconds=60, cwd=self.tmp.name,
                                     extra_env={"FAKE_CLI_MODE": "sleep"})
        self.addCleanup(self.launcher.stop, "item-1", grace=1.0)
        fifo = handle.run_dir / "killed.json"
        os.mkfifo(fifo)
        self.addCleanup(fifo.unlink, missing_ok=True)  # before the Stop above, which would read it
        with self.assertRaises(OSError):
            unblocked(self, lambda: self.launcher.kill_owned_attempt("item-1", handle.pid), fifo)
        self.assertIsNone(handle.process.poll())

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFOs in a directory are POSIX")
    def test_a_stop_replaces_a_fifo_the_live_worker_swapped_in_for_its_teardown_record(self):
        # The worker is still running while Stop reads killed.json and then writes it: it can swap in a FIFO,
        # whose open for writing waits for a reader that never comes.
        handle = self.launcher.spawn("item-1", self.message, {}, budget_seconds=60, cwd=self.tmp.name,
                                     extra_env={"FAKE_CLI_MODE": "sleep"})
        fifo = handle.run_dir / "killed.json"
        walk = Launcher.descendants

        def swapped_during_the_walk(pid):
            if not fifo.exists():
                os.mkfifo(fifo)
            return walk(pid)
        with patch.object(Launcher, "descendants", side_effect=swapped_during_the_walk):
            self.assertTrue(unblocked(self, lambda: self.launcher.stop("item-1", grace=2.0), fifo))
        self.assertEqual(json.loads(fifo.read_text(encoding="utf-8")), {"pid": handle.pid, "descendants": []})
        self.assertEqual([f.reason for f in self.wait_finished()], ["stopped"])
        self.launcher.assert_quiescent("item-1", handle.pid)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFOs in a directory are POSIX")
    def test_spawn_records_the_pid_past_a_fifo_the_new_worker_put_at_its_launch_record(self):
        popen = subprocess.Popen
        records = []

        def fast_worker(command, **kwargs):
            process = popen(command, **kwargs)
            if not records:  # the worker is running, and replaces its launch record before spawn writes it
                records.append(Path(command[-1]).parent / "process.json")
                records[0].unlink()
                os.mkfifo(records[0])
            return process
        with patch("agent.launcher.subprocess.Popen", side_effect=fast_worker):
            handle = unblocked(self, lambda: self.launcher.spawn(
                "item-1", self.message, {}, budget_seconds=60, cwd=self.tmp.name,
                extra_env={"FAKE_CLI_MODE": "crash"}), lambda: records[0])
        self.assertEqual(json.loads(records[0].read_text(encoding="utf-8")), {"pid": handle.pid})
        self.assertEqual([f.returncode for f in self.wait_finished()], [3])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFOs in a directory are POSIX")
    def test_spawn_notes_an_undelivered_prompt_past_a_fifo_the_worker_left_for_the_note(self):
        # The worker plants the FIFO, then closes stdin unread: a prompt larger than the pipe fails to arrive.
        script = ("import os, sys, time\n"
                  "os.mkfifo(os.path.join(os.path.dirname(sys.argv[1]), 'stdin-error.txt'))\n"
                  "os.close(0)\n"
                  "time.sleep(30)\n")
        self.launcher.runtime = RUNTIMES["fake"]._replace(command=[sys.executable, "-c", script, "{last_message}"])
        self.addCleanup(self.wait_finished)
        self.addCleanup(self.launcher.stop, "item-1", grace=1.0)
        handle = unblocked(self, lambda: self.launcher.spawn("item-1", "x" * (1 << 20), {}, 60, self.tmp.name),
                           lambda: self.launcher.running()["item-1"].run_dir / "stdin-error.txt")
        self.assertEqual((handle.run_dir / "stdin-error.txt").read_text(encoding="utf-8"),
                         "BrokenPipeError: prompt not fully delivered\n")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFOs in a directory are POSIX")
    def test_a_kill_after_a_restart_persists_its_targets_past_a_fifo_left_for_their_temporary_file(self):
        handle = self.launcher.spawn("item-1", self.message, {}, budget_seconds=60, cwd=self.tmp.name,
                                     extra_env={"FAKE_CLI_MODE": "sleep"})
        fifo = handle.run_dir / "killed.tmp"
        os.mkfifo(fifo)
        fsync, synced = os.fsync, []
        with patch("agent.launcher.os.fsync", side_effect=lambda fd: (synced.append(fd), fsync(fd))):
            # 10 s: the kill then waits out its 5 s grace, since this unreaped child's zombie answers alive().
            recorded = unblocked(self, lambda: self.launcher.kill_owned_attempt("item-1", handle.pid), fifo,
                                 timeout=10)
        self.assertEqual(recorded, [handle.pid])
        self.assertTrue(synced, "the targets are no longer flushed to disk before the kill")
        self.assertEqual(json.loads((handle.run_dir / "killed.json").read_text(encoding="utf-8")),
                         {"pid": handle.pid, "descendants": []})
        self.assertEqual([f.returncode for f in self.wait_finished()], [-signal.SIGTERM])

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

    def test_an_unsandboxed_unity_child_keeps_host_settings_without_the_kw_ops_token(self):
        launcher = Launcher(Path(self.tmp.name) / "unity-runs", RUNTIMES["fake"], host="test",
                            token_env="KW_OPS_TOKEN")
        result_path = Path(self.tmp.name) / "unity-env.json"
        script = ("import json,os,pathlib,sys; "
                  "pathlib.Path(sys.argv[1]).write_text(json.dumps({"
                  "'token': os.environ.get('KW_OPS_TOKEN'), "
                  "'license': os.environ.get('UNITY_LICENSE_MARKER')}))")
        with patch.dict(os.environ, {"KW_OPS_TOKEN": "dummy-token", "UNITY_LICENSE_MARKER": "kept"}):
            result = launcher.run_unsandboxed([sys.executable, "-c", script, str(result_path)],
                                              cwd=self.tmp.name, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result_path.read_text(encoding="utf-8")),
                         {"token": None, "license": "kept"})

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

    def test_a_fifo_teardown_record_holds_cleanup_without_blocking_the_poll(self):
        handle = self.launcher.spawn("fifo", "prompt", {}, 30, self.root)
        self.wait_exited(handle)
        fifo = handle.run_dir / "killed.json"
        os.mkfifo(fifo)
        self.assertEqual([f.reason for f in unblocked(self, self.launcher.poll, fifo)], ["exited"])
        self.assertEqual(self.evidence(handle, "teardown-unverified.json")["reason"],
                         "earlier teardown record is unreadable or not this attempt's")
        self.assertTrue((handle.run_dir / "killed.superseded.json").is_fifo())
        with self.assertRaisesRegex(RuntimeError, "teardown"):
            self.launcher.assert_quiescent("fifo", handle.pid)

    def test_what_the_worker_left_for_the_unverified_record_is_replaced_not_opened(self):
        # An unusable killed.json makes the reap hold cleanup, writing teardown-unverified.json. Opening a FIFO
        # there for writing waited for ever, and a symlink redirected the service's write out of the sandbox.
        outside = self.root / "outside.txt"
        plants = {"fifo": os.mkfifo, "symlink": lambda path: path.symlink_to(outside)}
        for name, plant in plants.items():
            with self.subTest(name):
                outside.write_text("not FarmBot's", encoding="utf-8")
                handle = self.launcher.spawn(name, "prompt", {}, 30, self.root)
                self.wait_exited(handle)
                (handle.run_dir / "killed.json").write_text("not json", encoding="utf-8")
                planted = handle.run_dir / "teardown-unverified.json"
                plant(planted)
                self.assertEqual([f.reason for f in unblocked(self, self.launcher.poll, planted)], ["exited"])
                self.assertEqual(self.evidence(handle, "teardown-unverified.json")["reason"],
                                 "earlier teardown record is unreadable or not this attempt's")
                self.assertEqual(outside.read_text(encoding="utf-8"), "not FarmBot's")
                self.assertEqual((handle.run_dir / "killed.superseded.json").read_text(encoding="utf-8"),
                                 "not json")
                with self.assertRaisesRegex(RuntimeError, "teardown"):
                    self.launcher.assert_quiescent(name, handle.pid)

    def test_a_fifo_that_appears_at_the_teardown_record_is_replaced_by_the_verified_one(self):
        # Evidence is written last, after the session scan and the reap: whatever is at killed.json by then is
        # not what was read, and a FIFO must not stop the write that certifies the exit.
        handle = self.launcher.spawn("appears", "prompt", {}, 30, self.root)
        self.wait_exited(handle)
        fifo = handle.run_dir / "killed.json"
        gone = Launcher._group_gone

        def appears_before_the_write(leader, timeout=1.0):
            os.mkfifo(fifo)
            return gone(leader, timeout)
        with patch.object(Launcher, "_group_gone", side_effect=appears_before_the_write):
            self.assertEqual([f.reason for f in unblocked(self, self.launcher.poll, fifo)], ["exited"])
        self.assertEqual(self.evidence(handle), {"pid": handle.pid, "descendants": [], "terminated": [],
                                                 "posix_session": handle.pid, "exited": True, "empty": True})
        self.launcher.assert_quiescent("appears", handle.pid)

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


class WorkerFileReadTests(unittest.TestCase):
    """Every file the launcher reads from a worker's state directory is read as a bounded regular file."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "record"

    def read(self, path=None, **kwargs):
        return agent.launcher._read_worker_file(self.path if path is None else path, **kwargs)

    def test_a_regular_file_is_read_as_its_exact_bytes(self):
        # Windows opens a file descriptor in text mode unless told otherwise, which ends a read at Ctrl-Z.
        self.path.write_bytes(b"first\r\nsecond\x1athird\xff")
        self.assertEqual(self.read(), b"first\r\nsecond\x1athird\xff")

    def test_text_is_decoded_as_read_text_decoded_it(self):
        self.path.write_bytes("first\r\nsecond\rthird\n—".encode("utf-8"))
        self.assertEqual(agent.launcher._read_worker_text(self.path), "first\nsecond\nthird\n—")
        self.path.write_bytes(b"report \xff\xfe")
        with self.assertRaises(UnicodeDecodeError):
            agent.launcher._read_worker_text(self.path)

    def test_a_file_up_to_the_bound_is_read_and_a_larger_one_is_refused(self):
        limit = agent.launcher._WORKER_FILE_LIMIT
        self.path.write_bytes(b"x" * limit)
        self.assertEqual(len(self.read()), limit)
        self.path.write_bytes(b"x" * (limit + 1))
        with self.assertRaises(OSError):
            self.read()

    def test_a_tail_is_only_the_last_bytes_of_a_file_of_any_size(self):
        self.path.write_bytes(b"x" * (4 * agent.launcher._WORKER_FILE_LIMIT) + b"final line\n")
        tail = self.read(tail=4096)
        self.assertEqual((len(tail), tail[-11:]), (4096, b"final line\n"))
        self.path.write_bytes(b"short\n")
        self.assertEqual(self.read(tail=4096), b"short\n")

    def test_a_device_is_refused(self):
        with self.assertRaises(OSError):
            self.read(Path(os.devnull))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFOs in a directory are POSIX")
    def test_a_fifo_is_refused_without_waiting_for_a_writer(self):
        os.mkfifo(self.path)
        with self.assertRaises(OSError):
            unblocked(self, self.read, self.path)

    @unittest.skipIf(os.name == "nt", "Windows has no O_NOFOLLOW; making a symlink there needs a privilege")
    def test_a_symlink_is_refused_even_to_a_regular_file(self):
        # Nothing FarmBot or a CLI writes there is a link: a worker made it, and it may lead off this filesystem.
        target = Path(self.tmp.name) / "elsewhere"
        target.write_text("{}", encoding="utf-8")
        self.path.symlink_to(target)
        with self.assertRaises(OSError):
            self.read()

    @unittest.skipUnless(os.name == "nt", "the Windows containment path reads every attempt's launch record")
    def test_an_oversized_launch_record_is_refused_by_the_windows_containment_check(self):
        attempt = Path(self.tmp.name) / "runs" / "item" / "attempt"
        attempt.mkdir(parents=True)
        (attempt / "process.json").write_text(
            '{"state": "not_started"}' + " " * agent.launcher._WORKER_FILE_LIMIT, encoding="utf-8")
        with self.assertRaises(OSError):
            Launcher(Path(self.tmp.name) / "runs", RUNTIMES["fake"], host="test-host").certified_pids("item")


class WorkerFileWriteTests(unittest.TestCase):
    """Every file FarmBot writes where a worker can write replaces what is there instead of opening it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "record.json"

    def write(self, text, **kwargs):
        agent.launcher._write_worker_file(self.path, text, **kwargs)

    def test_the_file_holds_exactly_what_write_text_wrote_with_its_permissions(self):
        # Including Windows, where text mode writes each "\n" as "\r\n" and an fd opened without O_BINARY
        # would translate it a second time.
        text = '{\n  "state": "ran",\n  "result": "—"\n}'
        self.write(text)
        expected = self.root / "expected.json"
        expected.write_text(text, encoding="utf-8")
        self.assertEqual(self.path.read_bytes(), expected.read_bytes())
        self.assertEqual(self.path.stat().st_mode, expected.stat().st_mode)
        self.write("second")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "second")
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["expected.json", "record.json"])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFOs in a directory are POSIX")
    def test_a_fifo_at_the_name_is_replaced_without_waiting_for_a_reader(self):
        os.mkfifo(self.path)
        unblocked(self, lambda: self.write("{}"), self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{}")

    @unittest.skipIf(os.name == "nt", "making a symlink on Windows needs a privilege")
    def test_a_symlink_at_the_name_is_replaced_and_what_it_names_is_left_alone(self):
        outside = self.root / "outside.txt"
        outside.write_text("not FarmBot's", encoding="utf-8")
        self.path.symlink_to(outside)
        self.write("{}")
        self.assertFalse(self.path.is_symlink())
        self.assertEqual((self.path.read_text(encoding="utf-8"), outside.read_text(encoding="utf-8")),
                         ("{}", "not FarmBot's"))

    def test_anything_already_at_the_temporary_name_is_refused_and_left_alone(self):
        # The name is unguessable; were it guessed, O_EXCL still refuses to open what the worker put there.
        planted = self.root / ".record.json.0123456789abcdef0123456789abcdef.tmp"
        planted.write_text("not FarmBot's", encoding="utf-8")
        with patch("agent.launcher.uuid4", return_value=uuid.UUID(planted.name.split(".")[3])):
            with self.assertRaises(FileExistsError):
                self.write("{}")
        self.assertEqual((planted.read_text(encoding="utf-8"), self.path.exists()), ("not FarmBot's", False))

    def test_a_replace_that_fails_raises_and_leaves_no_temporary_file(self):
        (self.path / "inside").mkdir(parents=True)
        with self.assertRaises(OSError):
            self.write("{}")
        self.assertEqual([p.name for p in self.root.iterdir()], ["record.json"])

    def test_a_synced_write_reaches_the_disk_before_it_replaces_the_name(self):
        self.path.write_text("earlier", encoding="utf-8")
        fsync, seen = os.fsync, []
        with patch("agent.launcher.os.fsync",
                   side_effect=lambda fd: (seen.append(self.path.read_text(encoding="utf-8")), fsync(fd))):
            self.write("later")
            self.assertEqual(seen, [])
            self.write("synced", sync=True)
        self.assertEqual((seen, self.path.read_text(encoding="utf-8")), (["later"], "synced"))
