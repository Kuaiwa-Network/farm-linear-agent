"""Portable, redacted diagnostic evidence. Never construct Ledger or replay host state."""
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import sqlite3
import stat
import subprocess
import time
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import UUID
import zipfile

from .config import Paths, ROOT, load_config
from .doctor import diagnose
from .heartbeat import MAX_BYTES as HEARTBEAT_BYTES, STALE_AFTER, validate as validate_heartbeat
from .readonly_db import snapshot_connection
from .worktrees import HOOKS_OFF

MAX_JOBS = 10
MAX_EVENTS = 200
MAX_ATTEMPTS = 5
MAX_DIRECTORY_ENTRIES = 2000
MAX_FILE_BYTES = 256 * 1024
MAX_BUNDLE_BYTES = 8 * 1024 * 1024
LOG_NAMES = ("stdout.log", "stderr.log", "last_message.txt")
ATTEMPT = re.compile(r"[0-9]{8}T[0-9]{6}Z-[a-zA-Z0-9_-]{1,64}")
JOB_FIELDS = ("id", "issue_id", "skill", "state", "stage", "priority", "host", "generation",
              "worker_pid", "created_at", "updated_at", "execution_contract", "needs_resource",
              "root_repo", "lease_expires_at", "predecessor_id")
REDACTED = "[REDACTED]"
_SENSITIVE = re.compile(r"secret|token|password|passwd|authorization|cookie|api[_-]?key|private[_-]?key", re.I)
_URL = re.compile(r"(?:https?|wss?)://[^\s<>\"'\\]+", re.I)
_CREDENTIAL = re.compile(
    r"(?i)(?:claim_|res_|lin_api_|gh[pousr]_|github_pat_|sk-)[A-Za-z0-9_-]+"
    r"|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_KEY = r"[\w.-]{0,64}(?:secret|token|password|passwd|api[_-]?key|private[_-]?key)[\w.-]{0,64}"
_ASSIGNMENT = re.compile(
    r"(?i)(?<![\w.-])((?:" + _KEY + r"[\"']?[ \t]*[:=]\s*|--" + _KEY + r"[ \t]+))"
    r"(?:\"(?:\\.|[^\"\\])*\"|'[^']*'|[^\s,;\}\]]+)")
_AUTH = re.compile(r"(?im)(?:authorization|proxy-authorization|(?:set-)?cookie)[\"']?\s*[:=][^\r\n]*"
                   r"|\b(?:Bearer|Basic)\s+[A-Za-z0-9+/=_-]+")
_PEM = re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|\Z)", re.S)


class ExportError(ValueError):
    """Messages contain no source text, credentials or underlying exception text."""


