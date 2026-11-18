"""Pre-finish review by fresh read-only helpers of the issue owner."""
import json

from .execution import single
from .maintenance import Maintenance
from .request_scope import require_reviewed
from .review_policy import ReviewError, digest, encoded, evidence_policy, validate_verdict
from .review_store import ReviewStore
from .version_targets import require_current


def enabled(config, policy):
    return (policy['mode'] == 'merge' and config.instance_id == policy['authority']
            and config.runtime in ('codex', 'claude', 'fake'))


def prepare_packet(controller, pr, snapshot, context):
    """Only the controller materializes read-only integrated sources."""
    store = controller.store
    inputs = encoded(store.review_inputs(pr['run_id']))
    if store.db.execute('SELECT 1 FROM review_owner_packets WHERE candidate_id=? AND inputs=?',
                        (snapshot['id'], inputs)).fetchone():
        return
    candidates = [controller.candidate(p) for p in controller.prs(pr['run_id'])]
    sources = {s['repository']: str(controller.git.read(s)) for s in candidates}
    packet = {**context, 'snapshot': store.review_snapshot(snapshot['id']), 'sources': sources,
              'related_snapshots': candidates, 'ticket': digest([snapshot['id'], inputs])}
    store.db.execute('INSERT OR IGNORE INTO review_owner_packets(candidate_id,inputs,context) VALUES(?,?,?)',
                     (snapshot['id'], inputs, encoded(packet)))


def rows(store, run_id):
    return [dict(r) for r in store.db.execute('SELECT * FROM review_prs WHERE run_id=? ORDER BY sequence', (run_id,))]


def register(store, item, proofs, policy):
    run_id = digest([item['id'], item['generation']])
    if not store.db.execute('SELECT 1 FROM review_runs WHERE id=?', (run_id,)).fetchone():
        run = store.enroll_locked({'item_id': item['id'], 'generation': item['generation'],
                                   'fingerprint': item['claimed_fingerprint'], 'inputs': encoded(proofs)},
                                  evidence_policy(policy, [p['repo'] for p in proofs]))
        store.db.execute('INSERT INTO review_owners(run_id) VALUES(?)', (run['id'],))
        return
    run = store.row('review_runs', run_id)
    if not store.current(run) or not store.owner_pending(run_id):
        raise ReviewError('owner review is no longer current')
    prs = rows(store, run_id)
    verified = {p['url']: p for p in proofs}
    if set(verified) != {p['url'] for p in prs}:
        raise ReviewError('republish the complete enrolled PR set')
    changes = []
    for pr in prs:
        proof = verified[pr['url']]
        if (proof['repo'], proof['branch'], proof['base_branch']) != (pr['repository'], pr['source_branch'], pr['target']):
            raise ReviewError('owner repair destination changed')
        if proof['head'] == pr['source_head']:
            if pr['state'] == 'repair':
                record = store.row('review_candidates', pr['candidate_id'])
                if record['state'] == 'stale' or proof['base'] != json.loads(record['snapshot'])['base']:
                    changes.append((pr, proof))
            continue
        if pr['state'] != 'repair':
            raise ReviewError('only a requested repair can replace its published head')
        if pr['repository'] == 'Farm-Client' and not store.db.execute(
                "SELECT 1 FROM reservations r WHERE item_id=? AND generation=? AND kind='unity_slot' "
                "AND EXISTS (SELECT 1 FROM audit a WHERE a.item_id=r.item_id AND a.kind='unity_test_passed' "
                "AND json_extract(a.details,'$.reservation')=r.reservation_id AND json_extract(a.details,'$.commit')=r.commit_sha) "
                "AND commit_sha=? AND state='released' AND release_reason='worker reported quiescent'",
                (item['id'], item['generation'], proof['head'])).fetchone():
            raise ReviewError('verify the repaired Client SHA with an owned Unity slot and release it before resubmission')
        changes.append((pr, proof))
    for pr, proof in changes:
        store.db.execute("UPDATE review_prs SET state='queued' WHERE id=?", (pr['id'],))
        store.invalidate(pr['candidate_id'], 'Owner resubmitted verified PR inputs')
        repaired = int(pr['source_head'] != proof['head'])
        store.db.execute('UPDATE review_prs SET source_head=?,repairs=repairs+? WHERE id=?',
                         (proof['head'], repaired, pr['id']))
    if changes:
        store.db.execute('UPDATE review_owners SET wake=1 WHERE run_id=?', (run_id,))


