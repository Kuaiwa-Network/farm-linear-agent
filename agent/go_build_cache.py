"""Operator-published Go cache seeds, copied one way into private issue caches.

The controller never builds source or promotes worker-produced cache entries.
Publication is a host operation after an explicitly approved compile-only warmup.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from uuid import uuid4


SCHEMA = 'farmbot-go-build-cache-v1'
HEX = re.compile(r'[0-9a-f]{64}')
ENTRY = re.compile(r'([0-9a-f]{2})/([0-9a-f]{64})-[ad]')
MAX_MANIFEST = 32 << 20
MAX_FILES = 100000
MAX_BYTES = 16 << 30


def plain(path):
    path = Path(path).absolute()
    if '..' in path.parts or any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise ValueError('Go build cache paths must not contain links or parent traversal')
    return path


def regular(path):
    path = plain(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('Go build cache input must be an unlinked regular file')
    return info


def fingerprint(path):
    info = regular(path)
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    if stamp(info) != stamp(regular(path)):
        raise ValueError('Go build cache input changed during reading')
    return digest.hexdigest()


def stamp(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode, info.st_nlink


def worker_temp_roots():
    return [Path(p).resolve() for p in (tempfile.gettempdir(), '/tmp', '/var/tmp',
            *(os.environ.get(k) for k in ('TEMP', 'TMP', 'TMPDIR'))) if p and Path(p).is_absolute()]


def outside_state(path, local_root):
    if not Path(path).is_absolute():
        raise ValueError('Go build cache paths must be absolute')
    path = plain(path)
    for state in (Path(local_root).resolve(), *worker_temp_roots()):
        if path.is_relative_to(state) or state.is_relative_to(path):
            raise ValueError('Go build cache seed must be outside worker state/write roots')
    return path


def validate_settings(block, local_root, dependencies):
    if block == {}:
        return
    if (not isinstance(block, dict) or set(block) != {'manifest', 'sha256'}
            or not isinstance(block['manifest'], str) or not Path(block['manifest']).is_absolute()
            or not isinstance(block['sha256'], str) or not HEX.fullmatch(block['sha256'])):
        raise ValueError('go_build_cache requires an absolute manifest and its SHA256')
    if not dependencies:
        raise ValueError('go_build_cache requires pinned go_dependencies tools')
    root = outside_state(Path(block['manifest']).parent, local_root)
    for key in ('cache_root', 'work_root'):
        other = Path(dependencies[key]).resolve()
        if root.is_relative_to(other) or other.is_relative_to(root):
            raise ValueError('Go build seed must be separate from module/preparation storage')


def owner(config):
    return {'instance_id': config.instance_id, 'app_user_id': config.expected_app_user_id,
            'local_root': str(Path(config.local_root).resolve())}


def load(config, *, check_files=False):
    block = config.go_build_cache
    validate_settings(block, config.local_root, config.go_dependencies)
    if not block:
        return None
    path = plain(block['manifest'])
    if regular(path).st_size > MAX_MANIFEST or fingerprint(path) != block['sha256']:
        raise ValueError('Go build cache manifest changed or exceeds its limit')
    data = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(data, dict) or data.get('schema') != SCHEMA or data.get('owner') != owner(config)
            or data.get('go_sha256') != config.go_dependencies['go_sha256']
            or data.get('trimpath') is not True):
        raise ValueError('Go build cache seed belongs to another instance/toolchain/profile')
    if fingerprint(config.go_dependencies['go']) != data['go_sha256']:
        raise ValueError('Go build cache toolchain changed')
    files = data.get('files')
    if not isinstance(files, dict) or not files or len(files) > MAX_FILES:
        raise ValueError('Go build cache manifest has an invalid file inventory')
    total = 0
    for name, entry in files.items():
        match = ENTRY.fullmatch(name)
        if (not match or match[1] != match[2][:2] or not isinstance(entry, dict)
                or set(entry) != {'size', 'sha256'} or type(entry['size']) is not int
                or not 0 <= entry['size'] <= 1 << 30 or not isinstance(entry['sha256'], str)
                or not HEX.fullmatch(entry['sha256'])):
            raise ValueError('Go build cache manifest contains an invalid entry')
        total += entry['size']
        if check_files and regular(path.parent / 'cache' / name).st_size != entry['size']:
            raise ValueError('Go build cache seed file size changed')
    if total > MAX_BYTES:
        raise ValueError('Go build cache seed exceeds its size limit')
    return Seed(path, block['sha256'], files)


def copy_checked(source, destination, entry):
    """Never hardlink shared artifacts or publish a partially copied cache file."""
    before = regular(source)
    if before.st_size != entry['size']:
        raise ValueError('Go build cache seed file size changed')
    plain(destination.parent).mkdir(parents=True, exist_ok=True)
    temp = destination.parent / ('.farmbot-seed-' + uuid4().hex)
    digest, count = hashlib.sha256(), 0
    try:
        with source.open('rb') as src, temp.open('xb') as dest:
            while chunk := src.read(1 << 20):
                digest.update(chunk)
                count += len(chunk)
                dest.write(chunk)
        if (count != entry['size'] or digest.hexdigest() != entry['sha256']
                or stamp(before) != stamp(regular(source))):
            raise ValueError('Go build cache seed changed during copy')
        # The caller owns the destination; worker startup holds its retention lock
        # and proves all previous attempts quiescent before importing anything.
        if destination.exists() or destination.is_symlink():
            raise ValueError('Go build cache destination appeared during copy')
        temp.replace(destination)
    finally:
        temp.unlink(missing_ok=True)


class Seed:
    def __init__(self, manifest, digest, files):
        self.manifest, self.digest, self.files = manifest, digest, files

    def populate(self, cache):
        cache = Path(cache).resolve(strict=True)  # compiler_cache already fences item-owned links.
        if fingerprint(self.manifest) != self.digest:
            raise ValueError('Go build cache manifest changed before worker launch')
        started = time.monotonic()
        copied = total = 0
        for name, entry in self.files.items():
            target = plain(cache / name)
            if target.exists():
                regular(target)  # Retain this issue's own artifacts, without following links.
                continue
            copy_checked(self.manifest.parent / 'cache' / name, target, entry)
            copied += 1
            total += entry['size']
        return {'manifest_sha256': self.digest, 'copied_files': copied, 'copied_bytes': total,
                'seconds': round(time.monotonic() - started, 3)}


def publish(config, cache, destination, provenance):
    """Host-only sealing. Never call this on a worker's writable cache or worktree."""
    cache = outside_state(cache, config.local_root)
    destination = outside_state(destination, config.local_root)
    validate_settings({'manifest': str(destination / 'manifest.json'), 'sha256': '0' * 64},
                      config.local_root, config.go_dependencies)
    if destination.is_relative_to(cache) or cache.is_relative_to(destination):
        raise ValueError('Go build cache publication needs separate source and destination')
    if (not isinstance(provenance, dict) or set(provenance) != {'repo', 'commit', 'packages'}
            or provenance['repo'] not in config.repos
            or not re.fullmatch('[0-9a-f]{40}', provenance['commit'])
            or not isinstance(provenance['packages'], list) or not provenance['packages']
            or any(not isinstance(p, str) or not re.fullmatch(r'\./[A-Za-z0-9_/-]+', p)
                   or '..' in p.split('/') for p in provenance['packages'])):
        raise ValueError('Go build cache publication requires operator build provenance')
    if fingerprint(config.go_dependencies['go']) != config.go_dependencies['go_sha256']:
        raise ValueError('Go build cache toolchain changed')
    files, total = {}, 0
    for prefix in sorted(cache.iterdir()):
        plain(prefix)
        if not re.fullmatch('[0-9a-f]{2}', prefix.name):
            continue  # Go's README and trim timestamps are not compilation artifacts.
        for path in sorted(prefix.iterdir()):
            name = prefix.name + '/' + path.name
            match = ENTRY.fullmatch(name)
            if not match or match[1] != match[2][:2]:
                raise ValueError('Unexpected Go build cache entry')
            size = regular(path).st_size
            if size > 1 << 30:
                raise ValueError('Go build cache artifact exceeds its size limit')
            total += size
            files[name] = {'size': size, 'sha256': fingerprint(path)}
            if len(files) > MAX_FILES or total > MAX_BYTES:
                raise ValueError('Go build cache seed exceeds its limits')
    if not files:
        raise ValueError('Cannot publish an empty Go build cache seed')
    destination.mkdir(parents=True, exist_ok=False)
    for name, entry in files.items():
        copy_checked(cache / name, destination / 'cache' / name, entry)
    data = {'schema': SCHEMA, 'owner': owner(config), 'go_sha256': config.go_dependencies['go_sha256'],
            'trimpath': True, 'provenance': provenance, 'files': files}
    path = destination / 'manifest.json'
    encoded = json.dumps(data, sort_keys=True).encode('utf-8')
    if len(encoded) > MAX_MANIFEST:
        raise ValueError('Go build cache manifest exceeds its limit')
    path.write_bytes(encoded)  # Publish the manifest only after every file is complete.
    return {'manifest': str(path), 'sha256': fingerprint(path)}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Seal an operator-built, trimpath Go cache; never runs tests')
    parser.add_argument('--config', required=True)
    parser.add_argument('--cache', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--package', action='append', required=True)
    args = parser.parse_args(argv)
    from .config import load_config
    config = load_config(args.config)
    print(json.dumps(publish(config, args.cache, args.output,
                             {'repo': args.repo, 'commit': args.commit, 'packages': args.package})))


if __name__ == '__main__':
    main()
