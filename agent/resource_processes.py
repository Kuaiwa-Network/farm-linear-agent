"""Reservation-scoped process proofs. Never replace missing proof with a dead PID."""
import json
import os
from uuid import uuid4

SCHEMA = """
CREATE TABLE IF NOT EXISTS resource_process_scopes (
 reservation_id TEXT PRIMARY KEY REFERENCES reservations(reservation_id),
 item_id TEXT NOT NULL, generation INTEGER NOT NULL, claim_hash TEXT NOT NULL,
 closed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS resource_commands (
 id TEXT PRIMARY KEY, reservation_id TEXT NOT NULL REFERENCES resource_process_scopes(reservation_id),
 state TEXT NOT NULL, identity TEXT, started_at REAL NOT NULL, ended_at REAL
);
CREATE INDEX IF NOT EXISTS resource_commands_scope ON resource_commands(reservation_id);
"""


def scope(ledger, reservation_id):
    row = ledger.connection.execute('SELECT * FROM resource_process_scopes WHERE reservation_id=?',
                                    (reservation_id,)).fetchone()
    return dict(row) if row else None


def create(ledger, reservation_id, item):
    ledger.connection.execute('INSERT INTO resource_process_scopes VALUES(?,?,?,?,0)',
        (reservation_id, item['id'], item['generation'], item['token']))


def native_empty(identity):
    from .launcher import Launcher
    if not isinstance(identity, dict) or type(identity.get('pid')) is not int or identity['pid'] <= 0:
        return False
    if os.name == 'nt':
        from .windows_job import WindowsJob
        return bool(identity.get('windows_job') and WindowsJob.empty(identity['windows_job']))
    if identity.get('process_group') != identity['pid'] or identity.get('escaped'):
        return False
    # No signal sent by the controller from a recovered PID. Reuse only inhibits
    # cleanup; the owning creation object already stopped and reaped this group.
    return (not Launcher.alive(identity['pid']) and Launcher._group_gone(identity['pid'], timeout=0)
            and not any(Launcher.alive(pid) for pid in identity.get('descendants', [])))


def quiescent(ledger, reservation_id, *, sealed=True):
    if sealed and os.name != 'nt':
        return False  # POSIX process groups cannot contain a child moving groups.
    saved = scope(ledger, reservation_id)
    if not saved or sealed and not saved['closed']:
        return False
    reservation = ledger.reservation(reservation_id)
    if not reservation or (saved['item_id'], saved['generation']) != (reservation['item_id'], reservation['generation']):
        return False
    for row in ledger.connection.execute('SELECT * FROM resource_commands WHERE reservation_id=?', (reservation_id,)):
        if row['state'] != 'quiescent' or not row['identity'] or not native_empty(json.loads(row['identity'])):
            return False
    return True


def seal(ledger, reservation, item):
    from .ledger import LedgerError
    saved = scope(ledger, reservation['reservation_id'])
    if not saved:
        return False  # Historical grants retain the all-worker handoff path.
    if (saved['claim_hash'] != item['token'] or saved['generation'] != item['generation']
            or saved['item_id'] != item['id'] or reservation['state'] != 'active'):
        raise LedgerError('resource process scope belongs to a different claim')
    if not quiescent(ledger, reservation['reservation_id'], sealed=False):
        raise LedgerError('service commands still active or teardown uncertain; retain evidence and release unclean')
    if os.name != 'nt':
        return False  # Keep the outer worker-session proof on POSIX.
    ledger.connection.execute('UPDATE resource_process_scopes SET closed=1 WHERE reservation_id=?',
                              (reservation['reservation_id'],))
    return True


class Commands:
    def __init__(self, ledger, item_id, token, reservation_id):
        self.ledger, self.item_id, self.token, self.reservation_id = ledger, item_id, token, reservation_id

    def command(self, argv, **options):
        from .integration_runtime import Command
        from .ledger import LedgerError
        from .maintenance import Maintenance
        from .request_scope import require_reviewed
        db, key = self.ledger.connection, str(uuid4())
        def check():
            item = self.ledger._owned(self.item_id, self.token)
            self.ledger._refuse_withdrawn(item)
            require_reviewed(self.ledger, self.item_id)
            saved = scope(self.ledger, self.reservation_id)
            reservation = self.ledger.reservation(self.reservation_id)
            from .integration import companion
            paired = companion(self.ledger, reservation) if reservation else None
            slots_ready = bool(reservation and reservation['resource'] and
                self.ledger.slot(reservation['resource'])['state'] ==
                    ('batch_busy' if reservation['kind'] == 'server_slot' else 'interactive_busy') and
                (not paired or self.ledger.slot(paired['resource'])['state'] == 'batch_busy'))
            if (not slots_ready or not saved or saved['closed'] or saved['claim_hash'] != item['token']
                    or saved['item_id'] != item['id'] or saved['generation'] != item['generation'] or not reservation
                    or reservation['state'] != 'active' or Maintenance(self.ledger).requested(self.item_id)):
                raise LedgerError('service command has no current open process scope')
        with self.ledger._transaction():
            check()
            # Persist before spawning: a crash between creation and identification
            # retains an incomplete entry, which can only use whole-worker recovery.
            db.execute('INSERT INTO resource_commands VALUES(?,?,?,NULL,?,NULL)',
                       (key, self.reservation_id, 'starting', self.ledger.clock()))
        def identified(child):
            with self.ledger._transaction():
                check()
                db.execute("UPDATE resource_commands SET state='running',identity=? WHERE id=?",
                           (json.dumps(child.identity()), key))
        child = Command(argv, identified=identified, **options)
        stop = child.stop
        def stopped():
            try:
                stop()  # native containment must complete before recording success
            except BaseException:
                with self.ledger._transaction():
                    db.execute("UPDATE resource_commands SET state='uncertain',identity=? WHERE id=?",
                               (json.dumps(child.identity()), key))
                raise
            with self.ledger._transaction():
                db.execute("UPDATE resource_commands SET state='quiescent',identity=?,ended_at=? WHERE id=?",
                           (json.dumps(child.identity()), self.ledger.clock(), key))
        child.stop = stopped
        return child
