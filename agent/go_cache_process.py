"""Bounded host Go/Git processes; no worker command or environment is accepted."""
import os
from pathlib import Path
import signal
import subprocess
import time


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
            while process.poll() is None:
                if cancelled() or time.monotonic() >= deadline:
                    raise RuntimeError('Go dependency preparation cancelled or timed out')
                time.sleep(.1)
            if process.returncode:
                raise RuntimeError('Go dependency command failed; retained host diagnostics')
        finally:
            if job is not None:
                try:
                    job.terminate_and_wait()
                finally:
                    job.close()
            elif process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process is not None:
                # Assignment failure still owns this suspended creation object.
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                if os.name != 'nt':
                    deadline = time.monotonic() + 5
                    while True:
                        try:
                            os.killpg(process.pid, 0)
                        except ProcessLookupError:
                            break
                        if time.monotonic() >= deadline:
                            raise RuntimeError('Go preparation process group did not drain; preserve evidence')
                        time.sleep(.05)
    if stdout.stat().st_size > 16 << 20:
        raise RuntimeError('Go dependency output exceeded host limit')
    return stdout.read_bytes()
