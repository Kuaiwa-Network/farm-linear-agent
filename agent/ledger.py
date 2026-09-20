"""FarmBot ledger: SQLite work items, leases, handoffs, outbox and audit.

Connectors stay outside this module. confirm_comment records a caller's
remote-readback attestation; it cannot verify Linear itself.
"""

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import secrets
import sqlite3
import time
from uuid import UUID, uuid4

from . import memory

MARKER = re.compile(r"\[farmbot:[0-9a-f]{64}\]")
STATES = ("queued", "running", "awaiting_input", "awaiting_resource",
          "delivered", "blocked", "cancelled", "failed")
ACTIVE_STATES = ("queued", "running", "awaiting_input", "awaiting_resource")
TERMINAL_STATUS_TYPES = ("completed", "canceled")


class LedgerError(ValueError):
    """Invalid input or a conflicting state transition; safe to show on stderr."""


def _text(value, name, *, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise LedgerError(f"{name} must be {'a string' if empty else 'a nonempty string'}")
    return value


def _uuid(value, name):
    _text(value, name)
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise LedgerError(f"{name} must be a UUID") from exc


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise LedgerError("value must contain valid JSON data") from exc


def _timestamp(value, name):
    _text(value, name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timezone missing")
    except ValueError as exc:
        raise LedgerError(f"{name} must be an ISO-8601 timestamp with a timezone") from exc
    return value


COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
TARGET_KEYS = ("repository", "requested_ref", "commit_sha", "server_environment", "selected_at")


def checked_target(raw):
    """Validate then reproject: only target metadata reaches the ledger, the dispatch payload and the slot."""
    if not isinstance(raw, dict):
        raise LedgerError("target must be an object")
    _text(raw.get("repository"), "target repository")
    _text(raw.get("requested_ref"), "target requested_ref")
    _text(raw.get("server_environment"), "target server_environment")
    commit = raw.get("commit_sha")
    if not isinstance(commit, str) or not COMMIT_SHA.match(commit):
        raise LedgerError("target commit_sha must be a full lowercase 40-character hex commit")
    _timestamp(raw.get("selected_at"), "target selected_at")
    return {key: raw[key] for key in TARGET_KEYS}


def _normalize(raw):
    if not isinstance(raw, dict):
        raise LedgerError("issue must be an object")
    required = {"id", "identifier", "team_id", "url", "branch_name", "title", "description",
                "status", "status_type", "labels", "priority", "archived", "attachments",
                "comments", "detail_complete", "comments_complete"}
    missing = required - raw.keys()
    if missing:
        raise LedgerError(f"issue missing fields: {', '.join(sorted(missing))}")
    if raw["detail_complete"] is not True or raw["comments_complete"] is not True:
        raise LedgerError("complete issue detail and all comment pages are required")
    value = {key: raw[key] for key in required}
    value["delegate_id"] = raw.get("delegate_id")
    for field in ["id", "team_id"]:
        value[field] = _uuid(value[field], field)
    for field in ["identifier", "title", "status", "status_type", "url"]:
        _text(value[field], field)
    _text(value["description"], "description", empty=True)
    _text(value["branch_name"], "branch_name", empty=True)
    if value["delegate_id"] is not None:
        value["delegate_id"] = _uuid(value["delegate_id"], "delegate_id")
    if type(value["priority"]) is not int or not 0 <= value["priority"] <= 4:
        raise LedgerError("priority must be an integer from 0 to 4")
    if type(value["archived"]) is not bool:
        raise LedgerError("archived must be a boolean")
    for field in ["labels", "attachments"]:
        if not isinstance(value[field], list):
            raise LedgerError(f"{field} must be an array of stable strings")
        value[field] = sorted({_text(item, field) for item in value[field]})
    if not isinstance(value["comments"], list):
        raise LedgerError("comments must be an array")
    comments, seen = [], set()
    fields = {"id", "body", "author_kind", "created_at", "updated_at"}
    for raw_comment in value["comments"]:
        if not isinstance(raw_comment, dict) or not fields <= raw_comment.keys():
            raise LedgerError("each comment needs id, body, author_kind, created_at and updated_at")
        comment = {key: raw_comment[key] for key in fields}
        _text(comment["id"], "comment id")
        _text(comment["body"], "comment body", empty=True)
        if comment["author_kind"] not in ("human", "bot", "unknown"):
            raise LedgerError("comment author_kind must be human, bot or unknown")
        for field in ["created_at", "updated_at"]:
            _timestamp(comment[field], f"comment {field}")
        if comment["id"] in seen:
            raise LedgerError("duplicate comment id")
        seen.add(comment["id"])
        comments.append(comment)
    value["comments"] = sorted(comments, key=lambda item: item["id"])
    return value


def _hash_token(token):
    """Only the digest is stored: a leaked ledger file must not hand out live claims."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _in_scope(issue):
    return not issue["archived"] and issue["status_type"] not in TERMINAL_STATUS_TYPES


def _fingerprint(issue, own_bodies, own_prs=()):
    material = {key: issue[key] for key in ["title", "description", "attachments"]}
    material["attachments"] = [url for url in issue["attachments"] if url not in own_prs]
    material["comments"] = [{"id": comment["id"], "body": comment["body"]}
                            for comment in issue["comments"]
                            if comment["author_kind"] != "bot" and comment["body"] not in own_bodies]
    return hashlib.sha256(_json(material).encode("utf-8")).hexdigest()


def _validate_handoff(value):
    """Bound resumable memory and separate evidence from conjecture."""
    schemas = {"facts": {"claim", "evidence"}, "hypotheses": None,
               "checks": {"command", "result", "evidence"},
               "repositories": {"path", "branch", "head"}, "next_actions": None}
    if not isinstance(value, dict) or set(value) != set(schemas):
        raise LedgerError("handoff requires facts, hypotheses, checks, repositories and next_actions only")
    if len(_json(value)) > 12000:
        raise LedgerError("handoff exceeds 12000 characters; store detailed evidence in files")
    for field, fields in schemas.items():
        items = value[field]
        if not isinstance(items, list) or len(items) > 20:
            raise LedgerError(f"handoff {field} must be an array of at most 20 items")
        for item in items:
            if fields is None:
                texts = [item]
            else:
                if not isinstance(item, dict) or set(item) != fields:
                    raise LedgerError(f"handoff {field} items require {', '.join(sorted(fields))}")
                texts = item.values()
            for text in texts:
                _text(text, f"handoff {field}")
                if len(text) > 2000:
                    raise LedgerError(f"handoff {field} text exceeds 2000 characters")


class Ledger:
    """One connection, owned by its caller; use a separate connection per thread."""

    def __init__(self, path, *, clock=time.time, lease_seconds=2700, check_same_thread=True):
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, (int, float)) or not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise LedgerError("lease_seconds must be a positive finite number")
        self.clock = clock
        self.lease_seconds = lease_seconds
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=check_same_thread)
        self.connection.row_factory = sqlite3.Row
        try:
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.executescript("""
                CREATE TABLE IF NOT EXISTS issues (
                    id TEXT PRIMARY KEY,
                    metadata TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    observed_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    issue_id TEXT,
                    delegation INTEGER NOT NULL,
                    target_json TEXT,
                    guidance TEXT,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS work_items (
                    id TEXT PRIMARY KEY,
                    issue_id TEXT NOT NULL REFERENCES issues(id),
                    session_id TEXT NOT NULL REFERENCES sessions(session_id),
                    skill TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('queued','running','awaiting_input',
                        'awaiting_resource','delivered','blocked','cancelled','failed')),
                    stage TEXT NOT NULL DEFAULT 'intake',
                    priority INTEGER NOT NULL,
                    host TEXT,
                    claimed_fingerprint TEXT,
                    generation INTEGER NOT NULL DEFAULT 0,
                    requeue_requested INTEGER NOT NULL DEFAULT 0,
                    resume_authorized INTEGER NOT NULL DEFAULT 0,
                    token TEXT,
                    lease_expires_at REAL,
                    lease_seconds REAL,
                    worker_pid INTEGER,
                    needs_resource TEXT,
                    target_json TEXT,
                    checkpoint TEXT NOT NULL DEFAULT '{}',
                    evidence TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_item_per_issue
                    ON work_items(issue_id)
                    WHERE state IN ('queued','running','awaiting_input','awaiting_resource');
                CREATE TABLE IF NOT EXISTS outbox (
                    action_id TEXT PRIMARY KEY,
                    item_id TEXT NOT NULL REFERENCES work_items(id),
                    issue_id TEXT NOT NULL REFERENCES issues(id),
                    fingerprint TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('started','blocker','delivery')),
                    marker TEXT NOT NULL,
                    body TEXT NOT NULL,
                    remote_id TEXT,
                    created_at REAL NOT NULL,
                    confirmed_at REAL,
                    UNIQUE(issue_id, fingerprint, generation, kind)
                );
                CREATE TABLE IF NOT EXISTS published_prs (
                    issue_id TEXT NOT NULL REFERENCES issues(id),
                    url TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(issue_id, url)
                );
                CREATE TABLE IF NOT EXISTS inbox (
                    id INTEGER PRIMARY KEY,
                    item_id TEXT NOT NULL REFERENCES work_items(id),
                    body TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    consumed_at REAL
                );
                CREATE TABLE IF NOT EXISTS slots (
                    slot_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    host TEXT NOT NULL,
                    folder TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('idle_closed','idle_open','switching',
                        'interactive_busy','batch_busy','held')),
                    parked_commit TEXT,
                    instance TEXT,
                    mcp_address TEXT,
                    account TEXT,
                    last_switch_at REAL,
                    updated_at REAL NOT NULL,
                    UNIQUE(host, folder)
                );
                CREATE TABLE IF NOT EXISTS reservations (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    reservation_id TEXT NOT NULL UNIQUE,
                    item_id TEXT NOT NULL REFERENCES work_items(id),
                    generation INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    mode TEXT NOT NULL CHECK(mode IN ('interactive','batch')),
                    resource TEXT,
                    host TEXT,
                    commit_sha TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('queued','active','cancel_requested','cancelled','released')),
                    owner TEXT,
                    token_hash TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    acquired_at REAL,
                    released_at REAL,
                    release_reason TEXT,
                    CHECK ((owner IS NULL) = (token_hash IS NULL)),
                    CHECK (state NOT IN ('active','cancel_requested')
                           OR (token_hash IS NOT NULL AND resource IS NOT NULL))
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_owner_per_resource
                    ON reservations(resource)
                    WHERE state IN ('active','cancel_requested');
                CREATE UNIQUE INDEX IF NOT EXISTS one_open_reservation_per_item
                    ON reservations(item_id)
                    WHERE state IN ('queued','active','cancel_requested');
                CREATE INDEX IF NOT EXISTS reservations_queue ON reservations(kind, state, sequence);
                CREATE TABLE IF NOT EXISTS identity_observations (
                    observation_id TEXT PRIMARY KEY,
                    item_id TEXT NOT NULL REFERENCES work_items(id),
                    reservation_id TEXT NOT NULL,
                    slot_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    result_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS identity_by_item ON identity_observations(item_id, created_at);
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL,
                    scope TEXT NOT NULL, body TEXT NOT NULL, source TEXT NOT NULL,
                    build_commit TEXT, created_by_item TEXT, updated_by_item TEXT,
                    actor_kind TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    revision INTEGER NOT NULL, deleted INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS memory_requests (
                    actor TEXT NOT NULL, request_id TEXT NOT NULL, payload_hash TEXT NOT NULL,
                    note_id TEXT NOT NULL REFERENCES memories(id), PRIMARY KEY(actor, request_id)
                );
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY,
                    item_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    details TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
            """)
            # Columns added after the first ledgers were written; CREATE TABLE IF NOT EXISTS leaves those files as they were.
            for table, column, declaration in (("sessions", "guidance", "TEXT"), ("work_items", "lease_seconds", "REAL")):
                present = {row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})")}
                if column not in present:
                    self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
        except Exception:
            self.connection.close()
            raise

    def close(self):
        """Release this process's SQLite connection."""
        self.connection.close()

    @contextmanager
    def _transaction(self):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def _row(self, item_id):
        _text(item_id, "item")
        row = self.connection.execute("SELECT * FROM work_items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise LedgerError(f"unknown work item: {item_id}")
        return row

    def _issue_row(self, issue_id):
        issue_id = _uuid(issue_id, "issue")
        row = self.connection.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()
        if row is None:
            raise LedgerError(f"unknown issue: {issue_id}")
        return row

    def _view(self, row):
        issue = json.loads(self._issue_row(row["issue_id"])["metadata"])
        result = {key: row[key] for key in ["id", "issue_id", "session_id", "skill", "state", "stage",
                                            "priority", "host", "generation", "lease_expires_at",
                                            "worker_pid", "needs_resource", "created_at"]}
        result.update(identifier=issue["identifier"], target=json.loads(row["target_json"]) if row["target_json"] else None,
                      resume_authorized=bool(row["resume_authorized"]), checkpoint=json.loads(row["checkpoint"]),
                      evidence=json.loads(row["evidence"]))
        return result

    def _audit(self, item_id, kind, reason="", details=None):
        self.connection.execute("INSERT INTO audit(item_id,kind,reason,details,created_at) VALUES(?,?,?,?,?)",
                                (item_id, kind, reason, _json(details or {}), self.clock()))

    def note(self, item_id, kind, reason="", details=None):
        """Record an operator action in `audit`, so a human act outside Linear leaves the trail a webhook would."""
        with self._transaction():
            self._audit(item_id, kind, reason, details)

    def _owned(self, item_id, token):
        row = self._row(item_id)
        presented = _hash_token(token) if isinstance(token, str) and token.isascii() and token else ""
        if (row["state"] != "running" or not presented
                or not secrets.compare_digest(row["token"] or "", presented)):
            raise LedgerError("running claim and matching token required")
        if row["lease_expires_at"] <= self.clock():
            raise LedgerError("lease expired; the launcher recovers expired work, workers must stop")
        return row

    def observe_issue(self, raw):
        """Store one complete snapshot. Observing a comment is not authority to restart."""
        issue = _normalize(raw)
        with self._transaction():
            own_bodies = {r["body"] for r in self.connection.execute("SELECT body FROM outbox WHERE issue_id=?", (issue["id"],))}
            own_prs = {r["url"] for r in self.connection.execute("SELECT url FROM published_prs WHERE issue_id=?", (issue["id"],))}
            fingerprint = _fingerprint(issue, own_bodies, own_prs)
            self.connection.execute("""INSERT INTO issues(id,metadata,fingerprint,observed_at) VALUES(?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET metadata=excluded.metadata,fingerprint=excluded.fingerprint,observed_at=excluded.observed_at""",
                                    (issue["id"], _json(issue), fingerprint, self.clock()))
        return {"id": issue["id"], "identifier": issue["identifier"], "fingerprint": fingerprint,
                "in_scope": _in_scope(issue)}

    def issue(self, issue_id):
        return json.loads(self._issue_row(issue_id)["metadata"])

    def ensure_session(self, session_id, issue_id, delegation, guidance=None):
        """Guidance is Linear's operator text for this session; later events may add it."""
        _text(session_id, "session_id")
        if guidance is not None:
            _text(guidance, "guidance", empty=True)
        with self._transaction():
            self.connection.execute("""INSERT OR IGNORE INTO sessions(session_id,issue_id,delegation,guidance,created_at)
                VALUES(?,?,?,?,?)""", (session_id, issue_id, int(bool(delegation)), guidance, self.clock()))
            if isinstance(guidance, str) and guidance.strip():
                self.connection.execute("UPDATE sessions SET guidance=? WHERE session_id=?", (guidance, session_id))
        return self.session(session_id)

    def set_session_target(self, session_id, target):
        """The pin for later items in this session. An accepted item keeps the target it snapshotted (spec §6)."""
        target = checked_target(target)
        with self._transaction():
            if self.session(session_id) is None:
                raise LedgerError(f"unknown session: {session_id}")
            self.connection.execute("UPDATE sessions SET target_json=? WHERE session_id=?",
                                    (_json(target), session_id))
        return self.session(session_id)

    def session(self, session_id):
        row = self.connection.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if row is None:
            return None
        return {"session_id": row["session_id"], "issue_id": row["issue_id"], "delegation": bool(row["delegation"]),
                "target": json.loads(row["target_json"]) if row["target_json"] else None, "guidance": row["guidance"]}

    def create_work_item(self, *, issue_id, session_id, skill, target=None):
        _text(skill, "skill")
        with self._transaction():
            issue = json.loads(self._issue_row(issue_id)["metadata"])
            if self.session(session_id) is None:
                raise LedgerError(f"unknown session: {session_id}")
            if not _in_scope(issue):
                raise LedgerError("issue is archived or in a terminal status")
            if self.connection.execute("SELECT 1 FROM work_items WHERE issue_id=? AND state IN ('queued','running','awaiting_input','awaiting_resource')",
                                       (issue["id"],)).fetchone():
                raise LedgerError("an active work item already exists for this issue")
            item_id = str(uuid4())
            now = self.clock()
            self.connection.execute("""INSERT INTO work_items(id,issue_id,session_id,skill,state,priority,target_json,created_at,updated_at)
                VALUES(?,?,?,?,'queued',?,?,?,?)""",
                                    (item_id, issue["id"], session_id, skill, issue["priority"] or 5,
                                     json.dumps(target) if target is not None else None, now, now))
            self._audit(item_id, "create", f"skill {skill}")
            return self._view(self._row(item_id))

    def active_item_for_session(self, session_id):
        row = self.connection.execute("""SELECT * FROM work_items WHERE session_id=? AND state IN
            ('queued','running','awaiting_input','awaiting_resource') ORDER BY created_at DESC LIMIT 1""", (session_id,)).fetchone()
        return self._view(row) if row else None

    def items_for_session(self, session_id):
        rows = self.connection.execute("SELECT * FROM work_items WHERE session_id=? ORDER BY created_at, id", (session_id,))
        return [self._view(row) for row in rows]

    def active_item_for_issue(self, issue_id):
        row = self.connection.execute("""SELECT * FROM work_items WHERE issue_id=? AND state IN
            ('queued','running','awaiting_input','awaiting_resource') ORDER BY created_at DESC LIMIT 1""", (issue_id,)).fetchone()
        return self._view(row) if row else None

    def item(self, item_id):
        return self._view(self._row(item_id))

    def queue(self):
        rows = self.connection.execute("SELECT * FROM work_items WHERE state='queued' AND worker_pid IS NULL ORDER BY priority, created_at, id")
        return [self._view(row) for row in rows]

    def launched(self):
        """Queued items whose worker was spawned but has not claimed yet."""
        rows = self.connection.execute("SELECT * FROM work_items WHERE state='queued' AND worker_pid IS NOT NULL ORDER BY created_at, id")
        return [{**self._view(row), "updated_at": row["updated_at"]} for row in rows]

    def status(self):
        rows = [self._view(row) for row in self.connection.execute("SELECT * FROM work_items ORDER BY created_at, id")]
        counts = {}
        for row in rows:
            counts[row["state"]] = counts.get(row["state"], 0) + 1
        counts["total"] = len(rows)
        compact = [{key: row[key] for key in ("id", "identifier", "skill", "state", "stage", "priority", "lease_expires_at", "worker_pid")}
                   for row in rows]
        return {"counts": counts, "items": compact,
                "recovery_required": [row["id"] for row in rows if row["state"] == "running" and row["lease_expires_at"] <= self.clock()]}

    def borrowed_comments(self, limit=20):
        """Items whose conclusion never reached the issue, because another item had already posted one.

        Deliberately not part of status(): the scheduler calls that twice a second and this scans `audit`,
        which has no index and only grows. The operator readers merge it in instead.

        This exists because the alternative was silence. An item created by `agent.service enqueue` has a
        `local-` session and no Linear agent session at all, so spec §6's reporting surface for it is the
        issue comment — and a deduplicated item posts none. Its own words went to `audit`, which until now
        had one writer and no readers anywhere in the codebase. A `started` marker states no conclusion and
        is not listed; the trail keeps it either way.
        """
        rows = self.connection.execute(
            """SELECT a.item_id, a.reason, a.details, a.created_at, w.issue_id
               FROM audit a JOIN work_items w ON w.id = a.item_id
               WHERE a.kind='deduplicated' AND a.reason IN ('blocker','delivery')
               ORDER BY a.id DESC LIMIT ?""", (limit,))
        listed = []
        for row in rows:
            details = json.loads(row["details"])
            listed.append({"item_id": row["item_id"], "kind": row["reason"], "created_at": row["created_at"],
                           "identifier": json.loads(self._issue_row(row["issue_id"])["metadata"])["identifier"],
                           "prepared_by": details.get("prepared_by"), "action_id": details.get("action_id"),
                           "suppressed_body": details.get("suppressed_body")})
        return listed

    PR_URL = re.compile(r"https://[A-Za-z0-9.-]+(?::[0-9]+)?/[^/?#\s]+/[^/?#\s]+/pull/[1-9][0-9]*")

    def _set_state(self, item_id, state, reason, **columns):
        assignments = ",".join(f"{name}=?" for name in columns)
        values = list(columns.values())
        self.connection.execute(f"UPDATE work_items SET state=?,updated_at=?{',' + assignments if assignments else ''} WHERE id=?",
                                (state, self.clock(), *values, item_id))
        self._audit(item_id, state, reason)

    def claim(self, item_id, *, worker_id):
        _text(worker_id, "worker_id")
        if len(worker_id) > 200 or "\n" in worker_id:
            raise LedgerError("worker_id must be a runtime identifier of at most 200 characters")
        with self._transaction():
            row = self._row(item_id)
            if row["state"] != "queued":
                raise LedgerError("only a queued work item can be claimed")
            issue_row = self._issue_row(row["issue_id"])
            if not _in_scope(json.loads(issue_row["metadata"])):
                raise LedgerError("issue left scope; cancel instead of claiming")
            token = "claim_" + secrets.token_urlsafe(32)
            generation = row["generation"] + int(bool(row["requeue_requested"]))
            checkpoint = json.loads(row["checkpoint"])
            checkpoint.pop("worker_id", None)
            checkpoint["worker_id"] = worker_id
            self._set_state(item_id, "running", "claim", token=_hash_token(token),
                            lease_expires_at=self.clock() + (row["lease_seconds"] or self.lease_seconds),
                            claimed_fingerprint=issue_row["fingerprint"], generation=generation, requeue_requested=0,
                            resume_authorized=0, checkpoint=_json(checkpoint))
            result = self._view(self._row(item_id))
            result["token"] = token  # the only time the raw token exists outside the worker
            return result

    def renew(self, item_id, token):
        with self._transaction():
            row = self._owned(item_id, token)
            self.connection.execute("UPDATE work_items SET lease_expires_at=?,updated_at=? WHERE id=?",
                                    (self.clock() + (row["lease_seconds"] or self.lease_seconds), self.clock(), row["id"]))
            self._audit(row["id"], "renew")
            return self._view(self._row(row["id"]))

    def set_worker(self, item_id, pid, host, lease_seconds=None):
        """Record the launched worker and, with it, the lease this item's skill budgets."""
        if type(pid) is not int or pid <= 0:
            raise LedgerError("pid must be a positive integer")
        if lease_seconds is not None and (isinstance(lease_seconds, bool) or not isinstance(lease_seconds, (int, float))
                                          or not math.isfinite(lease_seconds) or lease_seconds <= 0):
            raise LedgerError("lease_seconds must be a positive finite number")
        with self._transaction():
            row = self._row(item_id)
            if row["state"] not in ("queued", "running"):
                raise LedgerError("worker can only be recorded for queued or running items")
            self.connection.execute(
                "UPDATE work_items SET worker_pid=?,host=?,lease_seconds=COALESCE(?,lease_seconds),updated_at=? WHERE id=?",
                (pid, host, lease_seconds, self.clock(), row["id"]))
            self._audit(row["id"], "worker", f"pid {pid} on {host}")
            return self._view(self._row(row["id"]))

    SLOT_STATES = ("idle_closed", "idle_open", "switching", "interactive_busy", "batch_busy", "held")
    FREE_SLOT_STATES = ("idle_closed", "idle_open")

    def _slot_row(self, slot_id):
        row = self.connection.execute("SELECT * FROM slots WHERE slot_id=?", (slot_id,)).fetchone()
        if row is None:
            raise LedgerError(f"unknown slot: {slot_id}")
        return row

    @staticmethod
    def _slot_view(row):
        return {key: row[key] for key in ("slot_id", "kind", "host", "folder", "state", "parked_commit",
                                          "instance", "mcp_address", "account", "last_switch_at")}

    def ensure_slot(self, slot_id, *, kind, host, folder, instance=None, mcp_address=None, account=None):
        """Upsert a slot from the host configuration. Discovered values are never cleared by a later None."""
        for value, name in ((slot_id, "slot_id"), (kind, "kind"), (host, "host"), (folder, "folder")):
            _text(value, name)
        with self._transaction():
            self.connection.execute(
                """INSERT INTO slots(slot_id,kind,host,folder,state,mcp_address,account,instance,updated_at)
                   VALUES(?,?,?,?,'idle_closed',?,?,?,?)
                   ON CONFLICT(slot_id) DO UPDATE SET kind=excluded.kind, host=excluded.host,
                       folder=excluded.folder,
                       mcp_address=COALESCE(excluded.mcp_address, slots.mcp_address),
                       account=COALESCE(excluded.account, slots.account),
                       instance=COALESCE(excluded.instance, slots.instance),
                       updated_at=excluded.updated_at""",
                (slot_id, kind, host, str(folder), mcp_address, account, instance, self.clock()))
        return self.slot(slot_id)

    def slot(self, slot_id):
        row = self.connection.execute("SELECT * FROM slots WHERE slot_id=?", (slot_id,)).fetchone()
        return self._slot_view(row) if row else None

    def slots(self, kind=None, host=None):
        clauses, values = [], []
        if kind:
            clauses.append("kind=?")
            values.append(kind)
        if host:
            clauses.append("host=?")
            values.append(host)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return [self._slot_view(row) for row in
                self.connection.execute(f"SELECT * FROM slots{where} ORDER BY slot_id", values)]

    _UNSET = object()

    def set_slot_state(self, slot_id, state, *, parked_commit=_UNSET, instance=_UNSET, last_switch_at=_UNSET):
        if state not in self.SLOT_STATES:
            raise LedgerError(f"slot state must be one of {self.SLOT_STATES}")
        with self._transaction():
            row = self._slot_row(slot_id)
            columns, values = ["state=?"], [state]
            for name, value in (("parked_commit", parked_commit), ("instance", instance),
                                ("last_switch_at", last_switch_at)):
                if value is not self._UNSET:
                    columns.append(f"{name}=?")
                    values.append(value)
            self.connection.execute(f"UPDATE slots SET {','.join(columns)},updated_at=? WHERE slot_id=?",
                                    (*values, self.clock(), row["slot_id"]))
            self._audit(slot_id, "slot", state)
        return self.slot(slot_id)

    def checkpoint(self, item_id, token, progress):
        if not isinstance(progress, dict):
            raise LedgerError("checkpoint input must be an object")
        if "handoff" in progress:
            _validate_handoff(progress["handoff"])
        published = progress.get("published_prs", [])
        if not isinstance(published, list):
            raise LedgerError("published_prs must be an array of canonical HTTPS PR URLs")
        for url in published:
            _text(url, "published PR URL")
            if not self.PR_URL.fullmatch(url):
                raise LedgerError("published_prs must contain canonical HTTPS PR URLs without credentials or query parameters")
        stage = progress.get("stage")
        if stage is not None:
            _text(stage, "stage")
        with self._transaction():
            row = self._owned(item_id, token)
            previous = json.loads(row["checkpoint"])
            progress = dict(progress)
            if previous.get("worker_id"):
                if "worker_id" in progress and progress["worker_id"] != previous["worker_id"]:
                    raise LedgerError("checkpoint cannot change the running worker identity")
                progress["worker_id"] = previous["worker_id"]
            progress.pop("handoff_meta", None)
            if "handoff" in progress:
                progress["handoff_meta"] = {"fingerprint": row["claimed_fingerprint"],
                                            "generation": row["generation"], "recorded_at": self.clock()}
            elif "handoff" in previous:
                progress["handoff"] = previous["handoff"]
                progress["handoff_meta"] = previous.get("handoff_meta")
            known = {r["url"] for r in self.connection.execute("SELECT url FROM published_prs WHERE issue_id=?", (row["issue_id"],))}
            existing_input = set(json.loads(self._issue_row(row["issue_id"])["metadata"])["attachments"])
            for url in sorted(set(published) - known):
                if url in existing_input:
                    raise LedgerError("published PR was already issue input; reconcile it instead of registering it as new output")
                self.connection.execute("INSERT INTO published_prs(issue_id,url,generation,created_at) VALUES(?,?,?,?)",
                                        (row["issue_id"], url, row["generation"], self.clock()))
                self._audit(row["id"], "published_pr", details={"url": url})
            self.connection.execute("UPDATE work_items SET checkpoint=?,stage=COALESCE(?,stage),updated_at=? WHERE id=?",
                                    (_json(progress), stage, self.clock(), row["id"]))
            self._audit(row["id"], "checkpoint", stage or "")
            return self._view(self._row(row["id"]))

    def await_input(self, item_id, token, question):
        _text(question, "question")
        with self._transaction():
            row = self._owned(item_id, token)
            checkpoint = json.loads(row["checkpoint"])
            checkpoint["pending_question"] = question
            # Linear may deliver the answer between posting the question and this
            # transaction. Do not strand that reply behind an awaiting-input gate.
            pending = self.connection.execute("SELECT 1 FROM inbox WHERE item_id=? AND consumed_at IS NULL",
                                              (item_id,)).fetchone() is not None
            self._set_state(row["id"], "queued" if pending else "awaiting_input", "human gate",
                            token=None, lease_expires_at=None, worker_pid=None,
                            resume_authorized=int(pending), checkpoint=_json(checkpoint))
            return self._view(self._row(row["id"]))

    RESERVATION_OPEN = ("queued", "active", "cancel_requested")

    @staticmethod
    def _reservation_view(row):
        return {key: row[key] for key in ("reservation_id", "sequence", "item_id", "generation", "kind", "mode",
                                          "resource", "host", "commit_sha", "state", "attempts", "created_at",
                                          "acquired_at", "released_at", "release_reason")}

    def await_resource(self, item_id, token, resource, mode):
        _text(resource, "resource")
        if mode not in ("interactive", "batch"):
            raise LedgerError("mode must be interactive or batch")
        with self._transaction():
            row = self._owned(item_id, token)
            target = json.loads(row["target_json"]) if row["target_json"] else None
            if not target or not COMMIT_SHA.match(target.get("commit_sha") or ""):
                raise LedgerError("a resource request needs a pinned commit; this item has none")
            open_row = self.connection.execute(
                f"""SELECT reservation_id FROM reservations WHERE item_id=? AND state IN
                    ({','.join('?' * len(self.RESERVATION_OPEN))})""",
                (row["id"], *self.RESERVATION_OPEN)).fetchone()
            if open_row:
                raise LedgerError("release the resource this item already holds before requesting another")
            reservation_id = str(uuid4())
            self.connection.execute(
                """INSERT INTO reservations(reservation_id,item_id,generation,kind,mode,commit_sha,state,created_at)
                   VALUES(?,?,?,?,?,?,'queued',?)""",
                (reservation_id, row["id"], row["generation"], resource, mode, target["commit_sha"], self.clock()))
            self._set_state(row["id"], "awaiting_resource", f"needs {resource}:{mode}", token=None,
                            lease_expires_at=None, worker_pid=None, needs_resource=f"{resource}:{mode}")
            self._audit(row["id"], "reservation", "queued", details={"reservation_id": reservation_id, "mode": mode})
            return self._view(self._row(row["id"]))

    def acquire(self, kind, *, owner, host):
        """Grant the oldest queued request of this kind a free slot. FIFO by arrival, never by item priority.

        There is deliberately no kind-wide "is anything active?" pre-check. It would read as an optimisation
        and behave as a restriction: with a second slot it would refuse to grant slot 2 while slot 1 was busy,
        and with a second host it would starve one host behind the other. A busy slot is not in
        FREE_SLOT_STATES, which is the whole gate; the partial unique index is the fence behind it. Because
        the body runs inside BEGIN IMMEDIATE, two connections racing here serialize: the loser reads the slot
        this transaction already moved to 'switching' and returns None, so the index never has to fire.
        """
        _text(kind, "kind")
        _text(owner, "owner")
        _text(host, "host")
        with self._transaction():
            row = self.connection.execute(
                "SELECT * FROM reservations WHERE kind=? AND state='queued' ORDER BY sequence LIMIT 1",
                (kind,)).fetchone()
            if row is None:
                return None
            # Spec §7 scheduling preference: interactive wants an Editor already open, batch wants none.
            order = ("idle_open", "idle_closed") if row["mode"] == "interactive" else ("idle_closed", "idle_open")
            free = [s for s in self.slots(kind=kind, host=host) if s["state"] in self.FREE_SLOT_STATES]
            free.sort(key=lambda s: (order.index(s["state"]), s["slot_id"]))
            if not free:
                return None
            slot = free[0]
            token = "res_" + secrets.token_urlsafe(32)
            try:
                self.connection.execute(
                    """UPDATE reservations SET state='active',resource=?,host=?,owner=?,token_hash=?,acquired_at=?
                       WHERE reservation_id=?""",
                    (slot["slot_id"], host, owner, _hash_token(token), self.clock(), row["reservation_id"]))
            except sqlite3.IntegrityError as exc:
                # one_active_owner_per_resource fired: the slot says it is free while a reservation still
                # holds it. The index is the fence and it is doing its job; what a caller cannot act on is a
                # raw sqlite3.IntegrityError, which is indistinguishable from a corrupt database. The
                # transaction rolls back, so the holder keeps the slot it never gave up.
                raise LedgerError(f"{slot['slot_id']} is in {slot['state']} but a reservation still holds it; "
                                  f"the slot was returned to the pool early") from exc
            self.connection.execute("UPDATE slots SET state='switching',updated_at=? WHERE slot_id=?",
                                    (self.clock(), slot["slot_id"]))
            self._audit(row["item_id"], "reservation", "acquired",
                        details={"reservation_id": row["reservation_id"], "slot": slot["slot_id"]})
            granted = self._reservation_view(self.connection.execute(
                "SELECT * FROM reservations WHERE reservation_id=?", (row["reservation_id"],)).fetchone())
            granted["folder"] = slot["folder"]
            granted["token"] = token  # the only time the raw token exists outside the pool
            return granted

    def reservation(self, reservation_id):
        row = self.connection.execute("SELECT * FROM reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
        return self._reservation_view(row) if row else None

    def reservations(self, states=None):
        states = tuple(states) if states else None
        sql = "SELECT * FROM reservations"
        values = ()
        if states:
            sql += f" WHERE state IN ({','.join('?' * len(states))})"
            values = states
        return [self._reservation_view(row) for row in self.connection.execute(sql + " ORDER BY sequence", values)]

    def active_reservation(self, item_id):
        row = self.connection.execute(
            "SELECT * FROM reservations WHERE item_id=? AND state IN ('active','cancel_requested')",
            (item_id,)).fetchone()
        return self._reservation_view(row) if row else None

    def active_reservation_on(self, slot_id):
        """The holder of a slot, asked from the slot's side rather than the item's.

        SlotPool.park_idle needs this: `release` leaves the slot 'switching' and so does `acquire`, so the
        state alone cannot tell a slot that is on its way back to the pool from one that is at that moment
        being handed to a worker. Parking the second would return to the pool a slot a live reservation
        still owns.
        """
        row = self.connection.execute(
            "SELECT * FROM reservations WHERE resource=? AND state IN ('active','cancel_requested')",
            (slot_id,)).fetchone()
        return self._reservation_view(row) if row else None

    def last_reservation_on(self, slot_id):
        """The reservation that most recently held this slot, whatever state it ended in.

        SlotPool.park_idle needs the *departing* mode, and neither the slot row nor the pool's own memory
        can supply it. The row cannot, because `release` has already overwritten the slot's state with the
        transient 'switching'. An in-process note cannot either: the normal way a slot comes back is the
        worker's own `release-resource`, which runs in the worker's process against its own connection, so
        the pool never observes that release at all and would park every interactive slot as though a batch
        run had just ended — closed, per spec §7, when the Editor is in fact still open.
        """
        row = self.connection.execute(
            "SELECT * FROM reservations WHERE resource=? ORDER BY sequence DESC LIMIT 1",
            (slot_id,)).fetchone()
        return self._reservation_view(row) if row else None

    def reservations_to_settle(self):
        """A slot is only useful to a running worker: anything else is the pool's to probe and release.

        A slot in 'held' is excluded. hold() leaves the reservation 'active' and only changes the slot, so
        without this clause the same reservation would come back on every two-second pool tick: the pool
        would re-probe a slot the operator has been told to recover, write an audit row each time, and — if a
        probe happened to pass later — release and park a slot spec §7 says must wait for recover-slot.
        """
        rows = self.connection.execute(
            """SELECT r.* FROM reservations r JOIN work_items w ON w.id=r.item_id
               LEFT JOIN slots s ON s.slot_id=r.resource
               WHERE r.state IN ('active','cancel_requested')
                 AND COALESCE(s.state,'') <> 'held'
                 AND (r.state='cancel_requested' OR w.state NOT IN ('queued','running','awaiting_resource'))
               ORDER BY r.sequence""")
        return [self._reservation_view(row) for row in rows]

    def _reservation_owned(self, reservation_id, token):
        row = self.connection.execute("SELECT * FROM reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
        if row is None:
            raise LedgerError(f"unknown reservation: {reservation_id}")
        presented = _hash_token(token) if isinstance(token, str) and token.isascii() and token else ""
        if not presented or not secrets.compare_digest(row["token_hash"] or "", presented):
            raise LedgerError("reservation token required")
        return row

    def release(self, reservation_id, token, reason):
        """FarmQA's rule: cancel_requested resolves to cancelled, active to released.

        The slot is left 'switching', not free: the folder is the pool's to park or close before anyone else
        may take it, and only the pool knows what commit it ends up on.
        """
        _text(reason, "reason")
        with self._transaction():
            row = self._reservation_owned(reservation_id, token)
            if row["state"] in ("released", "cancelled"):
                return row["state"]
            state = "cancelled" if row["state"] == "cancel_requested" else "released"
            self.connection.execute(
                "UPDATE reservations SET state=?,released_at=?,release_reason=? WHERE reservation_id=?",
                (state, self.clock(), reason[:500], reservation_id))
            if row["resource"]:
                self.connection.execute("UPDATE slots SET state='switching',updated_at=? WHERE slot_id=?",
                                        (self.clock(), row["resource"]))
            self._audit(row["item_id"], "reservation", state, details={"reservation_id": reservation_id,
                                                                      "reason": reason[:200]})
            return state

    def hold(self, reservation_id, reason):
        """A failing quiescence probe keeps the reservation open and takes the slot out of the pool (spec §7)."""
        _text(reason, "reason")
        with self._transaction():
            row = self.connection.execute("SELECT * FROM reservations WHERE reservation_id=?",
                                          (reservation_id,)).fetchone()
            if row is None or row["state"] not in ("active", "cancel_requested"):
                raise LedgerError("only an active reservation can be held")
            self.connection.execute("UPDATE slots SET state='held',updated_at=? WHERE slot_id=?",
                                    (self.clock(), row["resource"]))
            self._audit(row["item_id"], "reservation", "held", details={"reservation_id": reservation_id,
                                                                       "reason": reason[:200]})
            return self._reservation_view(row)

    def hold_owned(self, reservation_id, token, reason):
        """A worker's own `--outcome unclean`: the same hold, but the caller must prove it holds the slot.

        `hold` itself stays token-free on purpose and this is a wrapper rather than a parameter on it,
        because SlotPool.settle holds *precisely when the token file is gone* and so can never present one;
        requiring a token there would break the backstop spec §7 relies on. A worker is the opposite case:
        it is sandboxed, the pool wrote it the token for this reason, and taking the host's only slot out of
        the pool until an operator runs `recover-slot` is the most consequential thing it can do. The
        `quiescent` path is authenticated by `release`, and leaving the worse outcome open to any non-empty
        string — including the claim token `release` correctly refuses — had the asymmetry backwards.
        """
        self._reservation_owned(reservation_id, token)
        return self.hold(reservation_id, reason)

    def requeue_reservation(self, reservation_id, reason):
        """One more chance at the tail of the queue after a retryable failure, never a third.

        A new row rather than a reset of the old one: the queue is FIFO by `sequence`, so re-opening the
        original would put a failed attempt back at the *head*, ahead of everything that arrived while it
        was failing. `attempts` carries over incremented, which is what the pool reads to decide there is no
        second retry.
        """
        _text(reason, "reason")
        with self._transaction():
            row = self.connection.execute("SELECT * FROM reservations WHERE reservation_id=?",
                                          (reservation_id,)).fetchone()
            if row is None or row["state"] not in ("released", "cancelled"):
                raise LedgerError("only a closed reservation can be re-queued")
            fresh = str(uuid4())
            self.connection.execute(
                """INSERT INTO reservations(reservation_id,item_id,generation,kind,mode,commit_sha,state,
                   attempts,created_at) VALUES(?,?,?,?,?,?,'queued',?,?)""",
                (fresh, row["item_id"], row["generation"], row["kind"], row["mode"], row["commit_sha"],
                 row["attempts"] + 1, self.clock()))
            self._audit(row["item_id"], "reservation", "requeued", details={"reservation_id": fresh,
                                                                            "reason": reason[:200]})
            return fresh

    def recover_slot(self, slot_id, reason):
        """The operator's way out of held: force-release whatever holds the slot and return it to the pool.

        parked_commit is cleared, not kept. Whatever the slot was doing when it was held, the ledger no longer
        knows what commit the folder is on, and park_idle will not correct it because the state is no longer
        'switching'. A NULL says "unknown", and the pool's next grant does a full switch rather than trusting
        a stale value.
        """
        _text(reason, "reason")
        with self._transaction():
            row = self._slot_row(slot_id)
            self.connection.execute(
                """UPDATE reservations SET state=CASE WHEN state='cancel_requested' THEN 'cancelled' ELSE 'released' END,
                   released_at=?,release_reason=? WHERE resource=? AND state IN ('active','cancel_requested')""",
                (self.clock(), f"recover-slot: {reason}"[:500], slot_id))
            self.connection.execute(
                "UPDATE slots SET state='idle_closed',parked_commit=NULL,updated_at=? WHERE slot_id=?",
                (self.clock(), slot_id))
            self._audit(slot_id, "slot", "recovered", details={"reason": reason[:200]})
            return self._slot_view(self._slot_row(row["slot_id"]))

    def cancel_reservations(self, item_id, reason):
        """Stop: queued requests die, an active one keeps the slot and is marked for the probe (spec §7)."""
        _text(reason, "reason")
        with self._transaction():
            self.connection.execute(
                """UPDATE reservations
                   SET state=CASE WHEN state='queued' THEN 'cancelled' ELSE 'cancel_requested' END,
                       released_at=CASE WHEN state='queued' THEN ? ELSE released_at END,
                       release_reason=CASE WHEN state='queued' THEN ? ELSE release_reason END
                   WHERE item_id=? AND state IN ('queued','active')""",
                (self.clock(), reason[:500], item_id))
            self._audit(item_id, "reservation", "cancel requested", details={"reason": reason[:200]})
        return [r for r in self.reservations() if r["item_id"] == item_id]

    def record_identity(self, item_id, reservation_id, slot_id, result):
        observation_id = str(uuid4())
        with self._transaction():
            self.connection.execute(
                """INSERT INTO identity_observations(observation_id,item_id,reservation_id,slot_id,created_at,result_json)
                   VALUES(?,?,?,?,?,?)""",
                (observation_id, item_id, reservation_id, slot_id, self.clock(), _json(result)))
            self._audit(item_id, "identity", str(result.get("aggregate", "unknown")))
        return observation_id

    def identity_observations(self, item_id):
        rows = self.connection.execute(
            "SELECT * FROM identity_observations WHERE item_id=? ORDER BY created_at", (item_id,))
        return [{"observation_id": r["observation_id"], "reservation_id": r["reservation_id"],
                 "slot_id": r["slot_id"], "created_at": r["created_at"],
                 "result": json.loads(r["result_json"])} for r in rows]

    def resume(self, item_id, reason):
        _text(reason, "reason")
        with self._transaction():
            row = self._row(item_id)
            if row["state"] not in ("awaiting_input", "awaiting_resource"):
                raise LedgerError("only a waiting work item can be resumed")
            self._set_state(row["id"], "queued", reason, needs_resource=None)
            return self._view(self._row(row["id"]))

    def cancel(self, item_id, reason):
        _text(reason, "reason")
        with self._transaction():
            row = self._row(item_id)
            if row["state"] not in ACTIVE_STATES:
                raise LedgerError("work item is already terminal")
            # The operator CLI path must not orphan a slot: a queued request dies with the item, an active one
            # keeps the slot until the pool has probed and released it.
            self.connection.execute(
                """UPDATE reservations
                   SET state=CASE WHEN state='queued' THEN 'cancelled' ELSE 'cancel_requested' END,
                       released_at=CASE WHEN state='queued' THEN ? ELSE released_at END,
                       release_reason=CASE WHEN state='queued' THEN ? ELSE release_reason END
                   WHERE item_id=? AND state IN ('queued','active')""",
                (self.clock(), reason[:500], row["id"]))
            self._set_state(row["id"], "cancelled", reason, token=None, lease_expires_at=None, worker_pid=None,
                            needs_resource=None)
            return self._view(self._row(row["id"]))

    def fail(self, item_id, reason):
        _text(reason, "reason")
        with self._transaction():
            row = self._row(item_id)
            if row["state"] != "running":
                raise LedgerError("only a running work item can fail")
            self._set_state(row["id"], "failed", reason, token=None, lease_expires_at=None, worker_pid=None)
            return self._view(self._row(row["id"]))

    def fail_queued(self, item_id, reason):
        """Fail an item no worker owns. 'awaiting_resource' is accepted beside 'queued' because a switch
        that fails never reaches resume(): the item is still waiting for the slot it will not get."""
        _text(reason, "reason")
        with self._transaction():
            row = self._row(item_id)
            if row["state"] not in ("queued", "awaiting_resource"):
                raise LedgerError("only a queued or waiting work item can fail before claim")
            self._set_state(row["id"], "failed", reason, worker_pid=None)
            return self._view(self._row(row["id"]))

    def recover(self, item_id, reason):
        _text(reason, "reason")
        with self._transaction():
            row = self._row(item_id)
            if row["state"] != "running":
                raise LedgerError("only expired running work can be recovered")
            if row["lease_expires_at"] > self.clock():
                raise LedgerError("lease is still valid; recovery cannot transfer live ownership")
            self._set_state(row["id"], "queued", reason, token=None, lease_expires_at=None, worker_pid=None, resume_authorized=1)
            return self._view(self._row(row["id"]))

    def retry(self, item_id, reason):
        _text(reason, "reason")
        with self._transaction():
            row = self._row(item_id)
            if row["state"] not in ("blocked", "delivered", "cancelled", "failed"):
                raise LedgerError("retry requires a terminal work item")
            if not _in_scope(json.loads(self._issue_row(row["issue_id"])["metadata"])):
                raise LedgerError("issue is archived or in a terminal status")
            try:
                self._set_state(row["id"], "queued", reason, worker_pid=None, generation=row["generation"] + 1,
                                requeue_requested=0)
            except sqlite3.IntegrityError:
                raise LedgerError("another active work item exists for this issue")
            return self._view(self._row(row["id"]))

    def _resumable_work(self, issue_id, session_id):
        return self.connection.execute("""SELECT w.* FROM work_items w JOIN sessions s
            ON s.session_id=w.session_id WHERE w.issue_id=? AND w.skill='fix' AND s.delegation=1
            AND w.state IN ('blocked','delivered','cancelled','failed')
            ORDER BY (w.session_id=?) DESC,w.created_at DESC,w.rowid DESC LIMIT 1""",
            (issue_id, session_id)).fetchone()

    def resume_work(self, item_id, token, message_id, app_user_id):
        """Atomically hand a chat's natural-language request to its issue's prior fix.

        Intent is interpreted by the chat worker. The host checks fresh delegation;
        this transaction enforces item ownership, provenance and one active worker.
        """
        with self._transaction():
            chat = self._owned(item_id, token)
            if chat["skill"] != "chat":
                raise LedgerError("resume-work requires an owned chat item")
            issue = json.loads(self._issue_row(chat["issue_id"])["metadata"])
            if not _in_scope(issue) or not app_user_id or issue.get("delegate_id") != app_user_id:
                raise LedgerError("issue must remain open and delegated to FarmBot")
            messages = self.connection.execute("SELECT id,body FROM inbox WHERE item_id=? ORDER BY id",
                                               (item_id,)).fetchall()
            if message_id not in {m["id"] for m in messages}:
                raise LedgerError("resume-work requires a real message from this chat")
            work = self._resumable_work(chat["issue_id"], chat["session_id"])
            if work is None:
                raise LedgerError("no previously delegated fix work on this issue")
            self._set_state(item_id, "delivered", "handed request to previously delegated work",
                            token=None, lease_expires_at=None, worker_pid=None,
                            evidence=_json({"summary": "Resumed previously delegated work", "prs": [],
                                            "resumed_item": work["id"], "message_id": message_id}))
            self._set_state(work["id"], "queued", "human requested continuation via chat",
                            token=None, lease_expires_at=None, worker_pid=None,
                            generation=work["generation"] + 1, requeue_requested=0)
            self.connection.executemany("INSERT INTO inbox(item_id,body,created_at) VALUES(?,?,?)",
                                        [(work["id"], m["body"], self.clock()) for m in messages])
            self._audit(work["id"], "resume_request", details={"chat_item": item_id, "message_id": message_id})
            return self._view(self._row(work["id"]))

    def prepare_comment(self, item_id, token, kind, body):
        """Claim the one outbox row for this issue, claimed input, generation and kind, and say plainly
        whether it is this item's own or one another item already posted.

        The key excludes the item id on purpose: an unchanged issue must not collect two identical blocker
        comments. An operator `enqueue` on an issue that already carried a work item makes a second item
        land on the same key, and the old code returned the other item's row as though it were this one's,
        which was a silent stall (2026-09-20 rehearsal, Finding 2). Three cases now:

        - the row is this item's, or there is none: as before, idempotent in the item's own body.
        - the row exists, belongs to another item and was never posted: nothing is on the issue, so it is
          handed to the live item with its own wording. Safe because `one_active_item_per_issue` means the
          other item is necessarily terminal; the guard re-states that rather than trusting it.
        - the row exists, belongs to another item and was confirmed: the comment is genuinely on the issue,
          so this item borrows it and posts nothing. `kind` is only 'started', 'blocker' or 'delivery', so
          a second item can reach the same kind with materially different words; those words must not
          reach the issue twice, but losing them is what Finding 1 was about, so a diverging body is kept
          in `audit`.
        """
        if kind not in ("started", "blocker", "delivery"):
            raise LedgerError("comment kind must be started, blocker or delivery")
        _text(body, "comment body")
        with self._transaction():
            row = self._owned(item_id, token)
            if not _in_scope(json.loads(self._issue_row(row["issue_id"])["metadata"])):
                raise LedgerError("issue left scope; do not post a new comment")
            key = f"{row['issue_id']}:{row['claimed_fingerprint']}:{row['generation']}:{kind}"
            action_id = hashlib.sha256(key.encode("utf-8")).hexdigest()
            marker = f"[farmbot:{action_id}]"
            clean_body = MARKER.sub("", body).rstrip()
            _text(clean_body, "comment body")
            existing = self.connection.execute("SELECT * FROM outbox WHERE action_id=?", (action_id,)).fetchone()
            deduplicated = False
            if existing is None:
                self.connection.execute("""INSERT INTO outbox(action_id,item_id,issue_id,fingerprint,generation,kind,marker,body,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?)""", (action_id, row["id"], row["issue_id"], row["claimed_fingerprint"],
                                                   row["generation"], kind, marker, f"{clean_body}\n\n{marker}", self.clock()))
                self._audit(row["id"], "prepare_comment", kind, {"action_id": action_id})
            elif existing["item_id"] != row["id"]:
                owner = self.connection.execute("SELECT state FROM work_items WHERE id=?",
                                                (existing["item_id"],)).fetchone()
                if existing["remote_id"] is None and (owner is None or owner["state"] not in ACTIVE_STATES):
                    self.connection.execute("UPDATE outbox SET item_id=?,body=?,created_at=? WHERE action_id=?",
                                            (row["id"], f"{clean_body}\n\n{marker}", self.clock(), action_id))
                    self._audit(row["id"], "prepare_comment", kind,
                                {"action_id": action_id, "taken_over_from": existing["item_id"]})
                else:
                    deduplicated = True
                    details = {"action_id": action_id, "prepared_by": existing["item_id"]}
                    if kind != "started" and clean_body != MARKER.sub("", existing["body"]).rstrip():
                        # The conclusion this item reached, in its own words. It is not posted — the issue
                        # already carries a comment of this kind at this input — but an operator asking
                        # what the second worker actually concluded must not be told nothing. A 'started'
                        # marker states no conclusion, so its wording is not worth keeping.
                        details["suppressed_body"] = clean_body[:2000]
                    # Its own audit kind, not a reason on `prepare_comment`: borrowed_comments() reads this
                    # back for the operator, and a reason string is not something to build a reader on.
                    self._audit(row["id"], "deduplicated", kind, details)
            return {**dict(self.connection.execute("SELECT * FROM outbox WHERE action_id=?", (action_id,)).fetchone()),
                    "deduplicated": deduplicated}

    def confirm_comment(self, action_id, remote_id):
        _text(action_id, "action_id")
        _text(remote_id, "remote_id")
        with self._transaction():
            action = self.connection.execute("SELECT * FROM outbox WHERE action_id=?", (action_id,)).fetchone()
            if action is None:
                raise LedgerError("unknown comment action")
            if action["remote_id"] is not None and action["remote_id"] != remote_id:
                raise LedgerError("comment action already confirmed with a different remote id")
            if action["remote_id"] is None:
                self.connection.execute("UPDATE outbox SET remote_id=?,confirmed_at=? WHERE action_id=?", (remote_id, self.clock(), action_id))
                self._audit(action["item_id"], "confirm_comment", action["kind"], {"action_id": action_id, "remote_id": remote_id})
            return dict(self.connection.execute("SELECT * FROM outbox WHERE action_id=?", (action_id,)).fetchone())

    def outbox(self, item_id):
        return [dict(r) for r in self.connection.execute("SELECT * FROM outbox WHERE item_id=? ORDER BY created_at,action_id", (item_id,))]

    def finish(self, item_id, token, outcome, evidence):
        if outcome not in ("blocked", "delivered"):
            raise LedgerError("outcome must be blocked or delivered")
        if not isinstance(evidence, dict):
            raise LedgerError("finish input must be an object")
        _text(evidence.get("summary"), "summary")
        with self._transaction():
            row = self._owned(item_id, token)
            chat = row["skill"] == "chat"
            if chat and outcome == "delivered":
                _text(evidence.get("verification"), "verification")
                if evidence.get("comment_action_id") is not None or evidence.get("prs"):
                    raise LedgerError("chat deliveries carry no issue comment and no PR")
            elif chat and evidence.get("comment_action_id") is None:
                pass  # a chat worker explains its blocker in the session; the issue gets no comment
            else:
                _text(evidence.get("comment_action_id"), "comment_action_id")
                if outcome == "delivered":
                    _text(evidence.get("verification"), "verification")
                    prs = evidence.get("prs") or []
                    if not isinstance(prs, list):
                        raise LedgerError("prs must be an array")
                    no_change = evidence.get("no_change")
                    if no_change is not None:
                        # The investigation is the deliverable: already fixed, a duplicate, does not reproduce.
                        _text(no_change, "no_change")
                        if prs:
                            raise LedgerError("a no_change delivery carries no PR")
                    elif not prs:
                        raise LedgerError("delivered finish requires a nonempty prs array or a no_change reason")
                    for pr in prs:
                        _text(pr, "PR URL")
                        if not self.PR_URL.fullmatch(pr):
                            raise LedgerError("each PR URL must be a canonical HTTPS pull request URL")
                action = self.connection.execute("SELECT * FROM outbox WHERE action_id=?", (evidence["comment_action_id"],)).fetchone()
                kind = "blocker" if outcome == "blocked" else "delivery"
                # Scoped by the issue rather than by the item. What an outbox row asserts is "the comment
                # for this issue, at this claimed input and generation and of this kind, exists and was
                # posted", and that is exactly what a terminal state needs to be true. A second item on an
                # unchanged issue is handed that row by prepare_comment and has no way to obtain one of its
                # own, so requiring item_id here is what left it with no path to a terminal state at all.
                # `issue_id` replaces it rather than simply going: a fingerprint is hashed from title,
                # description, attachments and comments and never from the issue id, so two issues can
                # share one, and without this clause a comment posted on one issue could close a work item
                # on another.
                if (action is None or action["issue_id"] != row["issue_id"]
                        or action["fingerprint"] != row["claimed_fingerprint"]
                        or action["generation"] != row["generation"] or action["kind"] != kind or not action["remote_id"]):
                    raise LedgerError("finish requires a confirmed comment for this issue, claimed input and outcome")
                if action["item_id"] != row["id"]:
                    # The borrowed comment is the only account of this work a human ever reads, and unlike
                    # a blocker's wording a PR URL is a fact that comment either states or does not. A
                    # delivery citing a pull request it does not name would put work in the ledger that was
                    # announced nowhere. A no_change delivery carries no `prs`, so it borrows freely, and
                    # an item that really has something unannounced to say still has its own blocker key.
                    for pr in (evidence.get("prs") or []):
                        if not re.search(re.escape(pr) + r"(?![0-9])", action["body"]):
                            raise LedgerError(f"the posted comment does not mention {pr}; a borrowed "
                                              "delivery may only claim pull requests it announced")
                    self._audit(row["id"], "borrowed_comment", kind,
                                {"action_id": action["action_id"], "prepared_by": action["item_id"]})
            current = self._issue_row(row["issue_id"])["fingerprint"]
            changed = current != row["claimed_fingerprint"] or row["requeue_requested"]
            state = "queued" if changed else outcome
            self._set_state(row["id"], state, evidence["summary"], token=None, lease_expires_at=None, worker_pid=None,
                            evidence=_json(evidence), generation=row["generation"] + int(state == "queued"))
            return self._view(self._row(row["id"]))

    def push_inbox(self, item_id, body, *, resume_waiting=False):
        _text(body, "body")
        with self._transaction():
            row = self._row(item_id)
            # A receiver may have selected the chat just before resume_work handed it
            # off. Follow the recorded handoff under this same write transaction.
            if row["state"] == "delivered" and row["skill"] == "chat":
                resumed = json.loads(row["evidence"]).get("resumed_item")
                if resumed:
                    target = self._row(resumed)
                    if target["issue_id"] == row["issue_id"]:
                        row = target
            if row["state"] not in ACTIVE_STATES:
                raise LedgerError("cannot steer a terminal work item")
            self.connection.execute("INSERT INTO inbox(item_id,body,created_at) VALUES(?,?,?)", (row["id"], body, self.clock()))
            self._audit(row["id"], "inbox", "steering message")
            if resume_waiting and row["state"] == "awaiting_input":
                self._set_state(row["id"], "queued", "human answered in Linear", token=None,
                                lease_expires_at=None, worker_pid=None, resume_authorized=1)
            return {"item_id": row["id"], "state": self._row(row["id"])["state"], "pending": self.connection.execute(
                "SELECT count(*) FROM inbox WHERE item_id=? AND consumed_at IS NULL", (row["id"],)).fetchone()[0]}

    def pop_inbox(self, item_id, token):
        with self._transaction():
            row = self._owned(item_id, token)
            rows = self.connection.execute("SELECT id,body FROM inbox WHERE item_id=? AND consumed_at IS NULL ORDER BY id", (row["id"],)).fetchall()
            self.connection.execute("UPDATE inbox SET consumed_at=? WHERE item_id=? AND consumed_at IS NULL", (self.clock(), row["id"]))
            return [r["body"] for r in rows]

    def issue_context(self, item_id):
        """Everything one worker needs about its own item; no token, no other items."""
        row = self._row(item_id)
        issue_row = self._issue_row(row["issue_id"])
        checkpoint = json.loads(row["checkpoint"])
        meta = checkpoint.get("handoff_meta")
        handoff = None
        if "handoff" in checkpoint and meta:
            handoff = {"content": checkpoint["handoff"], "recorded_at": meta["recorded_at"],
                       "stale": meta["fingerprint"] != issue_row["fingerprint"] or meta["generation"] != row["generation"]
                                or bool(row["requeue_requested"]),
                       "revalidation_required": True, "source": "previous_worker_checkpoint"}
        view = self._view(row)
        coordination = {key: view[key] for key in ("id", "identifier", "skill", "state", "stage", "generation", "target")}
        return {"issue": json.loads(issue_row["metadata"]), "coordination": coordination, "handoff": handoff,
                "resumable_work": (self._view(candidate) if row["skill"] == "chat"
                                   and (candidate := self._resumable_work(row["issue_id"], row["session_id"])) else None),
                "session_messages": [dict(r) for r in self.connection.execute(
                    "SELECT id,body FROM inbox WHERE item_id=? ORDER BY id", (item_id,))],
                "pending_question": checkpoint.get("pending_question"),
                "published_prs": [r["url"] for r in self.connection.execute(
                    "SELECT url FROM published_prs WHERE issue_id=? ORDER BY url", (row["issue_id"],))],
                "inbox_pending": self.connection.execute(
                    "SELECT count(*) FROM inbox WHERE item_id=? AND consumed_at IS NULL", (row["id"],)).fetchone()[0]}

    # Memory is shared recall data. These operations never widen work-item authority.
    def memory_rows(self):
        """Trusted scheduler read: one statement gives a coherent full snapshot."""
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM memories WHERE deleted=0 ORDER BY scope,title,id")]

    def _memory_read(self, note_id, *, include_deleted=False):
        try:
            memory.note_id(note_id)
        except ValueError as exc:
            raise LedgerError(str(exc)) from exc
        row = self.connection.execute("SELECT * FROM memories WHERE id=?", (note_id,)).fetchone()
        if row is None or (row["deleted"] and not include_deleted):
            raise LedgerError("unknown or forgotten memory")
        return dict(row)

    def _memory_list(self):
        return [{k: v for k, v in row.items() if k not in ("body", "source", "build_commit", "deleted")}
                for row in self.memory_rows()]

    def memory_list(self, item_id, token):
        with self._transaction():
            self._owned(item_id, token)
            return self._memory_list()

    def memory_read(self, item_id, token, note_id):
        with self._transaction():
            self._owned(item_id, token)
            return self._memory_read(note_id)

    def memory_save(self, item_id, token, value):
        with self._transaction():
            self._owned(item_id, token)
            return self._memory_save(value, item_id)

    def memory_forget(self, item_id, token, note_id, expected_revision, reason):
        with self._transaction():
            self._owned(item_id, token)
            return self._memory_forget(note_id, expected_revision, reason, item_id)

    def memory_admin(self, action, *, value=None, note_id=None, expected_revision=None, reason=""):
        """Trusted-host convention, like retry/cancel; not a boundary against direct DB access."""
        with self._transaction():
            if action == "list":
                return self._memory_list()
            if action == "read":
                return self._memory_read(note_id)
            if action == "save":
                return self._memory_save(value, None)
            if action == "forget":
                return self._memory_forget(note_id, expected_revision, reason, None)
            raise LedgerError("unknown memory administration action")

    def _memory_save(self, value, item_id):
        try:
            value = memory.validate_note(value)
        except ValueError as exc:
            raise LedgerError(str(exc)) from exc
        actor = item_id or "operator"
        actor_kind = "worker" if item_id else "operator"
        now = self.clock()
        if "id" in value:
            old = self._memory_read(value["id"])
            if old["revision"] != value["expected_revision"]:
                raise LedgerError(f"memory revision conflict: current revision is {old['revision']}")
            self.connection.execute("""UPDATE memories SET title=?,category=?,scope=?,body=?,source=?,
                build_commit=?,updated_by_item=?,actor_kind=?,updated_at=?,revision=revision+1 WHERE id=?""",
                tuple(value[k] for k in ("title", "category", "scope", "body", "source", "build_commit")) +
                (item_id, actor_kind, now, old["id"]))
            note_id, action = old["id"], "update"
        else:
            payload_hash = hashlib.sha256(_json({k: v for k, v in value.items() if k != "request_id"}).encode()).hexdigest()
            previous = self.connection.execute("SELECT * FROM memory_requests WHERE actor=? AND request_id=?",
                                               (actor, value["request_id"])).fetchone()
            if previous:
                if previous["payload_hash"] != payload_hash:
                    raise LedgerError("memory request_id reused with different content")
                current = self._memory_read(previous["note_id"], include_deleted=True)
                if current["deleted"]:
                    return {"id": current["id"], "revision": current["revision"], "deleted": True, "replayed": True}
                return {**current, "replayed": True}
            if self.connection.execute("SELECT count(*) FROM memories WHERE deleted=0").fetchone()[0] >= memory.MAX_NOTES:
                raise LedgerError("memory capacity reached; consolidate or forget an entry first")
            note_id, action = str(uuid4()), "create"
            self.connection.execute("""INSERT INTO memories
                (id,title,category,scope,body,source,build_commit,created_by_item,updated_by_item,
                 actor_kind,created_at,updated_at,revision) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                (note_id,) + tuple(value[k] for k in ("title", "category", "scope", "body", "source", "build_commit")) +
                (item_id, item_id, actor_kind, now, now))
            self.connection.execute("INSERT INTO memory_requests VALUES(?,?,?,?)",
                                    (actor, value["request_id"], payload_hash, note_id))
        result = self._memory_read(note_id)
        self._audit(actor, "memory_" + action, details={"id": note_id, "revision": result["revision"], "actor": actor_kind})
        return {**result, "replayed": False}

    def _memory_forget(self, note_id, expected_revision, reason, item_id):
        try:
            memory.revision(expected_revision)
            memory.text(reason, "forget reason", limit=500)
        except ValueError as exc:
            raise LedgerError(str(exc)) from exc
        old = self._memory_read(note_id)
        if old["revision"] != expected_revision:
            raise LedgerError(f"memory revision conflict: current revision is {old['revision']}")
        self.connection.execute("""UPDATE memories SET title='',body='',source='',build_commit=NULL,
            deleted=1,revision=revision+1,updated_by_item=?,actor_kind=?,updated_at=? WHERE id=?""",
            (item_id, "worker" if item_id else "operator", self.clock(), note_id))
        self._audit(item_id or "operator", "memory_forget", reason,
                    {"id": note_id, "revision": old["revision"] + 1, "actor": "worker" if item_id else "operator"})
        return {"id": note_id, "revision": old["revision"] + 1, "deleted": True}
