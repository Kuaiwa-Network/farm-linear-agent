"""Ground UI export events in actual human messages and the unchanged source round.

Semantic interpretation of a person's request remains the scoped worker's duty;
this helper does not infer approval from text, a description, silence or a PR.
It rejects fabricated attribution and stale identities before a future export CLI.
"""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re

from .fgui_export import _package
from .ledger import LedgerError


def source_digest(files):
    if not isinstance(files, dict) or not 0 < len(files) <= 20000:
        raise LedgerError("UI approval requires a bounded complete source hash map")
    for name, value in files.items():
        if (not isinstance(name, str) or not name or len(name) > 512 or any(ord(c) < 32 for c in name)
                or "\\" in name or ":" in name or any(p in ("", ".", "..") for p in name.split("/"))
                or not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)):
            raise LedgerError("UI source hash identity is malformed")
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _time(value):
    if not isinstance(value, str) or len(value) > 64:
        raise LedgerError("UI event requires the actual attributed timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise LedgerError("UI event requires the actual attributed timestamp") from None
    if result.tzinfo is None:
        raise LedgerError("UI event timestamp requires a timezone")
    return result


def _human_event(event, context, bot_id):
    keys = [key for key in ("message_id", "comment_id") if key in event]
    if len(keys) != 1:
        raise LedgerError("UI event requires one actual session message or human comment")
    key, identity = keys[0], event[keys[0]]
    if key == "message_id":
        if isinstance(identity, bool) or not isinstance(identity, int) or identity < 1:
            raise LedgerError("UI event session message identity is invalid")
        matches = [m for m in context.get("session_messages", []) if m.get("id") == identity]
        if not matches:
            # Canonical messages from this same issue's earlier jobs are actual
            # stored attribution, unlike worker-written handoff/summary prose.
            # They allow an unchanged round to survive a retired/replaced attempt.
            matches = [m for job in context.get("conversation_history", []) for m in job.get("messages", [])
                       if m.get("id") == identity]
    else:
        if not isinstance(identity, str) or not 0 < len(identity) <= 128:
            raise LedgerError("UI event comment identity is invalid")
        issue = context.get("issue", {})
        if issue.get("comments_complete") is not True:
            raise LedgerError("UI event cannot use an incomplete current comment inventory")
        matches = [c for c in issue.get("comments", []) if c.get("id") == identity and c.get("author_kind") == "human"]
    if len(matches) != 1:
        raise LedgerError("UI event attribution is missing or ambiguous in current issue context")
    message = matches[0]; author = message.get("author"); named = event.get("author")
    body = message.get("body")
    if (not isinstance(author, dict) or not isinstance(author.get("id"), str) or not author["id"]
            or not isinstance(author.get("name"), str) or not author["name"]
            or author["id"] == bot_id or not isinstance(named, dict)
            or named.get("id") != author["id"] or named.get("name") != author["name"]
            or not isinstance(body, str) or not body.strip() or "[farmbot:" in body.casefold()
            or body.lstrip().startswith((">", "```"))):
        raise LedgerError("UI event must name the actual human author and unquoted non-bot message")
    if _time(event.get("created_at")) != _time(message.get("created_at")):
        raise LedgerError("UI event timestamp differs from its actual human message")
    return {"kind": event["kind"], key: identity,
            "author_id": author["id"], "author_name": author["name"],
            "created_at": message["created_at"], "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest()}


@dataclass(frozen=True)
class ExportAuthority:
    head: str
    source_digest: str
    round: int
    preview_sha256: str
    changed_packages: tuple
    approval: tuple
    request: tuple

    def evidence(self):
        def event(values):
            value = dict(values)
            value["author"] = {"id": value.pop("author_id"), "name": value.pop("author_name")}
            return value
        return {"format": "farmbot-fgui-authority-v1", "head": self.head, "source_digest": self.source_digest,
                "round": self.round, "preview_sha256": self.preview_sha256,
                "changed_packages": dict(self.changed_packages), "approval": event(self.approval),
                "request": event(self.request)}


def export_authority(plan, context, *, head, files, preview_sha256, changed_packages, bot_id):
    """Check actual attribution/identity, never treat a worker plan as a human answer.

    The trusted caller verifies the current owned PR/source HEAD, all tracked
    hydrated bytes and the actual retained review PNG. `changed_packages` is the
    actual source PR diff, including changed shared packages, not a worker guess.
    Source/art/state or review-image changes require a new verified visual round.
    """
    if not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}", head):
        raise LedgerError("UI export requires the actual full source commit")
    if not isinstance(preview_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", preview_sha256):
        raise LedgerError("UI export requires the actual retained review-image hash")
    if not isinstance(bot_id, str) or not bot_id or not isinstance(context, dict) or not isinstance(plan, dict):
        raise LedgerError("UI export requires current issue context and FarmBot identity")
    if (not isinstance(changed_packages, dict) or not 0 < len(changed_packages) <= 100
            or any(not isinstance(n, str) or not isinstance(i, str) for n, i in changed_packages.items())
            or len(set(changed_packages.values())) != len(changed_packages)):
        raise LedgerError("UI export requires actual changed package identities")
    for name, identity in changed_packages.items():
        _package(name, identity)
    if len({n.casefold() for n in changed_packages}) != len(changed_packages):
        raise LedgerError("UI changed package names collide")
    digest = source_digest(files)
    ui = plan.get("ui"); events = plan.get("events")
    if (not isinstance(ui, dict) or not isinstance(events, list) or len(events) > 50
            or isinstance(ui.get("round"), bool) or not isinstance(ui.get("round"), int) or not 0 < ui["round"] <= 10000
            or ui.get("head") != head or ui.get("source_digest") != digest
            or ui.get("preview_sha256") != preview_sha256):
        raise LedgerError("UI export requires the unchanged verified visual-round identity")
    names = ui.get("packages")
    if (not isinstance(names, list) or any(not isinstance(n, str) for n in names)
            or len(names) != len(set(names)) or set(names) != set(changed_packages)):
        raise LedgerError("UI visual round must include every changed source package")
    accepted = {}
    for event in events:
        if not isinstance(event, dict):
            raise LedgerError("UI approval/export events are malformed")
        if event.get("kind") not in ("visual_approved", "export_requested"):
            continue
        if (event.get("round"), event.get("head"), event.get("source_digest"), event.get("preview_sha256")) != (
                ui["round"], head, digest, preview_sha256):
            continue  # Historical events remain evidence, never current authority.
        value = _human_event(event, context, bot_id)
        previous = accepted.get(event["kind"])
        if previous is None or _time(value["created_at"]) > _time(previous["created_at"]):
            accepted[event["kind"]] = value
    if set(accepted) != {"visual_approved", "export_requested"}:
        raise LedgerError("UI export needs separate attributed visual approval and export request for this round")
    if _time(accepted["export_requested"]["created_at"]) < _time(accepted["visual_approved"]["created_at"]):
        raise LedgerError("UI export request must follow approval of the unchanged visual round")
    return ExportAuthority(head, digest, ui["round"], preview_sha256, tuple(sorted(changed_packages.items())),
                           tuple(sorted(accepted["visual_approved"].items())), tuple(sorted(accepted["export_requested"].items())))
