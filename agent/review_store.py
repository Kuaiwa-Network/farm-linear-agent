"""Durable review state. Mutations use the caller's ledger transaction and fences."""
import hashlib
import json
import secrets
from uuid import uuid4

from .review_policy import ReviewError, commit, digest, encoded, validate_verdict

SCHEMA = """
CREATE TABLE IF NOT EXISTS review_deliveries (
 item_id TEXT NOT NULL REFERENCES work_items(id), generation INTEGER NOT NULL,
 fingerprint TEXT NOT NULL, inputs TEXT NOT NULL, created_at REAL NOT NULL,
 PRIMARY KEY(item_id,generation)
);
CREATE TABLE IF NOT EXISTS review_runs (
 id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES work_items(id), generation INTEGER NOT NULL,
 fingerprint TEXT NOT NULL, policy TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
 disposition TEXT, reason TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL,
 notified INTEGER NOT NULL DEFAULT 0, UNIQUE(item_id,generation)
);
CREATE TABLE IF NOT EXISTS review_prs (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
 run_id TEXT NOT NULL REFERENCES review_runs(id), repository TEXT NOT NULL, url TEXT NOT NULL,
 target TEXT NOT NULL, source_head TEXT NOT NULL, source_branch TEXT NOT NULL,
 candidate_id TEXT, state TEXT NOT NULL DEFAULT 'queued', repairs INTEGER NOT NULL DEFAULT 0,
 retries INTEGER NOT NULL DEFAULT 0, rebuilds INTEGER NOT NULL DEFAULT 0,
 retry_at REAL NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', UNIQUE(run_id,url)
);
CREATE TABLE IF NOT EXISTS review_candidates (
 id TEXT PRIMARY KEY, pr_id TEXT NOT NULL REFERENCES review_prs(id), snapshot TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'prepared', verdict TEXT, review_inputs TEXT, ci TEXT, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS review_edges (
 predecessor TEXT NOT NULL REFERENCES review_candidates(id),
 successor TEXT NOT NULL REFERENCES review_candidates(id), PRIMARY KEY(predecessor,successor)
);
CREATE TABLE IF NOT EXISTS review_attempts (
 id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES review_candidates(id), role TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'queued', token_hash TEXT, expires_at REAL, pid INTEGER,
 result TEXT, review_inputs TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS review_one_attempt ON review_attempts(candidate_id)
 WHERE state IN ('queued','running','submitted','stopping');
CREATE TABLE IF NOT EXISTS review_merges (
 candidate_id TEXT PRIMARY KEY REFERENCES review_candidates(id), state TEXT NOT NULL,
 request TEXT NOT NULL, response TEXT, merge_sha TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS review_control (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), paused INTEGER NOT NULL DEFAULT 0,
 reason TEXT NOT NULL DEFAULT ''
);
INSERT OR IGNORE INTO review_control(singleton) VALUES(1);
CREATE TABLE IF NOT EXISTS review_incidents (
 event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES review_runs(id), kind TEXT NOT NULL,
 created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS review_observations (
 run_id TEXT PRIMARY KEY REFERENCES review_runs(id), completed_seen INTEGER NOT NULL DEFAULT 0,
 checked_at REAL NOT NULL DEFAULT 0
);
"""


def record_delivery(ledger, row, inputs, fingerprint):
    """Called inside the successful finish transaction, with CLI-verified inputs only."""
    if inputs:
        ledger.connection.execute('INSERT OR IGNORE INTO review_deliveries VALUES(?,?,?,?,?)',
            (row['id'], row['generation'], fingerprint, encoded(inputs), ledger.clock()))


