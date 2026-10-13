"""Bounded host Go/Git processes; no worker command or environment is accepted."""
import os
from pathlib import Path
import signal
import subprocess
import time


def _drain_posix(process):
    # The leader is still unreaped, so its process-group ID cannot be reused.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        # macOS can report EPERM for a group containing only zombies. This is
        # not evidence of quiescence: reap our child, then require ESRCH below.
        pass
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
    deadline = time.monotonic() + 5
    while True:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            pass  # A denied probe proves neither absence nor quiescence.
        if time.monotonic() >= deadline:
            raise RuntimeError('Go preparation process group did not drain; preserve evidence')
        time.sleep(.05)


def run(argv, *, cwd, env, log, cancelled=lambda: False, timeout=600):
    """Drain owned descendants before allowing cache publication, including Stop."""
    job = process = None
    stdout, stderr = Path(str(log) + '.out'), Path(str(log) + '.err')
    with stdout.open('wb') as out, stderr.open('wb') as err:
        try:
            if cancelled():
                raise RuntimeError('Go dependency preparation cancelled')
            options = {'start_new_session': True}
            if os.name == 'nt':
                from .windows_job import CREATE_SUSPENDED, WindowsJob
                job = WindowsJob()
                options = {'creationflags': CREATE_SUSPENDED | subprocess.CREATE_NO_WINDOW}
            process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                       stdout=out, stderr=err, **options)
            if job is not None:
                job.assign(process)
                job.resume(process)
            deadline = time.monotonic() + timeout
            code = None
            while code is None:
                if os.name == 'nt':
                    code = process.poll()
                else:
                    # Keep the group leader unreaped until its group is drained;
                    # otherwise a reused PID could identify an unrelated group.
                    info = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                    if info is not None:
                        code = info.si_status if info.si_code == os.CLD_EXITED else -info.si_status
                if code is not None:
                    break
                if cancelled() or time.monotonic() >= deadline:
                    raise RuntimeError('Go dependency preparation cancelled or timed out')
                time.sleep(.1)
            if code:
                raise RuntimeError('Go dependency command failed; retained host diagnostics')
        finally:
            if job is not None:
                try:
                    job.terminate_and_wait()
                finally:
                    job.close()
            elif process is not None:
                _drain_posix(process)
            if process is not None and os.name == 'nt':
                # Assignment failure still owns this suspended creation object.
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
    if stdout.stat().st_size > 16 << 20:
        raise RuntimeError('Go dependency output exceeded host limit')
    return stdout.read_bytes()
