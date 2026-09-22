"""Profile ownership and OS-backed controller locking; never inspect or migrate a ledger."""
import json
import os
from pathlib import Path


def validate_runtime(config, runtime_override=None):
    runtime = runtime_override or config.runtime
    stub = os.environ.get('FARMBOT_LINEAR_STUB_DIR')
    if config.environment in ('development', 'production') and (runtime == 'fake' or stub):
        raise ValueError('live profiles refuse fake workers and Linear stub selectors')
    if config.environment == 'offline' and (runtime != 'fake' or not stub):
        raise ValueError('offline profiles require fake workers and a Linear stub fixture')


def _binding(config):
    return {'version': 1, 'environment': config.environment, 'instance_id': config.instance_id,
            'client_id': config.client_id, 'app_user_id': config.expected_app_user_id,
            'organization_id': config.expected_organization_id,
            'local_root': str(Path(config.local_root).resolve())}


def validate_paths(config):
    from .config import Paths
    if config.environment == 'legacy':
        return
    root = Path(config.local_root).resolve()
    paths = Paths(config)
    candidates = [paths.config_dir, paths.ledger, paths.runs, paths.worktrees, paths.repos, paths.editors]
    for slot in config.slots:
        if slot.get('folder'):
            folder = Path(slot['folder'])
            if not folder.is_absolute():
                raise ValueError('Unity folder must be absolute and inside local_root')
        else:
            folder = paths.editors / f"slot-{slot['id'].split(':', 1)[-1]}"
        candidates.append(folder)
    for path in candidates:
        resolved = path.resolve()
        if resolved == root or not resolved.is_relative_to(root):
            raise ValueError('state and Unity folders must remain inside local_root')


def _runtime_paths(config):
    from .config import Paths
    paths = Paths(config)
    return [paths.ledger, paths.runs, paths.repos, paths.worktrees, paths.editors]


def check_ownership(config, *, require_initialized=False):
    """Read-only check also used by worker API clients and maintenance commands."""
    validate_paths(config)
    marker = Path(config.local_root) / 'environment.json'
    if marker.is_symlink():
        raise RuntimeError('environment marker must not be a symlink')
    if marker.exists():
        try:
            stored = json.loads(marker.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise RuntimeError('invalid environment marker; preserve it for inspection') from exc
        if stored != _binding(config):
            raise RuntimeError('state ownership differs from the selected profile')
    elif config.environment != 'legacy':
        if any(path.exists() for path in _runtime_paths(config)):
            raise RuntimeError('unmarked runtime state requires explicit migration; use a fresh root')
        if require_initialized:
            raise RuntimeError('initialize the profile with serve or seed-clones before using its state')


def check_worker_state(db_path):
    """Validate profile/ledger agreement before CLI commands can create or migrate it."""
    from .config import DEFAULT_CONFIG, Paths, load_config
    database = Path(db_path).resolve()
    selected = Path(os.environ.get('FARMBOT_CONFIG') or DEFAULT_CONFIG).expanduser()
    marker = database.parent.parent / 'environment.json'
    if not selected.is_file():
        if marker.exists() or marker.is_symlink():
            raise RuntimeError('marked state requires its selected FARMBOT_CONFIG')
        return  # Compatibility for config-free offline/legacy CLI fixtures.
    config = load_config(selected, secure_permissions=False)
    validate_runtime(config)
    check_ownership(config, require_initialized=True)
    if (config.environment != 'legacy' or marker.exists() or marker.is_symlink()) and database != Paths(config).ledger.resolve():
        raise RuntimeError('worker ledger differs from selected profile')
    if marker.exists() or marker.is_symlink():
        # A legacy config cannot address a different profile's marked DB.
        if database != Paths(config).ledger.resolve():
            raise RuntimeError('worker ledger ownership differs from selected profile')


class ControllerGuard:
    """Own a file lock until shutdown. The lock file is never unlinked (no inode race)."""
    def __init__(self, config):
        self.config = config
        self.handle = None

    def __enter__(self):
        validate_runtime(self.config)
        check_ownership(self.config)
        root = Path(self.config.local_root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        lock_path = root / '.controller.lock'
        if lock_path.is_symlink():
            raise RuntimeError('controller lock must not be a symlink')
        self.handle = lock_path.open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                if lock_path.stat().st_size == 0:
                    self.handle.write(b'0')
                    self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise RuntimeError('another controller owns this state root') from exc
        try:
            check_ownership(self.config)
            marker = root / 'environment.json'
            if self.config.environment != 'legacy' and not marker.exists():
                # Configuration/log directories are harmless, but never automatically
                # re-label a previous installation's durable runtime state.
                temporary = root / '.environment.json.tmp'
                with temporary.open('x', encoding='utf-8') as handle:
                    json.dump(_binding(self.config), handle, indent=2)
                    handle.write('\n')
                temporary.replace(marker)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        if self.handle is not None:
            # Closing releases both flock and Windows byte-range locks.
            self.handle.close()
            self.handle = None
