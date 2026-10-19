"""Host-only continuation of a settled Client comparison capability gap."""
import json
from .config import Paths, linear_api
from .environment import ControllerGuard, check_ownership
from .launcher import Launcher, RUNTIMES
from .ledger import Ledger, LedgerError, _in_scope
from .maintenance import Maintenance


def resume_comparison(config, item_id, candidate, reason):
    """Preserve the recorded round and budgets; grant no comparison/pass verdict."""
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
        raise LedgerError('comparison recovery requires an operator reason of 1 to 500 characters')
    check_ownership(config, require_initialized=True)
    with ControllerGuard(config):
        paths = Paths(config)
        ledger = Ledger(paths.ledger)
        try:
            if not Maintenance(ledger).requested():
                raise LedgerError('comparison recovery requires maintenance pause and a stopped controller')
            item = ledger.item(item_id)
            api = linear_api(config)
            current = api.fetch_issue(item['issue_id'])
            if (current.get('id') != item['issue_id'] or not _in_scope(current) or not api.app_user_id
                    or current.get('delegate_id') != api.app_user_id):
                raise LedgerError('issue must remain open and delegated to FarmBot')
            ledger.observe_issue(current)
            launcher = Launcher(paths.runs, RUNTIMES[config.runtime], config.host)
            with ledger._transaction():
                item = ledger.item(item_id)
                evidence_gap = (item['state'] == 'blocked' and item['stage'] in
                                ('client-comparison-evidence-gap', 'client-verification-evidence-gap'))
                failed_recheck = item['state'] == 'failed' and item['stage'] == 'client-comparison-recheck'
                attribution_gap = (item['state'] == 'blocked'
                                   and item['stage'] == 'client-diagnostic-complete-attribution-blocked')
                baseline_gap = (item['state'] == 'blocked'
                                and item['stage'] == 'client-pinned-baseline-requeue-gap')
                if (item['skill'] not in ('work', 'feature', 'fix') or item.get('parent_id')
                        or not (evidence_gap or failed_recheck or attribution_gap or baseline_gap) or item['root_repo'] != 'Farm-Client'
                        or item.get('next_root_repo') or ledger.human_question(item_id)):
                    raise LedgerError('comparison recovery requires a blocked Client evidence gap, not a human question')
                ledger._refuse_withdrawn(ledger._row(item_id))
                if ledger._unfinished_children(item_id):
                    raise LedgerError('comparison recovery requires completed presentation tasks')
                if ledger.connection.execute("SELECT 1 FROM reservations r JOIN work_items w ON w.id=r.item_id "
                        "WHERE w.issue_id=? AND r.state IN ('queued','active','cancel_requested')", (item['issue_id'],)).fetchone():
                    raise LedgerError('comparison recovery requires all issue resources settled')
                for row in ledger.connection.execute('SELECT id,host,state,worker_pid,token,next_root_repo FROM work_items WHERE issue_id=?',
                                                     (item['issue_id'],)).fetchall():
                    if (row['state'] == 'running' or row['worker_pid'] or row['token'] or row['next_root_repo']
                            or Maintenance(ledger).pending(row['id'])):
                        raise LedgerError('comparison recovery requires settled issue workers and handoffs')
                    if row['host'] not in (None, config.host):
                        raise LedgerError('comparison recovery cannot certify another host')
                    launcher.assert_quiescent(row['id'], ledger.last_worker_pid(row['id']))
                    cleanup = ledger.cleanup_record(row['id'])
                    if cleanup and not cleanup.get('done'):
                        raise LedgerError('comparison recovery requires certified cleanup completion')
                prior_pair = None
                if failed_recheck or attribution_gap:
                    prior = ledger.connection.execute(
                        "SELECT details FROM audit WHERE item_id=? AND kind='operator_comparison_resume' ORDER BY id DESC LIMIT 1",
                        (item_id,)).fetchone()
                    try:
                        details = json.loads(prior['details']) if prior else None
                    except (TypeError, ValueError):
                        details = None
                    if isinstance(details, dict):
                        prior_pair = details.get('verification_pair')
                    if (not isinstance(details, dict) or details.get('generation') != item['generation']
                            or not isinstance(prior_pair, dict) or not prior_pair.get('candidate_reservation')):
                        raise LedgerError('failed comparison recovery requires the prior operator resume for this exact generation and pair')
                pair = ledger.client_verification_pair(item_id, candidate, require_candidate=not baseline_gap,
                    candidate_reservation=prior_pair['candidate_reservation'] if prior_pair else None)
                if not pair:
                    raise LedgerError('comparison recovery requires the retained completed candidate pair')
                if baseline_gap:
                    from .baseline_resume import pinned_baseline
                    pinned_baseline(ledger, item_id, candidate, pair['baseline_reservation'])
                if prior_pair is not None and prior_pair != pair:
                    raise LedgerError('failed comparison recovery requires the prior operator resume for this exact generation and pair')
                stage = 'client-baseline-resume' if baseline_gap else 'client-comparison-recheck'
                ledger._set_state(item_id, 'queued', reason, needs_resource=None, stage=stage)
                ledger._audit(item_id, 'operator_comparison_resume', reason,
                              {'generation': item['generation'], 'verification_pair': pair})
                return {'item': item_id, 'state': 'queued', 'stage': stage,
                        'generation': item['generation'], 'verification_pair': pair,
                        'verification_required': True, 'maintenance_paused': True}
        finally:
            ledger.close()