def require_finish(store, item, proofs):
    run_id = digest([item['id'], item['generation']])
    if not store.owner_pending(run_id):
        raise ReviewError('start owner-review with every published --pr before finishing')
    run = store.row('review_runs', run_id)
    prs = rows(store, run_id)
    if {(p['url'], p['head']) for p in proofs} != {(p['url'], p['source_head']) for p in prs}:
        raise ReviewError('delivery PRs differ from the owner review')
    if run['disposition'] == 'human_handoff':
        return
    if not store.current(run) or not all(p['state'] == 'ready' and store.inputs_current(
            run_id, store.row('review_candidates', p['candidate_id'])['review_inputs']) for p in prs):
        raise ReviewError('owner-review must settle all current candidates before finish')


def command(ledger, item_id, token, policy, proofs=None, report=None, handoff=None):
    store = ReviewStore(ledger)
    with ledger._transaction():
        item = ledger._owned(item_id, token)
        if not single(item) or item['skill'] != 'work':
            raise ReviewError('owner review requires the running issue owner')
        ledger._refuse_withdrawn(item)
        require_current(ledger, item_id)
        require_reviewed(ledger, item_id)
        if ledger.requires_issue_review(item_id) or Maintenance(ledger).requested(item_id):
            raise ReviewError('settle changed inputs or maintenance before owner review')
        run_id = digest([item_id, item['generation']])
        if proofs:
            register(store, item, proofs, policy)
        run = store.row('review_runs', run_id)
        if not store.owner_pending(run_id):
            raise ReviewError('run is not an owner review')
        if run['disposition']:
            return {'state': run['disposition'], 'reason': run['reason']}
        if not store.current(run):
            raise ReviewError('owner review inputs changed')
        if handoff is not None:
            if not handoff.strip() or len(handoff) > 6000:
                raise ReviewError('describe the concrete verification or decision needed')
            store.db.execute('UPDATE review_owners SET handoff=?,wake=1 WHERE run_id=?', (handoff, run_id))
            return {'state': 'handoff_requested'}
        prs = rows(store, run_id)
        inputs = encoded(store.review_inputs(run_id))
        packets = []
        for pr in prs:
            if pr['state'] == 'repair':
                candidate = store.row('review_candidates', pr['candidate_id'])
                packets.append({'action': 'repair', 'repository': pr['repository'], 'reason': pr['error'],
                                'snapshot': json.loads(candidate['snapshot']),
                                'verdict': json.loads(candidate['verdict'] or 'null'),
                                'ci': json.loads(candidate['ci'] or 'null')})
                continue
            packet = store.db.execute('SELECT * FROM review_owner_packets WHERE candidate_id=? AND inputs=?',
                                      (pr['candidate_id'], inputs)).fetchone()
            if not packet:
                continue
            context = json.loads(packet['context'])
            if report is not None and report.get('ticket') == context['ticket']:
                reviewer = report.get('reviewer')
                if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 256:
                    raise ReviewError('report requires the fresh native subagent ID')
                result = validate_verdict(report.get('verdict'), context['snapshot'])
                if packet['result']:
                    if packet['result'] != encoded(result) or packet['reviewer'] != reviewer:
                        raise ReviewError('review packet already completed')
                else:
                    store.db.execute('UPDATE review_owner_packets SET reviewer=?,result=? WHERE candidate_id=? AND inputs=?',
                                     (reviewer, encoded(result), pr['candidate_id'], inputs))
                    store.db.execute('UPDATE review_candidates SET verdict=?,review_inputs=? WHERE id=?',
                                     (encoded(result), inputs, pr['candidate_id']))
                    store.db.execute('UPDATE review_owners SET wake=1 WHERE run_id=?', (run_id,))
                return {'state': 'submitted'}
            if not packet['result']:
                packets.append({'action': 'review', **context})
        if report is not None:
            raise ReviewError('stale review ticket; read current owner-review status')
        return {'state': 'ready' if all(pr['state'] == 'ready' for pr in prs) else 'active', 'tasks': packets}
