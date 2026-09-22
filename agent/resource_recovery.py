"""Durable controller-owned recovery of quarantined Unity resources.

The ledger fences claims; the controller proves teardown before detaching a
reservation. Editor repair is independent of a job's replacement reservation.
"""
import errno
import json
import os
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


SCHEMA = """
CREATE TABLE IF NOT EXISTS resource_recoveries (
    id TEXT PRIMARY KEY, slot_id TEXT NOT NULL REFERENCES slots(slot_id), host TEXT NOT NULL,
    reservation_id TEXT, item_id TEXT, worker_pid INTEGER, commit_sha TEXT,
    state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
    due_at REAL NOT NULL DEFAULT 0, lease_until REAL NOT NULL DEFAULT 0,
    detached INTEGER NOT NULL DEFAULT 0, resume_job INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL, error TEXT, evidence TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_pending_resource_recovery ON resource_recoveries(slot_id)
    WHERE state IN ('pending','repairing');
CREATE TABLE IF NOT EXISTS resource_job_retries (
    item_id TEXT PRIMARY KEY REFERENCES work_items(id), attempts INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS resource_recovery_notices (
    id TEXT PRIMARY KEY, item_id TEXT NOT NULL, body TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0,
    generation INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS resource_watches (
    reservation_id TEXT PRIMARY KEY, signature TEXT NOT NULL, since REAL NOT NULL
);
"""

MAX_JOB_RETRIES = 2
MAX_REPAIR_ATTEMPTS = 3


