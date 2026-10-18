"""The single issue owner contract. Historical staged jobs are never upgraded implicitly.

This module owns the execution boundary, not a second test-result judge. The
worker follows repository instructions and records its own verification account.
"""
from dataclasses import replace
import json

CONTRACT = 'single-worker-v1'
LEGACY = 'staged-v1'
REPOSITORIES = ('Farm-Contract', 'common', 'farm-hive', 'farmgui', 'Farm-Client')
RETIRED_COMMANDS = frozenset(('handoff-repository', 'delegate-task', 'await-tasks', 'answer-task',
    'retry-task', 'authorize-scene-scope', 'yield-presentation', 'resume-presentation',
    'closing-preflight', 'verification-closeout', 'evidence-index', 'verify-candidate',
    'compare-verification', 'continue-verification', 'review-ui', 'export-ui', 'install-ui',
    'verify-ui', 'verify-ui-loading', 'verify-ui-guards', 'ui-verification-status',
    'authorize-ui-guard-repair', 'verify-ui-source', 'verify-scene-assets', 'shared-test-repairs'))


def single(item):
    return dict(item).get('execution_contract') == CONTRACT


def skill_for(item, skill):
    if skill is None:
        return None
    if not single(item):
        if skill.name == 'work' and not skill.staged:
            from .skills import _load_one
            return _load_one(skill.path.parent / 'legacy' / 'work')
        return skill
    return replace(skill, writes=REPOSITORIES, reads=(), staged=False,
                   initial_root=None, gates=(), exclusive=False)


def validate_progress(progress):
    """Bound storage without prescribing a workflow or manufacturing acceptance gates."""
    from .ledger import LedgerError
    if len(json.dumps(progress, ensure_ascii=False).encode('utf-8')) > 65536:
        raise LedgerError('checkpoint exceeds 64 KiB; keep detailed reports in state_dir')
    if 'summary' in progress and not isinstance(progress['summary'], str):
        raise LedgerError('checkpoint summary must be text')


def queue_resource(ledger, item_id, token, resource, mode, commit, *, test_plan=None):
    from uuid import uuid4
    from .ledger import COMMIT_SHA, LedgerError, _json
    from .maintenance import Maintenance
    from .server_test_plan import validate_plan
    if resource not in ('unity_slot', 'server_slot'):
        raise LedgerError('unsupported resource')
    if mode != ('interactive' if resource == 'unity_slot' else 'batch'):
        raise LedgerError('Unity uses interactive mode; server services use batch mode')
    if not isinstance(commit, str) or not COMMIT_SHA.fullmatch(commit):
        raise LedgerError('resource requires the clean full candidate SHA')
    if resource == 'server_slot':
        if test_plan is None:
            raise LedgerError('server resource requires the actual scoped test plan')
        test_plan = validate_plan(test_plan)
    with ledger._transaction():
        row = ledger._owned(item_id, token)
        if not single(row):
            raise LedgerError('single-worker resource request requires its execution contract')
        ledger._refuse_withdrawn(row)
        if Maintenance(ledger).requested():
            raise LedgerError('maintenance requested; checkpoint and hand off instead of starting tests')
        if ledger.connection.execute("SELECT 1 FROM reservations WHERE item_id=? AND state IN "
                                     "('queued','active','cancel_requested')", (item_id,)).fetchone():
            raise LedgerError('settle the current resource before requesting another')
        reservation = str(uuid4())
        ledger.connection.execute("INSERT INTO reservations(reservation_id,item_id,generation,kind,mode,"
            "commit_sha,state,created_at,server_test_plan) VALUES(?,?,?,?,?,?,'queued',?,?)",
            (reservation, item_id, row['generation'], resource, mode, commit, ledger.clock(),
             _json(test_plan) if test_plan else None))
        # Keep the claim, process and its seat. The controller prepares only the
        # external resource; it never launches another worker to receive it.
        ledger.connection.execute('UPDATE work_items SET stage=?,needs_resource=?,updated_at=? WHERE id=?',
            ('waiting-for-' + resource, resource + ':' + mode, ledger.clock(), item_id))
        ledger._audit(item_id, 'reservation', 'queued',
                      {'reservation_id': reservation, 'commit_sha': commit, 'mode': mode})
        return {'reservation_id': reservation, 'state': 'queued', 'keep_worker': True}


def resource_ready(ledger, reservation):
    """Publish readiness only while the original claim and reservation still exist."""
    from .ledger import LedgerError
    with ledger._transaction():
        row = ledger._row(reservation['item_id'])
        current = ledger.reservation(reservation['reservation_id'])
        if (not single(row) or row['state'] != 'running' or not row['token']
                or row['lease_expires_at'] <= ledger.clock() or row['withdraw_deadline'] is not None
                or row['generation'] != current['generation'] or current['state'] != 'active'):
            raise LedgerError('resource owner no longer running')
        ledger.connection.execute("UPDATE work_items SET stage='resource-ready',needs_resource=NULL,updated_at=? WHERE id=?",
                                  (ledger.clock(), row['id']))
        ledger._audit(row['id'], 'resource_ready', details={'reservation_id': current['reservation_id']})


