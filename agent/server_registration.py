"""Ancestry continuity for a registration-only repair; never a test verdict."""
import hashlib
import re

from .worktrees import GIT_ENV, WorktreeError

GATE = 'ci/check_txn_callsites.sh'
ANCHORS = 'ci/degrade.d/guild-compete-disband-a.sh'


def registration_delta(before, after):
    """Accept only sorted additions to the existing exact-set declaration and both anchors."""
    if set(before) != {GATE, ANCHORS} or set(after) != set(before):
        return False
    old = re.findall(r'^allow=([A-Za-z0-9_,]+)$', before[GATE], re.M)
    new = re.findall(r'^allow=([A-Za-z0-9_,]+)$', after[GATE], re.M)
    if len(old) != 1 or len(new) != 1:
        return False
    old_names, new_names = old[0].split(','), new[0].split(',')
    if (old_names != sorted(set(old_names)) or new_names != sorted(set(new_names))
            or not set(old_names) < set(new_names)
            or any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', n) for n in new_names)
            or 'upgradeBuildingImpl' in new_names):
        return False
    old_line, new_line = 'allow=' + old[0], 'allow=' + new[0]
    if before[GATE].replace(old_line, new_line, 1) != after[GATE]:
        return False
    old_anchor = "$'" + old_line + "'"
    old_negative = "$'" + old_line + ",upgradeBuildingImpl'"
    if before[ANCHORS].count(old_anchor) != 1 or before[ANCHORS].count(old_negative) != 1:
        return False
    expected = before[ANCHORS].replace(old_anchor, "$'" + new_line + "'").replace(
        old_negative, "$'" + new_line + ",upgradeBuildingImpl'")
    return expected == after[ANCHORS]


def retained_registration_base(trees, ledger, item_id, candidate, branch):
    """Use only this generation's latest quiescent server round and an exact Git delta."""
    item = ledger.item(item_id)
    rows = [r for r in ledger.reservations() if r['item_id'] == item_id
            and r['generation'] == item['generation'] and r['kind'] == 'server_slot']
    if not rows:
        return None
    latest = max(rows, key=lambda r: r['sequence'])
    source = latest['commit_sha']
    if (source == candidate or latest['state'] != 'released'
            or latest['release_reason'] != 'worker reported quiescent'):
        return None
    record = ledger.server_verification_round(item_id, source, require_released=True)
    if record is None:
        return None
    try:
        paths = trees.verification_repair_delta('farm-hive', item_id, candidate, source,
                                                 expected_branch=branch)
        if set(paths) != {GATE, ANCHORS}:
            return None
        from .workflow import repository_path
        path = repository_path(trees, 'farm-hive', item_id)
        env = {**GIT_ENV, 'GIT_NO_REPLACE_OBJECTS': '1'}
        snapshots, modes = [], {}
        for commit in (source, candidate):
            blobs = {}
            for name in paths:
                entry = trees.git_in(path, 'ls-tree', commit, '--', name, env=env)
                mode = entry.split()[0]
                if mode not in ('100644', '100755') or (name in modes and modes[name] != mode):
                    return None
                modes[name] = mode
                # A second batch header protects the first blob's trailing whitespace
                # from git_in.strip(); validate the Git hash to reject text conversion.
                spec = f'{commit}:{name}'
                batch = trees.git_in(path, 'cat-file', '--batch', env=env,
                                     input_text=spec + '\n' + spec + '\n').encode('utf-8')
                header, payload = batch.split(b'\n', 1)
                oid, kind, length = header.split()
                size = int(length)
                blob = payload[:size]
                if (kind != b'blob' or len(blob) != size or oid.decode() != entry.split()[2]
                        or hashlib.sha1(b'blob ' + length + b'\0' + blob).hexdigest() != oid.decode()):
                    return None
                blobs[name] = blob.decode('utf-8')
            snapshots.append(blobs)
        if not registration_delta(*snapshots):
            return None
        trees.verification_commit('farm-hive', item_id, candidate, expected_branch=branch)
    except (WorktreeError, ValueError, UnicodeError):
        return None
    return {'base_commit': record['base_commit'], 'source_candidate': source,
            'candidate_commit': candidate, 'source_reservation': latest['reservation_id'],
            'changed_paths': paths, 'ancestry_only': True, 'test_verdict': None}
