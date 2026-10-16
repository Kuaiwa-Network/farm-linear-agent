"""Claim-fenced source-object requests, serviced only by the host scheduler.

The Client sandbox never gains source writes. The host fetches the configured,
validated farmgui clone with hooks disabled; it never checks out or merges.
"""
import json
import re
from time import monotonic, sleep
from uuid import uuid4

from .fgui_records import _live
from .ledger import LedgerError
from .worktrees import HOOKS_OFF

REQUEST = 'fgui_source_history_requested'
RESULT = 'fgui_source_history_result'


def ensure_history(workflow, main, head, *, timeout=45):
    if workflow.trees.has_commit('farmgui', main):
        return
    request_id = uuid4().hex
    with workflow.ledger._transaction():
        row = _live(workflow.ledger, workflow.item_id, workflow.token, (None, 'farmgui', 'Farm-Client'))
        workflow.ledger._audit(workflow.item_id, REQUEST, details={
            'request_id': request_id, 'main': main, 'source_head': head,
            'generation': row['generation'], 'worker_pid': row['worker_pid'],
            'fingerprint': row['claimed_fingerprint'], 'claim_token_hash': row['token']})
    deadline = monotonic() + timeout
    while True:
        workflow.fence()
        if workflow.trees.has_commit('farmgui', main):
            workflow.fence(refresh=True)
            return
        result = workflow.ledger.connection.execute(
            "SELECT details FROM audit WHERE item_id=? AND kind=? AND json_extract(details,'$.request_id')=? "
            'ORDER BY id DESC LIMIT 1', (workflow.item_id, RESULT, request_id)).fetchone()
        if result and json.loads(result['details']).get('state') in ('failed', 'stale'):
            raise LedgerError('UI source history refresh failed; preserve evidence and report the host prerequisite gap')
        if monotonic() >= deadline:
            raise LedgerError('UI source history refresh pending; retry the same command in this repository stage')
        sleep(.25)


def refresh_pending(ledger, trees):
    """Host-only tick. Worker input can select no repository, URL or Git arguments."""
    pending = ledger.connection.execute(
        'SELECT a.item_id,a.details FROM audit a WHERE a.kind=? AND NOT EXISTS '
        "(SELECT 1 FROM audit r WHERE r.item_id=a.item_id AND r.kind=? "
        "AND json_extract(r.details,'$.request_id')=json_extract(a.details,'$.request_id')) "
        'ORDER BY a.id LIMIT 16', (REQUEST, RESULT)).fetchall()
    fetched = False
    for entry in pending:
        requested = json.loads(entry['details'])
        row = ledger._row(entry['item_id'])
        live = (row['skill'] == 'fgui' and row['state'] == 'running' and row['withdraw_deadline'] is None
                and row['lease_expires_at'] is not None and row['lease_expires_at'] > ledger.clock()
                and row['token'] == requested.get('claim_token_hash')
                and row['generation'] == requested.get('generation')
                and row['worker_pid'] == requested.get('worker_pid')
                and row['claimed_fingerprint'] == requested.get('fingerprint'))
        result = {'request_id': requested.get('request_id'), 'main': requested.get('main'), 'state': 'stale'}
        if live:
            try:
                if any(not isinstance(requested.get(key), str) or not re.fullmatch('[0-9a-f]{40}', requested[key])
                       for key in ('main', 'source_head')):
                    raise ValueError('source history requires full commit identities')
                if not trees.has_commit('farmgui', requested['main']) and not fetched:
                    fetched = True
                    trees.fetch('farmgui', config=HOOKS_OFF, timeout=60)
                if not trees.has_commit('farmgui', requested['main']):
                    raise ValueError('configured origin did not supply the requested main history')
                result['state'] = 'ready'
            except Exception as exc:
                result.update(state='failed', error_type=type(exc).__name__)
        with ledger._transaction():
            current = ledger._row(entry['item_id'])
            if (current['state'] != 'running' or current['withdraw_deadline'] is not None
                    or current['token'] != requested.get('claim_token_hash')
                    or current['lease_expires_at'] is None or current['lease_expires_at'] <= ledger.clock()
                    or current['generation'] != requested.get('generation')
                    or current['worker_pid'] != requested.get('worker_pid')
                    or current['claimed_fingerprint'] != requested.get('fingerprint')):
                result['state'] = 'stale'
            ledger._audit(entry['item_id'], RESULT, details=result)
    return len(pending)
