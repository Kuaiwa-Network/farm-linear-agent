"""Assess one actual Client run; request comparison only for unresolved attribution.

The source base is a Git identity, never an invented baseline test execution.
This module does not run tests, waive affected checks or grant publication authority.
"""
from .shared_test_findings import digest, review_finding
from .test_baseline import (ComparisonError, SHA, _normalized, _outcome, _path,
                            _reviewed, _run, _signature)


def failure_id(candidate, source_base_commit, name, row):
    return digest({'source_base_commit': source_base_commit,
                   'candidate': [candidate[k] for k in ('commit', 'reservation_id', 'job_id')],
                   'environment': candidate['environment'], 'selection': candidate['selection'],
                   'test': name, 'outcome': list(_signature(row))})


def assess(candidate, *, source_base_commit, affected_tests, changed_paths, issue_scope,
           unrelated_failures, reviewed_skips=None, verified_source_tree=None):
    rows = _run(candidate)
    if not isinstance(source_base_commit, str) or not SHA.fullmatch(source_base_commit):
        raise ComparisonError('source_base_commit requires the controller-recorded full Git SHA')
    if (not isinstance(affected_tests, list) or not affected_tests
            or any(not isinstance(n, str) or not n.strip() for n in affected_tests)
            or len(set(affected_tests)) != len(affected_tests)):
        raise ComparisonError('distinct nonempty affected tests are required')
    if not isinstance(changed_paths, list):
        raise ComparisonError('the complete changed paths list is required')
    changed_paths = [_path(p) for p in changed_paths]
    if len({p.casefold() for p in changed_paths}) != len(changed_paths):
        raise ComparisonError('duplicate or aliased changed paths')
    if source_base_commit == candidate['commit'] and changed_paths:
        raise ComparisonError('identical source commits cannot have a diff')
    if verified_source_tree is not None and (not isinstance(verified_source_tree, str)
            or not SHA.fullmatch(verified_source_tree) or changed_paths):
        raise ComparisonError('verified equal trees require a full tree identity and empty actual diff')
    same_source = source_base_commit == candidate['commit'] or verified_source_tree is not None
    if (not _reviewed(issue_scope) or issue_scope.get('source_base_commit') != source_base_commit
            or issue_scope.get('candidate_commit') != candidate['commit']):
        raise ComparisonError('issue scope must bind source base and actual candidate with reason/evidence')
    for key, expected in (('changed_paths', changed_paths), ('affected_tests', affected_tests)):
        if issue_scope.get(key) != expected:
            raise ComparisonError('issue scope must match ' + key)
    requirements = issue_scope.get('requirements')
    if (not isinstance(requirements, list) or not requirements
            or any(not isinstance(x, str) or not x.strip() for x in requirements)):
        raise ComparisonError('issue scope must name actual acceptance requirements')
    if not isinstance(unrelated_failures, dict) or (reviewed_skips is not None
                                                  and not isinstance(reviewed_skips, dict)):
        raise ComparisonError('failure and skip reviews must map exact test identities')
    reviewed_skips = {} if reviewed_skips is None else reviewed_skips
    blockers, unresolved, affected, ambiguous = [], [], [], []
    confirmed, unchanged, skips, ids = [], [], [], {}
    for name in affected_tests:
        if name not in rows or any(_outcome(row['state']) != 'passed' for row in rows[name]):
            affected.append(name)
    for name, group in sorted(rows.items()):
        if len(group) > 1 and any(_outcome(row['state']) != 'passed' for row in group):
            ambiguous.append(name)
            continue
        for row in group:
            outcome = _outcome(row['state'])
            if outcome == 'passed':
                continue
            if name in affected:
                continue
            identity = failure_id(candidate, source_base_commit, name, row)
            ids[name] = identity
            if outcome == 'skipped':
                review = reviewed_skips.get(name)
                if (_reviewed(review) and review.get('evidence_id') == identity
                        and review.get('classification') == 'not_applicable'):
                    skips.append({'fullName': name, 'review': review})
                else:
                    blockers.append('unreviewed skip or missing required coverage: ' + name)
                continue
            review = unrelated_failures.get(name)
            if row['state'] == 'Failed:Invalid' or any(
                    p.casefold() in _normalized(row.get('stackTrace')).casefold() for p in changed_paths):
                unresolved.append(name)
            elif (_reviewed(review) and review.get('classification') == 'unchanged_source'
                    and review.get('evidence_id') == identity
                    and same_source and not changed_paths):
                # No code delta can introduce a source regression. This says nothing
                # about the root cause or the health of the unchanged environment.
                unchanged.append({'fullName': name, 'review': review})
            elif isinstance(review, dict) and review.get('confirmed_unrelated') is not None:
                confirmed.append(review_finding(review, name, row, changed_paths, identity))
            else:
                unresolved.append(name)
    if affected:
        blockers.append('affected test missing or not passed: ' + ', '.join(affected))
    if ambiguous:
        blockers.append('ambiguous repeated non-passing executions: ' + ', '.join(ambiguous))
    if unresolved:
        blockers.append('unresolved failure attribution: ' + ', '.join(unresolved))
    eligible = not blockers
    return {'source_base_commit': source_base_commit,
            'verified_source_tree': verified_source_tree,
            'baseline_executed': False, 'candidate_commit': candidate['commit'],
            'candidate_reservation': candidate['reservation_id'], 'candidate_job': candidate['job_id'],
            'candidate_counts': dict(candidate['summary']), 'scope_policy': 'candidate-first-v1',
            'issue_scope': issue_scope, 'draft_eligible': eligible,
            'verdict': 'passed_with_retained_findings' if eligible and (confirmed or unchanged) else
                       'passed' if eligible else 'blocked',
            'full_suite_passed': (candidate['selection'] == {'assembly_names': ['HotUpdate.PlayTests']}
                                  and candidate['summary']['failed'] == 0),
            'selected_tests_passed': candidate['summary']['failed'] == 0,
            'confirmed_unrelated_failures': confirmed, 'unchanged_source_failures': unchanged,
            'reviewed_skips': skips, 'failure_evidence_ids': ids, 'unresolved_failures': unresolved,
            'affected_not_passed': affected, 'ambiguous_tests': ambiguous, 'blockers': blockers,
            'comparison_needed': bool(unresolved) and not same_source,
            'next_action': ('complete remaining required checks and normal draft delivery' if eligible else
                            'diagnose affected failures or evidence gaps; compare only unresolved failure '
                            'attribution when a meaningful source/input difference can be tested')}
