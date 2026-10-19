"""Host operator's durable, temporary issue admission fence.

The audit record survives restart without rewriting candidates or queue order.
Only maintenance-resume can select/clear it; it never certifies acceptance.
"""
import json
import re

KIND = 'maintenance_issue_scope'


def entries(scope):
    return scope['issues'] if 'issues' in scope else [scope]


def issue_ids(scope):
    return [entry['issue_id'] for entry in entries(scope)]


def identifiers(scope):
    return [entry['identifier'] for entry in entries(scope)]


def public_scope(scope):
    if not scope:
        return {}
    names = identifiers(scope)
    return {'only_issue': names[0]} if len(names) == 1 else {'only_issues': names}


def valid_entry(value):
    return (isinstance(value, dict) and set(value) == {'issue_id', 'identifier'}
            and isinstance(value['issue_id'], str) and 0 < len(value['issue_id']) <= 200
            and isinstance(value['identifier'], str)
            and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,19}-[0-9]{1,12}', value['identifier']))


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
        if valid_entry(value):
            return value  # Preserve existing single-issue records and callers.
        if (not isinstance(value, dict) or set(value) != {'issues'}
                or not isinstance(value['issues'], list) or not 2 <= len(value['issues']) <= 32
                or not all(valid_entry(entry) for entry in value['issues'])
                or len(set(issue_ids(value))) != len(value['issues'])
                or len({name.upper() for name in identifiers(value)}) != len(value['issues'])):
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
    return row is not None and row['issue_id'] in issue_ids(scope)


def select(ledger, identifier):
    """Caller owns the maintenance transaction; no Linear or worker authority."""
    from .ledger import LedgerError
    if identifier is None:
        if ledger.connection.execute("SELECT 1 FROM audit WHERE item_id='maintenance' AND kind=? LIMIT 1", (KIND,)).fetchone():
            ledger._audit('maintenance', KIND, 'operator restored all-issue admission', details={'cleared': True})
        return
    names = [identifier] if isinstance(identifier, str) else identifier
    if (not isinstance(names, list) or not 1 <= len(names) <= 32
            or any(not isinstance(name, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,19}-[0-9]{1,12}', name)
                   for name in names) or len({name.upper() for name in names}) != len(names)):
        raise LedgerError('issue admission requires 1 to 32 distinct known issue identifiers')
    selected = []
    for name in names:
        rows = ledger.connection.execute("SELECT id,metadata FROM issues WHERE upper(json_extract(metadata,'$.identifier'))=upper(?)",
                                         (name,)).fetchall()
        if len(rows) != 1:
            raise LedgerError('issue admission requires exactly one known issue for each identifier')
        selected.append({'issue_id': rows[0]['id'], 'identifier': json.loads(rows[0]['metadata'])['identifier']})
    ids = [entry['issue_id'] for entry in selected]
    marks = ','.join('?' for _ in ids)
    if (ledger.connection.execute(f"SELECT 1 FROM work_items WHERE issue_id NOT IN ({marks}) AND state IN "
                                  "('queued','running','awaiting_input','awaiting_resource') "
                                  "AND (state='running' OR worker_pid IS NOT NULL)",
                                  ids).fetchone()
            or ledger.connection.execute("SELECT 1 FROM reservations r JOIN work_items w ON w.id=r.item_id "
                f"WHERE w.issue_id NOT IN ({marks}) AND r.state IN ('active','cancel_requested')", ids).fetchone()):
        raise LedgerError('settle other issue workers and active resources before scoped resume')
    ledger._audit('maintenance', KIND, 'operator selected issue acceptance',
                  details=selected[0] if len(selected) == 1 else {'issues': selected})
