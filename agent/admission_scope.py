"""Host operator's durable, temporary single-issue admission fence.

The audit record survives restart without rewriting candidates or queue order.
Only maintenance-resume can select/clear it; it never certifies acceptance.
"""
import json
import re

KIND = 'maintenance_issue_scope'


def read(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='audit'").fetchone():
        return None
    if not {'id', 'item_id', 'kind', 'details'} <= {row[1] for row in db.execute('PRAGMA table_info(audit)')}:
        return None
    row = db.execute("SELECT details FROM audit WHERE item_id='maintenance' AND kind=? ORDER BY id DESC LIMIT 1",
                     (KIND,)).fetchone()
    if row is None:
        return None
    try:
        value = json.loads(row['details'])
        if isinstance(value, dict) and set(value) == {'cleared'} and value['cleared'] is True:
            return None
        if (not isinstance(value, dict) or set(value) != {'issue_id', 'identifier'}
                or not isinstance(value['issue_id'], str) or not 0 < len(value['issue_id']) <= 200
                or not isinstance(value['identifier'], str)
                or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,19}-[0-9]{1,12}', value['identifier'])):
            raise ValueError()
        return value
    except (ValueError, TypeError, RecursionError):
        from .ledger import LedgerError
        raise LedgerError('operator issue admission scope is damaged; keep admission paused') from None


def allows(db, item_id):
    scope = read(db)
    if scope is None:
        return True
    row = db.execute('SELECT issue_id FROM work_items WHERE id=?', (item_id,)).fetchone()
    return row is not None and row['issue_id'] == scope['issue_id']


def select(ledger, identifier):
    """Caller owns the maintenance transaction; no Linear or worker authority."""
    from .ledger import LedgerError
    if identifier is None:
        if ledger.connection.execute("SELECT 1 FROM audit WHERE item_id='maintenance' AND kind=? LIMIT 1", (KIND,)).fetchone():
            ledger._audit('maintenance', KIND, 'operator restored all-issue admission', details={'cleared': True})
        return
    if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,19}-[0-9]{1,12}', identifier):
        raise LedgerError('single-issue admission requires a known issue identifier')
    rows = ledger.connection.execute("SELECT id,metadata FROM issues WHERE upper(json_extract(metadata,'$.identifier'))=upper(?)",
                                     (identifier,)).fetchall()
    if len(rows) != 1:
        raise LedgerError('single-issue admission requires exactly one known issue')
    selected = {'issue_id': rows[0]['id'], 'identifier': json.loads(rows[0]['metadata'])['identifier']}
    if (ledger.connection.execute("SELECT 1 FROM work_items WHERE issue_id<>? AND state IN "
                                  "('queued','running','awaiting_input','awaiting_resource') "
                                  "AND (state='running' OR worker_pid IS NOT NULL)",
                                  (selected['issue_id'],)).fetchone()
            or ledger.connection.execute("SELECT 1 FROM reservations r JOIN work_items w ON w.id=r.item_id "
                "WHERE w.issue_id<>? AND r.state IN ('active','cancel_requested')", (selected['issue_id'],)).fetchone()):
        raise LedgerError('settle other issue workers and active resources before single-issue resume')
    ledger._audit('maintenance', KIND, 'operator selected single-issue acceptance', details=selected)
