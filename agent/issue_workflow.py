"""Durable, controller-owned Linear status updates for delegated issue work."""
from .router import WRITE_SKILLS


SCHEMA = """
CREATE TABLE IF NOT EXISTS issue_workflow_updates (
    issue_id TEXT PRIMARY KEY REFERENCES issues(id),
    item_id TEXT NOT NULL REFERENCES work_items(id),
    generation INTEGER NOT NULL,
    target TEXT NOT NULL CHECK (target IN ('In Progress', 'In Review')),
    revision INTEGER NOT NULL DEFAULT 1,
    synced INTEGER NOT NULL DEFAULT 0 CHECK (synced IN (0,1,2)),
    error TEXT
);
"""
# synced: 0 waits for Linear, 1 was applied/already correct, 2 was skipped.


def request_update(ledger, row, target):
    """Called inside the worker-start/delivery transaction; never contacts Linear.

    Repeated launches in the same generation do not overwrite a human's later
    status choice. A new generation or delivery creates a new transition.
    """
    if row['parent_id'] or row['skill'] not in WRITE_SKILLS or row['authority'] not in (None, 'delegation'):
        return
    changed = ledger.connection.execute("""INSERT INTO issue_workflow_updates
        (issue_id,item_id,generation,target) VALUES(?,?,?,?)
        ON CONFLICT(issue_id) DO UPDATE SET item_id=excluded.item_id,
            generation=excluded.generation,target=excluded.target,revision=revision+1,synced=0,error=NULL
        WHERE item_id!=excluded.item_id OR generation!=excluded.generation OR target!=excluded.target OR synced=2""",
        (row['issue_id'], row['id'], row['generation'], target)).rowcount
    if changed:
        ledger.connection.execute("UPDATE issue_checks SET requested=1,due_at=0 WHERE issue_id=?", (row['issue_id'],))
        ledger._audit(row['id'], 'issue_status_requested', details={'target': target, 'generation': row['generation']})


def _current(ledger, update):
    pending = ledger.connection.execute("SELECT * FROM issue_workflow_updates WHERE issue_id=?",
                                        (update['issue_id'],)).fetchone()
    if not pending or pending['revision'] != update['revision'] or pending['synced']:
        return False
    item = ledger.item(update['item_id'])
    states = ('delivered',) if update['target'] == 'In Review' else (
        'queued', 'running', 'awaiting_input', 'awaiting_resource')
    if (item['generation'] != update['generation'] or item['state'] not in states
            or item['withdraw_deadline'] is not None):
        return False
    # A queued successor may exist before its first worker has started.
    return not ledger.connection.execute("SELECT 1 FROM work_items WHERE issue_id=? AND parent_id IS NULL "
        "AND predecessor_id=? LIMIT 1", (item['issue_id'], item['id'])).fetchone()


def reconcile(ledger, api, issue_id):
    """Retry pending transitions after a successful lifecycle read, even after delivery."""
    row = ledger.connection.execute("SELECT * FROM issue_workflow_updates WHERE issue_id=? AND synced=0",
                                    (issue_id,)).fetchone()
    if row is None:
        return
    update = dict(row)
    try:
        applied = (_current(ledger, update) and api.set_issue_workflow_state(
            issue_id, update['target'], still_current=lambda: _current(ledger, update)))
    except Exception as exc:
        # Only the exception's class is retained: transports can include secrets.
        with ledger._transaction():
            changed = ledger.connection.execute("UPDATE issue_workflow_updates SET error=? "
                "WHERE issue_id=? AND revision=? AND synced=0",
                (type(exc).__name__, issue_id, update['revision'])).rowcount
            if changed:
                ledger._audit(update['item_id'], 'issue_status_sync_failed', type(exc).__name__,
                              {'target': update['target']})
        return
    with ledger._transaction():
        changed = ledger.connection.execute("UPDATE issue_workflow_updates SET synced=?,error=NULL "
            "WHERE issue_id=? AND revision=? AND synced=0", (1 if applied else 2, issue_id, update['revision'])).rowcount
        if changed:
            ledger._audit(update['item_id'], 'issue_status_synced',
                          details={'target': update['target'], 'applied': bool(applied)})
