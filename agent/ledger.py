"""FarmBot ledger: SQLite work items, leases, handoffs, outbox and audit.

Connectors stay outside this module. confirm_comment records a caller's
remote-readback attestation; it cannot verify Linear itself.

"design" followed by an ID such as P3 or F14 cites the withdrawn-work design,
docs/superpowers/specs/2026-09-29-withdrawn-work-design.md.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
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
from .attention import QUESTION_KEY, question_from
from .publication import SUFFIXES, PublicationError, is_issue_branch, issue_branch, suffix_of
from .resource_recovery import RecoveryStore, SCHEMA as RECOVERY_SCHEMA
from .maintenance import Maintenance, SCHEMA as MAINTENANCE_SCHEMA
from .router import CONVERSATION_SKILLS, WRITE_SKILLS
from .stages import current_root
from .withdrawal import DEFAULT_GRACE_SECONDS, SILENT_GRACE_SECONDS
from .server_test_plan import FULL_PLAN, validate_plan
from .worktrees import SAFE_BRANCH

MARKER = re.compile(r"\[farmbot:[0-9a-f]{64}\]")
STATES = ("queued", "running", "awaiting_input", "awaiting_resource",
          "delivered", "blocked", "cancelled", "failed")
ACTIVE_STATES = ("queued", "running", "awaiting_input", "awaiting_resource")
# Linear's closed workflow-state types. Duplicate reports its own type, not "canceled".
TERMINAL_STATUS_TYPES = ("completed", "canceled", "duplicate")
# Notice kinds (spec §9.4). Later phases add theirs: `notices.kind` has no CHECK constraint, so a new kind needs no
# table rebuild, which is what the outbox's CHECK and UNIQUE key would demand. Phase B (P7) adds stage progress
# and a request to merge a named PR.
NOTICE_KINDS = ("question", "waiting", "foreign_work", "stage", "merge_request")
REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
# Why a pause waits (spec §5.2): a question needs an answer and adds needs-more-info; waiting is a human step elsewhere.
AWAIT_REASONS = ("question", "waiting")
# The skills that start at an initial root, whose jobs last days across stages and human gates: their
# automatic-retry allowances bound one stage, not the job (spec §5.8, D16). The ledger reads no manifests, so it
# knows them by name, as it knows fix, whose allowances last the job.
STAGE_ALLOWANCE_SKILLS = ("work", "feature", "fgui", "scene")
# What authorised an item, recorded once when it is created (withdrawn-work design P1): the card's delegation to this
# app (every write item, and a chat a delegation opened), a person's @mention or message on a card not delegated
# here, or the operator's `agent.service enqueue` of a chat. Only delegation-authority work is withdrawn when the
# delegation goes.
AUTHORITIES = ("delegation", "mention", "operator")
# How a delegation episode ends (silent-delegation design P11, P12): a delegation session followed (`heard`); the
# card's work was kept, taken over in its own thread, or its threads were told; no open thread was found (`unseen`);
# or the card was no longer delegated, or closed (`dropped`). Until then it is `waiting`.
EPISODE_SETTLED = ("heard", "served", "in_place", "told", "unseen", "dropped")


class LedgerError(ValueError):
    """Invalid input or a conflicting state transition; safe to show on stderr."""


class StaleRouting(LedgerError):
    """The item a caller routed to is no longer in the state it saw: route again (withdrawn-work design J5)."""


def _default_authority(skill):
    return "delegation" if skill in WRITE_SKILLS else "mention"


def _checked_authority(skill, authority):
    """The authority a new item of `skill` records: the given one, or the skill's default. A write item exists only
    under a delegation (spec §4), so it takes no other."""
    if authority is None:
        return _default_authority(skill)
    if authority not in AUTHORITIES:
        raise LedgerError(f"authority must be one of {', '.join(AUTHORITIES)}")
    if skill in WRITE_SKILLS and authority != "delegation":
        raise LedgerError(f"a {skill} item has delegation authority only")
    return authority


def _authority(row):
    """The item's recorded authority. A row written before authorities were recorded has none, and reads as its
    skill's default: a write item the delegation's, a chat a mention's, which no withdrawal touches (design K2)."""
    return row["authority"] or _default_authority(row["skill"])


def _checked_guards(authority, created_before):
    """A caller's recheck of the item it decided on: an authority of AUTHORITIES and a finite time, or None."""
    if authority is not None and authority not in AUTHORITIES:
        raise LedgerError(f"authority must be one of {', '.join(AUTHORITIES)}")
    if created_before is not None and (type(created_before) not in (int, float) or not math.isfinite(created_before)):
        raise LedgerError("created_before must be a finite number of seconds")


