"""Age out regenerable worker data, never a whole home, job or evidence directory.

The service owns the runs root and serializes each batch with Launcher.spawn.
No ledger is opened and no process is signalled. Uncertain teardown fails closed.
"""
from contextlib import contextmanager, ExitStack
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time

from .runtime_retention import (
    RetentionHeld, _ordinary, _proof, _record, _locked_file, _pin_directories, _pin_directory,
)

DEFAULTS = {'enabled': True, 'keep_hours': 24, 'sweep_seconds': 3600}
# No generic bin/obj, global cache, module source, Unity Library or home deletion.
CACHE_ROOTS = ('home/cache/remote_plugin_catalog', 'home/cache/codex_apps_tools', 'home/plugins/cache',
               'disposable/go-cache', 'disposable/go-tmp', 'disposable/build')
# Earlier Scheduler versions put Go's worker caches here. No other job paths
# are inferred from names, file extensions or disk size.
JOB_CACHE_ROOTS = ('server-tests/go-cache', 'server-tests/tmp')
FILES_PER_BATCH = 500


def settings(block):
    if not isinstance(block, dict) or set(block) - set(DEFAULTS):
        raise ValueError('worker_retention accepts enabled, keep_hours and sweep_seconds only')
    result = {**DEFAULTS, **block}
    if type(result['enabled']) is not bool:
        raise ValueError('worker_retention.enabled must be a boolean')
    for name in ('keep_hours', 'sweep_seconds'):
        value = result[name]
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError('worker_retention.' + name + ' must be positive and finite')
    return result