class Redactor:
    def __init__(self, config):
        secrets = {config.client_secret, config.webhook_secret}
        for block, key in ((config.kw_ops, "token_env"), (config.lark_cli, "secret_env")):
            value = os.environ.get(block.get(key, ""), "")
            if value:
                secrets.add(value)
        # Recognized credential environment variables are replacement inputs only;
        # neither their names nor values are exported or used to contact a service.
        secrets.update(value for key, value in os.environ.items() if _SENSITIVE.search(key) and value)
        self.secrets = sorted({variant for value in secrets if value for variant in
                               (value, quote(value, safe=""), json.dumps(value)[1:-1])}, key=len, reverse=True)
        roots = [(config.local_root, "<STATE_ROOT>"), (ROOT, "<SOURCE_ROOT>"), (Path.home(), "<HOME>")]
        if config.source_path:
            roots.append((config.source_path, "<CONFIG>"))
        self.roots = sorted({(variant, label) for path, label in roots
                             for variant in (str(path), str(path).replace("\\", "/"),
                                             str(path).replace("\\", "\\\\"))}, key=lambda pair: len(pair[0]), reverse=True)

    def text(self, value):
        for secret in self.secrets:
            value = value.replace(secret, REDACTED)
        value = _PEM.sub(REDACTED, value)
        value = _AUTH.sub(REDACTED, value)
        value = _ASSIGNMENT.sub(lambda match: match[1] + REDACTED, value)
        value = _CREDENTIAL.sub(REDACTED, value)

        def url(match):
            try:
                parsed = urlsplit(match[0])
                # Remove userinfo, every query parameter and every fragment.
                return urlunsplit((parsed.scheme, parsed.netloc.rsplit("@", 1)[-1], parsed.path, "", ""))
            except ValueError:
                return "[REDACTED_URL]"

        value = _URL.sub(url, value)
        for root, label in self.roots:
            value = re.sub(re.escape(root), lambda match: label, value, flags=re.I if os.name == "nt" else 0)
        return value

    def data(self, value, depth=0):
        if depth > 30:
            return "[OMITTED: nesting limit]"
        if isinstance(value, dict):
            return {self.text(str(key)): REDACTED if _SENSITIVE.search(str(key)) else self.data(item, depth + 1)
                    for key, item in value.items()}
        if isinstance(value, list):
            return [self.data(item, depth + 1) for item in value]
        if isinstance(value, str):
            # Tool arguments can themselves be serialized JSON inside JSONL.
            if value.lstrip().startswith(("{", "[")):
                try:
                    nested = json.loads(value)
                except (ValueError, RecursionError):
                    pass
                else:
                    return json.dumps(self.data(nested, depth + 1), ensure_ascii=False)
            return self.text(value)
        if isinstance(value, float) and not math.isfinite(value):
            return "[OMITTED: nonfinite number]"
        return value

    def log(self, value):
        # Codex output is usually JSONL. Redact nested keys before serializing, so
        # escaped strings and credentials split across JSON formatting are covered.
        if re.search(r"-----(?:BEGIN|END) [^-]*PRIVATE KEY-----", value):
            # A tail can start inside a multiline key, without the BEGIN marker.
            return "[OMITTED: private key material]\n"
        try:
            parsed = json.loads(value)
        except (ValueError, RecursionError):
            pass
        else:
            return json.dumps(self.data(parsed), ensure_ascii=False, indent=2) + "\n"
        lines = []
        for line in value.splitlines():
            try:
                parsed = json.loads(line)
            except (ValueError, RecursionError):
                lines.append(line)
            else:
                lines.append(json.dumps(self.data(parsed), ensure_ascii=False))
        return self.text("\n".join(lines)) + "\n"


def _identity(info):
    return info.st_dev, info.st_ino


def _regular(path, *, directory=False):
    info = path.lstat()
    if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or (not directory and info.st_nlink != 1)):
        raise OSError("unsafe diagnostic source")
    return info


def _parents(path):
    return [(parent, _identity(_regular(parent, directory=True))) for parent in reversed(path.parents)]


def _read(path, *, limit=MAX_FILE_BYTES, tail=False):
    """Bounded reads; refuse links, junctions, hard links, devices and changed parents."""
    path = Path(path).absolute()
    parents = _parents(path)
    before = _regular(path)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    try:
        current = os.fstat(fd)
        if _identity(current) != _identity(before) or not stat.S_ISREG(current.st_mode) or current.st_nlink != 1:
            raise OSError("diagnostic source changed")
        if current.st_size > limit and not tail:
            raise OSError("diagnostic source too large")
        offset = max(0, current.st_size - limit) if tail else 0
        with os.fdopen(fd, "rb", closefd=False) as stream:
            stream.seek(offset)
            data = stream.read(min(limit, current.st_size))
        if (_identity(_regular(path)) != _identity(current)
                or any(_identity(_regular(parent, directory=True)) != identity for parent, identity in parents)):
            raise OSError("diagnostic source changed")
        # Never export the suffix of a cut line, which may have lost its credential key.
        if offset:
            data = data.partition(b"\n")[2]
        return data, {"source_bytes": current.st_size, "read_bytes": len(data), "truncated": bool(offset)}
    finally:
        os.close(fd)


