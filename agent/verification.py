"""Claim-fenced progress and comparison of the controller-recorded Client pair."""
from pathlib import Path

from .config import Paths
from .ledger import LedgerError
from .test_baseline import ComparisonError, _read, compare

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
    if any(not file.is_relative_to(root) for file in files):
        raise LedgerError('comparison inputs must be retained under this item state_dir')
    ledger.verification_progress(args.item, token, 'comparison')
    try:
        baseline, candidate, scope = [_read(file) for file in files]
        pair = ledger.client_verification_pair(args.item, candidate.get('commit'), require_candidate=True,
                                               candidate_reservation=candidate.get('reservation_id'))
        if (not pair or pair['baseline_commit'] != baseline.get('commit')
                or pair['baseline_reservation'] != baseline.get('reservation_id')
                or pair['candidate_reservation'] != candidate.get('reservation_id')):
            raise ComparisonError('evidence must name this item completed controller-selected pair')
        report = compare(baseline, candidate, **scope)
    except (ComparisonError, OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
        ledger.verification_progress(args.item, token, 'comparison-gap')
        raise ComparisonError(f'invalid or missing comparison evidence: {exc}') from exc
    ledger.verification_progress(args.item, token,
                                 'comparison-passed' if report['draft_eligible'] else 'comparison-blocked',
                                 report=report)
    return report
