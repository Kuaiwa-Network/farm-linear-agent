"""Claim-fenced progress and comparison of the controller-recorded Client pair."""
from pathlib import Path

from .config import Paths
from .ledger import LedgerError
from .test_baseline import ComparisonError, _read, compare
from .worktrees import WorktreeError

STAGES = {
    'preparation': 'client-preparing-verification',
    'testing': 'client-running-tests',
    'presentation-qa': 'client-presentation-qa',
    'failure-review': 'client-reviewing-test-failures',
    'comparison': 'client-comparing-test-results',
    'comparison-passed': 'client-verified-awaiting-publication',
    'comparison-blocked': 'client-verification-blocked',
    'comparison-gap': 'client-verification-evidence-gap',
    'publication': 'client-publication-check',
    'delivery': 'client-delivery-recording',
}


def compare_recorded_pair(args, ledger, config, token):
    ledger.renew(args.item, token)
    paths = Paths(config)
    if Path(args.db).resolve() != paths.ledger.resolve():
        raise LedgerError('comparison must use the configured host ledger')
    root = (paths.runs / args.item).resolve()
    files = [Path(value).resolve() for value in (args.baseline, args.candidate, args.scope)]
    original_path = getattr(args, 'repair_original', None)
    if original_path:
        files.append(Path(original_path).resolve())
    if any(not file.is_relative_to(root) for file in files):
        raise LedgerError('comparison inputs must be retained under this item state_dir')
    ledger.verification_progress(args.item, token, 'comparison')
    try:
        baseline, candidate, scope = [_read(file) for file in files[:3]]
        pair = ledger.client_verification_pair(args.item, candidate.get('commit'), require_candidate=True,
                                               candidate_reservation=candidate.get('reservation_id'))
        if (not pair or pair['baseline_commit'] != baseline.get('commit')
                or pair['baseline_reservation'] != baseline.get('reservation_id')
                or pair['candidate_reservation'] != candidate.get('reservation_id')):
            raise ComparisonError('evidence must name this item completed controller-selected pair')
        if original_path:
            from .repair_verification import compare_test_repair, verify_test_fixtures
            from .worktrees import Worktrees
            from .workflow import JobWorktrees
            repair = pair.get('repair')
            if not repair:
                raise ComparisonError('focused repair requires a controller-recorded repair reservation')
            trees = JobWorktrees(Worktrees(paths.repos, paths.worktrees, config.repos), ledger)
            branch = (ledger.staged_client_branch(args.item, issue_prefix=config.issue_prefix)
                      if ledger.item(args.item)['skill'] in ('work', 'feature') else None)
            delta = trees.verification_repair_delta('Farm-Client', args.item, candidate['commit'],
                                                    repair['source_commit'], expected_branch=branch)
            if delta != repair['changed_paths']:
                raise ComparisonError('repair source delta changed after admission')
            verify_test_fixtures(trees, args.item, repair, scope.get('repair'))
            report = compare_test_repair(baseline, _read(files[3]), candidate, scope, repair)
        else:
            report = compare(baseline, candidate, **scope)
    except (ComparisonError, WorktreeError, OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
        ledger.verification_progress(args.item, token, 'comparison-gap')
        raise ComparisonError(f'invalid or missing comparison evidence: {exc}') from exc
    ledger.verification_progress(args.item, token,
                                 'comparison-passed' if report['draft_eligible'] else 'comparison-blocked',
                                 report=report)
    return report
