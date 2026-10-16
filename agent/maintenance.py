"""Durable, cooperative host maintenance. No process termination or lease timeout bypass."""
import json
import re
from uuid import uuid4

SCHEMA = """
CREATE TABLE IF NOT EXISTS maintenance_control (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    request_id TEXT NOT NULL, requested_at REAL NOT NULL, reason TEXT NOT NULL,
    checked_at REAL, blockers TEXT
);
CREATE TABLE IF NOT EXISTS maintenance_handoffs (
    item_id TEXT PRIMARY KEY REFERENCES work_items(id),
    generation INTEGER NOT NULL, worker_pid INTEGER NOT NULL,
    requested_at REAL NOT NULL, completed_at REAL
);
CREATE TABLE IF NOT EXISTS maintenance_attempts (
    request_id TEXT NOT NULL, item_id TEXT NOT NULL REFERENCES work_items(id),
    worker_pid INTEGER NOT NULL,
    PRIMARY KEY(request_id,item_id,worker_pid)
);
"""


def request(db):
    """Also usable on old, read-only databases. Never creates or migrates state."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='maintenance_control'").fetchone():
        return None
    row = db.execute("SELECT * FROM maintenance_control WHERE singleton=1").fetchone()
    return dict(row) if row else None


class Maintenance:
    def __init__(self, ledger):
        self.ledger = ledger
        self.db = ledger.connection

    def requested(self):
        return request(self.db) is not None

    def pending(self, item_id):
        return self.db.execute("SELECT 1 FROM maintenance_handoffs WHERE item_id=? AND completed_at IS NULL",
                               (item_id,)).fetchone() is not None

    def pause(self, reason):
        from .ledger import LedgerError
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
            raise LedgerError("maintenance pause requires a reason of 1 to 500 characters")
        with self.ledger._transaction():
            if not self.requested():
                self.db.execute("INSERT INTO maintenance_control VALUES(1,?,?,?,NULL,NULL)",
                                (str(uuid4()), self.ledger.clock(), reason.strip()))
                self.ledger._audit('maintenance', "maintenance_pause", reason.strip())
            # Include workers which just parked and cleared worker_pid but have not exited yet.
            for row in self.db.execute("SELECT id FROM work_items WHERE state IN "
                                       "('queued','running','awaiting_input','awaiting_resource')").fetchall():
                self.track(row['id'], self.ledger.last_worker_pid(row['id']))
        return request(self.db)

    def resume(self):
        with self.ledger._transaction():
            if self.requested():
                self.ledger._audit('maintenance', "maintenance_resume", "operator resumed scheduling")
                self.db.execute("DELETE FROM maintenance_control")
                self.db.execute("DELETE FROM maintenance_attempts")
        # Pending handoffs survive resume and still require proven teardown.

    def track(self, item_id, pid):
        current = request(self.db)
        if current and pid:
            self.db.execute("INSERT OR IGNORE INTO maintenance_attempts VALUES(?,?,?)",
                            (current['request_id'], item_id, pid))

    def may_launch(self, item_id):
        if self.pending(item_id):
            return False
        # An already admitted reservation needs its worker to collect results and release it.
        # The worker sees maintenance in claim and must yield without starting another test.
        return not self.requested() or self.ledger.active_reservation(item_id) is not None

    def handoff(self, item_id, token):
        from .ledger import LedgerError
        with self.ledger._transaction():
            row = self.ledger._owned(item_id, token)
            if not self.requested():
                raise LedgerError("maintenance is not requested; continue the current claim")
            self.ledger.require_valid_checkpoint(item_id, token)
            checkpoint = json.loads(row['checkpoint'])
            meta = checkpoint.get('handoff_meta') or {}
            if (not checkpoint.get('handoff') or meta.get('claim_token_hash') != row['token']
                    or meta.get('generation') != row['generation']
                    or meta.get('worker_id') != checkpoint.get('worker_id')
                    or meta.get('fingerprint') != row['claimed_fingerprint']):
                raise LedgerError("save a current worker checkpoint before maintenance handoff")
            if not row['worker_pid']:
                raise LedgerError("maintenance handoff requires a recorded worker process")
            if self.db.execute("SELECT 1 FROM reservations WHERE item_id=? AND state IN "
                               "('queued','active','cancel_requested')", (item_id,)).fetchone():
                raise LedgerError("settle and release resources before maintenance handoff")
            self.db.execute("INSERT OR REPLACE INTO maintenance_handoffs VALUES(?,?,?,?,NULL)",
                            (item_id, row['generation'], row['worker_pid'], self.ledger.clock()))
            self.track(item_id, row['worker_pid'])
            # Keep generation, stage, root, candidate, evidence and all retry allowances exactly as saved.
            self.ledger._set_state(item_id, 'queued', 'maintenance handoff requested',
                                   token=None, lease_expires_at=None)
            self.ledger._audit(item_id, 'maintenance_handoff_requested')
        return {**self.ledger.item(item_id), 'next_action': 'exit; controller must verify process teardown'}

    def settle(self, launcher):
        """Called by the controller on every tick, even after maintenance-resume/restart."""
        for saved in self.db.execute("SELECT * FROM maintenance_handoffs WHERE completed_at IS NULL").fetchall():
            item_id, pid = saved['item_id'], saved['worker_pid']
            if item_id in launcher.running():
                continue
            try:
                # Never kill a still-running worker merely because it acknowledged a handoff.
                launcher.assert_quiescent(item_id, pid)
            except Exception:
                continue
            with self.ledger._transaction():
                current = self.ledger._row(item_id)
                if (current['generation'] == saved['generation'] and current['worker_pid'] == pid
                        and current['state'] == 'queued' and current['token'] is None
                        and current['next_root_repo'] is None):
                    self.db.execute("UPDATE work_items SET worker_pid=NULL,updated_at=? WHERE id=?",
                                    (self.ledger.clock(), item_id))
                # Cancellation/material changes retain their own state; never resurrect them.
                self.db.execute("UPDATE maintenance_handoffs SET completed_at=? WHERE item_id=?",
                                (self.ledger.clock(), item_id))
                self.ledger._audit(item_id, 'maintenance_handoff_complete')

    def observe(self, launcher, busy=(), *, controller_started_at=None, resource_check=None):
        """Controller proof, with admission fenced durably while slow operations finish."""
        current = request(self.db)
        if not current:
            return
        blockers = [{'code': 'controller_busy', 'subject': name} for name in busy]
        if resource_check is not None:
            blockers.extend(resource_check(current))
        handles = launcher.running()
        with self.ledger._transaction():
            for item_id, handle in handles.items():
                self.track(item_id, handle.pid)
            for row in self.db.execute("SELECT id,worker_pid FROM work_items WHERE state IN ('queued','running')"):
                self.track(row['id'], row['worker_pid'])
        for row in self.db.execute("SELECT * FROM maintenance_attempts WHERE request_id=?", (current['request_id'],)):
            try:
                if row['item_id'] in handles:
                    raise RuntimeError('worker still exiting')
                launcher.assert_quiescent(row['item_id'], row['worker_pid'])
            except Exception:
                blockers.append({'code': 'worker_teardown', 'subject': row['item_id']})
        for row in self.db.execute("SELECT id FROM work_items WHERE state='running' OR "
                                   "(state='queued' AND worker_pid IS NOT NULL)"):
            blockers.append({'code': 'worker_handoff', 'subject': row['id']})
        for row in self.db.execute("SELECT item_id FROM maintenance_handoffs WHERE completed_at IS NULL"):
            blockers.append({'code': 'worker_teardown', 'subject': row['item_id']})
        for row in self.db.execute("SELECT item_id FROM reservations WHERE state IN ('active','cancel_requested')"):
            blockers.append({'code': 'resource_active', 'subject': row['item_id']})
        for row in self.db.execute("SELECT slot_id FROM slots WHERE state NOT IN ('idle_open','idle_closed')"):
            blockers.append({'code': 'resource_unsettled', 'subject': row['slot_id']})
        unique = list({(b['code'], b['subject']): b for b in blockers}.values())
        observation = {'controller_started_at': controller_started_at, 'blockers': unique}
        with self.ledger._transaction():
            self.db.execute("UPDATE maintenance_control SET checked_at=?,blockers=? WHERE request_id=?",
                            (self.ledger.clock(), json.dumps(observation), current['request_id']))


def status(db, heartbeat, now):
    """Public projection: an old observation or a dead controller is never permission to stop."""
    current = request(db)
    if not current:
        return {'state': 'off', 'ready_to_stop': False, 'blockers': []}
    state, beat = heartbeat
    checked = current['checked_at']
    schedule = (beat or {}).get('loops', {}).get('schedule', {})
    observation = json.loads(current['blockers']) if current['blockers'] else {}
    if not isinstance(observation, dict):
        observation = {}  # no controller identity, never a ready proof
    verified = (state == 'fresh' and beat and beat.get('phase') == 'serving' and checked is not None
                and observation.get('controller_started_at') == beat['started_at']
                and checked >= beat['started_at'] and 0 <= now - checked <= 15
                and not schedule.get('consecutive_errors'))
    raw = observation.get('blockers')
    if not isinstance(raw, list):
        verified, raw = False, []
    blockers = []
    codes = {'worker_teardown', 'worker_handoff', 'resource_active', 'resource_unsettled', 'controller_busy',
             'resource_probe'}
    for entry in raw[:1000]:
        if not isinstance(entry, dict) or entry.get('code') not in codes:
            verified = False
            continue
        subject = entry.get('subject')
        subject = subject if isinstance(subject, str) and re.fullmatch(r'[A-Za-z0-9:_-]{1,100}', subject) else None
        identifier = None
        if subject and entry['code'] in ('worker_teardown', 'worker_handoff', 'resource_active'):
            row = db.execute("SELECT json_extract(i.metadata,'$.identifier') FROM work_items w "
                             "JOIN issues i ON i.id=w.issue_id WHERE w.id=?", (subject,)).fetchone()
            if row and isinstance(row[0], str) and re.fullmatch(r'[A-Za-z0-9_-]{1,32}', row[0]):
                identifier = row[0]
        blockers.append({'code': entry['code'], 'subject': identifier or subject})
    if len(raw) > 1000:
        verified = False
    if not verified:
        blockers.append({'code': 'controller_unconfirmed', 'subject': None})
    return {'state': 'ready' if verified and not blockers else 'draining',
            'ready_to_stop': bool(verified and not blockers), 'requested_at': current['requested_at'],
            'checked_at': checked, 'blockers': blockers}


def read_status(config):
    from .config import Paths
    from .heartbeat import read
    from .readonly_db import snapshot_connection
    import time
    now = time.time()
    paths = Paths(config)
    with snapshot_connection(paths.ledger) as db:
        return status(db, read(paths.heartbeat, now=now), now)
