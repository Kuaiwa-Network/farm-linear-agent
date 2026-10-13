"""Opt-in controller preparation of committed Go dependency inputs.

Workers request a digest, never a path, command, environment or download URL.
Only module download/verification runs on the host: no package build, tool
execution, generate, installation or repository code. Audit is the durable queue.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import tempfile
from uuid import uuid4
import zipfile

from .fgui_source import _git
from .go_cache_process import run
from .ledger import LedgerError
from .preview_upload import _plain_path
from .workflow import JobWorktrees, repository_path

REQUEST = 'go_dependencies_requested'
RESULT = 'go_dependencies_result'
MAX_INPUT = 2 << 20


def digest(value):
    return hashlib.sha256(value).hexdigest()


def module_path(value):
    if value == '.':
        return value
    if (not isinstance(value, str) or len(value) > 200
            or any(not re.fullmatch(r'[A-Za-z0-9_-]+', p) for p in value.split('/'))):
        raise ValueError('Go module path must be a portable relative directory or dot')
    return value


def validate_settings(block, local_root, repos):
    if block == {}:
        return
    required = {'go', 'go_sha256', 'git', 'gh', 'cache_root', 'work_root', 'modules', 'private_prefixes'}
    if not isinstance(block, dict) or set(block) != required:
        raise ValueError('go_dependencies requires explicit tools, digest, roots, modules and private prefixes')
    if not isinstance(block['go_sha256'], str) or not re.fullmatch('[0-9a-f]{64}', block['go_sha256']):
        raise ValueError('go_dependencies requires a fixed native Go executable SHA256')
    paths = {}
    for key in ('go', 'git', 'gh', 'cache_root', 'work_root'):
        raw = block[key]
        if (not isinstance(raw, str) or not raw.isprintable() or not Path(raw).is_absolute()
                or any(c in raw for c in '\"\'`$!')):
            raise ValueError('go_dependencies paths must be absolute native paths without shell syntax')
        paths[key] = Path(raw).resolve()
        if paths[key].is_relative_to(Path(local_root).resolve()):
            raise ValueError('go_dependencies tools and storage must stay outside worker state/write roots')
        if key.endswith('_root') and Path(local_root).resolve().is_relative_to(paths[key]):
            raise ValueError('Go dependency storage cannot contain worker state/write roots')
    if any(paths[a].is_relative_to(paths[b]) or paths[b].is_relative_to(paths[a])
           for a, b in (('cache_root', 'work_root'),)):
        raise ValueError('Go dependency cache and staging roots must be separate')
    if any(paths[k].is_relative_to(paths[r]) for k in ('go', 'git', 'gh') for r in ('cache_root', 'work_root')):
        raise ValueError('Go dependency tools must stay outside cache and staging storage')
    if not isinstance(block['modules'], dict) or not block['modules']:
        raise ValueError('go_dependencies.modules requires configured repositories')
    for repo, modules in block['modules'].items():
        if repo not in repos or not isinstance(modules, list) or not modules or len(modules) > 8:
            raise ValueError('Go dependency module selection must belong to configured repositories')
        if len(set(module_path(p) for p in modules)) != len(modules):
            raise ValueError('Go dependency module paths must be distinct')
    prefixes = block['private_prefixes']
    if (not isinstance(prefixes, list) or not prefixes or len(prefixes) > 8
            or any(not isinstance(p, str) or not re.fullmatch(r'github\.com/[A-Za-z0-9_-]+', p) for p in prefixes)):
        raise ValueError('Go dependency private prefixes must name explicit GitHub organizations')


def owner(config):
    return {'schema': 'farmbot-go-dependencies-v1', 'instance_id': config.instance_id,
            'app_user_id': config.expected_app_user_id, 'local_root':str(Path(config.local_root).resolve())}


def settings_digest(config):
    return digest(json.dumps(config.go_dependencies,sort_keys=True).encode('utf-8'))


def initialize(config, *, adopt_existing=False):
    """Explicit trusted operator action; serve never adopts an unmarked cache."""
    validate_settings(config.go_dependencies, config.local_root, config.repos)
    if not config.go_dependencies:
        raise ValueError('Go dependency preparation is not configured')
    for key in ('cache_root', 'work_root'):
        path = Path(config.go_dependencies[key])
        _plain_path(path)
        path.mkdir(parents=True, exist_ok=True)
        marker = path / '.farmbot-go-dependencies.json'
        if marker.exists():
            if json.loads(marker.read_text(encoding='utf-8')) != owner(config):
                raise ValueError('Go dependency storage belongs to another instance')
        else:
            if any(path.iterdir()) and not adopt_existing:
                raise ValueError('Existing Go dependency storage requires explicit adoption')
            with marker.open('x', encoding='utf-8') as stream:
                json.dump(owner(config), stream)
    return {'initialized': True}


def roots(config):
    block = config.go_dependencies
    for key in ('cache_root', 'work_root'):
        root = Path(block[key]); _plain_path(root)
        marker = root / '.farmbot-go-dependencies.json'; _plain_path(marker)
        if json.loads(marker.read_text(encoding='utf-8')) != owner(config):
            raise ValueError('Go dependency storage ownership mismatch')
    for key in ('go', 'git', 'gh'):
        tool = Path(block[key]); _plain_path(tool)
        if not tool.is_file():
            raise ValueError('Configured Go dependency tool unavailable')
    if digest(Path(block['go']).read_bytes()) != block['go_sha256']:
        raise ValueError('Configured Go executable digest changed')


def snapshot(trees, item, module, commit):
    repo = item['root_repo']
    trees.verification_commit(repo, item['id'], commit)
    path = repository_path(trees, repo, item['id'])
    prefix = '' if module == '.' else module + '/'
    values = {}
    for name in ('go.mod', 'go.sum'):
        tracked = prefix + name
        mode = _git(trees, path, ['ls-tree', commit, '--', tracked], repo=repo).split(b' ', 1)[0]
        if mode != b'100644' and mode != b'100755':
            raise LedgerError('Go dependency inputs must be committed regular files')
        values[name] = _git(trees, path, ['show', commit + ':' + tracked], repo=repo, limit=MAX_INPUT)
    return values


def request(ledger, trees, config, item_id, token, module, commit, *, retry=False):
    """Authenticate a bounded input request; no network or host cache write."""
    ledger.renew(item_id, token)
    item = ledger.item(item_id)
    if (item['skill'] not in ('work', 'feature', 'fix') or item.get('parent_id')
            or item['root_repo'] not in config.go_dependencies.get('modules', {})
            or module not in config.go_dependencies['modules'][item['root_repo']]):
        raise LedgerError('Go dependency preparation is unavailable for this repository/module')
    module_path(module)
    values = snapshot(trees, item, module, commit)
    inputs = {name: digest(raw) for name, raw in values.items()}
    with ledger._transaction():
        row = ledger._owned(item_id, token)
        ledger._refuse_withdrawn(row)
        ledger.require_no_reservation(item_id)
        matching = []
        for record in ledger.connection.execute('SELECT details FROM audit WHERE item_id=? AND kind=? ORDER BY id DESC',
                                                (item_id, REQUEST)):
            prior = json.loads(record['details'])
            if (prior['generation'] == item['generation'] and prior['repo'] == item['root_repo']
                    and prior['module'] == module and prior['commit'] == commit and prior['inputs'] == inputs
                    and prior.get('settings') == settings_digest(config)):
                matching.append(prior)
        if matching:
            result = status(ledger, config, item_id, matching[0]['id'])
            if not retry or result['state'] not in ('failed', 'cancelled'):
                return result
            if len(matching) >= 3:
                raise LedgerError('Go dependency preparation retry limit reached; retain evidence for host maintenance')
        details = {'id': str(uuid4()), 'repo': item['root_repo'], 'module': module, 'commit': commit,
                   'generation': row['generation'], 'inputs': inputs, 'settings':settings_digest(config),
                   'claim_fence':digest(row['token'].encode('utf-8'))}
        ledger._audit(item_id, REQUEST, 'host Go cache preparation', details)
    return {'id': details['id'], 'state': 'pending'}


def status(ledger, config, item_id, request_id):
    requests = [json.loads(r['details']) for r in ledger.connection.execute(
        'SELECT details FROM audit WHERE item_id=? AND kind=?', (item_id, REQUEST))]
    matching = next((r for r in requests if r['id'] == request_id), None)
    if matching is None:
        raise LedgerError('Unknown Go dependency request for this item')
    for row in ledger.connection.execute('SELECT details FROM audit WHERE item_id=? AND kind=? ORDER BY id DESC',
                                         (item_id, RESULT)):
        result = json.loads(row['details'])
        if result['id'] == request_id:
            if result['state'] == 'ready' and matching.get('settings') != settings_digest(config):
                return {'id':request_id, 'state':'cancelled', 'reason':'Host settings changed; request the currently configured cache.'}
            return result
    return {'id': request_id, 'state': 'pending'}


def json_stream(raw):
    text = raw.decode('utf-8'); decoder = json.JSONDecoder(); values = []
    while text.strip():
        text = text.lstrip(); value, offset = decoder.raw_decode(text)
        values.append(value); text = text[offset:]
    return values


def environment(block, work, *, online):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(
        ('GO', 'GIT_', 'FARMBOT_', 'CODEX_', 'OPENAI_', 'ANTHROPIC_', 'AZURE_', 'LARKSUITE_'))
        and k.upper() not in ('HOME', 'USERPROFILE', 'TEMP', 'TMP', 'GH_TOKEN', 'GITHUB_TOKEN')}
    env.update(GOENV='off', GOWORK='off', GOTOOLCHAIN='local', GOFLAGS='-mod=readonly',
               GOPROXY='https://proxy.golang.org' if online else 'off', GOSUMDB='sum.golang.org' if online else 'off',
               GOPRIVATE=','.join(p+'/*' for p in block['private_prefixes']),
               GONOPROXY=','.join(p+'/*' for p in block['private_prefixes']) if online else 'none',
               GOVCS='public:off,private:git' if online else '*:off',
               CGO_ENABLED='0', GOMODCACHE=str(work/'modules'), GOCACHE=str(work/'build-cache'),
               GOMAXPROCS='2', GOMEMLIMIT='512MiB',
               GOPATH=str(work/'go-path'), HOME=str(work/'home'), USERPROFILE=str(work/'home'),
               TEMP=str(work/'temp'), TMP=str(work/'temp'),
               PATH=str(Path(block['git']).parent)+os.pathsep+os.environ.get('PATH',''),
               GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=str(work/'empty-git-config'),
               GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='never', GIT_CONFIG_COUNT='3',
               GIT_CONFIG_KEY_0='core.hooksPath', GIT_CONFIG_VALUE_0=os.devnull,
               GIT_CONFIG_KEY_1='core.fsmonitor', GIT_CONFIG_VALUE_1='false',
               GIT_CONFIG_KEY_2='credential.helper',
               GIT_CONFIG_VALUE_2='!'+shlex.quote(block['gh'].replace('\\','/'))+' auth git-credential' if online else '')
    if online:
        env.update(GH_PROMPT_DISABLED='1', GH_NO_UPDATE_NOTIFIER='1')
    # Preserve only the host gh store location, never its token in a worker reply.
    if online and 'GH_CONFIG_DIR' not in env:
        env['GH_CONFIG_DIR'] = str(Path(os.environ.get('APPDATA', Path.home()/'.config'))/'GitHub CLI') if os.name == 'nt' else str(Path.home()/'.config/gh')
    if not online:
        for key in tuple(env):
            if key.upper().startswith(('GH_', 'GITHUB_')): env.pop(key)
    return env


def complete_source(module, cache):
    if module.get('Main'): return True
    module = module.get('Replace') or module
    # list -m omits Dir for some graph-only modules absent from the root sums,
    # even after download all has expanded them. Use Go's canonical cache name.
    def escaped(value):
        return ''.join('!'+c.lower() if 'A' <= c <= 'Z' else c for c in value)
    if module.get('Dir'):
        folder = Path(module['Dir'])
    elif module.get('Path') and module.get('Version'):
        folder = cache/(escaped(module['Path'])+'@'+escaped(module['Version']))
    else:
        return False
    _plain_path(folder)
    if not folder.resolve().is_relative_to(cache.resolve()) or not folder.is_dir(): return False
    _plain_path(folder/'go.mod')
    if (folder/'go.mod').is_file(): return True
    if not module.get('Path') or not module.get('Version'): return False
    archive = (Path(module['GoMod']).with_suffix('.zip') if module.get('GoMod') else
               cache/'cache/download'/escaped(module['Path'])/'@v'/(escaped(module['Version'])+'.zip'))
    _plain_path(archive)
    if not archive.resolve().is_relative_to(cache.resolve()) or not archive.is_file(): return False
    with zipfile.ZipFile(archive) as stream:
        return module['Path']+'@'+module['Version']+'/go.mod' not in stream.namelist()


def prepare(config, values, *, cancelled=lambda: False, runner=run):
    roots(config)
    block = config.go_dependencies
    cache = Path(block['cache_root']); work = Path(block['work_root'])/uuid4().hex
    work.mkdir()
    for part in ('input', 'modules', 'home', 'temp', 'build-cache', 'go-path'):
        (work/part).mkdir()
    (work/'empty-git-config').write_text('', encoding='utf-8')
    for name, raw in values.items(): (work/'input'/name).write_bytes(raw)
    env = environment(block, work, online=True)
    def command(label, *args):
        if cancelled(): raise RuntimeError('Go dependency preparation cancelled')
        return runner([block['go'], *args], cwd=work/'input', env=env, log=work/label,
                      cancelled=cancelled, timeout=600)
    parsed = json.loads(command('parse', 'mod', 'edit', '-json'))
    for replacement in parsed.get('Replace') or []:
        if not (replacement.get('New') or {}).get('Version'):
            raise RuntimeError('Local Go replacements are not eligible for host preparation')
    for dep in parsed.get('Require') or []:
        path = dep['Path']
        if path.startswith('github.com/') and any(path.casefold().startswith(p.casefold()+'/') for p in block['private_prefixes']):
            if not any(path.startswith(p+'/') for p in block['private_prefixes']):
                raise RuntimeError('Private module prefix spelling differs from host allowlist')
    # Most candidates change code without changing dependencies. Prove that the
    # shared cache is complete offline before paying for another staging expansion.
    env = environment(block, work, online=False); env['GOMODCACHE'] = str(cache)
    try:
        graph = json_stream(command('warm-graph', 'list', '-m', '-json', 'all'))
        if len(graph) <= 1024 and all(complete_source(v, cache) for v in graph):
            command('warm-verify', 'mod', 'verify')
            if any((work/'input'/n).read_bytes() != raw for n,raw in values.items()):
                raise RuntimeError('Warm Go dependency inputs changed')
            if cancelled(): raise RuntimeError('Go dependency preparation cancelled')
            roots(config)
            receipt = {'state':'ready', 'cache_root':str(cache), 'inputs':{n:digest(v) for n,v in values.items()},
                       'modules':len(graph), 'added_files':0, 'receipt':str(work/'receipt.json')}
            (work/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n', encoding='utf-8')
            return receipt
    except (RuntimeError, ValueError, OSError, zipfile.BadZipFile):
        # An incomplete cache is the expected preparation trigger, not a human
        # question. The isolated download/verification below establishes readiness.
        pass
    for name, raw in values.items(): (work/'input'/name).write_bytes(raw)
    archives = cache/'cache/download'
    if archives.is_dir():
        _plain_path(archives)
        for file in archives.rglob('*'): _plain_path(file)
        shutil.copytree(archives, work/'modules/cache/download', dirs_exist_ok=True)
    env = environment(block, work, online=True)
    downloaded = json_stream(command('download', 'mod', 'download', '-json', 'all'))
    if len(downloaded) > 512 or any(v.get('Error') for v in downloaded):
        raise RuntimeError('Go module download failed or exceeded host module limit')
    command('online-graph', 'list', '-m', '-json', 'all')
    # Restore the committed sums: download all can supplement a disposable copy.
    for name, raw in values.items(): (work/'input'/name).write_bytes(raw)
    env = environment(block, work, online=False)
    selected = json_stream(command('offline-graph', 'list', '-m', '-json', 'all'))
    if len(selected) > 1024 or any(v.get('Error') for v in selected):
        raise RuntimeError('Go module graph failed or exceeded host limit')
    command('offline-verify', 'mod', 'verify')
    if any((work/'input'/n).read_bytes() != raw for n, raw in values.items()):
        raise RuntimeError('Go dependency inputs changed during offline verification')
    # Append missing expanded source and metadata without rewriting old cache
    # bytes. A cancelled partial publication remains evidence, never readiness.
    for module in downloaded:
        if module.get('Dir'):
            path = Path(module['Dir']); _plain_path(path)
            if not path.resolve().is_relative_to((work/'modules').resolve()) or not path.is_dir():
                raise RuntimeError('Incomplete expanded Go module')
            _plain_path(path/'go.mod')
            if not (path/'go.mod').is_file():
                archive = Path(module['Zip']); _plain_path(archive)
                if not archive.resolve().is_relative_to((work/'modules').resolve()):
                    raise RuntimeError('Go module archive is outside staging')
                with zipfile.ZipFile(archive) as stream:
                    if module['Path']+'@'+module['Version']+'/go.mod' in stream.namelist():
                        raise RuntimeError('Expanded Go module is missing its archived go.mod')
    if cancelled(): raise RuntimeError('Go dependency preparation cancelled')
    roots(config)
    added = 0
    for file in (work/'modules').rglob('*'):
        if file.is_symlink() or getattr(file, 'is_junction', lambda: False)():
            raise RuntimeError('Go cache contains a linked path')
        if not file.is_file(): continue
        rel = file.relative_to(work/'modules')
        if rel.parts[:2] == ('cache','vcs'): continue
        target = cache/rel; _plain_path(target)
        if target.exists():
            is_index = rel.parts[:2] == ('cache', 'download') and file.name == 'list'
            if not is_index and digest(target.read_bytes()) != digest(file.read_bytes()):
                raise RuntimeError('Existing Go cache object differs; retained evidence requires host review')
            continue
        if cancelled(): raise RuntimeError('Go dependency preparation cancelled')
        target.parent.mkdir(parents=True, exist_ok=True)
        # Stage on the destination volume. link() publishes exclusively without
        # replacing a concurrent writer; remove only our own temporary name.
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.farmbot-publish-', delete=False) as stream:
            temp = Path(stream.name)
            with file.open('rb') as source: shutil.copyfileobj(source, stream)
            stream.flush(); os.fsync(stream.fileno())
        try:
            _plain_path(target)
            os.link(temp, target)
        finally:
            temp.unlink()
        added += 1
    env['GOMODCACHE'] = str(cache)
    command('shared-offline-graph', 'list', '-m', '-json', 'all')
    command('shared-offline-verify', 'mod', 'verify')
    if any((work/'input'/n).read_bytes() != raw for n, raw in values.items()):
        raise RuntimeError('Go dependency inputs changed during shared cache verification')
    receipt = {'state':'ready', 'cache_root':str(cache), 'inputs':{n:digest(v) for n,v in values.items()},
               'modules':len(downloaded), 'added_files':added, 'receipt':str(work/'receipt.json')}
    (work/'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n', encoding='utf-8')
    return receipt


class Controller:
    def __init__(self, ledger, trees, config, *, stop=lambda: False, preparer=prepare):
        self.ledger, self.trees, self.config, self.stop, self.preparer = ledger, JobWorktrees(trees, ledger), config, stop, preparer

    def active(self, item_id, request):
        item = self.ledger.item(item_id)
        claim = self.ledger._row(item_id)['token']
        return (not self.stop() and item['state'] == 'running' and item['generation'] == request['generation']
                and item['root_repo'] == request['repo'] and item['withdraw_deadline'] is None
                and item['lease_expires_at'] > self.ledger.clock() and claim
                and digest(claim.encode('utf-8')) == request.get('claim_fence'))

    def tick(self):
        rows = self.ledger.connection.execute('SELECT item_id,details FROM audit WHERE kind=? ORDER BY id', (REQUEST,)).fetchall()
        for row in rows:
            item_id, value = row['item_id'], json.loads(row['details'])
            if status(self.ledger, self.config, item_id, value['id'])['state'] != 'pending': continue
            if not self.active(item_id, value):
                result = {'state':'cancelled'}
            else:
                try:
                    item = self.ledger.item(item_id)
                    if value.get('settings') != settings_digest(self.config):
                        raise LedgerError('Go dependency host settings changed; request the current configuration')
                    if value['module'] not in self.config.go_dependencies['modules'].get(value['repo'], []):
                        raise LedgerError('Go module is no longer configured')
                    values = snapshot(self.trees, item, value['module'], value['commit'])
                    if {n:digest(v) for n,v in values.items()} != value['inputs']:
                        raise LedgerError('Go dependency request inputs changed')
                    result = self.preparer(self.config, values, cancelled=lambda: not self.active(item_id, value))
                    if not self.active(item_id, value): result = {'state':'cancelled'}
                except Exception:
                    # Tool/Git diagnostics remain in host storage, never echo a
                    # credential, repository-controlled error or signed URL.
                    result = {'state':'failed', 'reason':'Host Go dependency preparation failed; inspect retained host diagnostics and tool/authentication/cache ownership.',
                              'diagnostics_root':self.config.go_dependencies['work_root']}
            with self.ledger._transaction():
                if result['state'] == 'ready' and not self.active(item_id, value):
                    result = {'state':'cancelled'}
                self.ledger._audit(item_id, RESULT, 'host Go cache preparation result', {**result, 'id':value['id']})
            return True
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description='Trusted host Go cache initialization; no service starts')
    parser.add_argument('--config', required=True)
    parser.add_argument('--adopt-existing', action='store_true')
    args = parser.parse_args(argv)
    from .config import load_config
    print(json.dumps(initialize(load_config(args.config), adopt_existing=args.adopt_existing)))


if __name__ == '__main__': main()
