"""Cooperative server-service release without weakening all-attempt teardown."""
import json

from .execution import single

STAGE = 'settling-server-services'


def requested(ledger, item):
    row = ledger.connection.execute("SELECT details FROM audit WHERE item_id=? "
        "AND kind='server_resource_handoff' ORDER BY id DESC LIMIT 1", (item['id'],)).fetchone()
    return bool(row and json.loads(row['details']).get('generation') == item['generation'])


def pending_cleanup(ledger, item_id):
    """Only the current occupant counts, including a combined lease's server half."""
    for slot in ledger.slots(kind='server_slot'):
        if slot['state'] == 'idle_closed':
            continue
        reservation = ledger.connection.execute('SELECT r.* FROM reservations r '
            'LEFT JOIN integration_leases i ON i.reservation_id=r.reservation_id '
            'WHERE r.resource=? OR i.server_slot=? ORDER BY r.acquired_at DESC,r.sequence DESC LIMIT 1',
            (slot['slot_id'], slot['slot_id'])).fetchone()
        if (reservation and reservation['item_id'] == item_id
                and reservation['state'] in ('released', 'cancelled')):
            return True
    return False


def release(ledger, reservation_id, token):
    """Atomically release and retire the live claim; the pool alone recycles services.

    A current checkpoint must exist before the reservation token can retire a
    continuing worker. Cancellation/withdrawal/expired claims keep their existing
    recovery path and never become queued merely by releasing a resource.
    """
    from .integration import get as integration_request
    from .ledger import LedgerError, _in_scope
    with ledger._transaction():
        reservation = ledger._reservation_owned(reservation_id, token)
        item = ledger._row(reservation['item_id'])
        combined = integration_request(ledger, reservation_id)
        handoff = (single(item) and (reservation['kind'] == 'server_slot' or combined)
                   and reservation['state'] == 'active' and item['state'] == 'running'
                   and reservation['generation'] == item['generation']
                   and item['token'] and item['worker_pid']
                   and item['lease_expires_at'] > ledger.clock()
                   and item['withdraw_deadline'] is None
                   and _in_scope(json.loads(ledger._issue_row(item['issue_id'])['metadata'])))
        if handoff:
            checkpoint = json.loads(item['checkpoint'])
            meta = checkpoint.get('handoff_meta') or {}
            if (ledger.checkpoint_error(item['id']) or not checkpoint.get('handoff')
                    or meta.get('claim_token_hash') != item['token']
                    or meta.get('generation') != item['generation']
                    or meta.get('fingerprint') != item['claimed_fingerprint']
                    or meta.get('worker_id') != checkpoint.get('worker_id')
                    or meta.get('recorded_at', 0) < reservation['acquired_at']):
                raise LedgerError('save a current checkpoint with results and handoff after acquisition before releasing server services')
        ledger._release_in_transaction(reservation_id, token, 'worker reported quiescent')
        result = ledger.reservation(reservation_id)
        if handoff:
            ledger._audit(item['id'], 'server_resource_handoff', 'checkpointed server release; worker must exit',
                          {'generation': item['generation'], 'worker_pid': item['worker_pid'],
                           'reservation_id': reservation_id})
            ledger._set_state(item['id'], 'queued', 'server services require verified worker teardown',
                              stage=STAGE, token=None, lease_expires_at=None, worker_pid=None,
                              needs_resource=None)
            result.update(worker_handoff=True, next_action='Exit now. The claim is retired; the controller will verify '
                          'all worker attempts, recycle released services, and resume this checkpoint automatically.')
        return result