def _revision(root):
    env = {key: value for key, value in os.environ.items() if key.upper() not in
           {"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE"}}
    def git(*args):
        return subprocess.run(["git", "--no-optional-locks", *HOOKS_OFF, "-C", str(root), *args],
                              env=env, capture_output=True, encoding="utf-8", errors="replace", timeout=5, check=True).stdout.strip()
    try:
        revision = git("rev-parse", "HEAD")
        dirty = bool(git("status", "--porcelain", "--untracked-files=normal"))
        if not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", revision):
            raise ValueError()
        return {"commit": revision, "dirty": dirty}
    except (OSError, subprocess.SubprocessError, ValueError):
        return {"commit": None, "dirty": None}


def _selection(issue, item):
    if issue and item:
        raise ExportError("Select either --issue or --item, not both.")
    if item:
        try:
            return None, str(UUID(item))
        except ValueError:
            raise ExportError("--item must be a work item UUID.") from None
    if issue:
        try:
            return str(UUID(issue)), None
        except ValueError:
            if re.fullmatch(r"[A-Z][A-Z0-9]{0,19}-[1-9][0-9]*", issue.upper()):
                return issue.upper(), None
            raise ExportError("--issue must be an issue UUID or identifier such as FARM-1.") from None
    return None, None


def _job_snapshot(path, issue, item, problems):
    if not (issue or item):
        return []
    try:
        _parents(path.absolute())
        _regular(path)
        with snapshot_connection(path) as db:
            columns = {row["name"] for row in db.execute("PRAGMA table_info(work_items)")}
            fields = [f"w.{name}" for name in JOB_FIELDS if name in columns]
            # Do not read unbounded JSON blobs, even from a damaged/old ledger.
            for name in ("checkpoint", "target_json"):
                if name in columns:
                    fields.append(f"CASE WHEN length(CAST(w.{name} AS BLOB)) <= {MAX_FILE_BYTES} "
                                  f"THEN w.{name} ELSE NULL END AS {name}")
            clause = "w.id=?" if item else "(i.id=? OR json_extract(i.metadata,'$.identifier')=? COLLATE NOCASE)"
            args = (item,) if item else (issue, issue)
            rows = db.execute(f"SELECT {','.join(fields)}, json_extract(i.metadata,'$.identifier') AS identifier "
                              f"FROM work_items w JOIN issues i ON i.id=w.issue_id WHERE {clause} "
                              "ORDER BY w.updated_at DESC,w.id LIMIT ?", (*args, MAX_JOBS + 1)).fetchall()
            if len(rows) > MAX_JOBS:
                problems.append({"source": "jobs", "reason": "job_limit"})
            jobs = []
            for row in rows[:MAX_JOBS]:
                job = dict(row)
                try:
                    if str(UUID(job["id"])) != job["id"]:
                        raise ValueError()
                except (ValueError, TypeError, AttributeError):
                    problems.append({"source": "jobs", "reason": "invalid_item_id"})
                    continue
                for name, allowed in (("checkpoint", ("summary", "stage", "candidates", "handoff", "pending_reason")),
                                      ("target_json", ("repository", "requested_ref", "commit_sha", "server_environment"))):
                    if name not in job:
                        continue
                    raw = job.pop(name)
                    try:
                        value = json.loads(raw)
                        if not isinstance(value, dict):
                            raise ValueError()
                        job[name] = {key: value[key] for key in allowed if key in value}
                    except (ValueError, TypeError, RecursionError):
                        if name == "checkpoint" or raw is not None:
                            problems.append({"source": f"jobs/{job['id']}/{name}", "reason": "unavailable_or_oversized"})
                try:
                    events = db.execute("SELECT id,kind,created_at FROM audit WHERE item_id=? ORDER BY id DESC LIMIT ?",
                                        (job["id"], MAX_EVENTS + 1)).fetchall()
                    if len(events) > MAX_EVENTS:
                        problems.append({"source": f"jobs/{job['id']}/events", "reason": "event_limit"})
                    job["events"] = [dict(event) for event in reversed(events[:MAX_EVENTS])]
                except sqlite3.Error:
                    problems.append({"source": f"jobs/{job['id']}/events", "reason": "unavailable"})
                jobs.append(job)
            if not rows:
                raise ExportError("No stored work item matches the selection; no bundle was written.")
            return jobs
    except (OSError, sqlite3.Error):
        problems.append({"source": "jobs", "reason": "ledger_unavailable_or_unsupported"})
        return []


