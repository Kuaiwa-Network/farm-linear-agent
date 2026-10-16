"""Host-only continuation after correcting a parked UI scope verifier."""
from .config import Paths, linear_api
from .environment import ControllerGuard, check_ownership
from .launcher import Launcher, RUNTIMES
from .ledger import ACTIVE_STATES, Ledger, LedgerError, _in_scope
from .maintenance import Maintenance


def resume_scope(config, item_id, reason):
    """Requeue one technical wait; never answer a question or certify its candidate."""
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
        raise LedgerError("UI scope recovery requires an operator reason of 1 to 500 characters")
    check_ownership(config, require_initialized=True)
    with ControllerGuard(config):
        paths = Paths(config)
        ledger = Ledger(paths.ledger)
        try:
            if not Maintenance(ledger).requested():
                raise LedgerError("UI scope recovery requires maintenance pause and a stopped controller")
            item = ledger.item(item_id)
            api = linear_api(config)
            current = api.fetch_issue(item['issue_id'])
            if (current.get('id') != item['issue_id'] or not _in_scope(current) or not api.app_user_id
                    or current.get('delegate_id') != api.app_user_id):
                raise LedgerError("issue must remain open and delegated to FarmBot")
            ledger.observe_issue(current)
            launcher = Launcher(paths.runs, RUNTIMES[config.runtime], config.host)
            with ledger._transaction():
                item = ledger.item(item_id)
                if (item['skill'] != 'fgui' or not item.get('parent_id')
                        or item['state'] != 'awaiting_input' or item['stage'] != 'client-scope-stage-limit'
                        or item['root_repo'] != 'Farm-Client' or item.get('next_root_repo')
                        or item['checkpoint'].get('pending_reason') != 'waiting'
                        or ledger.human_question(item_id)):
                    raise LedgerError("UI scope recovery requires an internal Client scope technical wait, not a human question")
                parent = ledger.item(item['parent_id'])
                if parent['skill'] != 'work' or parent['state'] not in ACTIVE_STATES:
                    raise LedgerError("UI scope recovery requires its active general parent")
                ledger._refuse_withdrawn(ledger._row(item_id))
                if not item.get('task_client_base'):
                    raise LedgerError("UI scope recovery requires its retained controller Client entry")
                if ledger.connection.execute("SELECT 1 FROM reservations r JOIN work_items w ON w.id=r.item_id "
                        "WHERE w.issue_id=? AND r.state IN ('queued','active','cancel_requested')", (item['issue_id'],)).fetchone():
                    raise LedgerError("UI scope recovery requires all issue resources settled")
                for row in ledger.connection.execute('SELECT id,host,state,worker_pid,token,next_root_repo FROM work_items WHERE issue_id=?',
                                                     (item['issue_id'],)).fetchall():
                    if (row['state'] == 'running' or row['worker_pid'] or row['token'] or row['next_root_repo']
                            or Maintenance(ledger).pending(row['id'])):
                        raise LedgerError("UI scope recovery requires settled issue workers and handoffs")
                    if row['host'] not in (None, config.host):
                        raise LedgerError("UI scope recovery cannot certify another host's workers")
                    launcher.assert_quiescent(row['id'], ledger.last_worker_pid(row['id']))
                from .fgui_client import installation_record
                receipt = (item['checkpoint'].get('plan', {}).get('ui') or {}).get('receipt_id')
                if not receipt or installation_record(ledger, item_id, receipt) is None:
                    raise LedgerError("UI scope recovery requires its retained certified installation")
                ledger._set_state(item_id, 'queued', reason, needs_resource=None, stage='client-scope-recheck')
                resumed = ledger.item(item_id)
                ledger._audit(item_id, 'operator_ui_scope_resume', reason,
                              {'generation': item['generation'], 'receipt_id': receipt, 'parent_id': parent['id']})
                return {'item': resumed['id'], 'state': resumed['state'], 'stage': resumed['stage'],
                        'generation': resumed['generation'], 'receipt_id': receipt,
                        'verification_required': True, 'maintenance_paused': True}
        finally:
            ledger.close()
