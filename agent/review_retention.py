"""Retire disposable review sources only after terminal delivery and proven teardown."""
import json

from .review_policy import ReviewError
from .worker_retention import Controller as CacheRetention


class Retention:
    def __init__(self, controller, policy):
        self.controller = controller
        self.store, self.workers, self.git = controller.store, controller.workers, controller.git
        self.cache = CacheRetention(self.workers.launcher, policy, clock=self.store.ledger.clock)

    def tick(self):
        if not self.cache.policy['enabled']:
            return
        self.cache.tick()  # This launcher's root is runs/review, not the general pool's root.
        now = self.store.ledger.clock()
        cutoff = now - self.cache.policy['keep_hours'] * 3600
        db = self.store.db
        # Related reviews read multiple snapshots. Fence the whole delivery, while
        # unrelated active deliveries may keep using their own review capacity.
        row = db.execute('SELECT c.*,r.id AS run_id FROM review_candidates c '
            'JOIN review_prs p ON p.id=c.pr_id JOIN review_runs r ON r.id=p.run_id '
            'LEFT JOIN review_cleanup x ON x.candidate_id=c.id '
            "WHERE r.state IN ('delivered','superseded') AND r.updated_at<=? AND c.created_at<=? "
            'AND x.completed_at IS NULL AND COALESCE(x.retry_at,0)<=? '
            "AND NOT EXISTS (SELECT 1 FROM review_merges m WHERE m.candidate_id=c.id AND m.state='pending') "
            'AND NOT EXISTS (SELECT 1 FROM review_attempts a JOIN review_candidates ac ON ac.id=a.candidate_id '
            'JOIN review_prs ap ON ap.id=ac.pr_id WHERE ap.run_id=r.id '
            "AND (a.state<>'processed' OR a.updated_at>?)) "
            'ORDER BY c.created_at,c.id LIMIT 1', (cutoff, cutoff, now, cutoff)).fetchone()
        if row is None:
            return
        candidate = dict(row)
        snapshot = json.loads(candidate['snapshot'])
        try:
            related = [dict(a) for a in db.execute('SELECT a.* FROM review_attempts a '
                'JOIN review_candidates c ON c.id=a.candidate_id JOIN review_prs p ON p.id=c.pr_id '
                'WHERE p.run_id=?', (candidate['run_id'],))]
            for attempt in related:
                self.workers.launcher.assert_quiescent(attempt['id'], attempt['pid'])
            attempts = [a for a in related if a['candidate_id'] == candidate['id']]
            evidence = {'candidate_ref': self.git.retain(snapshot), 'repairs': {}}
            for attempt in attempts:
                if attempt['role'] != 'repair':
                    continue
                path = self.git.worktrees._stage_path(snapshot['repository'], attempt['id'])
                item_id = self.store.row('review_runs', candidate['run_id'])['item_id']
                if path.exists():
                    report = self.git.worktrees.preserve(attempt['id'])
                    if report['errors']:
                        raise ReviewError('Repair preservation incomplete')
                    evidence['repairs'][attempt['id']] = report
                    # Persist before removing a checkout, including on restart after partial cleanup.
                    self.store.ledger._audit(item_id, 'review_cleanup_preserved', details=evidence)
                    self.git.worktrees.remove_preserved(attempt['id'], report)
                else:
                    key = '$.repairs."' + attempt['id'] + '"'
                    saved = db.execute('SELECT json_extract(details,?) FROM audit WHERE item_id=? '
                        "AND kind='review_cleanup_preserved' AND json_extract(details,?) IS NOT NULL ORDER BY id DESC LIMIT 1",
                        (key, item_id, key)).fetchone()
                    if not saved:
                        continue
                    report = json.loads(saved[0])
                branch = report.get('branches', {}).get(snapshot['repository'])
                if branch and branch.startswith('farmbot/') and branch.endswith('-review-' + attempt['id'][:8]):
                    ref = 'refs/heads/' + branch
                    clone = self.git.worktrees.ensure_clone(snapshot['repository'])
                    if self.git.worktrees._recovery_commit(clone, ref):
                        self.git.local(snapshot['repository'], 'update-ref', '-d', ref,
                                       report['committed'][snapshot['repository']])
            root = self.git.worktrees.reads_root('review-' + snapshot['id'])
            if root.exists():
                path = self.git.worktrees.read_candidate(snapshot['repository'], 'review-' + snapshot['id'],
                                                        snapshot['commit'], create=False)
                if list(root.iterdir()) != [path]:
                    raise ReviewError('Unexpected review source paths; preserve evidence')
                self.git.worktrees.remove_reads('review-' + snapshot['id'])
            self.git.retire_remote(snapshot, allow_delete=self.controller.writes
                                   and snapshot['repository'] in self.controller.authorized)
            db.execute('INSERT INTO review_cleanup(candidate_id,completed_at) VALUES(?,?) '
                       'ON CONFLICT(candidate_id) DO UPDATE SET completed_at=excluded.completed_at,error=\'\'',
                       (candidate['id'], now))
        except Exception as exc:
            db.execute('INSERT INTO review_cleanup(candidate_id,retry_at,error) VALUES(?,?,?) '
                       'ON CONFLICT(candidate_id) DO UPDATE SET retry_at=excluded.retry_at,error=excluded.error',
                       (candidate['id'], now + 3600, type(exc).__name__))

    def close(self):
        self.cache.close()
