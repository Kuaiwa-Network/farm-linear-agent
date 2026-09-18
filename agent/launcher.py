"""Spawn, watch and kill one headless CLI worker per work item (spec §8)."""
from collections import namedtuple
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

RuntimeConfig = namedtuple("RuntimeConfig", "name command home_env mcp_format seed_files")
Handle = namedtuple("Handle", "item_id pid started_at deadline run_dir process last_message_path")
Finished = namedtuple("Finished", "item_id returncode last_message killed reason")

RUNTIMES = {
    "codex": RuntimeConfig(
        name="codex",
        command=["codex", "exec", "--cd", "{cwd}", "--approve-for-me", "--skip-git-repo-check",
                 "--output-last-message", "{last_message}", "-"],
        home_env="CODEX_HOME", mcp_format="toml",
        seed_files={os.path.expanduser("~/.codex/auth.json"): "auth.json"}),
    "claude": RuntimeConfig(
        name="claude",
        command=["claude", "-p", "--output-format", "json", "--permission-mode", "bypassPermissions",
                 "--mcp-config", "{mcp_config}", "--strict-mcp-config", "--add-dir", "{cwd}"],
        home_env="CLAUDE_CONFIG_DIR", mcp_format="json", seed_files={}),
    "fake": RuntimeConfig(
        name="fake",
        command=[sys.executable, str(Path(__file__).resolve().parents[1] / "tests" / "fake_cli.py"), "{last_message}"],
        home_env="CODEX_HOME", mcp_format="toml", seed_files={}),
}


def _toml_value(value):
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{k} = {_toml_value(v)}" for k, v in value.items()) + " }"
    raise ValueError("unsupported MCP config value")


def write_mcp_config(home, mcp_format, servers):
    if mcp_format == "toml":
        lines = []
        for name, server in servers.items():
            lines.append(f"[mcp_servers.{name}]")
            lines.extend(f"{key} = {_toml_value(value)}" for key, value in server.items())
            lines.append("")
        path = home / "config.toml"
        path.write_text("\n".join(lines), encoding="utf-8")
        return path
    path = home / "mcp.json"
    path.write_text(json.dumps({"mcpServers": servers}, indent=2), encoding="utf-8")
    return path


