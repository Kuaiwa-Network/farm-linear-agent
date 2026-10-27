"""Current attributed instructions, distinct from retained progress and evidence."""
import json


def current(ledger, item_id):
    rows = ledger.connection.execute("SELECT id,body,author_json,created_at FROM inbox WHERE item_id=? "
        "AND author_json IS NOT NULL ORDER BY id DESC LIMIT 1", (item_id,)).fetchall()
    from .ledger import _message
    return [_message(row) for row in rows]


def view(ledger, item_id):
    messages = current(ledger, item_id)
    saved = json.loads(ledger._row(item_id)['checkpoint']).get('request_scope')
    latest = messages[0]['id'] if messages else None
    return {'latest_message_id': latest, 'latest_request': messages[0] if messages else None,
            'recorded_scope': saved,
            'review_required': bool(latest and (not isinstance(saved, dict) or saved.get('through_message_id') != latest)),
            'history_is_evidence': True}


def validate(ledger, item_id, value):
    from .ledger import LedgerError
    latest = view(ledger, item_id)['latest_message_id']
    if (not isinstance(value, dict) or set(value) != {'through_message_id', 'summary'}
            or value['through_message_id'] != latest or not isinstance(value['summary'], str)
            or not 1 <= len(value['summary'].strip()) <= 2000):
        raise LedgerError('request_scope needs the latest attributed through_message_id and a bounded summary of current scope')


def require_reviewed(ledger, item_id):
    from .ledger import LedgerError
    if view(ledger, item_id)['review_required']:
        raise LedgerError('new attributed request: read issue-context and checkpoint request_scope before new tests or delivery')
