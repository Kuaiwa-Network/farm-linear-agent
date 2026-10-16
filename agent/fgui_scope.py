"""Verify committed UI-only Client output before Unity or draft delivery."""
from collections import Counter
import hashlib
import json
import re

from .fgui_client import _installed_files, _guid_digest, installation_record, installation_history
from .fgui_install import _GUID, _metadata_guids
from .fgui_records import _canonical
from .ledger import LedgerError
from .worktrees import GIT_ENV

GUARD = "tests/Farm.Tests.Unit/FguiDependencyGuardTests.cs"
_NO_REPLACE = {**GIT_ENV, "GIT_NO_REPLACE_OBJECTS": "1"}
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


def _delta(workflow, client, before, after):
    return set(workflow.trees.git_in(client, "diff", "--name-only", "--no-renames", "--no-ext-diff",
                                     "--no-textconv", before, after, env=_NO_REPLACE).splitlines())


def _inherited_base(workflow, client, baseline, commit, main):
    """Use the controller-pinned task entry, never a worker's claimed parent base."""
    item = workflow.ledger.item(workflow.item_id)
    stage_base = item["target"]["commit_sha"]
    if not item.get("parent_id"):
        return stage_base

    def bases(before, after):
        return workflow.trees.git_in(client, "merge-base", "--all", before, after,
                                     env=_NO_REPLACE).splitlines()

    if (item.get("task_client_base") != baseline or bases(stage_base, baseline) != [stage_base]
            or bases(baseline, commit) != [baseline]):
        raise LedgerError("UI inherited Client scope requires its unchanged controller-pinned task entry ancestry")
    common = bases(baseline, main)
    if len(common) != 1:
        raise LedgerError("UI inherited Client scope requires one unambiguous task-entry/main merge base")
    return common[0]


def _integrated_main(workflow, installed, client, commit):
    # A completed guard proof fixes this candidate's qualified main through
    # queuing, loading, release and delivery, including a resumed worker. A new
    # candidate/receipt still needs ordinary current-main qualification.
    reservation = workflow.ledger.active_reservation(workflow.item_id)
    reserved = (reservation and reservation["state"] == "active" and reservation["kind"] == "unity_slot"
                and reservation["commit_sha"] == commit)
    from .fgui_guards import retained_guards
    checked = retained_guards(workflow, installed["receipt_id"],
                              {"commit": commit, "source_head": installed["source_head"]}, installed)
    if reserved or checked is not None:
        for row in workflow.ledger.connection.execute(
                "SELECT details FROM audit WHERE item_id=? AND kind='fgui_client_scope' ORDER BY id DESC",
                (workflow.item_id,)):
            prior = json.loads(row["details"])
            if (prior.get("commit") != commit or prior.get("installation_id") != installed["installation_id"]
                    or prior.get("receipt_id") != installed["receipt_id"]):
                continue
            if "main_integration" not in prior:
                if not reserved:
                    continue
                if any(key in prior for key in ("scope_sha256", "scope_base", "global_guid_digest", "inherited_client_files")):
                    raise LedgerError("UI main integration has damaged controller evidence; preserve recovery records")
                # Legacy scope evidence can retain only the original, unchanged installation
                # at its existing reservation. It cannot authorize any imported metadata.
                if prior.get("baseline") == installed["baseline"] and prior.get("source_head") == installed["source_head"]:
                    return None
                continue
            raw = {k: v for k, v in prior.items() if k != "scope_sha256"}
            if hashlib.sha256(_canonical(raw).encode("utf-8")).hexdigest() != prior.get("scope_sha256"):
                raise LedgerError("UI main integration has damaged controller evidence; preserve recovery records")
            authority = prior["main_integration"]
            if (not isinstance(authority, dict) or prior.get("scope_base") != authority.get("commit")
                    or prior.get("baseline") != installed["baseline"] or prior.get("source_head") != installed["source_head"]
                    or prior.get("branch") != installed["branch"] or authority.get("candidate") != commit
                    or not workflow.trees.has_commit("Farm-Client", authority.get("commit"))
                    or workflow.trees.git_in(client, "merge-base", "--all", authority["commit"], commit,
                        env=_NO_REPLACE).splitlines() != [authority["commit"]]):
                raise LedgerError("UI reserved main integration identity changed")
            if not reserved:
                # Keep the qualified base on ordinary main advances, but never
                # borrow it across a remote default-branch change/history rewrite.
                workflow.trees.require_main_integration("Farm-Client", client, commit,
                    default_branch=authority["branch"], verified_base=authority["commit"])
            return authority
    return workflow.trees.require_main_integration("Farm-Client", client, commit)


