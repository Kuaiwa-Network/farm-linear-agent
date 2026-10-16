"""Explicit host recovery of a settled server deadline, reusing completed packages."""
import json
from pathlib import Path

from .config import Paths, linear_api
from .environment import ControllerGuard, check_ownership
from .launcher import Launcher, RUNTIMES, _write_worker_file
from .ledger import Ledger, LedgerError, _in_scope
from .maintenance import Maintenance
from .server_evidence import digest, verify_manifest


def resume_server(config, item_id, manifest_path, reason):
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 500:
        raise LedgerError('server recovery requires an operator reason')
    check_ownership(config, require_initialized=True)
    with ControllerGuard(config):
        paths = Paths(config)
        ledger = Ledger(paths.ledger)
        try:
            if not Maintenance(ledger).requested():
                raise LedgerError('server recovery requires maintenance pause and a stopped controller')
            item = ledger.item(item_id)
            api = linear_api(config)
            current = api.fetch_issue(item['issue_id'])
            if (current.get('id') != item['issue_id'] or not _in_scope(current) or not api.app_user_id
                    or current.get('delegate_id') != api.app_user_id):
                raise LedgerError('issue must remain open and delegated to FarmBot')
            ledger.observe_issue(current)
            manifest = json.loads(Path(manifest_path).read_text(encoding='utf-8'))
            candidate = manifest['candidate_commit']
            launcher = Launcher(paths.runs, RUNTIMES[config.runtime], config.host)
            with ledger._transaction():
                item = ledger.item(item_id)
                last = ledger.connection.execute("SELECT kind,reason FROM audit WHERE item_id=? "
                    "AND kind IN ('cancelled','blocked','queued','delivered','failed') ORDER BY id DESC LIMIT 1", (item_id,)).fetchone()
                legacy = item['state'] == 'cancelled' and last and last['reason'] == 'server integration slot timed out'
                timeout = item['state'] == 'blocked' and item['stage'] == 'server-verification-timeout'
                if (not (legacy or timeout) or item['skill'] not in ('work', 'feature', 'fix')
                        or item.get('parent_id') or item['root_repo'] != 'farm-hive' or ledger.human_question(item_id)):
                    raise LedgerError('server recovery requires a technical server timeout, not Stop or a human question')
                ledger._refuse_withdrawn(ledger._row(item_id))
                if ledger._unfinished_children(item_id):
                    raise LedgerError('server recovery requires completed child tasks')
                if ledger.connection.execute("SELECT 1 FROM reservations r JOIN work_items w ON w.id=r.item_id "
                    "WHERE w.issue_id=? AND r.state IN ('queued','active','cancel_requested')", (item['issue_id'],)).fetchone():
                    raise LedgerError('server recovery requires all issue resources settled')
                for row in ledger.connection.execute('SELECT * FROM work_items WHERE issue_id=?', (item['issue_id'],)).fetchall():
                    if (row['state'] == 'running' or row['worker_pid'] or row['token'] or row['next_root_repo']
                            or Maintenance(ledger).pending(row['id']) or row['host'] not in (None, config.host)):
                        raise LedgerError('server recovery requires settled local workers')
                    launcher.assert_quiescent(row['id'], ledger.last_worker_pid(row['id']))
                    cleanup = ledger.cleanup_record(row['id'])
                    if cleanup and not cleanup.get('done'):
                        raise LedgerError('server recovery requires completed cleanup')
                round_ = ledger.server_verification_round(item_id, candidate)
                if not round_ or manifest.get('base_commit') != round_['base_commit']:
                    raise LedgerError('server recovery requires the original candidate and base')
                res = ledger.reservation(manifest['reservation_id'])
                if (not res or res['item_id'] != item_id or res['generation'] != item['generation']
                        or res['commit_sha'] != candidate or res['kind'] != 'server_slot'
                        or res['state'] != 'cancelled' or res['release_reason'] != 'server services settled'):
                    raise LedgerError('server recovery requires the settled timed-out reservation')
                run = paths.runs / item_id
                result = verify_manifest(run, manifest, candidate=candidate, generation=item['generation'],
                                         reservation=res['reservation_id'])
                # The host-reviewed manifest must cover the trusted diff; no worker
                # may narrow acceptance merely because some package happened to pass.
                from .server_test_plan import changed_scope, check_scope
                from .worktrees import Worktrees
                trees = Worktrees(paths.repos, paths.worktrees, config.repos)
                clone = trees.ensure_clone('farm-hive')
                names = trees.git_in(clone, 'diff', '--name-only', '--no-renames', '-z', round_['base_commit'], candidate)
                changes = changed_scope([name for name in names.split('\0') if name])
                check_scope(manifest['plan'], changes, manifest.get('scope_reason'))
                saved = run / 'operator-server-evidence-review.json'
                _write_worker_file(saved, json.dumps(manifest, ensure_ascii=False, indent=2), sync=True)
                record = {'generation': item['generation'], 'candidate_commit': candidate,
                          'base_commit': round_['base_commit'], 'reservation_id': res['reservation_id'],
                          'run_root': str(run.resolve()), 'manifest_sha256': digest(saved), 'result': result}
                ledger._audit(item_id, 'operator_server_evidence_resume', reason, record)
                ledger._set_state(item_id, 'queued', reason, needs_resource=None, stage='server-evidence-recheck')
                return {'item': item_id, 'state': 'queued', 'stage': 'server-evidence-recheck',
                        'candidate_commit': candidate, 'result': result, 'maintenance_paused': True}
        finally:
            ledger.close()


def retained_round(ledger, item_id, candidate, original):
    """Publication still checks source/destination; this replaces only the release proof."""
    row = ledger.connection.execute("SELECT details FROM audit WHERE item_id=? "
        "AND kind='operator_server_evidence_resume' ORDER BY id DESC LIMIT 1", (item_id,)).fetchone()
    if not row:
        return None
    proof = json.loads(row['details'])
    if (proof['generation'] != original['generation'] or proof['candidate_commit'] != candidate
            or proof['base_commit'] != original['base_commit']):
        return None
    reservation = ledger.reservation(proof['reservation_id'])
    if (not reservation or reservation['item_id'] != item_id or reservation['commit_sha'] != candidate
            or reservation['generation'] != proof['generation'] or reservation['kind'] != 'server_slot'
            or reservation['state'] != 'cancelled' or reservation['release_reason'] != 'server services settled'):
        return None
    if ledger.connection.execute("SELECT 1 FROM reservations WHERE item_id=? AND kind='server_slot' "
            "AND generation=? AND sequence>?", (item_id, proof['generation'], reservation['sequence'])).fetchone():
        return None  # A newer verification attempt must be assessed on its own evidence.
    from .server_evidence import checked_file
    manifest_path = checked_file(proof['run_root'], 'operator-server-evidence-review.json', proof['manifest_sha256'])
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    verify_manifest(proof['run_root'], manifest, candidate=candidate, generation=proof['generation'],
                    reservation=proof['reservation_id'])
    return {**original, 'retained_evidence_reservation': proof['reservation_id'],
            'evidence_manifest_sha256': proof['manifest_sha256']}
