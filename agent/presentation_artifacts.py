"""Certified source delivery and parent consumption, without borrowing child claims."""
import hashlib
import json
from uuid import uuid4

from .fgui_records import _canonical, export_record
from .ledger import LedgerError


def _seal(value, key):
    return {**value, key: hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()}


def _check(value, key):
    raw = {k: v for k, v in value.items() if k != key}
    if _seal(raw, key) != value:
        raise LedgerError('presentation artifact has damaged controller evidence')
    return value


def _records(ledger, item_id, kind):
    return [json.loads(r['details']) for r in ledger.connection.execute(
        'SELECT details FROM audit WHERE item_id=? AND kind=? ORDER BY id', (item_id, kind))]


def snapshot(workflow, child_id, exported):
    from .fgui_export import snapshot_export
    selected = {p['name']: p['id'] for p in exported['publisher']['export']['packages']}
    stage = workflow.paths.runs / child_id / 'ui-exports' / exported['receipt_id'] / 'staging'
    value = snapshot_export(stage, selected)
    if value.evidence() != exported['publisher']['export']:
        raise LedgerError('presentation export bytes differ from the certified immutable artifact')
    return value


def verify_source_delivery(workflow, urls):
    """Legacy clean Client entrants may hand back their completed source in place."""
    ledger, item_id = workflow.ledger, workflow.item_id
    item = ledger.item(item_id)
    if item['skill'] != 'fgui' or not item.get('parent_id'):
        raise LedgerError('source delivery requires an internal FGUI task')
    ledger.require_no_reservation(item_id)
    receipt_id = item['checkpoint'].get('plan', {}).get('ui', {}).get('receipt_id')
    exported = workflow.retained_export(receipt_id)
    snapshot(workflow, item_id, exported)
    if urls != [exported['source_pr']]:
        raise LedgerError('source delivery names exactly its certified farmgui draft')
    branches = ledger.recorded_branches(item_id, issue_prefix=workflow.config.issue_prefix)
    heads = {'farmgui': exported['authority']['head']}
    if item.get('task_client_base'):
        # Never discard a half-applied installation or pretend Client changes were delivered.
        from .fgui_client import _prepared_record, installation_records
        if installation_records(ledger, item_id) or _prepared_record(workflow, receipt_id):
            raise LedgerError('legacy Client installation requires journal recovery before source handoff')
        client = workflow.paths.worktrees / item['parent_id'] / 'Farm-Client'
        head = workflow.trees.head(client)
        branch = ledger.staged_client_branch(item_id, issue_prefix=workflow.config.issue_prefix)
        workflow.trees.verification_commit('Farm-Client', item_id, head, expected_branch=branch)
        if workflow.trees.git_in(client, 'diff', '--name-only', '--no-renames', '--no-ext-diff',
                                 '--no-textconv', item['task_client_base'], head):
            raise LedgerError('legacy source handoff requires unchanged Client entry; retain recovery evidence')
        heads['Farm-Client'], branches['Farm-Client'] = head, branch
    branches = {repo: branches[repo] for repo in heads}
    for repo, head in heads.items():
        workflow.trees.verification_commit(repo, item_id, head, expected_branch=branches[repo])
    workflow.fence(refresh=True)
    with ledger._transaction():
        row = ledger._owned(item_id, workflow.token)
        ledger._refuse_withdrawn(row)
        ledger.require_no_reservation(item_id)
        if row['claimed_fingerprint'] != ledger._issue_row(row['issue_id'])['fingerprint']:
            raise LedgerError('reconcile changed inputs before source delivery')
        proof = _seal({'format': 'farmbot-presentation-source-v1', 'delivery_id': uuid4().hex,
            'receipt_id': receipt_id, 'receipt_sha256': exported['receipt_sha256'],
            'parent_id': row['parent_id'], 'generation': row['generation'],
            'fingerprint': row['claimed_fingerprint'], 'heads': heads, 'branches': branches,
            'baseline': item.get('task_client_base'), 'prs': urls}, 'source_sha256')
        ledger._audit(item_id, 'fgui_source_verified', details=proof)
    return proof


def source_proof(ledger, task, *, delivery_id=None):
    evidence = task.get('evidence') or {}
    identity = delivery_id or evidence.get('ui_source_delivery_id')
    matches = [p for p in _records(ledger, task['id'], 'fgui_source_verified') if p.get('delivery_id') == identity]
    if len(matches) != 1:
        raise LedgerError('source delivery requires its controller source proof')
    proof = _check(matches[0], 'source_sha256')
    row = ledger._row(task['id'])
    if (proof['parent_id'] != task['parent_id'] or proof['generation'] != task['generation']
            or proof['fingerprint'] != row['claimed_fingerprint']):
        raise LedgerError('source delivery identity changed')
    return proof


