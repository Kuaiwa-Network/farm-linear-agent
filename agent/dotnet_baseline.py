"""Read-only comparison of complete standalone dotnet-test TRX evidence.

No Unity reservations are invented. Repeated display/test IDs retain every raw
execution; ambiguous non-passing duplicates are evidence gaps, never deduplicated.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

from .test_baseline import ComparisonError, SHA, _normalized, _path, _read, _reviewed, _text

NS = '{http://microsoft.com/schemas/VisualStudio/TeamTest/2010}'
ENV = {'host', 'execution_platform', 'toolchain_digest', 'config_digest', 'package_digest'}


def _one(element, path):
    nodes = element.findall('/'.join(NS + part for part in path.split('/')))
    if len(nodes) != 1:
        raise ComparisonError('TRX requires exactly one ' + path)
    return nodes[0]


def _stamp(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.utcoffset() is None:
            raise ValueError()
        return result
    except (AttributeError, TypeError, ValueError):
        raise ComparisonError('TRX requires finished, timezone-qualified run/result timestamps') from None


def load_run(manifest, *, base=Path('.')):
    if not isinstance(manifest, dict) or manifest.get('completed') is not True:
        raise ComparisonError('a completed standalone run is required')
    if not isinstance(manifest.get('commit'), str) or not SHA.fullmatch(manifest['commit']):
        raise ComparisonError('run commit must be a full lowercase SHA')
    if manifest.get('mode') != 'dotnet-test':
        raise ComparisonError('standalone mode must be dotnet-test')
    selection = manifest.get('selection')
    if not isinstance(selection, dict) or 'filter' not in selection:
        raise ComparisonError('explicit project and filter (null for full suite) required')
    _path(selection.get('project'))
    if selection['filter'] is not None:
        _text(selection['filter'], 'filter')
    environment = manifest.get('environment')
    if not isinstance(environment, dict) or not ENV <= environment.keys():
        raise ComparisonError('complete standalone environment identity required')
    for key in ENV:
        _text(environment[key], key)
        if key.endswith('_digest') and not re.fullmatch('[0-9a-f]{64}', environment[key]):
            raise ComparisonError(key + ' must be SHA-256')
    path = Path(_text(manifest.get('trx'), 'trx'))
    if not path.is_absolute():
        path = base / path
    with path.open('rb') as stream:
        raw = stream.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ComparisonError('TRX exceeds size bound or contains DTD/entity declarations')
    digest = hashlib.sha256(raw).hexdigest()
    if digest != manifest.get('trx_sha256'):
        raise ComparisonError('raw TRX digest differs from recorded manifest')
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise ComparisonError('invalid TRX XML') from None
    if root.tag != NS + 'TestRun':
        raise ComparisonError('unsupported TRX root/namespace')
    run_id = _text(root.get('id'), 'TRX run ID')
    times = _one(root, 'Times')
    start, finish = _stamp(times.get('start')), _stamp(times.get('finish'))
    if finish < start:
        raise ComparisonError('TRX run finishes before it starts')
    definitions, definition_executions = {}, set()
    for definition in _one(root, 'TestDefinitions'):
        if definition.tag != NS + 'UnitTest':
            raise ComparisonError('unsupported TRX test definition')
        execution, method = _one(definition, 'Execution'), _one(definition, 'TestMethod')
        test_id = _text(definition.get('id'), 'test ID')
        identity = (test_id, _text(execution.get('id'), 'execution ID'))
        value = (_text(method.get('className'), 'class'), _text(definition.get('name'), 'display name'))
        if test_id in definitions:
            raise ComparisonError('duplicate TRX test definition')
        definitions[test_id] = value
        definition_executions.add(identity)
    entries = set()
    for entry in _one(root, 'TestEntries'):
        identity = (_text(entry.get('testId'), 'test ID'), _text(entry.get('executionId'), 'execution ID'))
        if entry.tag != NS + 'TestEntry' or identity in entries or identity[0] not in definitions:
            raise ComparisonError('duplicate or unbound TRX test entry')
        entries.add(identity)
    if not definition_executions <= entries:
        raise ComparisonError('test definitions lack execution entries')
    groups, executions, counts = defaultdict(list), set(), Counter()
    for result in _one(root, 'Results'):
        if result.tag != NS + 'UnitTestResult' or result.find(NS + 'InnerResults') is not None:
            raise ComparisonError('unsupported nested or non-unit TRX result')
        test_id, execution_id = result.get('testId'), result.get('executionId')
        identity = (test_id, execution_id)
        if not execution_id or execution_id in executions or identity not in entries:
            raise ComparisonError('missing, duplicate or unbound TRX execution identity')
        executions.add(execution_id)
        entries.remove(identity)
        cls, display = definitions[test_id]
        if display != result.get('testName'):
            raise ComparisonError('TRX definition/result name mismatch')
        observed_start, observed_end = _stamp(result.get('startTime')), _stamp(result.get('endTime'))
        if observed_start < start or observed_end < observed_start or observed_end > finish:
            raise ComparisonError('TRX result lies outside completed run interval')
        state = result.get('outcome')
        if state not in ('Passed', 'Failed', 'NotExecuted'):
            raise ComparisonError('unsupported TRX outcome: ' + str(state))
        error = result.find(NS + 'Output/' + NS + 'ErrorInfo')
        message = error.findtext(NS + 'Message', '') if error is not None else ''
        stack = error.findtext(NS + 'StackTrace', '') if error is not None else ''
        if state != 'Passed':
            _text(message, 'failure/skip reason')
        name = cls + '.' + display
        groups[(name, test_id)].append({'execution_id': execution_id, 'state': state,
                                      'message': message, 'stackTrace': stack})
        counts[state] += 1
    if entries or not executions or set(definitions) != {test_id for _, test_id in groups}:
        raise ComparisonError('TRX has missing results or an empty selection')
    summary = _one(root, 'ResultSummary')
    counters = _one(summary, 'Counters').attrib
    try:
        numeric = {key: int(value) for key, value in counters.items()}
    except ValueError:
        raise ComparisonError('invalid TRX counters') from None
    expected = {'total': len(executions), 'passed': counts['Passed'], 'failed': counts['Failed'],
                'executed': counts['Passed'] + counts['Failed']}
    if any(numeric.get(key) != value for key, value in expected.items()):
        raise ComparisonError('TRX counters disagree with complete execution results')
    # NUnit's VSTest adapter emits NotExecuted leaves but may leave its aggregate
    # notExecuted counter at zero. Total/executed/pass/fail and every leaf bind it.
    if numeric.get('notExecuted') not in (0, counts['NotExecuted']):
        raise ComparisonError('TRX skipped count disagrees with leaves')
    if any(value != 0 for key, value in numeric.items() if key not in {*expected, 'notExecuted'}):
        raise ComparisonError('TRX has unfinished, error or unsupported aggregate outcomes')
    if summary.get('outcome') != ('Failed' if counts['Failed'] else 'Completed'):
        raise ComparisonError('TRX overall result is inconsistent or interrupted')
    return {'commit': manifest['commit'], 'selection': selection, 'environment': environment,
            'run_id': run_id, 'trx_sha256': digest, 'groups': groups, 'raw_counters': numeric,
            'counts': {'total': len(executions), 'passed': counts['Passed'], 'failed': counts['Failed'],
                       'skipped': counts['NotExecuted']}}


def _signature(row):
    stack = (row['stackTrace'] or '').replace('\\', '/')
    stack = re.sub(r'(?<= in )(?:[A-Za-z]:)?/[^\n]*?/(?=Assets/|Packages/|tests/)', '', stack)
    stack = re.sub(r':line \d+', '', stack)
    return row['state'], row['message'].replace('\r\n', '\n').strip(), _normalized(stack)


def compare(baseline, candidate, *, affected_tests, unrelated_failures, changed_paths):
    if not isinstance(affected_tests, list) or not affected_tests:
        raise ComparisonError('explicit complete affected-test selection required')
    if not isinstance(unrelated_failures, dict) or not isinstance(changed_paths, list):
        raise ComparisonError('explicit source/dependency reviews and changed paths required')
    paths = [_path(path) for path in changed_paths]
    affected = {_text(name, 'affected test') for name in affected_tests}
    before, after = baseline['groups'], candidate['groups']
    blockers, matched, reviewed, unresolved, skips, existing_skips, duplicates = [], [], [], [], [], [], []
    comparable = baseline['environment'] == candidate['environment'] and baseline['selection'] == candidate['selection']
    if not comparable:
        blockers.append('environment or selection differs')
    if baseline['commit'] == candidate['commit'] or baseline['run_id'] == candidate['run_id'] or baseline['trx_sha256'] == candidate['trx_sha256']:
        blockers.append('distinct baseline/candidate commits and raw runs required')
    for identity, rows in before.items():
        if len(after.get(identity, [])) < len(rows):
            blockers.append('baseline executions disappeared: ' + identity[0])
    observed = {name for name, _ in after}
    for name in affected - observed:
        blockers.append('affected test missing: ' + name)
    for identity, rows in sorted(after.items()):
        name, test_id = identity
        old = before.get(identity, [])
        if len(rows) > 1 or len(old) > 1:
            duplicates.append({'fullName': name, 'test_id': test_id,
                'baseline_executions': [r['execution_id'] for r in old],
                'candidate_executions': [r['execution_id'] for r in rows]})
            if any(r['state'] != 'Passed' for r in rows + old):
                blockers.append('ambiguous duplicate non-passing executions: ' + name)
                continue
        for row in rows:
            if row['state'] == 'Passed':
                continue
            if name in affected:
                blockers.append('affected test did not pass: ' + name)
            same = len(old) == 1 and _signature(row) == _signature(old[0])
            if row['state'] == 'NotExecuted':
                if not same:
                    skips.append(name)
                else:
                    existing_skips.append(name)
                continue
            if not same:
                unresolved.append(name)
                continue
            matched.append(name)
            review = unrelated_failures.get(name)
            if not comparable or not _reviewed(review) or name in affected or any(path in _signature(row)[2] for path in paths):
                unresolved.append(name)
            else:
                reviewed.append(name)
    if unresolved:
        blockers.append('new, changed, affected or unreviewed failures')
    if skips:
        blockers.append('new or changed skips')
    return {'mode': 'dotnet-test', 'baseline_commit': baseline['commit'], 'candidate_commit': candidate['commit'],
            'baseline_run': baseline['run_id'], 'candidate_run': candidate['run_id'],
            'baseline_trx_sha256': baseline['trx_sha256'], 'candidate_trx_sha256': candidate['trx_sha256'],
            'baseline_counts': baseline['counts'], 'candidate_counts': candidate['counts'],
            'draft_eligible': not blockers, 'full_suite_passed': candidate['counts']['failed'] == 0,
            'verdict': 'blocked' if blockers else ('passed_with_existing_failures' if reviewed else 'passed'),
            'matched_existing_failures': matched, 'reviewed_unrelated_failures': reviewed,
            'unresolved_failures': unresolved, 'new_or_changed_skips': skips,
            'existing_skips': existing_skips,
            'duplicate_executions': duplicates, 'blockers': blockers}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('baseline', 'candidate', 'scope'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args(argv)
    try:
        baseline = load_run(_read(args.baseline), base=Path(args.baseline).parent)
        candidate = load_run(_read(args.candidate), base=Path(args.candidate).parent)
        report = compare(baseline, candidate, **_read(args.scope))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report['draft_eligible'] else 1
    except (ComparisonError, OSError, ValueError, TypeError) as exc:
        print(json.dumps({'draft_eligible': False, 'verdict': 'gap', 'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    sys.exit(main())
