"""Discover diagnosed fixture repairs across jobs, without borrowing a passing verdict."""
import json
import re
from contextlib import nullcontext

from .test_baseline import ComparisonError, _path, _run, _signature
from .worktrees import GIT_ENV, WorktreeError, _git


def blob(trees, commit, path):
    if not isinstance(commit, str) or not re.fullmatch('[0-9a-f]{40}', commit):
        raise ComparisonError('shared repair requires full source commits')
    path = _path(path)
    # Validate the controller clone before Git; never follow a worker's .git/config.
    clone = trees._clone('Farm-Client')
    value = _git('rev-parse', '--verify', commit + ':' + path, cwd=clone,
                 env={**GIT_ENV, 'GIT_NO_REPLACE_OBJECTS': '1'}).strip()
    if _git('cat-file', '-t', value, cwd=clone,
            env={**GIT_ENV, 'GIT_NO_REPLACE_OBJECTS': '1'}).strip() != 'blob':
        raise ComparisonError('shared repair inputs must be files')
    return value


def record(ledger, trees, item_id, original, candidate, report, *, token=None):
    """Called only after the normal claim-fenced repair comparison succeeds."""
    if (not report.get('draft_eligible') or not report.get('repaired_failures')
            or report.get('validation_scope') != 'test-only-repair-with-retained-full-evidence'):
        return
    review = report['repair_review']
    dependencies = review.get('shared_dependencies', [])
    if (not isinstance(dependencies, list) or len(dependencies) > 128
            or len(set(dependencies)) != len(dependencies)):
        raise ComparisonError('shared_dependencies must list at most 128 distinct reviewed input paths')
    inputs = {}
    for path in dependencies:
        path = _path(path)
        old, new = blob(trees, original['commit'], path), blob(trees, candidate['commit'], path)
        if old != new:
            raise ComparisonError('shared repair dependency changed; review it as a repaired fixture instead')
        inputs[path] = old
    rows = _run(original)
    fixtures = {}
    for path, fixture in review['fixtures'].items():
        failures = {name: list(_signature(rows[name][0])) for name in report['repaired_failures']
                    if name.startswith(fixture + '.')}
        if failures:
            fixtures[path] = {'fixture': fixture, 'before': blob(trees, original['commit'], path),
                              'after': blob(trees, candidate['commit'], path), 'failures': failures}
    document = {'schema': 1, 'source_item': item_id, 'source_commit': original['commit'],
                'repair_commit': candidate['commit'], 'source_reservation': original['reservation_id'],
                'repair_reservation': candidate['reservation_id'], 'repair_job': candidate['job_id'],
                'fixtures': fixtures, 'dependencies': inputs,
                'engine': {k: original['environment'][k] for k in
                           ('unity_version', 'platform', 'test_framework_version')},
                'reason': review['reason'], 'evidence': review['evidence'],
                'focused_tests': review['expected_tests']}
    # Immutable controller audit events; no new schema or worker-writable registry.
    # Git reads precede this short transaction. A stop/withdrawal during those reads
    # must revoke the worker's ability to publish a new shared record too.
    with ledger._transaction() if token is not None else nullcontext():
        if token is not None:
            owned = ledger._owned(item_id, token)
            ledger._refuse_withdrawn(owned)
        previous = ledger.connection.execute("SELECT details FROM audit WHERE item_id=? "
            "AND kind='shared_test_repair' ORDER BY id DESC LIMIT 1", (item_id,)).fetchone()
        if not previous or json.loads(previous['details']) != document:
            ledger._audit(item_id, 'shared_test_repair', 'verified fixture repair', details=document)


def lookup(ledger, trees, candidate):
    """Return exact matches or concrete drift, never suppress current failures."""
    rows = _run(candidate)
    failed = {name: list(_signature(group[0])) for name, group in rows.items()
              if len(group) == 1 and group[0]['state'].startswith('Failed')}
    matches, seen, inputs = [], set(), {}

    def current_blob(path):
        if path not in inputs:
            try:
                inputs[path] = blob(trees, candidate['commit'], path)
            except WorktreeError:
                inputs[path] = None  # Missing historical source is a review gap, never a match.
        return inputs[path]
    for event in ledger.connection.execute("SELECT id,details FROM audit WHERE kind='shared_test_repair' "
                                           "ORDER BY id DESC LIMIT 200"):
        saved = json.loads(event['details'])
        if saved.get('schema') != 1:
            continue
        for path, fixture in saved['fixtures'].items():
            names = sorted(set(fixture['failures']) & failed.keys())
            identity = (path, saved['repair_commit'])
            if not names or identity in seen:
                continue
            seen.add(identity)
            reasons = []
            if any(failed[n] != fixture['failures'][n] for n in names):
                reasons.append('failure signature differs')
            if current_blob(path) != fixture['before']:
                reasons.append('fixture source differs')
            if any(candidate['environment'].get(k) != v for k, v in saved['engine'].items()):
                reasons.append('engine/toolchain differs')
            if not saved['dependencies']:
                reasons.append('dependency review not recorded')
            drift = [p for p, expected in saved['dependencies'].items()
                     if current_blob(p) != expected]
            if drift:
                reasons.append('reviewed dependency source differs')
            matches.append({'audit_id': event['id'], 'source_item': saved['source_item'],
                'source_commit': saved['source_commit'], 'repair_commit': saved['repair_commit'],
                'fixture_path': path, 'fixture': fixture['fixture'], 'failures': names,
                'status': 'review_required' if reasons else 'matching_diagnosed_repair',
                'differences': reasons, 'dependency_drift': drift,
                'dependencies': saved['dependencies'], 'reason': saved['reason'], 'evidence': saved['evidence'],
                'repair_reservation': saved['repair_reservation'], 'repair_job': saved['repair_job'],
                'focused_tests': saved['focused_tests']})
    from .shared_test_findings import lookup as findings
    return {'matches': matches, 'maintenance_findings': findings(ledger, trees, candidate),
            'evidence_is_authority': False, 'current_tests_passed': False,
            'next': 'First review current issue diff and causal applicability. Confirmed unrelated failures '
                    'use references/legacy/shared-test-findings.md and separate maintenance; do not require a fixture repair '
                    'for business delivery. If repair is actually required or separately authorized, port only the fixture repair, then '
                    'validate whole changed fixtures at the new candidate using the original baseline and reports. '
                    'Reuse matching diagnosis, not historical execution results. Do not merge newer main.'}


def discover(args, ledger, config, token):
    from pathlib import Path
    from .config import Paths
    from .evidence_index import _file
    from .ledger import LedgerError
    from .test_baseline import _read
    from .worktrees import Worktrees
    ledger.renew(args.item, token)
    item = ledger.item(args.item)
    if item['skill'] not in ('work', 'feature', 'fix') or item['root_repo'] != 'Farm-Client':
        raise LedgerError('shared repairs require a claimed Client code job')
    paths = Paths(config)
    if Path(args.db).resolve() != paths.ledger.resolve():
        raise LedgerError('shared repairs must use the configured host ledger')
    candidate = _read(_file(paths.runs / args.item, args.candidate))
    pair = ledger.client_verification_pair(args.item, candidate.get('commit'), require_candidate=True,
                                          candidate_reservation=candidate.get('reservation_id'))
    if not pair:
        raise ComparisonError('lookup requires this item settled candidate report')
    trees = Worktrees(paths.repos, paths.worktrees, config.repos)
    trees._clone('Farm-Client')  # A clone trust violation must fail before lookup's missing-object handling.
    return lookup(ledger, trees, candidate)
