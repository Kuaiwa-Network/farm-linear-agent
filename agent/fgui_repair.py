"""Controller-qualified registrations for dependencies already in the UI baseline.

This is a bounded metadata repair, not a new source/asset grant or a test waiver.
The worker supplies audited reasons; the controller derives every edge from the
immutable baseline descriptor and retains exact literal entries in its audit.
"""
import hashlib
import re
from uuid import uuid4

from .fgui_client import _client, installation_record
from .fgui_export import MAX_DESCRIPTOR_BYTES, _Reader, descriptor, _package
from .fgui_install import _bytes
from .fgui_records import _canonical, _live
from .fgui_source import _git, _POINTER
from .ledger import LedgerError
from .preview_upload import _plain_path

KIND = "fgui_baseline_guard_repair"


def repair_records(ledger, item_id, baseline):
    import json
    records = []
    for row in ledger.connection.execute("SELECT details FROM audit WHERE item_id=? AND kind=? ORDER BY id",
                                         (item_id, KIND)):
        try:
            value = json.loads(row["details"])
            body = {k: v for k, v in value.items() if k != "repair_sha256"}
            if (value["format"] != "farmbot-fgui-baseline-guard-repair-v1" or value["baseline"] != baseline
                    or hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest() != value["repair_sha256"]
                    or not isinstance(value["entries"], dict) or not value["entries"]):
                raise ValueError()
            for name, entry in value["entries"].items():
                _package(name, entry["descriptor_id"])
                if (not isinstance(entry["raw"], list) or not all(isinstance(s, str) for s in entry["raw"])
                        or not isinstance(entry["dependencies"], list)):
                    raise ValueError()
            records.append(value)
        except (KeyError, TypeError, ValueError):
            raise LedgerError("UI baseline repair has damaged controller evidence; preserve recovery evidence") from None
    return records


def baseline_descriptor(workflow, client, baseline, name):
    """Materialized input must match the immutable raw Git blob or LFS pointer."""
    path = "Assets/GameRes/FairyRes/" + name + "/" + name + "_fui.bytes"
    raw = _git(workflow.trees, client, ["show", baseline + ":" + path],
               repo="Farm-Client", limit=MAX_DESCRIPTOR_BYTES)
    data = _bytes(client / path, MAX_DESCRIPTOR_BYTES)
    pointer = _POINTER.fullmatch(raw)
    if pointer:
        if len(data) != int(pointer[2]) or hashlib.sha256(data).hexdigest() != pointer[1].decode("ascii"):
            raise LedgerError("UI repair descriptor must be hydrated to its immutable baseline LFS identity")
    elif data != raw:
        raise LedgerError("UI repair descriptor differs from its immutable baseline")
    reader = _Reader(data)
    if reader.take(4) != b"FGUI":
        raise LedgerError("UI repair needs a hydrated baseline descriptor")
    reader.number(">i"); reader.boolean()
    identity = reader.text()
    parsed = descriptor(data, name, identity)
    return identity, sorted(n for n, _ in parsed.dependencies), hashlib.sha256(data).hexdigest()