class Launcher:
    def __init__(self, runs_root, runtime, host, clock=time.time):
        self.runs_root = Path(runs_root)
        self.runtime = runtime
        self.host = host
        self.clock = clock
        self._handles = {}
        self._stopping = {}

    def running(self):
        return dict(self._handles)

    @staticmethod
    def alive(pid):
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def spawn(self, item_id, message, mcp_servers, budget_seconds, cwd, extra_env=None):
        if item_id in self._handles:
            raise RuntimeError(f"worker already running for {item_id}")
        run_dir = self.runs_root / item_id / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(self.clock()))
        home = run_dir / "home"
        home.mkdir(parents=True, exist_ok=True)
        for source, relative in self.runtime.seed_files.items():
            destination = home / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if Path(source).is_file():
                shutil.copy2(source, destination)
        mcp_config = write_mcp_config(home, self.runtime.mcp_format, mcp_servers)
        last_message = run_dir / "last_message.txt"
        (run_dir / "prompt.md").write_text(message, encoding="utf-8")
        command = [part.format(cwd=str(cwd), last_message=str(last_message), mcp_config=str(mcp_config), home=str(home))
                   for part in self.runtime.command]
        env = {k: v for k, v in os.environ.items() if k not in ("CODEX_HOME", "CLAUDE_CONFIG_DIR")}
        env[self.runtime.home_env] = str(home)
        env["FARMBOT_ITEM_ID"] = item_id
        env.update(extra_env or {})
        stdout = open(run_dir / "stdout.log", "w", encoding="utf-8")
        stderr = open(run_dir / "stderr.log", "w", encoding="utf-8")
        kwargs = {"start_new_session": True} if os.name != "nt" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        try:
            process = subprocess.Popen(command, cwd=str(cwd), env=env, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                                       text=True, encoding="utf-8", **kwargs)
        finally:
            stdout.close()
            stderr.close()
        try:
            process.stdin.write(message)
        except (BrokenPipeError, OSError) as exc:
            (run_dir / "stdin-error.txt").write_text(f"{type(exc).__name__}: prompt not fully delivered\n", encoding="utf-8")
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass
        handle = Handle(item_id, process.pid, self.clock(), self.clock() + budget_seconds, run_dir, process, last_message)
        self._handles[item_id] = handle
        return handle

    @staticmethod
    def descendants(pid):
        """Pids below pid at this moment, deepest last. Empty on Windows, where taskkill /T walks the tree."""
        if os.name == "nt":
            return []
        try:
            out = subprocess.run(["ps", "-eo", "pid=,ppid="], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.TimeoutExpired):
            return []
        children = {}
        for line in out.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                children.setdefault(int(parts[1]), []).append(int(parts[0]))
        result, stack = [], [pid]
        while stack:
            for child in children.get(stack.pop(), []):
                result.append(child)
                stack.append(child)
        return result

    @staticmethod
    def _command_line(pid):
        try:
            if os.name == "nt":
                out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                      f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').CommandLine"],
                                     capture_output=True, text=True, timeout=10).stdout
            else:
                out = subprocess.run(["ps", "-o", "command=", "-p", str(int(pid))], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return ""
        return out or ""

    def owned_pid(self, pid, item_id):
        """True only when pid is alive and its command line names this work item's paths.

        Every runtime receives an item-scoped path on its command line (the worktree for codex
        and claude, the run directory for the fake), so the item id is a reliable marker. An
        unverifiable command line yields False: never kill what was not verified.
        """
        if not pid or not item_id or not self.alive(pid):
            return False
        return item_id in self._command_line(pid)

    def kill_pid(self, pid, grace=5.0):
        """Terminate a worker this launcher no longer tracks (after a restart) plus its descendants."""
        targets = [pid] + self.descendants(pid)
        for target in targets:
            self._signal_pid(target, signal.SIGTERM)
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline and any(self.alive(p) for p in targets):
            time.sleep(0.05)
        for target in targets:
            if self.alive(target):
                self._signal_pid(target, signal.SIGKILL)
        return targets

    @staticmethod
    def _signal_pid(pid, sig):
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    def _kill(self, handle, grace):
        process = handle.process
        if process.poll() is not None:
            return
        survivors = self.descendants(process.pid)
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/PID", str(process.pid)], capture_output=True)
            else:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True)
                else:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            process.wait(timeout=grace)
        # Children detached into their own sessions are not reached by the group signal.
        for pid in survivors:
            if self.alive(pid):
                self._signal_pid(pid, signal.SIGTERM)
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline and any(self.alive(pid) for pid in survivors):
            time.sleep(0.05)
        for pid in survivors:
            if self.alive(pid):
                self._signal_pid(pid, signal.SIGKILL)
        (handle.run_dir / "killed.json").write_text(json.dumps({"pid": process.pid, "descendants": survivors}), encoding="utf-8")

    def stop(self, item_id, grace=5.0):
        handle = self._handles.get(item_id)
        if handle is None:
            return False
        self._stopping[item_id] = "stopped"
        self._kill(handle, grace)
        return True

    def _read_last_message(self, handle):
        if handle.last_message_path.exists():
            return handle.last_message_path.read_text(encoding="utf-8").strip()
        if self.runtime.name == "claude":
            try:
                data = json.loads((handle.run_dir / "stdout.log").read_text(encoding="utf-8"))
                return str(data.get("result", "")).strip()
            except (OSError, ValueError):
                return ""
        return ""

    def poll(self):
        finished = []
        for item_id, handle in list(self._handles.items()):
            if handle.process.poll() is None and self.clock() >= handle.deadline and item_id not in self._stopping:
                self._stopping[item_id] = "budget"
                self._kill(handle, grace=5.0)
            code = handle.process.poll()
            if code is None:
                continue
            reason = self._stopping.pop(item_id, "exited")
            finished.append(Finished(item_id, code, self._read_last_message(handle), reason != "exited", reason))
            del self._handles[item_id]
        return finished
