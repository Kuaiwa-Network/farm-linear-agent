"""A scene may yield Client for a bounded prerequisite repair, retaining its entry."""
import json
import re
from uuid import uuid4

from .ledger import LedgerError, _json
from .presentation_contract import current_claim


def pending(ledger, item_id):
    values = [json.loads(r['details']) for r in ledger.connection.execute(
        "SELECT details FROM audit WHERE item_id=? AND kind='presentation_yield' ORDER BY id DESC", (item_id,))]
    if not values:
        return None
    proof = values[0]
    resumed = any(json.loads(r['details']).get('yield_id') == proof['yield_id'] for r in ledger.connection.execute(
        "SELECT details FROM audit WHERE item_id=? AND kind='presentation_repaired'", (item_id,)))
    row = ledger._row(item_id)
    if resumed or row['generation'] != proof['generation'] or row['state'] != 'awaiting_input':
        return None
    return proof


def yield_client(ledger, item_id, token, trees, config, value):
    from .workflow import repository_path
    from .scene_scope import scope_for
    if not isinstance(value, dict) or set(value) != {'reason', 'paths'}:
        raise LedgerError('presentation yield requires a reason and exact prerequisite repair paths')
    paths = value['paths']
    if (not isinstance(value['reason'], str) or not 10 <= len(value['reason'].strip()) <= 3000
            or not isinstance(paths, list) or not 0 < len(paths) <= 64):
        raise LedgerError('presentation yield requires a concrete reason and bounded paths')
    seen = set()
    for p in paths:
        if (not isinstance(p, str) or '\\' in p or ':' in p or '\x00' in p or p.startswith('/')
                or any(ord(c) < 32 or c in '*?<>|"' for c in p)
                or any(s in ('', '.', '..') or s.rstrip(' .') != s or s.lower() == '.git'
                       or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', s)
                       for s in p.split('/')) or p.casefold() in seen):
            raise LedgerError('prerequisite repair paths must be repository-relative ordinary files')
        seen.add(p.casefold())
    row = current_claim(ledger, item_id, token)
    item = ledger.item(item_id)
    if row['skill'] != 'scene' or not row['parent_id'] or row['root_repo'] not in (None, 'Farm-Client'):
        raise LedgerError('Client prerequisite yield belongs to scene tasks; FGUI delivers its source artifact')
    ledger.require_no_reservation(item_id)
    assets = scope_for(ledger, item)['assets']
    if set(paths) & (set(assets) | {p + '.meta' for p in assets}):
        raise LedgerError('scene retains responsibility for its own assets; yield only outside prerequisites')
    client = repository_path(trees, 'Farm-Client', item_id)
    commit = trees.head(client)
    branch = ledger.staged_client_branch(item_id, issue_prefix=config.issue_prefix)
    trees.verification_commit('Farm-Client', item_id, commit, expected_branch=branch)
    checkpoint = item['checkpoint']
    if checkpoint.get('pending_question') or checkpoint.get('human_question'):
        raise LedgerError('resolve real human decisions before requesting a technical repair')
    with ledger._transaction():
        current_claim(ledger, item_id, token)
        ledger.require_valid_checkpoint(item_id, token)
        proof = {'yield_id': uuid4().hex, 'generation': row['generation'], 'commit': commit,
                 'branch': branch, 'reason': value['reason'], 'paths': sorted(set(paths))}
        checkpoint['presentation_repair'] = proof
        ledger._audit(item_id, 'presentation_yield', details=proof)
        ledger._set_state(item_id, 'awaiting_input', 'waiting for parent prerequisite repair', token=None,
            lease_expires_at=None, worker_pid=None, stage='waiting-parent-repair', checkpoint=_json(checkpoint))
    return proof


def resume(ledger, parent_id, token, task_key, trees, config, launcher):
    from .workflow import repository_path
    parent = current_claim(ledger, parent_id, token)
    if parent['skill'] != 'work' or parent['parent_id'] or parent['root_repo'] != 'Farm-Client':
        raise LedgerError('only the parent Client stage may finish a prerequisite repair')
    task = ledger.connection.execute('SELECT id FROM work_items WHERE parent_id=? AND task_key=?',
                                     (parent_id, task_key)).fetchone()
    proof = pending(ledger, task['id']) if task else None
    if proof is None:
        raise LedgerError('task has no pending certified prerequisite handoff')
    task_id = task['id']
    launcher.assert_quiescent(task_id, ledger.last_worker_pid(task_id))
    ledger.require_no_reservation(task_id)
    ledger.require_no_reservation(parent_id)
    client = repository_path(trees, 'Farm-Client', parent_id)
    head = trees.head(client)
    trees.verification_commit('Farm-Client', parent_id, head, expected_branch=proof['branch'])
    if trees.git_in(client, 'merge-base', '--all', proof['commit'], head).splitlines() != [proof['commit']]:
        raise LedgerError('prerequisite repair must retain the original yielded candidate history')
    changed = set(filter(None, trees.git_in(client, 'diff', '--name-only', '--no-renames', '--no-ext-diff',
                                           '--no-textconv', '-z', proof['commit'], head).split('\0')))
    if not changed or changed - set(proof['paths']):
        raise LedgerError('parent repair must change only the named prerequisite paths')
    with ledger._transaction():
        current_claim(ledger, parent_id, token)
        if pending(ledger, task_id) != proof:
            raise LedgerError('presentation handoff changed during repair')
        result = {'yield_id': proof['yield_id'], 'generation': proof['generation'],
                  'before': proof['commit'], 'commit': head, 'paths': sorted(changed)}
        ledger._audit(task_id, 'presentation_repaired', details=result)
        checkpoint = ledger.item(task_id)['checkpoint']
        checkpoint.pop('presentation_repair', None)
        ledger._set_state(task_id, 'queued', 'parent prerequisite repair ready', stage='scene-after-repair',
                          checkpoint=_json(checkpoint), retry_not_before=0)
    return result


def inherited_paths(ledger, item, trees, client, commit):
    latest = {}
    for row in ledger.connection.execute("SELECT details FROM audit WHERE item_id=? AND kind='presentation_repaired' ORDER BY id",
                                         (item['id'],)):
        proof = json.loads(row['details'])
        if proof['generation'] != item['generation']:
            continue
        if trees.git_in(client, 'merge-base', '--all', proof['commit'], commit).splitlines() != [proof['commit']]:
            raise LedgerError('scene must retain its certified parent repair history')
        latest.update({path: proof['commit'] for path in proof['paths']})
    for path, head in latest.items():
        changed = set(filter(None, trees.git_in(client, 'diff', '--name-only', '--no-renames', '--no-ext-diff',
            '--no-textconv', '-z', head, commit, '--', path).split('\0')))
        if changed:
            raise LedgerError('scene cannot alter inherited parent prerequisite repairs')
    return set(latest)
