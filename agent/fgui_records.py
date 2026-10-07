"""Controller records for UI previews and exports, separate from worker plans.

Audit rows survive attempt handoffs; checkpoint prose cannot create these proofs.
No schema migration, new claim authority or worker write root is introduced here.
"""
import hashlib
from datetime import datetime
import json
import re
from uuid import uuid4

from .ledger import LedgerError


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _live(ledger, item_id, token, root):
    row = ledger._owned(item_id, token)
    ledger._refuse_withdrawn(row)
    if row["skill"] != "fgui" or row["root_repo"] not in root:
        raise LedgerError("UI evidence requires this job's matching rooted stage")
    return row


def record_preview(ledger, item_id, token, image, result):
    """Called only after the trusted image transfer and its final claim fence."""
    from .preview_upload import _destination
    if not isinstance(result, dict) or set(result) != {"asset_url", *image.evidence()}:
        raise LedgerError("UI preview upload returned incomplete evidence")
    expected = image.evidence()
    if any(result.get(key) != value for key, value in expected.items()):
        raise LedgerError("UI preview upload differs from the immutable image")
    _destination(result["asset_url"], asset=True)
    proof = {"format": "farmbot-fgui-preview-v1", **expected, "asset_url": result["asset_url"]}
    with ledger._transaction():
        row = _live(ledger, item_id, token, (None, "farmgui"))
        ledger._audit(item_id, "fgui_preview_uploaded", details={**proof, "generation": row["generation"]})
    return proof


def published_preview(ledger, item_id, digest, asset_url, *, before):
    """Find this issue's actual transfer, preceding the named human approval.

    Earlier same-issue UI jobs can supply an unchanged retained visual round;
    unrelated jobs, planned assets and post-approval uploads cannot supply proof.
    The caller separately validates the actual review image and current source.
    """
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise LedgerError("UI review image requires its actual SHA-256")
    issue = ledger.item(item_id)["issue_id"]
    rows = ledger.connection.execute(
        "SELECT a.details,a.created_at FROM audit a JOIN work_items w ON w.id=a.item_id "
        "WHERE w.issue_id=? AND w.skill='fgui' AND a.kind='fgui_preview_uploaded' AND a.created_at<=? "
        "ORDER BY a.id DESC", (issue, before))
    for row in rows:
        proof = json.loads(row["details"])
        if proof.get("sha256") == digest and proof.get("asset_url") == asset_url:
            return {**proof, "recorded_at": row["created_at"]}
    raise LedgerError("UI visual approval requires an actual earlier same-issue preview upload")


def human_event_time(ledger, item_id, event):
    """Use canonical inbox precision, which its worker-facing ISO display omits.

    Rounding a session reply to whole seconds can incorrectly place it before an
    earlier upload in the same second. Never add a grace interval that could also
    accept a later upload; use the actual stored time for that same-issue message.
    Actual comment timestamps retain their API-provided precision instead.
    """
    if "message_id" in event:
        row = ledger.connection.execute(
            "SELECT i.created_at FROM inbox i JOIN work_items w ON w.id=i.item_id "
            "WHERE i.id=? AND w.issue_id=?", (event["message_id"], ledger.item(item_id)["issue_id"])).fetchone()
        if row is None:
            raise LedgerError("UI human event has no canonical same-issue timestamp")
        return row["created_at"]
    return datetime.fromisoformat(event["created_at"].replace("Z", "+00:00")).timestamp()


def published_round(ledger, item_id, review, *, before):
    """Require a confirmed same-issue visual notice naming the actual image/head."""
    issue = ledger.item(item_id)["issue_id"]
    rows = ledger.connection.execute(
        "SELECT n.body,n.remote_id FROM notices n JOIN work_items w ON w.id=n.item_id "
        "WHERE w.issue_id=? AND w.skill='fgui' AND n.request_id=? AND n.kind='waiting' "
        "AND n.remote_id IS NOT NULL AND n.confirmed_at<=?", (issue, 'visual-'+str(review["round"]), before))
    for row in rows:
        if review["asset_url"] in row["body"] and review["head"] in row["body"]:
            return row["remote_id"]
    raise LedgerError("UI approval requires the confirmed visual-round notice with its actual image and source HEAD")


