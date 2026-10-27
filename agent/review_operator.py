"""Explicit host controls. Resume opens the circuit, never revives delivered handoffs."""
import os
from .config import Paths
from .environment import check_ownership
from .ledger import Ledger
from .readonly_db import snapshot_connection
from .review_policy import settings
from .review_store import ReviewStore, summary


def control(config, command, *, reason=None, run=None, event=None, kind=None):
    check_ownership(config, require_initialized=True)
    path = Paths(config).ledger
    if command == 'review-status':
        with snapshot_connection(path) as db:
            return summary(db)
    if os.environ.get('FARMBOT_ITEM_ID'):
        raise ValueError('review control mutations are host-operator commands, not worker commands')
    if not reason:
        raise ValueError('review control requires an operator reason')
    ledger = Ledger(path)
    try:
        if command in ('review-pause', 'review-resume'):
            with ledger._transaction():
                ledger.connection.execute('UPDATE review_control SET paused=?,reason=?',
                                          (int(command == 'review-pause'), reason))
                ledger._audit('review-control', command.replace('-', '_'), reason)
        elif command == 'review-incident':
            if not run or not event or kind not in ('revert', 'reopen'):
                raise ValueError('review-incident needs --review-run, --event-id and --incident-kind revert|reopen')
            ReviewStore(ledger).incident(event, run, kind, settings(config.auto_merge, config.repos))
            ledger.note(ledger.connection.execute('SELECT item_id FROM review_runs WHERE id=?', (run,)).fetchone()[0],
                        'review_incident', reason)
        else:
            raise ValueError('unknown review control command')
        return summary(ledger.connection)
    finally:
        ledger.close()
