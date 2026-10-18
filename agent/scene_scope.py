"""Controller checks for a scene task's exact asset delta and local load evidence."""
import hashlib
import json
import re
from pathlib import Path, PurePosixPath

from .fgui_records import _canonical
from .ledger import LedgerError, TERMINAL_STATUS_TYPES
from .presentation_contract import request, validate_scope
from .preview_upload import _plain_path


def scope_for(ledger, item):
    values = [json.loads(r['details']) for r in ledger.connection.execute(
        "SELECT details FROM audit WHERE item_id=? AND kind='scene_scope_authorized' ORDER BY id", (item['id'],))]
    current = [v for v in values if v.get('generation') == item['generation']]
    value = current[-1]['scope'] if current else request(item).get('scope')
    if value is None:
        raise LedgerError('scene scope is missing; parent must authorize exact assets before delivery')
    return validate_scope(value)


def authorize(ledger, parent_id, token, task_key, scope, launcher):
    scope = validate_scope(scope)
    parent = ledger._owned(parent_id, token)
    ledger._refuse_withdrawn(parent)
    if parent['skill'] != 'work' or parent['parent_id']:
        raise LedgerError('only the parent can authorize scene asset scope')
    task = ledger.connection.execute('SELECT * FROM work_items WHERE parent_id=? AND task_key=?',
                                      (parent_id, task_key)).fetchone()
    if not task or task['skill'] != 'scene' or task['state'] not in ('queued', 'awaiting_input', 'blocked', 'failed'):
        raise LedgerError('scene scope changes require a parked child')
    pid = ledger.last_worker_pid(task['id'])
    launcher.assert_quiescent(task['id'], pid)
    with ledger._transaction():
        ledger._refuse_withdrawn(ledger._owned(parent_id, token))
        current = ledger._row(task['id'])
        if (current['state'] != task['state'] or current['token'] is not None
                or current['generation'] != task['generation'] or ledger.last_worker_pid(task['id']) != pid):
            raise LedgerError('scene child changed during scope authorization')
        ledger.require_no_reservation(task['id'])
        proof = {'scope': scope, 'parent_id': parent_id, 'generation': current['generation']}
        ledger._audit(task['id'], 'scene_scope_authorized', details=proof)
        cp = json.loads(current['checkpoint'])
        if (current['state'] == 'awaiting_input' and current['stage'] == 'scene-scope-missing'
                and cp.get('pending_reason') == 'waiting' and not ledger.human_question(task['id'])):
            from .ledger import _json
            cp.pop('pending_question', None); cp.pop('pending_reason', None)
            ledger._set_state(task['id'], 'queued', 'parent supplied exact scene asset scope',
                              stage='scene-scope-ready', checkpoint=_json(cp))
    return proof


def _guid(data):
    try:
        text = data.decode('utf-8') if isinstance(data, bytes) else data
    except UnicodeError:
        raise LedgerError('scene metadata must be UTF-8') from None
    matches = re.findall(r'^guid: ([0-9a-fA-F]{32})\s*$', text, re.M)
    if len(matches) != 1 or len(re.findall(r'^guid:', text, re.M)) != 1:
        raise LedgerError('scene asset metadata requires exactly one valid GUID')
    return matches[0].lower()


def verify_scope(ledger, item, trees, client, commit):
    scope = scope_for(ledger, item)
    base = item['task_client_base']
    changed = set(filter(None, trees.git_in(client, 'diff', '--name-only', '--no-renames', '--no-ext-diff',
                                            '--no-textconv', '-z', base, commit).split('\0')))
    allowed = set(scope['assets']) | {p + '.meta' for p in scope['assets']}
    from .presentation_repair import inherited_paths
    inherited = inherited_paths(ledger, item, trees, client, commit)
    # New nested asset folders need metadata too, but existing folder identities
    # cannot be rewritten as a side effect of declaring one child asset.
    for name in scope['assets']:
        for folder in PurePosixPath(name).parents:
            if str(folder) in ('Assets', '.'):
                break
            path = str(folder) + '.meta'
            if not trees.git_in(client, 'ls-tree', base, '--', path):
                allowed.add(path)
    if changed - allowed - inherited:
        raise LedgerError('scene diff exceeds its declared asset scope: ' + ', '.join(sorted(changed - allowed - inherited)[:10]))
    inventory = []
    for name in sorted((changed - inherited) | {p + '.meta' for p in scope['assets'] if (client / p).exists()}):
        path = client / name
        _plain_path(path)
        if not path.exists():
            inventory.append({'path': name, 'deleted': True})
            continue
        if not path.is_file() or path.stat().st_nlink != 1:
            raise LedgerError('scene assets must be ordinary files without hardlink aliases')
        entry = trees.git_in(client, 'ls-tree', commit, '--', name)
        if not entry.startswith('100644 ') and not entry.startswith('100755 '):
            raise LedgerError('scene assets must be committed ordinary files')
        from .uploads import _read_regular
        data = _read_regular(path, 256 << 20)
        from .fgui_source import _git, _POINTER
        committed = _git(trees, client, ['show', commit + ':' + name], repo='Farm-Client', limit=256 << 20)
        pointer = _POINTER.fullmatch(committed)
        text_asset = path.suffix.lower() in ('.meta', '.unity', '.prefab', '.asset', '.mat', '.anim', '.controller', '.overridecontroller')
        same = data == committed or text_asset and data.replace(b'\r\n', b'\n') == committed.replace(b'\r\n', b'\n')
        if (pointer and (len(data) != int(pointer[2]) or hashlib.sha256(data).hexdigest() != pointer[1].decode('ascii'))
                or not pointer and not same):
            raise LedgerError('scene file bytes differ from their committed content or LFS identity')
        if name.endswith('.meta'):
            guid = _guid(data)
            matches = trees.git_in(client, 'grep', '-l', '-i', '-I', '-E',
                '^guid:[[:space:]]*' + guid + '[[:space:]]*$', commit, '--', ':(glob)Assets/**/*.meta').splitlines()
            if len(matches) != 1:
                raise LedgerError('scene asset GUID collides with another committed asset')
            if trees.git_in(client, 'ls-tree', base, '--', name):
                if _guid(trees.git_in(client, 'show', base + ':' + name)) != guid:
                    raise LedgerError('scene edits must preserve existing asset GUID identities')
        inventory.append({'path': name, 'sha256': hashlib.sha256(data).hexdigest()})
    for asset in scope['assets']:
        if (client / asset).exists() != (client / (asset + '.meta')).exists():
            raise LedgerError('scene assets and their metadata must be added or removed together')
    return {'scope': scope, 'commit': commit, 'baseline': base, 'files': inventory,
            'scope_sha256': hashlib.sha256(_canonical(scope).encode('utf-8')).hexdigest()}


