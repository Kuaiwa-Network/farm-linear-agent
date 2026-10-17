"""Resume a maintenance-interrupted comparison, never select a new source base."""
import json


def pinned_baseline(ledger, item_id, candidate, reservation_id, *, mode=None):
    from .ledger import LedgerError
    item = ledger.item(item_id)
    source = ledger.reservation(reservation_id)
    comparison = ledger.reservation_comparison(reservation_id) if source else None
    if (item['skill'] not in ('work', 'feature', 'fix') or item['root_repo'] != 'Farm-Client'
            or not source or source['item_id'] != item_id or source['generation'] != item['generation']
            or source['kind'] != 'unity_slot' or source['state'] != 'released'
            or source['host'] != item['host']
            or source['release_reason'] != 'worker reported quiescent'
            or (mode is not None and source['mode'] != mode)
            or not comparison or comparison['candidate_commit'] != candidate):
        raise LedgerError('baseline resume requires this generation exact cleanly released comparison')
    # A later round or diagnostic owns its evidence. Never go back to an older
    # reservation just because it has a more convenient result or upstream pin.
    if ledger.connection.execute("SELECT 1 FROM reservations WHERE item_id=? AND kind='unity_slot' "
            "AND sequence>?", (item_id, source['sequence'])).fetchone():
        raise LedgerError('baseline resume requires the latest Unity reservation')
    handoff = ledger.connection.execute('SELECT generation,completed_at FROM maintenance_handoffs WHERE item_id=?',
                                       (item_id,)).fetchone()
    if (not handoff or handoff['generation'] != item['generation'] or handoff['completed_at'] is None
            or source['released_at'] is None or handoff['completed_at'] < source['released_at']):
        raise LedgerError('baseline resume requires completed maintenance handoff after release')
    for row in ledger.connection.execute("SELECT details FROM audit WHERE item_id=? "
            "AND kind='verification_comparison' AND created_at>=?", (item_id, source['created_at'])):
        details = json.loads(row['details'])
        if details.get('candidate_commit') == candidate and details.get('baseline_commit') == source['commit_sha']:
            raise LedgerError('comparison already reviewed; reuse its reports, do not rerun baseline')
    retained = comparison.get('reuse_candidate_reservation')
    if retained is not None:
        tested = ledger.reservation(retained)
        if (not tested or tested['item_id'] != item_id or tested['generation'] != item['generation']
                or tested['kind'] != 'unity_slot' or tested['mode'] != source['mode']
                or tested['commit_sha'] != candidate or tested['state'] != 'released'
                or tested['release_reason'] != 'worker reported quiescent'
                or tested['sequence'] >= source['sequence'] or ledger.reservation_comparison(retained)):
            raise LedgerError('baseline resume cannot change or weaken its retained candidate binding')
    return {'baseline_commit': source['commit_sha'], 'candidate_commit': candidate,
            'baseline_reservation': reservation_id, 'mode': source['mode'],
            'reuse_candidate_reservation': retained}


def retained_baseline_report(ledger, item_id, pair, reported_id):
    """Old report IDs survive only an explicit, clean maintenance-resume chain.

    Each report still undergoes ordinary complete-run, selection, environment and
    failure comparison. Never rewrite it to the resumed reservation's identity.
    """
    current = ledger.reservation(pair['baseline_reservation'])
    if not current:
        return False
    expected = {k: current[k] for k in ('item_id', 'generation', 'kind', 'mode', 'host', 'commit_sha')}
    if expected['item_id'] != item_id or expected['kind'] != 'unity_slot' or expected['commit_sha'] != pair['baseline_commit']:
        return False
    for _ in range(100):
        comparison = ledger.reservation_comparison(current['reservation_id'])
        if (any(current[k] != v for k, v in expected.items()) or current['state'] != 'released'
                or current['release_reason'] != 'worker reported quiescent'
                or not comparison or comparison['candidate_commit'] != pair['candidate_commit']):
            return False
        if current['reservation_id'] == reported_id:
            return True
        link = None
        for row in ledger.connection.execute("SELECT details FROM audit WHERE item_id=? AND kind='reservation' "
                "AND reason IN ('queued','requeued') ORDER BY id DESC", (item_id,)):
            details = json.loads(row['details'])
            if details.get('reservation_id') == current['reservation_id']:
                link = details.get('baseline_resume_of')
                break
        parent = ledger.reservation(link) if isinstance(link, str) else None
        if not parent or parent['sequence'] >= current['sequence']:
            return False
        current = parent
    return False
