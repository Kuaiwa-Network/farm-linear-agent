"""Spawn, watch and kill one headless CLI worker per work item (spec §8)."""
from collections import namedtuple
import io
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

from .kw_ops import child_environment

RuntimeConfig = namedtuple("RuntimeConfig", "name command home_env mcp_format seed_files writable_flag",
                           defaults=(None,))
Handle = namedtuple("Handle", "item_id pid started_at deadline run_dir process last_message_path")
Finished = namedtuple("Finished", "item_id returncode last_message killed reason failure_kind worker_pid", defaults=(None, None))
Unsandboxed = namedtuple("Unsandboxed", "returncode timed_out seconds")
# Seeing a POSIX worker's exit without reaping it needs waitid(WNOWAIT), which CPython exposes on macOS
# only from 3.13. Without it no self-exit evidence is made and such attempts keep holding their cleanup.
# A killed.json that is unreadable, malformed or names another pid cannot be merged, only held.
_UNUSABLE_RECORD = "earlier teardown record is unreadable or not this attempt's"
_PINNED_EXIT = os.name != "nt" and all(hasattr(os, name) for name in ("waitid", "P_PID", "WEXITED", "WNOHANG",
                                                                         "WNOWAIT"))
# The most read from a whole file that a worker can replace; stderr.log is read only from its tail.
_WORKER_FILE_LIMIT = 1 << 20
# Opening a FIFO waits for a writer, and an exited worker leaves none. Nothing FarmBot or a CLI writes in the
# state directory is a symlink. Windows has neither flag nor FIFOs in a directory, and needs O_BINARY for bytes.
_WORKER_FILE_FLAGS = (os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
                      | getattr(os, "O_BINARY", 0))


def _read_worker_file(path, tail=None):
    """The bytes of a file in a worker-writable directory, or its last `tail` bytes, read without blocking.

    Raises OSError for anything but a regular file (on POSIX, for a symlink too), and without `tail` for one
    larger than _WORKER_FILE_LIMIT: a FIFO, a device or an endless file is refused instead of waited on or read.
    """
    fd = os.open(path, _WORKER_FILE_FLAGS)
    try:
        status = os.fstat(fd)
        if not stat.S_ISREG(status.st_mode):
            raise OSError(f"{Path(path).name} is not a regular file")
        with open(fd, "rb", closefd=False) as stream:
            if tail is not None:
                stream.seek(max(0, status.st_size - tail))
                return stream.read(tail)
            data = stream.read(_WORKER_FILE_LIMIT + 1)
    finally:
        os.close(fd)
    if len(data) > _WORKER_FILE_LIMIT:
        raise OSError(f"{Path(path).name} is larger than {_WORKER_FILE_LIMIT} bytes")
    return data


def _read_worker_text(path):
    """_read_worker_file decoded as Path.read_text(encoding="utf-8") decodes: strict UTF-8, universal newlines."""
    return io.TextIOWrapper(io.BytesIO(_read_worker_file(path)), encoding="utf-8").read()


def _write_worker_file(path, text, *, mode=0o666, sync=False):
    """Put a file holding `text` at `path`, in a worker-writable directory, without opening what is there.

    A FIFO the worker left at `path` would make the open wait for a reader, and a symlink would take the write
    outside the sandbox. The text goes to a new file under an unguessable name, which O_EXCL refuses to open
    through a link or FIFO, and os.replace then swaps it in for whatever is at `path`. `sync` flushes it to disk
    first. It is written as Path.write_text(text, encoding="utf-8") writes it.
    """
    path = Path(path)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), mode)
    try:
        with open(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            if sync:
                stream.flush()
                os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


RUNTIMES = {
    "codex": RuntimeConfig(
        name="codex",
        # --approve-for-me rejects --sandbox; the isolated home selects workspace-write.
        command=["codex", "exec", "-c", "features.memories=false", "--cd", "{cwd}",
                 "--approve-for-me", "--skip-git-repo-check",
                 "--output-last-message", "{last_message}", "-"],
        home_env="CODEX_HOME", mcp_format="toml",
        seed_files={os.path.expanduser("~/.codex/auth.json"): "auth.json"}),
    # `claude -p` skips the workspace trust dialog and loads the cwd's .claude/settings.json and
    # settings.local.json: measured, their hooks and apiKeyHelper run and their env applies, so an
    # ANTHROPIC_BASE_URL they set received the worker's requests and OAuth token. That cwd is a repository or the
    # job's state directory, which the worker writes and every attempt shares. `--setting-sources user` keeps
    # only the isolated CLAUDE_CONFIG_DIR and also stops the cwd's CLAUDE.md being injected;
    # --strict-mcp-config keeps only the injected MCP servers.
    "claude": RuntimeConfig(
        name="claude",
        command=["claude", "-p", "--output-format", "json", "--permission-mode", "bypassPermissions",
                 "--mcp-config", "{mcp_config}", "--strict-mcp-config", "--setting-sources", "user",
                 "--add-dir", "{cwd}"],
        home_env="CLAUDE_CONFIG_DIR", mcp_format="json", seed_files={}, writable_flag="--add-dir"),
    "fake": RuntimeConfig(
        name="fake",
        command=[sys.executable, str(Path(__file__).resolve().parents[1] / "tests" / "fake_cli.py"), "{last_message}"],
        home_env="CODEX_HOME", mcp_format="toml", seed_files={}),
}


def _toml_string(value):
    """A TOML basic string. JSON's escapes are TOML's, except that JSON writes a character beyond U+FFFF as a
    surrogate pair, which TOML rejects: one emoji in a path would make the whole worker config unreadable."""
    return '"' + "".join(json.dumps(char)[1:-1] if ord(char) <= 0xFFFF else f"\\U{ord(char):08X}"
                         for char in value) + '"'


def _toml_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{k} = {_toml_value(v)}" for k, v in value.items()) + " }"
    raise ValueError("unsupported MCP config value")


