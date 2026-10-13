"""Host-side controller supervision; never opens a ledger or kills a process.

May be copied to stable private state and run directly, outside a release checkout.
Windows Task Scheduler owns the supervisor independently of the invoking app.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


class InstanceLock:
    def __init__(self, path):
        self.path = Path(path)
        self.handle = None

    def __enter__(self):
        if self.path.is_symlink() or self.path.is_junction():
            raise RuntimeError('supervisor lock must not be a symlink')
        self.handle = self.path.open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                if self.path.stat().st_size == 0:
                    self.handle.write(b'0'); self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close(); self.handle = None
            raise RuntimeError('another supervisor owns this instance') from exc
        return self

    def __exit__(self, *args):
        if self.handle is not None:
            self.handle.close(); self.handle = None


def digest(path):
    path = Path(path)
    if path.is_symlink() or path.is_junction() or not path.is_file():
        raise ValueError('pinned input must be a regular file')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def selected_environment(config_path, environment_file=None, environment_sha256=None):
    env = dict(os.environ)
    if environment_file is not None:
        if not environment_sha256 or digest(environment_file) != environment_sha256:
            raise ValueError('private environment digest differs from installation')
        values = json.loads(Path(environment_file).read_text(encoding='utf-8'))
        if not isinstance(values, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                                 for k, v in values.items()):
            raise ValueError('private environment must contain string pairs')
        for key in list(env):
            if key.upper().startswith(('FARMBOT_', 'FAKE_CLI_', 'CODEX_', 'OPENAI_', 'CLAUDE_',
                                       'ANTHROPIC_', 'AZURE_', 'GIT_', 'LARKSUITE_CLI_')) or key.upper() in (
                    'HOME', 'PYTHONPATH', 'PYTHONHOME', 'GH_TOKEN', 'GITHUB_TOKEN', 'GOROOT',
                    'GOTOOLCHAIN', 'DOTNET_ROOT', 'DOTNET_ROOT_X64'):
                env.pop(key)
        env.update(values)
    env['FARMBOT_CONFIG'] = str(Path(config_path).resolve())
    env['PYTHONUTF8'] = '1'
    return env


class Supervisor:
    def __init__(self, config, source_root, config_path, environment, *, config_sha256,
                 launch_manifest=None, launch_manifest_sha256=None,
                 spawn=subprocess.Popen, clock=time.monotonic):
        from agent.environment import check_ownership, validate_runtime
        validate_runtime(config)
        check_ownership(config, require_initialized=True)
        self.config = config
        self.source_root = Path(source_root).resolve()
        self.config_path = Path(config_path).resolve()
        self.config_sha256 = config_sha256
        self.environment = dict(environment, FARMBOT_CONFIG=str(self.config_path), PYTHONUTF8='1')
        if config.runtime == 'fake' or self.environment.get('FARMBOT_LINEAR_STUB_DIR'):
            raise ValueError('controller supervision requires live integrations')
        self.launch_manifest = Path(launch_manifest).resolve() if launch_manifest else None
        self.launch_manifest_sha256 = launch_manifest_sha256
        self.root = Path(config.local_root).resolve()
        self.pause_path = self.root / 'supervisor.pause'
        self.log_dir = self.root / 'agent' / 'logs'
        self.spawn, self.clock = spawn, clock
        self.child = None
        self.started_at = None
        self.retry_at = 0
        self.failures = 0
        self.last_state = None

    def validate_launch(self):
        """Preserve sealed host inputs without touching its ledger or worker processes."""
        if self.launch_manifest is None:
            return
        if digest(self.launch_manifest) != self.launch_manifest_sha256:
            raise ValueError('launch manifest changed; reinstall after settled maintenance')
        manifest = json.loads(self.launch_manifest.read_text(encoding='utf-8'))
        if (Path(manifest['source_root']).resolve() != self.source_root
                or Path(manifest['production_config']).resolve() != self.config_path
                or manifest['new_production_config_sha256'] != self.config_sha256):
            raise ValueError('launch manifest differs from selected installation')
        if digest(manifest['environment']) != manifest['environment_sha256']:
            raise ValueError('launch environment changed')
        for path, expected in manifest['pins'].items():
            if digest(path) != expected:
                raise ValueError('pinned host input changed')
        command = ['git', '--no-optional-locks', '-c', 'core.hooksPath=' + os.devnull,
                   '-c', 'core.fsmonitor=false']
        def git(*args):
            return subprocess.check_output(command + list(args), cwd=self.source_root,
                                           env=self.environment, stderr=subprocess.PIPE,
                                           timeout=30).decode('utf-8').strip()
        if (git('rev-parse', 'HEAD') != manifest['candidate']
                or git('rev-parse', 'HEAD^{tree}') != manifest['tree']
                or git('status', '--porcelain')):
            raise ValueError('controller release differs from accepted source')

    def record(self, event, **details):
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with (self.log_dir / 'supervisor.jsonl').open('a', encoding='utf-8') as log:
            log.write(json.dumps({'at': time.time(), 'event': event, **details}) + '\n')

    def state(self, value):
        if value != self.last_state:
            self.record(value)
            self.last_state = value
        return value

    def tick(self):
        from agent.environment import ControllerGuard, check_ownership
        if digest(self.config_path) != self.config_sha256:
            raise ValueError('config changed; pause and reinstall after settled maintenance')
        check_ownership(self.config, require_initialized=True)
        now = self.clock()
        if self.child is not None:
            code = self.child.poll()
            if code is None:
                return self.state('child_running')
            if now - self.started_at >= 300:
                self.failures = 0
            self.failures += 1
            delay = min(300, 10 * 2 ** min(self.failures - 1, 5))
            self.record('controller_exited', pid=self.child.pid, returncode=code, retry_seconds=delay)
            self.child = None
            self.retry_at = now + delay
        if self.pause_path.is_symlink() or self.pause_path.is_junction():
            raise ValueError('pause marker must not be a symlink')
        if self.pause_path.exists():
            if not self.pause_path.is_file():
                raise ValueError('pause marker must be a regular file')
            return self.state('paused')
        # The OS-backed controller lock is authority, not a health timeout or a PID file.
        # Child serve checks the same lock again; a controller winning the intervening race is safe.
        try:
            with ControllerGuard(self.config):
                pass
        except RuntimeError as exc:
            if str(exc) != 'another controller owns this state root':
                raise
            return self.state('existing_controller')
        if now < self.retry_at:
            return self.state('backoff')
        self.validate_launch()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with (self.log_dir / 'controller.out.log').open('ab', buffering=0) as stdout, \
                (self.log_dir / 'controller.err.log').open('ab', buffering=0) as stderr:
            self.child = self.spawn([sys.executable, '-X', 'utf8', '-u', '-B', '-m', 'agent.service',
                                     'serve', '--config', str(self.config_path)],
                                    cwd=self.source_root, env=self.environment,
                                    stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
        self.started_at = now
        from agent.config import Paths
        # A diagnostic hint only. The controller lock remains restart authority.
        (Paths(self.config).config_dir / 'receiver.pid').write_text(str(self.child.pid) + '\n', encoding='utf-8')
        self.record('controller_started', pid=self.child.pid)
        return self.state('child_running')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--config-sha256', required=True)
    parser.add_argument('--source-root', required=True)
    parser.add_argument('--environment-file')
    parser.add_argument('--environment-sha256')
    parser.add_argument('--launch-manifest')
    parser.add_argument('--launch-manifest-sha256')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    try:
        config_path, source_root = Path(args.config), Path(args.source_root)
        if not config_path.is_absolute() or not source_root.is_absolute():
            raise ValueError('config and source root must be absolute')
        if not (source_root / 'agent' / 'service.py').is_file():
            raise ValueError('source root has no controller entry')
        if digest(config_path) != args.config_sha256:
            raise ValueError('config digest differs from installation')
        if args.launch_manifest:
            if digest(args.launch_manifest) != args.launch_manifest_sha256:
                raise ValueError('launch manifest digest differs from installation')
            manifest = json.loads(Path(args.launch_manifest).read_text(encoding='utf-8'))
            if (not args.environment_file
                    or Path(args.environment_file).resolve() != Path(manifest['environment']).resolve()
                    or args.environment_sha256 != manifest['environment_sha256']):
                raise ValueError('selected environment differs from sealed launch manifest')
        env = selected_environment(config_path, args.environment_file, args.environment_sha256)
        os.environ.clear(); os.environ.update(env)
        sys.path.insert(0, str(source_root.resolve()))
        from agent.config import load_config
        config = load_config(config_path)
        if config.environment not in ('development', 'production', 'legacy'):
            raise ValueError('supervisor installation requires a live profile')
        if config.environment == 'legacy' and (not args.launch_manifest
                or not Path(config.local_root).is_absolute()):
            raise ValueError('legacy supervision requires a sealed launch manifest and absolute state root')
        supervisor = Supervisor(config, source_root, config_path, env, config_sha256=args.config_sha256,
                                launch_manifest=args.launch_manifest,
                                launch_manifest_sha256=args.launch_manifest_sha256)
        supervisor.validate_launch()
        if args.check:
            print(json.dumps({'environment': config.environment, 'instance_id': config.instance_id,
                              'local_root': str(supervisor.root)}))
            return 0
        with InstanceLock(supervisor.root / '.supervisor.lock'):
            supervisor.record('supervisor_started')
            while True:
                supervisor.tick()
                time.sleep(5)
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        # Private config/environment validation errors can contain values; never print their messages.
        if 'supervisor' in locals():
            try:
                supervisor.record('supervisor_failed', error_type=type(exc).__name__)
            except OSError:
                pass
        print(json.dumps({'event': 'supervisor_failed', 'error_type': type(exc).__name__}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
