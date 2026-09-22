"""Private configuration under .local/agent and the Linear client factory (spec §8, §15)."""
from dataclasses import dataclass, field
import getpass
import json
import math
import os
from pathlib import Path

from .linear_api import LinearAPI

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / ".local" / "agent" / "config.json"
REQUIRED = ("client_id", "client_secret", "webhook_secret")


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
    # Provenance set by the loader, never accepted from JSON or written as credentials.
    source_path: Path | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self):
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


class Paths:
    def __init__(self, config):
        self.config_dir = Path(config.local_root) / "agent"
        self.ledger = self.config_dir / "ledger.sqlite3"
        self.runs = Path(config.local_root) / "runs"
        self.worktrees = Path(config.local_root) / "worktrees"
        self.repos = Path(config.local_root) / "repos"
        self.editors = Path(config.local_root) / "editors"


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
    stub = os.environ.get("FARMBOT_LINEAR_STUB_DIR")
    if stub:
        return StubLinear(stub)
    # The service hardens its config before launching workers. A worker consumes
    # that file read-only; it may live outside the worker's writable state roots.
    config = config or load_config(secure_permissions=False)
    api = LinearAPI(config.client_id, config.client_secret)
    api.identity()
    return api
