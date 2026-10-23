"""Scoped Go tests inside the worker sandbox and a reservation-owned process group."""
import json
import os
from pathlib import Path
import time
from uuid import uuid4

from .launcher import _write_worker_file
from .ledger import LedgerError
from .resource_processes import Commands, scope


def run_owned(ledger, args, token):
    from .config import load_config, Paths
    from .server_slots import read_resource
    from .worktrees import Worktrees
    item = ledger.renew(args.item, token)
    ledger._refuse_withdrawn(ledger._owned(args.item, token))
    reservation = ledger.active_reservation(args.item)
    if (not reservation or reservation['kind'] != 'server_slot' or reservation['state'] != 'active'
            or reservation['generation'] != item['generation'] or not scope(ledger, reservation['reservation_id'])):
        raise LedgerError('run-server-tests requires this claim managed server reservation')
    config = load_config(secure_permissions=False)
    paths = Paths(config)
    if Path(args.db).resolve() != paths.ledger.resolve():
        raise LedgerError('server tests require the configured host ledger')
    trees = Worktrees(paths.repos, paths.worktrees, config.repos)
    source = paths.worktrees / args.item / 'farm-hive'
    module = (source / args.module).resolve()
    if not module.is_relative_to(source.resolve()) or not (module / 'go.mod').is_file():
        raise LedgerError('module must be an owned farm-hive directory containing go.mod')
    trees.verification_commit('farm-hive', args.item, reservation['commit_sha'])
    resource = read_resource(ledger.slot(reservation['resource']), reservation, paths.server_slots)
    directory = paths.runs / args.item / 'reports' / 'server-tests' / uuid4().hex
    directory.mkdir(parents=True, exist_ok=False)
    env = {**os.environ, **resource['environment'], 'GOMAXPROCS': '2'}
    argv = resource['test_argv']
    if config.go_dependencies:
        argv[0] = config.go_dependencies['go']
        env.update(GOMODCACHE=config.go_dependencies['cache_root'], GOPROXY='off', GOSUMDB='off')
    report = {'schema': 1, 'reservation_id': reservation['reservation_id'], 'commit': reservation['commit_sha'],
              'module': args.module, 'test_plan': resource['test_plan'], 'argv': argv,
              'started_at': time.time(), 'status': 'failed', 'teardown': False, 'exit_code': None}
    child = None
    next_renew = 0
    try:
        child = Commands(ledger, args.item, token, reservation['reservation_id']).command(
            argv, cwd=module, env=env, log=directory/'tests.jsonl')
        while not child.exited():
            if time.time() >= resource['expires_at']:
                raise TimeoutError('server resource deadline reached')
            if time.monotonic() >= next_renew:
                current = ledger.renew(args.item, token)
                ledger._refuse_withdrawn(ledger._owned(args.item, token))
                active = ledger.active_reservation(args.item)
                if not active or active['reservation_id'] != reservation['reservation_id'] or active['state'] != 'active':
                    raise LedgerError('server reservation no longer active')
                if ledger.slot(active['resource'])['state'] != 'batch_busy':
                    raise LedgerError('server services are no longer ready; preserve recovery evidence')
                if current['maintenance_requested']:
                    raise LedgerError('maintenance requested; collecting partial results and stopping owned tests')
                next_renew = time.monotonic() + 1
            time.sleep(.05)
        if os.name == 'nt':
            report['exit_code'] = child.process.poll()
        else:
            result = os.waitid(os.P_PID, child.process.pid, os.WEXITED | os.WNOWAIT)
            report['exit_code'] = result.si_status if result.si_code == os.CLD_EXITED else -result.si_status
        report['status'] = 'completed' if report['exit_code'] == 0 else 'failed'
    except Exception as exc:
        report['error'] = type(exc).__name__ + ': ' + str(exc)[:500]
    finally:
        if child:
            try:
                child.stop()
                report['teardown'] = True
            except Exception as exc:
                report.update(status='failed', teardown_error=type(exc).__name__)
        report['finished_at'] = time.time()
        try:
            trees.verification_commit('farm-hive', args.item, reservation['commit_sha'])
        except Exception:
            report.update(status='failed', source_changed=True)
        _write_worker_file(directory/'report.json', json.dumps(report, indent=2), sync=True)
    return {**report, 'report': str(directory/'report.json'), 'output': str(directory/'tests.jsonl')}
