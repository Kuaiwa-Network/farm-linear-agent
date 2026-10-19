"""Worker-only, bounded single-game-server / single-Unity-client verification."""
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
from uuid import uuid4

from .integration import validate_argv
from .launcher import Launcher, _write_worker_file


class Command:
    """A retained creation object; no recovered PID is ever a termination authority.

    Windows children use a nested non-breakaway Job. POSIX children get their own
    group inside the worker's session, so outer worker containment still covers them.
    """
    def __init__(self, argv, *, cwd, env, log):
        self.job = None
        options = {'process_group':0} if os.name!='nt' else {}
        if os.name=='nt':
            from .windows_job import WindowsJob, CREATE_SUSPENDED
            self.job = WindowsJob()
            options = {'creationflags':CREATE_SUSPENDED | subprocess.CREATE_NO_WINDOW}
        self.process = None
        try:
            with open(log, 'wb') as stream:
                self.process = subprocess.Popen(validate_argv(argv), cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                                stdout=stream, stderr=subprocess.STDOUT, **options)
            if self.job:
                self.job.assign(self.process)
                self.job.resume(self.process)
        except BaseException:
            if self.process:
                self.process.kill()
                self.process.wait()
            if self.job:
                self.job.close()
            raise

    def exited(self):
        return Launcher.exited(self.process)

    def stop(self):
        if self.job:
            try:
                self.job.terminate_and_wait()
                self.process.wait(timeout=5)
            finally:
                self.job.close()
            return
        # Keep the leader unreaped until the group is gone, preventing PID/PGID reuse.
        if self.exited() and not Launcher._pinned(self.process):
            raise RuntimeError('cannot certify POSIX command ownership without a pinned leader')
        try:
            if os.getpgid(self.process.pid)!=self.process.pid:
                raise RuntimeError('command process group ownership changed')
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 5
        while True:
            result = subprocess.run(['ps','-A','-o','pid=,pgid=,stat='], capture_output=True, text=True, timeout=5)
            rows = [line.split() for line in result.stdout.splitlines()]
            if result.returncode or not any(row and row[0]==str(os.getpid()) for row in rows):
                raise RuntimeError('command teardown process observation unavailable')
            if not any(len(row)>=3 and row[1]==str(self.process.pid) and not row[2].startswith('Z') for row in rows):
                break
            if time.monotonic()>=deadline:
                raise RuntimeError('command descendants did not exit')
            time.sleep(.02)
        self.process.wait(timeout=5)


