"""Retire only an unstarted duplicate source round; never relabel executed evidence."""
import json

from .ledger import COMMIT_SHA


def select_candidate(ledger, trees, reservation):
    """Immediately after acquire, before switch/identity/token/worker handover.

    The original request remains in audit. This lease has never reached an Editor
    or worker, so its first real execution can be the candidate at the same tree.
    A maintenance continuation or any started comparison keeps its exact pair.
    """
    identity = reservation['reservation_id']
    comparison = ledger.reservation_comparison(identity)
    if (not comparison or comparison.get('reuse_candidate_reservation')
            or reservation['mode'] != 'interactive'):
        return reservation
    candidate = comparison['candidate_commit']
    base = reservation['commit_sha']
    acquired = [json.loads(row['details']) for row in ledger.connection.execute(
        "SELECT details FROM audit WHERE item_id=? AND kind='reservation' AND reason='acquired'",
        (reservation['item_id'],))]
    if sum(row.get('reservation_id') == identity for row in acquired) != 1 or reservation['attempts']:
        return reservation
    # An old round or certified recovery may already contain real evidence.
    for row in ledger.reservations():
        if row['item_id'] != reservation['item_id'] or row['reservation_id'] == identity:
            continue
        pair = ledger.reservation_comparison(row['reservation_id'])
        if (row['kind'] == 'unity_slot' and (row['commit_sha'] == candidate
                or pair and pair['candidate_commit'] == candidate)):
            return reservation
    tree = trees.verification_tree('Farm-Client', base)
    if tree != trees.verification_tree('Farm-Client', candidate):
        return reservation
    if not isinstance(tree, str) or not COMMIT_SHA.fullmatch(tree):
        raise ValueError('equal source tree proof must be a full Git tree identity')
    with ledger._transaction():
        current = ledger.reservation(identity)
        item = ledger._row(reservation['item_id'])
        if (current['state'] != 'active' or current['commit_sha'] != base
                or current['acquired_at'] != reservation['acquired_at']
                or current['generation'] != item['generation'] or item['state'] != 'awaiting_resource'
                or item['worker_pid'] is not None or item['token'] is not None
                or ledger.reservation_comparison(identity) != comparison):
            return {**reservation, **current}
        latest = ledger.connection.execute("SELECT details FROM audit WHERE item_id=? AND kind='reservation' "
            "AND reason IN ('queued','requeued') ORDER BY id DESC", (item['id'],)).fetchall()
        request = next((json.loads(row['details']) for row in latest
                        if json.loads(row['details']).get('reservation_id') == identity), {})
        if request.get('baseline_resume_of') or request.get('continuation_of'):
            return {**reservation, **current}
        ledger.connection.execute('UPDATE reservations SET commit_sha=? WHERE reservation_id=?',
                                  (candidate, identity))
        ledger._audit(item['id'], 'verification_equal_tree', 'unstarted baseline replaced by actual candidate',
                      {'reservation_id': identity, 'baseline_commit': base, 'candidate_commit': candidate,
                       'tree': tree, 'baseline_executed': False})
        ledger._audit(item['id'], 'reservation', 'requeued', details={
            'reservation_id': identity, 'mode': current['mode'], 'commit_sha': candidate,
            'client_base_commit': base, 'equal_source_tree': tree})
        # acquire's secret exists only in this local value, never in the audit.
        return {**reservation, **ledger.reservation(identity)}