def require_loading(ledger, item, checked):
    if checked['scope']['validation'] != 'unity':
        return
    for r in ledger.connection.execute("SELECT details FROM audit WHERE item_id=? AND kind='scene_assets_loaded'",
                                      (item['id'],)):
        proof = json.loads(r['details'])
        if (proof.get('commit') == checked['commit'] and proof.get('scope_sha256') == checked['scope_sha256']
                and proof.get('generation') == item['generation']):
            return
    raise LedgerError('scene delivery requires verify-scene-assets at its exact candidate and scope')


def verify_loading(ledger, item_id, token, config, api_factory):
    from types import SimpleNamespace
    from .config import Paths
    from .fgui_unity import _reservation
    from .slots import UnityIdentity
    from .workflow import JobWorktrees, repository_path
    from .worktrees import Worktrees
    paths = Paths(config)
    trees = JobWorktrees(Worktrees(paths.repos, paths.worktrees, config.repos), ledger)

    def fence():
        ledger.renew(item_id, token)
        row = ledger._owned(item_id, token)
        ledger._refuse_withdrawn(row)
        api = api_factory()
        issue = api.fetch_issue(row['issue_id'])
        observed = ledger.observe_issue(issue)
        if (row['skill'] != 'scene' or not row['parent_id'] or row['root_repo'] not in (None, 'Farm-Client')
                or not observed['in_scope'] or issue.get('status_type') in TERMINAL_STATUS_TYPES
                or row['requeue_requested']
                or not api.app_user_id or issue.get('delegate_id') != api.app_user_id or issue.get('archived')
                or row['claimed_fingerprint'] != ledger._issue_row(row['issue_id'])['fingerprint']):
            raise LedgerError('scene loading requires the current delegated scene claim')
        return ledger.item(item_id)

    item = fence()
    client = repository_path(trees, 'Farm-Client', item_id)
    commit = trees.head(client)
    branch = ledger.staged_client_branch(item_id, issue_prefix=config.issue_prefix)
    trees.verification_commit('Farm-Client', item_id, commit, expected_branch=branch)
    checked = verify_scope(ledger, item, trees, client, commit)
    workflow = SimpleNamespace(ledger=ledger, item_id=item_id, config=config, trees=trees, paths=paths, fence=fence)
    reservation, slot, entry = _reservation(workflow, commit)
    assets = ','.join(json.dumps(p, ensure_ascii=True) for p in checked['scope']['assets'] if (client / p).exists())
    source = '''if (UnityEngine.Application.isPlaying || UnityEditor.EditorApplication.isPlayingOrWillChangePlaymode
    || UnityEditor.EditorApplication.isCompiling || UnityEditor.EditorApplication.isUpdating)
    throw new System.Exception("Scene loading requires idle Edit Mode");
var paths = new string[] { PATHS };
foreach (var p in paths) {
    var asset = UnityEditor.AssetDatabase.LoadMainAssetAtPath(p);
    if (asset == null) throw new System.Exception("Cannot load " + p);
    if (asset is UnityEngine.GameObject go)
        foreach (var component in go.GetComponentsInChildren<UnityEngine.MonoBehaviour>(true))
            if (component == null) throw new System.Exception("Missing script in " + p);
}
return new { assets = paths, loaded = true };'''.replace('PATHS', assets)
    identity = UnityIdentity(Path(__file__).with_name('probes') / 'editor-readiness.cs.txt')
    target = {'repository': slot['folder'], 'commit_sha': commit, 'build_target': entry['build_target']}
    if identity.probe(slot, target).get('aggregate') != 'match':
        raise LedgerError('scene loading requires verified Editor identity')
    fence()
    result = identity._client(slot).call_tool('execute_code', {'action': 'execute', 'code': source, 'safety_checks': True}).get('result')
    after = identity.probe(slot, target)
    fence()
    current, _, _ = _reservation(workflow, commit)
    expected = [p for p in checked['scope']['assets'] if (client / p).exists()]
    if (current['reservation_id'] != reservation['reservation_id'] or after.get('aggregate') != 'match'
            or not isinstance(result, dict) or result.get('loaded') is not True or result.get('assets') != expected
            or verify_scope(ledger, item, trees, client, commit) != checked):
        raise LedgerError('scene loading did not establish unchanged assets and successful loading')
    proof = {**checked, 'generation': item['generation'], 'reservation_id': reservation['reservation_id'], 'result': result}
    with ledger._transaction():
        ledger._owned(item_id, token)
        ledger._audit(item_id, 'scene_assets_loaded', details=proof)
    return proof