def summary(connection):
    """Read-only diagnostics, including on a database from before this migration."""
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='review_runs'").fetchone():
        return {'available': False}
    return {'available': True,
            'runs': {r[0]: r[1] for r in connection.execute('SELECT state,count(*) FROM review_runs GROUP BY state')},
            'attempts': {r[0]: r[1] for r in connection.execute('SELECT state,count(*) FROM review_attempts GROUP BY state')},
            'prs': [dict(r) for r in connection.execute('SELECT p.repository,p.url,p.state,p.repairs,p.rebuilds,p.error,'
                'c.id AS candidate,json_extract(c.snapshot,\'$.commit\') AS commit_sha,json_extract(c.ci,\'$.state\') AS ci,'
                'r.disposition FROM review_prs p JOIN review_runs r ON r.id=p.run_id '
                'LEFT JOIN review_candidates c ON c.id=p.candidate_id ORDER BY p.sequence DESC LIMIT 50')],
            'metrics': dict(connection.execute('SELECT count(*) AS candidates, '
                "sum(state='stale') AS invalidated,sum(state='merged') AS merged FROM review_candidates").fetchone()),
            'control': dict(connection.execute('SELECT * FROM review_control').fetchone())}


class ReviewStore:
    def __init__(self, ledger):
        self.ledger, self.db = ledger, ledger.connection

    def row(self, table, identity):
        if table not in ('review_runs', 'review_prs', 'review_candidates', 'review_attempts'):
            raise ReviewError('invalid review table')
        row = self.db.execute(f'SELECT * FROM {table} WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise ReviewError('unknown review record')
        return dict(row)

    def current(self, run):
        item = self.ledger.item(run['item_id'])
        issue = self.db.execute('SELECT fingerprint,metadata FROM issues WHERE id=?', (item['issue_id'],)).fetchone()
        other = self.db.execute("SELECT 1 FROM work_items WHERE issue_id=? AND id<>? AND (predecessor_id=? OR state IN "
                               "('queued','running','awaiting_input','awaiting_resource'))", (item['issue_id'], item['id'], item['id'])).fetchone()
        return (run['state'] not in ('delivered', 'superseded') and item['state'] == 'delivered'
                and item['generation'] == run['generation'] and issue['fingerprint'] == run['fingerprint']
                and item['withdraw_deadline'] is None and not other)

    def enroll(self, delivery, policy_hash):
        inputs = json.loads(delivery['inputs'])
        run_id = digest([delivery['item_id'], delivery['generation']])
        now = self.ledger.clock()
        with self.ledger._transaction():
            self.db.execute('INSERT OR IGNORE INTO review_runs(id,item_id,generation,fingerprint,policy,created_at,updated_at) '
                'VALUES(?,?,?,?,?,?,?)', (run_id, delivery['item_id'], delivery['generation'], delivery['fingerprint'], policy_hash, now, now))
            for value in inputs:
                pr_id = digest([run_id, value['url']])
                self.db.execute('INSERT OR IGNORE INTO review_prs(id,run_id,repository,url,target,source_head,source_branch) '
                    'VALUES(?,?,?,?,?,?,?)', (pr_id, run_id, value['repo'], value['url'], value['base_branch'],
                                           commit(value['head']), value['branch']))
        return self.row('review_runs', run_id)

    def invalidate(self, candidate_id, reason):
        """Caller holds transaction. Fence all descendants before scheduling any rebuild."""
        rows = self.db.execute('WITH RECURSIVE affected(id) AS (SELECT ? UNION SELECT e.successor '
            'FROM review_edges e JOIN affected a ON a.id=e.predecessor) SELECT id FROM affected', (candidate_id,)).fetchall()
        for row in rows:
            cid = row['id']
            self.db.execute("UPDATE review_candidates SET state='stale' WHERE id=? AND state<>'merged'", (cid,))
            self.db.execute("UPDATE review_attempts SET state='stopping',token_hash=NULL WHERE candidate_id=? "
                            "AND state IN ('queued','running','submitted')", (cid,))
            self.db.execute("UPDATE review_prs SET candidate_id=NULL,state='queued',rebuilds=rebuilds+1,error=? "
                            "WHERE candidate_id=? AND state NOT IN ('merged','delivered')", (reason, cid))

    def handoff(self, run_id, reason, *, superseded=False):
        with self.ledger._transaction():
            run = self.row('review_runs', run_id)
            if run['state'] in ('delivered', 'superseded'):
                return
            retained = self.db.execute("SELECT id,candidate_id FROM review_prs WHERE run_id=? AND state<>'merged'", (run_id,)).fetchall()
            for pr in retained:
                if pr['candidate_id']:
                    self.invalidate(pr['candidate_id'], reason)
            self.db.execute("UPDATE review_prs SET state='delivered',error=? WHERE run_id=? AND state<>'merged'", (reason, run_id))
            for pr in retained:
                # Retain the last reviewed identity for the handoff report, without
                # restoring its now-stale eligibility or any descendant's pointer.
                self.db.execute('UPDATE review_prs SET candidate_id=? WHERE id=?', (pr['candidate_id'], pr['id']))
            self.db.execute('UPDATE review_runs SET state=?,disposition=?,reason=?,updated_at=? WHERE id=?',
                ('superseded' if superseded else 'delivered', 'superseded' if superseded else 'human_handoff',
                 reason, self.ledger.clock(), run_id))
            self.ledger._audit(run['item_id'], 'review_handoff', reason, {'run': run_id, 'disposition': 'human_handoff'})

    def candidate(self, pr_id, snapshot, predecessors):
        for key in ('head', 'base', 'commit', 'tree', 'prefix'):
            commit(snapshot[key])
        snapshot = {**snapshot, 'predecessors': list(predecessors), 'pr_id': pr_id}
        snapshot['id'] = digest(snapshot)
        with self.ledger._transaction():
            pr = self.row('review_prs', pr_id)
            if not self.current(self.row('review_runs', pr['run_id'])) or pr['state'] != 'queued':
                raise ReviewError('candidate no longer eligible')
            if any(self.row('review_candidates', p)['state'] == 'stale' for p in predecessors):
                raise ReviewError('candidate predecessor changed')
            # Exact same snapshot on recovery reuses its evidence; invalidated snapshots stay invalidated.
            old = self.db.execute('SELECT state FROM review_candidates WHERE id=?', (snapshot['id'],)).fetchone()
            if old and old['state'] == 'stale':
                snapshot['revision'] = pr['rebuilds']
                snapshot['id'] = digest({k: v for k, v in snapshot.items() if k != 'id'})
            self.db.execute('INSERT OR IGNORE INTO review_candidates(id,pr_id,snapshot,created_at) VALUES(?,?,?,?)',
                (snapshot['id'], pr_id, encoded(snapshot), self.ledger.clock()))
            for predecessor in predecessors:
                self.db.execute('INSERT OR IGNORE INTO review_edges VALUES(?,?)', (predecessor, snapshot['id']))
            self.db.execute("UPDATE review_prs SET candidate_id=?,state='reviewing',error='' WHERE id=?", (snapshot['id'], pr_id))
        return snapshot

    def attempt(self, candidate_id, role):
        if role not in ('review', 'repair'):
            raise ReviewError('invalid review role')
        with self.ledger._transaction():
            candidate = self.row('review_candidates', candidate_id)
            pr = self.row('review_prs', candidate['pr_id'])
            if candidate['state'] in ('stale', 'merged') or not self.current(self.row('review_runs', pr['run_id'])):
                raise ReviewError('cannot launch stale review')
            inputs = self.review_inputs(pr['run_id']) if role == 'review' else None
            if role == 'review' and inputs is None:
                raise ReviewError('all delivery candidates must be conflict-free before independent review')
            identity = str(uuid4())
            now = self.ledger.clock()
            self.db.execute('INSERT INTO review_attempts(id,candidate_id,role,review_inputs,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                            (identity, candidate_id, role, encoded(inputs) if inputs is not None else None, now, now))
        return self.row('review_attempts', identity)

    def review_inputs(self, run_id):
        """Immutable identities for the full delivery, without cyclic queue edges.

        A related repair fences claims and verdicts, but does not change an
        unaffected repository's candidate commit or reuse CI for another SHA.
        """
        result = {}
        for pr in self.db.execute('SELECT * FROM review_prs WHERE run_id=? ORDER BY id', (run_id,)):
            if not pr['candidate_id'] or pr['state'] in ('queued', 'repair', 'delivered', 'shadow_repair'):
                return None
            candidate = self.row('review_candidates', pr['candidate_id'])
            if candidate['state'] == 'stale' or json.loads(candidate['snapshot'])['conflicts']:
                return None
            result[pr['id']] = candidate['id']
        return result or None

    def inputs_current(self, run_id, inputs):
        current = self.review_inputs(run_id)
        return inputs is not None and current is not None and json.loads(inputs) == current

    def review_snapshot(self, candidate_id):
        candidate = self.row('review_candidates', candidate_id)
        snapshot = json.loads(candidate['snapshot'])
        if candidate['verdict']:
            # Re-review after another repository changes must explicitly answer
            # prior Important findings. Preserve them even though the verdict is stale.
            snapshot['open_findings'] = [f for f in json.loads(candidate['verdict'])['findings']
                                         if f['severity'] == 'important']
        return snapshot

    def claim(self, attempt_id, seconds):
        with self.ledger._transaction():
            row = self.row('review_attempts', attempt_id)
            if row['state'] != 'queued':
                raise ReviewError('review attempt already claimed or fenced')
            self._check_attempt(row)
            token = secrets.token_urlsafe(32)
            now = self.ledger.clock()
            self.db.execute("UPDATE review_attempts SET state='running',token_hash=?,expires_at=?,updated_at=? WHERE id=?",
                            (hashlib.sha256(token.encode()).hexdigest(), now + seconds, now, attempt_id))
            return token

    def _check_attempt(self, attempt):
        from .maintenance import Maintenance
        candidate = self.row('review_candidates', attempt['candidate_id'])
        pr = self.row('review_prs', candidate['pr_id'])
        run = self.row('review_runs', pr['run_id'])
        if (candidate['state'] in ('stale', 'merged') or pr['candidate_id'] != candidate['id']
                or not self.current(run) or Maintenance(self.ledger).requested()):
            raise ReviewError('review attempt superseded, withdrawn or under maintenance')
        if attempt['role'] == 'review' and not self.inputs_current(run['id'], attempt['review_inputs']):
            raise ReviewError('related delivery candidate changed during review')
        return candidate

    def owned(self, attempt_id, token):
        row = self.row('review_attempts', attempt_id)
        if (row['state'] != 'running' or not secrets.compare_digest(row['token_hash'] or '', hashlib.sha256(token.encode()).hexdigest())
                or row['expires_at'] <= self.ledger.clock()):
            raise ReviewError('review claim is invalid or expired')
        self._check_attempt(row)
        return row

    def submit(self, attempt_id, token, result):
        with self.ledger._transaction():
            row = self.owned(attempt_id, token)
            snapshot = self.review_snapshot(row['candidate_id'])
            if row['role'] == 'review':
                validate_verdict(result, snapshot)
            elif (not isinstance(result, dict) or set(result) != {'snapshot', 'head', 'summary', 'needs_person'}
                    or result['snapshot'] != snapshot['id'] or type(result['needs_person']) is not bool
                    or not isinstance(result['summary'], str) or not result['summary'].strip()):
                raise ReviewError('invalid repair response')
            else:
                commit(result['head'])
            if len(encoded(result).encode('utf-8')) > 65536:
                raise ReviewError('review result exceeds 64 KiB')
            self.db.execute("UPDATE review_attempts SET state='submitted',result=?,token_hash=NULL,updated_at=? WHERE id=?",
                            (encoded(result), self.ledger.clock(), attempt_id))

    def incident(self, event_id, run_id, kind, policy):
        with self.ledger._transaction():
            run = self.row('review_runs', run_id)
            if run['disposition'] != 'auto_merged' or kind not in ('revert', 'reopen'):
                raise ReviewError('incident must identify auto-merged work')
            inserted = self.db.execute('INSERT OR IGNORE INTO review_incidents VALUES(?,?,?,?)',
                                      (event_id, run_id, kind, self.ledger.clock())).rowcount
            if not inserted:
                return
            count = self.db.execute('SELECT count(DISTINCT run_id) FROM review_incidents WHERE created_at>=?',
                (self.ledger.clock() - policy['circuit_days'] * 86400,)).fetchone()[0]
            if count >= policy['circuit_limit']:
                self.db.execute("UPDATE review_control SET paused=1,reason='auto-merge regression threshold exceeded'")