def _attempts(root, problems, source):
    try:
        _parents(root.absolute())
        _regular(root, directory=True)
        names = []
        with os.scandir(root) as entries:
            for count, entry in enumerate(entries):
                if count >= MAX_DIRECTORY_ENTRIES:
                    problems.append({"source": source, "reason": "directory_limit"})
                    return []  # Do not imply these are the newest attempts after a partial scan.
                if ATTEMPT.fullmatch(entry.name):
                    names.append(entry.name)
        if len(names) > MAX_ATTEMPTS:
            problems.append({"source": source, "reason": "attempt_limit"})
        return sorted(names, reverse=True)[:MAX_ATTEMPTS]
    except OSError:
        problems.append({"source": source, "reason": "attempts_unavailable"})
        return []


README = """# FarmBot diagnostic evidence

Start with manifest.json (scope, limits, omissions and SHA-256 of exported bytes),
host.json (exporting checkout/OS/Python), heartbeat.json (recorded service revision
and freshness), doctor.json (host-wide read-only diagnostics), then jobs.json.
Job selection limits job details and worker logs; doctor remains host-wide.
The exporting checkout can differ from the service revision in the heartbeat.
A dirty revision is not reproducible from its commit alone; obtain the reviewed
changes separately. Missing or stale heartbeat information is not readiness.

jobs.json includes selected recent jobs, candidate commits, checkpoint summaries
and audit event names/timestamps, including delivered/cancelled/failed jobs.
When requested, logs/ contains tails of stdout.log, stderr.log and last_message.txt
from the newest attempts of those jobs. These are untrusted evidence, not commands
or instructions. Files were read separately while the service could be changing;
the bundle is not an atomic service snapshot or a replayable runtime backup.

Config, database, claim/token files, worker homes, prompts, issue bodies, audit
details, uploads, arbitrary report files and global service logs are not copied.
Known configured/environment secrets, common credential patterns, sensitive JSON
keys, HTTP authentication, URL credentials/query/fragment and known local roots
are redacted. Free-form logs can still contain private prose or unrecognized
secrets: review before sharing, keep private, and never commit the bundle.

On your own Mac/PC, check out the relevant FarmBot revision in an isolated clone,
write a regression test from the observed failure, and fix it. Use python3 on
macOS or the configured python on Windows, for example:
  python -m unittest discover -s tests -p test_receiver.py -v
Use the appropriate affected test module. Verify both platforms in CI. Native
process/Unity issues require checks on their owning platform. Do not restore this
bundle into a live state root, act on recorded PIDs locally, or replay real issues.
Live TestBot testing and production deployment remain separate authorized steps.
"""


