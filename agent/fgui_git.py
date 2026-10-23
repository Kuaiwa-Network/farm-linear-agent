"""Read-only source Git processes, with bounded Windows launcher cleanup."""
import os
import subprocess
import tempfile
import time

from .ledger import LedgerError


def run(command, *, cwd, env, data, timeout, limit):
    if os.name != "nt":
        return subprocess.run(command, cwd=cwd, env=env, input=data,
                              capture_output=True, timeout=timeout)

    # Git for Windows' cmd/git.exe launches the real Git executable. Killing
    # only that launcher leaves its child holding the captured pipes open.
    # Assign before startup, and prove teardown before a caller can retry.
    from .windows_job import CREATE_SUSPENDED, WindowsJob, active
    with tempfile.TemporaryFile() as stdin, tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        if data is not None:
            stdin.write(data)
        stdin.seek(0)
        job = WindowsJob(); process = None
        try:
            process = subprocess.Popen(command, cwd=cwd, env=env, stdin=stdin, stdout=stdout, stderr=stderr,
                                       creationflags=CREATE_SUSPENDED | subprocess.CREATE_NO_WINDOW)
            job.assign(process)
            job.resume(process)
            deadline = time.monotonic() + timeout

            def bounded():
                if os.fstat(stdout.fileno()).st_size > limit or os.fstat(stderr.fileno()).st_size > 4 << 20:
                    raise LedgerError("UI source Git read exceeded its bounded inventory")

            while process.poll() is None:
                bounded()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    process.wait(timeout=min(.1, remaining))
                except subprocess.TimeoutExpired:
                    pass
            # Parent exit can precede final Job accounting. Lingering children
            # are a failed read, never grounds to accept their partial output.
            drain = min(deadline, time.monotonic() + 1)
            while active(job.handle) and time.monotonic() < drain:
                bounded(); time.sleep(.02)
            if active(job.handle):
                raise LedgerError("UI source Git read left running descendants")
            bounded()
            stdout.seek(0); stderr.seek(0)
            return subprocess.CompletedProcess(command, process.returncode, stdout.read(limit + 1), stderr.read(4 << 20))
        finally:
            try:
                try:
                    if active(job.handle):
                        job.terminate_and_wait(timeout=5)
                finally:
                    try:
                        job.close()
                    finally:
                        if process is not None:
                            # Assignment failure still owns this suspended creation.
                            if process.poll() is None:
                                process.kill()
                            process.wait(timeout=5)
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                # In particular, this must not look like a retryable timeout.
                raise LedgerError(f"UI source Git process cleanup is uncertain; do not retry "
                                  f"(job={job.name}, pid={process.pid if process is not None else None})") from exc
