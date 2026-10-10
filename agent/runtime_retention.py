"""Reclaim only Codex's disposable Windows executable after certified teardown.

Never remove a worker home, credentials, logs, checkpoints or process evidence.
Windows handles pin every ancestor and the exact file throughout deletion.
"""
from contextlib import contextmanager, ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
from uuid import uuid4


class RetentionHeld(RuntimeError):
    pass


def _ordinary(path, directory=False):
    info = path.lstat()
    if (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
            or (not stat.S_ISDIR(info.st_mode) if directory else not stat.S_ISREG(info.st_mode))
            or (not directory and info.st_nlink != 1)):
        raise RetentionHeld('unsafe_path')
    return info


def _record(path):
    _ordinary(path)
    with path.open('rb') as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise RetentionHeld('oversized_evidence')
    data = json.loads(raw.decode('utf-8'))
    if not isinstance(data, dict):
        raise RetentionHeld('invalid_evidence')
    return data, hashlib.sha256(raw).hexdigest()


def _proof(attempt, empty):
    process, process_hash = _record(attempt / 'process.json')
    killed, killed_hash = _record(attempt / 'killed.json')
    pid, job = process.get('pid'), process.get('windows_job')
    if (type(pid) is not int or pid <= 0 or not isinstance(job, str)
            or not re.fullmatch(r'Local\\FarmBot-[0-9a-f]{32}', job)
            or killed.get('pid') != pid or killed.get('windows_job') != job
            or killed.get('empty') is not True or killed.get('descendants') != []):
        raise RetentionHeld('unverified_teardown')
    if not empty(job):
        raise RetentionHeld('active_job')
    return {'process_sha256': process_hash, 'teardown_sha256': killed_hash}


def _native():
    import ctypes as c
    from ctypes import wintypes as w
    kernel = c.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
    create.restype = w.HANDLE
    close = kernel.CloseHandle
    close.argtypes, close.restype = [w.HANDLE], w.BOOL
    disposition = kernel.SetFileInformationByHandle
    disposition.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
    disposition.restype = w.BOOL
    return c, w, create, close, disposition


@contextmanager
def _pin_directories(path):
    c, _, create, close, _ = _native()
    handles = []
    try:
        for parent in [*reversed(path.parents), path]:
            _ordinary(parent, directory=True)
            # Permit ordinary reads/writes, but deny rename/deletion of ancestors.
            handle = create(str(parent), 0x80, 3, None, 3, 0x02000000 | 0x00200000, None)
            if handle == c.c_void_p(-1).value:
                raise c.WinError(c.get_last_error())
            handles.append(handle)
            _ordinary(parent, directory=True)
        yield
    finally:
        for handle in reversed(handles):
            close(handle)


@contextmanager
def _locked_file(path, *, deletion=False):
    import msvcrt
    c, _, create, close, disposition = _native()
    before = _ordinary(path)
    # No FILE_SHARE_WRITE or FILE_SHARE_DELETE: pin the exact content and name.
    handle = create(str(path), 0x80000000 | (0x10000 if deletion else 0), 1,
                    None, 3, 0x00200000, None)
    if handle == c.c_void_p(-1).value:
        raise c.WinError(c.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close(handle)
        raise
    with os.fdopen(descriptor, 'rb') as stream:
        after = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino, before.st_size) != (after.st_dev, after.st_ino, after.st_size):
            raise RetentionHeld('file_identity_changed')
        _ordinary(path)

        def remove():
            delete = c.c_ubyte(1)  # FILE_DISPOSITION_INFO.DeleteFile (BOOLEAN)
            if not disposition(handle, 4, c.byref(delete), c.sizeof(delete)):
                raise c.WinError(c.get_last_error())
        yield stream, remove


def _digest(stream):
    stream.seek(0)
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b''):
        digest.update(block)
    return digest.hexdigest()


def _archive(stream, root, digest):
    """Store one verified public executable per hash; never copy a worker home."""
    destination = root / (digest + '.exe')
    with _pin_directories(root):
        if destination.exists() or destination.is_symlink():
            with _locked_file(destination) as (existing, _):
                if _digest(existing) != digest:
                    raise RetentionHeld('archive_hash_mismatch')
            return
        temporary = root / ('.pending-' + uuid4().hex + '.exe')
        # On failure preserve the source. No cleanup of arbitrary archive entries.
        with temporary.open('xb') as out:
            stream.seek(0)
            shutil.copyfileobj(stream, out, 1024 * 1024)
            out.flush()
            os.fsync(out.fileno())
        with _locked_file(temporary) as (copied, _):
            if _digest(copied) != digest:
                raise RetentionHeld('archive_copy_mismatch')
        # Windows rename refuses an existing destination rather than overwriting it.
        temporary.rename(destination)
        with _locked_file(destination) as (copied, _):
            if _digest(copied) != digest:
                raise RetentionHeld('archive_copy_mismatch')


