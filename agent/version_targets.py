"""Controller-owned branch selection and durable per-job starting points.

The issue requests a target; only the controller resolves it against configured
remotes. A worker checkpoint cannot select, replace or forget a target.
"""
import hashlib
import json
import re
import time

GROUP = 'Target version'
VERSION = re.compile(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)')
RELEASE = re.compile(r'release/((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))')


class TargetError(ValueError):
    pass


def branch_name(value):
    # A branch name, never an arbitrary revision expression, URL or refspec.
    from .worktrees import SAFE_BRANCH
    from .ledger import _git_accepts
    if (not isinstance(value, str) or not SAFE_BRANCH.fullmatch(value)
            or value.startswith(('-', '/', 'refs/')) or value.endswith('/')
            or value in ('HEAD', '@') or not _git_accepts(value)):
        raise TargetError('target must name a valid branch, without origin/ or refs/ prefixes')
    if value.startswith('origin/'):
        raise TargetError('target branch must omit the origin/ prefix')
    return value


def settings(block, repos):
    if not isinstance(block, dict) or set(block) - {'source_repo', 'sync_labels', 'sync_seconds', 'overrides'}:
        raise ValueError('version_targets accepts source_repo, sync_labels, sync_seconds and overrides')
    result = {'source_repo': 'Farm-Client', 'sync_labels': False, 'sync_seconds': 300, 'overrides': {}, **block}
    if type(result['sync_labels']) is not bool:
        raise ValueError('version_targets.sync_labels must be boolean')
    if type(result['sync_seconds']) is not int or not 30 <= result['sync_seconds'] <= 86400:
        raise ValueError('version_targets.sync_seconds must be 30..86400 seconds')
    if not isinstance(result['source_repo'], str) or (result['sync_labels'] and result['source_repo'] not in repos):
        raise ValueError('version_targets.source_repo must name a configured repository')
    overrides = result['overrides']
    if not isinstance(overrides, dict):
        raise ValueError('version_targets.overrides must map versions to repository branch maps')
    for version, mapping in overrides.items():
        if version != 'main' and (not isinstance(version, str) or not VERSION.fullmatch(version)):
            raise ValueError('version target override must name main or MAJOR.MINOR')
        if not isinstance(mapping, dict) or set(mapping) - set(repos):
            raise ValueError('version target overrides must name configured repositories')
        for branch in mapping.values():
            branch_name(branch)
    return result


def selection(issue):
    """One explicit line overrides a version label; unrelated labels do nothing."""
    lines = re.findall(r'^[ \t]*Target branch[ \t]*:[ \t]*([^\r\n]*)', issue.get('description') or '', re.M | re.I)
    if lines:
        if len(lines) != 1:
            raise TargetError('use exactly one Target branch: line in the issue description')
        return {'kind': 'branch', 'value': branch_name(lines[0].strip())}
    labels = [v['label'] for v in issue.get('label_groups', []) if v['group'] == GROUP]
    if not labels:
        return {'kind': 'default'}
    if len(labels) != 1 or (labels[0] != 'main' and not VERSION.fullmatch(labels[0])):
        raise TargetError('select one Target version label: main or MAJOR.MINOR')
    return {'kind': 'version', 'value': labels[0]}


def label_input(issue):
    return [v for v in issue.get('label_groups', []) if v['group'] == GROUP]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class Store:
    def __init__(self, ledger):
        self.ledger = ledger

    def get(self, item_id):
        row = self.ledger.connection.execute('SELECT record FROM work_targets WHERE item_id=?', (item_id,)).fetchone()
        return json.loads(row['record']) if row else None

    def current(self, item_id):
        from .execution import single
        item = self.ledger.item(item_id)
        if not single(item):
            return None
        requested = selection(self.ledger.issue(item['issue_id']))
        saved = self.get(item_id)
        if saved and requested != saved['selection']:
            raise TargetError('Target changed after preparation. Restore the original selection or use a new linked issue '
                              'for the new target; existing work and PRs are preserved.')
        if not saved and requested['kind'] != 'default':
            raise TargetError('This job has no recorded branch target. Use a new linked issue for version-specific work.')
        return saved

    def save(self, item, record):
        from .ledger import LedgerError
        with self.ledger._transaction():
            row = self.ledger._row(item['id'])
            if row['state'] != 'queued' or row['generation'] != item['generation'] or row['worker_pid'] is not None:
                raise LedgerError('job changed during target resolution')
            if selection(self.ledger.issue(row['issue_id'])) != record['selection']:
                raise TargetError('Target changed during preparation; retry with the current issue selection')
            existing = self.get(item['id'])
            if existing:
                if existing != record:
                    raise TargetError('job already has a different immutable branch target')
                return existing
            self.ledger.connection.execute('INSERT INTO work_targets(item_id,record) VALUES(?,?)',
                                           (item['id'], json.dumps(record, sort_keys=True)))
            self.ledger._audit(item['id'], 'version_target_selected', details=record)
        return record