def authorize_repair(workflow, receipt_id, reasons):
    """Authorize exact missing baseline registrations; never edit the checkout."""
    from .fgui_scope import GUARD, _guard, verify_candidate
    if not isinstance(reasons, dict) or not 0 < len(reasons) <= 100:
        raise LedgerError("UI baseline repair requires a bounded package-to-audited-reason map")
    for name, reason in reasons.items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name):
            raise LedgerError("UI baseline repair package name is invalid")
        if (not isinstance(reason, str) or not 20 <= len(reason.strip()) <= 3000
                or any(ord(c) < 32 and c != "\n" for c in reason)):
            raise LedgerError("UI baseline repair requires a concrete source/reference and lifetime reason")
    workflow.ledger.require_no_reservation(workflow.item_id)
    scope = verify_candidate(workflow, receipt_id)
    installed = installation_record(workflow.ledger, workflow.item_id, receipt_id)
    client, branch, baseline = _client(workflow)
    if set(reasons) & set(installed["packages"]):
        raise LedgerError("UI baseline repair is only for unchanged packages outside this export")
    old_text = workflow.trees.git_in(client, "show", baseline + ":" + GUARD)
    current = workflow.trees.git_in(client, "show", scope["commit"] + ":" + GUARD)
    _, _, old, _ = _guard(old_text)
    prefix, suffix, entries, tail = _guard(current)
    records = repair_records(workflow.ledger, workflow.item_id, baseline)
    for record in records:
        if record["reasons"] == reasons and record["receipt_id"] == receipt_id:
            return record
    prior_names = {n for r in records for n in r["entries"]}
    if prior_names & set(reasons):
        raise LedgerError("UI baseline repair entries are immutable; reuse their original reasons")
    repaired = {}
    for name, reason in sorted(reasons.items()):
        identity, deps, digest = baseline_descriptor(workflow, client, baseline, name)
        previous = old.get(name, {}).get("dependencies", set())
        if not set(deps) - previous:
            raise LedgerError("UI baseline repair can only add missing actual baseline edges, never remove or invent edges")
        registered = sorted(previous | set(deps))
        raw = [*old.get(name, {}).get("raw", [])[:-1],
               *["            // " + line for line in reason.strip().splitlines()],
               '            ["' + name + '"] = new[] { ' + ", ".join('"' + d + '"' for d in registered) + " },"]
        repaired[name] = {"raw": raw, "dependencies": registered, "descriptor_dependencies": deps, "descriptor_id": identity,
                          "descriptor_sha256": digest}
    # Preserve the candidate's changed-package entries byte-for-byte (apart from
    # normalized newlines); add only the qualified unrelated literal entries.
    combined = {**entries, **repaired}
    proposal = prefix + "\n".join(line for entry in combined.values() for line in entry["raw"]) + "\n"
    if tail:
        proposal += "\n".join(tail) + "\n"
    proposal += suffix
    _guard(proposal)
    run = workflow.paths.runs / workflow.item_id / "ui-guard-repairs" / uuid4().hex
    _plain_path(run.parent); run.parent.mkdir(parents=True, exist_ok=True); _plain_path(run.parent)
    run.mkdir(); output = run / "guard.cs"
    output.write_bytes(proposal.encode("utf-8"))
    workflow.fence(refresh=True)
    workflow.ledger.require_no_reservation(workflow.item_id)
    if verify_candidate(workflow, receipt_id) != scope:
        raise LedgerError("UI candidate changed during baseline repair authorization")
    proof = {"format": "farmbot-fgui-baseline-guard-repair-v1", "receipt_id": receipt_id,
             "installation_sha256": installed["installation_sha256"], "baseline": baseline,
             "branch": branch, "initial_commit": scope["commit"], "entries": repaired,
             "reasons": reasons, "proposal_path": str(output),
             "proposal_sha256": hashlib.sha256(proposal.encode("utf-8")).hexdigest()}
    proof["repair_sha256"] = hashlib.sha256(_canonical(proof).encode("utf-8")).hexdigest()
    with workflow.ledger._transaction():
        _live(workflow.ledger, workflow.item_id, workflow.token, ("Farm-Client",))
        workflow.ledger._audit(workflow.item_id, KIND, details=proof)
    return proof


def repaired_baseline(workflow, installed, client, baseline, before, after):
    """Account for controller-qualified entries without moving the task baseline."""
    from .fgui_scope import _guard
    prefix, suffix, entries, tail = _guard(before)
    actual = _guard(after)[2]
    names, hashes = set(), []
    for record in repair_records(workflow.ledger, workflow.item_id, baseline):
        if record["branch"] != installed["branch"]:
            raise LedgerError("UI baseline repair branch differs from the certified installation")
        for name, entry in record["entries"].items():
            if name in installed["packages"]:
                # A new approved export owns this entry through normal exact
                # changed-package validation, not the historical repair grant.
                continue
            if name in names:
                raise LedgerError("UI baseline repair package scope overlaps; preserve recovery evidence")
            if actual.get(name, {}).get("raw") != entry["raw"]:
                # Authorization is durable before a worker applies its proposal.
                # An interrupted attempt may still have the original entry.
                if actual.get(name) == entries.get(name):
                    continue
                raise LedgerError("UI baseline repair entry differs from its exact controller authorization")
            identity, deps, digest = baseline_descriptor(workflow, client, baseline, name)
            if (identity, deps, digest) != (entry["descriptor_id"], entry["descriptor_dependencies"], entry["descriptor_sha256"]):
                raise LedgerError("UI baseline repair descriptor evidence changed")
            entries[name] = entry; names.add(name)
        hashes.append(record["repair_sha256"])
    if not names:
        return before, [], []
    text = prefix + "\n".join(line for e in entries.values() for line in e["raw"]) + "\n"
    if tail:
        text += "\n".join(tail) + "\n"
    return text + suffix, sorted(names), hashes
