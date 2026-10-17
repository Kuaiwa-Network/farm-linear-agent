"""Keep proven unrelated test defects as maintenance debt, not business work."""
import hashlib
import json

from .test_baseline import ComparisonError, _normalized, _path, _reviewed, _signature


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def evidence_id(baseline, candidate, name, old, row):
    return digest({'baseline': [baseline[k] for k in ('commit', 'reservation_id', 'job_id')],
                   'candidate': [candidate[k] for k in ('commit', 'reservation_id', 'job_id')],
                   'test': name, 'before': list(_signature(old)) if old else None,
                   'after': list(_signature(row))})


def review_failure(review, baseline, candidate, name, old, row, changed_paths):
    """Validate a specific causal review; absence is never an implicit exception."""
    finding = review.get('confirmed_unrelated') if isinstance(review, dict) else None
    if finding is None:
        return None
    if not _reviewed(review) or not isinstance(finding, dict):
        raise ComparisonError('confirmed unrelated failure requires a causal review')
    if (old is None or not (old['state'] == 'Passed' or old['state'].startswith('Failed'))
            or row['state'] == 'Failed:Invalid'
            or finding.get('evidence_id') != evidence_id(baseline, candidate, name, old, row)):
        raise ComparisonError('shared finding must bind both actual executions; new/invalid tests cannot be deferred')
    if finding.get('kind') not in ('shared_test_defect', 'shared_environment_defect'):
        raise ComparisonError('shared finding requires a diagnosed test or shared environment defect')
    for key in ('cause', 'exclusion', 'maintenance_action', 'evidence_file', 'evidence_sha256'):
        if not isinstance(finding.get(key), str) or not finding[key].strip():
            raise ComparisonError('shared finding requires ' + key)
    import re
    if not re.fullmatch('[0-9a-f]{64}', finding['evidence_sha256']):
        raise ComparisonError('shared finding requires the retained causal evidence SHA-256')
    fixture = _path(finding.get('fixture_path'))
    if not re.fullmatch(r'Assets/(?:[^/]+/)*Tests/(?:[^/]+/)*[^/]+\.cs', fixture):
        raise ComparisonError('shared finding requires its actual test fixture source')
    if fixture not in _normalized(row.get('stackTrace')):
        raise ComparisonError('shared finding fixture must occur in the actual failing stack')
    dependencies = finding.get('dependencies')
    if (not isinstance(dependencies, list) or not dependencies or len(dependencies) > 128
            or any(not isinstance(p, str) for p in dependencies)):
        raise ComparisonError('shared finding requires reviewed causal dependencies (at most 128)')
    dependencies = [_path(p) for p in dependencies]
    if len({p.casefold() for p in dependencies}) != len(dependencies):
        raise ComparisonError('shared finding has duplicate dependency paths')
    associated = {p.casefold() for p in changed_paths}
    if any(p.casefold() in associated for p in [fixture, *dependencies]):
        raise ComparisonError('changed fixture or causal dependency cannot be deferred as unrelated')
    return {**finding, 'fixture_path': fixture, 'dependencies': dependencies,
            'fullName': name, 'signature': list(_signature(row)),
            'reason': review['reason'], 'evidence': review['evidence']}


def verify(trees, root, baseline, candidate, scope, report, path):
    """Check real Git inputs and retained evidence before granting the new exception."""
    from .evidence_index import _file
    from .shared_test_repairs import blob
    from .worktrees import GIT_ENV
    findings = report['confirmed_unrelated_failures']
    if not findings:
        return
    delta = trees.git_in(path, 'diff', '--name-only', '--no-renames', '-z',
                        baseline['commit'], candidate['commit'],
                        env={**GIT_ENV, 'GIT_NO_REPLACE_OBJECTS': '1'}).split('\0')
    if set(filter(None, delta)) != set(scope['changed_paths']):
        raise ComparisonError('shared finding requires the complete actual baseline/candidate diff')
    cached = {}
    for finding in findings:
        inputs = {}
        for name in [finding['fixture_path'], *finding['dependencies']]:
            if name not in cached:
                before = blob(trees, baseline['commit'], name)
                after = blob(trees, candidate['commit'], name)
                if before != after:
                    raise ComparisonError('shared finding fixture or dependency changed: ' + name)
                cached[name] = before
            inputs[name] = cached[name]
        file = _file(root, finding['evidence_file'])
        if file.stat().st_size > 1024 * 1024:
            raise ComparisonError('shared finding causal evidence exceeds 1 MiB; retain a bounded review')
        with file.open('rb') as stream:
            data = stream.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise ComparisonError('shared finding causal evidence grew beyond 1 MiB')
        if not data.strip() or hashlib.sha256(data).hexdigest() != finding['evidence_sha256']:
            raise ComparisonError('shared finding causal evidence missing or changed')
        finding['inputs'] = inputs
        finding['environment'] = candidate['environment']
        finding['followup_id'] = digest({k: finding[k] for k in
            ('fullName', 'signature', 'kind', 'inputs', 'environment')})
        finding['status'] = 'open'
        finding['blocks_business_delivery'] = False


def record(ledger, item_id, report):
    """Called in the same claim-fenced transaction as the comparison verdict."""
    if not report['draft_eligible']:
        return
    for finding in report.get('confirmed_unrelated_failures', []):
        document = {'schema': 1, 'source_item': item_id, 'baseline_commit': report['baseline_commit'],
                    'candidate_commit': report['candidate_commit'], **finding}
        previous = ledger.connection.execute("SELECT details FROM audit WHERE item_id=? "
            "AND kind='shared_test_followup' ORDER BY id DESC LIMIT 200", (item_id,))
        if any(json.loads(row['details']) == document for row in previous):
            continue
        ledger._audit(item_id, 'shared_test_followup', 'confirmed unrelated; independent maintenance', details=document)


def lookup(ledger, trees, candidate):
    """Discover maintenance findings without borrowing the source issue's scope decision."""
    from .shared_test_repairs import blob
    from .test_baseline import _run
    from .worktrees import WorktreeError
    failed = {n: list(_signature(g[0])) for n, g in _run(candidate).items()
              if len(g) == 1 and g[0]['state'].startswith('Failed')}
    result, seen, inputs = [], set(), {}
    for row in ledger.connection.execute("SELECT id,details FROM audit WHERE kind='shared_test_followup' "
                                         "ORDER BY id DESC LIMIT 200"):
        finding = json.loads(row['details'])
        if finding.get('schema') != 1 or finding['fullName'] not in failed or finding['followup_id'] in seen:
            continue
        seen.add(finding['followup_id'])
        differences = []
        if failed[finding['fullName']] != finding['signature']:
            differences.append('failure signature differs')
        if candidate['environment'] != finding['environment']:
            differences.append('environment differs')
        for path, expected in finding['inputs'].items():
            if path not in inputs:
                try:
                    inputs[path] = blob(trees, candidate['commit'], path)
                except WorktreeError:
                    inputs[path] = None
            if inputs[path] != expected:
                differences.append('source differs: ' + path)
        result.append({**finding, 'audit_id': row['id'], 'differences': differences,
                       'reuse_status': 'review_required' if differences else 'matching_diagnosis',
                       'next': 'Review this issue complete diff and acceptance; bind a new causal review to its '
                               'actual reports. No repair, rerun or waiting for maintenance is required for '
                               'confirmed unrelated failures. This lookup is not a verdict.'})
    return result
