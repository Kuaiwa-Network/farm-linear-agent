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
from urllib.parse import urlsplit
from uuid import UUID, uuid4

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

    def __init__(self, path, *, clock=time.time, lease_seconds=2700):
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, (int, float)) or not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise LedgerError("lease_seconds must be a positive finite number")
        self.clock = clock
        self.lease_seconds = lease_seconds
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=10, isolation_level=None)
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
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY,
                    item_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    details TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
            """)
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

    def _view(self, row, *, token=False):
        issue = json.loads(self._issue_row(row["issue_id"])["metadata"])
        result = {key: row[key] for key in ["id", "issue_id", "session_id", "skill", "state", "stage",
                                            "priority", "host", "generation", "lease_expires_at",
                                            "worker_pid", "needs_resource", "created_at"]}
        result.update(identifier=issue["identifier"], target=json.loads(row["target_json"]) if row["target_json"] else None,
                      resume_authorized=bool(row["resume_authorized"]), checkpoint=json.loads(row["checkpoint"]),
                      evidence=json.loads(row["evidence"]))
        if token:
            result["token"] = row["token"]
        return result

    def _audit(self, item_id, kind, reason="", details=None):
        self.connection.execute("INSERT INTO audit(item_id,kind,reason,details,created_at) VALUES(?,?,?,?,?)",
                                (item_id, kind, reason, _json(details or {}), self.clock()))

    def _owned(self, item_id, token):
        row = self._row(item_id)
        if (row["state"] != "running" or not isinstance(token, str) or not token.isascii()
                or not secrets.compare_digest(row["token"] or "", token)):
            raise LedgerError("running claim and matching token required")
        if row["lease_expires_at"] <= self.clock():
            raise LedgerError("lease expired; the launcher recovers expired work, workers must stop")
        return row

    def observe_issue(self, raw):
        """Store one complete issue snapshot; requeue blocked items on material change."""
        issue = _normalize(raw)
        with self._transaction():
            own_bodies = {r["body"] for r in self.connection.execute("SELECT body FROM outbox WHERE issue_id=?", (issue["id"],))}
            own_prs = {r["url"] for r in self.connection.execute("SELECT url FROM published_prs WHERE issue_id=?", (issue["id"],))}
            fingerprint = _fingerprint(issue, own_bodies, own_prs)
            self.connection.execute("""INSERT INTO issues(id,metadata,fingerprint,observed_at) VALUES(?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET metadata=excluded.metadata,fingerprint=excluded.fingerprint,observed_at=excluded.observed_at""",
                                    (issue["id"], _json(issue), fingerprint, self.clock()))
            for row in self.connection.execute("SELECT * FROM work_items WHERE issue_id=? AND state='blocked'", (issue["id"],)):
                if fingerprint != row["claimed_fingerprint"] and _in_scope(issue):
                    self.connection.execute("UPDATE work_items SET state='queued',generation=generation+1,updated_at=? WHERE id=?",
                                            (self.clock(), row["id"]))
                    self._audit(row["id"], "requeue", "material change while blocked")
        return {"id": issue["id"], "identifier": issue["identifier"], "fingerprint": fingerprint,
                "in_scope": _in_scope(issue)}

    def issue(self, issue_id):
        return json.loads(self._issue_row(issue_id)["metadata"])

    def ensure_session(self, session_id, issue_id, delegation):
        _text(session_id, "session_id")
        with self._transaction():
            self.connection.execute("""INSERT OR IGNORE INTO sessions(session_id,issue_id,delegation,created_at)
                VALUES(?,?,?,?)""", (session_id, issue_id, int(bool(delegation)), self.clock()))
        return self.session(session_id)

    def session(self, session_id):
        row = self.connection.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if row is None:
            return None
        return {"session_id": row["session_id"], "issue_id": row["issue_id"], "delegation": bool(row["delegation"]),
                "target": json.loads(row["target_json"]) if row["target_json"] else None}

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
            self._set_state(item_id, "running", "claim", token=token, lease_expires_at=self.clock() + self.lease_seconds,
                            claimed_fingerprint=issue_row["fingerprint"], generation=generation, requeue_requested=0,
                            resume_authorized=0, checkpoint=_json(checkpoint))
            return self._view(self._row(item_id), token=True)

    def renew(self, item_id, token):
        with self._transaction():
            row = self._owned(item_id, token)
            self.connection.execute("UPDATE work_items SET lease_expires_at=?,updated_at=? WHERE id=?",
                                    (self.clock() + self.lease_seconds, self.clock(), row["id"]))
            self._audit(row["id"], "renew")
            return self._view(self._row(row["id"]))

    def set_worker(self, item_id, pid, host):
        if type(pid) is not int or pid <= 0:
            raise LedgerError("pid must be a positive integer")
        with self._transaction():
            row = self._row(item_id)
            if row["state"] not in ("queued", "running"):
                raise LedgerError("worker can only be recorded for queued or running items")
            self.connection.execute("UPDATE work_items SET worker_pid=?,host=?,updated_at=? WHERE id=?",
                                    (pid, host, self.clock(), row["id"]))
            self._audit(row["id"], "worker", f"pid {pid} on {host}")
            return self._view(self._row(row["id"]))

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
            self._set_state(row["id"], "awaiting_input", "human gate", token=None, lease_expires_at=None,
                            worker_pid=None, checkpoint=_json(checkpoint))
            return self._view(self._row(row["id"]))

    def await_resource(self, item_id, token, resource, mode):
        _text(resource, "resource")
        _text(mode, "mode")
        with self._transaction():
            row = self._owned(item_id, token)
            self._set_state(row["id"], "awaiting_resource", f"needs {resource}:{mode}", token=None,
                            lease_expires_at=None, worker_pid=None, needs_resource=f"{resource}:{mode}")
            return self._view(self._row(row["id"]))

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
            self._set_state(row["id"], "cancelled", reason, token=None, lease_expires_at=None, needs_resource=None)
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
        _text(reason, "reason")
        with self._transaction():
            row = self._row(item_id)
            if row["state"] != "queued":
                raise LedgerError("only a queued work item can fail before claim")
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
                self._set_state(row["id"], "queued", reason, generation=row["generation"] + 1, requeue_requested=0)
            except sqlite3.IntegrityError:
                raise LedgerError("another active work item exists for this issue")
            return self._view(self._row(row["id"]))

    def prepare_comment(self, item_id, token, kind, body):
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
            if self.connection.execute("SELECT 1 FROM outbox WHERE action_id=?", (action_id,)).fetchone() is None:
                self.connection.execute("""INSERT INTO outbox(action_id,item_id,issue_id,fingerprint,generation,kind,marker,body,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?)""", (action_id, row["id"], row["issue_id"], row["claimed_fingerprint"],
                                                   row["generation"], kind, marker, f"{clean_body}\n\n{marker}", self.clock()))
                self._audit(row["id"], "prepare_comment", kind, {"action_id": action_id})
            return dict(self.connection.execute("SELECT * FROM outbox WHERE action_id=?", (action_id,)).fetchone())

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
            chat_delivery = row["skill"] == "chat" and outcome == "delivered"
            if chat_delivery:
                _text(evidence.get("verification"), "verification")
                if evidence.get("comment_action_id") is not None or evidence.get("prs"):
                    raise LedgerError("chat deliveries carry no issue comment and no PR")
            else:
                _text(evidence.get("comment_action_id"), "comment_action_id")
                if outcome == "delivered":
                    _text(evidence.get("verification"), "verification")
                    prs = evidence.get("prs")
                    if not isinstance(prs, list) or not prs:
                        raise LedgerError("delivered finish requires a nonempty prs array")
                    for pr in prs:
                        _text(pr, "PR URL")
                        parsed = urlsplit(pr)
                        if parsed.scheme != "https" or not parsed.netloc or not parsed.path.strip("/"):
                            raise LedgerError("each PR URL must be an https URL with a path")
                action = self.connection.execute("SELECT * FROM outbox WHERE action_id=?", (evidence["comment_action_id"],)).fetchone()
                kind = "blocker" if outcome == "blocked" else "delivery"
                if (action is None or action["item_id"] != row["id"] or action["fingerprint"] != row["claimed_fingerprint"]
                        or action["generation"] != row["generation"] or action["kind"] != kind or not action["remote_id"]):
                    raise LedgerError("finish requires a confirmed comment for this item, claimed input and outcome")
            current = self._issue_row(row["issue_id"])["fingerprint"]
            changed = current != row["claimed_fingerprint"] or row["requeue_requested"]
            state = "queued" if changed else outcome
            self._set_state(row["id"], state, evidence["summary"], token=None, lease_expires_at=None, worker_pid=None,
                            evidence=_json(evidence), generation=row["generation"] + int(state == "queued"))
            return self._view(self._row(row["id"]))

    def push_inbox(self, item_id, body):
        _text(body, "body")
        with self._transaction():
            row = self._row(item_id)
            if row["state"] not in ACTIVE_STATES:
                raise LedgerError("cannot steer a terminal work item")
            self.connection.execute("INSERT INTO inbox(item_id,body,created_at) VALUES(?,?,?)", (row["id"], body, self.clock()))
            self._audit(row["id"], "inbox", "steering message")
            return {"item_id": row["id"], "pending": self.connection.execute(
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
                "pending_question": checkpoint.get("pending_question"),
                "published_prs": [r["url"] for r in self.connection.execute(
                    "SELECT url FROM published_prs WHERE issue_id=? ORDER BY url", (row["issue_id"],))],
                "inbox_pending": self.connection.execute(
                    "SELECT count(*) FROM inbox WHERE item_id=? AND consumed_at IS NULL", (row["id"],)).fetchone()[0]}
