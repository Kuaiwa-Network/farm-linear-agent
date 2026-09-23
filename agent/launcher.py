"""Spawn, watch and kill one headless CLI worker per work item (spec §8)."""
from collections import namedtuple
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

RuntimeConfig = namedtuple("RuntimeConfig", "name command home_env mcp_format seed_files writable_flag",
                           defaults=(None,))
Handle = namedtuple("Handle", "item_id pid started_at deadline run_dir process last_message_path")
Finished = namedtuple("Finished", "item_id returncode last_message killed reason failure_kind worker_pid", defaults=(None, None))
Unsandboxed = namedtuple("Unsandboxed", "returncode timed_out seconds")
# Seeing a POSIX worker's exit without reaping it needs waitid(WNOWAIT), which CPython exposes on macOS
# only from 3.13. Without it no self-exit evidence is made and such attempts keep holding their cleanup.
_PINNED_EXIT = os.name != "nt" and all(hasattr(os, name) for name in ("waitid", "P_PID", "WEXITED", "WNOHANG",
                                                                         "WNOWAIT"))

RUNTIMES = {
    "codex": RuntimeConfig(
        name="codex",
        command=["codex", "exec", "-c", "features.memories=false", "--cd", "{cwd}", "--approve-for-me", "--skip-git-repo-check",
                 "--output-last-message", "{last_message}", "-"],
        home_env="CODEX_HOME", mcp_format="toml",
        seed_files={os.path.expanduser("~/.codex/auth.json"): "auth.json"}),
    "claude": RuntimeConfig(
        name="claude",
        command=["claude", "-p", "--output-format", "json", "--permission-mode", "bypassPermissions",
                 "--mcp-config", "{mcp_config}", "--strict-mcp-config", "--add-dir", "{cwd}"],
        home_env="CLAUDE_CONFIG_DIR", mcp_format="json", seed_files={}, writable_flag="--add-dir"),
    "fake": RuntimeConfig(
        name="fake",
        command=[sys.executable, str(Path(__file__).resolve().parents[1] / "tests" / "fake_cli.py"), "{last_message}"],
        home_env="CODEX_HOME", mcp_format="toml", seed_files={}),
}