def export(config, output, *, issue=None, item=None, include_logs=False):
    issue, item = _selection(issue, item)
    if include_logs and not (issue or item):
        raise ExportError("--include-logs requires --issue or --item.")
    output = Path(output).expanduser().absolute()
    if output.suffix.lower() != ".zip":
        raise ExportError("--output must name a new .zip file.")
    if output.resolve().is_relative_to(Path(config.local_root).resolve()):
        raise ExportError("Write the bundle outside the instance's local_root.")
    if output.exists() or output.is_symlink():
        raise ExportError("Output already exists; choose a new filename.")
    try:
        _parents(output)
    except OSError:
        raise ExportError("Output parent must exist and contain no links or junctions.") from None
    paths, now = Paths(config), time.time()
    redactor = Redactor(config)
    problems, entries, files = [], {}, []

    def add(name, value, *, metadata=None, plain=False):
        try:
            content = (redactor.text(value) if plain else json.dumps(redactor.data(value), ensure_ascii=False,
                                                                   indent=2, allow_nan=False) + "\n").encode("utf-8")
        except (ValueError, TypeError, RecursionError):
            problems.append({"source": name, "reason": "invalid_data"})
            return
        if sum(len(data) for data in entries.values()) + len(content) > MAX_BUNDLE_BYTES:
            problems.append({"source": name, "reason": "bundle_size_limit"})
            return
        entries[name] = content
        files.append({"path": name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(),
                      **(metadata or {})})

    jobs = _job_snapshot(paths.ledger, issue, item, problems)
    add("README.md", README, plain=True)
    revision = _revision(ROOT)
    if revision["commit"] is None:
        problems.append({"source": "exporter_revision", "reason": "unavailable"})
    add("host.json", {"exporter": revision, "os": platform.system(), "os_release": platform.release(),
                      "machine": platform.machine(), "python": platform.python_version(),
                      "environment": config.environment, "instance_id": config.instance_id, "runtime": config.runtime})
    try:
        data, _ = _read(paths.heartbeat, limit=HEARTBEAT_BYTES)
        beat = validate_heartbeat(json.loads(data.decode("utf-8")))
        state = "fresh" if abs(now - beat["written_at"]) < STALE_AFTER else "stale"
        if state == "stale":
            problems.append({"source": "heartbeat", "reason": "stale"})
        add("heartbeat.json", {"state": state, "heartbeat": beat})
    except (OSError, ValueError, TypeError, RecursionError):
        problems.append({"source": "heartbeat", "reason": "unavailable"})
        add("heartbeat.json", {"state": "unavailable", "heartbeat": None})
    try:
        report = diagnose(config, now=now)
        add("doctor.json", report)
        if report["status"] == "incomplete":
            problems.append({"source": "doctor", "reason": "incomplete"})
    except Exception as exc:
        # A partial evidence bundle remains useful; raw exception text may hold secrets.
        problems.append({"source": "doctor", "reason": "unavailable", "error_type": type(exc).__name__})
    add("jobs.json", jobs)
    if include_logs:
        for job in jobs:
            source = f"logs/{job['id']}"
            # A remote job's recorded PID and paths never establish local ownership.
            if job.get("host") != config.host:
                problems.append({"source": source, "reason": "host_unverified"})
                continue
            root = paths.runs / job["id"]
            for attempt in _attempts(root, problems, source):
                for name in LOG_NAMES:
                    destination = f"{source}/{attempt}/{name}"
                    try:
                        data, metadata = _read(root / attempt / name, tail=True)
                        text = data.decode("utf-8")
                        if "\x00" in text:
                            raise ValueError()
                    except (OSError, ValueError):
                        problems.append({"source": destination, "reason": "missing_unsafe_or_non_utf8"})
                        continue
                    if metadata["truncated"]:
                        problems.append({"source": destination, "reason": "file_tail_only"})
                    add(destination, redactor.log(text), metadata=metadata, plain=True)

    manifest = {"schema_version": 1, "created_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                "scope": {"issue": issue, "item": item, "include_logs": include_logs, "doctor": "host-wide"},
                "status": "partial" if problems else "complete", "problems": redactor.data(problems), "files": files,
                "limits": {"jobs": MAX_JOBS, "events_per_job": MAX_EVENTS, "attempts_per_job": MAX_ATTEMPTS,
                           "bytes_per_log": MAX_FILE_BYTES, "payload_bytes": MAX_BUNDLE_BYTES}}
    entries["manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    # Only sanitized bytes ever reach disk. Exclusive creation never clobbers an
    # existing archive, config, link or other file, including in a concurrent export.
    try:
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    except OSError:
        raise ExportError("Cannot create output; check the directory and choose a new filename.") from None
    identity = _identity(os.fstat(fd))
    try:
        with os.fdopen(fd, "wb") as stream:
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, content in entries.items():
                    archive.writestr(name, content)
    except BaseException as exc:
        try:
            if _identity(_regular(output)) != identity:
                raise OSError("output was replaced")
            output.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            raise ExportError("Writing failed; output cleanup was uncertain. Inspect the selected output path.") from None
        if isinstance(exc, OSError):
            raise ExportError("Writing the bundle failed; the incomplete output was removed.") from None
        raise
    return {"output": str(output), "status": manifest["status"], "files": len(entries), "problems": len(problems)}


def run(config_path, output, **kwargs):
    try:
        config = load_config(config_path, secure_permissions=False)
    except (OSError, ValueError, TypeError):
        raise ExportError("Cannot read the selected configuration; no bundle was written.") from None
    return export(config, output, **kwargs)
