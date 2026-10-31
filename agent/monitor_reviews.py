"""Read-only, issue-level review progress for Monitor. No claim or result payloads."""

LIVE_ATTEMPTS = ('queued', 'running', 'submitted', 'stopping', 'settled')
RUNNING_PHASES = ('review_running', 'repair_running', 'review_finishing', 'repair_finishing',
                  'report_pending', 'promoting', 'merging')
PHASE_ORDER = (*RUNNING_PHASES, 'repair_starting', 'review_starting', 'retry_wait',
               'repair_queued', 'review_queued', 'integrating', 'waiting_ci', 'merge_queued',
               'shadow_repair', 'unknown', 'merged', 'human_handoff')


def _phase(row, now):
    if row['state'] == 'merged':
        return 'merged'
    if row['state'] == 'delivered':
        return 'human_handoff'
    role, attempt = row['role'], row['attempt_state']
    if role in ('review', 'repair') and attempt in LIVE_ATTEMPTS:
        if attempt == 'settled':
            return 'report_pending'
        suffix = {'queued': 'starting', 'running': 'running',
                  'submitted': 'finishing', 'stopping': 'finishing'}[attempt]
        return role + '_' + suffix
    if row['retry_at'] and row['retry_at'] > now:
        return 'retry_wait'
    return {'queued': 'integrating', 'reviewing': 'review_queued', 'repair': 'repair_queued',
            'waiting_ci': 'waiting_ci', 'ready': 'merge_queued', 'promoting': 'promoting',
            'merging': 'merging', 'shadow_repair': 'shadow_repair'}.get(row['state'], 'unknown')


def active_reviews(db, now, beat_state, beat, *, maintenance=False):
    # Imported at call time by build_status, after monitor_view is initialized.
    from .monitor_view import _ISSUE_FIELDS, _text, _time, _linear_url, _pr, _live
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='review_runs'").fetchone():
        return []
    paused = bool(db.execute('SELECT paused FROM review_control').fetchone()[0])
    runs = db.execute(f"""SELECT r.id,r.created_at,r.updated_at,{_ISSUE_FIELDS}
        FROM review_runs r JOIN work_items w ON w.id=r.item_id JOIN issues i ON i.id=w.issue_id
        WHERE r.state NOT IN ('delivered','superseded') AND w.state='delivered'
        AND r.generation=w.generation AND r.fingerprint=i.fingerprint
        AND w.withdraw_deadline IS NULL
        AND NOT EXISTS (SELECT 1 FROM work_items newer WHERE newer.issue_id=w.issue_id
            AND newer.id<>w.id AND (newer.predecessor_id=w.id
                OR newer.state IN ('queued','running','awaiting_input','awaiting_resource')))
        ORDER BY r.created_at,r.id""").fetchall()
    jobs = []
    for run in runs:
        # Current-candidate attempts only: an invalidated repair/review cannot appear live.
        rows = db.execute("""SELECT p.repository,p.url,p.state,p.repairs,p.retry_at,
            CASE WHEN json_valid(c.ci) THEN json_extract(c.ci,'$.state') END AS ci,
            c.created_at AS candidate_at,a.id AS attempt_id,a.role,a.state AS attempt_state,
            a.updated_at AS attempt_at,
            (SELECT count(*) FROM review_attempts h JOIN review_candidates hc ON hc.id=h.candidate_id
             WHERE hc.pr_id=p.id AND h.role='review' AND h.result IS NOT NULL) AS reports
            FROM review_prs p LEFT JOIN review_candidates c ON c.id=p.candidate_id
            LEFT JOIN review_attempts a ON a.id=(SELECT id FROM review_attempts
                WHERE candidate_id=c.id ORDER BY created_at DESC,rowid DESC LIMIT 1)
            WHERE p.run_id=? ORDER BY p.sequence""", (run['id'],)).fetchall()
        prs, updates = [], [_time(run['updated_at']), _time(run['created_at'])]
        for raw in rows:
            row = dict(raw)
            row['retry_at'] = _time(row['retry_at'])
            phase = _phase(row, now)
            pr = _pr(row['url'])
            live = row['attempt_state'] in LIVE_ATTEMPTS[:-1] and row['role'] in ('review', 'repair')
            tracked = (row['attempt_id'] in (beat.get('workers') or {})
                       if _live(beat_state, beat) and live else None)
            updated = max(filter(lambda value: value is not None,
                                 (_time(row['attempt_at']), _time(row['candidate_at']), 0))) or None
            updates.append(updated)
            prs.append({'repository': _text(row['repository'], 64),
                        'url': pr['url'] if pr else None, 'label': pr['label'] if pr else None,
                        'phase': phase, 'ci': row['ci'] if row['ci'] in ('passed', 'failed', 'waiting') else None,
                        'repairs': row['repairs'] if type(row['repairs']) is int and row['repairs'] >= 0 else 0,
                        'reports': row['reports'],
                        'retry_at': _time(row['retry_at']) if phase == 'retry_wait' else None,
                        'updated_at': updated, 'tracked': tracked})
        phases = {pr['phase'] for pr in prs} or {'integrating'}
        phase = next(value for value in PHASE_ORDER if value in phases)
        state = ('running' if phase in RUNNING_PHASES else
                 'waiting_ci' if phase == 'waiting_ci' else 'queued')
        # Pausing stops future admission; a still-exiting worker remains visible.
        if (paused or maintenance) and state != 'running':
            phase, state = 'paused', 'queued'
        progress = {'phase': phase, 'prs': prs, 'total': len(prs),
                    'merged': sum(pr['phase'] == 'merged' for pr in prs),
                    'reports': sum(pr['reports'] for pr in prs),
                    'repairs': sum(pr['repairs'] for pr in prs),
                    'paused': paused or maintenance, 'stale': not _live(beat_state, beat),
                    'updated_at': max((value for value in updates if value is not None), default=None)}
        jobs.append({'kind': 'review', 'identifier': _text(run['identifier'], 32),
                     'title': _text(run['title'], 200), 'url': _linear_url(run['url']),
                     'skill': 'review', 'state': state, 'display_state': 'review',
                     'created_at': _time(run['created_at']), 'state_since': _time(run['created_at']),
                     'stage': None, 'repo': None, 'worker': None, 'checkpoint_at': None,
                     'queue_position': None, 'retry_at': None, 'resource_wait': None,
                     'prs': [_pr(row['url']) for row in rows if _pr(row['url'])], 'review': progress})
    return jobs
