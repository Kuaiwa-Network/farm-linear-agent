"""Assess retained suites and retain one immutable closeout index in one invocation."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from .config import Paths
from .evidence_index import _digest, _file, create_index
from .ledger import LedgerError
from .test_baseline import _read
from .verification import compare_recorded_pair, verify_recorded_candidate


def prepare(args, ledger, config, token):
    ledger.renew(args.item, token)
    try:
        return _prepare(args, ledger, config, token)
    except (LedgerError, ValueError, OSError, TypeError, AttributeError, KeyError):
        try:
            ledger.verification_progress(args.item, token, 'comparison-gap')
        except LedgerError:
            pass  # a retired claim cannot change the successor's status
        raise


def _prepare(args, ledger, config, token):
    paths = Paths(config)
    if Path(args.db).resolve() != paths.ledger.resolve():
        raise LedgerError('verification closeout must use the configured host ledger')
    root = paths.runs / args.item
    manifest = _read(_file(root, args.input))
    runs = manifest.get('runs') if isinstance(manifest, dict) else None
    if not isinstance(runs, list) or not 1 <= len(runs) <= 6:
        raise LedgerError('closeout requires 1 to 6 retained run entries')
    entries, seen, pins, digests = [], set(), {}, {}
    # Validate all paths and identities before recording any verdict.
    for entry in runs:
        if (not isinstance(entry, dict) or not {'candidate', 'scope'} <= entry.keys()
                or set(entry) - {'candidate', 'scope', 'baseline', 'repair_original'}):
            raise LedgerError('each closeout run needs candidate/scope and optional baseline/repair_original')
        if entry.get('repair_original') and not entry.get('baseline'):
            raise LedgerError('repair closeout requires its actual baseline pair')
        files = {key: str(_file(root, value)) for key, value in entry.items()}
        for path in files.values():
            digests.setdefault(path, _digest(Path(path)))
        candidate = _read(files['candidate'])
        if not isinstance(candidate, dict):
            raise LedgerError('candidate report must be an object')
        identity = (candidate.get('reservation_id'), candidate.get('job_id'), candidate.get('mode'))
        if identity in seen:
            raise LedgerError('duplicate actual test execution in closeout')
        seen.add(identity)
        pin = candidate.get('commit')
        if pins and pins['candidate'] != pin:
            raise LedgerError('closeout suites must verify one exact candidate')
        pins['candidate'] = pin
        entries.append((files, candidate.get('mode')))
    reports, results, indexed = [], [], set()
    for files, mode in entries:
        call = SimpleNamespace(item=args.item, db=args.db, **files)
        result = (compare_recorded_pair(call, ledger, config, token) if 'baseline' in files else
                  verify_recorded_candidate(call, ledger, config, token))
        data = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')
        name = 'verification-verdict-' + hashlib.sha256(data).hexdigest() + '.json'
        target = root / name
        try:
            with target.open('xb') as stream:
                stream.write(data)
        except FileExistsError:
            if _file(root, name).read_bytes() != data:
                raise LedgerError('retained verdict changed; preserve and diagnose it') from None
        reports.append({'path': name, 'purpose': str(mode) + ' scoped verification',
                        'summary': result['verdict'] + '; original failures and skips retained'})
        indexed.add(target.resolve())
        for key, value in files.items():
            if Path(value).resolve() not in indexed:
                reports.append({'path': value, 'purpose': str(mode) + ' ' + key,
                                'summary': 'Original retained input; no test rerun or result rewriting'})
                indexed.add(Path(value).resolve())
        results.append({'mode': mode, 'path': str(target), 'verdict': result['verdict'],
                        'draft_eligible': result['draft_eligible'], 'blockers': result['blockers']})
    ledger.renew(args.item, token)
    owned = ledger._owned(args.item, token)
    issue = ledger._issue_row(owned['issue_id'])
    if owned['requeue_requested'] or owned['claimed_fingerprint'] != issue['fingerprint']:
        raise LedgerError('issue changed; revalidate before saving closeout')
    index = create_index(root, {'id': args.item, 'claimed_fingerprint': owned['claimed_fingerprint']},
                         {'pins': pins, 'reports': reports})
    if any(_digest(Path(path)) != digest for path, digest in digests.items()):
        raise LedgerError('closeout input changed during assessment; preserve and review the original reports')
    eligible = all(result['draft_eligible'] for result in results)
    # A later passing suite must not hide an earlier blocked suite in Monitor.
    ledger.verification_progress(args.item, token, 'comparison-passed' if eligible else 'comparison-blocked')
    return {'draft_eligible': eligible, 'suites': results, 'evidence_index': index,
            'completion_checked': False, 'next_action':
            ('reuse this index in the compact checkpoint; finish --check-ready --checkpoint after remaining gates'
             if eligible else 'diagnose the listed blockers using retained reports; do not repeat baseline')}