def consumed_export(workflow, receipt_id, *, create=False):
    """Resolve only a retired child of this exact parent; never accept a worker path."""
    ledger = workflow.ledger
    parent = ledger.item(workflow.item_id)
    if parent['skill'] != 'work' or parent.get('parent_id') or parent['root_repo'] != 'Farm-Client':
        raise LedgerError('presentation consumption requires the parent Client stage')
    from .workflow import task_views
    tasks = [t for t in task_views(ledger, parent['id']) if t['skill'] == 'fgui'
             and (t.get('evidence') or {}).get('receipt_id') == receipt_id
             and (t.get('evidence') or {}).get('ui_source_delivery_id')]
    if len(tasks) != 1:
        raise LedgerError('installation requires one certified source artifact from this parent')
    task = tasks[0]
    proof = source_proof(ledger, task)
    if proof['fingerprint'] != ledger._issue_row(parent['issue_id'])['fingerprint']:
        raise LedgerError('source artifact requires reconciliation of changed issue inputs')
    if (task['state'] != 'delivered' or not (ledger.cleanup_record(task['id']) or {}).get('done')
            or task.get('withdraw_deadline') is not None):
        raise LedgerError('source artifact awaits certified child teardown')
    exported = export_record(ledger, task['id'], receipt_id)
    if (exported['receipt_sha256'] != proof['receipt_sha256']
            or exported['authority']['head'] != proof['heads']['farmgui']):
        raise LedgerError('source artifact no longer matches the certified export')
    records = [r for r in _records(ledger, parent['id'], 'fgui_source_consumed') if r['receipt_id'] == receipt_id]
    if len(records) > 1:
        raise LedgerError('ambiguous source consumption; preserve recovery evidence')
    if records:
        record = _check(records[0], 'consumption_sha256')
        if (record['source_sha256'] != proof['source_sha256'] or record['task_id'] != task['id']
                or record['generation'] != parent['generation']):
            raise LedgerError('source consumption identity changed')
    else:
        if not create:
            raise LedgerError('install the certified source artifact before checking its Client integration')
        client = workflow.paths.worktrees / parent['id'] / 'Farm-Client'
        head = workflow.trees.head(client)
        branch = ledger.staged_client_branch(parent['id'], issue_prefix=workflow.config.issue_prefix)
        workflow.trees.verification_commit('Farm-Client', parent['id'], head, expected_branch=branch)
        snapshot(workflow, task['id'], exported)
        with ledger._transaction():
            row = ledger._owned(parent['id'], workflow.token)
            ledger._refuse_withdrawn(row)
            ledger.require_no_reservation(parent['id'])
            record = _seal({'format': 'farmbot-presentation-consumption-v1', 'task_id': task['id'],
                'receipt_id': receipt_id, 'source_sha256': proof['source_sha256'],
                'entry_head': head, 'branch': branch, 'generation': row['generation']}, 'consumption_sha256')
            ledger._audit(parent['id'], 'fgui_source_consumed', details=record)
    workflow.source_item_id, workflow.consumption = task['id'], record
    return exported


def parent_install_base(workflow, client):
    from .fgui_client import installation_record, _prepared_record
    installed = installation_record(workflow.ledger, workflow.item_id, workflow.receipt_id)
    prepared = _prepared_record(workflow, workflow.receipt_id)
    # The immutable consumption entry remains recorded. A prerequisite repair before
    # installation may advance HEAD; once writes are prepared, their base is fixed.
    return (installed or (prepared or {}).get('proof') or {}).get('baseline') or workflow.trees.head(client)


def require_parent_integrations(ledger, parent):
    from .workflow import task_views
    from .fgui_client import installation_record
    commit = parent['checkpoint'].get('plan', {}).get('client', {}).get('head')
    for task in task_views(ledger, parent['id']):
        if not (task.get('evidence') or {}).get('ui_source_delivery_id'):
            continue  # Legacy complete Client deliveries keep their original contract.
        source = source_proof(ledger, task)
        receipt_id = source['receipt_id']
        installed = installation_record(ledger, parent['id'], receipt_id)
        if not installed or not commit:
            raise LedgerError('parent delivery requires each source artifact installed and verified at its final Client candidate')
        for kind, key in (('fgui_guards_complete', 'guards_sha256'), ('fgui_unity_complete', 'unity_sha256')):
            proofs = [_check(p, key) for p in _records(ledger, parent['id'], kind)]
            if not any(p.get('receipt_id') == receipt_id and p.get('commit') == commit
                       and p.get('source_head') == source['heads']['farmgui']
                       and p.get('installation_sha256') == installed['installation_sha256'] for p in proofs):
                raise LedgerError('parent delivery requires certified guards and Unity loading for every source artifact at the final candidate')


def verify_parent_head(ledger, parent, config):
    """Closing may run in Contract; reading Client proves the checkpoint names its actual HEAD."""
    from .config import Paths
    from .workflow import JobWorktrees, repository_path, task_views
    from .worktrees import Worktrees
    if not any((t.get('evidence') or {}).get('ui_source_delivery_id') for t in task_views(ledger, parent['id'])):
        return
    require_parent_integrations(ledger, parent)
    paths = Paths(config)
    trees = JobWorktrees(Worktrees(paths.repos, paths.worktrees, config.repos), ledger)
    client = repository_path(trees, 'Farm-Client', parent['id'])
    commit = parent['checkpoint']['plan']['client']['head']
    branch = ledger.staged_client_branch(parent['id'], issue_prefix=config.issue_prefix)
    if trees.head(client) != commit:
        raise LedgerError('parent final candidate differs from its actual Client HEAD')
    trees.verification_commit('Farm-Client', parent['id'], commit, expected_branch=branch)