def write_mcp_config(home, mcp_format, servers, settings=None, root_settings=None):
    """The worker's home config: runtime settings tables first, then one table per injected MCP server."""
    if mcp_format == "toml":
        lines = [f"{key} = {_toml_value(value)}" for key, value in (root_settings or {}).items()]
        for table, values in (settings or {}).items():
            lines.append(f"[{table}]")
            lines.extend(f"{key} = {_toml_value(value)}" for key, value in values.items())
            lines.append("")
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
    # SIGTERM grace for members of an exited POSIX worker's session, before SIGKILL.
    exit_grace = 5.0
    # How long an unreadable process table is retried while the exited worker stays unreaped.
    settle_retry_seconds = 60.0

    def __init__(self, runs_root, runtime, host, clock=time.time, token_env=None):
        self.runs_root = Path(runs_root)
        self.runtime = runtime
        self.host = host
        self.clock = clock
        self.token_env = token_env
        self._handles = {}
        self._stopping = {}
        self._jobs = {}
        self._job_lock = threading.RLock()
        # Items whose live POSIX worker `_kill` has claimed: until it has reaped the worker and written
        # killed.json, poll() neither reaps nor reports it.
        self._killing = set()
        self._teardown_lock = threading.Lock()
        self._settling = {}
        # Touched from two threads at once: run_unsandboxed registers from the pool thread while
        # Scheduler.stop and serve()'s shutdown read from theirs.
        self._unsandboxed = {}
        self._unsandboxed_lock = threading.Lock()
        self._cancelled_runs = set()
        self._shutdown = False

    def running(self):
        return dict(self._handles)

    @staticmethod
    def alive(pid):
        if os.name == "nt":
            from .windows_job import alive
            return alive(pid)
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def state_dir(self, item_id):
        """The one directory outside its worktrees a worker may write: its runs, token and logs."""
        return self.runs_root / item_id

    def spawn(self, item_id, message, mcp_servers, budget_seconds, cwd, extra_env=None, writable=(), cancelled=None,
              model_settings=None, withheld_env=()):
        # A fresh worker is also a new attempt after an operator retry. Stop fences from
        # its previous attempt must not prevent this worker requesting another batch.
        with self._unsandboxed_lock:
            self._cancelled_runs.discard(item_id)
        if item_id in self._handles:
            raise RuntimeError(f"worker already running for {item_id}")
        run_dir = self.state_dir(item_id) / (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(self.clock())) + "-" + uuid4().hex[:12])
        home = run_dir / "home"
        home.mkdir(parents=True, exist_ok=True)
        process_record = run_dir / "process.json"
        process_record.write_text(json.dumps({"state": "preparing"}), encoding="utf-8")
        for source, relative in self.runtime.seed_files.items():
            destination = home / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if Path(source).is_file():
                shutil.copy2(source, destination)
        # Codex's workspace-write sandbox allows only the cwd and temp dirs and no network by default; the
        # worker also writes only its active repository worktree, the ledger and its state dir,
        # and talks to Linear and GitHub.
        roots = [str(self.state_dir(item_id)), *(str(path) for path in writable)]
        settings = {"sandbox_workspace_write": {"writable_roots": roots, "network_access": True}}
        if self.runtime.name == "codex":
            settings["features"] = {"memories": False}
            # codex exec records `trust_level = "trusted"` here for a cwd it has no decision for, then loads the
            # repository's own .codex/config.toml: measured, its MCP servers start and send any inline
            # credentials; per Codex's trust prompt, project hooks and exec policies load too. The literal --cd
            # spelling is the key exec was measured to honour; the resolved one covers canonical lookups.
            # Distrusted, the cwd's AGENTS.md is no longer injected, so the --approve-for-me reviewer stops
            # counting it as trusted text; fix workers still read it, as tool output.
            for path in dict.fromkeys((str(cwd), str(Path(cwd).resolve()))):
                settings[f"projects.{_toml_string(path)}"] = {"trust_level": "untrusted"}
            # A server the CLI authenticates from an environment variable names it, never its value, and the
            # worker's shell must not see it either. codex-cli 0.156.1 re-exports variables from its shell
            # snapshot, so `exclude` holds only with the snapshot off (measured).
            secrets = sorted({server["bearer_token_env_var"] for server in mcp_servers.values()
                              if "bearer_token_env_var" in server})
            if secrets:
                settings["features"]["shell_snapshot"] = False
                settings["shell_environment_policy"] = {"exclude": secrets}
        root_settings = {}
        if self.runtime.name == "codex":
            root_settings["sandbox_mode"] = "workspace-write"
            for key, target in (("model", "model"), ("reasoning_effort", "model_reasoning_effort")):
                if key in (model_settings or {}):
                    root_settings[target] = model_settings[key]
        mcp_config = write_mcp_config(home, self.runtime.mcp_format, mcp_servers, settings, root_settings)
        last_message = run_dir / "last_message.txt"
        (run_dir / "prompt.md").write_text(message, encoding="utf-8")
        command = [part.format(cwd=str(cwd), last_message=str(last_message), mcp_config=str(mcp_config), home=str(home))
                   for part in self.runtime.command]
        if self.runtime.writable_flag:
            # Codex takes its roots from the isolated home's config; Claude takes them on the command line.
            for root in roots:
                command += [self.runtime.writable_flag, root]
        env = {k: v for k, v in os.environ.items() if k not in ("CODEX_HOME", "CLAUDE_CONFIG_DIR")}
        env[self.runtime.home_env] = str(home)
        env["FARMBOT_ITEM_ID"] = item_id
        env.update(extra_env or {})
        if self.runtime.name == "claude":
            env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
        # Apply withholding last: per-worker overrides must not put a denied secret back.
        for name in withheld_env:
            env.pop(name, None)
        stdout = open(run_dir / "stdout.log", "w", encoding="utf-8")
        stderr = open(run_dir / "stderr.log", "w", encoding="utf-8")
        kwargs = {"start_new_session": True} if os.name != "nt" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        job = None
        process = None
        try:
            if os.name == "nt":
                from .windows_job import WindowsJob
                job = WindowsJob()
                command = [sys.executable, "-I", str(Path(__file__).with_name("windows_worker_gate.py")),
                           str(run_dir), *command]
            with self._unsandboxed_lock:
                if self._shutdown or item_id in self._cancelled_runs or (cancelled and cancelled()):
                    process_record.write_text(json.dumps({"state": "not_started"}), encoding="utf-8")
                    raise RuntimeError("launch cancelled before process creation")
                # A previous sandbox principal may own a restrictive Windows ACL on this file.
                # Archive the now-obsolete claim so the fresh worker can create its own token file;
                # never reuse a claim or alter the separate reservation token.
                previous_token = self.state_dir(item_id) / "token"
                if previous_token.is_symlink():
                    raise RuntimeError("claim token must not be a symlink")
                if previous_token.exists():
                    previous_token.rename(run_dir / "previous-claim.token")
                process = subprocess.Popen(command, cwd=str(cwd), env=env, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                                           text=True, encoding="utf-8", **kwargs)
                if job is not None:
                    try:
                        job.assign(process)
                    except BaseException:
                        process.kill()
                        process.wait()
                        process.stdin.close()
                        process_record.write_text(json.dumps({"state": "not_started"}), encoding="utf-8")
                        raise
                handle = Handle(item_id, process.pid, self.clock(), self.clock() + budget_seconds, run_dir, process, last_message)
                self._handles[item_id] = handle
                record = {"pid": process.pid}
                if job is not None:
                    self._jobs[item_id] = job
                    record["windows_job"] = job.name
                # The worker is running and may already have replaced this file.
                _write_worker_file(process_record, json.dumps(record))
        except BaseException:
            self._handles.pop(item_id, None)
            self._jobs.pop(item_id, None)
            if job is not None:
                job.close()
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                process.stdin.close()
            raise
        finally:
            stdout.close()
            stderr.close()
        try:
            if job is not None:
                process.stdin.write("G")
            process.stdin.write(message)
        except (BrokenPipeError, OSError) as exc:
            _write_worker_file(run_dir / "stdin-error.txt", f"{type(exc).__name__}: prompt not fully delivered\n")
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass
        return handle

    def run_unsandboxed(self, argv, *, cwd, timeout, log=None, env=None, owner=None, cancelled=None):
        """Run one process OUTSIDE the worker seatbelt. This is the only method in FarmBot that does.

        Task 0's sandbox addendum: `Unity -batchmode -runTests` under [sandbox_workspace_write] hangs for
        ever — 25 minutes at 0.0% CPU, log frozen at 2,289 bytes, no results file — because the seatbelt
        denies a Mach lookup for com.apple.hiservices-xpcservice and the Editor blocks instead of exiting.
        There were zero file-permission denials, and Mach service access is not expressible through
        sandbox_workspace_write, so no writable_roots entry can fix it. The same command unsandboxed exits 2
        in 19 s. Hence: the launcher runs the Editor, the worker never does.

        The argv is the caller's, composed from configuration — never anything a worker supplied. Nothing
        here writes an isolated home, a CODEX_HOME or a sandbox table; this is a plain subprocess, and the
        deadline exists because the thing it runs is the thing that was watched hang.

        The Editor receives the service's environment, including the real HOME needed for licensing, with
        only the configured kw_ops token removed. Task 0 Steps 3 and 6 measured licensing resolving under
        the service environment — foreground and under launchd.
        """
        start = time.monotonic()
        handle = open(log, "w", encoding="utf-8") if log else subprocess.DEVNULL
        kwargs = ({"start_new_session": True} if os.name != "nt"
                  else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP})
        try:
            # Spawn and registration share the Stop lock. A cancellation before this
            # section prevents spawning; one after it always sees the registered child.
            with self._unsandboxed_lock:
                if (self._shutdown or owner in self._cancelled_runs
                        or (cancelled is not None and cancelled())):
                    return Unsandboxed(-signal.SIGTERM, False, time.monotonic() - start)
                process = subprocess.Popen([str(part) for part in argv], cwd=str(cwd),
                                           env=child_environment(self.token_env, env),
                                           stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT,
                                           **kwargs)
                if owner is not None:
                    self._unsandboxed[owner] = process
            # Registered under the item, because this process is the one thing in FarmBot that nothing else
            # can reach. The worker that asked for the run has already exited; the Editor is a direct child
            # of `serve`, not a descendant of any worker, so `Launcher.stop`'s handle lookup and its
            # `descendants` walk both miss it entirely. Without this line a Stop leaves a real Editor running
            # for the rest of `batch_timeout` on a cancelled item, and a `serve` shutdown orphans it holding
            # the slot folder.
            try:
                return Unsandboxed(process.wait(timeout=timeout), False, time.monotonic() - start)
            except subprocess.TimeoutExpired:
                # Take the group, not the pid: a hung Editor has children, and leaving them holding the slot
                # folder is what turns a gap into a held slot. `start_new_session=True` above made this pid a
                # process-group leader precisely so this call can exist; `kill_pid` walks `ps` for
                # descendants, which misses anything Unity re-parented on its way down.
                #
                # `reap=process`: this thread is the only one that can wait() on the child, so liveness here
                # has to be asked of the Popen and not of `alive(pid)` — an unreaped child is a zombie, and
                # os.kill(zombie, 0) succeeds, which would spin kill_group's whole grace on every timeout and
                # then leave the corpse for the interpreter to warn about.
                self.kill_group(process.pid, reap=process)
                return Unsandboxed(None, True, time.monotonic() - start)
        finally:
            if owner is not None:
                with self._unsandboxed_lock:
                    self._unsandboxed.pop(owner, None)
            if log:
                handle.close()

    def kill_group(self, pid, grace=5.0, reap=None):
        """SIGTERM the whole process group, then SIGKILL what is left.

        `kill_pid` exists beside this and is the wrong tool here: it walks `ps` for descendants, which sees
        only processes still parented to `pid`. A Unity Editor that is wedged — the case Task 0 measured,
        25 minutes at 0.0% CPU — leaves children that may have been re-parented, and those are exactly the
        ones still holding the slot folder open. Every process this method exists to kill was started by
        `run_unsandboxed` with `start_new_session=True`, so the pid is its own group leader and the group is
        the honest unit. Mirrors the killpg pair `stop()` already uses.

        `reap` is the Popen when the caller is the thread that owns the wait; without it the child becomes an
        unreaped zombie whose pid still answers `os.kill(pid, 0)`, so `alive` would never go False.
        """
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(int(pid))], capture_output=True)
            if reap is not None:
                try:
                    reap.wait(timeout=grace)
                except subprocess.TimeoutExpired:
                    pass
            return
        try:
            group = os.getpgid(pid)
        except (ProcessLookupError, PermissionError):
            return
        def gone():
            if reap is not None:
                reap.poll()
            try:
                os.killpg(group, 0)
            except ProcessLookupError:
                return True
            except PermissionError:
                pass
            return False
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(group, sig)
            except (ProcessLookupError, PermissionError):
                return
            deadline = time.monotonic() + (grace if sig is signal.SIGTERM else 1.0)
            while time.monotonic() < deadline:
                if gone():
                    return
                time.sleep(0.05)

    def stop_unsandboxed(self, owner):
        """Kill the unsandboxed run registered for this item, if one is in flight. Returns True if it was.

        The reachability fix. `stop()` resolves `self._handles`, which `spawn` populates — and the batch
        Editor never went through `spawn`. So Stop, service shutdown and `recover-slot` all had no way to
        touch it, while `SlotPool.run_batch` sat in `process.wait(timeout=batch_timeout)` for up to half an
        hour on the pool thread. Both callers reach it through here.
        """
        with self._unsandboxed_lock:
            self._cancelled_runs.add(owner)
            process = self._unsandboxed.get(owner)
        if process is None or process.poll() is not None:
            return False
        # reap=None deliberately: the thread blocked in run_unsandboxed's own wait() owns this child and is
        # the one that reaps it, after which `alive(pid)` goes False. Polling it from here would race that
        # wait for the same waitpid.
        self.kill_group(process.pid)
        return True

    def stop_all_unsandboxed(self):
        """Every in-flight unsandboxed run, for service shutdown. The pool thread is a daemon, so without
        this an Editor outlives the process that started it and keeps the slot folder open across a
        restart — which the next `ensure` then reads as a slot another Editor already holds."""
        with self._unsandboxed_lock:
            self._shutdown = True
            owners = list(self._unsandboxed)
        return [owner for owner in owners if self.stop_unsandboxed(owner)]

    @staticmethod
    def descendants(pid):
        """Pids below pid at this moment; diagnostics alone cannot prove an exited tree is empty."""
        if os.name == "nt":
            from .windows_job import descendants
            return descendants(pid)
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
    def exited(process):
        """Whether a worker has exited, without reaping it on POSIX.

        An unreaped worker keeps its pid allocated, and with it the IDs of the session and process group it
        leads, so nothing new can take them while its session is inspected. Only poll(), or the `_kill` that
        claimed the worker, reaps it.
        """
        if process.returncode is not None or not _PINNED_EXIT:
            return process.poll() is not None
        try:
            return os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
        except ChildProcessError:
            return True  # Already reaped by the thread holding Popen's waitpid lock.

    @staticmethod
    def _pinned(process):
        """True while an exited worker is still this process's unreaped child: its session is still ours."""
        if process.returncode is not None or not _PINNED_EXIT:
            return False
        try:
            return os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
        except ChildProcessError:
            return False

    def _wait_exited(self, process, timeout):
        deadline = time.monotonic() + timeout
        while not self.exited(process):
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.02)
        return True

    @staticmethod
    def session_members(leader):
        """Live processes other than `leader` in its process group or session, from one `ps` snapshot.

        None when the snapshot cannot be trusted: a table that does not list this process proves nothing.
        Zombies have already exited. A process that called setsid() is in neither and is not reported.
        """
        try:
            out = subprocess.run(["ps", "-A", "-o", "pid=,pgid=,stat="], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return None
        rows = []
        for line in out.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit():
                rows.append((int(parts[0]), int(parts[1]), parts[2]))
        if out.returncode != 0 or os.getpid() not in {pid for pid, _, _ in rows}:
            return None
        members = []
        for pid, pgid, stat in rows:
            if pid == leader or stat.startswith("Z"):
                continue
            if pgid != leader:
                # A job-control shell gives each job its own group inside the worker's session.
                try:
                    if os.getsid(pid) != leader:
                        continue
                except ProcessLookupError:
                    continue  # exited since the snapshot
                except OSError:
                    return None
            members.append(pid)
        return members

    @staticmethod
    def _signal_session(leader, members, sig):
        """Signal a pinned worker's process group, then each member still verified in its session."""
        try:
            os.killpg(leader, sig)
        except (ProcessLookupError, PermissionError):
            pass
        for pid in members:
            try:
                if os.getsid(pid) == leader:
                    os.kill(pid, sig)
            except (ProcessLookupError, PermissionError):
                pass

    def _terminate_session(self, leader, members):
        """SIGTERM, then SIGKILL, what remains of a pinned worker's session; returns every member seen."""
        seen = set(members)
        for sig, wait in ((signal.SIGTERM, self.exit_grace), (signal.SIGKILL, 1.0)):
            self._signal_session(leader, sorted(seen), sig)
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline:
                current = self.session_members(leader)
                seen.update(current or ())
                # A killed member is a zombie until launchd/init reaps it, and alive() still answers for it.
                if current == [] and not any(self.alive(pid) for pid in seen):
                    return sorted(seen)
                time.sleep(0.1)
        return sorted(seen)

    @staticmethod
    def _group_gone(leader, timeout=1.0):
        """True once no process at all has process group `leader` (checked after its leader is reaped)."""
        deadline = time.monotonic() + timeout
        while True:
            try:
                os.killpg(leader, 0)
            except ProcessLookupError:
                return True
            except PermissionError:
                pass  # macOS reports a group of zombies, or of processes we cannot signal, this way.
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.02)

    def _settle(self, handle):
        """_settle_exited, except that its failure holds cleanup and lets the exit be reported instead of
        raising, which would leave the exited worker for poll() to retry."""
        try:
            return self._settle_exited(handle)
        except Exception as exc:
            try:
                self._hold(handle, {"pid": handle.pid, "posix_session": handle.pid, "exited": True},
                           f"teardown check failed: {type(exc).__name__}")
            except OSError:
                pass
            return True

    def _settle_exited(self, handle):
        """Teardown evidence for a POSIX worker that exited by itself, and its reap.

        spawn's start_new_session=True made the worker the leader of a session and a process group whose IDs
        both equal its pid, and a leader cannot move out of either. Until this reaps it, the exited worker
        still holds that pid, so every process now in its session or group descends from this attempt and
        may be signalled. Evidence is written only after the session scan is empty and, once the leader is
        reaped, the kernel reports no process in its group. A process that called setsid() left the session
        and is outside this proof: the documented POSIX limit.

        Returns False to be retried on a later poll with the worker still unreaped: when the process table
        cannot be read (for up to settle_retry_seconds) or the reap itself did not happen.
        """
        leader = handle.pid
        state = self._settling.setdefault(handle.item_id, {
            "until": time.monotonic() + self.settle_retry_seconds, "terminated": set()})
        members = self.session_members(leader)
        if members:
            state["terminated"].update(self._terminate_session(leader, members))
            members = self.session_members(leader)
        if members is None and time.monotonic() < state["until"]:
            return False
        if handle.process.poll() is None:
            return False  # Another thread holds Popen's waitpid lock; the worker is not reaped yet.
        # Every member seen and signalled that is still alive (it left the session, or is not yet reaped)
        # stays recorded and checked by pid, as do the descendants an interrupted Stop recorded. Members seen
        # gone were proved so through the pinned session and are not re-checked later.
        escaped = {pid for pid in state["terminated"] if self.alive(pid)}
        prior = self._prior_descendants(handle)
        record = {"pid": leader, "descendants": sorted(escaped | set(prior or ())),
                  "terminated": sorted(state["terminated"] - escaped), "posix_session": leader, "exited": True}
        if prior is None:
            reason = _UNUSABLE_RECORD
        elif members is None:
            reason = "process table unreadable"
        elif members:
            reason = "session members survived SIGKILL"
        elif not self._group_gone(leader):
            reason = "process group still present after its leader was reaped"
        else:
            _write_worker_file(handle.run_dir / "killed.json", json.dumps({**record, "empty": True}))
            return True
        self._hold(handle, {**record, "remaining": members}, reason)
        return True

    @staticmethod
    def _hold(handle, record, reason):
        """Recovery evidence for the operator; assert_quiescent reads only killed.json and keeps holding.

        An earlier killed.json is kept aside as killed.superseded.json, never left to certify what this
        could not verify.
        """
        stale = handle.run_dir / "killed.json"
        if stale.exists():
            stale.replace(handle.run_dir / "killed.superseded.json")
        _write_worker_file(handle.run_dir / "teardown-unverified.json",
                           json.dumps({**record, "empty": False, "reason": reason}))

    @staticmethod
    def _prior_descendants(handle):
        """What an earlier (interrupted) kill of this attempt recorded: [] when there is no record, None when
        the record is not verifiably this attempt's own list of pids. The file is in the worker-writable state
        directory, so reading it must never raise (a huge number or deep nesting would) or block (a FIFO would)."""
        path = handle.run_dir / "killed.json"
        if not path.exists():
            return []
        try:
            prior = json.loads(_read_worker_text(path))
        except Exception:
            return None
        if not isinstance(prior, dict) or prior.get("pid") != handle.pid:
            return None
        descendants = prior.get("descendants", [])
        if not isinstance(descendants, list) or not all(type(pid) is int and pid > 0 for pid in descendants):
            return None
        return descendants

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

    def kill_owned_attempt(self, item_id, pid):
        """Persist the exact attempt's teardown targets before signalling them."""
        attempts = [p for p in self.state_dir(item_id).glob('*/process.json')
                    if json.loads(_read_worker_text(p)).get('pid') == pid]
        if len(attempts) != 1 or not self.owned_pid(pid, item_id):
            raise RuntimeError('worker attempt ownership is ambiguous')
        return self.kill_pid(pid, evidence=attempts[0].parent / 'killed.json')

    def kill_pid(self, pid, grace=5.0, *, evidence=None):
        """Terminate a worker this launcher no longer tracks (after a restart) plus its descendants."""
        targets = [pid] + self.descendants(pid)
        retained = set(targets)
        if evidence is not None:
            if evidence.exists():
                previous = json.loads(_read_worker_text(evidence))
                if previous.get('pid') != pid:
                    raise RuntimeError('teardown evidence belongs to a different attempt')
                retained.update(previous['descendants'])
            # assert_quiescent checks that every recorded target is dead. Save
            # before the kill so a crash after teardown cannot lose the targets.
            _write_worker_file(evidence, json.dumps({'pid': pid, 'descendants': sorted(retained - {pid})}), sync=True)
        for target in targets:
            self._signal_pid(target, signal.SIGTERM)
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline and any(self.alive(p) for p in targets):
            time.sleep(0.05)
        for target in targets:
            if self.alive(target):
                self._signal_pid(target, signal.SIGKILL)
        # Only signal the presently owned process tree. Orphaned targets from a
        # prior scan stay in the proof and block reuse while alive; PID reuse
        # means that an old numeric PID alone never authorizes a new signal.
        return sorted(retained)

    @staticmethod
    def _signal_pid(pid, sig):
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    def _kill(self, handle, grace):
        process = handle.process
        if handle.item_id in self._jobs:
            self._finish_job(handle)
            process.wait(timeout=max(grace, 1))
            return
        with self._teardown_lock:
            if handle.item_id in self._killing or self.exited(process):
                # Another Stop is already killing it, or it exited on its own and poll() records that
                # session: reaping it here would free the pid that pins the session first.
                return
            self._killing.add(handle.item_id)
        try:
            self._kill_claimed(handle, grace)
        finally:
            with self._teardown_lock:
                self._killing.discard(handle.item_id)

    def _kill_claimed(self, handle, grace):
        """Terminate a live worker claimed in `_killing`; poll() leaves it alone until the claim is released."""
        process = handle.process
        # Stop repeats each tick on a stuck worker and every walk starts afresh. What an earlier Stop recorded
        # stays in the proof, but is not signalled: an old numeric pid alone never authorizes a signal.
        prior = self._prior_descendants(handle)
        survivors = self.descendants(process.pid)
        self._record_kill(handle, survivors, prior)
        # On POSIX the pid is also the group ID (start_new_session=True) and stays ours until the reap below.
        # Not os.getpgid(): macOS refuses it for a worker that has just become a zombie.
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/PID", str(process.pid)], capture_output=True)
            else:
                os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        exited = self._wait_exited(process, grace)
        if not exited:
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True)
                else:
                    os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            exited = self._wait_exited(process, grace)
        if exited and os.name != "nt" and self._pinned(process):
            # Still unreaped, so its session is still pinned: sweep it as a self-exit is swept. The group
            # signal does not wait for members, and a SIGTERM-ignoring member outlives it.
            members = self.session_members(process.pid)
            if members:
                survivors = sorted(set(survivors) | set(self._terminate_session(process.pid, members)))
        process.wait(timeout=grace if exited else 0)
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
        self._record_kill(handle, survivors, prior)

    def _record_kill(self, handle, survivors, prior):
        record = {"pid": handle.pid, "descendants": sorted(set(survivors) | set(prior or ()))}
        if prior is None:
            self._hold(handle, record, _UNUSABLE_RECORD)
        else:
            _write_worker_file(handle.run_dir / "killed.json", json.dumps(record))

    def _finish_job(self, handle):
        with self._job_lock:
            job = self._jobs.get(handle.item_id)
            if job is None:
                return
            job.terminate_and_wait()
            # Evidence is written only after Windows reports zero active members.
            _write_worker_file(handle.run_dir / "killed.json", json.dumps({
                "pid": handle.pid, "descendants": [], "windows_job": job.name, "empty": True}))
            job.close()
            del self._jobs[handle.item_id]

    def certified_pids(self, item_id, boot_proof=None):
        """Identities proved ended by containment/boot, regardless of numeric PID reuse."""
        if os.name != "nt":
            return set()
        from .windows_job import WindowsJob
        boot, certified = None, set()
        if boot_proof:
            from .cleanup_recovery import windows_boot_time
            if (boot_proof.get("host") != self.host
                    or abs(windows_boot_time() - boot_proof["boot_time"]) > .001):
                raise RuntimeError("cleanup boot evidence does not match this host boot")
            boot = boot_proof["boot_time"]
            certified.update(boot_proof["processes"])
        jobs, unverified = set(), set()
        for path in self.state_dir(item_id).glob("*/process.json"):
            attempt = json.loads(_read_worker_text(path))
            if attempt.get("state") == "not_started":
                continue
            proved = boot is not None and path.stat().st_mtime < boot and attempt.get("pid") in certified
            if attempt.get("windows_job"):
                if not WindowsJob.empty(attempt["windows_job"]):
                    raise RuntimeError("Windows worker job still has active processes")
                jobs.add(attempt["pid"])
                proved = True
            if not proved:
                unverified.add(attempt.get("pid"))
        return (certified | jobs) - unverified

    def assert_quiescent(self, item_id, pid, recorded_processes=(), boot_proof=None):
        """Dead parents do not prove detached children died. Missing evidence holds cleanup."""
        root = self.state_dir(item_id)
        pids = {pid} if pid else set()
        contained = self.certified_pids(item_id, boot_proof)
        attempts = [(path, json.loads(_read_worker_text(path))) for path in root.glob("*/process.json")]
        for path, attempt in attempts:
            if attempt.get("state") == "not_started":
                continue
            if not attempt.get("pid"):
                raise RuntimeError("incomplete launch identity; operator investigation required")
            # Teardown evidence belongs to an attempt, never every occurrence of its PID.
            # In particular, a contained retry cannot certify an older uncontained run.
            if attempt["pid"] not in contained:
                killed_path = path.parent / "killed.json"
                killed = json.loads(_read_worker_text(killed_path)) if killed_path.exists() else {}
                unique = sum(other.get("pid") == attempt["pid"] for _, other in attempts) == 1
                if killed.get("pid") != attempt["pid"] and not (unique and attempt["pid"] in recorded_processes):
                    raise RuntimeError("worker exited without verified descendant teardown; operator investigation required")
            pids.add(attempt["pid"])
        # Job identity is independent of PID reuse; never signal a new owner of an old PID.
        pids -= contained
        verified = set(recorded_processes) | contained
        for path in root.glob("*/killed.json"):
            data = json.loads(_read_worker_text(path))
            verified.add(data["pid"])
            if any(self.alive(child) for child in data["descendants"] if child not in contained):
                raise RuntimeError("worker descendants have not exited")
        if any(self.alive(process) for process in (pids | set(recorded_processes)) - contained):
            raise RuntimeError("worker processes have not exited")
        if pids - verified:
            raise RuntimeError("worker exited without verified descendant teardown; operator investigation required")

    def stop(self, item_id, grace=5.0):
        handle = self._handles.get(item_id)
        if handle is None:
            return False
        self._stopping[item_id] = "stopped"
        self._kill(handle, grace)
        return True

    def _read_last_message(self, handle):
        """Best-effort: the report files are in the worker's writable state directory, and a read that raised
        would fail every poll of this reaped worker, keeping it registered and holding its slot until restart."""
        try:
            if handle.last_message_path.exists():
                return _read_worker_text(handle.last_message_path).strip()
        except (OSError, ValueError):
            return ""
        if self.runtime.name == "claude":
            try:
                data = json.loads(_read_worker_text(handle.run_dir / "stdout.log"))
                return str(data.get("result", "")).strip()
            except (OSError, ValueError):
                return ""
        return ""

    def _failure_kind(self, handle, code, reason):
        """Classify the CLI's final diagnostic, never arbitrary tool output earlier in its log."""
        if self.runtime.name != "codex" or code == 0 or reason != "exited":
            return None
        try:
            tail = _read_worker_file(handle.run_dir / "stderr.log", tail=4096).decode("utf-8", errors="replace")
        except OSError:
            return None
        lines = [line.strip() for line in tail.splitlines() if line.strip()]
        if len(lines) >= 2 and lines[-2] == "tokens used" and lines[-1].replace(",", "").isdigit():
            lines = lines[:-2]
        if lines and lines[-1] == "ERROR: Selected model is at capacity. Please try a different model.":
            return "model_capacity"
        return None

    def poll(self):
        """Finished records of the workers done since the last poll, each removed from the handles.

        A failure while processing one worker never escapes: that would discard the records of workers this
        poll has already removed, and the scheduler would hold their slots for ever. The failing worker stays
        registered with its state intact and is processed again by the next poll; nothing here writes
        teardown evidence for it.
        """
        finished = []
        for item_id, handle in list(self._handles.items()):
            try:
                record = self._poll_one(item_id, handle)
            except Exception as exc:
                try:
                    print(json.dumps({"event": "worker_poll_error", "item_id": item_id, "error": type(exc).__name__}),
                          flush=True)
                except (OSError, ValueError):
                    pass
                continue
            if record is not None:
                finished.append(record)
        return finished

    def _poll_one(self, item_id, handle):
        """The worker's Finished record, once it is done and its handle removed; None while it is not done."""
        with self._teardown_lock:
            if item_id in self._killing:
                return None  # Stop's _kill reaps it and writes killed.json; report it after that.
        if not self.exited(handle.process) and self.clock() >= handle.deadline and item_id not in self._stopping:
            self._stopping[item_id] = "budget"
            self._kill(handle, grace=5.0)
        if not self.exited(handle.process):
            return None
        if item_id in self._jobs:
            self._finish_job(handle)
        elif os.name != "nt":
            with self._teardown_lock:
                if item_id in self._killing:
                    return None  # Stop's _kill reaps it and writes killed.json; report it after that.
                settle = self._pinned(handle.process)
            if settle and not self._settle(handle):
                return None  # Retried on a later poll; the unreaped worker keeps its session pinned.
        code = handle.process.poll()
        if code is None:
            return None
        reason = self._stopping.get(item_id, "exited")
        record = Finished(item_id, code, self._read_last_message(handle), reason != "exited", reason,
                          self._failure_kind(handle, code, reason), handle.pid)
        # Nothing below raises: the handle and its state go only with a complete record in hand.
        self._settling.pop(item_id, None)
        self._stopping.pop(item_id, None)
        del self._handles[item_id]
        return record
