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
        if pair and pair['baseline_reservation'] != baseline.get('reservation_id') and not original_path:
            from .baseline_resume import retained_baseline_report
            if retained_baseline_report(ledger, args.item, pair, baseline.get('reservation_id')):
                pair = {**pair, 'baseline_reservation': baseline['reservation_id']}
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
            from .shared_test_repairs import record
            record(ledger, trees, args.item, _read(files[3]), candidate, report, token=token)
        else:
            report = compare(baseline, candidate, **scope)
        if report.get('confirmed_unrelated_failures'):
            if original_path:
                raise ComparisonError('defer unrelated findings on the original unchanged pair, separately from a repair')
            from .shared_test_findings import verify
            from .worktrees import Worktrees
            from .workflow import JobWorktrees, repository_path
            trees = JobWorktrees(Worktrees(paths.repos, paths.worktrees, config.repos), ledger)
            branch = (ledger.staged_client_branch(args.item, issue_prefix=config.issue_prefix)
                      if ledger.item(args.item)['skill'] in ('work', 'feature') else None)
            trees.verification_commit('Farm-Client', args.item, candidate['commit'], expected_branch=branch)
            verify(trees, root, baseline, candidate, scope, report, repository_path(trees, 'Farm-Client', args.item))
    except (ComparisonError, WorktreeError, OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
        ledger.verification_progress(args.item, token, 'comparison-gap')
        raise ComparisonError(f'invalid or missing comparison evidence: {exc}') from exc
    ledger.verification_progress(args.item, token,
                                 'comparison-passed' if report['draft_eligible'] else 'comparison-blocked',
                                 report=report)
    return report


def verify_recorded_candidate(args, ledger, config, token):
    """A real candidate-only round with reviewed scope, never a synthetic test pair."""
    from .candidate_verification import assess
    from .shared_test_findings import verify
    from .worktrees import GIT_ENV, Worktrees
    from .workflow import JobWorktrees, repository_path

    ledger.renew(args.item, token)
    paths = Paths(config)
    if Path(args.db).resolve() != paths.ledger.resolve():
        raise LedgerError('candidate verification must use the configured host ledger')
    root = (paths.runs / args.item).resolve()
    files = [Path(value).resolve() for value in (args.candidate, args.scope)]
    if any(not file.is_relative_to(root) for file in files):
        raise LedgerError('candidate evidence must be retained under this item state_dir')
    ledger.verification_progress(args.item, token, 'failure-review')
    try:
        candidate, scope = [_read(file) for file in files]
        pin = ledger.client_candidate_round(args.item, candidate.get('commit'))
        if (not pin or pin['candidate_reservation'] != candidate.get('reservation_id')
                or pin['base_commit'] != scope.get('source_base_commit')):
            raise ComparisonError('evidence must name the current cleanly released candidate-only round; '
                                  'an existing comparison must retain its actual pair')
        trees = JobWorktrees(Worktrees(paths.repos, paths.worktrees, config.repos), ledger)
        branch = (ledger.staged_client_branch(args.item, issue_prefix=config.issue_prefix)
                  if ledger.item(args.item)['skill'] in ('work', 'feature') else None)
        trees.verification_commit('Farm-Client', args.item, candidate['commit'], expected_branch=branch)
        path = repository_path(trees, 'Farm-Client', args.item)
        delta = trees.git_in(path, 'diff', '--name-only', '--no-renames', '-z',
                            pin['base_commit'], candidate['commit'],
                            env={**GIT_ENV, 'GIT_NO_REPLACE_OBJECTS': '1'}).split('\0')
        if set(filter(None, delta)) != set(scope.get('changed_paths', [])):
            raise ComparisonError('candidate scope must cover the complete actual source diff')
        # This proof is host-derived, not a field a worker can assert in its scope.
        same_tree = None
        if not any(delta):
            base_tree = trees.verification_tree('Farm-Client', pin['base_commit'])
            if base_tree == trees.verification_tree('Farm-Client', candidate['commit']):
                same_tree = base_tree
        report = assess(candidate, **scope, verified_source_tree=same_tree)
        # This object is a Git source identity only. No baseline run, job,
        # reservation or passing result is constructed or recorded.
        verify(trees, root, {'commit': pin['base_commit']}, candidate, scope, report, path)
    except (ComparisonError, WorktreeError, OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
        ledger.verification_progress(args.item, token, 'comparison-gap')
        raise ComparisonError(f'invalid or missing candidate evidence: {exc}') from exc
    ledger.verification_progress(args.item, token,
                                 'comparison-passed' if report['draft_eligible'] else 'comparison-blocked',
                                 report=report)
    return report
