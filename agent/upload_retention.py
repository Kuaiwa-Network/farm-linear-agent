"""Retire only unchanged ZIP downloads and members in the fixed job uploads cache.

The caller supplies verified job quiescence, the grace period and a pinned job
directory. The worker-written manifest is untrusted data, never path authority.
"""
from contextlib import contextmanager, ExitStack
import hashlib
import json
import os
import re
import stat

from .runtime_retention import RetentionHeld, _locked_file, _pin_directory
from .uploads import MANIFEST, MANIFEST_FORMAT, MANIFEST_LIMIT, _key, _usable


def _stamp(info):
    # Reading may update atime; content/identity changes must still fail closed.
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _targets(raw):
    data = json.loads(raw)
    if (not isinstance(data, dict) or data.get('format') != MANIFEST_FORMAT
            or not isinstance(data.get('files'), list)):
        raise RetentionHeld('invalid_upload_manifest')
    records, names = [], set()
    for entry in data['files']:
        if not _usable(entry):
            raise RetentionHeld('invalid_upload_manifest_entry')
        name = entry['name']
        if name:
            key = _key(name)
            if key in names or key == _key(MANIFEST):
                raise RetentionHeld('ambiguous_upload_manifest')
            names.add(key)
        if entry['status'] == 'ok':
            if (not name or not re.fullmatch('[0-9a-f]{64}', entry['sha256'])
                    or entry['size'] < 0):
                raise RetentionHeld('invalid_upload_digest')
            records.append(entry)
    archives = {e['name']: e for e in records if e['source_zip'] is None and
                (e['name'].lower().endswith('.zip') or e['content_type'] in
                 ('application/zip', 'application/x-zip-compressed') or e['extract_dir'])}
    targets = dict(archives)
    for entry in records:
        if entry['source_zip'] is None:
            continue
        archive = archives.get(entry['source_zip'])
        if (not archive or not archive['extract_dir']
                or entry['url_path'] != archive['url_path']
                or not entry['name'].startswith(archive['extract_dir'] + '/')):
            raise RetentionHeld('invalid_upload_member_source')
        targets[entry['name']] = entry
    return targets


@contextmanager
def _posix_file(directory, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RetentionHeld('unsafe_upload_file')
        yield stream


@contextmanager
def upload_plan(job, descriptor, batch):
    """Pin the manifest through audit and deletion; missing caches are normal."""
    from .worker_retention import _posix_directory
    with ExitStack() as stack:
        root = job / 'uploads'
        try:
            if descriptor is None:
                stack.enter_context(_pin_directory(root))
                stream, _ = stack.enter_context(_locked_file(root / MANIFEST))
                directory = None
            else:
                directory = stack.enter_context(_posix_directory(parent=descriptor, name='uploads'))
                stream = stack.enter_context(_posix_file(directory, MANIFEST))
        except FileNotFoundError:
            yield None
            return
        info = os.fstat(stream.fileno())
        if not batch.eligible(info):
            yield None
            return
        raw = stream.read(MANIFEST_LIMIT + 1)
        if len(raw) > MANIFEST_LIMIT:
            raise RetentionHeld('oversized_upload_manifest')
        targets = _targets(raw)
        yield {'root': root, 'directory': directory, 'stream': stream, 'info': info,
               'targets': targets, 'sha256': hashlib.sha256(raw).hexdigest()}


def _unchanged(stream, entry, batch):
    info = os.fstat(stream.fileno())
    if not batch.eligible(info) or info.st_size != entry['size']:
        return False
    digest = hashlib.sha256()
    while block := stream.read(1024 * 1024):
        if batch.stop():
            raise RetentionHeld('stopping')
        digest.update(block)
    return digest.hexdigest() == entry['sha256'] and _stamp(os.fstat(stream.fileno())) == _stamp(info)


def retire_uploads(plan, batch):
    from .worker_retention import _posix_directory
    for name, entry in plan['targets'].items():
        if batch.full():
            return
        parts = name.split('/')  # _usable has validated each canonical component.
        with ExitStack() as stack:
            try:
                if plan['directory'] is None:
                    path = plan['root']
                    for part in parts[:-1]:
                        path = path / part
                        stack.enter_context(_pin_directory(path))
                    stream, remove = stack.enter_context(_locked_file(path / parts[-1], deletion=batch.apply))
                    if _unchanged(stream, entry, batch):
                        info = os.fstat(stream.fileno())
                        if batch.apply:
                            remove()
                        batch.count(info)
                else:
                    directory = plan['directory']
                    for part in parts[:-1]:
                        directory = stack.enter_context(_posix_directory(parent=directory, name=part))
                    stream = stack.enter_context(_posix_file(directory, parts[-1]))
                    if _unchanged(stream, entry, batch):
                        info = os.fstat(stream.fileno())
                        current = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
                        manifest = os.stat(MANIFEST, dir_fd=plan['directory'], follow_symlinks=False)
                        if (_stamp(current) != _stamp(info) or _stamp(manifest) != _stamp(plan['info'])
                                or _stamp(os.fstat(plan['stream'].fileno())) != _stamp(plan['info'])):
                            raise RetentionHeld('upload_identity_changed')
                        if batch.apply:
                            os.unlink(parts[-1], dir_fd=directory)
                        batch.count(info)
            except FileNotFoundError:
                continue  # Already expired entries remain in the durable manifest.
