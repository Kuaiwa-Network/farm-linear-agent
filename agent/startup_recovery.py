"""Host-only startup retries and persisted admission backpressure.

Fencing precedes process termination. Completion requires the scheduler's existing
all-attempt quiescence proof; neither a timeout nor a dead parent is that proof.
"""
import json

from .execution import single
from .maintenance import Maintenance

DELAYS = (60, 180, 600)
SCHEMA = """
CREATE TABLE IF NOT EXISTS worker_startup_recovery (
    item_id TEXT PRIMARY KEY REFERENCES work_items(id),
    generation INTEGER NOT NULL, worker_pid INTEGER NOT NULL,
    host TEXT NOT NULL, reason TEXT NOT NULL, requested_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS worker_startup_control (
    host TEXT PRIMARY KEY, failures INTEGER NOT NULL, last_failure_at REAL NOT NULL,
    cooldown_until REAL NOT NULL, probe_item TEXT, probe_generation INTEGER, probe_pid INTEGER
);
"""


class StartupRecovery:
    def __init__(self, ledger):
        self.ledger = ledger
        self.db = ledger.connection

    def pending(self, item_id):
        row = self.db.execute('SELECT * FROM worker_startup_recovery WHERE item_id=?', (item_id,)).fetchone()
        return dict(row) if row else None

    def begin(self, item, reason):
        """Atomically refuse a late claim before touching the exact owned attempt."""
        from .ledger import _in_scope
        with self.ledger._transaction():
            row = self.ledger._row(item['id'])
            if (row['state'] != 'queued' or row['worker_pid'] != item['worker_pid']
                    or row['generation'] != item['generation'] or not row['worker_pid']
                    or row['next_root_repo'] is not None or row['withdraw_deadline'] is not None
                    or not (single(row) or row['skill'] == 'chat')
                    or Maintenance(self.ledger).requested(row['id']) or Maintenance(self.ledger).pending(row['id'])
                    or not _in_scope(json.loads(self.ledger._issue_row(row['issue_id'])['metadata']))):
                return None
            existing = self.pending(row['id'])
            if existing:
                return existing
            now = self.ledger.clock()
            self.db.execute('INSERT INTO worker_startup_recovery VALUES(?,?,?,?,?,?)',
                            (row['id'], row['generation'], row['worker_pid'], row['host'], reason, now))
            control = self.db.execute('SELECT * FROM worker_startup_control WHERE host=?', (row['host'],)).fetchone()
            failures = (control['failures'] if control and
                        (control['failures'] >= 2 or now - control['last_failure_at'] <= 900) else 0) + 1
            delay = min(600, 180 * 2 ** min(failures - 2, 2)) if failures >= 2 else 0
            self.db.execute('INSERT OR REPLACE INTO worker_startup_control VALUES(?,?,?,?,NULL,NULL,NULL)',
                            (row['host'], failures, now, now + delay))
            self.ledger._audit(row['id'], 'startup_failure', reason,
                               {'pid': row['worker_pid'], 'generation': row['generation'],
                                'host': row['host'], 'cooldown_until': now + delay})
            return self.pending(row['id'])

    def complete(self, pending):
        """Caller has verified teardown; never revive a changed/cancelled/withdrawn item."""
        from .ledger import _in_scope
        with self.ledger._transaction():
            saved = self.pending(pending['item_id'])
            if saved != pending:
                return None
            row = self.ledger._row(pending['item_id'])
            if (row['state'] != 'queued' or row['worker_pid'] != pending['worker_pid']
                    or row['generation'] != pending['generation']):
                self.db.execute('DELETE FROM worker_startup_recovery WHERE item_id=?', (row['id'],))
                return None
            if row['withdraw_deadline'] is not None or not _in_scope(json.loads(
                    self.ledger._issue_row(row['issue_id'])['metadata'])):
                return None  # lifecycle owns cancellation; keep the late-claim fence meanwhile
            attempts = row['startup_retries']
            if attempts >= len(DELAYS):
                self.ledger._set_state(row['id'], 'failed', 'worker startup automatic retries exhausted (3)',
                                       token=None, lease_expires_at=None, worker_pid=None)
                result = {'status': 'exhausted', 'attempt': attempts}
            else:
                delay = DELAYS[attempts]
                self.ledger._set_state(row['id'], 'queued', 'worker startup; delayed automatic retry',
                                       token=None, lease_expires_at=None, worker_pid=None,
                                       startup_retries=attempts + 1, retry_not_before=self.ledger.clock() + delay)
                result = {'status': 'queued', 'attempt': attempts + 1, 'delay_seconds': delay}
            self.db.execute('DELETE FROM worker_startup_recovery WHERE item_id=?', (row['id'],))
            return result

    def may_launch(self, host):
        """After two failures, cool down and admit one unclaimed probe at a time."""
        with self.ledger._transaction():
            control = self.db.execute('SELECT * FROM worker_startup_control WHERE host=?', (host,)).fetchone()
            if not control or control['failures'] < 2:
                return True
            if control['cooldown_until'] > self.ledger.clock():
                return False
            if self.db.execute("SELECT 1 FROM work_items WHERE host=? AND state='queued' AND worker_pid IS NOT NULL",
                               (host,)).fetchone():
                return False
            # A cancelled/exited probe must not leave a permanent queue pause.
            if control['probe_item'] is not None:
                self.db.execute('UPDATE worker_startup_control SET probe_item=NULL,probe_generation=NULL,probe_pid=NULL '
                                'WHERE host=?', (host,))
            return True

    def launched(self, row, pid, host):
        """Called in set_worker's transaction, including a claim racing process registration."""
        self.db.execute('UPDATE worker_startup_control SET probe_item=?,probe_generation=?,probe_pid=? '
                        'WHERE host=? AND failures>=2 AND cooldown_until<=?',
                        (row['id'], row['generation'] + int(bool(row['requeue_requested'])),
                         pid, host, self.ledger.clock()))
        if row['state'] == 'running':
            self.claimed({**dict(row), 'worker_pid': pid, 'host': host})

    def claimed(self, row):
        """Only the selected probe's genuine claim restores ordinary admission."""
        changed = self.db.execute('DELETE FROM worker_startup_control WHERE host=? AND probe_item=? '
                                  'AND probe_generation=? AND probe_pid=?',
                                  (row['host'], row['id'], row['generation'], row['worker_pid'])).rowcount
        if changed:
            self.ledger._audit(row['id'], 'startup_admission_restored', 'probe claimed; normal admission restored')
