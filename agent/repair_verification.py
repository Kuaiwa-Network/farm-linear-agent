"""Explicit descendant repair of a settled Client round, never an old result relabel."""
import json
import re

from .ledger import LedgerError


def source_pair(ledger, item_id, reservation_id):
    item = ledger.item(item_id)
    source = ledger.reservation(reservation_id)
    if (item['skill'] not in ('work', 'feature', 'fix')
            or not source or source['item_id'] != item_id or source['kind'] != 'unity_slot'
            or source['state'] != 'released' or source['release_reason'] != 'worker reported quiescent'
            or ledger.reservation_comparison(reservation_id) is not None
            or reservation_repair(ledger, reservation_id) is not None):
        raise LedgerError('repair requires this item original cleanly settled candidate reservation')
    pair = ledger.client_verification_pair(item_id, source['commit_sha'], require_candidate=True,
                                           candidate_reservation=reservation_id)
    if not pair or pair['candidate_reservation'] != reservation_id:
        raise LedgerError('repair requires the current settled original comparison round')
    return pair


def reservation_repair(ledger, reservation_id):
    row = ledger.reservation(reservation_id)
    if not row:
        return None
    for event in ledger.connection.execute(
            "SELECT details FROM audit WHERE item_id=? AND kind='reservation' "
            "AND reason IN ('queued','requeued') ORDER BY id DESC", (row['item_id'],)):
        details = json.loads(event['details'])
        if details.get('reservation_id') == reservation_id:
            repair = details.get('client_repair')
            if repair is not None and (not isinstance(repair, dict)
                    or repair.get('candidate_commit') != row['commit_sha']
                    or not isinstance(repair.get('source_reservation'), str)
                    or not isinstance(repair.get('changed_paths'), list) or not repair['changed_paths']):
                raise LedgerError('repair reservation evidence is damaged')
            return repair
    return None


def repaired_pair(ledger, item_id, candidate, *, require_candidate=False, candidate_reservation=None):
    for row in ledger.connection.execute("SELECT * FROM reservations WHERE item_id=? AND kind='unity_slot' "
                                         "AND commit_sha=? ORDER BY sequence DESC", (item_id, candidate)):
        repair = reservation_repair(ledger, row['reservation_id'])
        if not repair:
            continue
        # Never borrow an older repair if the newest reservation is unclean or unfinished.
        if row['state'] != 'released' or row['release_reason'] != 'worker reported quiescent':
            return None
        if candidate_reservation is not None and candidate_reservation != row['reservation_id']:
            continue
        pair = source_pair(ledger, item_id, repair['source_reservation'])
        source = ledger.reservation(repair['source_reservation'])
        if (pair['baseline_commit'] != repair.get('baseline_commit')
                or pair['candidate_commit'] != repair.get('source_commit') or source['mode'] != row['mode']):
            raise LedgerError('repair no longer matches its original verification round')
        return {**pair, 'candidate_commit': candidate, 'candidate_reservation': row['reservation_id'],
                'repair': repair}
    return None