def require_current(ledger, item_id):
    from .ledger import LedgerError
    try:
        return Store(ledger).current(item_id)
    except TargetError as exc:
        raise LedgerError(str(exc)) from exc


class TargetController:
    def __init__(self, ledger, trees, config):
        self.ledger, self.trees = ledger, trees
        self.policy = settings(config, trees.remotes)

    def prepare(self, item, repos):
        store = Store(self.ledger)
        issue = self.ledger.issue(item['issue_id'])
        requested = selection(issue)
        saved = store.get(item['id'])
        needs_save = saved is None
        if saved:
            store.current(item['id'])
        else:
            # Continuations inherit the original selection, including its exact
            # commits and remote identities. No config change silently retargets it.
            previous, seen = item.get('predecessor_id'), {item['id']}
            while previous and previous not in seen:
                seen.add(previous)
                prior = self.ledger.item(previous)
                if prior['issue_id'] != item['issue_id']:
                    raise TargetError('target predecessor belongs to another issue')
                saved = store.get(previous)
                if saved:
                    if saved['selection'] != requested:
                        raise TargetError('Continuation target differs; restore its selection or use a new linked issue')
                    break
                previous = prior.get('predecessor_id')
            if not saved:
                # Never guess a new baseline for historical candidates.
                if (self.ledger.last_worker_pid(item['id']) or item.get('predecessor_id')
                        or (self.trees.worktrees_root / item['id']).exists()):
                    if requested['kind'] != 'default':
                        raise TargetError('Existing work has no saved version target; use a new linked issue')
                    return None
                selected = {}
                for repo in repos:
                    branch = None
                    if requested['kind'] == 'branch':
                        branch = requested['value']
                    elif requested['kind'] == 'version':
                        version = requested['value']
                        branch = self.policy['overrides'].get(version, {}).get(repo,
                            'main' if version == 'main' else 'release/' + version)
                    resolved = self.trees.resolve_target(repo, branch)
                    selected[repo] = {**resolved, 'remote': self.trees.remotes[repo]}
                saved = {'schema': 1, 'selection': requested, 'repositories': selected,
                         'selected_at': self.ledger.clock(), 'selection_hash': digest(requested)}
        if set(saved['repositories']) != set(repos):
            raise TargetError('configured repository set differs from the saved target')
        for repo, target in saved['repositories'].items():
            if self.trees.remotes.get(repo) != target['remote']:
                raise TargetError('configured repository differs from the saved target')
        # Validate the entire inherited repository map before touching any clone.
        # Retain objects before recording the durable snapshot. Orphaned pins
        # after a failed transaction are harmless; never remove recovery data.
        for repo, target in saved['repositories'].items():
            self.trees.retain_target(repo, item['id'], target['commit'])
        if needs_save:
            store.save(item, saved)
        return saved


class LabelSync:
    """One designated host owns sync. Errors back off for the normal interval."""
    def __init__(self, trees, api, config, *, clock=time.monotonic):
        self.trees, self.api, self.clock = trees, api, clock
        self.policy = settings(config, trees.remotes)
        self.next_at = 0
        self.busy = False

    def tick(self):
        if not self.policy['sync_labels'] or self.clock() < self.next_at:
            return False
        self.next_at = self.clock() + self.policy['sync_seconds']
        self.busy = True
        try:
            branches = self.trees.remote_branches(self.policy['source_repo'])
            versions = {'main': 'main'} if 'main' in branches else {}
            versions.update({match[1]: name for name in branches if (match := RELEASE.fullmatch(name))})
            self.api.sync_target_versions(versions)
            return True
        finally:
            self.busy = False