@contextmanager
def _posix_directory(path=None, *, parent=None, name=None):
    """Open without following links; subsequent traversal stays relative to this fd."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    if path is not None:
        with ExitStack() as stack:
            descriptor = None
            for part in Path(path).parts:
                descriptor = stack.enter_context(_posix_directory(parent=descriptor, name=part))
            yield descriptor
    else:
        descriptor = os.open(name, flags, dir_fd=parent)
        try:
            yield descriptor
        finally:
            os.close(descriptor)


def _posix_record(directory, name):
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RetentionHeld('unsafe_evidence')
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise RetentionHeld('oversized_evidence')
    record = json.loads(raw)
    if not isinstance(record, dict):
        raise RetentionHeld('invalid_evidence')
    return record, hashlib.sha256(raw).hexdigest(), info


def _posix_proof(directory):
    from .launcher import Launcher
    process, process_hash, _ = _posix_record(directory, 'process.json')
    killed, killed_hash, info = _posix_record(directory, 'killed.json')
    pid = process.get('pid')
    if (type(pid) is not int or pid <= 0 or 'windows_job' in process
            or killed.get('pid') != pid or killed.get('posix_session') != pid
            or killed.get('exited') is not True or killed.get('empty') is not True
            or killed.get('descendants') != []):
        raise RetentionHeld('unverified_teardown')
    # Never signal old numeric identities. PID reuse conservatively holds cleanup.
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        pass
    except OSError:
        raise RetentionHeld('unknown_process_state') from None
    else:
        raise RetentionHeld('active_or_unknown_session')
    if Launcher.session_members(pid) != [] or not Launcher._group_gone(pid, timeout=0):
        raise RetentionHeld('active_or_unknown_session')
    return {'process_sha256': process_hash, 'teardown_sha256': killed_hash}, info.st_mtime


@contextmanager
def _attempt(path):
    """Pin evidence and the selected attempt while yielding its teardown proof."""
    if os.name == 'nt':
        from .windows_job import WindowsJob
        with _pin_directories(path), ExitStack() as stack:
            for name in ('process.json', 'killed.json'):
                stack.enter_context(_locked_file(path / name))
            proof = _proof(path, WindowsJob.empty)
            yield None, proof, (path / 'killed.json').stat().st_mtime
    else:
        with _posix_directory(path) as descriptor:
            proof, ended = _posix_proof(descriptor)
            yield descriptor, proof, ended


def _job_quiescent(job, stop):
    """A resumed worker can write older attempts in this job, so check every attempt."""
    latest = 0
    for child in job.iterdir():
        if stop():
            raise RetentionHeld('stopping')
        if not child.is_dir():
            continue
        # Only attempt records grant authority. Legacy job-level scratch is ignored.
        if (not (child / 'process.json').exists() and not (child / 'process.json').is_symlink()
                and not re.fullmatch(r'\d{8}T\d{6}Z-[0-9a-f]{12}', child.name)):
            continue
        with _attempt(child) as (_, _, ended):
            latest = max(latest, ended)
    return latest


class _Batch:
    def __init__(self, cutoff, limit, stop, apply):
        self.cutoff, self.limit, self.stop, self.apply = cutoff, limit, stop, apply
        self.files = self.bytes = 0
        self.partial = False

    def full(self):
        self.partial = self.files >= self.limit or self.stop()
        return self.partial

    def eligible(self, info):
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise RetentionHeld('unsafe_cache_file')
        return max(info.st_mtime, info.st_ctime) <= self.cutoff

    def count(self, info):
        self.files += 1
        self.bytes += info.st_size


def _windows_tree(root, batch, *, keep=False):
    with _pin_directory(root):
        with os.scandir(root) as entries:
            for entry in entries:
                if batch.full():
                    return
                path = root / entry.name
                info = path.lstat()
                if stat.S_ISDIR(info.st_mode):
                    _windows_tree(path, batch)
                elif batch.eligible(info):
                    with _locked_file(path, deletion=batch.apply) as (stream, remove):
                        info = os.fstat(stream.fileno())
                        if batch.eligible(info):
                            if batch.apply:
                                remove()
                            batch.count(info)
    if batch.apply and not keep:
        # Empty ordinary directories only, never recursive deletion or an ACL repair.
        try:
            root.rmdir()
        except OSError:
            pass


def _posix_tree(directory, batch):
    with os.scandir(directory) as entries:
        for entry in entries:
            if batch.full():
                return
            info = os.stat(entry.name, dir_fd=directory, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                with _posix_directory(parent=directory, name=entry.name) as child:
                    identity = os.fstat(child)
                    _posix_tree(child, batch)
                current = os.stat(entry.name, dir_fd=directory, follow_symlinks=False)
                if (identity.st_dev, identity.st_ino) != (current.st_dev, current.st_ino):
                    raise RetentionHeld('cache_identity_changed')
                if batch.apply:
                    try:
                        os.rmdir(entry.name, dir_fd=directory)
                    except OSError:
                        pass
            elif batch.eligible(info):
                descriptor = os.open(entry.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                try:
                    opened = os.fstat(descriptor)
                    current = os.stat(entry.name, dir_fd=directory, follow_symlinks=False)
                    if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino) or current != info:
                        raise RetentionHeld('cache_identity_changed')
                    if batch.eligible(opened):
                        if batch.apply:
                            os.unlink(entry.name, dir_fd=directory)
                        batch.count(opened)
                finally:
                    os.close(descriptor)


def _receipt(attempt, descriptor, evidence):
    from .launcher import _write_worker_file
    name = 'cache-retention.json'
    text = json.dumps(evidence, sort_keys=True)
    if descriptor is None:
        target = attempt / name
        if target.exists() or target.is_symlink():
            _ordinary(target)
        _write_worker_file(target, text, sync=True)
    else:
        # /dev/fd paths are not portable to macOS. Write and rename through the pinned fd.
        from uuid import uuid4
        temporary = '.retention-' + uuid4().hex
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=descriptor)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, name, src_dir_fd=descriptor, dst_dir_fd=descriptor)
        finally:
            try:
                os.unlink(temporary, dir_fd=descriptor)
            except FileNotFoundError:
                pass


def _previous_totals(attempt, descriptor, proof):
    try:
        previous = (_record(attempt / 'cache-retention.json')[0] if descriptor is None
                    else _posix_record(descriptor, 'cache-retention.json')[0])
    except FileNotFoundError:
        return {'total_files': 0, 'total_bytes': 0}
    totals = {name: previous.get(name, 0) for name in ('total_files', 'total_bytes')}
    if (previous.get('version') != 1 or any(previous.get(key) != value for key, value in proof.items())
            or any(type(value) is not int or value < 0 for value in totals.values())):
        raise RetentionHeld('invalid_retention_receipt')
    return totals


def retire_attempt(runs_root, attempt, *, keep_hours=24, now=None, apply=False,
                   limit=FILES_PER_BATCH, stop=lambda: False):
    """One bounded batch; receipt precedes mutation and records partial failures.

    Calls from the service must hold the launcher's retention_lock. Callers must
    select the controller-owned root, never a worker-supplied path.
    """
    now = time.time() if now is None else now
    batch = _Batch(now - keep_hours * 3600, limit, stop, apply)
    result = {'status': 'held', 'files': 0, 'bytes': 0}
    try:
        runs_root, attempt = Path(runs_root), Path(attempt)
        if (not runs_root.is_absolute() or not attempt.is_absolute() or '..' in attempt.parts
                or '..' in runs_root.parts or attempt.parent.parent != runs_root):
            raise RetentionHeld('outside_runs_root')
        latest = _job_quiescent(attempt.parent, stop)
        with _attempt(attempt) as (descriptor, proof, ended), ExitStack() as parents:
            if ended > batch.cutoff:
                return {**result, 'status': 'young'}
            roots = [(attempt, descriptor, relative) for relative in CACHE_ROOTS]
            job_roots = JOB_CACHE_ROOTS if latest <= batch.cutoff else ()
            uploads = None
            if job_roots:
                job_descriptor = (None if descriptor is None else
                                  parents.enter_context(_posix_directory(attempt.parent)))
                roots.extend((attempt.parent, job_descriptor, relative) for relative in job_roots)
                from .upload_retention import upload_plan
                uploads = parents.enter_context(upload_plan(attempt.parent, job_descriptor, batch))
            evidence = {'version': 1, 'policy': 'disposable-worker-caches', 'checked_at': now,
                        'keep_hours': keep_hours, 'roots': list(CACHE_ROOTS), **proof,
                        'job_roots': list(job_roots),
                        'upload_cache': ({'root': 'uploads', 'manifest_sha256': uploads['sha256'],
                                          'candidates': len(uploads['targets'])} if uploads else None),
                        **_previous_totals(attempt, descriptor, proof),
                        'status': 'started', 'files': 0, 'bytes': 0}
            if apply:
                _receipt(attempt, descriptor, evidence)
            try:
                for base, base_descriptor, relative in roots:
                    if batch.full():
                        break
                    with ExitStack() as stack:
                        try:
                            if base_descriptor is None:
                                root = base
                                for part in relative.split('/'):
                                    root = root / part
                                    stack.enter_context(_pin_directory(root))
                            else:
                                child = base_descriptor
                                for part in relative.split('/'):
                                    child = stack.enter_context(_posix_directory(parent=child, name=part))
                        except FileNotFoundError:
                            continue  # Missing cache roots are normal; visit the rest.
                        if descriptor is None:
                            _windows_tree(root, batch, keep=True)
                        else:
                            _posix_tree(child, batch)
                if uploads and not batch.full():
                    from .upload_retention import retire_uploads
                    retire_uploads(uploads, batch)
            except Exception as exc:
                result.update(status='held', reason=str(exc) if isinstance(exc, RetentionHeld) else type(exc).__name__)
                raise
            else:
                result['status'] = 'partial' if batch.partial else 'complete'
            finally:
                result.update(files=batch.files, bytes=batch.bytes)
                if apply:
                    _receipt(attempt, descriptor, {**evidence, **result,
                             'total_files': evidence['total_files'] + batch.files,
                             'total_bytes': evidence['total_bytes'] + batch.bytes})
        return result
    except Exception as exc:
        return {**result, 'status': 'held', 'files': batch.files, 'bytes': batch.bytes,
                'reason': str(exc) if isinstance(exc, RetentionHeld) else type(exc).__name__}


class Controller:
    """Incremental backlog sweep in its own service thread; no scheduler/ledger lock."""
    def __init__(self, launcher, block, *, clock=time.time):
        self.launcher, self.policy, self.clock = launcher, settings(block), clock
        self._pending = None
        self._attempts = None
        self._next = 0

    def _scan(self, stop):
        root = self.launcher.runs_root.absolute()
        if not root.exists():
            return
        with os.scandir(root) as jobs:
            for job in jobs:
                if stop():
                    return
                try:
                    if not job.is_dir(follow_symlinks=False):
                        continue
                    _ordinary(Path(job.path), directory=True)
                    with os.scandir(job.path) as attempts:
                        for attempt in attempts:
                            if stop():
                                return
                            if attempt.is_dir(follow_symlinks=False) and (Path(attempt.path) / 'process.json').exists():
                                yield Path(attempt.path)
                except (OSError, RetentionHeld) as exc:
                    # An unreadable/removed job must not starve every later job.
                    print(json.dumps({'event': 'worker_cache_retention', 'item_id': job.name, 'status': 'held',
                                      'reason': str(exc) if isinstance(exc, RetentionHeld) else type(exc).__name__}), flush=True)

    def tick(self, stop=lambda: False):
        if not self.policy['enabled'] or stop() or self.clock() < self._next:
            return False
        if self._attempts is None:
            self._attempts = self._scan(stop)
        attempt = self._pending or next(self._attempts, None)
        if attempt is None:
            self.close()
            self._next = self.clock() + self.policy['sweep_seconds']
            return False
        self._pending = None
        # A same-job resume cannot create a new attempt during the proof/delete interval.
        with self.launcher.retention_lock:
            if attempt.parent.name in self.launcher.running():
                return True
            result = retire_attempt(self.launcher.runs_root.absolute(), attempt, apply=True,
                                    keep_hours=self.policy['keep_hours'], now=self.clock(), stop=stop)
        if result['status'] == 'partial':
            self._pending = attempt
        if result['files'] or result['status'] == 'held':
            print(json.dumps({'event': 'worker_cache_retention', 'item_id': attempt.parent.name,
                              'attempt': attempt.name, **result}), flush=True)
        return True

    def close(self):
        if self._attempts is not None:
            self._attempts.close()
        self._attempts = self._pending = None