def compare_test_repair(baseline, original, candidate, scope, repair):
    """Combine a retained full verdict with explicitly scoped new evidence; never forge a full run."""
    from .test_baseline import ComparisonError, _run, _reviewed, compare
    scope = dict(scope)
    review = scope.pop('repair', None)
    if not _reviewed(review):
        raise ComparisonError('test repair needs a causal repair review with reason and evidence')
    paths = repair['changed_paths']
    if (not paths or any(not re.fullmatch(r'Assets/(?:[^/]+/)*Tests/(?:[^/]+/)*[^/]+\.cs', p) for p in paths)
            or review.get('changed_paths') != paths):
        raise ComparisonError('focused evidence reuse permits only the exact test fixture source delta')
    if (original['commit'] != repair['source_commit'] or candidate['commit'] != repair['candidate_commit']
            or original['reservation_id'] != repair['source_reservation']):
        raise ComparisonError('repair reports do not match the recorded original and repaired candidates')
    before, after = _run(original), _run(candidate)
    if (candidate['mode'] != original['mode'] or candidate['environment'] != original['environment']
            or candidate.get('package_inventory') != original.get('package_inventory')
            or candidate['job_id'] in (original['job_id'], baseline['job_id'])
            or candidate['reservation_id'] in (original['reservation_id'], baseline['reservation_id'])):
        raise ComparisonError('repair must have fresh comparable evidence with unchanged runtime inputs')
    fixtures = review.get('fixtures')
    expected = review.get('expected_tests')
    if (not isinstance(fixtures, dict) or set(fixtures) != set(paths)
            or any(not isinstance(v, str) or not v.strip() for v in fixtures.values())
            or not isinstance(expected, list) or not expected or len(expected) != len(set(expected))
            or set(expected) != set(after)):
        raise ComparisonError('repair review must cover every changed fixture and every observed test')
    selected = candidate['selection'].get('test_names', [])
    if not isinstance(selected, list) or not set(fixtures.values()) <= set(selected):
        raise ComparisonError('repair must select whole changed fixtures, not only previously failing leaves')
    for fixture in fixtures.values():
        names = {n for n in before if n.startswith(fixture + '.')}
        if not names or not names <= after.keys():
            raise ComparisonError('repair must retain every original test in each changed fixture')
    if any(len(rows) != 1 or rows[0]['state'] != 'Passed' for rows in after.values()):
        raise ComparisonError('every focused repair test must pass exactly once; skips/failures remain blockers')
    report = compare(baseline, original, **scope)
    resolved = set(report['unresolved_failures'])
    if (not resolved or not resolved <= after.keys()
            or any(not any(n.startswith(f + '.') for f in fixtures.values()) for n in resolved)):
        raise ComparisonError('repair must resolve all original unmatched failures in reviewed changed fixtures')
    # Keep all environment, missing-test, changed-skip, duplicate and scope blockers.
    allowed = {'new, changed, affected or unreviewed failures: ' + ', '.join(report['unresolved_failures'])}
    allowed.update('affected test missing or not passed: ' + n for n in resolved)
    blockers = [b for b in report['blockers'] if b not in allowed]
    eligible = not blockers
    return {**report, 'candidate_commit': candidate['commit'], 'candidate_reservation': candidate['reservation_id'],
            'candidate_job': candidate['job_id'], 'candidate_counts': candidate['summary'],
            'draft_eligible': eligible, 'full_suite_passed': False,
            'verdict': 'passed_with_retained_evidence' if eligible else 'blocked',
            'unresolved_failures': [], 'blockers': blockers, 'repaired_failures': sorted(resolved),
            'validation_scope': 'test-only-repair-with-retained-full-evidence',
            'retained_original': {'commit': original['commit'], 'reservation_id': original['reservation_id'],
                'job_id': original['job_id'], 'counts': original['summary'], 'verdict': report['verdict']},
            'repair_review': review}


def verify_test_fixtures(trees, item_id, repair, review):
    """Check reviewed fixture identities against both Git trees, not worker path claims."""
    from .test_baseline import ComparisonError
    from .workflow import repository_path
    from .worktrees import GIT_ENV
    fixtures = review.get('fixtures') if isinstance(review, dict) else None
    if not isinstance(fixtures, dict) or set(fixtures) != set(repair['changed_paths']):
        raise ComparisonError('repair fixture map must match the controller observed delta')
    path = repository_path(trees, 'Farm-Client', item_id)
    for name, fixture in fixtures.items():
        if not re.fullmatch(r'Assets/(?:[^/]+/)*Tests/(?:[^/]+/)*[^/]+\.cs', name):
            raise ComparisonError('focused repair cannot reuse runtime source changes')
        for commit in (repair['source_commit'], repair['candidate_commit']):
            source = trees.git_in(path, 'show', commit + ':' + name,
                                  env={**GIT_ENV, 'GIT_NO_REPLACE_OBJECTS': '1'})
            # Strip comments before identifying simple top-level fixtures. Ambiguous/nested
            # fixture layouts require full evidence; they never gain a permissive fallback.
            source = re.sub(r'/\*.*?\*/|//[^\n]*', '', source, flags=re.S)
            spaces = re.findall(r'\bnamespace\s+([\w.]+)\s*[;{]', source)
            classes = re.findall(r'\bclass\s+(\w+)\b', source)
            if len(spaces) != 1 or len(classes) != 1 or fixture != spaces[0] + '.' + classes[0]:
                raise ComparisonError('changed fixture identity is ambiguous or differs from its review')
