"""Claim-bound resource tools for a continuing worker. No acceptance verdicts."""
import json
from pathlib import Path

from .config import Paths
from .execution import queue_resource, resource_status, single
from .ledger import LedgerError
from .worktrees import Worktrees


def context(args, ledger, config, token):
    ledger.renew(args.item, token)
    item = ledger.item(args.item)
    if not single(item):
        raise LedgerError('this tool requires a single-worker job')
    ledger._refuse_withdrawn(ledger._row(args.item))
    paths = Paths(config)
    if Path(args.db).resolve() != paths.ledger.resolve():
        raise LedgerError('resource tools require the configured host ledger')
    return item, paths


def request(args, ledger, config, token):
    item, paths = context(args, ledger, config, token)
    if any(getattr(args, key, None) for key in ('baseline', 'reuse_candidate_reservation',
            'resume_baseline_reservation', 'repair_candidate_reservation')):
        raise LedgerError('single-worker requests select an explicit commit; staged comparison flags are retired')
    repo = {'unity_slot': 'Farm-Client', 'server_slot': 'farm-hive'}.get(args.resource)
    if repo is None:
        raise LedgerError('unsupported resource')
    if args.resource == 'unity_slot' and not config.slots:
        raise LedgerError('no Unity resource configured')
    if args.resource == 'server_slot' and not config.server_slots:
        raise LedgerError('no server services configured')
    trees = Worktrees(paths.repos, paths.worktrees, config.repos)
    # Clean exact HEAD and owned issue branch, without current-main merge or a
    # baseline/candidate result gate. Source validity belongs to repository tests.
    trees.verification_commit(repo, item['id'], args.commit)
    from .ledger import plan_issue_branches
    branch = trees.git_in(paths.worktrees / item['id'] / repo, 'branch', '--show-current')
    plan_issue_branches({'prs': {repo: [{'role': 'issue', 'branch': branch}]}},
                        ledger.issue(item['issue_id'])['identifier'], config.issue_prefix, reserved=True)
    test_plan = None
    integration_plan = None
    if getattr(args, 'integration_plan', None):
        from .integration import validate_plan
        from .resource_admission import validate_settings
        if args.resource!='unity_slot' or args.mode!='interactive' or not config.server_slots:
            raise LedgerError('combined verification requires Unity and configured server services')
        if getattr(args, 'server_test_plan', None):
            raise LedgerError('combined verification uses a full server lease; do not mix a Go test plan')
        budgets = validate_settings(config.resource_admission)
        if budgets['unity_mib'] + budgets['server_mib'] > budgets['max_reserved_mib']:
            raise LedgerError('integration needs budget for one Unity Editor plus one full server lease')
        integration_plan = validate_plan(json.loads(Path(args.integration_plan).read_text(encoding='utf-8')))
        trees.verification_commit('farm-hive', item['id'], integration_plan['server_commit'])
        branch = trees.git_in(paths.worktrees / item['id'] / 'farm-hive', 'branch', '--show-current')
        plan_issue_branches({'prs': {'farm-hive':[{'role':'issue','branch':branch}]}},
                            ledger.issue(item['issue_id'])['identifier'], config.issue_prefix, reserved=True)
    if args.resource == 'server_slot' and args.server_test_plan:
        test_plan = json.loads(Path(args.server_test_plan).read_text(encoding='utf-8'))
    return queue_resource(ledger, item['id'], token, args.resource, args.mode, args.commit,
                          test_plan=test_plan, integration_plan=integration_plan)


def status(args, ledger, config, token):
    item, paths = context(args, ledger, config, token)
    result = resource_status(ledger, item['id'], token)
    if result.get('state') == 'held':
        result['next_action'] = 'Preserve reports, save a checkpoint and exit; host recovery must establish resource quiescence.'
    if result.get('ready'):
        result['token_file'] = str(paths.runs / item['id'] / 'reservation.token')
        if result['kind'] == 'server_slot':
            from .server_slots import read_resource
            reservation = ledger.reservation(result['reservation_id'])
            result['services'] = read_resource(ledger.slot(reservation['resource']), reservation, paths.server_slots)
        else:
            from .integration import companion
            reservation = companion(ledger, ledger.reservation(result['reservation_id']))
            if reservation:
                from .server_slots import read_resource
                trees = Worktrees(paths.repos, paths.worktrees, config.repos)
                source = trees.read_candidate('farm-hive', item['id'], reservation['commit_sha'])
                result['integration'] = {**read_resource(ledger.slot(reservation['resource']), reservation, paths.server_slots),
                                         'plan':reservation['integration']['plan'], 'source':str(source)}
    return result


def unity(args, ledger, config, token, *, client_factory=None):
    from .unity_mcp import UnityMcp
    result = status(args, ledger, config, token)
    if result.get('kind') != 'unity_slot' or not result.get('ready'):
        raise LedgerError('Unity is not ready for this claim')
    reservation = ledger.reservation(result['reservation_id'])
    slot = ledger.slot(reservation['resource'])
    if slot['state'] != 'interactive_busy':
        raise LedgerError('Unity is preparing or held for recovery')
    client = (client_factory or UnityMcp)(slot['mcp_address'], timeout=60)
    client.select_instance(slot['instance'])
    # Recheck after connecting: no call may inherit a stopped or superseded claim.
    ledger.renew(args.item, token)
    ledger._refuse_withdrawn(ledger._row(args.item))
    if ledger.reservation(reservation['reservation_id'])['state'] != 'active':
        raise LedgerError('Unity reservation was released during connection')
    if args.command == 'unity-tools':
        return client._rpc('tools/list', {})
    if args.command == 'unity-read':
        return client.read_resource(args.uri)
    if args.command == 'unity-test-result':
        from .unity_test_result import retained_result
        report = retained_result(client, args.job_id, reservation['acquired_at'])
        # The read must not turn a withdrawn/released reservation into evidence
        # certified as belonging to a still-owned candidate.
        current = status(args, ledger, config, token)
        if (not current.get('ready') or current.get('reservation_id') != reservation['reservation_id']
                or current.get('commit_sha') != result['commit_sha']):
            raise LedgerError('Unity reservation changed during report collection')
        return {'reservation_id': reservation['reservation_id'], 'commit_sha': result['commit_sha'],
                'instance': slot['instance'], 'report': report}
    if args.name == 'set_active_instance':
        raise LedgerError('instance selection is bound to the owned reservation')
    arguments = json.loads(Path(args.input).read_text(encoding='utf-8'))
    if not isinstance(arguments, dict):
        raise LedgerError('Unity tool input must be an object')
    # Raw MCP results preserve tools returning images, text or structured data.
    # Never retry a mutation when its response is lost.
    return client._rpc('tools/call', {'name': args.name, 'arguments': arguments})