def installed_scope(workflow, installed, *, expected_commit=None):
    """Prove current committed output using complete prior install records too."""
    client, branch, baseline = _installed_files(workflow, installed)
    commit = workflow.trees.git_in(client, "rev-parse", "HEAD")
    if expected_commit is not None and expected_commit != commit:
        raise LedgerError("UI verification commit differs from its actual committed Client HEAD")
    workflow.trees.verification_commit("Farm-Client", workflow.item_id, commit, expected_branch=branch)
    changed = _delta(workflow, client, baseline, commit)
    allowed = {"Assets/GameRes/FairyRes/" + entry["file"] for entry in installed["files"]}
    # Earlier complete installs may have removed artifacts absent from the new
    # plan. Those paths remain authorized deletions, never new arbitrary writes.
    for previous in installation_history(workflow.ledger, workflow.item_id):
        allowed.update("Assets/GameRes/FairyRes/" + name for name in previous["plan"]["deletes"])
    guid_digest = _guid_digest(_metadata_guids(client))
    integration = _integrated_main(workflow, installed, client, commit)
    scope_base = baseline; inherited = set()
    if integration is not None:
        scope_base = integration["commit"]
        integrated_delta = _delta(workflow, client, scope_base, commit)
        # Retained parent output must be byte/mode-identical to the task entry.
        # Exclude upstream changes since that entry's shared history, not changes
        # the parent had already integrated before delegating this UI repair.
        inherited_base = _inherited_base(workflow, client, baseline, commit, scope_base)
        inherited = (_delta(workflow, client, inherited_base, baseline)
                     - _delta(workflow, client, inherited_base, scope_base) - changed) & integrated_delta
        if integrated_delta - allowed - {GUARD} - inherited:
            raise LedgerError("UI Client diff contains changes outside certified package outputs and dependency-guard reasons")
        changed = integrated_delta - inherited
        # Clean Git status can omit ignored files or assume-unchanged entries. Upstream
        # provenance may change the GUID set only to the exact committed candidate set.
        committed = workflow.trees.git_in(client, "grep", "-h", "-I", "-E",
            "^guid:[[:space:]]*[[:xdigit:]]{32}[[:space:]]*$", commit, "--", ":(glob)Assets/**/*.meta",
            env=_NO_REPLACE)
        if _guid_digest(Counter(value.lower() for value in _GUID.findall(committed))) != guid_digest:
            raise LedgerError("UI Client metadata GUIDs differ from the committed integrated candidate")
    elif changed - allowed - {GUARD} or guid_digest != installed["global_guid_digest"]:
        raise LedgerError("legacy UI reservation cannot authorize changes outside its original certified scope")
    packages = installed["packages"]
    dependencies = installed["dependencies"]
    guard = []
    repairs, repair_hashes = [], []
    if GUARD in changed:
        before = workflow.trees.git_in(client, "show", scope_base + ":" + GUARD, env=_NO_REPLACE)
        after = workflow.trees.git_in(client, "show", commit + ":" + GUARD, env=_NO_REPLACE)
        from .fgui_repair import repaired_baseline
        if scope_base == baseline:
            before, repairs, repair_hashes = repaired_baseline(workflow, installed, client, baseline, before, after)
        guard = dependency_guard_change(before, after, dependencies)
    workflow.fence(refresh=True)
    workflow.trees.verification_commit("Farm-Client", workflow.item_id, commit, expected_branch=branch)
    proof = {"format": "farmbot-fgui-client-scope-v1", "receipt_id": installed["receipt_id"],
             "installation_id": installed["installation_id"], "commit": commit, "baseline": baseline,
             "branch": branch, "source_head": installed["source_head"],
             "changed_packages": sorted(packages), "changed_client_files": sorted(changed), "guard_packages": guard}
    if repair_hashes:
        proof.update(baseline_guard_repairs=repairs, baseline_guard_repair_sha256=repair_hashes)
    if integration is not None:
        proof.update(main_integration=integration, scope_base=scope_base, global_guid_digest=guid_digest,
                     inherited_client_files=sorted(inherited))
        proof["scope_sha256"] = hashlib.sha256(_canonical(proof).encode("utf-8")).hexdigest()
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