def execute(resource, client_argv, directory, *, check=lambda:None, command=Command):
    """Run within the worker's existing sandbox; this function is never called by a pool."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    integration = resource['integration']
    plan, server = integration['plan'], None
    client = None
    port = integration['game_port']
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('invalid reserved game port')
    server_argv = [arg.replace('{host}', '127.0.0.1').replace('{port}', str(port)) for arg in plan['server_argv']]
    environment = {**os.environ, **integration['environment'], 'FARMBOT_GAME_HOST':'127.0.0.1',
                   'FARMBOT_GAME_PORT':str(port), 'FARMBOT_CLIENT_COMMIT':resource['commit'],
                   'FARMBOT_INTEGRATION_DIR':str(directory),
                   'FARMBOT_SERVER_COMMIT':integration['commit'], 'FARMBOT_UNITY_INSTANCE':resource['instance'],
                   'FARMBOT_UNITY_MCP':resource['mcp_address']}
    for key in ('GOCACHE','GOTMPDIR'):
        path = directory / key.lower()
        path.mkdir()
        environment[key] = str(path)
    environment['GOMAXPROCS'] = '2'
    report = {'client_commit':resource['commit'], 'server_commit':integration['commit'],
              'scenarios':plan['scenarios'], 'server_argv':server_argv, 'client_argv':validate_argv(client_argv),
              'endpoint':f'127.0.0.1:{port}', 'started_at':time.time(), 'ready_at':None,
              'status':'failed', 'teardown':False, 'client_exit_code':None}
    report_path = directory / 'report.json'
    def guard():
        check()
        if time.time() >= integration['expires_at']:
            raise TimeoutError('combined verification lease expired')
    try:
        guard()
        server = command(server_argv, cwd=integration['source'], env=environment, log=directory / 'server.log')
        startup_deadline = time.monotonic() + plan['startup_seconds']
        while True:
            guard()
            if server.exited():
                raise RuntimeError('game server exited before readiness')
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.1):
                    break
            except OSError:
                if time.monotonic() >= startup_deadline:
                    raise TimeoutError('game server did not open its reserved loopback port')
                time.sleep(.05)
        # TCP readiness is transport readiness only; the client script must exercise application scenarios.
        report['ready_at'] = time.time()
        client = command(client_argv, cwd=directory, env=environment, log=directory / 'client.log')
        while not client.exited():
            guard()
            if server.exited():
                raise RuntimeError('game server exited during client verification')
            time.sleep(.05)
        guard()
        if server.exited():
            raise RuntimeError('game server exited before client completion')
        # Keep POSIX creation objects pinned until descendant teardown.
        if os.name=='nt':
            report['client_exit_code'] = client.process.poll()
        else:
            status = os.waitid(os.P_PID, client.process.pid, os.WEXITED | os.WNOWAIT)
            report['client_exit_code'] = status.si_status if status.si_code==os.CLD_EXITED else -status.si_status
        if report['client_exit_code']!=0:
            raise RuntimeError('client acceptance command failed; inspect client.log')
        report['status'] = 'passed'
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'[:1000]
    finally:
        errors = []
        for child in (client, server):
            if child:
                try:
                    child.stop()
                except Exception as exc:
                    errors.append(f'{type(exc).__name__}: {exc}')
        report['teardown'] = not errors
        report['server_exit_code'] = server.process.returncode if server else None
        if errors:
            report.update(status='failed', teardown_errors=errors)
        report['finished_at'] = time.time()
        _write_worker_file(report_path, json.dumps(report, ensure_ascii=True, indent=2) + '\n', sync=True)
    return {'report':str(report_path), **report}


def run_owned(ledger, args, token):
    from .config import load_config, Paths
    from .integration import companion
    from .ledger import LedgerError
    from .server_slots import read_resource
    from .worktrees import Worktrees
    from .workflow import JobWorktrees
    if ledger.renew(args.item, token).get('maintenance_requested'):
        raise LedgerError('maintenance requested; do not start a new integration run')
    item = ledger.item(args.item)
    ledger._refuse_withdrawn(ledger._row(args.item))
    reservation = ledger.active_reservation(args.item)
    server = companion(ledger, reservation) if reservation else None
    if (not server or reservation['state']!='active' or reservation['generation']!=item['generation']
            or item['root_repo']!='Farm-Client' or ledger.slot(reservation['resource'])['state']!='interactive_busy'):
        raise LedgerError('run-integration requires this worker current combined Client lease')
    config = load_config(secure_permissions=False)
    paths = Paths(config)
    if Path(args.db).resolve()!=paths.ledger.resolve():
        raise LedgerError('integration requires the configured host ledger')
    integration = read_resource(ledger.slot(server['resource']), server, paths.server_slots)
    trees = JobWorktrees(Worktrees(paths.repos, paths.worktrees, config.repos), ledger)
    trees.verification_commit('Farm-Client', args.item, reservation['commit_sha'])
    trees.verification_commit('farm-hive', args.item, server['commit_sha'])
    source = trees.read_candidate('farm-hive', args.item, server['commit_sha'])
    integration.update(source=str(source), plan=server['integration']['plan'])
    slot = ledger.slot(reservation['resource'])
    resource = {'commit':reservation['commit_sha'], 'instance':slot['instance'],
                'mcp_address':slot['mcp_address'], 'integration':integration}
    with open(args.client_command, encoding='utf-8') as stream:
        client_argv = validate_argv(json.load(stream))
    directory = paths.runs / args.item / 'state' / 'integration' / reservation['reservation_id'] / uuid4().hex
    directory.parent.mkdir(parents=True, exist_ok=True)
    next_renew = 0
    def check():
        nonlocal next_renew
        if time.monotonic() >= next_renew:
            ledger.renew(args.item, token)
            next_renew = time.monotonic() + 10
        ledger._refuse_withdrawn(ledger._owned(args.item, token))
        current = ledger.active_reservation(args.item)
        if not current or current['reservation_id']!=reservation['reservation_id'] or current['state']!='active':
            raise LedgerError('combined lease no longer active')
        if any(ledger.slot(slot_id)['state']=='held' for slot_id in (current['resource'], server['resource'])):
            raise LedgerError('combined resource held for recovery')
    result = execute(resource, client_argv, directory, check=check)
    ledger.renew(args.item, token)
    ledger.note(args.item, 'client_server_verification', result['status'],
                {'reservation_id':reservation['reservation_id'], 'report':result['report'],
                 'sha256':hashlib.sha256(Path(result['report']).read_bytes()).hexdigest(),
                 'client_commit':resource['commit'], 'server_commit':server['commit_sha'],
                 'scenarios':integration['plan']['scenarios'], 'teardown':result['teardown']})
    return result