def record_review(ledger, item_id, token, proof):
    """Anchor a verified source/review identity before asking for visual approval.

    An unchanged round is idempotent. A different source/image/package identity
    needs a new round; a worker cannot replace the controller's existing record.
    The caller supplies owned source/draft/diff and actual uploaded-image proof.
    """
    with ledger._transaction():
        _live(ledger, item_id, token, (None, "farmgui"))
        for old in ledger.connection.execute("SELECT details FROM audit WHERE item_id=? AND kind='fgui_review_created'", (item_id,)):
            value = json.loads(old["details"])
            if value.get("round") == proof["round"]:
                if {k: v for k, v in value.items() if k != "review_id"} != proof:
                    raise LedgerError("UI review round identity changed; render/upload a new numbered round")
                return value
        value = {**proof, "review_id": uuid4().hex}
        ledger._audit(item_id, "fgui_review_created", details=value)
    return value


def review_record(ledger, item_id, identity):
    if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{32}", identity):
        raise LedgerError("UI review identity is invalid")
    matches = [json.loads(row["details"]) for row in ledger.connection.execute(
        "SELECT details FROM audit WHERE item_id=? AND kind='fgui_review_created'", (item_id,))
        if json.loads(row["details"]).get("review_id") == identity]
    if len(matches) != 1:
        raise LedgerError("UI review requires exactly one controller record from this job")
    return matches[0]


def record_export(ledger, item_id, token, receipt_id, authority, result, *, source_pr, main):
    """Trusted caller persists successful immutable helper/approval identities.

    The receipt ID is generated by the controller; private directories are derived
    from it, never selected through worker checkpoint paths. Source draft/current
    main proof and named approval are established by that caller before execution.
    """
    from .fgui_approval import ExportAuthority
    from .fgui_export import ExportSnapshot
    from .fgui_publisher import PublishResult
    if (not isinstance(receipt_id, str) or not re.fullmatch(r"[0-9a-f]{32}", receipt_id)
            or not isinstance(authority, ExportAuthority) or not isinstance(result, PublishResult)
            or not isinstance(result.snapshot, ExportSnapshot)
            or not isinstance(main, str) or not re.fullmatch(r"[0-9a-f]{40}", main)
            or not isinstance(source_pr, str) or not re.fullmatch(r"https://github\.com/[^/\s]+/[^/\s]+/pull/[1-9][0-9]*", source_pr)):
        raise LedgerError("UI export requires controller-created identity and verified immutable helper evidence")
    process = dict(result.process)
    if (result.source_head != authority.head or result.source_digest != authority.source_digest
            or type(process.get("exit_code")) is not int or process["exit_code"] != 0 or process.get("job_empty") is not True
            or process.get("job_assigned_before_startup") is not True
            or process.get("job_assigned_before_export") is not True):
        raise LedgerError("UI export receipt cannot certify stale source or uncertain process ownership")
    selected = {package.name: package.identity for package in result.snapshot.packages}
    if any(selected.get(name) != identity for name, identity in authority.changed_packages):
        raise LedgerError("UI export inventory omits an actually changed package")
    proof = {"format": "farmbot-fgui-receipt-v1", "receipt_id": receipt_id,
             "authority": authority.evidence(), "publisher": result.evidence(), "source_pr": source_pr, "main": main}
    proof["receipt_sha256"] = hashlib.sha256(_canonical(proof).encode("utf-8")).hexdigest()
    with ledger._transaction():
        row = _live(ledger, item_id, token, (None, "farmgui"))
        for old in ledger.connection.execute("SELECT details FROM audit WHERE item_id=? AND kind='fgui_export_complete'", (item_id,)):
            if json.loads(old["details"]).get("receipt_id") == receipt_id:
                raise LedgerError("UI export receipt identity is immutable and already recorded")
        ledger._audit(item_id, "fgui_export_complete", details={**proof, "generation": row["generation"]})
    return proof


def export_record(ledger, item_id, receipt_id):
    if not isinstance(receipt_id, str) or not re.fullmatch(r"[0-9a-f]{32}", receipt_id):
        raise LedgerError("UI export receipt identity is invalid")
    matches = [json.loads(row["details"]) for row in ledger.connection.execute(
        "SELECT details FROM audit WHERE item_id=? AND kind='fgui_export_complete'", (item_id,))
        if json.loads(row["details"]).get("receipt_id") == receipt_id]
    if len(matches) != 1:
        raise LedgerError("UI export requires exactly one controller record from this job")
    expected = {k: v for k, v in matches[0].items() if k not in ("receipt_sha256", "generation")}
    if hashlib.sha256(_canonical(expected).encode("utf-8")).hexdigest() != matches[0].get("receipt_sha256"):
        raise LedgerError("UI controller export record has damaged immutable evidence")
    return matches[0]
