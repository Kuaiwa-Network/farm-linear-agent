"""Verify committed UI-only Client output before Unity or draft delivery."""
import re

from .fgui_client import _client, assert_installed, installation_record, installation_records
from .ledger import LedgerError

GUARD = "tests/Farm.Tests.Unit/FguiDependencyGuardTests.cs"
_BLOCK = re.compile(r"(^[ \t]*private static readonly Dictionary<string, string\[\]> _allowed = new\(\)\n"
                    r"[ \t]*\{\n)(.*?)(^[ \t]*\};)", re.M | re.S)
_ENTRY = re.compile(r'\["([A-Za-z][A-Za-z0-9_]{0,63})"\]\s*=\s*new\[\]\s*\{([^{}]*)\},\s*(?://.*)?$')
_DEPENDENCY = re.compile(r'"([A-Za-z][A-Za-z0-9_]{0,63})"')


def _guard(text):
    text = text.replace("\r\n", "\n")
    matches = list(_BLOCK.finditer(text))
    if len(matches) != 1:
        raise LedgerError("UI dependency guard must retain its one modeled literal _allowed block")
    match = matches[0]; entries = {}; pending = []
    for raw in match[2].splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            pending.append(raw); continue
        entry = _ENTRY.fullmatch(line)
        if entry is None:
            raise LedgerError("UI dependency guard cannot add executable or unmodeled dictionary content")
        name, body = entry[1], entry[2]
        deps = _DEPENDENCY.findall(body)
        if re.sub(_DEPENDENCY, "", body).strip(" ,\t") or len(deps) != len(set(deps)) or name in entries:
            raise LedgerError("UI dependency guard has duplicate or malformed package entries")
        entries[name] = {"dependencies": set(deps), "raw": [*pending, raw],
                         "reason": any(p.strip().startswith("//") and p.strip()[2:].strip() for p in pending)
                                   or bool(line.split("//", 1)[1].strip() if "//" in line else "")}
        pending = []
    return text[:match.start()] + match[1], match[3] + text[match.end():], entries, pending


def dependency_guard_change(before, after, dependencies):
    """Only changed-package literal entries/reasons; surrounding test code is fixed."""
    old_prefix, old_suffix, old, old_tail = _guard(before)
    prefix, suffix, new, tail = _guard(after)
    if (prefix, suffix, tail) != (old_prefix, old_suffix, old_tail):
        raise LedgerError("UI dependency-guard edits must leave test code and unrelated trailing content unchanged")
    touched = {name for name in old.keys() | new.keys() if old.get(name) != new.get(name)}
    if touched - set(dependencies):
        raise LedgerError("UI dependency-guard edits may change only this export's changed packages")
    for name in touched:
        expected = set(dependencies[name]); actual = new.get(name)
        if actual is None or actual["dependencies"] != expected:
            raise LedgerError("UI dependency-guard entries must describe the actual exported descriptor dependencies")
        added = expected - old.get(name, {}).get("dependencies", set())
        if added and not actual["reason"]:
            raise LedgerError("new UI dependency edges require a reason in the same guard entry")
    return sorted(touched)


def installed_scope(workflow, installed, *, expected_commit=None):
    """Prove current committed output using complete prior install records too."""
    assert_installed(workflow, installed)
    client, branch, baseline = _client(workflow)
    commit = workflow.trees.git_in(client, "rev-parse", "HEAD")
    if expected_commit is not None and expected_commit != commit:
        raise LedgerError("UI verification commit differs from its actual committed Client HEAD")
    workflow.trees.verification_commit("Farm-Client", workflow.item_id, commit, expected_branch=branch)
    changed = workflow.trees.git_in(client, "diff", "--name-only", "--no-renames", "--no-ext-diff", "--no-textconv", baseline, commit).splitlines()
    allowed = {"Assets/GameRes/FairyRes/" + entry["file"] for entry in installed["files"]}
    # Earlier complete installs may have removed artifacts absent from the new
    # plan. Those paths remain authorized deletions, never new arbitrary writes.
    for previous in installation_records(workflow.ledger, workflow.item_id):
        allowed.update("Assets/GameRes/FairyRes/" + name for name in previous["plan"]["deletes"])
    if set(changed) - allowed - {GUARD}:
        raise LedgerError("UI Client diff contains changes outside certified package outputs and dependency-guard reasons")
    packages = installed["packages"]
    dependencies = installed["dependencies"]
    guard = []
    if GUARD in changed:
        before = workflow.trees.git_in(client, "show", baseline + ":" + GUARD)
        after = workflow.trees.git_in(client, "show", commit + ":" + GUARD)
        guard = dependency_guard_change(before, after, dependencies)
    workflow.fence(refresh=True)
    proof = {"format": "farmbot-fgui-client-scope-v1", "receipt_id": installed["receipt_id"],
             "installation_id": installed["installation_id"], "commit": commit, "baseline": baseline,
             "branch": branch, "source_head": installed["source_head"],
             "changed_packages": sorted(packages), "changed_client_files": sorted(changed), "guard_packages": guard}
    return proof


def verify_candidate(workflow, receipt_id, *, expected_commit=None):
    workflow._stage("Farm-Client")
    workflow.retained_export(receipt_id)
    installed = installation_record(workflow.ledger, workflow.item_id, receipt_id)
    if installed is None:
        raise LedgerError("UI verification requires its completed controller-certified Client installation")
    proof = installed_scope(workflow, installed, expected_commit=expected_commit)
    with workflow.ledger._transaction():
        from .fgui_records import _live
        _live(workflow.ledger, workflow.item_id, workflow.token, ("Farm-Client",))
        workflow.ledger._audit(workflow.item_id, "fgui_client_scope", details=proof)
    return proof
