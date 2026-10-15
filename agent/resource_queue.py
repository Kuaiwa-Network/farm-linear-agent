"""Read-only resource ordering, shared by admission and Monitor.

A candidate atomically queued by its baseline's release may use that slot's
next turn. The exception expires once another reservation has used the slot.
Reservation sequence remains immutable verification history.
"""
import json


def last_on_slot(db, slot_id):
    """Acquisition order, not queue sequence: a continuation can overtake older requests."""
    rows = list(db.execute('SELECT * FROM reservations WHERE resource=? '
                           'ORDER BY acquired_at DESC,sequence DESC', (slot_id,)))
    if not rows:
        return None
    tied = [row for row in rows if row['acquired_at'] == rows[0]['acquired_at']]
    if len(tied) == 1:
        return tied[0]
    def acquisition(row):
        for event in db.execute("SELECT id,details FROM audit WHERE item_id=? AND kind='reservation' "
                                "AND reason='acquired' ORDER BY id DESC", (row['item_id'],)):
            try:
                details = json.loads(event['details'])
            except (ValueError, TypeError):
                continue
            if isinstance(details, dict) and details.get('reservation_id') == row['reservation_id']:
                return event['id'], row['sequence']
        return -1, row['sequence']
    return max(tied, key=acquisition)


def _request(db, row):
    for event in db.execute("SELECT details FROM audit WHERE item_id=? AND kind='reservation' "
                            "AND reason IN ('queued','requeued') ORDER BY id DESC", (row['item_id'],)):
        try:
            value = json.loads(event['details'])
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and value.get('reservation_id') == row['reservation_id']:
            return value if value.get('commit_sha') == row['commit_sha'] else {}
    return {}


def continuation_slot(db, row):
    if row['kind'] != 'unity_slot' or row['mode'] != 'interactive' or row['state'] != 'queued':
        return None
    source_id = _request(db, row).get('continuation_of')
    if not isinstance(source_id, str):
        return None
    source = db.execute('SELECT * FROM reservations WHERE reservation_id=?', (source_id,)).fetchone()
    if (source is None or source['item_id'] != row['item_id'] or source['generation'] != row['generation']
            or source['kind'] != 'unity_slot' or source['mode'] != 'interactive'
            or source['state'] != 'released' or source['release_reason'] != 'worker reported quiescent'
            or source['sequence'] >= row['sequence'] or source['commit_sha'] == row['commit_sha']
            or _request(db, source).get('comparison_candidate') != row['commit_sha']):
        return None
    slot = db.execute('SELECT * FROM slots WHERE slot_id=?', (source['resource'],)).fetchone()
    last = last_on_slot(db, source['resource'])
    work = db.execute('SELECT state,generation,withdraw_deadline,host FROM work_items WHERE id=?',
                      (row['item_id'],)).fetchone()
    if (not slot or slot['state'] not in ('switching', 'idle_open', 'idle_closed')
            or not last or last['reservation_id'] != source_id or not work
            or work['state'] != 'awaiting_resource' or work['generation'] != row['generation']
            or work['withdraw_deadline'] is not None or work['host'] != source['host']):
        return None
    return source['resource']


def queued_rows(db, *, kind=None):
    query = "SELECT * FROM reservations WHERE state='queued'"
    values = ()
    if kind is not None:
        query += ' AND kind=?'
        values = (kind,)
    rows = list(db.execute(query + ' ORDER BY sequence', values))
    return sorted(rows, key=lambda row: (0 if continuation_slot(db, row) else 1, row['sequence']))