def retire_codex_copy(runs_root, attempt, *, apply=False, archive_root=None):
    """Return a sanitized result. Missing/uncertain evidence always preserves data.

    The caller selects its owned runs root; an attempt must be exactly two levels
    below it. Standalone maintenance must also validate the instance marker.
    """
    if os.name != 'nt':
        return {'status': 'unsupported_platform'}
    deleted = False
    result = {'status': 'held'}
    try:
        runs_root, attempt = Path(runs_root), Path(attempt)
        if (not runs_root.is_absolute() or not attempt.is_absolute()
                or '..' in runs_root.parts or '..' in attempt.parts
                or attempt.parent.parent != runs_root):
            raise RetentionHeld('outside_runs_root')
        source = attempt / 'home' / '.sandbox-bin' / 'codex.exe'
        if not source.exists() and not source.is_symlink():
            return {'status': 'absent'}
        if archive_root is not None:
            archive_root = Path(archive_root)
            if (not archive_root.is_absolute() or '..' in archive_root.parts
                    or archive_root.is_relative_to(runs_root.parent)
                    or runs_root.is_relative_to(archive_root)):
                raise RetentionHeld('archive_must_be_separate')
        # Pin the whole ancestor chain, including the sandbox directory.
        with _pin_directories(source.parent), ExitStack() as stack:
            from .windows_job import WindowsJob
            # Keep both authoritative records immutable during the operation.
            for record in ('process.json', 'killed.json'):
                stack.enter_context(_locked_file(attempt / record))
            proof = _proof(attempt, WindowsJob.empty)
            with _locked_file(source, deletion=apply) as (stream, remove):
                digest = _digest(stream)
                result.update(bytes=os.fstat(stream.fileno()).st_size, sha256=digest)
                if not apply:
                    return {**result, 'status': 'eligible'}
                if archive_root is not None:
                    _archive(stream, archive_root, digest)
                receipt = attempt / 'runtime-retention.json'
                if receipt.exists() or receipt.is_symlink():
                    _ordinary(receipt)
                from .launcher import _write_worker_file
                evidence = {'version': 1, 'runtime': 'codex', **result, **proof,
                            'archived': archive_root is not None, 'removed': False}
                _write_worker_file(receipt, json.dumps(evidence, sort_keys=True), sync=True)
                # A second native query closes the interval spent copying/hashing.
                _proof(attempt, WindowsJob.empty)
                remove()
                deleted = True
            evidence.update(status='removed', removed=True)
            _write_worker_file(receipt, json.dumps(evidence, sort_keys=True), sync=True)
        return {**result, 'status': 'removed'}
    except Exception as exc:
        # Never leak host paths from OS errors. No stop/kill/ACL-repair fallback.
        return {**result, 'status': 'removed' if deleted else 'held',
                'reason': str(exc) if isinstance(exc, RetentionHeld) else type(exc).__name__}


def _instance(runs_root, expected_instance):
    """Read only the ownership marker, never config, credentials or a ledger."""
    if not runs_root.is_absolute() or '..' in runs_root.parts or runs_root.name != 'runs':
        raise RetentionHeld('explicit_owned_runs_root_required')
    marker, digest = _record(runs_root.parent / 'environment.json')
    if (marker.get('version') != 1 or marker.get('instance_id') != expected_instance
            or marker.get('environment') not in {'development', 'production', 'offline'}
            or marker.get('local_root') != str(runs_root.parent.resolve())):
        raise RetentionHeld('instance_marker_mismatch')
    return digest


def sweep(runs_root, expected_instance, *, apply=False, archive_root=None):
    """Explicit operator maintenance of marked state; it can coexist with workers.

    Each attempt has independent teardown authority. A held/active attempt does
    not prevent reclaiming other certified copies. No process is ever signalled.
    """
    if os.name != 'nt':
        raise RetentionHeld('native_windows_required')
    runs_root = Path(runs_root)
    with _pin_directories(runs_root):
        marker_hash = _instance(runs_root, expected_instance)
        if archive_root is not None:
            archive_root = Path(archive_root)
            if (not archive_root.is_absolute() or '..' in archive_root.parts
                    or archive_root.is_relative_to(runs_root.parent)
                    or runs_root.is_relative_to(archive_root)):
                raise RetentionHeld('archive_must_be_separate')
            with _pin_directories(archive_root):
                owner = archive_root / 'retention-owner.json'
                expected = {'version': 1, 'purpose': 'retired-codex-binaries',
                            'source_marker_sha256': marker_hash}
                if owner.exists() or owner.is_symlink():
                    if _record(owner)[0] != expected:
                        raise RetentionHeld('archive_owner_mismatch')
                elif apply:
                    if any(archive_root.iterdir()):
                        raise RetentionHeld('unmarked_archive_not_empty')
                    with owner.open('x', encoding='utf-8') as out:
                        json.dump(expected, out, sort_keys=True)
                        out.flush()
                        os.fsync(out.fileno())
        results = []
        for item in sorted(runs_root.iterdir()):
            try:
                _ordinary(item, directory=True)
                attempts = sorted(item.iterdir())
            except (OSError, RetentionHeld):
                continue
            for attempt in attempts:
                if not attempt.is_dir():
                    continue
                result = retire_codex_copy(runs_root, attempt, apply=apply, archive_root=archive_root)
                if result['status'] != 'absent':
                    results.append(result)
        counts = {status: sum(r['status'] == status for r in results)
                  for status in ('eligible', 'removed', 'held')}
        return {**counts, 'bytes': sum(r.get('bytes', 0) for r in results
                                      if r['status'] in {'eligible', 'removed'}), 'results': results}


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs-root', required=True, type=Path)
    parser.add_argument('--expected-instance', required=True)
    parser.add_argument('--archive-root', type=Path,
                        help='Existing separate ordinary directory; one verified executable per hash.')
    parser.add_argument('--apply', action='store_true', help='Default is read-only inventory.')
    args = parser.parse_args()
    try:
        result = sweep(args.runs_root, args.expected_instance, apply=args.apply, archive_root=args.archive_root)
        print(json.dumps({k: v for k, v in result.items() if k != 'results'}, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'held', 'reason': str(exc) if isinstance(exc, RetentionHeld)
                          else type(exc).__name__}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
