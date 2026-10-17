"""Immutable, item-local report index. Hash integrity is never a test verdict."""
import hashlib
import json
import re
from pathlib import Path

MAX_REPORT_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024


def _file(root, value):
    if not isinstance(value, str) or not value:
        raise ValueError('report path must be nonempty text')
    root = Path(root).resolve()
    path = Path(value)
    if '..' in path.parts:
        raise ValueError('report must remain inside this item state directory without parent traversal')
    path = path if path.is_absolute() else root / path
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ValueError('report must remain inside this item state directory') from None
    if not resolved.is_file():
        raise ValueError('report must be an existing regular file')
    # Inspect the original path before resolving away links. The trusted item
    # root may have an OS alias (/var on macOS or an 8.3 name on Windows), so
    # recognize that boundary by its resolved identity, not its spelling.
    # Check links first: an item-local link back to the root is still forbidden.
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part, 'is_junction', lambda: False)():
            raise ValueError('report paths must not use links or junctions')
        if part.resolve() == root:
            break
    else:
        raise ValueError('report must remain inside this item state directory')
    return resolved


def _digest(path):
    before = path.stat()
    if before.st_size > MAX_REPORT_BYTES:
        raise ValueError('report exceeds 64 MiB; index its bounded result file instead')
    digest = hashlib.sha256()
    count = 0
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            count += len(chunk)
            if count > MAX_REPORT_BYTES:
                raise ValueError('report grew beyond 64 MiB while hashing')
            digest.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('report changed while hashing; wait for the writer to finish')
    return after.st_size, digest.hexdigest()


def _shape(value):
    if not isinstance(value, dict):
        raise ValueError('evidence index input must be an object')
    pins = value.get('pins')
    if (not isinstance(pins, dict) or not pins or len(pins) > 10 or
            any(not isinstance(k, str) or not k or len(k) > 100 or
                not isinstance(v, str) or not re.fullmatch('[0-9a-f]{40}', v) for k, v in pins.items())):
        raise ValueError('pins must name reviewed repositories/roles and their full commit SHAs')
    reports = value.get('reports')
    if not isinstance(reports, list) or not 1 <= len(reports) <= 32:
        raise ValueError('index requires 1 to 32 selected reports')
    for report in reports:
        if not isinstance(report, dict):
            raise ValueError('each report must be an object')
        for key, limit in (('purpose', 100), ('summary', 500)):
            if not isinstance(report.get(key), str) or not report[key].strip() or len(report[key]) > limit:
                raise ValueError(f'report {key} must be nonempty text of at most {limit} characters')
    return pins, reports


def _reports(root, reports, *, verify):
    result, seen, total = [], set(), 0
    for report in reports:
        path = _file(root, report.get('path'))
        if path in seen:
            raise ValueError('duplicate report path')
        seen.add(path)
        size, digest = _digest(path)
        total += size
        if total > MAX_TOTAL_BYTES:
            raise ValueError('selected reports exceed 256 MiB')
        if verify and (report.get('sha256') != digest or report.get('bytes') != size):
            raise ValueError(f'report changed: {path.relative_to(root).as_posix()}')
        result.append({'path': path.relative_to(root).as_posix(), 'bytes': size, 'sha256': digest,
                       'purpose': report['purpose'], 'summary': report['summary']})
    return result


def create_index(root, item, value):
    root = Path(root).resolve()
    pins, reports = _shape(value)
    document = {'schema': 1, 'item_id': item['id'], 'fingerprint': item['claimed_fingerprint'],
                'pins': pins, 'reports': _reports(root, reports, verify=False)}
    data = json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')
    if len(data) > 64000:
        raise ValueError('evidence index exceeds 64000 bytes; shorten summaries or split selected reports')
    name = 'evidence-index-' + hashlib.sha256(data).hexdigest() + '.json'
    target = root / name
    try:
        with target.open('xb') as stream:
            stream.write(data)
    except FileExistsError:
        if _file(root, name).read_bytes() != data:
            raise ValueError('existing evidence index content changed') from None
    return {'index': str(target), 'reports': len(document['reports']), 'pins': pins,
            'evidence_is_authority': False}


def verify_index(root, item, value):
    root = Path(root).resolve()
    target = _file(root, value)
    if target.stat().st_size > 64000:
        raise ValueError('evidence index exceeds 64000 bytes')
    data = target.read_bytes()
    if target.name != 'evidence-index-' + hashlib.sha256(data).hexdigest() + '.json':
        raise ValueError('evidence index identity changed')
    document = json.loads(data)
    pins, reports = _shape(document)
    if document.get('schema') != 1 or document.get('item_id') != item['id']:
        raise ValueError('evidence index belongs to another item or schema')
    return {'index': str(target), 'hashes_verified': True, 'evidence_is_authority': False,
            'requires_issue_review': (bool(item.get('requires_issue_review')) or
                                      document.get('fingerprint') != item['current_fingerprint'] or
                                      item['claimed_fingerprint'] != item['current_fingerprint']),
            'pins': pins, 'reports': _reports(root, reports, verify=True),
            'next': 'Check current scope, clean source pins and required gates; reuse these reports, not authorization.'}
