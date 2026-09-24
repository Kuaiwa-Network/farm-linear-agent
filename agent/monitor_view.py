"""The status monitor's /api/status document, read from one ledger without changing it.

Every emitted field is chosen here, from an allowlist: rows are never serialized whole, and issues.metadata,
which holds full descriptions and comments, is only asked for its identifier, title and URL. An older ledger
without optional tables or columns still produces a document; only the sections that need them are empty.
"""
import math
import re
import sqlite3

from .heartbeat import _ERROR_TYPE, LOOPS
from .readonly_db import snapshot_connection
from .resource_recovery import MAX_REPAIR_ATTEMPTS

SCHEMA_VERSION = 1
ACTIVE = ("queued", "running", "awaiting_input", "awaiting_resource")
TERMINAL = ("delivered", "blocked", "failed", "cancelled")
VERDICTS = ("ok", "attention", "starting", "unresponsive", "stopped", "unknown")
DISPLAY_STATES = ("queued", "launching", "switching_repo", "retry_wait", "running", "awaiting_input",
                  "awaiting_resource", "waiting_for_recovery")
OUTCOMES = ("delivered", "no_change", "blocked", "failed", "cancelled")
ATTENTION_CODES = ("slot_held", "slot_without_reservation", "lease_expired", "cleanup_pending",
                   "issue_status_error", "reservation_cancel_pending", "loop_erroring", "loop_stalled",
                   "webhook_rejected", "receiver_unreachable", "heartbeat_stale", "heartbeat_unreadable",
                   "renewal_overdue", "worker_untracked")
WORKER_STATES = ("alive", "renewal_overdue", "untracked", "lease_expired")
RECENT_SECONDS = 7 * 86400
RECENT_LIMIT = 30
CLEANUP_GRACE = 600
STATUS_FAILURES = 3
CANCEL_GRACE = 300
REJECT_WINDOW = 900
LOOP_ERRORS = 3
# A worker renews its lease at least every renew_minutes (skill.json); 1.5 intervals without one is overdue.
RENEWAL_GRACE = 1.5
# A worker that exits is reaped before its job leaves running, so a missing worker is only flagged after this.
UNTRACKED_GRACE = 30
LOOP_LIMITS = {"receive": 120, "lifecycle": 600, "progress": 600, "schedule": 1800,
               "resource_recovery": 1800, "pool": 5400}
KNOWN_TABLES = ("work_items", "issues", "audit", "published_prs", "slots", "reservations",
                "resource_recoveries", "job_cleanup", "issue_checks", "webhook_events")
REQUIRED = {"work_items": ("id", "issue_id", "skill", "state", "stage", "created_at", "updated_at"),
            "issues": ("id", "metadata")}
OPTIONAL_ITEM_COLUMNS = ("priority", "worker_pid", "next_root_repo", "root_repo", "retry_not_before",
                         "lease_expires_at")
SLOT_FIELDS = ("slot_id", "kind", "state", "commit", "holder", "mode", "recovery")
HEALTH_FIELDS = ("ok", "status", "latency_ms", "error_type", "checked_at")
INSTANCE_FIELDS = ("environment", "instance_id", "bot_name", "host")
_PR = re.compile(r"https://github\.com/([A-Za-z0-9_.-]{1,100})/([A-Za-z0-9_.-]{1,100})/pull/([1-9][0-9]{0,9})")
_ISSUE_FIELDS = ", ".join(f"CASE WHEN json_valid(i.metadata) THEN json_extract(i.metadata,'$.{name}') END AS {name}"
                          for name in ("identifier", "title", "url"))


class SchemaMismatch(ValueError):
    """The ledger lacks a table or column the monitor cannot do without."""


def _marks(values):
    return ",".join("?" * len(values))