def cancel_queued_resource(ledger, item_id, token, reservation_id):
    from .ledger import LedgerError
    with ledger._transaction():
        row = ledger._owned(item_id, token)
        reservation = ledger.reservation(reservation_id)
        if (not single(row) or not reservation or reservation['item_id'] != item_id
                or reservation['generation'] != row['generation']):
            raise LedgerError('resource request does not belong to this claim')
        if reservation['state'] != 'queued':
            raise LedgerError('resource already acquired; wait for readiness and release it safely')
        ledger.connection.execute("UPDATE reservations SET state='cancelled',released_at=?,release_reason=? WHERE reservation_id=?",
            (ledger.clock(), 'worker cancelled its queued request', reservation_id))
        ledger.connection.execute("UPDATE work_items SET stage='working',needs_resource=NULL WHERE id=?", (item_id,))
        ledger._audit(item_id, 'reservation', 'cancelled', {'reservation_id': reservation_id})
        return {'state': 'cancelled', 'reservation_id': reservation_id}


def adopt(ledger, item_id, launcher, reason):
    """Host-only, explicit maintenance conversion; preserve candidates and all audit evidence."""
    from .ledger import LedgerError, QUESTION_KEY, _json, _text
    from .maintenance import Maintenance
    _text(reason, 'adoption reason')
    item = ledger.item(item_id)
    if single(item):
        return item
    if item['parent_id'] or item['skill'] not in ('work', 'fix', 'feature', 'fgui'):
        raise LedgerError('adopt the top-level delegated write job')
    if item['state'] not in ('queued', 'awaiting_input', 'blocked', 'failed'):
        raise LedgerError('settle the existing job before adoption')
    if not Maintenance(ledger).requested():
        raise LedgerError('pause for maintenance before adopting historical work')
    launcher.assert_quiescent(item_id, ledger.last_worker_pid(item_id))
    with ledger._transaction():
        if not Maintenance(ledger).requested():
            raise LedgerError('maintenance ended during adoption checks')
        current = ledger.item(item_id)
        if (current['state'], current['generation']) != (item['state'], item['generation']):
            raise LedgerError('job changed during adoption checks')
        ledger.require_no_reservation(item_id)
        cleanup = ledger.cleanup_record(item_id)
        if cleanup and cleanup.get('removing') and not cleanup.get('done'):
            raise LedgerError('wait for in-progress checkout preservation/removal before adoption')
        if ledger.connection.execute("SELECT 1 FROM work_items WHERE issue_id=? AND id<>? "
                "AND state IN ('queued','running','awaiting_input','awaiting_resource')",
                (item['issue_id'], item_id)).fetchone():
            raise LedgerError('another active job already owns this issue')
        children = ledger.connection.execute('SELECT id,state FROM work_items WHERE parent_id=?', (item_id,)).fetchall()
        if any(c['state'] not in ('delivered', 'cancelled', 'failed', 'blocked')
               or not (ledger.cleanup_record(c['id']) or {}).get('done') for c in children):
            raise LedgerError('settle and preserve all historical presentation workers before adoption')
        ledger._audit(item_id, 'execution_adopted', reason, {'previous_skill': item['skill'],
            'previous_root': item['root_repo'], 'checkpoint': item['checkpoint'], 'target': item['target']})
        checkpoint = {'summary': reason, 'handoff': 'Continue the retained source and reports. Review execution_adopted history; '
            'old stage/receipt/closing gates are retired. Do not reset candidates, merge main or repeat tests just for adoption.',
            'legacy_checkpoint': item['checkpoint']}
        question = ledger.human_question(item_id)
        if question:
            checkpoint[QUESTION_KEY] = question
            checkpoint['pending_question'] = question['body']
            checkpoint['pending_reason'] = 'question'
        # Historical technical waits remain evidence for the owner to review,
        # not an active gate requiring another approval under the new contract.
        state = 'awaiting_input' if question else 'queued'
        ledger.connection.execute("UPDATE work_items SET execution_contract=?,skill='work',state=?,stage='resuming',"
            "root_repo=NULL,next_root_repo=NULL,token=NULL,worker_pid=NULL,lease_expires_at=NULL,needs_resource=NULL,checkpoint=?,"
            "updated_at=? WHERE id=?", (CONTRACT, state, _json(checkpoint), ledger.clock(), item_id))
        # An old rejected stage-shaped handoff cannot poison the new contract.
        ledger._audit(item_id, 'handoff_saved', 'historical execution explicitly adopted')
    return ledger.item(item_id)


def resource_status(ledger, item_id, token):
    ledger.renew(item_id, token)
    item = ledger.item(item_id)
    row = ledger.connection.execute('SELECT * FROM reservations WHERE item_id=? AND generation=? ORDER BY sequence DESC LIMIT 1',
                                    (item_id, item['generation'])).fetchone()
    if row is None:
        return {'state': 'none'}
    reservation = ledger._reservation_view(row)
    slot = ledger.slot(reservation['resource']) if reservation['resource'] else None
    ready = reservation['state'] == 'active' and slot is not None and slot['state'] in ('interactive_busy', 'batch_busy') and any(
        json.loads(r['details']).get('reservation_id') == reservation['reservation_id']
        for r in ledger.connection.execute("SELECT details FROM audit WHERE item_id=? AND kind='resource_ready'", (item_id,)))
    result = {key: reservation.get(key) for key in ('reservation_id', 'kind', 'mode', 'commit_sha', 'state', 'wait_detail', 'release_reason')}
    result['ready'] = ready
    result['wait_detail'] = json.loads(row['wait_detail']) if row['wait_detail'] else None
    if slot and reservation['state'] == 'active':
        result['state'] = 'held' if slot['state'] == 'held' else 'ready' if ready else 'preparing'
    if ready:
        result.update(slot=slot['slot_id'], instance=slot['instance'], folder=slot['folder'])
    return result