def _text(value, name, *, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise LedgerError(f"{name} must be {'a string' if empty else 'a nonempty string'}")
    return value


def _seconds(value, name):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise LedgerError(f"{name} must be a finite number of seconds")
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
FINGERPRINT = re.compile(r"[0-9a-f]{64}")
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


# A Linear web URL: a person's profile, which Linear renders as a mention, or a comment's own link. It is the rule
# of linear_api.LINEAR_URL, kept here because connectors stay outside this module.
LINEAR_URL = re.compile(r"https://linear\.app/\S+")


def require_listed_resource(skill, resource):
    """Resources follow the manifest (Phase B plan, P11): an item waits only for a kind its skill lists."""
    if resource not in skill.resources:
        raise LedgerError(f"{resource} is not a resource of {skill.name}; its manifest lists "
                          f"{', '.join(skill.resources) or 'none'}")

def _person(value, name):
    """None, or exactly {"id": UUID, "name": text, "url": Linear profile URL}: never an email or other field."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"id", "name", "url"}:
        raise LedgerError(f"{name} must be null or exactly id, name and url")
    if not isinstance(value["url"], str) or not LINEAR_URL.fullmatch(value["url"]):
        raise LedgerError(f"{name} url must be an https://linear.app/ profile URL")
    return {"id": _uuid(value["id"], f"{name} id"), "name": _text(value["name"], f"{name} name"), "url": value["url"]}


def _label_groups(value, labels):
    """Sorted, distinct {"group", "label"} pairs, each label one of the issue's labels and group its parent."""
    if not isinstance(value, list):
        raise LedgerError("label_groups must be an array")
    pairs = set()
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {"group", "label"}:
            raise LedgerError("each label group needs exactly group and label")
        pair = (_text(entry["group"], "label group"), _text(entry["label"], "grouped label"))
        if pair[1] not in labels:
            raise LedgerError("a grouped label must be one of the issue's labels")
        pairs.add(pair)
    return [{"group": group, "label": label} for group, label in sorted(pairs)]


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
    value["updated_at"] = _timestamp(raw["updated_at"], "updated_at") if raw.get("updated_at") else None
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
    # Optional keys. Rows stored before them, and older fetchers, lack them, which reads as unknown.
    for field in ("assignee", "creator"):
        if field in raw:
            value[field] = _person(raw[field], field)
    if "label_groups" in raw:
        value["label_groups"] = _label_groups(raw["label_groups"], value["labels"])
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
        if "author" in raw_comment:
            comment["author"] = _person(raw_comment["author"], "comment author")
            if comment["author"] is not None and comment["author_kind"] != "human":
                raise LedgerError("only a human comment has an author")
        if "parent_id" in raw_comment:
            comment["parent_id"] = (None if raw_comment["parent_id"] is None
                                    else _text(raw_comment["parent_id"], "comment parent_id"))
        if "url" in raw_comment:
            url = raw_comment["url"]
            if url is not None and not (isinstance(url, str) and LINEAR_URL.fullmatch(url)):
                raise LedgerError("comment url must be null or an https://linear.app/ URL")
            comment["url"] = url
        for field in ["created_at", "updated_at"]:
            _timestamp(comment[field], f"comment {field}")
        if comment["id"] in seen:
            raise LedgerError("duplicate comment id")
        seen.add(comment["id"])
        comments.append(comment)
    value["comments"] = sorted(comments, key=lambda item: item["id"])
    return value


def _person_json(value, name):
    """A person for a nullable people column, as JSON, or None. `_person` accepts exactly an id, a name and a
    linear.app profile URL, so an email or avatar never reaches these columns."""
    checked = _person(value, name)
    return None if checked is None else _json(checked)


def _message(row):
    """An inbox entry as workers read it. `author` is None for entries recorded before authors were kept and for
    those with no Linear author (an operator's enqueue, an event Linear sent without a user); `created_at` is when
    FarmBot received the entry, in ISO 8601 UTC."""
    return {"id": row["id"], "body": row["body"],
            "author": json.loads(row["author_json"]) if row["author_json"] else None,
            "created_at": datetime.fromtimestamp(row["created_at"], timezone.utc).isoformat(timespec="seconds")}


def _without_markers(body):
    """body with every `[farmbot:…]` marker removed, a copy nested inside another included, and trailing
    whitespace dropped, so that only the ledger's own marker ends a comment or a notice."""
    while True:
        stripped = MARKER.sub("", body)
        if stripped == body:
            return body.rstrip()
        body = stripped


def _owner(issue, delegation):
    """spec §4.2, §5.3: the assignee, else the human who last delegated the issue (the creator of its latest
    delegation session in Linear: a re-delegation hands the issue on, even to an item started under an earlier
    one, while an operator enqueue delegates to nobody and is skipped), else nobody (an issue only ever enqueued,
    or a delegation whose creator Linear did not report or an older ledger did not keep)."""
    if issue.get("assignee"):
        return {"person": dict(issue["assignee"]), "source": "assignee"}
    if delegation is not None and delegation["creator_json"]:
        return {"person": json.loads(delegation["creator_json"]), "source": "delegator"}
    return None


def _issue_creator(issue, owner):
    """D17: the issue's creator, usually the 策划 who wrote the card, or None when it is unknown, was not a person
    (Linear gives no creator user for an app or integration) or is the owner, whom a comment already mentions."""
    creator = issue.get("creator")
    if not creator or (owner is not None and owner["person"]["id"] == creator["id"]):
        return None
    return dict(creator)


def _hash_token(token):
    """Only the digest is stored: a leaked ledger file must not hand out live claims."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _in_scope(issue):
    return not issue["archived"] and issue["status_type"] not in TERMINAL_STATUS_TYPES


def _fingerprint(issue, own_bodies, own_prs=()):
    # Issue input only: labels, label groups, people, reply parents and comment links are left out, so reassigning
    # or relabelling an issue never requeues its work.
    material = {key: issue[key] for key in ["title", "description", "attachments"]}
    material["attachments"] = [url for url in issue["attachments"] if url not in own_prs]
    material["comments"] = [{"id": comment["id"], "body": comment["body"]}
                            for comment in issue["comments"]
                            if comment["author_kind"] != "bot" and comment["body"] not in own_bodies]
    return hashlib.sha256(_json(material).encode("utf-8")).hexdigest()


PLAN_KEYS = ("stages", "pause", "change", "ui", "client", "config", "prs", "closing", "events", "started")


def _validate_plan(value):
    """Bound the cross-stage plan like the handoff (spec §5.7), with room for a job that lasts weeks."""
    if not isinstance(value, dict):
        raise LedgerError("plan must be an object")
    unknown = sorted(str(key) for key in value if key not in PLAN_KEYS)
    if unknown:
        raise LedgerError(f"plan accepts only {', '.join(PLAN_KEYS)}; unknown: {', '.join(unknown)}")
    if len(_json(value)) > 16000:
        raise LedgerError("plan exceeds 16000 characters; store longer notes in files in the state directory")
    pending = [("plan", value)]
    while pending:
        where, item = pending.pop()
        if isinstance(item, dict):
            for key, entry in item.items():
                if not isinstance(key, str) or len(key) > 2000:
                    raise LedgerError(f"{where} keys must be strings of at most 2000 characters")
                pending.append((f"{where}.{key}", entry))
        elif isinstance(item, list):
            if len(item) > 50:
                raise LedgerError(f"{where} must be an array of at most 50 entries")
            pending.extend((f"{where}[{index}]", entry) for index, entry in enumerate(item))
        elif isinstance(item, str) and len(item) > 2000:
            raise LedgerError(f"{where} text exceeds 2000 characters")


def _git_accepts(branch):
    """git check-ref-format's rules for a branch name that SAFE_BRANCH's characters leave open: no `..`, no empty
    component, none that starts with `.` or ends with `.lock`, and no `.` at the end."""
    return ".." not in branch and not branch.endswith(".") and all(
        part and not part.startswith(".") and not part.endswith(".lock") for part in branch.split("/"))


def plan_issue_branches(plan, identifier, issue_prefix, *, reserved=False):
    """{repository: branch}: the issue branches a plan records, one entry of `plan.prs` with "role": "issue" per
    repository (spec §5.7 "Re-attachment"; plan P9). Each names this issue's FarmBot branch under the issue-branch
    policy publication enforces (`publication.is_issue_branch`), spelt as FarmBot's worktrees and git accept it, or
    no later launch could check it out. Entries of other roles, and values of other shapes, are the worker's own
    record and decide nothing. With reserved, for a skill in STAGE_ALLOWANCE_SKILLS, an issue branch is never one of
    publication.SUFFIXES: those are the job's Jenkins, waiver and follow-up branches (P9, P12). A successor on a
    Jenkins branch could publish none of its own commits. A fix may keep Linear's suffix suggestion as today.
    LedgerError names the first entry that breaks a rule."""
    recorded = plan.get("prs") if isinstance(plan, dict) else None
    found = {}
    for repo, entries in (recorded.items() if isinstance(recorded, dict) else ()):
        for entry in (entries if isinstance(entries, list) else ()):
            if not (isinstance(entry, dict) and entry.get("role") == "issue"):
                continue
            if repo in found:
                raise LedgerError(f"plan.prs.{repo} has more than one issue entry: record one issue branch per "
                                  "repository, and any other branch under another role")
            try:
                canonical = issue_branch(identifier, issue_prefix)
            except PublicationError as exc:
                raise LedgerError(f"plan.prs.{repo}: {exc}") from None
            branch = entry.get("branch")
            if not (is_issue_branch(branch, identifier, issue_prefix) and SAFE_BRANCH.fullmatch(branch)
                    and _git_accepts(branch)):
                raise LedgerError(f"plan.prs.{repo}: an issue entry names this issue's own branch, {canonical} or "
                                  f"{canonical}-<suffix>, as git spells it; {branch!r} is not one")
            if reserved and suffix_of(branch, canonical) is not None:
                raise LedgerError(f"plan.prs.{repo}: {branch} is one of this job's suffix branches "
                                  f"({', '.join(SUFFIXES)}); record it under its own role, never as the issue branch")
            found[repo] = branch
    return found


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
                CREATE TABLE IF NOT EXISTS issue_checks (
                    issue_id TEXT PRIMARY KEY REFERENCES issues(id),
                    due_at REAL NOT NULL DEFAULT 0,
                    requested INTEGER NOT NULL DEFAULT 0,
                    checked_at REAL NOT NULL DEFAULT 0,
                    failures INTEGER NOT NULL DEFAULT 0,
                    error TEXT
                );
                INSERT OR IGNORE INTO issue_checks(issue_id) SELECT id FROM issues;
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
                    root_repo TEXT,
                    next_root_repo TEXT,
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
                CREATE TABLE IF NOT EXISTS notices (
                    item_id TEXT NOT NULL REFERENCES work_items(id),
                    request_id TEXT NOT NULL,
                    issue_id TEXT NOT NULL REFERENCES issues(id),
                    kind TEXT NOT NULL,
                    marker TEXT NOT NULL,
                    body TEXT NOT NULL,
                    remote_id TEXT,
                    created_at REAL NOT NULL,
                    confirmed_at REAL,
                    PRIMARY KEY(item_id, request_id)
                );
                CREATE INDEX IF NOT EXISTS notices_by_issue ON notices(issue_id);
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
                CREATE TABLE IF NOT EXISTS job_cleanup (
                    item_id TEXT PRIMARY KEY REFERENCES work_items(id),
                    worker_pid INTEGER, result TEXT NOT NULL DEFAULT '{}', error TEXT,
                    done INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY,
                    item_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    details TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                -- A closing activity Linear refused, owed to its thread (silent-delegation design P10): one per
                -- thread, with the job it closed the thread for and that job's state then.
                CREATE TABLE IF NOT EXISTS session_closures (
                    session_id TEXT PRIMARY KEY,
                    issue_id TEXT, item_id TEXT, item_state TEXT,
                    kind TEXT NOT NULL CHECK(kind IN ('response','error')),
                    body TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 1,
                    due_at REAL NOT NULL, created_at REAL NOT NULL,
                    last_error TEXT, given_up_at REAL
                );
                -- A delegation that came back: a read found the card delegated to this app again after one that
                -- found it not delegated (silent-delegation design P11). One row per card; a new episode replaces
                -- one that is no longer waiting.
                CREATE TABLE IF NOT EXISTS delegation_episodes (
                    issue_id TEXT PRIMARY KEY REFERENCES issues(id),
                    since REAL NOT NULL,        -- start of the read that found the card delegated again
                    mark REAL NOT NULL,         -- the undelegated mark that read cleared
                    due_at REAL NOT NULL,       -- when the receiver settles it
                    state TEXT NOT NULL CHECK(state IN ('waiting','heard','served','in_place','told','unseen',
                        'dropped')),
                    session_id TEXT,            -- the thread that kept or took the delegation
                    settled_at REAL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    updated_at REAL NOT NULL,
                    unreachable_since REAL      -- first settle read in a row that Linear answered "not found"
                );
            """)
            self.connection.executescript(RECOVERY_SCHEMA)
            self.connection.executescript(MAINTENANCE_SCHEMA)
            # Columns added after the first ledgers were written; CREATE TABLE IF NOT EXISTS leaves those files as they were.
            for table, column, declaration in (("reservations", "server_test_plan", "TEXT"),
                                                ("reservations", "wait_detail", "TEXT"),
                                                ("resource_recovery_notices", "generation", "INTEGER NOT NULL DEFAULT 0"),
                                                ("resource_recoveries", "recovery_kind", "TEXT NOT NULL DEFAULT 'execution'"),
                                                ("resource_job_retries", "setup_attempts", "INTEGER NOT NULL DEFAULT 0"),
                                                ("sessions", "guidance", "TEXT"), ("work_items", "lease_seconds", "REAL"),
                                                ("work_items", "predecessor_id", "TEXT"),
                                                ("work_items", "parent_id", "TEXT REFERENCES work_items(id)"),
                                                ("work_items", "task_key", "TEXT"),
                                                ("work_items", "task_request", "TEXT"),
                                                ("work_items", "task_client_base", "TEXT"),
                                                ("work_items", "capacity_retries", "INTEGER NOT NULL DEFAULT 0"),
                                                ("work_items", "publication_retries", "INTEGER NOT NULL DEFAULT 0"),
                                                ("work_items", "retry_not_before", "REAL NOT NULL DEFAULT 0"),
                                                ("work_items", "root_repo", "TEXT"),
                                                ("work_items", "next_root_repo", "TEXT"),
                                                ("work_items", "revalidated_fingerprint", "TEXT"),
                                                ("job_cleanup", "removing", "INTEGER NOT NULL DEFAULT 0"),
                                                # People, as the shared person shape in JSON; NULL means unknown.
                                                ("sessions", "creator_json", "TEXT"),
                                                ("inbox", "author_json", "TEXT"),
                                                # Withdrawn work (design P1-P5). NULL authority reads by skill
                                                # (_authority); a NULL deadline means the item is not withdrawn.
                                                ("work_items", "authority", "TEXT"),
                                                ("work_items", "withdraw_deadline", "REAL"),
                                                ("work_items", "withdraw_reason", "TEXT"),
                                                ("issue_checks", "undelegated_since", "REAL"),
                                                ("issue_checks", "unreachable_since", "REAL"),
                                                ("sessions", "forwarded_item", "TEXT"),
                                                ("delegation_episodes", "unreachable_since", "REAL")):
                present = {row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})")}
                if column not in present:
                    self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
            # Only the root job owns the issue's active slot. Internal tasks are
            # admitted exclusively by a live, delegated general-job claim.
            index = self.connection.execute("SELECT sql FROM sqlite_master WHERE name='one_active_item_per_issue'").fetchone()
            if index and "parent_id" not in index["sql"]:
                with self._transaction():
                    self.connection.execute("DROP INDEX one_active_item_per_issue")
                    self.connection.execute("""CREATE UNIQUE INDEX one_active_item_per_issue ON work_items(issue_id)
                        WHERE parent_id IS NULL AND state IN ('queued','running','awaiting_input','awaiting_resource')""")
            self.connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_task_key_per_job ON work_items(parent_id,task_key)")
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
                                            "worker_pid", "needs_resource", "root_repo", "next_root_repo",
                                            "created_at", "predecessor_id", "parent_id", "task_key", "task_client_base",
                                            "capacity_retries", "publication_retries", "retry_not_before",
                                            "withdraw_deadline", "withdraw_reason"]}
        result.update(identifier=issue["identifier"], target=json.loads(row["target_json"]) if row["target_json"] else None,
                      authority=_authority(row),
                      resume_authorized=bool(row["resume_authorized"]), checkpoint=json.loads(row["checkpoint"]),
                      evidence=json.loads(row["evidence"]))
        result["task_request"] = json.loads(row["task_request"]) if row["task_request"] else None
        from .resource_recovery import RecoveryStore
        result['candidate_repair'] = RecoveryStore(self).candidate_context(row['id'], row['generation'])
        result['maintenance_requested'] = Maintenance(self).requested()
        return result

    def _audit(self, item_id, kind, reason="", details=None):
        self.connection.execute("INSERT INTO audit(item_id,kind,reason,details,created_at) VALUES(?,?,?,?,?)",
                                (item_id, kind, reason, _json(details or {}), self.clock()))

    def note(self, item_id, kind, reason="", details=None):
        """Record an operator action in `audit`, so a human act outside Linear leaves the trail a webhook would."""
        with self._transaction():
            self._audit(item_id, kind, reason, details)

    def human_question(self, item_id):
        question = question_from(self._view(self._row(item_id)))
        if question and question["id"].startswith("legacy-"):
            # Do not let pre-upgrade, already-consumed rulings acknowledge a
            # later question merely because the old checkpoint has no round id.
            asked = self.connection.execute("SELECT MAX(created_at) FROM audit WHERE item_id=? "
                "AND reason='human gate'", (item_id,)).fetchone()[0]
            if asked is not None:
                floor = self.connection.execute("SELECT COALESCE(MAX(id),0) FROM inbox WHERE item_id=? "
                    "AND consumed_at<=?", (item_id, asked)).fetchone()[0]
                question = {**question, "after_message_id": floor}
        return question

    def noted_since(self, item_id, kind, since):
        """Whether the job has an audit row of `kind` at or after `since`: how a line that a job gets at most once in
        a while finds its last one (silent-delegation design §3.3)."""
        return self.connection.execute("SELECT 1 FROM audit WHERE item_id=? AND kind=? AND created_at>=? LIMIT 1",
                                       (item_id, kind, since)).fetchone() is not None

    def _owned(self, item_id, token):
        row = self._row(item_id)
        presented = _hash_token(token) if isinstance(token, str) and token.isascii() and token else ""
        if (row["state"] != "running" or not presented
                or not secrets.compare_digest(row["token"] or "", presented)):
            raise LedgerError("running claim and matching token required")
        if row["lease_expires_at"] <= self.clock():
            raise LedgerError("lease expired; the launcher recovers expired work, workers must stop")
        if row["parent_id"]:
            parent = self._row(row["parent_id"])
            if parent["state"] not in ACTIVE_STATES:
                raise LedgerError("parent job ended")
        return row

    def _own_bodies(self, issue_id):
        """Bodies of the comments FarmBot prepared on this issue, outbox rows and notices: never issue input."""
        return {r["body"] for r in self.connection.execute(
            "SELECT body FROM outbox WHERE issue_id=? UNION SELECT body FROM notices WHERE issue_id=?",
            (issue_id, issue_id))}

    def observe_issue(self, raw):
        """Store one complete snapshot. Observing a comment is not authority to restart."""
        issue = _normalize(raw)
        with self._transaction():
            previous = self.connection.execute("SELECT metadata FROM issues WHERE id=?", (issue["id"],)).fetchone()
            if previous:
                previous = json.loads(previous["metadata"])
                if previous.get("updated_at") and (not issue.get("updated_at") or
                        self._version(issue["updated_at"]) <= self._version(previous["updated_at"])):
                    for key in ("status", "status_type", "archived", "delegate_id", "updated_at"):
                        issue[key] = previous.get(key)
            own_bodies = self._own_bodies(issue["id"])
            own_prs = {r["url"] for r in self.connection.execute("SELECT url FROM published_prs WHERE issue_id=?", (issue["id"],))}
            fingerprint = _fingerprint(issue, own_bodies, own_prs)
            self.connection.execute("""INSERT INTO issues(id,metadata,fingerprint,observed_at) VALUES(?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET metadata=excluded.metadata,fingerprint=excluded.fingerprint,observed_at=excluded.observed_at""",
                                    (issue["id"], _json(issue), fingerprint, self.clock()))
            self.connection.execute("INSERT OR IGNORE INTO issue_checks(issue_id) VALUES(?)", (issue["id"],))
        return {"id": issue["id"], "identifier": issue["identifier"], "fingerprint": fingerprint,
                "in_scope": _in_scope(issue)}

    def issue(self, issue_id):
        return json.loads(self._issue_row(issue_id)["metadata"])

    @staticmethod
    def _version(value):
        return datetime.fromisoformat(_timestamp(value, "updated_at").replace("Z", "+00:00")).timestamp()

    def apply_issue_status(self, raw):
        issue_id = _uuid(raw.get("id"), "issue")
        version = self._version(raw.get("updated_at"))
        for key in ("status", "status_type"):
            _text(raw.get(key), key)
        if type(raw.get("archived")) is not bool:
            raise LedgerError("archived must be boolean")
        if raw.get("delegate_id") is not None:
            _uuid(raw["delegate_id"], "delegate_id")
        with self._transaction():
            current = self.issue(issue_id)
            if current.get("updated_at") and version < self._version(current["updated_at"]):
                return current
            for key in ("status", "status_type", "archived", "delegate_id", "updated_at"):
                current[key] = raw.get(key)
            self.connection.execute("UPDATE issues SET metadata=?,observed_at=? WHERE id=?",
                                    (_json(current), self.clock(), issue_id))
        return current

    def request_status_check(self, issue_id):
        issue_id = _uuid(issue_id, "issue")
        with self._transaction():
            return bool(self.connection.execute("UPDATE issue_checks SET requested=1,due_at=0 WHERE issue_id=?",
                                                (issue_id,)).rowcount)

    def status_check(self, issue_id):
        row = self.connection.execute("SELECT * FROM issue_checks WHERE issue_id=?", (issue_id,)).fetchone()
        return dict(row) if row else None

    def due_issue(self):
        row = self.connection.execute("""SELECT c.issue_id FROM issue_checks c WHERE c.due_at<=?
            AND (c.requested=1 OR EXISTS (SELECT 1 FROM work_items w WHERE w.issue_id=c.issue_id
                AND w.state IN ('queued','running','awaiting_input','awaiting_resource','blocked')))
            ORDER BY c.checked_at,c.due_at,c.issue_id LIMIT 1""", (self.clock(),)).fetchone()
        return row["issue_id"] if row else None

    def finish_status_check(self, issue_id, interval, error=None, *, unreachable=False):
        """`unreachable`: Linear said the issue does not exist. The first such read in a row is kept in
        `unreachable_since`; any other result, a failure of another kind included, clears it (design R8)."""
        with self._transaction():
            previous = self.status_check(issue_id)
            failures = previous["failures"] + 1 if error else 0
            delay = min(300, 5 * 2 ** min(failures - 1, 6)) if error else interval
            self.connection.execute("""UPDATE issue_checks SET due_at=?,checked_at=?,requested=0,
                failures=?,error=?,unreachable_since=CASE WHEN ? THEN COALESCE(unreachable_since,?) END
                WHERE issue_id=?""",
                (self.clock() + delay, self.clock(), failures, error, bool(unreachable), self.clock(), issue_id))

    def mark_undelegated(self, issue_id, observed_at):
        """Record `observed_at`, the start of a status read that found the card not delegated to this app, unless an
        earlier one is recorded, and return the recorded one: work is withdrawn only once a later read, at least an
        interval after it, confirms (design P3). A delegation episode that waits from a read older than this one is
        dropped: the delegation it stands for went again (silent-delegation design P11)."""
        issue_id = _uuid(issue_id, "issue")
        if type(observed_at) not in (int, float) or not math.isfinite(observed_at):
            raise LedgerError("observed_at must be a finite number of seconds")
        with self._transaction():
            self._issue_row(issue_id)
            self.connection.execute("INSERT OR IGNORE INTO issue_checks(issue_id) VALUES(?)", (issue_id,))
            self.connection.execute("UPDATE issue_checks SET undelegated_since=COALESCE(undelegated_since,?) "
                                    "WHERE issue_id=?", (observed_at, issue_id))
            # The delegation a waiting episode stands for is gone again, as far as this read is newer than the one
            # that opened the episode: an older read that returned later ends nothing (silent-delegation design
            # P11, P3's fence the other way).
            now = self.clock()
            self.connection.execute("UPDATE delegation_episodes SET state='dropped',settled_at=?,updated_at=? "
                                    "WHERE issue_id=? AND state='waiting' AND since<?",
                                    (now, now, issue_id, observed_at))
            return self.connection.execute("SELECT undelegated_since FROM issue_checks WHERE issue_id=?",
                                           (issue_id,)).fetchone()[0]

    def clear_undelegated(self, issue_id, observed_at=None):
        """A read found the card delegated to this app: the mark goes, and with it the flags it set (design P3).
        `observed_at` is the start of that read. When it is later than the mark it clears, the delegation came back
        after a read that found it gone, and Linear may have opened no session for it: that opens a delegation
        episode, unless one already waits (silent-delegation design P11). The cleared flags are returned either
        way."""
        issue_id = _uuid(issue_id, "issue")
        if observed_at is not None:
            _seconds(observed_at, "observed_at")
        with self._transaction():
            return self._clear_undelegated(issue_id, observed_at)

    def _clear_undelegated(self, issue_id, observed_at=None):
        """Caller owns the transaction and has checked `observed_at`."""
        check = self.connection.execute("SELECT undelegated_since FROM issue_checks WHERE issue_id=?",
                                        (issue_id,)).fetchone()
        mark = check["undelegated_since"] if check else None
        self.connection.execute("UPDATE issue_checks SET undelegated_since=NULL WHERE issue_id=?", (issue_id,))
        if mark is not None and observed_at is not None and observed_at > mark:
            self._open_episode(issue_id, observed_at, mark)
        return self._clear_withdrawals(issue_id)

    def _open_episode(self, issue_id, since, mark):
        """Caller owns the transaction. The read that started at `since` found the card delegated again and cleared
        `mark`: the episode waits SILENT_GRACE_SECONDS from `since` for the delegation's session. An episode that
        already waits is left as it is, so a later read never pushes its grace out; one that was settled or dropped
        is replaced, in the card's one row."""
        if self.connection.execute("SELECT 1 FROM delegation_episodes WHERE issue_id=? AND state='waiting'",
                                   (issue_id,)).fetchone():
            return
        self.connection.execute("""INSERT OR REPLACE INTO delegation_episodes
            (issue_id,since,mark,due_at,state,session_id,settled_at,attempts,error,updated_at)
            VALUES(?,?,?,?,'waiting',NULL,NULL,0,NULL,?)""",
                                (issue_id, since, mark, since + SILENT_GRACE_SECONDS, self.clock()))

    def episode(self, issue_id):
        """The card's delegation episode, waiting or ended, or None when no read found its delegation back."""
        row = self.connection.execute("SELECT * FROM delegation_episodes WHERE issue_id=?", (issue_id,)).fetchone()
        return dict(row) if row else None

    def due_episode(self, now):
        """The waiting episode to settle next: the one longest due at `now`, or None."""
        row = self.connection.execute("""SELECT * FROM delegation_episodes WHERE state='waiting' AND due_at<=?
            ORDER BY due_at,since,issue_id LIMIT 1""", (now,)).fetchone()
        return dict(row) if row else None

    def delegation_heard(self, issue_id, mark):
        """Whether Linear opened a session for the delegation a read found back after `mark` (silent-delegation
        design §3.2). Either a delegation session of this app on the card, a Linear thread and so no `local-` one,
        was recorded at or after the mark: the receiver records an event's session before it routes the event, so
        one that failed afterwards counts. Or a `created` for the card still waits behind another session's worker,
        or a Stop cancelled one at or after the mark: those two keep their payload, which names the card, in the
        receiver's event table of this file."""
        if self.connection.execute("""SELECT 1 FROM sessions WHERE issue_id=? AND delegation=1
                AND session_id NOT LIKE 'local-%' AND created_at>=? LIMIT 1""", (issue_id, mark)).fetchone():
            return True
        if not self.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='webhook_events'").fetchone():
            return False
        # CASE, not AND: json_extract raises on text that is no JSON, and only CASE fixes the order of evaluation.
        return self.connection.execute("""SELECT 1 FROM webhook_events
            WHERE (status='deferred' OR (status='cancelled' AND completed_at>=?))
                AND CASE WHEN json_valid(payload) THEN json_extract(payload,'$.action')='created'
                    AND json_extract(payload,'$.issue_id')=? END
            LIMIT 1""", (mark, issue_id)).fetchone() is not None

    def finish_episode(self, issue_id, since, state, session_id=None):
        """End the card's episode as `state`, one of EPISODE_SETTLED, with the thread that kept or took the
        delegation. Only the episode the caller read is ended: the one that still waits with that `since`. Returns
        whether it was, so a settle acts once, and not at all on an episode a read dropped or replaced meanwhile.
        The count of its failed reads stays; the last failure's name and `unreachable_since` go, since this settle
        got through."""
        if state not in EPISODE_SETTLED:
            raise LedgerError(f"an episode ends as one of {', '.join(EPISODE_SETTLED)}")
        if session_id is not None:
            _text(session_id, "session_id")
        with self._transaction():
            now = self.clock()
            return bool(self.connection.execute("""UPDATE delegation_episodes
                SET state=?,session_id=?,settled_at=?,error=NULL,unreachable_since=NULL,updated_at=?
                WHERE issue_id=? AND since=? AND state='waiting'""",
                                                (state, session_id, now, now, issue_id, since)).rowcount)

    def retry_episode(self, issue_id, since, due_at, error=None, *, unreachable=False):
        """The episode the caller read, still waiting with that `since`, is settled again at `due_at`. `error` names
        a failed read, which is counted in `attempts`; without one the settle found the work changed under it and
        only looks again. `unreachable`: Linear said the card does not exist. The first such read in a row is kept
        in `unreachable_since`; any other result clears it, as for a status read (withdrawn-work design R8).
        Returns whether the episode was still that one."""
        _seconds(due_at, "due_at")
        error = None if error is None else str(error)[:200]
        with self._transaction():
            now = self.clock()
            return bool(self.connection.execute("""UPDATE delegation_episodes
                SET due_at=?,attempts=attempts+?,error=?,updated_at=?,
                    unreachable_since=CASE WHEN ? THEN COALESCE(unreachable_since,?) END
                WHERE issue_id=? AND since=? AND state='waiting'""",
                                                (due_at, int(error is not None), error, now, bool(unreachable), now,
                                                 issue_id, since)).rowcount)

    def drop_episode(self, issue_id, since):
        """End the episode the caller read, still waiting with that `since`, as `dropped` because its card is out of
        reach: no settle got through, so the last failure's name and `unreachable_since` stay as the record of why.
        Returns whether it was that one."""
        with self._transaction():
            now = self.clock()
            return bool(self.connection.execute("""UPDATE delegation_episodes
                SET state='dropped',settled_at=?,updated_at=?
                WHERE issue_id=? AND since=? AND state='waiting'""", (now, now, issue_id, since)).rowcount)

    def unfinished_for_issue(self, issue_id):
        return [self._view(r) for r in self.connection.execute("""SELECT * FROM work_items WHERE issue_id=?
            AND state IN ('queued','running','awaiting_input','awaiting_resource','blocked')""", (issue_id,))]

    def ensure_session(self, session_id, issue_id, delegation, guidance=None, creator=None):
        """Guidance is Linear's operator text for this session; later events may add it. They may also add the
        creator, the human who opened the session (Linear's agentSession.creator), never replaced once recorded."""
        _text(session_id, "session_id")
        if guidance is not None:
            _text(guidance, "guidance", empty=True)
        creator_json = _person_json(creator, "session creator")
        with self._transaction():
            self.connection.execute("""INSERT OR IGNORE INTO sessions(session_id,issue_id,delegation,guidance,created_at)
                VALUES(?,?,?,?,?)""", (session_id, issue_id, int(bool(delegation)), guidance, self.clock()))
            if isinstance(guidance, str) and guidance.strip():
                self.connection.execute("UPDATE sessions SET guidance=? WHERE session_id=?", (guidance, session_id))
            if creator_json is not None:
                self.connection.execute("UPDATE sessions SET creator_json=? WHERE session_id=? AND creator_json IS NULL",
                                        (creator_json, session_id))
        return self.session(session_id)

    def sessions_for_issue(self, issue_id, limit):
        """The card's recorded sessions that are Linear threads, newest first, at most `limit`: each with whether a
        delegation opened it, when it was recorded, and the job its messages were forwarded to, if any. An operator's
        `local-` session is no thread (silent-delegation design §3.5)."""
        if type(limit) is not int or limit < 1:
            raise LedgerError("limit must be a positive integer")
        rows = self.connection.execute("""SELECT session_id,delegation,created_at,forwarded_item FROM sessions
            WHERE issue_id=? AND session_id NOT LIKE 'local-%' ORDER BY created_at DESC,rowid DESC LIMIT ?""",
                                       (issue_id, limit))
        return [{"session_id": row["session_id"], "delegation": bool(row["delegation"]),
                 "created_at": row["created_at"], "forwarded_item": row["forwarded_item"]} for row in rows]

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
                "target": json.loads(row["target_json"]) if row["target_json"] else None, "guidance": row["guidance"],
                "creator": json.loads(row["creator_json"]) if row["creator_json"] else None}

    def create_work_item(self, *, issue_id, session_id, skill, target=None, authority=None):
        """`authority` is what authorised the item (AUTHORITIES); None records the skill's default."""
        _text(skill, "skill")
        if skill == "scene":
            raise LedgerError("scene is an internal presentation capability; delegate it from a general job")
        authority = _checked_authority(skill, authority)
        with self._transaction():
            issue = json.loads(self._issue_row(issue_id)["metadata"])
            return self._view(self._row(self._insert_item(issue, session_id, skill, target, authority)))

    def _insert_item(self, issue, session_id, skill, target, authority):
        """Caller owns the transaction and has checked `authority`. Queue a new item of `skill` on `issue` (its stored
        metadata) in `session_id`, and return its id."""
        if self.session(session_id) is None:
            raise LedgerError(f"unknown session: {session_id}")
        if not _in_scope(issue):
            raise LedgerError("issue is archived or in a terminal status")
        if self.connection.execute("SELECT 1 FROM work_items WHERE issue_id=? AND state IN ('queued','running','awaiting_input','awaiting_resource')",
                                   (issue["id"],)).fetchone():
            raise LedgerError("an active work item already exists for this issue")
        # A re-delegation continues its skill's cancelled job, for every write skill (spec §9.4): the successor
        # reads that job's plan and notices in `recovery` and launches after its cleanup. Chat continues nothing.
        prior = self.connection.execute(
            "SELECT id,state FROM work_items WHERE issue_id=? AND skill=? AND parent_id IS NULL ORDER BY created_at DESC,rowid DESC LIMIT 1",
            (issue["id"], skill)).fetchone() if skill in WRITE_SKILLS else None
        predecessor = prior["id"] if prior and prior["state"] == "cancelled" else None
        item_id = str(uuid4())
        now = self.clock()
        self.connection.execute("""INSERT INTO work_items(id,issue_id,session_id,skill,state,priority,target_json,created_at,updated_at,predecessor_id,authority)
            VALUES(?,?,?,?,'queued',?,?,?,?,?,?)""",
                                (item_id, issue["id"], session_id, skill, issue["priority"] or 5,
                                 json.dumps(target) if target is not None else None, now, now, predecessor, authority))
        self._audit(item_id, "create", f"skill {skill}")
        return item_id

    def active_item_for_session(self, session_id):
        row = self.connection.execute("""SELECT * FROM work_items WHERE session_id=? AND parent_id IS NULL AND state IN
            ('queued','running','awaiting_input','awaiting_resource') ORDER BY created_at DESC LIMIT 1""", (session_id,)).fetchone()
        if row is None:
            # A mention may have handed execution back to the original delegation
            # session. Replies and Stop still belong to the same logical conversation.
            row = self.connection.execute("""SELECT w.* FROM work_items c JOIN work_items w
                ON w.id=json_extract(c.evidence,'$.resumed_item') AND w.issue_id=c.issue_id
                WHERE c.session_id=? AND c.skill='chat' AND c.state='delivered'
                AND w.state IN ('queued','running','awaiting_input','awaiting_resource')
                ORDER BY c.created_at DESC,c.rowid DESC LIMIT 1""", (session_id,)).fetchone()
        return self._view(row) if row else None

    def record_forward(self, session_id, item_id):
        """`session_id`'s messages went to `item_id`, work of another session: a Stop there reaches it (design P5)."""
        _text(session_id, "session_id")
        with self._transaction():
            if self.session(session_id) is None:
                raise LedgerError(f"unknown session: {session_id}")
            self._row(item_id)
            self.connection.execute("UPDATE sessions SET forwarded_item=? WHERE session_id=?", (item_id, session_id))

    def stop_target(self, session_id):
        """(item, where): the work a Stop in `session_id` stops, the first of `own`, the session's own active item;
        `forwarded`, the active item its messages were forwarded to; `latest_delegation`, the issue's active item of
        delegation authority when this is the card's latest delegation session (design P5). With nothing to stop,
        item is None and `where` says why: `moved`, a new delegation session took the session's last item over;
        `moved_thread`, a person's message moved that conversation to another thread (design C5); `stopped`, it was
        cancelled; `none`, anything else."""
        own = self.active_item_for_session(session_id)
        if own is not None:
            return own, "own"
        session = self.connection.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if session is None:
            return None, "none"
        if session["forwarded_item"]:
            row = self.connection.execute("""SELECT * FROM work_items WHERE id=? AND state IN
                ('queued','running','awaiting_input','awaiting_resource')""", (session["forwarded_item"],)).fetchone()
            if row is not None:
                return self._view(row), "forwarded"
        if session["issue_id"]:
            latest = self._owner_delegation(session["issue_id"])
            active = self.active_item_for_issue(session["issue_id"])
            # Only work the delegation authorised: a conversation a mention or the operator started after this
            # session's work ended is not this session's to stop.
            if (latest is not None and latest["session_id"] == session_id and active is not None
                    and active["authority"] == "delegation"):
                return active, "latest_delegation"
        last = self.connection.execute("SELECT * FROM work_items WHERE session_id=? AND parent_id IS NULL ORDER BY created_at DESC,rowid DESC "
                                       "LIMIT 1", (session_id,)).fetchone()
        if last is None or last["state"] != "cancelled":
            return None, "none"
        # A takeover either cancelled the item at once or, for a claimed worker, flagged it until it withdrew. Only a
        # supersede can move work without a new delegation, and it records which it was.
        if last["withdraw_reason"] == "superseded":
            return None, "moved"
        superseded = self.connection.execute("SELECT details FROM audit WHERE item_id=? AND kind='superseded' "
                                             "ORDER BY id DESC LIMIT 1", (last["id"],)).fetchone()
        if superseded is None:
            return None, "stopped"
        return None, "moved" if json.loads(superseded["details"]).get("takeover", True) else "moved_thread"

    def items_for_session(self, session_id):
        rows = self.connection.execute("SELECT * FROM work_items WHERE session_id=? ORDER BY created_at, id", (session_id,))
        return [self._view(row) for row in rows]

    def active_item_for_issue(self, issue_id):
        row = self.connection.execute("""SELECT * FROM work_items WHERE issue_id=? AND parent_id IS NULL AND state IN
            ('queued','running','awaiting_input','awaiting_resource') ORDER BY created_at DESC LIMIT 1""", (issue_id,)).fetchone()
        return self._view(row) if row else None

    def item(self, item_id):
        return self._view(self._row(item_id))

    def queue(self):
        # Withdrawn work that went back to the queue (an expired lease, a retry delay) is never launched again: it
        # waits for the status read or the new session that ends it, or for a read that finds the delegation back.
        rows = self.connection.execute("SELECT * FROM work_items WHERE state='queued' AND worker_pid IS NULL "
                                       "AND retry_not_before<=? AND withdraw_deadline IS NULL "
                                       "ORDER BY priority, created_at, id", (self.clock(),))
        return [self._view(row) for row in rows]

    def expired_withdrawals(self):
        """Claimed workers past grace, or a flagged general coordinator whose group can now stop."""
        rows = self.connection.execute("""SELECT * FROM work_items w WHERE withdraw_deadline IS NOT NULL AND
            ((state='running' AND withdraw_deadline<=?) OR
             (skill='work' AND parent_id IS NULL AND state IN ('queued','running','awaiting_input','awaiting_resource')
              AND (withdraw_deadline<=? OR (state!='running' AND NOT EXISTS
                   (SELECT 1 FROM work_items t WHERE t.parent_id=w.id AND t.state='running')))))
            ORDER BY withdraw_deadline, id""", (self.clock(), self.clock()))
        return [self._view(row) for row in rows]

    def expire_withdrawal(self, item_id):
        """Cancel a flagged worker past grace, or its parked general coordinator after the children stop.

        Recheck the flag and group in the transaction: a delegation read may have cleared the listed flag.
        Returns the cancelled view, whose `withdraw_reason` says why, or None."""
        with self._transaction():
            row = self._row(item_id)
            group = row["skill"] == "work" and row["parent_id"] is None and row["state"] in ACTIVE_STATES
            children = self._active_children(row["id"]) if group else []
            ready = group and row["state"] != "running" and not any(c["state"] == "running" for c in children)
            if (row["withdraw_deadline"] is None or (row["state"] != "running" and not group)
                    or (row["withdraw_deadline"] > self.clock() and not ready)):
                return None
            self._cancel_row(row, f"withdrawal grace expired ({row['withdraw_reason']})", drop_progress=True)
            return self._view(self._row(row["id"]))

    def launched(self):
        """Queued items whose worker was spawned but has not claimed yet."""
        rows = self.connection.execute("SELECT * FROM work_items WHERE state='queued' AND worker_pid IS NOT NULL "
                                       "AND next_root_repo IS NULL AND NOT EXISTS (SELECT 1 FROM maintenance_handoffs m "
                                       "WHERE m.item_id=work_items.id AND m.completed_at IS NULL) ORDER BY created_at, id")
        return [{**self._view(row), "updated_at": row["updated_at"]} for row in rows]

    def repository_handoffs(self):
        """Revoked workers awaiting proven teardown before another repository is launched."""
        rows = self.connection.execute("SELECT * FROM work_items WHERE state='queued' "
                                       "AND next_root_repo IS NOT NULL AND worker_pid IS NOT NULL ORDER BY updated_at, id")
        return [self._view(row) for row in rows]

    def status(self):
        rows = [self._view(row) for row in self.connection.execute("SELECT * FROM work_items ORDER BY created_at, id")]
        counts = {}
        for row in rows:
            counts[row["state"]] = counts.get(row["state"], 0) + 1
        counts["total"] = len(rows)
        compact = [{key: row[key] for key in ("id", "identifier", "skill", "state", "stage", "priority", "lease_expires_at", "worker_pid", "next_root_repo")}
                   for row in rows]
        return {"counts": counts, "items": compact,
                "recovery_required": [row["id"] for row in rows if row["state"] == "running" and row["lease_expires_at"] <= self.clock()]}

    def lifecycle_status(self):
        """Operator diagnostics; avoid serializing history on every scheduler tick."""
        return {"cleanup_pending": [self.cleanup_record(r["item_id"]) for r in self.connection.execute(
                    "SELECT item_id FROM job_cleanup WHERE done=0 ORDER BY updated_at")],
                "issue_status_errors": [dict(r) for r in self.connection.execute(
                    "SELECT * FROM issue_checks WHERE error IS NOT NULL ORDER BY checked_at")]}

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
        if state in ("failed", "blocked", "cancelled"):
            for child in self.connection.execute("SELECT * FROM work_items WHERE parent_id=? AND state IN "
                                                 "('queued','running','awaiting_input','awaiting_resource')",
                                                 (item_id,)).fetchall():
                self._cancel_row(child, reason, drop_progress=True)
        assignments = ",".join(f"{name}=?" for name in columns)
        values = list(columns.values())
        self.connection.execute(f"UPDATE work_items SET state=?,updated_at=?{',' + assignments if assignments else ''} WHERE id=?",
                                (state, self.clock(), *values, item_id))
        self._audit(item_id, state, reason)
        # A closing activity owed for the job as it stood goes with that state (silent-delegation design P10): the job
        # speaks in its thread from here on, and a state it comes back to later must not bring the older words back.
        self.connection.execute("DELETE FROM session_closures WHERE item_id=?", (item_id,))

    def claim(self, item_id, *, worker_id):
        _text(worker_id, "worker_id")
        if len(worker_id) > 200 or "\n" in worker_id:
            raise LedgerError("worker_id must be a runtime identifier of at most 200 characters")
        with self._transaction():
            row = self._row(item_id)
            if row["state"] != "queued":
                raise LedgerError("only a queued work item can be claimed")
            if row["next_root_repo"] is not None:
                raise LedgerError("previous worker has not finished teardown")
            if Maintenance(self).pending(item_id):
                raise LedgerError("maintenance handoff awaits proven worker teardown")
            from .admission_scope import allows
            if not allows(self.connection, item_id) and not (Maintenance(self).requested() and self.active_reservation(item_id)):
                raise LedgerError('operator single-issue acceptance has paused this issue')
            if row["retry_not_before"] > self.clock():
                raise LedgerError("automatic retry delay has not elapsed")
            if row["parent_id"]:
                parent = self._row(row["parent_id"])
                if parent["state"] not in ACTIVE_STATES or parent["withdraw_deadline"] is not None:
                    raise LedgerError("parent job is no longer active or delegated")
            issue_row = self._issue_row(row["issue_id"])
            if not _in_scope(json.loads(issue_row["metadata"])):
                raise LedgerError("issue left scope; cancel instead of claiming")
            token = "claim_" + secrets.token_urlsafe(32)
            generation = row["generation"] + int(bool(row["requeue_requested"]))
            checkpoint = json.loads(row["checkpoint"])
            checkpoint.pop("worker_id", None)
            checkpoint["worker_id"] = worker_id
            stage = ("unity-verifying" if row["stage"] in ("unity-worker-ready", "unity-worker-starting")
                     else row["stage"])
            self._set_state(item_id, "running", "claim", token=_hash_token(token),
                            lease_expires_at=self.clock() + (row["lease_seconds"] or self.lease_seconds),
                            claimed_fingerprint=issue_row["fingerprint"], revalidated_fingerprint=None,
                            generation=generation, requeue_requested=0, resume_authorized=0,
                            stage=stage, checkpoint=_json(checkpoint))
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
            if row["stage"] == "unity-worker-ready":
                self.connection.execute("UPDATE work_items SET stage='unity-worker-starting' WHERE id=?", (item_id,))
            self.connection.execute("UPDATE job_cleanup SET worker_pid=?,done=0 WHERE item_id=?", (pid, item_id))
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

    def checkpoint_error(self, item_id):
        event = self.connection.execute("""SELECT kind,reason FROM audit WHERE item_id=?
            AND kind IN ('handoff_rejected','handoff_saved') ORDER BY id DESC LIMIT 1""", (item_id,)).fetchone()
        return event["reason"] if event and event["kind"] == "handoff_rejected" else None

    def require_valid_checkpoint(self, item_id, token):
        """A rejected handoff must be repaired before voluntarily retiring its claim."""
        self._owned(item_id, token)
        if error := self.checkpoint_error(item_id):
            raise LedgerError(f"repair the rejected checkpoint handoff before pausing or finishing: {error}")

    def checkpoint(self, item_id, token, progress, *, verified_prs=(), issue_prefix="FARM"):
        """`issue_prefix` is the host's issue namespace (Config.issue_prefix), which the worker CLI passes: a plan's
        issue branches are checked against it (plan P9)."""
        if not isinstance(progress, dict):
            raise LedgerError("checkpoint input must be an object")
        handoff_updated = "handoff" in progress
        if handoff_updated:
            try:
                _validate_handoff(progress["handoff"])
            except LedgerError as exc:
                # Commit the rejection independently of the failed write. It must survive a
                # CLI restart, and an invalid/stale claim must not poison another worker.
                with self._transaction():
                    self._owned(item_id, token)
                    self._audit(item_id, "handoff_rejected", str(exc))
                raise
        if "plan" in progress:
            _validate_plan(progress["plan"])  # refused outright: a plan is not a handoff, so no rejection is kept
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
            # A resume/checkpoint is not confirmation that a human question was
            # answered. Only resolve-question may acknowledge the current round.
            progress.pop(QUESTION_KEY, None)
            if QUESTION_KEY in previous or (question := self.human_question(item_id)):
                progress[QUESTION_KEY] = previous.get(QUESTION_KEY) if QUESTION_KEY in previous else question
            progress.pop("waiting_tasks", None)
            progress.pop("task_wake", None)
            if "task_wake" in previous:
                progress["task_wake"] = previous["task_wake"]
            if previous.get("worker_id"):
                if "worker_id" in progress and progress["worker_id"] != previous["worker_id"]:
                    raise LedgerError("checkpoint cannot change the running worker identity")
                progress["worker_id"] = previous["worker_id"]
            known = {r["url"] for r in self.connection.execute("SELECT url FROM published_prs WHERE issue_id=?", (row["issue_id"],))}
            issue = json.loads(self._issue_row(row["issue_id"])["metadata"])
            if "plan" in progress:
                # P9: a recorded issue branch decides where later attempts' worktrees start (spec §5.7). Refused
                # outright, like any invalid plan, before anything of this checkpoint is written.
                plan_issue_branches(progress["plan"], issue["identifier"], issue_prefix,
                                    reserved=row["skill"] in STAGE_ALLOWANCE_SKILLS)
            existing_input = set(issue["attachments"])
            new_prs = set(published) - known
            late_prs = new_prs & existing_input
            own_bodies = self._own_bodies(row["issue_id"])
            fingerprint = _fingerprint(issue, own_bodies, known | new_prs)
            claimed = row["claimed_fingerprint"]
            # A PR can reach Linear before its checkpoint (including during the next
            # verify-publication call). Only verified job output which exactly restores
            # the claimed input can repair that echo. Real human changes still requeue.
            if late_prs and not late_prs <= set(verified_prs):
                raise LedgerError("published PR was already issue input; reconcile it instead of registering it as new output")
            if late_prs and fingerprint != claimed:
                # A claim this worker revalidated may already count the echo as input: it re-read the issue with the
                # PR attached. If nothing else changed since, registering the PR moves the claim with the input.
                if (row["revalidated_fingerprint"] != claimed
                        or _fingerprint(issue, own_bodies, known | (new_prs - late_prs)) != claimed):
                    raise LedgerError("published PR was already issue input; read the issue and revalidate before registering it")
                claimed = fingerprint
            progress.pop("handoff_meta", None)
            if "handoff" in progress:
                progress["handoff_meta"] = {"fingerprint": claimed,
                                            "generation": row["generation"], "worker_id": progress.get("worker_id"),
                                            "claim_token_hash": row["token"],
                                            "recorded_at": self.clock()}
            elif "handoff" in previous:
                progress["handoff"] = previous["handoff"]
                progress["handoff_meta"] = previous.get("handoff_meta")
            if "plan" not in progress and "plan" in previous:
                progress["plan"] = previous["plan"]
            for url in sorted(set(published) - known):
                self.connection.execute("INSERT INTO published_prs(issue_id,url,generation,created_at) VALUES(?,?,?,?)",
                                        (row["issue_id"], url, row["generation"], self.clock()))
                self._audit(row["id"], "published_pr", details={"url": url})
            if late_prs:
                self.connection.execute("UPDATE issues SET fingerprint=? WHERE id=?", (fingerprint, row["issue_id"]))
            if claimed != row["claimed_fingerprint"]:
                self.connection.execute("UPDATE work_items SET claimed_fingerprint=?,revalidated_fingerprint=? WHERE id=?",
                                        (claimed, claimed, row["id"]))
                self._audit(row["id"], "revalidate", "registered a pull request Linear had attached",
                            {"from": row["claimed_fingerprint"], "to": claimed})
            self.connection.execute("UPDATE work_items SET checkpoint=?,stage=COALESCE(?,stage),updated_at=? WHERE id=?",
                                    (_json(progress), stage, self.clock(), row["id"]))
            self._audit(row["id"], "checkpoint", stage or "")
            if handoff_updated:
                self._audit(row["id"], "handoff_saved")
            return self._view(self._row(row["id"]))

    def revalidate(self, item_id, token, fingerprint):
        """Move a live claim onto the issue input its worker has just read (spec §9.9, §11).

        `fetch-issue` stores a snapshot and prints its fingerprint; the worker reads that snapshot through
        `issue-context` and passes the fingerprint back. Only the stored fingerprint is accepted, so an
        observation in between sends the worker back to read again, and a change after this one still
        refuses the next handoff and requeues `finish`. A handoff saved before the re-read stays stale.

        `requeue_requested` refuses rather than being cleared. No code path sets it: the input change is
        carried by the stored fingerprint alone. Its readers (claim, finish, handoff, issue_context) treat
        it as a restart request of its own, which a re-read must not cancel.
        """
        if not isinstance(fingerprint, str) or not FINGERPRINT.fullmatch(fingerprint):
            raise LedgerError("fingerprint must be the 64-character value of issue-context.fingerprint")
        with self._transaction():
            row = self._owned(item_id, token)
            issue_row = self._issue_row(row["issue_id"])
            if not _in_scope(json.loads(issue_row["metadata"])):
                raise LedgerError("issue left scope; cancel instead of revalidating")
            if row["requeue_requested"]:
                raise LedgerError("a requeue is already requested for this item; revalidate cannot cancel it")
            if fingerprint != issue_row["fingerprint"]:
                raise LedgerError("issue changed since that read; run fetch-issue, read the new input and revalidate again")
            self.connection.execute("UPDATE work_items SET claimed_fingerprint=?,revalidated_fingerprint=?,updated_at=? "
                                    "WHERE id=?", (fingerprint, fingerprint, self.clock(), row["id"]))
            self._audit(row["id"], "revalidate", "", {"from": row["claimed_fingerprint"], "to": fingerprint})
            return {**self._view(self._row(row["id"])), "claimed_fingerprint": fingerprint,
                    "previous_fingerprint": row["claimed_fingerprint"]}

    def handoff_repository(self, item_id, token, to_repo, *, skill):
        """Retire a claim while retaining its PID; only the controller may finish the handoff.

        The ledger reads no manifests: `skill` is the item's loaded skill, which the worker CLI passes. A staged
        skill may move to any repository in its writes other than its current root.
        """
        if to_repo not in skill.writes:
            raise LedgerError(f"repository is not a {skill.name} target")
        with self._transaction():
            row = self._owned(item_id, token)
            self._refuse_withdrawn(row)
            if skill.name != row["skill"]:
                raise LedgerError("repository handoff requires the work item's own skill manifest")
            if not skill.staged:
                raise LedgerError(f"repository handoff requires a staged skill; {skill.name} is not staged")
            root = current_root(row["root_repo"], skill)
            if root == to_repo:
                raise LedgerError("worker is already rooted in that repository")
            if to_repo == "Farm-Client" and (row["skill"] == "work" or row["parent_id"]):
                from .workflow import client_ready
                client_ready(self, row)
            if row["next_root_repo"] is not None:
                raise LedgerError("repository handoff is already pending")
            if row["worker_pid"] is None:
                raise LedgerError("worker process is not recorded yet; retry the handoff")
            self.require_valid_checkpoint(item_id, token)
            checkpoint = json.loads(row["checkpoint"])
            meta = checkpoint.get("handoff_meta") or {}
            if (not checkpoint.get("handoff") or meta.get("worker_id") != checkpoint.get("worker_id")
                    or meta.get("generation") != row["generation"]
                    or meta.get("claim_token_hash") != row["token"]
                    or meta.get("fingerprint") != row["claimed_fingerprint"]):
                raise LedgerError("save a current worker checkpoint before repository handoff")
            if self.connection.execute("SELECT 1 FROM reservations WHERE item_id=? "
                                       "AND state IN ('queued','active','cancel_requested') LIMIT 1", (item_id,)).fetchone():
                raise LedgerError("release the resource reservation before repository handoff")
            if not _in_scope(json.loads(self._issue_row(row["issue_id"])["metadata"])):
                raise LedgerError("issue left scope; cancel instead of handing off")
            if row["requeue_requested"] or self._issue_row(row["issue_id"])["fingerprint"] != row["claimed_fingerprint"]:
                raise LedgerError("issue changed; revalidate before repository handoff")
            self._set_state(item_id, "queued", "repository handoff requested", token=None,
                            lease_expires_at=None, next_root_repo=to_repo)
            self._audit(item_id, "repository_handoff_requested", details={
                "from": root, "to": to_repo, "worker_pid": row["worker_pid"]})
            return self._view(self._row(item_id))

    def complete_repository_handoff(self, item_id, expected_pid, *, skill):
        """Controller-only transition after the old worker and descendants are certified gone.

        `skill` is the item's loaded skill, which the scheduler passes: a target its manifest no longer allows
        stays pending.
        """
        with self._transaction():
            row = self._row(item_id)
            target = row["next_root_repo"]
            if (row["state"] != "queued" or skill.name != row["skill"] or not skill.staged
                    or target not in skill.writes
                    or row["worker_pid"] != expected_pid or row["token"] is not None):
                raise LedgerError("repository handoff no longer matches the retired worker")
            self._set_state(item_id, "queued", "repository handoff complete", root_repo=target,
                            next_root_repo=None, worker_pid=None, **self._new_stage_allowances(row))
            self._audit(item_id, "repository_handoff_complete", details={"to": target})
            return self._view(self._row(item_id))

    def pin_feature_client(self, item_id, expected_generation, target, *, branch, issue_prefix="FARM"):
        """Compatibility entry point for the existing Code-only controller call."""
        if self._row(item_id)["skill"] != "feature":
            raise LedgerError("feature client baseline requires a feature item")
        return self.pin_staged_client(item_id, expected_generation, target, branch=branch, issue_prefix=issue_prefix)

    def pin_staged_client(self, item_id, expected_generation, target, *, branch, issue_prefix="FARM"):
        """Controller-only client-stage baseline, fenced after Git reads and before a launch.

        Code/UI sessions keep no intake pin. Persist this item's baseline and exact issue branch before
        creating its client worktree, so retries cannot silently select newer main. No worker CLI exposes this
        transition; it creates neither a claim nor a resource reservation.
        """
        target = checked_target(target)
        if target["repository"] != "Farm-Client":
            raise LedgerError("staged client baseline requires Farm-Client")
        with self._transaction():
            row = self._row(item_id)
            client_root = row["root_repo"] == "Farm-Client" or row["skill"] == "scene" and row["root_repo"] is None
            if (row["skill"] not in ("work", "feature", "fgui", "scene") or row["state"] != "queued" or not client_root
                    or row["next_root_repo"] is not None or row["worker_pid"] is not None
                    or row["token"] is not None or row["generation"] != expected_generation):
                raise LedgerError("staged client baseline requires the same queued client-stage generation")
            self._refuse_withdrawn(row)
            metadata = json.loads(self._issue_row(row["issue_id"])["metadata"])
            if not _in_scope(metadata):
                raise LedgerError("issue left scope before client baseline selection")
            plan_issue_branches({"prs": {"Farm-Client": [{"role": "issue", "branch": branch}]}},
                                metadata["identifier"], issue_prefix, reserved=True)
            target["issue_branch"] = branch
            if row["parent_id"]:
                parent = self._row(row["parent_id"])
                if parent["skill"] != "work" or parent["state"] not in ACTIVE_STATES:
                    raise LedgerError("presentation baseline requires its active general job")
                self._refuse_withdrawn(parent)
                shared = json.loads(parent["target_json"]) if parent["target_json"] else None
                if shared is not None and shared != target:
                    raise LedgerError("presentation must retain its general job's immutable Client baseline and branch")
                if shared is None:
                    self.connection.execute("UPDATE work_items SET target_json=?,updated_at=? WHERE id=?",
                                            (_json(target), self.clock(), parent["id"]))
                    self._audit(parent["id"], "work_client_baseline", details=target)
            previous = json.loads(row["target_json"]) if row["target_json"] else None
            if previous is not None:
                if previous != target:
                    raise LedgerError("staged client baseline and issue branch are immutable")
                return self._view(row)
            self.connection.execute("UPDATE work_items SET target_json=?, updated_at=? WHERE id=?",
                                    (_json(target), self.clock(), item_id))
            self._audit(item_id, row["skill"] + "_client_baseline", details=target)
            return self._view(self._row(item_id))

    def pin_task_client_base(self, item_id, expected_generation, commit):
        """Controller records the clean owned entry HEAD for a presentation's scope.

        The job's main baseline stays immutable. A later task verifies only its
        own changes on top of earlier delivered presentation, never bypassing
        the UI-only diff check by including siblings in its certified scope.
        """
        if not isinstance(commit, str) or not COMMIT_SHA.fullmatch(commit):
            raise LedgerError("presentation entry requires a full commit")
        with self._transaction():
            row = self._row(item_id)
            if (not row["parent_id"] or row["state"] != "queued" or row["root_repo"] not in (None, "Farm-Client")
                    or row["generation"] != expected_generation or row["next_root_repo"] is not None
                    or row["worker_pid"] is not None or row["token"] is not None or not row["target_json"]):
                raise LedgerError("presentation entry requires its queued Client generation")
            self._refuse_withdrawn(row)
            parent = self._row(row["parent_id"])
            if parent["state"] not in ACTIVE_STATES or parent["skill"] != "work":
                raise LedgerError("presentation entry requires its active general job")
            entry_head, inherited = commit, []
            if row["skill"] == "fgui":
                predecessor, seen = parent["predecessor_id"], set()
                while predecessor and predecessor not in seen:
                    seen.add(predecessor)
                    previous = self.connection.execute("SELECT id,state,task_client_base,target_json FROM work_items WHERE parent_id=? "
                                                       "AND skill='fgui' ORDER BY created_at,rowid", (predecessor,)).fetchall()
                    unfinished = [task for task in previous
                                  if task["task_client_base"] and task["state"] != "delivered"]
                    if unfinished:
                        bases = {task["task_client_base"] for task in unfinished}
                        if len(bases) != 1 or any(json.loads(task["target_json"] or "null") != json.loads(row["target_json"])
                                                for task in unfinished):
                            raise LedgerError("uncertified predecessor Client baselines are ambiguous; preserve recovery evidence")
                        if any(not (self.cleanup_record(task["id"]) or {}).get("done") for task in unfinished):
                            raise LedgerError("predecessor UI baseline recovery waits for certified task teardown")
                        # Keep every unverified predecessor change in the new
                        # task's diff. The current candidate HEAD is evidence,
                        # never a replacement baseline that hides that output.
                        commit = bases.pop()
                        inherited = [task["id"] for task in unfinished]
                        break
                    if any(task["state"] == "delivered" for task in previous):
                        break  # a later UI delivery already revalidated the retained candidate
                    predecessor = self._row(predecessor)["predecessor_id"]
            if self.connection.execute("SELECT 1 FROM work_items WHERE parent_id=? AND id<>? AND task_client_base IS NOT NULL "
                                       "AND state IN ('queued','running','awaiting_input','awaiting_resource')",
                                       (row["parent_id"], item_id)).fetchone():
                raise LedgerError("another presentation task retains this job's Client stage")
            if row["task_client_base"] is not None:
                if row["task_client_base"] != commit:
                    raise LedgerError("presentation entry HEAD is immutable")
                return self._view(row)
            self.connection.execute("UPDATE work_items SET task_client_base=?,updated_at=? WHERE id=?",
                                    (commit, self.clock(), item_id))
            self._audit(item_id, "task_client_base", details={"commit": commit, "entry_head": entry_head,
                                                             "predecessor_tasks": inherited})
            return self._view(self._row(item_id))

    def _new_stage_allowances(self, row):
        """Caller owns the transaction. A new stage of a job whose skill starts at an initial root (a completed
        repository handoff, or a resume from a human gate) gets the automatic-retry allowances a job starts with
        (spec §5.8, D16): this clears the item's Unity execution and setup budgets and returns the capacity and
        publication counters to reset beside its next state. For every other skill it changes nothing and returns
        {}: a fix's allowances last its job, reset only by `retry` and a requested continuation."""
        if row["skill"] not in STAGE_ALLOWANCE_SKILLS:
            return {}
        self.connection.execute("DELETE FROM resource_job_retries WHERE item_id=?", (row["id"],))
        self._audit(row["id"], "stage_allowances", "automatic-retry allowances reset for a new stage")
        return {"capacity_retries": 0, "publication_retries": 0}

    def feature_client_branch(self, item_id, *, issue_prefix="FARM"):
        """Compatibility entry point retaining the Code-only caller boundary."""
        if self._row(item_id)["skill"] != "feature":
            raise LedgerError("feature verification requires a feature item")
        return self.staged_client_branch(item_id, issue_prefix=issue_prefix)

    def staged_client_branch(self, item_id, *, issue_prefix="FARM"):
        """Revalidate controller metadata before selecting a Code/UI verification checkout."""
        row = self._row(item_id)
        target = json.loads(row["target_json"]) if row["target_json"] else None
        if (row["skill"] not in ("work", "feature", "fgui", "scene") or not target or target.get("repository") != "Farm-Client"
                or not target.get("issue_branch")):
            raise LedgerError("staged verification requires its controller-selected client baseline")
        identifier = json.loads(self._issue_row(row["issue_id"])["metadata"])["identifier"]
        return plan_issue_branches({"prs": {"Farm-Client": [{"role": "issue", "branch": target["issue_branch"]}]}},
                                   identifier, issue_prefix, reserved=True)["Farm-Client"]

    def require_no_reservation(self, item_id):
        """A pause holds no process and no Unity slot (spec §5.2): release or withdraw the request first."""
        if self.connection.execute(f"""SELECT 1 FROM reservations WHERE item_id=? AND state IN
                ({','.join('?' * len(self.RESERVATION_OPEN))}) LIMIT 1""", (item_id, *self.RESERVATION_OPEN)).fetchone():
            raise LedgerError("release the resource reservation before pausing for input")

    def await_input(self, item_id, token, question, *, reason="question"):
        _text(question, "question")
        if reason not in AWAIT_REASONS:
            raise LedgerError("await-input reason must be question or waiting")
        with self._transaction():
            row = self._owned(item_id, token)
            # Withdrawn work is never parked: nothing would answer it on the card it left.
            self._refuse_withdrawn(row)
            self.require_valid_checkpoint(item_id, token)
            self.require_no_reservation(row["id"])
            checkpoint = json.loads(row["checkpoint"])
            checkpoint["pending_question"] = question
            checkpoint["pending_reason"] = reason
            if reason == "question":
                after = self.connection.execute("SELECT COALESCE(MAX(id),0) FROM inbox "
                    "WHERE item_id=? AND consumed_at IS NOT NULL", (item_id,)).fetchone()[0]
                checkpoint[QUESTION_KEY] = {"id": str(uuid4()), "body": question, "after_message_id": after}
            # Linear may deliver the answer between posting the question and this
            # transaction. Do not strand that reply behind an awaiting-input gate.
            pending = self.connection.execute("SELECT 1 FROM inbox WHERE item_id=? AND consumed_at IS NULL",
                                              (item_id,)).fetchone() is not None
            # An answer that is already here resumes the gate at once, and so starts a new stage (spec §5.8).
            self._set_state(row["id"], "queued" if pending else "awaiting_input", "human gate",
                            token=None, lease_expires_at=None, worker_pid=None,
                            resume_authorized=int(pending), checkpoint=_json(checkpoint),
                            **(self._new_stage_allowances(row) if pending else {}))
            return self._view(self._row(row["id"]))

    def resolve_question(self, item_id, token, question_id, message_ids):
        """A worker confirms its entire current question round against actual human replies."""
        if not message_ids or any(type(mid) is not int or mid <= 0 for mid in message_ids):
            raise LedgerError("resolve-question requires positive inbox message ids")
        with self._transaction():
            row = self._owned(item_id, token)
            self._refuse_withdrawn(row)
            question = self.human_question(item_id)
            if not question or question["id"] != question_id:
                raise LedgerError("resolve-question requires the current human_question id")
            for mid in set(message_ids):
                message = self.connection.execute("SELECT author_json FROM inbox WHERE item_id=? AND id=?",
                                                  (item_id, mid)).fetchone()
                if not message or not message["author_json"] or mid <= question["after_message_id"]:
                    raise LedgerError("resolve-question requires this round's actual attributed human replies")
            checkpoint = json.loads(row["checkpoint"])
            checkpoint[QUESTION_KEY] = None
            checkpoint.pop("pending_question", None)
            checkpoint.pop("pending_reason", None)
            self.connection.execute("UPDATE work_items SET checkpoint=?,updated_at=? WHERE id=?",
                                    (_json(checkpoint), self.clock(), item_id))
            self._audit(item_id, "question_resolved", "worker confirmed all questions answered",
                        {"question_id": question_id, "message_ids": sorted(set(message_ids))})
            return self._view(self._row(item_id))

    RESERVATION_OPEN = ("queued", "active", "cancel_requested")

    @staticmethod
    def _reservation_view(row):
        result = {key: row[key] for key in ("reservation_id", "sequence", "item_id", "generation", "kind", "mode",
                                          "resource", "host", "commit_sha", "state", "attempts", "created_at",
                                          "acquired_at", "released_at", "release_reason")}
        result["server_test_plan"] = json.loads(row["server_test_plan"]) if row["server_test_plan"] else None
        return result

    def await_resource(self, item_id, token, resource, mode, *, skill, commit_sha=None, issue_prefix="FARM",
                       comparison_candidate=None, server_test_plan=None, continuation=None, server_base_commit=None):
        """Park the claimed item until the pool grants `resource`.

        The ledger reads no manifests: `skill` is the item's loaded skill, which the worker CLI passes, as for
        handoff_repository. Resources follow the manifest (Phase B plan, P11): the item waits only for a kind its
        skill lists, and the Unity rules read the attempt's root through stages.current_root, so a skill with an
        initial root is never neutral and asks only from a Farm-Client root (spec §9.4). For fix, whose current
        root is its stored root, the rules are the ones it always had.
        """
        _text(resource, "resource")
        if continuation is not None and (resource != 'unity_slot' or mode != 'interactive'
                or comparison_candidate is not None or not isinstance(continuation, tuple) or len(continuation) != 2):
            raise LedgerError('only an interactive candidate can continue its completed baseline')
        if server_base_commit is not None and (resource != "server_slot" or
                not isinstance(server_base_commit, str) or not COMMIT_SHA.fullmatch(server_base_commit)):
            raise LedgerError("server verification base requires a full host-verified server SHA")
        if server_test_plan is not None and resource != "server_slot":
            raise LedgerError("a server test plan requires server_slot")
        try:
            server_plan = validate_plan(FULL_PLAN if server_test_plan is None else server_test_plan)
        except ValueError as exc:
            raise LedgerError(str(exc)) from exc
        if resource == "server_slot" and mode != "batch":
            raise LedgerError("server slots require batch mode")
        if resource != "server_slot" and mode not in ("interactive", "batch"):
            raise LedgerError("mode must be interactive or batch")
        with self._transaction():
            row = self._owned(item_id, token)
            self._refuse_withdrawn(row)
            self.require_valid_checkpoint(item_id, token)
            if skill.name != row["skill"]:
                raise LedgerError("a resource request requires the work item's own skill manifest")
            require_listed_resource(skill, resource)
            root = current_root(row["root_repo"], skill)
            if comparison_candidate is not None and (
                    resource != "unity_slot" or row["skill"] not in ("work", "fix", "feature")
                    or root != "Farm-Client" or not isinstance(comparison_candidate, str)
                    or not COMMIT_SHA.fullmatch(comparison_candidate)):
                raise LedgerError("baseline comparison requires an owned committed Client candidate")
            if resource == "server_slot":
                if row["skill"] not in ("work", "fix", "feature") or root != "farm-hive":
                    raise LedgerError("server integration requires a farm-hive write stage")
                if not isinstance(commit_sha, str) or not COMMIT_SHA.fullmatch(commit_sha):
                    raise LedgerError("server integration requires an explicit committed server HEAD")
                if self.connection.execute("SELECT 1 FROM reservations WHERE item_id=? AND state IN "
                        "('queued','active','cancel_requested')", (item_id,)).fetchone():
                    raise LedgerError("release the resource this item already holds before requesting another")
                reservation_id = str(uuid4())
                self.connection.execute("""INSERT INTO reservations
                    (reservation_id,item_id,generation,kind,mode,commit_sha,state,created_at,server_test_plan)
                    VALUES(?,?,?,?,?,?,'queued',?,?)""",
                    (reservation_id, item_id, row["generation"], resource, mode, commit_sha, self.clock(), _json(server_plan)))
                previous = self.server_verification_round(item_id, commit_sha)
                if previous and server_base_commit is not None and previous['base_commit'] != server_base_commit:
                    raise LedgerError("the server candidate's verification base is immutable")
                if server_base_commit is not None and previous is None:
                    self._audit(item_id, "server_verification_round", "pinned candidate upstream base",
                                {"generation": row["generation"], "candidate_commit": commit_sha,
                                 "base_commit": server_base_commit, "reservation_id": reservation_id})
                self._set_state(item_id, "awaiting_resource", "needs server_slot:batch", token=None,
                                lease_expires_at=None, worker_pid=None, needs_resource="server_slot:batch")
                self._audit(item_id, "reservation", "queued", details={"reservation_id": reservation_id,
                            "mode": mode, "commit_sha": commit_sha})
                return self._view(self._row(item_id))
            if root not in (None, "Farm-Client"):
                raise LedgerError("Unity verification requires the neutral or Farm-Client stage")
            target = json.loads(row["target_json"]) if row["target_json"] else None
            if not target or not COMMIT_SHA.match(target.get("commit_sha") or ""):
                raise LedgerError("a resource request needs a pinned commit; this item has none")
            if row["skill"] in ("work", "feature", "fgui", "scene"):
                if (target.get("repository") != "Farm-Client" or not target.get("issue_branch")
                        or root != "Farm-Client"):
                    raise LedgerError("staged verification requires its controller-selected client baseline")
                if commit_sha is None:
                    raise LedgerError("staged verification requires an explicit committed client HEAD")
                self.staged_client_branch(item_id, issue_prefix=issue_prefix)
            if commit_sha is not None:
                if not isinstance(commit_sha, str) or not COMMIT_SHA.fullmatch(commit_sha):
                    raise LedgerError("verification commit must be a full lowercase commit SHA")
                if (row["skill"] not in WRITE_SKILLS or resource != "unity_slot"
                        or target["repository"] != "Farm-Client" or root != "Farm-Client"):
                    raise LedgerError("only a write worker may select a Farm-Client Unity verification commit")
            selected_commit = target["commit_sha"] if commit_sha is None else commit_sha
            from .resource_recovery import RecoveryStore
            if resource == 'unity_slot' and RecoveryStore(self).rejected_candidate(row['id'], row['generation'], selected_commit):
                raise LedgerError('This Unity candidate already failed preparation. Diagnose the saved candidate_repair, '
                                  'commit corrected inputs at a new SHA, or ask for the specific human decision; '
                                  'do not resubmit the unchanged candidate.')
            if continuation is not None:
                source = self._continuation_baseline(row, continuation[1], selected_commit)
                if source['reservation_id'] != continuation[0]:
                    raise LedgerError('baseline reservation changed during continuation validation')
                self.connection.execute("UPDATE reservations SET state='released',released_at=?,release_reason=? "
                                        "WHERE reservation_id=?", (self.clock(), 'worker reported quiescent', source['reservation_id']))
                self.connection.execute("UPDATE slots SET state='switching',updated_at=? WHERE slot_id=?",
                                        (self.clock(), source['resource']))
                self._audit(item_id, 'reservation', 'released', details={
                    'reservation_id': source['reservation_id'], 'reason': 'worker reported quiescent'})
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
                (reservation_id, row["id"], row["generation"], resource, mode, selected_commit, self.clock()))
            self._set_state(row["id"], "awaiting_resource", f"needs {resource}:{mode}", token=None,
                            lease_expires_at=None, worker_pid=None, needs_resource=f"{resource}:{mode}")
            self._audit(row["id"], "reservation", "queued", details={"reservation_id": reservation_id, "mode": mode,
                        "commit_sha": selected_commit, "baseline_commit": target["commit_sha"],
                        **({"comparison_candidate": comparison_candidate} if comparison_candidate else {}),
                        **({'continuation_of': continuation[0]} if continuation else {})})
            return self._view(self._row(row["id"]))

    def _continuation_baseline(self, item, resource_token, candidate):
        source = self.active_reservation(item['id'])
        if source is None:
            raise LedgerError('continuation requires the still-owned comparison baseline')
        source = self._reservation_owned(source['reservation_id'], resource_token)
        comparison = self.reservation_comparison(source['reservation_id'])
        slot = self.slot(source['resource'])
        if (item['skill'] not in ('work', 'feature', 'fix') or item['root_repo'] != 'Farm-Client'
                or source['generation'] != item['generation'] or source['state'] != 'active'
                or source['kind'] != 'unity_slot' or source['mode'] != 'interactive'
                or not comparison or comparison['candidate_commit'] != candidate or source['commit_sha'] == candidate
                or not slot or slot['state'] != 'interactive_busy' or source['host'] != item['host']):
            raise LedgerError('continuation requires the exact interactive baseline/candidate pair on a healthy slot')
        return source

    def continuation_baseline(self, item_id, token, resource_token, candidate):
        with self._transaction():
            item = self._owned(item_id, token)
            self._refuse_withdrawn(item)
            self.require_valid_checkpoint(item_id, token)
            return self._reservation_view(self._continuation_baseline(item, resource_token, candidate))

    def verification_progress(self, item_id, token, phase, *, report=None):
        from .verification import STAGES
        if phase not in STAGES:
            raise LedgerError('unknown Client verification phase')
        with self._transaction():
            item = self._owned(item_id, token)
            self._refuse_withdrawn(item)
            if item['skill'] not in ('work', 'feature', 'fix') or item['root_repo'] != 'Farm-Client':
                raise LedgerError('verification progress requires a claimed Client code job')
            checkpoint = json.loads(item['checkpoint'])
            checkpoint['stage'] = STAGES[phase]
            self.connection.execute('UPDATE work_items SET stage=?,checkpoint=?,updated_at=? WHERE id=?',
                                    (STAGES[phase], _json(checkpoint), self.clock(), item_id))
            self._audit(item_id, 'verification_progress', phase)
            if report is not None:
                self._audit(item_id, 'verification_comparison', report['verdict'], details={
                    key: report[key] for key in ('draft_eligible', 'full_suite_passed', 'baseline_commit',
                                                'candidate_commit') if key in report})
            return {'stage': STAGES[phase], 'state': item['state']}

    def reservation_comparison(self, reservation_id):
        """Controller-written comparison identity; no schema migration or checkpoint authority."""
        reservation = self.reservation(reservation_id)
        if reservation is None:
            return None
        for row in self.connection.execute(
                "SELECT details FROM audit WHERE item_id=? AND kind='reservation' "
                "AND reason IN ('queued','requeued') ORDER BY id DESC",
                (reservation["item_id"],)):
            details = json.loads(row["details"])
            if details.get("reservation_id") == reservation_id:
                candidate = details.get("comparison_candidate")
                if candidate is None:
                    return None
                if (not isinstance(candidate, str) or not COMMIT_SHA.fullmatch(candidate)
                        or details.get("commit_sha") != reservation["commit_sha"]):
                    raise LedgerError("baseline comparison identity is damaged; preserve recovery evidence")
                return {"verification_role": "baseline", "candidate_commit": candidate}
        return None

    def server_verification_round(self, item_id, candidate, *, require_released=False):
        """Host-recorded ancestry for one exact server candidate, never a test verdict."""
        item = self._row(item_id)
        if item['skill'] not in ('work', 'feature', 'fix') or not isinstance(candidate, str) or not COMMIT_SHA.fullmatch(candidate):
            return None
        for row in self.connection.execute("SELECT details FROM audit WHERE item_id=? AND kind='server_verification_round' ORDER BY id", (item_id,)):
            record = json.loads(row['details'])
            if record.get('generation') != item['generation'] or record.get('candidate_commit') != candidate:
                continue
            if not isinstance(record.get('base_commit'), str) or not COMMIT_SHA.fullmatch(record['base_commit']):
                raise LedgerError("server verification base evidence is damaged")
            original = self.reservation(record.get('reservation_id'))
            if (not original or original['kind'] != 'server_slot' or original['item_id'] != item_id
                    or original['generation'] != item['generation'] or original['commit_sha'] != candidate):
                raise LedgerError("server verification reservation evidence is damaged")
            if require_released:
                released = self.connection.execute("""SELECT reservation_id FROM reservations WHERE item_id=?
                    AND generation=? AND kind='server_slot' AND commit_sha=? AND sequence>=?
                    AND state='released' AND release_reason='worker reported quiescent'
                    ORDER BY sequence DESC LIMIT 1""", (item_id, item['generation'], candidate, original['sequence'])).fetchone()
                if released is None:
                    from .server_recovery import retained_round
                    return retained_round(self, item_id, candidate, record)
                record['released_reservation'] = released['reservation_id']
            return record
        return None

    def client_verification_pair(self, item_id, candidate, *, require_candidate=False, candidate_reservation=None):
        """A settled, controller-selected comparison pins one verification round across main updates.

        This records no test verdict. Full result/affected-scope checks remain required.
        Retry may preserve source evidence; a different item or candidate never inherits it.
        Evidence may select its completed candidate reservation within the current baseline
        round. Later diagnostics do not replace that identity; a newer baseline still does.
        """
        item = self._row(item_id)
        if item["skill"] not in ("work", "feature", "fix") or not isinstance(candidate, str) or not COMMIT_SHA.fullmatch(candidate):
            return None
        rows = [r for r in self.reservations() if r["item_id"] == item_id and r["kind"] == "unity_slot"]
        for baseline in reversed(rows):
            comparison = self.reservation_comparison(baseline["reservation_id"])
            if not comparison or comparison["candidate_commit"] != candidate or baseline["commit_sha"] == candidate:
                continue
            if baseline["state"] != "released" or baseline["release_reason"] != "worker reported quiescent":
                return None  # A newer unfinished/unclean round cannot borrow older evidence.
            tested = next((r for r in reversed(rows) if r["sequence"] > baseline["sequence"]
                           and (candidate_reservation is None or r["reservation_id"] == candidate_reservation)
                           and r["commit_sha"] == candidate and r["state"] == "released"
                           and r["mode"] == baseline["mode"]
                           and r["release_reason"] == "worker reported quiescent"
                           and self.reservation_comparison(r["reservation_id"]) is None), None)
            if (require_candidate or candidate_reservation is not None) and tested is None:
                return None
            return {"baseline_commit": baseline["commit_sha"], "candidate_commit": candidate,
                    "baseline_reservation": baseline["reservation_id"],
                    "candidate_reservation": tested["reservation_id"] if tested else None}
        return None

    def begin_editor_warmup(self, slot_id, *, host):
        """Controller-only claim of an idle project before opening its persistent Editor."""
        with self._transaction():
            slot = self._slot_row(slot_id)
            if (slot["host"] != host or slot["kind"] != "unity_slot" or slot["state"] != "idle_closed"
                    or self.active_reservation_on(slot_id)):
                return None
            self.connection.execute("UPDATE slots SET state='switching',updated_at=? WHERE slot_id=?",
                                    (self.clock(), slot_id))
            self._audit(slot_id, "unity_editor_start", "persistent Editor startup")
            return self.slot(slot_id)

    def interactive_unity_requests(self, host):
        """Retain FIFO, candidates and claims while migrating only ungranted local Unity requests."""
        with self._transaction():
            rows = self.connection.execute("""SELECT r.reservation_id,r.item_id FROM reservations r
                JOIN work_items w ON w.id=r.item_id WHERE r.kind='unity_slot' AND r.mode='batch'
                AND r.state='queued' AND (w.host=? OR w.host IS NULL)""", (host,)).fetchall()
            for row in rows:
                self.connection.execute("UPDATE reservations SET mode='interactive' WHERE reservation_id=?",
                                        (row["reservation_id"],))
                self.connection.execute("""UPDATE work_items SET needs_resource='unity_slot:interactive'
                    WHERE id=? AND state='awaiting_resource'""", (row["item_id"],))
                self._audit(row["item_id"], "unity_mode", "fixed pool converted queued batch to interactive",
                            {"reservation_id": row["reservation_id"]})
            return len(rows)

    def acquire(self, kind, *, owner, host, exclude_resources=(), expected_reservation=None):
        """Grant FIFO, except for a baseline's one-turn continuation on its released slot.

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
            if Maintenance(self).requested():
                return None
            from .resource_queue import continuation_slot, queued_rows
            row = next(iter(queued_rows(self.connection, kind=kind)), None)
            if row is None or expected_reservation is not None and row["reservation_id"] != expected_reservation:
                return None
            # Spec §7 scheduling preference: interactive wants an Editor already open, batch wants none.
            order = ("idle_open", "idle_closed") if row["mode"] == "interactive" else ("idle_closed", "idle_open")
            free = [s for s in self.slots(kind=kind, host=host)
                    if s["state"] in self.FREE_SLOT_STATES and s["slot_id"] not in exclude_resources]
            continued_slot = continuation_slot(self.connection, row)
            if continued_slot:
                free = [s for s in free if s['slot_id'] == continued_slot]
            free.sort(key=lambda s: (order.index(s["state"]), s["slot_id"]))
            if not free:
                return None
            slot = free[0]
            token = "res_" + secrets.token_urlsafe(32)
            try:
                self.connection.execute(
                    """UPDATE reservations SET state='active',resource=?,host=?,owner=?,token_hash=?,acquired_at=?,wait_detail=NULL
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
            if kind == "unity_slot" and row["mode"] == "interactive":
                self.connection.execute("UPDATE work_items SET stage='unity-preparing' WHERE id=? AND state='awaiting_resource'",
                                        (row["item_id"],))
            self._audit(row["item_id"], "reservation", "acquired",
                        details={"reservation_id": row["reservation_id"], "slot": slot["slot_id"]})
            granted = self._reservation_view(self.connection.execute(
                "SELECT * FROM reservations WHERE reservation_id=?", (row["reservation_id"],)).fetchone())
            granted["folder"] = slot["folder"]
            granted["token"] = token  # the only time the raw token exists outside the pool
            return granted

    def pending_unity_workers(self, host):
        """Interactive grants reserve a worker seat through preparation and hand-over.

        Read from this connection so the pool and scheduler can use their own connections
        under the scheduler lock. Existing reservations also reconstruct seats after restart.
        Held/cancelled resources retain their recovery fences, but reserve no future worker.
        """
        pending = {r[0] for r in self.connection.execute("""SELECT r.item_id FROM reservations r
            JOIN work_items w ON w.id=r.item_id JOIN slots s ON s.slot_id=r.resource
            WHERE r.kind='unity_slot' AND r.mode='interactive' AND r.state='active' AND r.host=?
            AND s.state<>'held' AND w.state IN ('queued','awaiting_resource') AND w.withdraw_deadline IS NULL""", (host,))}
        # Keep the departing worker's seat through the short baseline/candidate handoff.
        # The active/claimed old worker and its continuation share one item ID, hence one
        # seat. This does not allocate Unity before teardown or grant queue-wide priority.
        from .resource_queue import continuation_slot, queued_rows
        for row in queued_rows(self.connection, kind='unity_slot'):
            slot_id = continuation_slot(self.connection, row)
            if slot_id and self.slot(slot_id)['host'] == host:
                pending.add(row['item_id'])
        return pending

    def unity_wait_stage(self, reservation_id, stage):
        if stage not in ("waiting-for-worker", "waiting-for-worker-teardown", "waiting-for-unity"):
            raise ValueError("unknown Unity wait stage")
        with self._transaction():
            self.connection.execute("""UPDATE work_items SET stage=? WHERE state='awaiting_resource'
                AND stage<>? AND id IN (SELECT item_id FROM reservations
                    WHERE reservation_id=? AND state='queued' AND kind='unity_slot' AND mode='interactive')""",
                (stage, stage, reservation_id))

    def resource_wait_detail(self, reservation_id, detail):
        """Host-only display observation; never changes a claim, source pin or reservation order."""
        with self._transaction():
            self.connection.execute("UPDATE reservations SET wait_detail=? WHERE reservation_id=? AND state='queued'",
                                    (_json(detail), reservation_id))

    def replan_legacy_server_request(self, reservation_id):
        """Wake an unleased legacy request to declare scope once, preserving its checkpoint and candidate."""
        with self._transaction():
            row = self.connection.execute("SELECT * FROM reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
            if not row or row["kind"] != "server_slot" or row["state"] != "queued" or row["server_test_plan"]:
                return False
            item = self._row(row["item_id"])
            if item["state"] != "awaiting_resource" or item["generation"] != row["generation"] or item["withdraw_deadline"]:
                return False
            self.connection.execute("UPDATE reservations SET state='cancelled',released_at=?,release_reason=?,wait_detail=NULL WHERE reservation_id=?",
                                    (self.clock(), "scope declaration required after upgrade", reservation_id))
            self._set_state(item["id"], "queued", "declare server test scope after upgrade", needs_resource=None,
                            stage="server-test-plan-required")
            self._audit(item["id"], "server_scope_replan", "legacy request resumed without allocating services",
                        {"reservation_id": reservation_id, "commit_sha": row["commit_sha"]})
            return True

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

    def reservation_queue(self, kind):
        from .resource_queue import queued_rows
        return [self._reservation_view(row) for row in queued_rows(self.connection, kind=kind)]

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
        from .resource_queue import last_on_slot
        row = last_on_slot(self.connection, slot_id)
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

    def hold(self, reservation_id, reason, *, recovery_kind='execution'):
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
            if row["kind"] != "server_slot":
                RecoveryStore(self).request_in_transaction(row["resource"], reason, recovery_kind=recovery_kind)
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
            comparison = self.reservation_comparison(reservation_id)
            fresh = str(uuid4())
            self.connection.execute(
                """INSERT INTO reservations(reservation_id,item_id,generation,kind,mode,commit_sha,state,
                   attempts,created_at,server_test_plan) VALUES(?,?,?,?,?,?,'queued',?,?,?)""",
                (fresh, row["item_id"], row["generation"], row["kind"], row["mode"], row["commit_sha"],
                 row["attempts"] + 1, self.clock(), row["server_test_plan"]))
            self._audit(row["item_id"], "reservation", "requeued", details={"reservation_id": fresh,
                "reason": reason[:200], **({"commit_sha": row["commit_sha"],
                "comparison_candidate": comparison["candidate_commit"]} if comparison else {})})
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
            if row["kind"] == "server_slot":
                raise LedgerError("server slots require controller-owned Docker cleanup; use recover-server-slot")
            if self.connection.execute("SELECT 1 FROM resource_recoveries WHERE slot_id=? AND state IN ('pending','repairing')",
                                       (slot_id,)).fetchone():
                raise LedgerError('slot is owned by automatic recovery; cannot bypass worker/editor fencing')
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

    def resume(self, item_id, reason, *, stage=None):
        _text(reason, "reason")
        with self._transaction():
            row = self._row(item_id)
            if row["state"] not in ("awaiting_input", "awaiting_resource"):
                raise LedgerError("only a waiting work item can be resumed")
            self._set_state(row["id"], "queued", reason, needs_resource=None,
                            **({"stage": stage} if stage is not None else {}))
            return self._view(self._row(row["id"]))

    def server_verification_timeout(self, reservation_id, deadline):
        """A technical deadline preserves evidence and never becomes an automatic retry."""
        with self._transaction():
            reservation = self.reservation(reservation_id)
            if not reservation or reservation['kind'] != 'server_slot' or reservation['state'] != 'active':
                return False
            row = self._row(reservation['item_id'])
            if (row['generation'] != reservation['generation'] or row['state'] not in
                    ('queued', 'running', 'awaiting_resource') or self.clock() < deadline):
                return False
            reason = 'server verification deadline reached; inspect retained reports before any retry'
            self.connection.execute('INSERT OR IGNORE INTO job_cleanup(item_id,worker_pid,updated_at) VALUES(?,?,?)',
                                    (row['id'], self.last_worker_pid(row['id']), self.clock()))
            self.connection.execute("""UPDATE reservations
                SET state=CASE WHEN state='queued' THEN 'cancelled' ELSE 'cancel_requested' END,
                    released_at=CASE WHEN state='queued' THEN ? ELSE released_at END,
                    release_reason=CASE WHEN state='queued' THEN ? ELSE release_reason END
                WHERE item_id=? AND state IN ('queued','active')""", (self.clock(), reason, row['id']))
            self._set_state(row['id'], 'blocked', reason, token=None, lease_expires_at=None,
                            worker_pid=None, needs_resource=None, next_root_repo=None,
                            stage='server-verification-timeout')
            self._audit(row['id'], 'server_verification_timeout', reason,
                        {'reservation_id': reservation_id, 'generation': row['generation'],
                         'candidate_commit': reservation['commit_sha'], 'deadline': deadline})
            return True

    def cancel(self, item_id, reason, *, states=None, drop_progress=False, authority=None, created_before=None):
        """`states` cancels only an item in one of those states, in this same transaction, and returns None for any
        other: delegation removal (spec §9.8) cancels queued and waiting work, never an attempt a worker has claimed
        since the caller looked. `authority` and `created_before` recheck the rest of such a caller's decision the
        same way: only an item of that authority (a person's answer may have made a conversation a mention's), and
        only one created before that time (a handover may have moved the work to a job the caller never saw; design
        P3). A handover made at or after `created_before` is new to that caller as well, whatever the age of the job it
        continued, so it cancels nothing: a worker's own read found the card delegated after the caller's began.
        `drop_progress` is for a caller that posts the item's one closing notice itself: the item's pending session
        heartbeat goes in the same transaction, so none follows the notice (design P7)."""
        _text(reason, "reason")
        _checked_guards(authority, created_before)
        with self._transaction():
            row = self._row(item_id)
            # Stop may have selected the source just before the atomic handoff.
            # Resolve that race under this same transaction, before revoking claims.
            if row["skill"] == "chat" and row["state"] == "delivered":
                destination = json.loads(row["evidence"]).get("resumed_item")
                if destination:
                    target = self._row(destination)
                    if target["issue_id"] == row["issue_id"]:
                        # The conversation's updated_at is the handover's time: _repair_work delivers it then.
                        if created_before is not None and row["updated_at"] >= created_before:
                            return None
                        row = target
            if states is not None and row["state"] not in states:
                return None
            if not self._still_decided(row, authority, created_before):
                return None
            if row["state"] == "cancelled":
                return self._view(row)
            if row["state"] not in (*ACTIVE_STATES, "blocked"):
                raise LedgerError("work item is already terminal")
            # A deciding read cannot end the coordinator while a child outside its decision still owns work.
            # Withdrawing parked work also must not revoke a child that claimed since the caller's listing.
            if row["skill"] == "work" and row["parent_id"] is None:
                children = self._unfinished_children(row["id"])
                if any(not self._still_decided(child, authority, created_before) for child in children):
                    return None
                if states is not None and "running" not in states and any(c["state"] == "running" for c in children):
                    return None
            self._cancel_row(row, reason, drop_progress=drop_progress)
            return self._view(self._row(row["id"]))

    @staticmethod
    def _still_decided(row, authority, created_before):
        """Whether `row` still has the `authority` and the creation before `created_before` a caller decided on;
        None skips either check."""
        return ((authority is None or _authority(row) == authority)
                and (created_before is None or row["created_at"] < created_before))

    def _cancel_row(self, row, reason, *, drop_progress):
        """Caller owns the transaction and has checked that `row` is active or blocked: revoke its claim, keep its
        worker's pid for cleanup and cancel it."""
        for child in self.connection.execute("SELECT * FROM work_items WHERE parent_id=? AND state IN "
                                             "('queued','running','awaiting_input','awaiting_resource','blocked')",
                                             (row["id"],)).fetchall():
            self._cancel_row(child, reason, drop_progress=drop_progress)
        pid = self.last_worker_pid(row["id"])
        self.connection.execute(
            "INSERT OR IGNORE INTO job_cleanup(item_id,worker_pid,updated_at) VALUES(?,?,?)",
            (row["id"], pid, self.clock()))
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
                        needs_resource=None, next_root_repo=None)
        # SessionProgress creates its table on the ledger's connection; a ledger it never ran on has none.
        if drop_progress and self.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='session_progress'").fetchone():
            self.connection.execute("DELETE FROM session_progress WHERE item_id=?", (row["id"],))

    def _active_children(self, item_id):
        return [row for row in self._unfinished_children(item_id) if row["state"] in ACTIVE_STATES]

    def _unfinished_children(self, item_id):
        return self.connection.execute("SELECT * FROM work_items WHERE parent_id=? AND state IN "
                                       "('queued','running','awaiting_input','awaiting_resource','blocked')",
                                       (item_id,)).fetchall()

    # A closing activity Linear refused is owed to its thread (silent-delegation design P10, which amends the
    # withdrawn-work design's P7): tried again 1, 2, 4, 8 and 16 minutes apart, about 31 minutes in all, and given up
    # at its sixth refusal. One still owed CLOSURE_WINDOW_SECONDS after the first refusal, because the controller was
    # down meanwhile, is given up untried: the 31 minutes of tries, and 9 more for a busy progress loop.
    CLOSURE_KINDS = ("response", "error")
    CLOSURE_RETRY_SECONDS = 60
    CLOSURE_ATTEMPTS = 6
    CLOSURE_WINDOW_SECONDS = 2400

    def owe_closure(self, session_id, *, issue_id=None, item_id=None, kind, body, error=None):
        """Linear refused the `kind` activity, a response or an error, that closes the thread `session_id` with `body`:
        it is owed to the thread, and the progress loop posts it again (SessionProgress.tick). `item_id` is the job
        it closes the thread for, as it stands now: the row goes as soon as that job's state changes (_set_state), and
        is dropped unposted once a newer job in the thread speaks there itself (closure_superseded). The error a
        failed event was answered with also goes once a later event of the thread is answered (drop_event_error),
        and a response owed for no job once the thread reaches work (closure_superseded). `error` names the refusal.

        A thread has one last word: the row replaces an older one of the thread, and its tries start anew. But what
        closes the thread for a job that ended there is replaced only by other closing words for ended work, or once
        it no longer holds: a row owed for no job, or for a job that goes on, can be dropped by the two rules above,
        which leave those words, and in their place it would leave the thread with neither. Such a row is then not
        owed (silent-delegation design P10). An operator's `local-` job has no Linear thread, so nothing is owed for
        one. Returns the row the thread is owed, or None."""
        _text(session_id, "session_id")
        if session_id.startswith("local-"):
            return None
        if kind not in self.CLOSURE_KINDS:
            raise LedgerError(f"an owed closing activity is one of {', '.join(self.CLOSURE_KINDS)}")
        _text(body, "body")
        with self._transaction():
            state = None
            if item_id is not None:
                row = self._row(item_id)
                issue_id, state = issue_id or row["issue_id"], row["state"]
            elif issue_id is None:
                session = self.connection.execute("SELECT issue_id FROM sessions WHERE session_id=?",
                                                  (session_id,)).fetchone()
                issue_id = session["issue_id"] if session else None
            held = self.connection.execute("SELECT * FROM session_closures WHERE session_id=?",
                                           (session_id,)).fetchone()
            if (held is not None and self._closes_ended_work(held["item_id"], held["item_state"])
                    and not self._closes_ended_work(item_id, state) and not self.closure_superseded(held)):
                return dict(held)
            now = self.clock()
            error = None if error is None else str(error)[:200]
            self.connection.execute("""INSERT OR REPLACE INTO session_closures
                (session_id,issue_id,item_id,item_state,kind,body,attempts,due_at,created_at,last_error,given_up_at)
                VALUES(?,?,?,?,?,?,1,?,?,?,NULL)""",
                                    (session_id, issue_id, item_id, state, kind, body,
                                     now + self.CLOSURE_RETRY_SECONDS, now, error))
            return dict(self.connection.execute("SELECT * FROM session_closures WHERE session_id=?",
                                                (session_id,)).fetchone())

    @staticmethod
    def _closes_ended_work(item_id, state):
        """Whether a closing activity owed for the job `item_id`, recorded in `state`, closes its thread for work that
        ended there: a job's own closing words, not the error of an event or a note owed for no job."""
        return item_id is not None and state not in ACTIVE_STATES

    def due_closure(self, now):
        """The owed closing activity to try next: the one longest due at `now` among those not given up, or None."""
        row = self.connection.execute("""SELECT * FROM session_closures WHERE due_at<=? AND given_up_at IS NULL
            ORDER BY due_at,created_at,session_id LIMIT 1""", (now,)).fetchone()
        return dict(row) if row else None

    def closure_superseded(self, row):
        """Whether the owed closing activity `row` no longer holds: a job was created in its thread after it was owed,
        or its job's state is not the one recorded. Either speaks in the thread itself, and the older closing words
        would follow its own. A state change drops the row when it happens (_set_state), so the recorded state only
        decides for a row the caller still holds, or one left by a revision that does not drop it.

        A response owed for no job, a note that the thread holds no work or a Stop's reply that found nothing to stop,
        also no longer holds while the thread reaches active work, its own or the job its messages were forwarded to:
        it would say the thread has none and close it, and a Stop there must keep reaching that work
        (silent-delegation design §3.5, P5)."""
        if row["item_id"] is not None:
            job = self.connection.execute("SELECT state FROM work_items WHERE id=?", (row["item_id"],)).fetchone()
            if job is None or job["state"] != row["item_state"]:
                return True
        elif row["kind"] == "response" and self.stop_target(row["session_id"])[1] in ("own", "forwarded"):
            return True
        return self.connection.execute("SELECT 1 FROM work_items WHERE session_id=? AND created_at>? LIMIT 1",
                                       (row["session_id"], row["created_at"])).fetchone() is not None

    def drop_closure(self, session_id, *, owed_at=None):
        """Forget the closing activity owed to `session_id`: Linear took it, or it no longer holds. `owed_at`, the
        `created_at` of the row the caller read, leaves alone a newer one that has replaced it since."""
        with self._transaction():
            self.connection.execute("DELETE FROM session_closures WHERE session_id=? AND (? IS NULL OR created_at=?)",
                                    (session_id, owed_at, owed_at))

    def give_up_closure(self, session_id, *, owed_at):
        """Give up the closing activity owed to `session_id` without trying it again: it is older than
        CLOSURE_WINDOW_SECONDS, and posted now it would land long after the words it answers. The row stays, as after
        its sixth refusal, for doctor to list. `owed_at` as for drop_closure."""
        with self._transaction():
            self.connection.execute("UPDATE session_closures SET given_up_at=? WHERE session_id=? AND created_at=? "
                                    "AND given_up_at IS NULL", (self.clock(), session_id, owed_at))

    def drop_event_error(self, session_id):
        """Linear took the answer to a later event of the thread `session_id`: the error an older event of the thread
        failed with, still owed or given up, goes (silent-delegation design P10). It would follow that answer and ask
        for a message again that has been taken since, and the later event need neither change a job's state nor
        start one: a message steered into the running job, or forwarded from a thread that has no job of its own.
        That error is the `error` owed for no job, or for a job that was going on. One owed for a job that had ended
        closes the thread for that job, as a `response` does, and stays until P10's own rules drop it."""
        with self._transaction():
            self.connection.execute(f"""DELETE FROM session_closures WHERE session_id=? AND kind='error'
                AND (item_state IS NULL OR item_state IN ({','.join('?' * len(ACTIVE_STATES))}))""",
                                    (session_id, *ACTIVE_STATES))

    def closure_result(self, session_id, *, sent, error=None, owed_at=None):
        """Record one try of the closing activity owed to `session_id`. `sent`: Linear took it, and the row goes.
        Otherwise the refusal `error` is counted: with the new count n, the next try is due 60 * 2 ** (n - 1) seconds
        from now, and the sixth refusal is the last: the row stays, given up, for doctor to list. `owed_at` as for
        drop_closure."""
        if sent:
            return self.drop_closure(session_id, owed_at=owed_at)
        with self._transaction():
            row = self.connection.execute("""SELECT attempts FROM session_closures WHERE session_id=?
                AND given_up_at IS NULL AND (? IS NULL OR created_at=?)""", (session_id, owed_at, owed_at)).fetchone()
            if row is None:
                return
            attempts, now = row["attempts"] + 1, self.clock()
            error = None if error is None else str(error)[:200]
            if attempts >= self.CLOSURE_ATTEMPTS:
                self.connection.execute("UPDATE session_closures SET attempts=?,last_error=?,given_up_at=? "
                                        "WHERE session_id=?", (attempts, error, now, session_id))
            else:
                self.connection.execute("UPDATE session_closures SET attempts=?,last_error=?,due_at=? "
                                        "WHERE session_id=?",
                                        (attempts, error, now + self.CLOSURE_RETRY_SECONDS * 2 ** (attempts - 1),
                                         session_id))

    def supersede(self, expected_id, expected_states, *, session_id, skill, reason, target=None, authority=None,
                  text=None, author=None, received_at=None, takeover=True, episode=None):
        """A newer session takes the card's work over (design P4), in one transaction: `expected_id`, still in one of
        `expected_states`, is cancelled, and a `skill` item is queued in `session_id` with every message the old one
        had, each with its author and time, then `text`, which `author` wrote and FarmBot received at `received_at`,
        as push_inbox records it. A write item of the old one's skill links it as its predecessor. An item's session
        never changes, so nothing is adopted. StaleRouting when the old item has left those states: the caller
        routes the event again. Returns the views (cancelled, created).

        `takeover` False records that a person's message moved a waiting conversation to its own thread (design C5)
        rather than a new delegation session taking the card over, so that nothing tells the old thread of a
        delegation that does not exist (stop_target).

        `episode` is the `since` of the card's delegation episode that this takeover settles: a delegation Linear
        opened no session for continues the card's work in the thread that work is in, so `session_id` is the old
        item's own (silent-delegation design P12). The episode must still be the waiting one the caller read, and it
        becomes `in_place` with that thread in this same transaction. Otherwise StaleRouting, and nothing changes: a
        read that found the delegation gone again, or another settle, got there first."""
        _text(skill, "skill")
        _text(reason, "reason")
        authority = _checked_authority(skill, authority)
        if episode is not None:
            _seconds(episode, "episode")
        if text is not None:
            _text(text, "text", empty=True)
        author_json = _person_json(author, "message author")
        if received_at is not None and (type(received_at) not in (int, float) or not math.isfinite(received_at)):
            raise LedgerError("received_at must be a finite number of seconds")
        with self._transaction():
            row = self._row(expected_id)
            if row["state"] not in expected_states:
                raise StaleRouting(f"work item {row['id']} is {row['state']} now; route the event again")
            issue = json.loads(self._issue_row(row["issue_id"])["metadata"])
            if episode is not None:
                now = self.clock()
                if not self.connection.execute("""UPDATE delegation_episodes
                        SET state='in_place',session_id=?,settled_at=?,error=NULL,updated_at=?
                        WHERE issue_id=? AND since=? AND state='waiting'""",
                                               (session_id, now, now, row["issue_id"], episode)).rowcount:
                    raise StaleRouting(f"the delegation episode of issue {row['issue_id']} is no longer the waiting "
                                       "one this takeover settles; look at the card again")
            self._cancel_row(row, reason, drop_progress=True)
            created = self._insert_item(issue, session_id, skill, target, authority)
            messages = self.connection.execute(
                "SELECT body,author_json,created_at FROM inbox WHERE item_id=? ORDER BY id", (row["id"],)).fetchall()
            self.connection.executemany("INSERT INTO inbox(item_id,body,author_json,created_at) VALUES(?,?,?,?)",
                                        [(created, m["body"], m["author_json"], m["created_at"]) for m in messages])
            if text and text.strip():
                self.connection.execute("INSERT INTO inbox(item_id,body,author_json,created_at) VALUES(?,?,?,?)",
                                        (created, text, author_json, self.clock() if received_at is None else received_at))
            self._audit(row["id"], "superseded", reason,
                        {"by": created, "session_id": session_id, "takeover": bool(takeover)})
            self._audit(created, "supersedes", reason,
                        {"superseded": row["id"], "session_id": row["session_id"], "takeover": bool(takeover)})
            return self._view(self._row(row["id"])), self._view(self._row(created))

    WITHDRAW_REASONS = ("undelegated", "superseded", "unreachable")
    WITHDRAWN = "delegation withdrawn: save a checkpoint, then run withdraw and exit"

    def flag_withdrawal(self, item_id, reason, deadline, *, authority=None, created_before=None):
        """Tell a claimed worker its work is withdrawn (design P2). It keeps its claim, to save its progress and run
        `withdraw`, and the controller stops it at `deadline`, in epoch seconds. Only the first flag counts: True when
        this call set it, False when the item has one already. A parked general coordinator shares its running
        children's grace, keeping queued and human-waiting tasks intact until the group stops or delegation returns.
        StaleRouting when no worker holds the job, or any child is outside the authority/time guards: guarded cancel
        rechecks the same group, and a later read decides work outside this read's fences."""
        if reason not in self.WITHDRAW_REASONS:
            raise LedgerError(f"withdrawal reason must be one of {', '.join(self.WITHDRAW_REASONS)}")
        if type(deadline) not in (int, float) or not math.isfinite(deadline):
            raise LedgerError("withdrawal deadline must be a finite number of seconds")
        _checked_guards(authority, created_before)
        with self._transaction():
            row = self._row(item_id)
            if not self._still_decided(row, authority, created_before):
                raise StaleRouting(f"work item {row['id']} is no longer the work the caller decided on")
            group = row["skill"] == "work" and row["parent_id"] is None and row["state"] in ACTIVE_STATES
            children = self._active_children(item_id) if group else []
            if group and any(not self._still_decided(child, authority, created_before)
                             for child in self._unfinished_children(item_id)):
                raise StaleRouting("general job contains work outside the caller's deciding read")
            if row["state"] != "running" and not (group and (row["withdraw_deadline"] is not None
                                                            or any(c["state"] == "running" for c in children))):
                raise StaleRouting(f"work item {row['id']} is {row['state']}, not running; cancel it instead")
            if row["withdraw_deadline"] is not None:
                return False
            self._flag_withdrawal_group(row, children, reason, deadline)
            return True

    def _flag_withdrawal_group(self, row, children, reason, deadline):
        """Caller owns the transaction and has fenced every row in this logical job."""
        for current in (row, *children):
            if current["withdraw_deadline"] is not None:
                continue
            self.connection.execute("UPDATE work_items SET withdraw_deadline=?,withdraw_reason=?,updated_at=? WHERE id=?",
                                    (deadline, reason, self.clock(), current["id"]))
            self._audit(current["id"], "withdrawal", reason, {"deadline": deadline})

    def _clear_withdrawals(self, issue_id):
        """Caller owns the transaction. A read found the card delegated again: the flags that loss of delegation set
        on the issue's work go. A takeover by a newer session stays."""
        rows = self.connection.execute("""SELECT * FROM work_items WHERE issue_id=? AND withdraw_reason='undelegated'
            AND state IN ('queued','running','awaiting_input','awaiting_resource')""", (issue_id,)).fetchall()
        for row in rows:
            self.connection.execute("UPDATE work_items SET withdraw_deadline=NULL,withdraw_reason=NULL,updated_at=? "
                                    "WHERE id=?", (self.clock(), row["id"]))
            if row["skill"] == "work" and row["parent_id"] is None and row["state"] == "awaiting_input":
                parked = self.connection.execute("SELECT id,details FROM audit WHERE item_id=? AND kind='withdrawn' "
                                                 "ORDER BY id DESC LIMIT 1", (row["id"],)).fetchone()
                state_change = self.connection.execute("SELECT id FROM audit WHERE item_id=? AND kind IN "
                                                       "('queued','running','awaiting_input','awaiting_resource') "
                                                       "ORDER BY id DESC LIMIT 1", (row["id"],)).fetchone()
                if (parked and state_change and parked["id"] > state_change["id"]
                        and json.loads(parked["details"]).get("waiting_tasks")):
                    self._set_state(row["id"], "queued", "delegation restored after coordinator saved progress",
                                    token=None, lease_expires_at=None, worker_pid=None)
            self._audit(row["id"], "withdrawal", "cleared: the card is delegated again")
        return [row["id"] for row in rows]

    def clear_withdrawals(self, issue_id):
        issue_id = _uuid(issue_id, "issue")
        with self._transaction():
            return self._clear_withdrawals(issue_id)

    def withdraw(self, item_id, token, *, delegated, closed, read_at=None, grace_seconds=DEFAULT_GRACE_SECONDS):
        """A worker ends its own claim as cancelled once its work is withdrawn: flagged, or the card is not delegated
        to this app (`delegated`) or is closed (`closed`), as its CLI has just read. Only work the delegation
        authorised ends with it: a conversation a mention or the operator started continues on an undelegated card
        (design P2, G2). A flag the delegation's loss set does not count once the read finds the card delegated
        again: the flag is cleared and the work continues (design P3). Cancelled, not blocked: a later delegation
        continues the job from its plan (design P2, F14). A general coordinator parks after saving its own progress
        while claimed tasks finish their grace; its returned view is still active, so callers post no closing notice.
        Returns (view, reason): the flag's reason, else `closed`, else `undelegated`.

        `read_at` is when the CLI's read began. A read that finds the delegation and clears the issue's mark records
        with it that the delegation is back, as clear_undelegated does (silent-delegation design §3.1)."""
        if type(delegated) is not bool or type(closed) is not bool:
            raise LedgerError("delegated and closed must be booleans")
        if read_at is not None:
            _seconds(read_at, "read_at")
        if type(grace_seconds) not in (int, float) or not math.isfinite(grace_seconds) or grace_seconds <= 0:
            raise LedgerError("withdrawal grace must be a positive finite number of seconds")
        with self._transaction():
            row = self._owned(item_id, token)
            flag = (row["withdraw_reason"] or "undelegated") if row["withdraw_deadline"] is not None else None
            if flag is not None and not (flag == "undelegated" and delegated and not closed):
                reason = flag
            elif closed:
                reason = "closed"
            elif not delegated and _authority(row) == "delegation":
                reason = "undelegated"
            elif not delegated:
                raise LedgerError("this conversation does not depend on the card's delegation; continue it")
            else:
                # The caller's read found the card delegated. Like any read that does, it clears the issue's mark and
                # the flags the delegation's loss set (design P3): the work goes on. This commits before the refusal.
                self._clear_undelegated(row["issue_id"], read_at)
                reason = None
            if reason is not None:
                if row["skill"] == "work" and row["parent_id"] is None and not closed:
                    children = self._active_children(item_id)
                    if any(c["state"] == "running" for c in children):
                        if row["withdraw_deadline"] is None:
                            self._flag_withdrawal_group(row, children, reason, self.clock() + grace_seconds)
                        # The coordinator saved its own work, but its siblings still need their grace to save theirs.
                        checkpoint = json.loads(row["checkpoint"])
                        for key in ("pending_question", "pending_reason", "waiting_tasks"):
                            checkpoint.pop(key, None)
                        self._set_state(item_id, "awaiting_input", "coordinator saved progress; waiting for task withdrawal",
                                        token=None, lease_expires_at=None, worker_pid=None, checkpoint=_json(checkpoint))
                        self._audit(item_id, "withdrawn", reason, {"waiting_tasks": True})
                        return self._view(self._row(item_id)), reason
                self._cancel_row(row, f"withdrawn by its worker: {reason}", drop_progress=True)
                self._audit(row["id"], "withdrawn", reason)
                return self._view(self._row(row["id"])), reason
        if flag is not None:
            raise LedgerError("the card is delegated to this app again, so its withdrawal is cleared; continue the work")
        raise LedgerError("the card is still delegated to this app and nothing withdrew this work; continue it")

    def _refuse_withdrawn(self, row):
        if row["withdraw_deadline"] is not None or (row["parent_id"] and self._row(row["parent_id"])["withdraw_deadline"] is not None):
            raise LedgerError(self.WITHDRAWN)

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

    def defer_capacity_retry(self, item_id, expected_pid):
        """Host-only recovery for a confirmed capacity exit; keep all issue work and reservations.

        This does not retire/remove a worktree or assert descendant cleanup. Like expired-lease
        recovery it revokes the old claim, so only the next worker can write through the ledger.
        """
        delays = (60, 180, 600)
        with self._transaction():
            row = self._row(item_id)
            if (row["state"] not in ("running", "queued") or not expected_pid
                    or row["worker_pid"] != expected_pid or row["capacity_retries"] >= len(delays)):
                return None
            if not _in_scope(json.loads(self._issue_row(row["issue_id"])["metadata"])):
                return None
            delay = delays[row["capacity_retries"]]
            self._set_state(item_id, "queued", "model capacity; delayed automatic retry",
                            token=None, lease_expires_at=None, worker_pid=None,
                            capacity_retries=row["capacity_retries"] + 1,
                            retry_not_before=self.clock() + delay)
            return {"attempt": row["capacity_retries"] + 1, "delay_seconds": delay}

    def defer_publication_retry(self, item_id, token, repo):
        """Only reached after fresh publication checks exhaust transient transport retries."""
        delays = (60, 180, 600)
        with self._transaction():
            row = self._owned(item_id, token)
            attempts = row["publication_retries"]
            if attempts >= len(delays):
                self._set_state(item_id, "failed", "publication transport automatic retries exhausted (3)",
                                token=None, lease_expires_at=None, worker_pid=None)
                return {"status": "retry_exhausted", "repository": repo, "attempts": attempts,
                        "message": "Publication checks remain unavailable; no publication authorized. Work preserved; exit worker."}
            delay = delays[attempts]
            self._set_state(item_id, "queued", "publication transport unavailable; delayed automatic retry",
                            token=None, lease_expires_at=None, worker_pid=None,
                            publication_retries=attempts + 1, retry_not_before=self.clock() + delay)
            return {"status": "retry_queued", "repository": repo, "attempt": attempts + 1,
                    "delay_seconds": delay, "message": "Work preserved; claim retired. Exit worker; do not publish or request human input."}

    def retry(self, item_id, reason):
        _text(reason, "reason")
        with self._transaction():
            row = self._row(item_id)
            if row["state"] not in ("blocked", "delivered", "cancelled", "failed"):
                raise LedgerError("retry requires a terminal work item")
            if not _in_scope(json.loads(self._issue_row(row["issue_id"])["metadata"])):
                raise LedgerError("issue is archived or in a terminal status")
            if row["parent_id"]:
                if self._row(row["parent_id"])["state"] not in ACTIVE_STATES:
                    raise LedgerError("presentation retry requires its active general job")
                if not (self.cleanup_record(row["id"]) or {}).get("done"):
                    raise LedgerError("presentation retry waits for certified teardown")
            try:
                if row["state"] == "cancelled" and not row["parent_id"]:
                    return self._view(self._row(self._cancelled_successor(row, reason)))
                self._guard_cleanup_retry(row["id"])
                # A retry is a person's decision to run the job again, so a withdrawal it outlived goes: queue()
                # would never launch it otherwise.
                self._set_state(row["id"], "queued", reason, worker_pid=None, generation=row["generation"] + 1,
                                requeue_requested=0, capacity_retries=0, publication_retries=0, retry_not_before=0,
                                root_repo=None, next_root_repo=None, withdraw_deadline=None, withdraw_reason=None)
                self.connection.execute('DELETE FROM resource_job_retries WHERE item_id=?', (row['id'],))
            except sqlite3.IntegrityError:
                raise LedgerError("another active work item exists for this issue")
            return self._view(self._row(row["id"]))

    def _resumable_work(self, issue_id, session_id):
        """The delegation's own earlier write job, which a request in a conversation continues whatever the card's
        label now says: the latest fix or feature job of a delegation session on the issue, this conversation's
        session first (spec §9.4). The CLI continues it only where the host runs its skill."""
        skills = ",".join("?" * len(CONVERSATION_SKILLS))
        return self.connection.execute(f"""SELECT w.* FROM work_items w JOIN sessions s
            ON s.session_id=w.session_id WHERE w.issue_id=? AND w.parent_id IS NULL AND w.skill IN ({skills}) AND s.delegation=1
            AND w.state IN ('blocked','delivered','cancelled','failed')
            ORDER BY (w.session_id=?) DESC,w.created_at DESC,w.rowid DESC LIMIT 1""",
            (issue_id, *CONVERSATION_SKILLS, session_id)).fetchone()

    # The delegate a caller has not read afresh: the stored snapshot's.
    _STORED = object()

    def resume_work(self, item_id, token, message_id, app_user_id, *, delegate_id=_STORED):
        """Compatibility command: only resume previously delegated repair work."""
        return self._repair_work(item_id, token, message_id, app_user_id, allow_start=False,
                                 summary="Continued previously delegated work", delegate_id=delegate_id)

    def request_repair(self, item_id, token, message_id, app_user_id, summary, *, start_skill="fix",
                       delegate_id=_STORED):
        """Request writable execution after interpreting the current conversation. `start_skill` is the job a first
        start creates, the one the card's Bot label names (D18 f), which the CLI passes; a request continues the
        delegation's earlier job, whatever it names. `delegate_id` is the card's delegate as the caller has just read
        it, which decides over the stored snapshot's (design J4)."""
        _text(summary, "repair summary")
        if len(summary) > 8000:
            raise LedgerError("repair summary must be at most 8000 characters")
        return self._repair_work(item_id, token, message_id, app_user_id, allow_start=True, summary=summary,
                                 start_skill=start_skill, delegate_id=delegate_id)

    def _delegation_session(self, issue_id, preferred):
        return self.connection.execute("""SELECT * FROM sessions WHERE issue_id=? AND delegation=1
            ORDER BY (session_id=?) DESC,created_at DESC,rowid DESC LIMIT 1""", (issue_id, preferred)).fetchone()

    def _owner_delegation(self, issue_id):
        """The latest delegation session a person could have opened. `agent.service enqueue` mints a `local-`
        session in the ledger, not in Linear (even when the enqueue is then refused): it delegates to nobody and
        takes the issue from nobody, so it never decides the owner. Authority still uses _delegation_session."""
        return self.connection.execute("""SELECT * FROM sessions WHERE issue_id=? AND delegation=1
            AND session_id NOT LIKE 'local-%' ORDER BY created_at DESC,rowid DESC LIMIT 1""", (issue_id,)).fetchone()

    def _repair_work(self, item_id, token, message_id, app_user_id, *, allow_start, summary, start_skill="fix",
                     delegate_id=_STORED):
        """Atomically retire read-only execution and queue its authorized repair.

        Intent belongs to the worker; the CLI checks fresh Linear state. The
        transaction fences claim ownership, newer input, provenance and concurrency.
        """
        if start_skill not in CONVERSATION_SKILLS:
            raise LedgerError(f"a conversation starts only {' or '.join(CONVERSATION_SKILLS)} work")
        with self._transaction():
            chat = self._owned(item_id, token)
            if chat["skill"] != "chat":
                raise LedgerError("repair transition requires an owned read-only chat item")
            issue = json.loads(self._issue_row(chat["issue_id"])["metadata"])
            # The stored snapshot keeps an older delegate when a read's updatedAt did not move, so a fresh read decides.
            delegate = issue.get("delegate_id") if delegate_id is self._STORED else delegate_id
            if not _in_scope(issue) or not app_user_id or delegate != app_user_id:
                raise LedgerError("issue must remain open and delegated to FarmBot")
            messages = self.connection.execute(
                "SELECT id,body,author_json,created_at FROM inbox WHERE item_id=? ORDER BY id", (item_id,)).fetchall()
            if message_id not in {m["id"] for m in messages if m["body"].strip() not in ("", "（无正文）")}:
                raise LedgerError("repair transition requires a real message from this chat")
            if message_id != messages[-1]["id"]:
                raise LedgerError("repair transition requires the latest message; reread the conversation")
            work = self._resumable_work(chat["issue_id"], chat["session_id"])
            if work is None:
                if not allow_start:
                    raise LedgerError("no previously delegated fix work on this issue")
                authority = self._delegation_session(chat["issue_id"], chat["session_id"])
                if authority is None:
                    raise LedgerError("a recorded delegation session on this issue is required")
            # Retire the chat first so the one-active-item index permits the fix, atomically.
            self._set_state(item_id, "delivered", "handed conversation to repair execution",
                            token=None, lease_expires_at=None, worker_pid=None)
            if work is None:
                destination, now = str(uuid4()), self.clock()
                # Plan P6: the session's Farm-Client target is a fix's reproduction baseline; a feature job takes none.
                target = authority["target_json"] if start_skill == "fix" else None
                self.connection.execute("""INSERT INTO work_items
                    (id,issue_id,session_id,skill,state,priority,target_json,created_at,updated_at,authority)
                    VALUES(?,?,?,?,'queued',?,?,?,?,'delegation')""",
                    (destination, chat["issue_id"], authority["session_id"], start_skill, issue["priority"] or 5,
                     target, now, now))
                self._audit(destination, "create", "conversation requested first repair" if start_skill == "fix"
                            else f"conversation requested a first {start_skill} job")
            elif work["state"] == "cancelled":
                destination = self._cancelled_successor(work, "human requested continuation via chat")
            else:
                destination = work["id"]
                self._guard_cleanup_retry(destination)
                self._set_state(destination, "queued", "human requested continuation via chat",
                                token=None, lease_expires_at=None, worker_pid=None,
                                generation=work["generation"] + 1, requeue_requested=0,
                                capacity_retries=0, publication_retries=0, retry_not_before=0,
                                root_repo=None, next_root_repo=None, withdraw_deadline=None, withdraw_reason=None)
                self.connection.execute('DELETE FROM resource_job_retries WHERE item_id=?', (destination,))
            self.connection.execute("UPDATE work_items SET evidence=? WHERE id=?", (
                _json({"summary": summary, "prs": [],
                       "resumed_item": destination, "message_id": message_id}), item_id))
            # Each copy keeps its author and the time it was received, which date a ruling given in that message.
            self.connection.executemany("INSERT INTO inbox(item_id,body,author_json,created_at) VALUES(?,?,?,?)",
                                        [(destination, m["body"], m["author_json"], m["created_at"]) for m in messages])
            self._audit(destination, "resume_request" if work else "repair_request",
                        details={"chat_item": item_id, "message_id": message_id, "summary": summary})
            return self._view(self._row(destination))

    def _cancelled_successor(self, previous, reason):
        """Caller owns the transaction; no execution fields are inherited. A write job's successor reports in the
        card's latest delegation session a person opened, which a re-delegation may have replaced since the job
        started (design C7); an operator's `local-` job keeps its own session. A conversation keeps its session and
        its authority: it may be a mention's, which no delegation session carries."""
        session_id = previous["session_id"]
        if previous["skill"] in WRITE_SKILLS and not str(session_id).startswith("local-"):
            latest = self._owner_delegation(previous["issue_id"])
            session_id = latest["session_id"] if latest is not None else session_id
        item_id, now = str(uuid4()), self.clock()
        self.connection.execute("""INSERT INTO work_items
            (id,issue_id,session_id,skill,state,priority,target_json,predecessor_id,created_at,updated_at,authority)
            VALUES(?,?,?,?,'queued',?,?,?,?,?,?)""",
            (item_id, previous["issue_id"], session_id, previous["skill"], previous["priority"],
             previous["target_json"], previous["id"], now, now, _authority(previous)))
        self._audit(item_id, "create", reason, {"predecessor_id": previous["id"], "session_id": session_id})
        return item_id

    def _guard_cleanup_retry(self, item_id):
        record = self.cleanup_record(item_id)
        if record and record["removing"]:
            raise LedgerError("worktree removal is in progress; retry shortly")
        self.connection.execute("UPDATE job_cleanup SET done=0 WHERE item_id=?", (item_id,))

    def begin_cleanup_removal(self, item_id):
        with self._transaction():
            if self._row(item_id)["state"] in ACTIVE_STATES:
                raise LedgerError("active work cannot be removed")
            self.connection.execute("UPDATE job_cleanup SET removing=1 WHERE item_id=?", (item_id,))

    def last_worker_pid(self, item_id):
        row = self._row(item_id)
        if row["worker_pid"] is not None:
            return row["worker_pid"]
        last = self.connection.execute("SELECT reason FROM audit WHERE item_id=? AND kind='worker' ORDER BY id DESC LIMIT 1",
                                       (item_id,)).fetchone()
        match = re.fullmatch(r"pid ([0-9]+) on .+", last["reason"]) if last else None
        return int(match[1]) if match else None

    def cleanup_record(self, item_id):
        row = self.connection.execute("SELECT * FROM job_cleanup WHERE item_id=?", (item_id,)).fetchone()
        return {**dict(row), "result": json.loads(row["result"]), "done": bool(row["done"])} if row else None

    def cleanup_boot_proof(self, item_id):
        row = self.connection.execute("SELECT details FROM audit WHERE item_id=? AND kind='cleanup_recovery' "
                                      "ORDER BY id DESC LIMIT 1", (item_id,)).fetchone()
        return json.loads(row["details"]) if row else None

    def record_cleanup(self, item_id, result, error=None, *, done=False):
        if not isinstance(result, dict):
            raise LedgerError("cleanup result must be an object")
        with self._transaction():
            row = self._row(item_id)
            if row["state"] in ACTIVE_STATES:
                raise LedgerError("active work cannot be cleaned up")
            self.connection.execute("""INSERT INTO job_cleanup(item_id,worker_pid,result,error,done,updated_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(item_id) DO UPDATE SET result=excluded.result,
                error=excluded.error,done=excluded.done,updated_at=excluded.updated_at,removing=0""",
                (item_id, self.last_worker_pid(item_id), _json(result), error, int(done), self.clock()))

    def recovery_context(self, item_id):
        row = self._row(item_id)
        if not row["predecessor_id"]:
            return None
        previous = self._row(row["predecessor_id"])
        return {"predecessor_id": previous["id"], "checkpoint": json.loads(previous["checkpoint"]),
                "evidence": json.loads(previous["evidence"]), "cleanup": self.cleanup_record(previous["id"]),
                "plan": self._predecessor_plan(previous), "notices": self._predecessor_notices(previous),
                "revalidation_required": True}

    def _predecessor_notices(self, row):
        """Every notice up this predecessor chain, the nearest predecessor's first: a successor of cancelled work
        reads which rounds were posted before it and never posts one of them again."""
        found, seen = [], set()
        while row is not None and row["id"] not in seen:
            seen.add(row["id"])
            found += [{"item_id": row["id"], **{key: notice[key] for key in
                                                ("request_id", "kind", "remote_id", "created_at", "confirmed_at")}}
                      for notice in self.notices(row["id"])]
            row = (self.connection.execute("SELECT * FROM work_items WHERE id=?", (row["predecessor_id"],)).fetchone()
                   if row["predecessor_id"] else None)
        return found

    def _predecessor_plan(self, row):
        """The plan of the nearest item up this predecessor chain that saved one. A successor stopped before
        its first checkpoint saved none, and must not hide the plan of the job it continued."""
        seen = set()
        while row is not None and row["id"] not in seen:
            seen.add(row["id"])
            plan = json.loads(row["checkpoint"]).get("plan")
            if plan is not None:
                return plan
            row = (self.connection.execute("SELECT * FROM work_items WHERE id=?", (row["predecessor_id"],)).fetchone()
                   if row["predecessor_id"] else None)
        return None

    def recorded_branches(self, item_id, *, issue_prefix="FARM"):
        """{repository: branch}: the issue branches the job's plan records (spec §5.7 "Re-attachment"), from the
        item's own plan, else the plan of the nearest predecessor that saved one, as recovery.plan is found. The
        checkpoint refused a plan that breaks plan_issue_branches' rules (P9); they are applied again here, because
        the scheduler checks out what this returns and the ledger file is in a directory every worker can write."""
        row = self._row(item_id)
        identifier = json.loads(self._issue_row(row["issue_id"])["metadata"])["identifier"]
        return plan_issue_branches(self._predecessor_plan(row), identifier, issue_prefix,
                                   reserved=row["skill"] in STAGE_ALLOWANCE_SKILLS)

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
            if row["parent_id"]:
                raise LedgerError("presentation tasks report to their parent; only the general job posts outcome comments")
            if not _in_scope(json.loads(self._issue_row(row["issue_id"])["metadata"])):
                raise LedgerError("issue left scope; do not post a new comment")
            key = f"{row['issue_id']}:{row['claimed_fingerprint']}:{row['generation']}:{kind}"
            action_id = hashlib.sha256(key.encode("utf-8")).hexdigest()
            marker = f"[farmbot:{action_id}]"
            clean_body = _without_markers(body)
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

    def prepare_notice(self, item_id, token, kind, request_id, body):
        """Record one notice per work item and request id (spec §9.4).

        The outbox key moves with the claimed input and the generation, so a changed issue gets a new
        started, blocker or delivery comment. A question round or a pause must reach the issue once however
        often the issue changes, and a later round needs a comment of its own, so the worker names each one.
        The same id and body return the recorded notice, posted or not, which is how a retried attempt
        reuses it; the same id with other words is refused rather than silently kept or replaced.
        """
        if kind not in NOTICE_KINDS:
            raise LedgerError(f"notice kind must be one of {', '.join(NOTICE_KINDS)}")
        if not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id):
            raise LedgerError("request id must be 1-64 ASCII letters, digits, '.', '_' or '-'")
        _text(body, "notice body")
        clean_body = _without_markers(body)
        _text(clean_body, "notice body")
        with self._transaction():
            row = self._owned(item_id, token)
            if not _in_scope(json.loads(self._issue_row(row["issue_id"])["metadata"])):
                raise LedgerError("issue left scope; do not post a new notice")
            action_id = hashlib.sha256(f"notice:{row['id']}:{request_id}".encode("utf-8")).hexdigest()
            marker = f"[farmbot:{action_id}]"
            full_body = f"{clean_body}\n\n{marker}"
            existing = self.notice(row["id"], request_id)
            if existing is None:
                self.connection.execute("""INSERT INTO notices(item_id,request_id,issue_id,kind,marker,body,created_at)
                    VALUES(?,?,?,?,?,?,?)""", (row["id"], request_id, row["issue_id"], kind, marker, full_body, self.clock()))
                self._audit(row["id"], "prepare_notice", kind, {"request_id": request_id})
            elif existing["kind"] != kind or existing["body"] != full_body:
                raise LedgerError(f"request id {request_id} already holds a different notice; "
                                  "post that one or choose a new request id")
            return self.notice(row["id"], request_id)

    def confirm_notice(self, item_id, request_id, remote_id):
        """Record the comment a notice became, once: the caller's readback, as for confirm_comment."""
        _text(remote_id, "remote_id")
        with self._transaction():
            notice = self.notice(item_id, request_id)
            if notice is None:
                raise LedgerError("unknown notice for this work item")
            if notice["remote_id"] is not None and notice["remote_id"] != remote_id:
                raise LedgerError("notice already confirmed with a different remote id")
            if notice["remote_id"] is None:
                self.connection.execute("UPDATE notices SET remote_id=?,confirmed_at=? WHERE item_id=? AND request_id=?",
                                        (remote_id, self.clock(), item_id, request_id))
                self._audit(item_id, "confirm_notice", notice["kind"], {"request_id": request_id, "remote_id": remote_id})
            return self.notice(item_id, request_id)

    def notice(self, item_id, request_id):
        row = self.connection.execute("SELECT * FROM notices WHERE item_id=? AND request_id=?",
                                      (item_id, request_id)).fetchone()
        return dict(row) if row else None

    def notices(self, item_id):
        return [dict(r) for r in self.connection.execute(
            "SELECT * FROM notices WHERE item_id=? ORDER BY created_at,request_id", (item_id,))]

    def validate_finish_input(self, item_id, token, outcome, evidence):
        """Local shape only, usable before delivery exists; never certifies live gates."""
        row = self._owned(item_id, token)
        if outcome not in ('blocked', 'delivered'):
            raise LedgerError('outcome must be blocked or delivered')
        if not isinstance(evidence, dict):
            raise LedgerError('finish input must be an object')
        _text(evidence.get('summary'), 'summary')
        if outcome == 'delivered':
            _text(evidence.get('verification'), 'verification')
            prs = evidence.get('prs') or []
            if not isinstance(prs, list) or any(not isinstance(pr, str) or not self.PR_URL.fullmatch(pr) for pr in prs):
                raise LedgerError('prs must be an array of canonical HTTPS pull request URLs')
            if row['skill'] == 'chat':
                if evidence.get('comment_action_id') is not None or prs:
                    raise LedgerError('chat deliveries carry no issue comment and no PR')
            elif evidence.get('no_change') is not None:
                _text(evidence['no_change'], 'no_change')
                if prs:
                    raise LedgerError('a no_change delivery carries no PR')
            elif not prs:
                raise LedgerError('delivered finish requires a nonempty prs array or a no_change reason')
        if evidence.get('comment_action_id') is not None:
            _text(evidence['comment_action_id'], 'comment_action_id')

    def finish(self, item_id, token, outcome, evidence):
        if outcome not in ("blocked", "delivered"):
            raise LedgerError("outcome must be blocked or delivered")
        if not isinstance(evidence, dict):
            raise LedgerError("finish input must be an object")
        _text(evidence.get("summary"), "summary")
        with self._transaction():
            self.require_valid_checkpoint(item_id, token)
            row = self._owned(item_id, token)
            if row["skill"] == "work":
                from .workflow import task_views
                tasks = task_views(self, item_id)
                if outcome == "delivered" and any(t["state"] in ACTIVE_STATES for t in tasks):
                    raise LedgerError("finish waits for all presentation tasks to settle")
                if outcome == "delivered" and any(t["state"] != "delivered" for t in tasks):
                    raise LedgerError("delivery requires every required presentation task to be delivered")
                if outcome == "delivered" and any(not (self.cleanup_record(t["id"]) or {}).get("done") for t in tasks):
                    raise LedgerError("general delivery waits for certified presentation teardown")
                if outcome == "delivered":
                    task_prs = set()
                    for task in tasks:
                        task_urls = task["evidence"].get("prs")
                        task_urls = [] if task_urls is None else task_urls
                        if not isinstance(task_urls, list) or any(not isinstance(pr, str) or not self.PR_URL.fullmatch(pr)
                                                                  for pr in task_urls):
                            raise LedgerError("task evidence must contain an array of canonical PR URLs")
                        task_prs.update(task_urls)
                    prs = evidence.get("prs") or []
                    if not isinstance(prs, list) or any(not isinstance(pr, str) for pr in prs):
                        raise LedgerError("prs must be an array of PR URLs")
                    if not task_prs.issubset(set(prs)):
                        raise LedgerError("general delivery must include every presentation task's draft PRs")
            chat = row["skill"] == "chat"
            if row["skill"] == "fgui" and outcome == "delivered" and not evidence.get("no_change"):
                proofs = [json.loads(proof["details"]) for proof in self.connection.execute(
                    "SELECT details FROM audit WHERE item_id=? AND kind='fgui_delivery_verified'", (item_id,))]
                if not any(proof.get("delivery_id") == evidence.get("ui_delivery_id")
                           and proof.get("generation") == row["generation"]
                           and proof.get("fingerprint") == row["claimed_fingerprint"]
                           and proof.get("prs") == sorted(evidence.get("prs") or []) for proof in proofs):
                    raise LedgerError("full UI delivery requires current controller verification of both drafts, guards and Unity loading")
            if row["parent_id"]:
                evidence = dict(evidence)
                task_urls = evidence.get("prs")
                evidence["prs"] = [] if task_urls is None else task_urls
                if not isinstance(evidence["prs"], list) or any(not isinstance(pr, str) or not self.PR_URL.fullmatch(pr)
                                                                for pr in evidence["prs"]):
                    raise LedgerError("task PRs must be canonical HTTPS pull request URLs")
                if outcome == "delivered":
                    _text(evidence.get("verification"), "verification")
                    prs = evidence.get("prs") or []
                    if not isinstance(prs, list) or any(not isinstance(pr, str) or not self.PR_URL.fullmatch(pr) for pr in prs):
                        raise LedgerError("task PRs must be canonical HTTPS pull request URLs")
                    if evidence.get("no_change"):
                        _text(evidence["no_change"], "no_change")
                        if prs:
                            raise LedgerError("a no_change task carries no PR")
                    elif not prs:
                        raise LedgerError("task delivery requires PRs or an explicit no_change reason")
                    if row["skill"] == "scene":
                        proofs = [json.loads(proof["details"]) for proof in self.connection.execute(
                            "SELECT details FROM audit WHERE item_id=? AND kind='scene_delivery_verified'", (item_id,))]
                        if not any(proof.get("delivery_id") == evidence.get("scene_delivery_id")
                                   and proof.get("generation") == row["generation"]
                                   and proof.get("fingerprint") == row["claimed_fingerprint"]
                                   and proof.get("baseline") == row["task_client_base"]
                                   and proof.get("prs") == prs
                                   and proof.get("no_change") == evidence.get("no_change") for proof in proofs):
                            raise LedgerError("scene delivery requires current controller verification of its clean Client and draft")
            elif chat and outcome == "delivered":
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
            if row["withdraw_deadline"] is not None and outcome == "blocked":
                # Withdrawn work that stops short of a delivery ends cancelled, not blocked: a later delegation then
                # continues it from its plan (design F14). The worker's evidence stays on the item.
                self.connection.execute("UPDATE work_items SET evidence=? WHERE id=?", (_json(evidence), row["id"]))
                self._cancel_row(row, f"withdrawn ({row['withdraw_reason']}); its worker finished blocked",
                                 drop_progress=False)
                return self._view(self._row(row["id"]))
            current = self._issue_row(row["issue_id"])["fingerprint"]
            changed = current != row["claimed_fingerprint"] or row["requeue_requested"]
            state = "queued" if changed else outcome
            self._set_state(row["id"], state, evidence["summary"], token=None, lease_expires_at=None, worker_pid=None,
                            evidence=_json(evidence), generation=row["generation"] + int(state == "queued"))
            return self._view(self._row(row["id"]))

    def push_inbox(self, item_id, body, *, resume_waiting=False, author=None, received_at=None,
                   demote_to_mention=False):
        """author: the person who wrote the message (a session reply's prompting user, or the creator of the mention
        session it opened), or None when unknown. received_at: when FarmBot received it, in epoch seconds (the
        receiver passes its webhook's time, which a pending event keeps across a restart), else now.

        demote_to_mention: a person answers a delegation's conversation on a card no longer delegated here. The
        conversation goes on as one that needs no delegation, so the withdrawal of the delegation's work leaves it
        (design P1). StaleRouting when the item has ended: the caller routes the message again (design J5)."""
        _text(body, "body")
        author_json = _person_json(author, "message author")
        if received_at is not None and (type(received_at) not in (int, float) or not math.isfinite(received_at)):
            raise LedgerError("received_at must be a finite number of seconds")
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
                raise StaleRouting("cannot steer a terminal work item")
            self.connection.execute("INSERT INTO inbox(item_id,body,author_json,created_at) VALUES(?,?,?,?)",
                                    (row["id"], body, author_json, self.clock() if received_at is None else received_at))
            self._audit(row["id"], "inbox", "steering message")
            if demote_to_mention and row["skill"] == "chat" and _authority(row) == "delegation":
                self.connection.execute("UPDATE work_items SET authority='mention',updated_at=? WHERE id=?",
                                        (self.clock(), row["id"]))
                self._audit(row["id"], "authority", "a person answered it on a card no longer delegated here",
                            {"from": "delegation", "to": "mention"})
            if resume_waiting and row["state"] == "awaiting_input":
                # A resume from a human gate, by a reply or a forwarded mention: a new stage (spec §5.8).
                self._set_state(row["id"], "queued", "human answered in Linear", token=None,
                                lease_expires_at=None, worker_pid=None, resume_authorized=1,
                                **self._new_stage_allowances(row))
            return {"item_id": row["id"], "state": self._row(row["id"])["state"], "pending": self.connection.execute(
                "SELECT count(*) FROM inbox WHERE item_id=? AND consumed_at IS NULL", (row["id"],)).fetchone()[0]}

    def pop_inbox(self, item_id, token):
        with self._transaction():
            row = self._owned(item_id, token)
            rows = self.connection.execute("SELECT id,body FROM inbox WHERE item_id=? AND consumed_at IS NULL ORDER BY id", (row["id"],)).fetchall()
            self.connection.execute("UPDATE inbox SET consumed_at=? WHERE item_id=? AND consumed_at IS NULL", (self.clock(), row["id"]))
            return [r["body"] for r in rows]

    def issue_context(self, item_id):
        """This item's issue, logical job and durable evidence; never claim tokens."""
        row = self._row(item_id)
        issue_row = self._issue_row(row["issue_id"])
        issue = json.loads(issue_row["metadata"])
        checkpoint = json.loads(row["checkpoint"])
        meta = checkpoint.get("handoff_meta")
        handoff = None
        if "handoff" in checkpoint and meta:
            handoff = {"content": checkpoint["handoff"], "recorded_at": meta["recorded_at"],
                       "stale": meta["fingerprint"] != issue_row["fingerprint"] or meta["generation"] != row["generation"]
                                or bool(row["requeue_requested"]),
                       "revalidation_required": True, "source": "previous_worker_checkpoint"}
        view = self._view(row)
        coordination = {key: view[key] for key in ("id", "identifier", "skill", "state", "stage", "generation",
                                                  "root_repo", "next_root_repo", "target", "authority")}
        # A withdrawn item's worker saves its progress and runs `withdraw` (design P2).
        coordination["withdrawn"] = view["withdraw_deadline"] is not None
        coordination['candidate_repair'] = view['candidate_repair']
        authority = self._delegation_session(row["issue_id"], row["session_id"])
        owner = _owner(issue, self._owner_delegation(row["issue_id"]))
        from .workflow import task_views
        return {"issue": issue, "fingerprint": issue_row["fingerprint"], "coordination": coordination,
                "tasks": task_views(self, item_id) if row["skill"] == "work" or row["parent_id"] else [],
                "parent_job": self._view(self._row(row["parent_id"])) if row["parent_id"] else None,
                "predecessor_tasks": task_views(self, row["predecessor_id"]) if row["skill"] == "work" and row["predecessor_id"] else [],
                "handoff": handoff, "conversation_history": self._conversation_history(row["issue_id"]),
                "delegation_session": authority["session_id"] if authority else None,
                "owner": owner, "creator": _issue_creator(issue, owner),
                "resource_recovery": {"attempts": RecoveryStore(self).job_attempts(item_id),
                                      "setup_attempts": RecoveryStore(self).job_attempts(item_id, recovery_kind='setup'),
                                      "records": [dict(r) for r in self.connection.execute(
                                          'SELECT id,slot_id,state,attempts,recovery_kind,reason,evidence,error FROM resource_recoveries WHERE item_id=? ORDER BY created_at', (item_id,))]},
                "checkpoint_error": self.checkpoint_error(item_id),
                "cleanup": self.cleanup_record(item_id),
                "recovery": self.recovery_context(item_id),
                "resumable_work": (self._view(candidate) if row["skill"] == "chat"
                                   and (candidate := self._resumable_work(row["issue_id"], row["session_id"])) else None),
                "session_messages": [_message(r) for r in self.connection.execute(
                    "SELECT id,body,author_json,created_at FROM inbox WHERE item_id=? ORDER BY id", (item_id,))],
                "pending_question": checkpoint.get("pending_question"),
                "pending_reason": checkpoint.get("pending_reason"),
                "human_question": self.human_question(item_id),
                "plan": checkpoint.get("plan"),
                "notices": [{key: notice[key] for key in ("request_id", "kind", "remote_id", "created_at", "confirmed_at")}
                            for notice in self.notices(item_id)],
                "published_prs": [r["url"] for r in self.connection.execute(
                    "SELECT url FROM published_prs WHERE issue_id=? ORDER BY url", (row["issue_id"],))],
                "inbox_pending": self.connection.execute(
                    "SELECT count(*) FROM inbox WHERE item_id=? AND consumed_at IS NULL", (row["id"],)).fetchone()[0]}

    def _conversation_history(self, issue_id):
        """Prior findings/questions are recall, separate from this item's live requests."""
        history = []
        for row in self.connection.execute("SELECT * FROM work_items WHERE issue_id=? ORDER BY created_at,rowid", (issue_id,)):
            checkpoint, evidence = json.loads(row["checkpoint"]), json.loads(row["evidence"])
            history.append({"item_id": row["id"], "session_id": row["session_id"], "skill": row["skill"],
                            "state": row["state"], "summary": evidence.get("summary"),
                            "pending_question": checkpoint.get("pending_question"),
                            "handoff": checkpoint.get("handoff"),
                            "messages": [_message(m) for m in self.connection.execute(
                                "SELECT id,body,author_json,created_at FROM inbox WHERE item_id=? ORDER BY id",
                                (row["id"],))]})
        return history

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
