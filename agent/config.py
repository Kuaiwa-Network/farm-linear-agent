"""Private configuration under .local/agent and the Linear client factory (spec §8, §15)."""
from dataclasses import dataclass, field
import getpass
import ipaddress
import json
import math
import os
import re
from pathlib import Path

from .linear_api import LinearAPI
from .kw_ops import validate_config as validate_kw_ops_config

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / ".local" / "agent" / "config.json"
REQUIRED = ("client_id", "client_secret", "webhook_secret")
# The status monitor's listener: loopback unless a host config opts into the office LAN. Serve and workers
# neither read nor validate this block; the monitor validates it when it starts (monitor_settings), so a
# mistake in it stops only the monitor, and editing it needs a monitor restart only.
MONITOR_DEFAULTS = {"bind": "127.0.0.1", "port": 8780, "hostnames": ()}
# One DNS name: dot-separated labels of lowercase letters, digits and inner hyphens.
_HOSTNAME = re.compile(r"(?=.{1,253}\Z)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*")


@dataclass
class Config:
    client_id: str
    client_secret: str
    webhook_secret: str
    host: str = "local"
    runtime: str = "codex"
    repos: dict = field(default_factory=dict)
    max_concurrent: int = 2
    reconcile_seconds: float = 60
    port: int = 8765
    local_root: Path = ROOT / ".local"
    default_server_environment: str = "公共测试服"
    slots: list = field(default_factory=list)
    tunnel: dict = field(default_factory=dict)
    codex_workers: dict = field(default_factory=dict)
    # {"url": ..., "token_env": NAME}; the token itself lives in the controller's environment, never here.
    kw_ops: dict = field(default_factory=dict)
    monitor: dict = field(default_factory=dict)
    environment: str = "legacy"
    instance_id: str = "default"
    expected_bot_name: str = "FarmBot"
    expected_app_user_id: str = ""
    expected_organization_id: str = ""
    issue_prefix: str = "FARM"
    # Provenance set by the loader, never accepted from JSON or written as credentials.
    source_path: Path | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self):
        if self.environment not in ('legacy', 'development', 'production', 'offline'):
            raise ValueError('invalid environment')
        if not isinstance(self.instance_id, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,47}', self.instance_id):
            raise ValueError('instance_id must be a lowercase name with optional digits/hyphens')
        if not isinstance(self.issue_prefix, str) or not re.fullmatch(r'[A-Z][A-Z0-9]{0,19}', self.issue_prefix):
            raise ValueError('issue_prefix must be an uppercase Linear team key')
        if not isinstance(self.expected_bot_name, str) or not self.expected_bot_name.strip():
            raise ValueError('expected_bot_name must be nonempty')
        if self.environment != 'legacy':
            if not Path(self.local_root).is_absolute() or Path(self.local_root).resolve() == (ROOT / '.local').resolve():
                raise ValueError('explicit profiles need a dedicated absolute local_root')
            if self.instance_id == 'default':
                raise ValueError('explicit profiles need a distinct instance_id')
        if self.environment in ('development', 'production'):
            if any(not isinstance(value, str) or not value.strip() for value in
                   (self.expected_app_user_id, self.expected_organization_id)):
                raise ValueError('live profiles require expected app and organization IDs')
        if (type(self.reconcile_seconds) not in (int, float) or not math.isfinite(self.reconcile_seconds)
                or self.reconcile_seconds <= 0):
            raise ValueError("reconcile_seconds must be positive and finite")
        if not isinstance(self.codex_workers, dict):
            raise ValueError("codex_workers must map skill names to model settings")
        for skill, settings in self.codex_workers.items():
            if (not isinstance(skill, str) or not skill.strip() or not isinstance(settings, dict)
                    or set(settings) - {"model", "reasoning_effort"}):
                raise ValueError("codex_workers entries accept model and reasoning_effort only")
            if "model" in settings and (not isinstance(settings["model"], str) or not settings["model"].strip()):
                raise ValueError("codex worker model must be a nonempty string")
            if "reasoning_effort" in settings and settings["reasoning_effort"] not in (
                    "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"):
                raise ValueError("invalid codex worker reasoning_effort")
        validate_kw_ops_config(self.kw_ops)


def monitor_settings(config):
    """The monitor's listener with defaults applied, or ValueError for a block it cannot use.

    This is the block's only validator, and only the monitor and `install-launchd` call it: a mistake in the
    block must never stop serve or a worker loading its config."""
    block = config.monitor
    if not isinstance(block, dict) or set(block) - set(MONITOR_DEFAULTS):
        raise ValueError("monitor accepts bind, port and hostnames only")
    bind = block.get("bind", MONITOR_DEFAULTS["bind"])
    try:
        valid = isinstance(bind, str) and str(ipaddress.IPv4Address(bind)) == bind
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("monitor.bind must be an IPv4 address such as 127.0.0.1 or 0.0.0.0")
    port = block.get("port", MONITOR_DEFAULTS["port"])
    # The default counts too: a receiver on 8780 with no block would otherwise hand the monitor its own port.
    if type(port) is not int or not 1 <= port <= 65535 or port == config.port:
        raise ValueError("monitor.port must be a TCP port other than the receiver's")
    hostnames = block.get("hostnames", [])
    if (not isinstance(hostnames, list) or len(hostnames) > 16
            or any(not isinstance(name, str) or not _HOSTNAME.fullmatch(name) for name in hostnames)):
        raise ValueError("monitor.hostnames must list at most 16 lowercase DNS names")
    return {"bind": bind, "port": port, "hostnames": tuple(hostnames)}