def _time(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def _text(value, limit):
    return value[:limit] if isinstance(value, str) and value else None


def _linear_url(value):
    if (isinstance(value, str) and len(value) <= 500 and value.startswith("https://linear.app/")
            and not any(character.isspace() for character in value)):
        return value
    return None


def _error_type(value):
    """A class name as given, and "Error" for anything else that is not None."""
    if value is None:
        return None
    return value if isinstance(value, str) and _ERROR_TYPE.fullmatch(value) else "Error"


def _health(raw):
    health = {key: raw.get(key) for key in HEALTH_FIELDS}
    health["error_type"] = _error_type(health["error_type"])
    return health


def _instance(raw):
    return {key: _text(raw.get(key), 64) for key in INSTANCE_FIELDS}


def _pr(url):
    match = _PR.fullmatch(url) if isinstance(url, str) else None
    return {"url": url, "label": f"{match[2]}#{match[3]}"} if match else None


def _schema(db):
    tables = {row["name"] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    # Only names from KNOWN_TABLES reach PRAGMA: a table a worker created is never interpolated into SQL.
    schema = {name: {row["name"] for row in db.execute(f"PRAGMA table_info({name})")}
              for name in KNOWN_TABLES if name in tables}
    missing = sorted({table if table not in schema else f"{table}.{column}"
                      for table, columns in REQUIRED.items() for column in columns
                      if column not in schema.get(table, ())})
    if missing:
        raise SchemaMismatch(", ".join(missing))
    return schema


def _has(schema, table, *columns):
    return table in schema and set(columns) <= schema[table]


def _identifiers(db, item_ids):
    if not item_ids:
        return {}
    rows = db.execute(f"""SELECT w.id, {_ISSUE_FIELDS} FROM work_items w JOIN issues i ON i.id=w.issue_id
        WHERE w.id IN ({_marks(item_ids)})""", item_ids)
    return {row["id"]: _text(row["identifier"], 32) for row in rows}


def _active_rows(db, schema):
    extra = [name for name in OPTIONAL_ITEM_COLUMNS if name in schema["work_items"]]
    columns = ", ".join(f"w.{name}" for name in ("id", "issue_id", "skill", "state", "stage", "created_at", *extra))
    rows = [dict(row) for row in db.execute(
        f"""SELECT {columns}, {_ISSUE_FIELDS} FROM work_items w JOIN issues i ON i.id=w.issue_id
            WHERE w.state IN ({_marks(ACTIVE)}) ORDER BY w.created_at, w.id""", ACTIVE)]
    for row in rows:
        for name in OPTIONAL_ITEM_COLUMNS:
            row.setdefault(name, None)
    return rows


def _audit_times(db, schema, item_ids):
    if not item_ids or not _has(schema, "audit", "item_id", "kind", "created_at"):
        return {}
    kinds = ("checkpoint", "renew", *ACTIVE)
    rows = db.execute(f"""SELECT item_id, kind, MAX(created_at) AS at FROM audit
        WHERE item_id IN ({_marks(item_ids)}) AND kind IN ({_marks(kinds)}) GROUP BY item_id, kind""",
                      (*item_ids, *kinds))
    return {(row["item_id"], row["kind"]): _time(row["at"]) for row in rows}


def _pull_requests(db, schema, issue_ids):
    if not issue_ids or not _has(schema, "published_prs", "issue_id", "url"):
        return {}
    order = "created_at, url" if "created_at" in schema["published_prs"] else "url"
    found = {}
    for row in db.execute(f"SELECT issue_id, url FROM published_prs WHERE issue_id IN ({_marks(issue_ids)}) "
                          f"ORDER BY {order}", issue_ids):
        pr = _pr(row["url"])
        if pr:
            found.setdefault(row["issue_id"], []).append(pr)
    return found


def _unity_queue(db, schema):
    if not _has(schema, "reservations", "item_id", "state", "sequence"):
        return None
    return [row["item_id"] for row in
            db.execute("SELECT item_id FROM reservations WHERE state='queued' ORDER BY sequence")]


def _display_state(row, now):
    if row["state"] == "queued":
        if row["worker_pid"] is not None:
            return "switching_repo" if row["next_root_repo"] else "launching"
        if (_time(row["retry_not_before"]) or 0) > now:
            return "retry_wait"
        return "queued"
    if row["state"] == "awaiting_resource" and row["stage"] == "waiting_for_recovery":
        return "waiting_for_recovery"
    return row["state"]


def _worker(row, times, now, beat_state, beat, renew_seconds):
    """The worker behind a running job, from its lease and renewals in the ledger and from the workers serve
    says it is managing; None for a job that is not running."""
    if row["state"] != "running":
        return None
    lease = _time(row["lease_expires_at"])
    claimed = times.get((row["id"], "running")) or _time(row["created_at"])
    renewals = [at for at in (times.get((row["id"], "renew")), claimed) if at is not None]
    renewed = max(renewals) if renewals else None
    live = beat is not None and beat_state == "fresh" and beat["phase"] == "serving" and beat["workers"] is not None
    tracked = row["id"] in beat["workers"] if live else None
    record = beat["workers"].get(row["id"], {}) if live else {}
    interval = renew_seconds.get(row["skill"])
    if lease is not None and lease <= now:
        state = "lease_expired"
    elif tracked is False and claimed is not None and beat["written_at"] - claimed > UNTRACKED_GRACE:
        state = "untracked"
    elif interval and renewed is not None and now - renewed > RENEWAL_GRACE * interval:
        state = "renewal_overdue"
    else:
        state = "alive"
    return {"state": state, "tracked": tracked, "started_at": record.get("started_at"),
            "deadline": record.get("deadline"), "renewed_at": renewed, "lease_expires_at": lease}


def _worker_attention(jobs):
    items = []
    for job in jobs:
        worker = job["worker"]
        if worker and worker["state"] == "renewal_overdue":
            items.append({"code": "renewal_overdue", "subject": job["identifier"], "since": worker["renewed_at"],
                          "count": None})
        elif worker and worker["state"] == "untracked":
            items.append({"code": "worker_untracked", "subject": job["identifier"], "since": job["state_since"],
                          "count": None})
    return items


def _jobs(rows, times, prs, unity_queue, now, beat_state, beat, renew_seconds):
    waiting = sorted((row for row in rows if _display_state(row, now) == "queued"),
                     key=lambda row: (row["priority"] if type(row["priority"]) is int else 0,
                                      _time(row["created_at"]) or 0, row["id"]))
    positions = {row["id"]: index for index, row in enumerate(waiting, 1)}
    unity = {item_id: index for index, item_id in enumerate(unity_queue, 1)}
    jobs = []
    for row in rows:
        display = _display_state(row, now)
        position = (positions.get(row["id"]) if display == "queued"
                    else unity.get(row["id"]) if row["state"] == "awaiting_resource" else None)
        jobs.append({"identifier": _text(row["identifier"], 32), "title": _text(row["title"], 200),
                     "url": _linear_url(row["url"]), "skill": _text(row["skill"], 32), "state": row["state"],
                     "display_state": display, "stage": _text(row["stage"], 120),
                     "repo": _text(row["root_repo"], 64), "created_at": _time(row["created_at"]),
                     "state_since": times.get((row["id"], row["state"])) or _time(row["created_at"]),
                     "checkpoint_at": times.get((row["id"], "checkpoint")),
                     "retry_at": _time(row["retry_not_before"]) if display == "retry_wait" else None,
                     "queue_position": position, "prs": prs.get(row["issue_id"], []),
                     "worker": _worker(row, times, now, beat_state, beat, renew_seconds)})
    return jobs


def _recent_rows(db, schema, now):
    columns = schema["work_items"]
    no_change = ("CASE WHEN json_valid(w.evidence) THEN json_extract(w.evidence,'$.no_change') IS NOT NULL ELSE 0 END"
                 if "evidence" in columns else "0")
    retried = "EXISTS (SELECT 1 FROM work_items s WHERE s.predecessor_id=w.id)" if "predecessor_id" in columns else "0"
    return [dict(row) for row in db.execute(
        f"""SELECT w.id, w.issue_id, w.skill, w.state, w.updated_at, {no_change} AS no_change,
            {retried} AS retried, {_ISSUE_FIELDS}
            FROM work_items w JOIN issues i ON i.id=w.issue_id
            WHERE w.state IN ({_marks(TERMINAL)}) AND w.updated_at >= ?
            ORDER BY w.updated_at DESC, w.id LIMIT ?""", (*TERMINAL, now - RECENT_SECONDS, RECENT_LIMIT))]


def _history(rows, prs):
    return [{"identifier": _text(row["identifier"], 32), "title": _text(row["title"], 200),
             "url": _linear_url(row["url"]), "skill": _text(row["skill"], 32),
             "outcome": "no_change" if row["state"] == "delivered" and row["no_change"] else row["state"],
             "finished_at": _time(row["updated_at"]), "retried": bool(row["retried"]),
             "prs": prs.get(row["issue_id"], [])} for row in rows]


def _slots(db, schema):
    """Internal slot records; `updated_at` and `reserved` feed attention and are dropped before output."""
    if not _has(schema, "slots", "slot_id", "kind", "state"):
        return None
    optional = [name for name in ("parked_commit", "updated_at") if name in schema["slots"]]
    holders = {}
    if _has(schema, "reservations", "resource", "mode", "item_id", "state"):
        for row in db.execute("SELECT resource, mode, item_id FROM reservations "
                              "WHERE state IN ('active','cancel_requested')"):
            holders[row["resource"]] = row
    names = _identifiers(db, list(dict.fromkeys(row["item_id"] for row in holders.values())))
    recoveries = {}
    if _has(schema, "resource_recoveries", "slot_id", "state", "attempts"):
        # Each slot's newest recovery by rowid. An older one, even an exhausted one, belongs to an earlier hold.
        for row in db.execute("SELECT slot_id, state, attempts FROM resource_recoveries ORDER BY rowid"):
            recoveries[row["slot_id"]] = {"state": _text(row["state"], 32),
                                          "attempts": row["attempts"] if type(row["attempts"]) is int else None,
                                          "max_attempts": MAX_REPAIR_ATTEMPTS}
    slots = []
    select = ", ".join(("slot_id", "kind", "state", *optional))
    for row in db.execute(f"SELECT {select} FROM slots ORDER BY slot_id"):
        row = dict(row)
        holder = holders.get(row["slot_id"])
        # Only a held slot has a current recovery. A newest one that already succeeded belongs to an earlier
        # hold as well: the pool can hold a slot it fails to park before the recovery loop opens a new one.
        recovery = recoveries.get(row["slot_id"]) if row["state"] == "held" else None
        if recovery and recovery["state"] == "recovered":
            recovery = None
        slots.append({"slot_id": _text(row["slot_id"], 64), "kind": _text(row["kind"], 32),
                      "state": _text(row["state"], 32), "commit": _text(row.get("parked_commit"), 7),
                      "holder": names.get(holder["item_id"]) if holder else None,
                      "mode": _text(holder["mode"], 16) if holder else None,
                      "recovery": recovery,
                      "updated_at": _time(row.get("updated_at")), "reserved": holder is not None})
    return slots


def _ledger_attention(db, schema, rows, slots, now):
    items = []

    def add(code, subject, since, count=None):
        items.append({"code": code, "subject": subject, "since": _time(since), "count": count})

    for slot in slots or ():
        if slot["state"] == "held":
            add("slot_held", slot["slot_id"], slot["updated_at"], (slot["recovery"] or {}).get("attempts"))
        elif slot["state"] in ("interactive_busy", "batch_busy") and not slot["reserved"]:
            add("slot_without_reservation", slot["slot_id"], slot["updated_at"])
    for row in rows:
        lease = _time(row["lease_expires_at"])
        if row["state"] == "running" and lease is not None and lease <= now:
            add("lease_expired", _text(row["identifier"], 32), lease)
    if _has(schema, "job_cleanup", "item_id", "done"):
        # set_worker and retry() reopen the row when a new attempt starts: for a job that is active again it
        # means "clean up after this attempt", so only a finished job's row can be overdue. Its age is the job's
        # terminal time, not the row's: Scheduler._retire rewrites job_cleanup.updated_at on every failed attempt,
        # about once a second, and a reopened row keeps the previous attempt's time. work_items.updated_at is
        # the time the job last changed state, the same finished_at recent[] shows. Cancelling a blocked job moves
        # its finish time once, which delays the flag by up to 10 minutes.
        pending = [dict(row) for row in db.execute(
            f"""SELECT c.item_id, w.updated_at FROM job_cleanup c JOIN work_items w ON w.id=c.item_id
                WHERE c.done=0 AND w.state IN ({_marks(TERMINAL)}) AND w.updated_at <= ? ORDER BY w.updated_at""",
            (*TERMINAL, now - CLEANUP_GRACE))]
        names = _identifiers(db, [row["item_id"] for row in pending])
        for row in pending:
            add("cleanup_pending", names.get(row["item_id"]), row["updated_at"])
    if _has(schema, "issue_checks", "issue_id", "error", "failures", "checked_at"):
        for row in db.execute(f"""SELECT c.checked_at, c.failures, {_ISSUE_FIELDS} FROM issue_checks c
                JOIN issues i ON i.id=c.issue_id WHERE c.error IS NOT NULL AND c.failures >= ?
                ORDER BY c.checked_at""", (STATUS_FAILURES,)):
            add("issue_status_error", _text(row["identifier"], 32), row["checked_at"],
                row["failures"] if type(row["failures"]) is int else None)
    if _has(schema, "reservations", "item_id", "state"):
        for row in db.execute(f"""SELECT w.updated_at, {_ISSUE_FIELDS} FROM reservations r
                JOIN work_items w ON w.id=r.item_id JOIN issues i ON i.id=w.issue_id
                WHERE r.state='cancel_requested' AND w.updated_at <= ? ORDER BY w.updated_at""",
                              (now - CANCEL_GRACE,)):
            add("reservation_cancel_pending", _text(row["identifier"], 32), row["updated_at"])
    return items


def _read_ledger(db, now, beat_state, beat, renew_seconds):
    schema = _schema(db)
    rows = _active_rows(db, schema)
    recent = _recent_rows(db, schema, now)
    issue_ids = list(dict.fromkeys([row["issue_id"] for row in rows] + [row["issue_id"] for row in recent]))
    prs = _pull_requests(db, schema, issue_ids)
    queue = _unity_queue(db, schema)
    slots = _slots(db, schema)
    counts = dict.fromkeys(ACTIVE, 0)
    for row in rows:
        counts[row["state"]] += 1
    service = {}
    if _has(schema, "webhook_events", "received_at"):
        service["agent_event_at"] = _time(db.execute("SELECT MAX(received_at) FROM webhook_events").fetchone()[0])
    if _has(schema, "issue_checks", "checked_at", "error"):
        row = db.execute("""SELECT MAX(CASE WHEN error IS NULL AND checked_at > 0 THEN checked_at END) AS ok,
            COUNT(error) AS failing FROM issue_checks""").fetchone()
        service["linear"] = {"last_ok_at": _time(row["ok"]), "failing_issues": row["failing"]}
    jobs = _jobs(rows, _audit_times(db, schema, [row["id"] for row in rows]), prs, queue or [], now,
                 beat_state, beat, renew_seconds)
    sections = {"counts": counts, "active": jobs,
                "slots": None if slots is None else [{key: slot[key] for key in SLOT_FIELDS} for slot in slots],
                "unity_queue": None if queue is None else len(queue),
                "recent": _history(recent, prs)}
    missing = [name for name in KNOWN_TABLES if name not in schema]
    return sections, service, _ledger_attention(db, schema, rows, slots, now) + _worker_attention(jobs), missing


def _loop_state(name, record, now):
    started, finished = record["started_at"], record["finished_at"]
    busy = started is not None and (finished is None or started > finished)
    if record["consecutive_errors"] >= LOOP_ERRORS:
        return "erroring"
    if busy and now - started > LOOP_LIMITS.get(name, max(LOOP_LIMITS.values())):
        return "stalled"
    return "busy" if busy else "idle"


def _service(beat_state, beat, health, now):
    fields = ("phase", "written_at", "started_at", "stopped_at", "revision", "dirty", "runtime")
    heartbeat = {"state": beat_state, **{key: (beat[key] if beat else None) for key in fields}}
    loops = ([{"name": name, "state": _loop_state(name, beat["loops"][name], now), **beat["loops"][name]}
              for name in LOOPS if name in beat["loops"]] if beat else [])
    return {"health": health, "heartbeat": heartbeat, "loops": loops,
            "webhooks": beat["webhooks"] if beat else None, "agent_event_at": None, "linear": None}


def _service_attention(beat_state, beat, health, now, failing_since):
    items = []

    def add(code, subject, since, count=None):
        items.append({"code": code, "subject": subject, "since": _time(since), "count": count})

    phase = beat["phase"] if beat else None
    if not health["ok"] and beat_state == "fresh" and phase == "serving":
        add("receiver_unreachable", None, failing_since)
    if health["ok"] and beat_state == "unreadable":
        add("heartbeat_unreadable", None, None)
    elif health["ok"] and beat is not None and not (beat_state == "fresh" and phase in ("serving", "starting")):
        add("heartbeat_stale", None, beat["written_at"])
    if beat is not None and beat_state == "fresh":
        for name in LOOPS:
            record = beat["loops"].get(name)
            if record is None:
                continue
            state = _loop_state(name, record, now)
            if state == "erroring":
                add("loop_erroring", name, record["error_at"], record["consecutive_errors"])
            elif state == "stalled":
                add("loop_stalled", name, record["started_at"])
        rejected = beat["webhooks"]["last_rejected_at"]
        if rejected is not None and now - rejected < REJECT_WINDOW:
            add("webhook_rejected", None, rejected, beat["webhooks"]["counts"]["rejected"])
    return items


def _verdict(ledger_ok, beat_state, beat, health, failing_since, attention):
    phase = beat["phase"] if beat else None
    if not ledger_ok:
        return "unknown", None
    if phase == "stopped" and not health["ok"]:
        return "stopped", beat["stopped_at"]
    if beat_state == "fresh" and phase == "starting":
        return "starting", beat["started_at"]
    if not health["ok"] and beat_state != "fresh":
        return "unresponsive", beat["written_at"] if beat else _time(failing_since)
    # Rules 5 and 6 carry their own time, however old the other items are. Their items exist exactly when their
    # conditions hold: after rules 2 and 3, a fresh beat with /health failing is a serving one.
    for codes in (("receiver_unreachable",), ("heartbeat_stale", "heartbeat_unreadable")):
        item = next((item for item in attention if item["code"] in codes), None)
        if item:
            return "attention", item["since"]
    if attention:
        times = [item["since"] for item in attention if item["since"] is not None]
        return "attention", min(times) if times else None
    return "ok", None


def build_status(ledger_path, *, heartbeat, health, instance, monitor_revision, now, failing_since=None,
                 renew_seconds=None):
    """The /api/status document. `heartbeat` is heartbeat.read()'s (state, beat) and `health` is
    monitor.probe_health()'s result; `failing_since` is when the monitor first saw the current run of
    /health failures; `renew_seconds` maps a skill to its lease-renewal interval."""
    beat_state, beat = heartbeat
    health = _health(health)  # the caller's dicts are never copied into the document whole
    document = {"schema_version": SCHEMA_VERSION, "generated_at": now, "verdict": None, "verdict_since": None,
                "instance": _instance(instance),
                "monitor": {"revision": monitor_revision[0], "dirty": monitor_revision[1]},
                "service": _service(beat_state, beat, health, now), "counts": dict.fromkeys(ACTIVE, 0),
                "active": [], "slots": None, "unity_queue": None, "recent": [], "attention": [],
                "ledger": {"ok": False, "error_type": None, "missing_optional": []}}
    attention = []
    try:
        with snapshot_connection(ledger_path) as db:
            sections, service, ledger_attention, missing = _read_ledger(db, now, beat_state, beat,
                                                                        renew_seconds or {})
    except (sqlite3.Error, OSError, ValueError) as exc:
        document["ledger"]["error_type"] = type(exc).__name__
    else:
        document.update(sections)
        document["service"].update(service)
        document["ledger"].update(ok=True, missing_optional=missing)
        attention.extend(ledger_attention)
    attention.extend(_service_attention(beat_state, beat, health, now, failing_since))
    document["attention"] = attention
    document["verdict"], document["verdict_since"] = _verdict(document["ledger"]["ok"], beat_state, beat,
                                                             health, failing_since, attention)
    return document