@contextmanager
def repair_lock(path):
    """Nonblocking host lock; held through physical repair, released by OS on death.

    A journal lease alone cannot fence an already-running external editor call.
    Never unlink this file: every process must lock the same inode.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        stream.seek(0, 2)
        if not stream.tell():
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            acquire = lambda: msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            release = lambda: msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            acquire = lambda: fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            release = lambda: fcntl.flock(stream, fcntl.LOCK_UN)
        try:
            acquire()
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise
            yield False
            return
        try:
            yield True
        finally:
            release()


class RecoveryStore:
    def __init__(self, ledger):
        self.ledger = ledger
        self.db = ledger.connection

    def get(self, recovery_id):
        row = self.db.execute('SELECT * FROM resource_recoveries WHERE id=?', (recovery_id,)).fetchone()
        return dict(row) if row else None

    def request_in_transaction(self, slot_id, reason, *, adopt=False):
        """Called by hold() inside its transaction; never infer intent from prose."""
        slot = self.ledger._slot_row(slot_id)
        existing = self.db.execute("SELECT * FROM resource_recoveries WHERE slot_id=? AND state IN ('pending','repairing')",
                                   (slot_id,)).fetchone()
        if existing:
            return dict(existing)
        reservation = self.ledger.active_reservation_on(slot_id)
        item = self.ledger.item(reservation['item_id']) if reservation else None
        resume = bool(item and (item['state'] in ('running', 'queued', 'awaiting_resource')
                              or (adopt and item['state'] == 'awaiting_input')))
        now, recovery_id = self.ledger.clock(), str(uuid4())
        self.db.execute("""INSERT INTO resource_recoveries
            (id,slot_id,host,reservation_id,item_id,worker_pid,commit_sha,resume_job,reason,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (recovery_id, slot_id, slot['host'], reservation['reservation_id'] if reservation else None,
             item['id'] if item else None, self.ledger.last_worker_pid(item['id']) if item else None,
             reservation['commit_sha'] if reservation else slot['parked_commit'], int(resume), reason[:1000], now, now))
        self.db.execute("UPDATE slots SET state='held',updated_at=? WHERE slot_id=?", (now, slot_id))
        if reservation:
            # Fence the old resource token immediately, before the worker can
            # release the held slot back to the ordinary scheduling loop.
            self.db.execute("UPDATE reservations SET owner='resource-recovery',token_hash=? WHERE reservation_id=?",
                            (uuid4().hex, reservation['reservation_id']))
        if resume:
            checkpoint = dict(item['checkpoint'])
            checkpoint.pop('pending_question', None)
            self.ledger._set_state(item['id'], 'awaiting_resource', 'automatic Unity recovery',
                                   stage='waiting_for_recovery', token=None, lease_expires_at=None, worker_pid=None,
                                   needs_resource=f"{reservation['kind']}:{reservation['mode']}",
                                   checkpoint=json.dumps(checkpoint, ensure_ascii=False))
        self.ledger._audit(item['id'] if item else slot_id, 'resource_recovery', 'requested',
                           {'recovery_id': recovery_id, 'slot_id': slot_id, 'reason': reason[:500]})
        return self.get(recovery_id)

    def adopt(self, slot_id, reason):
        """Explicit host migration of a verified legacy infrastructure-only pause."""
        with self.ledger._transaction():
            return self.request_in_transaction(slot_id, reason, adopt=True)

    def discover(self, host):
        """Migrate pre-existing holds, retaining genuine human questions and exhausted holds."""
        with self.ledger._transaction():
            for slot in self.ledger.slots(host=host):
                if slot['state'] != 'held':
                    continue
                latest = self.db.execute('SELECT state FROM resource_recoveries WHERE slot_id=? ORDER BY created_at DESC LIMIT 1',
                                         (slot['slot_id'],)).fetchone()
                if latest is None or latest['state'] == 'recovered':
                    self.request_in_transaction(slot['slot_id'], 'controller discovered quarantined slot')

    def pending(self, host):
        return [dict(row) for row in self.db.execute("SELECT * FROM resource_recoveries WHERE host=? AND state IN ('pending','repairing') ORDER BY created_at,id", (host,))]

    def due(self, host):
        now = self.ledger.clock()
        return [r for r in self.pending(host) if r['due_at'] <= now and r['lease_until'] <= now]

    def begin(self, recovery_id):
        with self.ledger._transaction():
            r = self.get(recovery_id)
            now = self.ledger.clock()
            if r['state'] not in ('pending', 'repairing') or r['due_at'] > now or r['lease_until'] > now:
                return None
            if r['attempts'] >= MAX_REPAIR_ATTEMPTS:
                self._exhaust(r, 'recovery attempts interrupted or exhausted')
                return None
            self.db.execute("UPDATE resource_recoveries SET state='repairing',attempts=attempts+1,lease_until=?,updated_at=? WHERE id=?",
                            (now + 900, now, recovery_id))
            return self.get(recovery_id)

    def job_attempts(self, item_id):
        row = self.db.execute('SELECT attempts FROM resource_job_retries WHERE item_id=?', (item_id,)).fetchone()
        return row['attempts'] if row else 0

    def _attempt(self, recovery_id, attempt):
        r = self.get(recovery_id)
        if not r or r['state'] != 'repairing' or r['attempts'] != attempt:
            raise ValueError('stale resource recovery attempt')
        return r

    def _notice(self, item_id, body):
        self.db.execute('INSERT INTO resource_recovery_notices(id,item_id,body,generation) VALUES(?,?,?,?)',
                        (str(uuid4()), item_id, body, self.ledger.item(item_id)['generation']))

    def _fail_job(self, item_id, reason):
        item = self.ledger.item(item_id)
        if item['state'] != 'awaiting_resource':
            return
        self.db.execute("UPDATE reservations SET state='cancelled',released_at=?,release_reason=? WHERE item_id=? AND state='queued'",
                        (self.ledger.clock(), reason, item_id))
        self.ledger._set_state(item_id, 'failed', reason, token=None, lease_expires_at=None,
                               worker_pid=None, needs_resource=None, stage='verification-infrastructure-failed',
                               evidence=json.dumps({**item['evidence'], 'summary': reason}, ensure_ascii=False))
        self._notice(item_id, reason + '; saved changes, draft PRs and diagnostics are retained. No host operation is requested.')

    def detach(self, recovery_id, attempt):
        """Caller has proved the old worker tree stopped. Atomic and restart-idempotent."""
        with self.ledger._transaction():
            r = self._attempt(recovery_id, attempt)
            if r['detached']:
                return
            old = self.ledger.reservation(r['reservation_id']) if r['reservation_id'] else None
            if old:
                cancelled = old['state'] == 'cancel_requested'
                self.db.execute("UPDATE reservations SET state=?,released_at=?,release_reason=? WHERE reservation_id=? AND state IN ('active','cancel_requested')",
                                ('cancelled' if cancelled else 'released', self.ledger.clock(), 'controller detached quarantined editor', old['reservation_id']))
                item = self.ledger.item(r['item_id'])
                if (r['resume_job'] and not cancelled and item['state'] == 'awaiting_resource'
                        and item['stage'] == 'waiting_for_recovery'):
                    if self.job_attempts(item['id']) >= MAX_JOB_RETRIES:
                        self._fail_job(item['id'], 'Unity verification repeatedly stalled; repeated automatic job retries exhausted (2)')
                    else:
                        self.db.execute("INSERT INTO resource_job_retries(item_id,attempts) VALUES(?,1) ON CONFLICT(item_id) DO UPDATE SET attempts=attempts+1", (item['id'],))
                        replacement = str(uuid4())
                        self.db.execute("""INSERT INTO reservations(reservation_id,item_id,generation,kind,mode,commit_sha,state,created_at)
                            VALUES(?,?,?,?,?,?,'queued',?)""",
                            (replacement, item['id'], item['generation'], old['kind'], old['mode'], old['commit_sha'], self.ledger.clock()))
                        self.ledger._audit(item['id'], 'resource_recovery', 'retry queued',
                                           {'recovery_id': recovery_id, 'reservation_id': replacement, 'commit_sha': old['commit_sha']})
            self.db.execute('UPDATE resource_recoveries SET detached=1,updated_at=? WHERE id=?', (self.ledger.clock(), recovery_id))

    def complete(self, recovery_id, attempt, commit, instance):
        with self.ledger._transaction():
            r = self._attempt(recovery_id, attempt)
            if self.ledger.active_reservation_on(r['slot_id']) is not None or not r['detached']:
                raise ValueError('cannot publish a recovered slot before worker and reservation detachment')
            updated = self.db.execute("UPDATE slots SET state='idle_open',parked_commit=?,instance=?,updated_at=? WHERE slot_id=? AND state='held'",
                                      (commit, instance, self.ledger.clock(), r['slot_id']))
            if not updated.rowcount:
                raise ValueError('recovery slot is no longer isolated')
            self.db.execute("UPDATE resource_recoveries SET state='recovered',lease_until=0,error=NULL,updated_at=? WHERE id=?",
                            (self.ledger.clock(), recovery_id))
            self.ledger._audit(r['slot_id'], 'resource_recovery', 'healthy', {'recovery_id': recovery_id, 'commit_sha': commit})

    def _exhaust(self, r, error):
        self.db.execute("UPDATE resource_recoveries SET state='exhausted',lease_until=0,error=?,updated_at=? WHERE id=?",
                        (error[:1000], self.ledger.clock(), r['id']))
        if r['item_id'] and r['resume_job'] and not r['detached']:
            self._fail_job(r['item_id'], 'Unity infrastructure recovery failed: ' + error[:300])

    def failed(self, recovery_id, attempt, error):
        with self.ledger._transaction():
            r = self._attempt(recovery_id, attempt)
            if r['attempts'] >= MAX_REPAIR_ATTEMPTS:
                self._exhaust(r, error)
            else:
                delay = (60, 180)[max(0, r['attempts'] - 1)]
                self.db.execute("UPDATE resource_recoveries SET state='pending',lease_until=0,due_at=?,error=?,updated_at=? WHERE id=?",
                                (self.ledger.clock() + delay, error[:1000], self.ledger.clock(), recovery_id))

    def notifications(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM resource_recovery_notices WHERE sent=0')]

    def evidence(self, recovery_id, path):
        with self.ledger._transaction():
            self.db.execute('UPDATE resource_recoveries SET evidence=? WHERE id=?', (str(path), recovery_id))

    def fail_unserviceable(self, host):
        """Do not leave jobs queued forever once every local slot exhausted repair."""
        with self.ledger._transaction():
            slots = self.ledger.slots(host=host)
            for kind in {s['kind'] for s in slots}:
                candidates = [s for s in slots if s['kind'] == kind]
                if any(s['state'] != 'held' for s in candidates):
                    continue
                if any(r['slot_id'] in {s['slot_id'] for s in candidates} for r in self.pending(host)):
                    continue
                exhausted = {r['slot_id'] for r in self.db.execute("SELECT slot_id FROM resource_recoveries WHERE state='exhausted' AND host=?", (host,))}
                if not all(s['slot_id'] in exhausted for s in candidates):
                    continue
                waiting = list(self.db.execute("""SELECT r.item_id FROM reservations r JOIN work_items w ON w.id=r.item_id
                    WHERE r.state='queued' AND r.kind=? AND (w.host=? OR w.host IS NULL)""", (kind, host)))
                for r in waiting:
                    self._fail_job(r['item_id'], 'Unity infrastructure unavailable: all configured slots exhausted automatic recovery')


class RecoveryController:
    """Run on its own service thread/connection, independently of slot grants."""
    def __init__(self, ledger, pool, *, host, evidence_root, fence, inspect=None, repair=None, api=None):
        self.ledger, self.pool, self.host = ledger, pool, host
        self.store = RecoveryStore(ledger)
        self.evidence_root = Path(evidence_root)
        self.fence, self.api = fence, api
        if inspect is None or repair is None:
            from .unity_recovery import UnityRecovery
            self.editor = UnityRecovery(pool)
            inspect = inspect or self.editor.inspect
            repair = repair or self.editor.repair
        self.inspect, self.repair = inspect, repair

    def _save(self, recovery, name, value):
        path = self.evidence_root / recovery['id'] / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        self.store.evidence(recovery['id'], path.parent)
        return path

    def watch(self):
        for r in self.ledger.reservations(states=('active',)):
            if r['host'] != self.host or r['mode'] != 'interactive':
                continue
            slot = self.ledger.slot(r['resource'])
            if slot['state'] != 'interactive_busy':
                continue
            try:
                snapshot = self.inspect(slot)
                tests = snapshot.get('state', {}).get('tests', {})
                if tests.get('is_running') is not True:
                    self.ledger.connection.execute('DELETE FROM resource_watches WHERE reservation_id=?', (r['reservation_id'],))
                    continue
                job = snapshot.get('job') or {}
                signature = json.dumps([tests.get('current_job_id'), job.get('status'),
                                        (job.get('progress') or {}).get('completed'), job.get('last_update_unix_ms')])
                reason = 'Unity test run made no progress for 180 seconds'
            except Exception as exc:
                snapshot = {'inspection_error': f'{type(exc).__name__}: {exc}'[:1000]}
                signature, reason = 'unreachable', 'Unity inspection continuously unavailable for 180 seconds'
            db, now = self.ledger.connection, self.ledger.clock()
            with self.ledger._transaction():
                old = db.execute('SELECT * FROM resource_watches WHERE reservation_id=?', (r['reservation_id'],)).fetchone()
                if old is None or old['signature'] != signature:
                    db.execute('INSERT INTO resource_watches VALUES(?,?,?) ON CONFLICT(reservation_id) DO UPDATE SET signature=excluded.signature,since=excluded.since',
                               (r['reservation_id'], signature, now))
                    continue
                if now - old['since'] < 180:
                    continue
                # Re-read under the write transaction: a worker may have released
                # the reservation while the external MCP inspection was in flight.
                current = self.ledger.reservation(r['reservation_id'])
                if current['state'] != 'active' or self.ledger.slot(r['resource'])['state'] != 'interactive_busy':
                    continue
                recovery = self.store.request_in_transaction(slot['slot_id'], reason)
            self._save(recovery, 'watchdog.json', snapshot)

    def report(self):
        if self.api is None:
            return
        for notice in self.store.notifications():
            item = self.ledger.item(notice['item_id'])
            if not self._notice_current(notice):
                self._correct_status(item)
                self.ledger.connection.execute('UPDATE resource_recovery_notices SET sent=1 WHERE id=?', (notice['id'],))
                continue
            try:
                if item['session_id'].startswith('local-'):
                    self.api.create_comment(item['issue_id'], notice['body'])
                else:
                    self.api.create_activity(item['session_id'], {'type': 'error', 'body': notice['body']}, activity_id=notice['id'])
            except Exception:
                # The server may have accepted a request whose response was
                # lost. A timeout is not proof that session state was unchanged.
                if not self._notice_current(notice):
                    self._correct_status(item)
                continue
            self.ledger.connection.execute('UPDATE resource_recovery_notices SET sent=1 WHERE id=?', (notice['id'],))
            if not item['session_id'].startswith('local-') and not self._notice_current(notice):
                # Retry, Stop or a new question may cross the remote send. Queue
                # an immediate durable correction on the shared status loop.
                self._correct_status(item)

    def _correct_status(self, item):
        if not item['session_id'].startswith('local-'):
            from .session_progress import SessionProgress
            SessionProgress(self.ledger, self.api).queue_current(item['id'])

    def _notice_current(self, notice):
        item = self.ledger.item(notice['item_id'])
        active = self.ledger.active_item_for_session(item['session_id'])
        return (item['generation'] == notice['generation'] and item['state'] == 'failed'
                and item['stage'] == 'verification-infrastructure-failed'
                and (active is None or active['id'] == item['id']))

    def tick(self):
        self.store.discover(self.host)
        self.watch()
        repaired = 0
        for pending in self.store.due(self.host):
            with repair_lock(self.evidence_root / pending['id'] / '.lock') as acquired:
                if not acquired:
                    continue
                repaired += self._repair(pending['id'])
        self.store.fail_unserviceable(self.host)
        self.report()
        return {'repaired': repaired}

    def _repair(self, recovery_id):
        recovery = self.store.begin(recovery_id)
        if recovery is None:
            return 0
        attempt = recovery['attempts']
        try:
            # Claims/resource tokens were revoked at request. Direct MCP access
            # ends only after process teardown, before a successor can launch.
            if not recovery['detached']:
                self.fence(recovery)
                self.store.detach(recovery_id, attempt)
            slot = self.ledger.slot(recovery['slot_id'])
            try:
                snapshot = self.inspect(slot)
            except Exception as exc:
                snapshot = {'inspection_error': f'{type(exc).__name__}: {exc}'[:1000]}
            self._save(recovery, f'attempt-{attempt}.json', snapshot)
            commit, instance = self.repair(recovery)
            self.store.complete(recovery_id, attempt, commit, instance)
            return 1
        except Exception as exc:
            self.store.failed(recovery_id, attempt, f'{type(exc).__name__}: {exc}')
            return 0

    def close(self):
        self.ledger.close()