def _toml_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value)
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

    def __init__(self, runs_root, runtime, host, clock=time.time):
        self.runs_root = Path(runs_root)
        self.runtime = runtime
        self.host = host
        self.clock = clock
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
              model_settings=None):
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
        # worker also writes its other worktrees, the ledger and its state dir, and talks to Linear and GitHub.
        roots = [str(self.state_dir(item_id)), *(str(path) for path in writable)]
        settings = {"sandbox_workspace_write": {"writable_roots": roots, "network_access": True}}
        if self.runtime.name == "codex":
            settings["features"] = {"memories": False}
        root_settings = {}
        if self.runtime.name == "codex":
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
                process_record.write_text(json.dumps(record), encoding="utf-8")
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
            (run_dir / "stdin-error.txt").write_text(f"{type(exc).__name__}: prompt not fully delivered\n", encoding="utf-8")
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

        env=None on purpose: the Editor inherits the service's own environment, including the real HOME, and
        Task 0 Steps 3 and 6 measured licensing resolving under exactly that — foreground and under launchd.
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
                process = subprocess.Popen([str(part) for part in argv], cwd=str(cwd), env=env,
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
        del self._settling[handle.item_id]
        # Members were proved gone through the pinned session, so they are not re-checked by pid later.
        record = {"pid": leader, "descendants": [], "terminated": sorted(state["terminated"]),
                  "posix_session": leader, "exited": True}
        if members is None:
            reason = "process table unreadable"
        elif members:
            reason = "session members survived SIGKILL"
        elif not self._group_gone(leader):
            reason = "process group still present after its leader was reaped"
        else:
            (handle.run_dir / "killed.json").write_text(json.dumps({**record, "empty": True}), encoding="utf-8")
            return True
        # Recovery evidence for the operator; assert_quiescent reads only killed.json and keeps holding.
        (handle.run_dir / "teardown-unverified.json").write_text(
            json.dumps({**record, "empty": False, "remaining": members, "reason": reason}), encoding="utf-8")
        return True

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
                    if json.loads(p.read_text(encoding='utf-8')).get('pid') == pid]
        if len(attempts) != 1 or not self.owned_pid(pid, item_id):
            raise RuntimeError('worker attempt ownership is ambiguous')
        return self.kill_pid(pid, evidence=attempts[0].parent / 'killed.json')

    def kill_pid(self, pid, grace=5.0, *, evidence=None):
        """Terminate a worker this launcher no longer tracks (after a restart) plus its descendants."""
        targets = [pid] + self.descendants(pid)
        retained = set(targets)
        if evidence is not None:
            if evidence.exists():
                previous = json.loads(evidence.read_text(encoding='utf-8'))
                if previous.get('pid') != pid:
                    raise RuntimeError('teardown evidence belongs to a different attempt')
                retained.update(previous['descendants'])
            # assert_quiescent checks that every recorded target is dead. Save
            # before the kill so a crash after teardown cannot lose the targets.
            temporary = evidence.with_suffix('.tmp')
            with temporary.open('w', encoding='utf-8') as stream:
                json.dump({'pid': pid, 'descendants': sorted(retained - {pid})}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(evidence)
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
        """Terminate a live worker claimed in `_killing`; nothing else reaps it until that claim is released."""
        process = handle.process
        survivors = self.descendants(process.pid)
        (handle.run_dir / "killed.json").write_text(json.dumps({"pid": process.pid, "descendants": survivors}), encoding="utf-8")
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
        (handle.run_dir / "killed.json").write_text(json.dumps({"pid": process.pid, "descendants": survivors}), encoding="utf-8")

    def _finish_job(self, handle):
        with self._job_lock:
            job = self._jobs.get(handle.item_id)
            if job is None:
                return
            job.terminate_and_wait()
            # Evidence is written only after Windows reports zero active members.
            (handle.run_dir / "killed.json").write_text(json.dumps({
                "pid": handle.pid, "descendants": [], "windows_job": job.name, "empty": True}), encoding="utf-8")
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
            attempt = json.loads(path.read_text(encoding="utf-8"))
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
        attempts = [(path, json.loads(path.read_text(encoding="utf-8"))) for path in root.glob("*/process.json")]
        for path, attempt in attempts:
            if attempt.get("state") == "not_started":
                continue
            if not attempt.get("pid"):
                raise RuntimeError("incomplete launch identity; operator investigation required")
            # Teardown evidence belongs to an attempt, never every occurrence of its PID.
            # In particular, a contained retry cannot certify an older uncontained run.
            if attempt["pid"] not in contained:
                killed_path = path.parent / "killed.json"
                killed = json.loads(killed_path.read_text(encoding="utf-8")) if killed_path.exists() else {}
                unique = sum(other.get("pid") == attempt["pid"] for _, other in attempts) == 1
                if killed.get("pid") != attempt["pid"] and not (unique and attempt["pid"] in recorded_processes):
                    raise RuntimeError("worker exited without verified descendant teardown; operator investigation required")
            pids.add(attempt["pid"])
        # Job identity is independent of PID reuse; never signal a new owner of an old PID.
        pids -= contained
        verified = set(recorded_processes) | contained
        for path in root.glob("*/killed.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
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
        if handle.last_message_path.exists():
            return handle.last_message_path.read_text(encoding="utf-8").strip()
        if self.runtime.name == "claude":
            try:
                data = json.loads((handle.run_dir / "stdout.log").read_text(encoding="utf-8"))
                return str(data.get("result", "")).strip()
            except (OSError, ValueError):
                return ""
        return ""

    def _failure_kind(self, handle, code, reason):
        """Classify the CLI's final diagnostic, never arbitrary tool output earlier in its log."""
        if self.runtime.name != "codex" or code == 0 or reason != "exited":
            return None
        try:
            with (handle.run_dir / "stderr.log").open("rb") as log:
                log.seek(0, 2)
                log.seek(max(0, log.tell() - 4096))
                tail = log.read().decode("utf-8", errors="replace")
        except OSError:
            return None
        lines = [line.strip() for line in tail.splitlines() if line.strip()]
        if len(lines) >= 2 and lines[-2] == "tokens used" and lines[-1].replace(",", "").isdigit():
            lines = lines[:-2]
        if lines and lines[-1] == "ERROR: Selected model is at capacity. Please try a different model.":
            return "model_capacity"
        return None

    def poll(self):
        finished = []
        for item_id, handle in list(self._handles.items()):
            if not self.exited(handle.process) and self.clock() >= handle.deadline and item_id not in self._stopping:
                self._stopping[item_id] = "budget"
                self._kill(handle, grace=5.0)
            if not self.exited(handle.process):
                continue
            if item_id in self._jobs:
                self._finish_job(handle)
            elif os.name != "nt":
                with self._teardown_lock:
                    if item_id in self._killing:
                        continue  # Stop's _kill reaps it and writes killed.json; report it after that.
                    settle = self._pinned(handle.process)
                if settle and not self._settle_exited(handle):
                    continue  # Retried on a later poll; the unreaped worker keeps its session pinned.
            code = handle.process.poll()
            if code is None:
                continue
            reason = self._stopping.pop(item_id, "exited")
            finished.append(Finished(item_id, code, self._read_last_message(handle), reason != "exited", reason,
                                     self._failure_kind(handle, code, reason), handle.pid))
            del self._handles[item_id]
        return finished
