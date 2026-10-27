"""Separate review capacity using the existing native contained launcher."""
import json
import os
from pathlib import Path

from .config import Paths, ROOT
from .maintenance import Maintenance
from .dispatch import review_message
from .review_policy import ReviewError


class ReviewWorkers:
    def __init__(self, store, launcher, git, config, policy, *, author_launcher=None):
        self.store, self.launcher, self.git = store, launcher, git
        self.config, self.policy = config, policy
        self.author_launcher = author_launcher

    def busy(self):
        return self.store.db.execute("SELECT count(*) FROM review_attempts WHERE state IN "
                                     "('queued','running','submitted','stopping')").fetchone()[0]

    def reap(self):
        self.launcher.poll()  # Same all-descendant containment verification as normal workers.
        completed = [dict(r) for r in self.store.db.execute("SELECT * FROM review_attempts WHERE state='settled'")]
        for raw in self.store.db.execute("SELECT * FROM review_attempts WHERE state IN "
                                         "('queued','running','submitted','stopping')").fetchall():
            attempt = dict(raw)
            current = self.policy['mode'] != 'off'
            try:
                self.store._check_attempt(attempt)
                pr = self.store.row('review_prs', self.store.row('review_candidates', attempt['candidate_id'])['pr_id'])
                if attempt['role'] == 'review' and pr['state'] == 'repair':
                    current = False
            except ReviewError:
                current = False
            if attempt['id'] in self.launcher.running():
                if not current or attempt['state'] == 'stopping':
                    self.launcher.stop(attempt['id'])
                continue
            try:
                from .launcher import _read_worker_text
                if not attempt['pid']:
                    records = [json.loads(_read_worker_text(p)) for p in self.launcher.state_dir(attempt['id']).glob('*/process.json')]
                    pids = {r['pid'] for r in records if r.get('state') != 'not_started' and type(r.get('pid')) is int and r['pid'] > 0}
                    if len(pids) == 1:
                        attempt['pid'] = pids.pop()
                        self.store.db.execute('UPDATE review_attempts SET pid=? WHERE id=?', (attempt['pid'], attempt['id']))
                certified = self.launcher.certified_pids(attempt['id'])
                if attempt['pid'] and attempt['pid'] not in certified and self.launcher.alive(attempt['pid']):
                    self.store.db.execute("UPDATE review_attempts SET state='stopping',token_hash=NULL WHERE id=?", (attempt['id'],))
                    if self.launcher.owned_pid(attempt['pid'], attempt['id']):
                        self.launcher.kill_owned_attempt(attempt['id'], attempt['pid'])
                    continue  # Never adopt or kill an unverified PID; preserve teardown evidence.
                self.launcher.assert_quiescent(attempt['id'], attempt['pid'])
            except (OSError, RuntimeError, ValueError):
                continue  # Uncertain descendants retain capacity and recovery evidence.
            self.store.db.execute("UPDATE review_attempts SET state='settled',token_hash=NULL,updated_at=? WHERE id=?",
                                  (self.store.ledger.clock(), attempt['id']))
            if current and attempt['state'] == 'submitted':
                completed.append(attempt)
            elif current:
                completed.append({**attempt, 'result': None})
            else:
                self.store.db.execute("UPDATE review_attempts SET state='processed' WHERE id=?", (attempt['id'],))
        return completed

    def launch(self, snapshot, role, context):
        if self.busy() >= self.policy['workers'] or Maintenance(self.store.ledger).requested():
            return False
        pr = self.store.row('review_prs', snapshot['pr_id'])
        run = self.store.row('review_runs', pr['run_id'])
        if not Maintenance(self.store.ledger).may_launch(run['item_id']):
            return False
        if self.author_launcher:
            try:
                self.author_launcher.assert_quiescent(run['item_id'], self.store.ledger.last_worker_pid(run['item_id']))
            except (OSError, RuntimeError, ValueError):
                return False
        # Immutable read-only reviews may overlap. A writer, a duplicate candidate,
        # or uncertain teardown fences all other roles for this issue.
        active = self.store.db.execute("SELECT 1 FROM review_attempts a JOIN review_candidates c ON c.id=a.candidate_id "
            "JOIN review_prs p ON p.id=c.pr_id WHERE p.run_id=? AND a.state IN ('queued','running','submitted','stopping') "
            "AND (a.role='repair' OR ?='repair' OR c.id=? OR a.state='stopping') LIMIT 1",
            (run['id'], role, snapshot['id'])).fetchone()
        if active:
            return False
        attempt = self.store.attempt(snapshot['id'], role)
        if role == 'review':
            snapshot = self.store.review_snapshot(snapshot['id'])
        item = self.store.ledger.item(run['item_id'])
        issue = self.store.ledger.issue(item['issue_id'])
        state_dir = self.launcher.state_dir(attempt['id'])
        state_dir.mkdir(parents=True, exist_ok=True)
        writable = [Paths(self.config).ledger.parent]
        source = {}
        try:
            if role == 'repair':
                path = self.git.repair(snapshot, attempt['id'], issue['identifier'], self.config.issue_prefix)
                source[snapshot['repository']] = path
                writable += [path, *self.git.worktrees.writable_parts(snapshot['repository'], path)]
            else:
                source[snapshot['repository']] = self.git.read(snapshot)
            # All related delivery snapshots are provided for cross-repository reasoning.
            related = self.store.db.execute('SELECT c.snapshot FROM review_prs p JOIN review_candidates c '
                                            'ON c.id=p.candidate_id WHERE p.run_id=?', (run['id'],)).fetchall()
            related_snapshots = [json.loads(r['snapshot']) for r in related]
            related_paths = []
            for other in related_snapshots:
                if other['id'] != snapshot['id'] and not other['conflicts']:
                    related_paths.append({'snapshot': other['id'], 'repository': other['repository'],
                                          'path': str(self.git.read(other))})
            context = {**context, 'related_snapshots': related_snapshots, 'related_sources': related_paths}
            prompt = review_message(attempt, snapshot, issue, source, state_dir, Paths(self.config).ledger, context)
            env = {'PYTHONPATH': os.pathsep.join([str(ROOT), os.environ.get('PYTHONPATH', '')]),
                   'FARMBOT_DB': str(Paths(self.config).ledger),
                   'GH_CONFIG_DIR': str(state_dir / 'github-disabled')}
            if self.config.source_path:
                env['FARMBOT_CONFIG'] = str(self.config.source_path)
            withheld = ['GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN']
            if self.config.kw_ops.get('token_env'):
                withheld.append(self.config.kw_ops['token_env'])
            handle = self.launcher.spawn(attempt['id'], prompt, {}, self.policy['worker_seconds'], cwd=state_dir,
                extra_env=env, writable=writable, withheld_env=withheld,
                model_settings=self.config.codex_workers.get('review', {}),
                cancelled=lambda: not self.store.current(self.store.row('review_runs', run['id'])))
            self.store.db.execute('UPDATE review_attempts SET pid=? WHERE id=?', (handle.pid, attempt['id']))
            return True
        except Exception:
            self.store.db.execute("UPDATE review_attempts SET state='stopping',token_hash=NULL WHERE id=?", (attempt['id'],))
            raise

    def close(self):
        for attempt in self.launcher.running():
            self.launcher.stop(attempt)
        self.reap()

    def blockers(self):
        return ['review_attempts'] if self.busy() else []
