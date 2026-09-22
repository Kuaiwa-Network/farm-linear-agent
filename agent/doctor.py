"""Read-only host diagnostics. Never open Ledger: its constructor migrates the DB."""
from contextlib import closing
import os
import sqlite3
import stat
import subprocess
import time
from uuid import UUID

from .config import Paths, load_config


class _SchemaMismatch(ValueError):
    def __init__(self, missing):
        self.missing = missing
        super().__init__("ledger predates lifecycle diagnostics")


def probe_process(pid, item_id):
    """Check existence and the job marker without exposing process arguments.

    A matching command is evidence of ownership, not proof of worker progress.
    Unknown includes PID reuse and races between kill(0) and ps. Never kill here.
    """
    if type(pid) is not int or pid <= 0:
        return {"state": "unknown", "reason": "invalid_pid"}
    # Windows kill(pid, 0) does not have POSIX probe semantics.
    if os.name == "nt":
        return {"state": "unknown", "reason": "platform_not_supported"}
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return {"state": "dead", "reason": "not_found"}
    except (OSError, OverflowError):
        return {"state": "unknown", "reason": "permission_or_probe_error"}
    try:
        result = subprocess.run(["ps", "-o", "stat=", "-o", "command=", "-p", str(pid)],
                                capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return {"state": "unknown", "reason": "inspection_failed"}
    parts = result.stdout.strip().split(None, 1)
    if result.returncode or len(parts) != 2:
        return {"state": "unknown", "reason": "inspection_failed"}
    if "Z" in parts[0]:
        return {"state": "dead", "reason": "zombie"}
    if item_id not in parts[1]:
        return {"state": "unknown", "reason": "ownership_unverified"}
    return {"state": "alive", "reason": "job_marker_matches"}


def _report(now):
    return {"schema_version": 1, "checked_at": now, "status": "ok", "host": None,
            "scope": "local ledger snapshot and recorded worker PIDs; no service, tunnel or Linear health probe",
            "counts": {}, "jobs": [], "slots": [], "reservations": [],
            "cleanup_pending": [], "issue_status_errors": [], "resource_recoveries": [], "findings": []}


def _finding(report, code, hint, *, incomplete=False, **evidence):
    report["findings"].append({"code": code, "severity": "unknown" if incomplete else "warning",
                               "hint": hint, **evidence})
    if incomplete:
        report["status"] = "incomplete"
    elif report["status"] == "ok":
        report["status"] = "attention"


def _snapshot(path):
    # mode=ro prevents creation; a transaction keeps all tables on the same snapshot.
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        tables = {row["name"] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = [name for name in ("issue_checks", "job_cleanup") if name not in tables]
        columns = {row["name"] for row in db.execute("PRAGMA table_info(work_items)")}
        if "predecessor_id" not in columns:
            missing.append("work_items.predecessor_id")
        if missing:
            raise _SchemaMismatch(missing)
        def rows(query):
            return [dict(row) for row in db.execute(query)]
        return {
            "counts": {row["state"]: row["count"] for row in rows(
                "SELECT state,COUNT(*) AS count FROM work_items GROUP BY state")},
            "jobs": rows("""SELECT w.id AS item_id,w.issue_id,json_extract(i.metadata,'$.identifier') AS identifier,
                w.skill,w.state,w.stage,w.host,w.worker_pid,w.lease_expires_at,w.updated_at
                FROM work_items w JOIN issues i ON i.id=w.issue_id
                WHERE w.state IN ('queued','running','awaiting_input','awaiting_resource')
                OR (w.state IN ('blocked','failed') AND NOT EXISTS (
                    SELECT 1 FROM work_items successor WHERE successor.predecessor_id=w.id))
                OR EXISTS (SELECT 1 FROM job_cleanup c WHERE c.item_id=w.id AND c.done=0)
                ORDER BY w.created_at,w.id"""),
            "slots": rows("SELECT slot_id,kind,host,folder,state,updated_at FROM slots ORDER BY slot_id"),
            "resource_recoveries": rows("""SELECT id,slot_id,item_id,state,attempts,detached,due_at,lease_until,
                error IS NOT NULL AS has_error,evidence,updated_at FROM resource_recoveries
                WHERE state != 'recovered' ORDER BY created_at,id""") if 'resource_recoveries' in tables else [],
            "reservations": rows("""SELECT reservation_id,item_id,resource,host,state,created_at,acquired_at
                FROM reservations WHERE state IN ('queued','active','cancel_requested') ORDER BY sequence"""),
            "cleanup_pending": rows("""SELECT item_id,worker_pid,error IS NOT NULL AS has_error,updated_at
                FROM job_cleanup WHERE done=0 ORDER BY updated_at,item_id"""),
            "issue_status_errors": rows("""SELECT c.issue_id,json_extract(i.metadata,'$.identifier') AS identifier,
                c.failures,c.checked_at,c.due_at FROM issue_checks c JOIN issues i ON i.id=c.issue_id
                WHERE c.error IS NOT NULL ORDER BY c.checked_at,c.issue_id"""),
        }


def _logs(paths, item_id):
    # Ledger item IDs are UUIDs. Do not traverse arbitrary paths in a damaged ledger.
    UUID(item_id)
    directory = paths.runs / item_id
    files = []
    try:
        mode = directory.lstat().st_mode
    except FileNotFoundError:
        return {"directory": str(directory), "files": []}
    if not stat.S_ISDIR(mode):
        raise OSError("run directory is not a regular directory")
    # glob() suppresses scanning errors on Python 3.13; iterdir() must expose them.
    for attempt in sorted(directory.iterdir()):
        try:
            mode = attempt.lstat().st_mode
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(mode):
            continue
        for name in ("stdout.log", "stderr.log", "last_message.txt", "process.json", "killed.json"):
            path = attempt / name
            try:
                mode = path.lstat().st_mode
            except FileNotFoundError:
                continue
            if stat.S_ISREG(mode):
                files.append(str(path))
    return {"directory": str(directory), "files": files}


def diagnose(config, *, now=None):
    report = _report(time.time() if now is None else now)
    paths = Paths(config)
    report.update(host=config.host, ledger=str(paths.ledger),
                  service_logs=str(paths.config_dir / "logs"))
    try:
        report.update(_snapshot(paths.ledger))
    except (OSError, sqlite3.Error, ValueError) as exc:
        _finding(report, "ledger_unreadable", "Check the ledger path, permissions and schema with this service version; no migration was attempted.",
                 incomplete=True, error_type=type(exc).__name__,
                 **({"missing_schema": exc.missing} if isinstance(exc, _SchemaMismatch) else {}))
        return report
    report["counts"]["total"] = sum(report["counts"].values())
    jobs = {job["item_id"]: job for job in report["jobs"]}
    for job in report["jobs"]:
        try:
            job["logs"] = _logs(paths, job["item_id"])
        except (OSError, ValueError) as exc:
            job["logs"] = {"directory": None, "files": []}
            _finding(report, "logs_unreadable", "Inspect the run directory and item ID.", incomplete=True,
                     item_id=job["item_id"], error_type=type(exc).__name__)
        evidence = {key: job[key] for key in ("item_id", "issue_id", "identifier", "logs")}
        if job["state"] == "running":
            lease = job["lease_expires_at"]
            if lease is None or lease <= report["checked_at"]:
                _finding(report, "lease_expired", "Inspect worker logs and scheduler recovery; the running claim has no valid lease.", **evidence)
            if job["worker_pid"] is None:
                _finding(report, "worker_pid_missing", "A running claim has no recorded PID; inspect scheduler and run logs.", **evidence)
        if job["worker_pid"] is not None:
            job["process"] = (probe_process(job["worker_pid"], job["item_id"]) if job["host"] == config.host
                              else {"state": "unknown", "reason": "host_mismatch"})
            state = job["process"]["state"]
            if state != "alive":
                _finding(report, "worker_dead" if state == "dead" else "worker_unverified",
                         "Inspect this job on its owning host; check run logs before taking recovery action.",
                         incomplete=state == "unknown", process=job["process"], **evidence)
        if job["state"] in ("failed", "blocked"):
            _finding(report, "job_" + job["state"], "Inspect the job's retained run logs and Linear context before retrying.", **evidence)
    for cleanup in report["cleanup_pending"]:
        job = jobs.get(cleanup["item_id"], {})
        if cleanup["worker_pid"] is not None:
            cleanup["process"] = (probe_process(cleanup["worker_pid"], cleanup["item_id"])
                                  if job.get("host") == config.host
                                  else {"state": "unknown", "reason": "host_mismatch"})
            if cleanup["process"]["state"] == "unknown":
                _finding(report, "cleanup_worker_unverified", "Inspect the retained cleanup PID on its owning host before releasing resources.",
                         incomplete=True, item_id=cleanup["item_id"], process=cleanup["process"], logs=job.get("logs"))
        _finding(report, "cleanup_pending", "Inspect job_cleanup in the ledger and run logs; cleanup may still be in progress or preserving uncertain process/source evidence.",
                 **cleanup, identifier=job.get("identifier"), logs=job.get("logs"))
    for error in report["issue_status_errors"]:
        _finding(report, "issue_status_error", "Inspect issue_checks.error and service logs for the stored Linear status failure; connectivity was not probed.", **error)
    owned_slots = {r["resource"] for r in report["reservations"] if r["state"] in ("active", "cancel_requested")}
    for slot in report["slots"]:
        if slot["state"] == "held":
            _finding(report, "slot_held", "Controller recovery owns this slot. Inspect resource_recoveries and retained diagnostics; do not release it manually.", slot_id=slot["slot_id"], host=slot["host"])
        # release() leaves a slot switching while park_idle() returns it to the pool.
        elif slot["state"] in ("interactive_busy", "batch_busy") and slot["slot_id"] not in owned_slots:
            _finding(report, "slot_without_reservation", "Inspect the slot and reservation history; a busy slot has no active reservation.", slot_id=slot["slot_id"], host=slot["host"])
    slots = {s["slot_id"]: s for s in report["slots"]}
    for reservation in report["reservations"]:
        if reservation["state"] == "cancel_requested":
            _finding(report, "reservation_cancel_pending", "Inspect the resource owner and service logs; cancellation has not settled yet.", **reservation)
        if reservation["state"] in ("active", "cancel_requested") and reservation["resource"] not in slots:
            _finding(report, "reservation_slot_missing", "Inspect reservation history; its assigned slot is absent from this ledger.", **reservation)
    return report


def run(config_path=None):
    try:
        config = load_config(config_path, secure_permissions=False)
        # Validate paths before reporting so malformed configuration remains machine-readable.
        Paths(config)
    except (OSError, ValueError, TypeError) as exc:
        report = _report(time.time())
        _finding(report, "config_unreadable", "Check the config path, permissions and JSON fields; configuration was not modified.",
                 incomplete=True, error_type=type(exc).__name__)
        return report
    return diagnose(config)
