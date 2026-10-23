"""Windows Task Scheduler watchdog for an explicitly pinned local Docker host.

Run once per minute and at user logon, outside packaged-app execution jobs.
Never opens FarmBot's ledger, stops processes, cleans resources or adopts a daemon.
The existing host task owns Docker and records its exit status independently.
"""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time


def digest(path):
    path = Path(path)
    if not path.is_file() or path.is_symlink() or path.is_junction():
        raise ValueError('pinned input must be a regular file')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


class Watchdog:
    def __init__(self, config, *, run=subprocess.run, clock=time.time):
        self.config, self.run, self.clock = config, run, clock
        self.root = Path(config['state_root'])
        if not self.root.is_absolute() or self.root.is_symlink() or self.root.is_junction():
            raise ValueError('watchdog needs a regular absolute state directory')
        if not re.fullmatch(r'FarmBot-Docker-[A-Za-z0-9_-]+', config['host_task']):
            raise ValueError('unexpected Docker host task name')
        if not config['endpoint'].startswith('npipe:////./pipe/') or not config['daemon_id']:
            raise ValueError('an existing local daemon identity is required')
        self.root.mkdir(parents=True, exist_ok=True)

    def command(self, argv, timeout=15):
        env = {k: v for k, v in os.environ.items()
               if not k.upper().startswith(('DOCKER_', 'COMPOSE_', 'FARMBOT_', 'CODEX_', 'GIT_'))
               and k.upper() not in ('GH_TOKEN', 'GITHUB_TOKEN', 'PYTHONPATH', 'PYTHONHOME')}
        return self.run(argv, capture_output=True, text=True, encoding='utf-8', errors='replace',
                        timeout=timeout, env=env,
                        **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))

    def powershell(self, script):
        shell = str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
        result = self.command([shell, '-NoProfile', '-NonInteractive', '-Command', script])
        if result.returncode:
            raise RuntimeError('Windows host inspection failed')
        return json.loads(result.stdout or 'null')

    def validate(self):
        for path, expected in self.config['pins'].items():
            if digest(path) != expected:
                raise ValueError('pinned Docker host input changed')
        settings = json.loads(Path(self.config['settings']).read_text(encoding='utf-8-sig'))
        if Path(settings.get('wslDefaultDataRoot', '')).resolve() != Path(self.config['data_root']).resolve():
            raise ValueError('Docker data root changed; automatic launch refused')
        if not (Path(self.config['data_root']) / 'disk/docker_data.vhdx').is_file():
            raise ValueError('original Docker data disk is missing')

    def health(self):
        try:
            result = self.command([self.config['docker'], '--host', self.config['endpoint'],
                                   'info', '--format', '{{json .}}'], timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode:
            return None
        info = json.loads(result.stdout)
        if info.get('ID') != self.config['daemon_id'] or info.get('OSType') != 'linux':
            raise ValueError('Docker daemon identity changed; automatic recovery refused')
        return info

    def host(self):
        name = self.config['host_task']  # validated restricted alphabet
        return self.powershell(
            "$ErrorActionPreference='Stop'; $t=Get-ScheduledTask -TaskName '" + name + "'; "
            "[pscustomobject]@{state=[string]$t.State; user=$t.Principal.UserId; "
            "actions=@($t.Actions|Select-Object Execute,Arguments,WorkingDirectory); "
            "processes=@(Get-CimInstance Win32_Process -Filter \"Name='com.docker.backend.exe'\" | "
            "Select-Object ProcessId,ParentProcessId,ExecutablePath,CreationDate)}|ConvertTo-Json -Depth 5")

    def record(self, state, **details):
        value = {'at': self.clock(), 'state': state, **details}
        path = self.root / 'status.json'
        previous = json.loads(path.read_text()) if path.exists() else {}
        if state != previous.get('state') or state == 'restart_requested':
            with (self.root / 'events.jsonl').open('a', encoding='utf-8') as log:
                log.write(json.dumps(value) + '\n')
        write_json(path, value)
        return value

    def tick(self):
        if (self.root / 'pause').exists():
            return self.record('paused')
        self.validate()
        info = self.health()
        if info is not None:
            write_json(self.root / 'retry.json', {'failures': 0, 'retry_at': 0})
            return self.record('healthy', daemon_id=info['ID'], containers_running=info.get('ContainersRunning'))
        host = self.host()
        if host['actions'] != [self.config['action']] or host['user'] != self.config['user']:
            raise ValueError('Docker host task ownership or action changed')
        if host['processes']:
            # A slow or hung backend may still own live workloads. Never kill it.
            return self.record('unhealthy_backend_alive', processes=host['processes'])
        if host['state'] == 'Running':
            return self.record('host_starting')
        if host['state'] != 'Ready':
            return self.record('host_task_unavailable', task_state=host['state'])
        retry_path = self.root / 'retry.json'
        retry = json.loads(retry_path.read_text()) if retry_path.exists() else {'failures': 0, 'retry_at': 0}
        if self.clock() < retry['retry_at']:
            return self.record('backoff', retry_at=retry['retry_at'])
        failures = retry['failures'] + 1
        write_json(retry_path, {'failures': failures, 'retry_at': self.clock() + min(300, 60 * 2 ** min(failures - 1, 3))})
        result = self.command(['schtasks.exe', '/Run', '/TN', self.config['host_task']])
        if result.returncode:
            raise RuntimeError('Windows refused Docker host task start')
        return self.record('restart_requested', attempt=failures)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--config-sha256', required=True)
    args = parser.parse_args()
    if os.name != 'nt':
        raise SystemExit('This watchdog requires Windows Task Scheduler')
    if digest(args.config) != args.config_sha256:
        raise SystemExit('Watchdog config changed')
    config = json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
    watchdog = Watchdog(config)
    import msvcrt
    with (watchdog.root / 'lock').open('a+b') as lock:
        if lock.tell() == 0:
            lock.write(b'0'); lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return
        try:
            size = ctypes.c_uint32()
            if ctypes.windll.kernel32.GetCurrentPackageFullName(ctypes.byref(size), None) != 15700:
                raise RuntimeError('Watchdog must run outside a packaged app; use its Windows task')
            watchdog.tick()
        except Exception as exc:
            watchdog.record('refused', error_type=type(exc).__name__, reason=str(exc)[:200])
            raise


if __name__ == '__main__':
    main()