class Paths:
    def __init__(self, config):
        self.config_dir = Path(config.local_root) / "agent"
        self.ledger = self.config_dir / "ledger.sqlite3"
        self.runs = Path(config.local_root) / "runs"
        self.worktrees = Path(config.local_root) / "worktrees"
        self.repos = Path(config.local_root) / "repos"
        self.editors = Path(config.local_root) / "editors"
        self.heartbeat = Path(config.local_root) / "service-heartbeat.json"


def load_config(path=None, *, secure_permissions=True):
    path = Path(path or os.environ.get("FARMBOT_CONFIG") or DEFAULT_CONFIG).expanduser().resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or any(not isinstance(data.get(k), str) or not data[k].strip() for k in REQUIRED):
        raise ValueError("configuration is incomplete")
    if secure_permissions and os.name != "nt":
        os.chmod(path, 0o600)
    known = {name for name, definition in Config.__dataclass_fields__.items() if definition.init}
    values = {k: v for k, v in data.items() if k in known}
    if "local_root" in values:
        values["local_root"] = Path(values["local_root"])
    config = Config(**values)
    config.source_path = path
    return config


def configure(path=None):
    path = Path(path or DEFAULT_CONFIG)
    if path.exists():
        raise RuntimeError("config already exists; edit it locally instead of replacing credentials")
    data = {"client_id": input("Linear FarmBot client ID: ").strip(),
            "client_secret": getpass.getpass("Linear client secret (hidden): ").strip(),
            "webhook_secret": getpass.getpass("Linear webhook signing secret (hidden): ").strip(),
            "host": input("Host name for this machine: ").strip() or "local",
            "runtime": input("Worker runtime (codex/claude): ").strip() or "codex",
            "repos": {"Farm-Client": "https://github.com/Kuaiwa-Network/Farm-Client.git",
                      "farm-hive": "https://github.com/Kuaiwa-Network/farm-hive.git",
                      "farmgui": "https://github.com/Kuaiwa-Network/farmgui.git",
                      "common": "https://github.com/Kuaiwa-Network/common.git",
                      "Farm-Contract": "https://github.com/Kuaiwa-Network/Farm-Contract.git"}}
    if not all(data[k] for k in REQUIRED):
        raise ValueError("client id, client secret and webhook secret are required")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
    print(f"Saved private configuration to {path}")


class StubLinear:
    """Test double selected by FARMBOT_LINEAR_STUB_DIR; records calls, never touches the network."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.app_user_id = "e5a8c16d-9f85-4123-acf5-94e41c3304d5"

    def _record(self, method, **fields):
        with open(self.directory / "calls.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"method": method, **fields}, ensure_ascii=False) + "\n")

    def identity(self):
        return {"viewer": {"id": self.app_user_id, "name": "FarmBot"}, "organization": {"id": "org", "name": "stub"}}

    def fetch_issue(self, issue_ref):
        self._record("fetch_issue", issue=issue_ref)
        # Per issue when the fixture wrote one, `issue.json` otherwise. A stub that answered the same issue
        # for every ref could only ever stand in for a host with one issue: the second `enqueue` would
        # observe the first issue's row and be refused as a duplicate work item.
        per_issue = self.directory / f"issue-{issue_ref}.json"
        source = per_issue if per_issue.is_file() else self.directory / "issue.json"
        return json.loads(source.read_text(encoding="utf-8"))

    def issue_status(self, issue_id):
        raw = self.fetch_issue(issue_id)
        return {**raw, "updated_at": raw.get("updated_at") or "2026-09-21T00:00:00Z"}

    def create_comment(self, issue_id, body):
        number = len(list(self.directory.glob("comment-*.txt"))) + 1
        remote_id = f"stub-comment-{number}"
        (self.directory / f"comment-{number}.txt").write_text(body, encoding="utf-8")
        self._record("create_comment", issue_id=issue_id, body=body, remote_id=remote_id)
        return remote_id

    def create_activity(self, session_id, content, activity_id=None):
        self._record("create_activity", session_id=session_id, content=content, activity_id=activity_id)
        return {"success": True, "agentActivity": {"id": f"stub-activity-{activity_id or 'x'}"}}

    def needs_more_info(self, issue_id):
        self._record("needs_more_info", issue_id=issue_id)


def linear_api(config=None):
    from .environment import validate_runtime, check_ownership
    stub = os.environ.get("FARMBOT_LINEAR_STUB_DIR")
    # Existing in-memory test fixtures may deliberately omit a config file.
    # File-backed workers always load it before considering a stub selector.
    if config is not None:
        validate_runtime(config)
        check_ownership(config)
    elif not stub:
        config = load_config(secure_permissions=False)
        validate_runtime(config)
        check_ownership(config)
    elif os.environ.get('FARMBOT_CONFIG') and Path(os.environ['FARMBOT_CONFIG']).is_file():
        config = load_config(secure_permissions=False)
        validate_runtime(config)
        check_ownership(config)
    if stub:
        return StubLinear(stub)
    # The service hardens its config before launching workers. A worker consumes
    # that file read-only; it may live outside the worker's writable state roots.
    config = config or load_config(secure_permissions=False)
    api = LinearAPI(config.client_id, config.client_secret, expected_name=config.expected_bot_name,
                    expected_app_user_id=config.expected_app_user_id,
                    expected_organization_id=config.expected_organization_id)
    api.identity()
    return api
