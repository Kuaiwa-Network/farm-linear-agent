"""Install only controller-certified UI exports into the pinned owned Client.

Interrupted writes retain the installer's journal. Neither a worker checkpoint
nor a dirty/changed branch can replace the controller's baseline or install proof.
"""
from collections import Counter
import hashlib
import json
from uuid import uuid4

from .fgui_export import MAX_FILE_BYTES, snapshot_export
from .fgui_install import _GUID, _bytes, _metadata_guids, _selected, apply_install, prepare_install
from .fgui_records import _canonical, _live
from .ledger import LedgerError
from .preview_upload import _plain_path


def _inventory(files):
    return [{"file": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in sorted(files.items())]


def _guid_digest(counts):
    if any(count != 1 for count in counts.values()):
        raise LedgerError("UI Client metadata GUIDs must remain globally unique")
    return hashlib.sha256(_canonical(dict(sorted(counts.items()))).encode("utf-8")).hexdigest()


def _client(workflow):
    workflow._stage("Farm-Client")
    item = workflow.ledger.item(workflow.item_id)
    branch = workflow.ledger.staged_client_branch(workflow.item_id, issue_prefix=workflow.config.issue_prefix)
    from .workflow import repository_owner
    client = workflow.paths.worktrees / repository_owner(item) / "Farm-Client"
    _plain_path(client)
    if workflow.trees.git_in(client, "branch", "--show-current") != branch:
        raise LedgerError("UI installation requires the controller's exact owned Client issue branch")
    baseline = item.get("task_client_base") if item.get("parent_id") else item["target"]["commit_sha"]
    if not baseline:
        raise LedgerError("internal UI installation requires its controller-selected presentation entry HEAD")
    return client, branch, baseline


def installation_records(ledger, item_id):
    rows = [json.loads(row["details"]) for row in ledger.connection.execute(
        "SELECT details FROM audit WHERE item_id=? AND kind='fgui_install_complete' ORDER BY id", (item_id,))]
    for value in rows:
        expected = {k: v for k, v in value.items() if k != "installation_sha256"}
        if hashlib.sha256(_canonical(expected).encode("utf-8")).hexdigest() != value.get("installation_sha256"):
            raise LedgerError("UI installation has damaged controller evidence; preserve recovery evidence")
    return rows


def installation_record(ledger, item_id, receipt_id):
    rows = installation_records(ledger, item_id)
    matches = [row for row in rows if row.get("receipt_id") == receipt_id]
    if len(matches) > 1:
        raise LedgerError("UI installation has ambiguous controller records; preserve recovery evidence")
    if not matches:
        return None
    return matches[0]


def installation_history(ledger, item_id):
    """Complete installs retained under the controller's original recovery base.

    Historical installs justify the existing candidate only. Fresh review,
    export, installation and verification records still belong to this task.
    """
    item = ledger.item(item_id)
    if not item.get("parent_id"):
        return installation_records(ledger, item_id)
    parent = ledger.item(item["parent_id"])
    ancestors, current = set(), parent.get("predecessor_id")
    while current and current not in ancestors:
        ancestors.add(current)
        current = ledger.item(current).get("predecessor_id")
    seen = set()

    def retained(task_id):
        if task_id in seen:
            raise LedgerError("UI installation recovery lineage is ambiguous; preserve recovery evidence")
        seen.add(task_id)
        task = ledger.item(task_id)
        if task_id != item_id and (task.get("parent_id") not in ancestors or task["skill"] != "fgui"
                or task["issue_id"] != item["issue_id"] or task.get("target") != item.get("target")
                or task.get("task_client_base") != item.get("task_client_base")
                or not (ledger.cleanup_record(task_id) or {}).get("done")):
            raise LedgerError("UI installation recovery must retain its certified predecessor ownership and baseline")
        pins = [json.loads(row["details"]) for row in ledger.connection.execute(
            "SELECT details FROM audit WHERE item_id=? AND kind='task_client_base' ORDER BY id", (task_id,))]
        if len(pins) > 1 or pins and pins[0].get("commit") != task.get("task_client_base"):
            raise LedgerError("UI installation recovery baseline evidence is damaged")
        records = []
        for previous in pins[0].get("predecessor_tasks", []) if pins else []:
            records.extend(retained(previous))
        for proof in installation_records(ledger, task_id):
            if (proof.get("format") != "farmbot-fgui-client-v1" or proof.get("baseline") != item.get("task_client_base")
                    or proof.get("branch") != (item.get("target") or {}).get("issue_branch")):
                raise LedgerError("UI installation recovery records differ from the retained baseline or branch")
            records.append(proof)
        return records

    return retained(item_id)


def assert_installed(workflow, proof):
    client, branch, baseline = _client(workflow)
    if proof["branch"] != branch or proof["baseline"] != baseline:
        raise LedgerError("UI installation cannot change its controller-selected Client baseline")
    files, dirs = _selected(client / "Assets/GameRes/FairyRes", proof["packages"])
    if _inventory(files) != proof["files"] or sorted(dirs) != proof["directories"]:
        raise LedgerError("Client files differ from the controller-certified UI installation")
    if _guid_digest(_metadata_guids(client)) != proof["global_guid_digest"]:
        raise LedgerError("Client global metadata identity changed after UI installation")
    workflow.fence()
    return proof


def _prepared_record(workflow, receipt_id):
    rows = [json.loads(row["details"]) for row in workflow.ledger.connection.execute(
        "SELECT details FROM audit WHERE item_id=? AND kind='fgui_install_prepared' ORDER BY id",
        (workflow.item_id,))]
    abandoned = {json.loads(row["details"])["prepared_sha256"] for row in workflow.ledger.connection.execute(
        "SELECT details FROM audit WHERE item_id=? AND kind='fgui_install_abandoned'", (workflow.item_id,))}
    matches = [row for row in rows if row.get("proof", {}).get("receipt_id") == receipt_id
               and row.get("prepared_sha256") not in abandoned]
    if len(matches) > 1:
        raise LedgerError("UI installation has ambiguous prepared records; preserve recovery evidence")
    if not matches:
        return None
    value = matches[0]
    expected = {k: v for k, v in value.items() if k != "prepared_sha256"}
    if hashlib.sha256(_canonical(expected).encode("utf-8")).hexdigest() != value.get("prepared_sha256"):
        raise LedgerError("UI installation has damaged prepared evidence; preserve recovery evidence")
    return value


def _prepared_identity(workflow, exported, prepared):
    proof = prepared["proof"]
    client, branch, baseline = _client(workflow)
    if (prepared.get("format") != "farmbot-fgui-client-prepared-v1"
            or prepared["receipt_sha256"] != exported["receipt_sha256"]
            or proof["receipt_id"] != exported["receipt_id"]
            or proof["source_head"] != exported["authority"]["head"]
            or proof["branch"] != branch or proof["baseline"] != baseline
            or workflow.trees.git_in(client, "rev-parse", "HEAD") != proof["initial_head"]):
        raise LedgerError("UI installation recovery identity changed; preserve recovery evidence")
    expected = {k: v for k, v in proof.items() if k != "installation_sha256"}
    if hashlib.sha256(_canonical(expected).encode("utf-8")).hexdigest() != proof.get("installation_sha256"):
        raise LedgerError("UI installation has damaged planned proof; preserve recovery evidence")
    return client, proof


def _abandon_unapplied(workflow, exported, prepared):
    """A clean, byte-identical before state can be planned again without rollback."""
    client, proof = _prepared_identity(workflow, exported, prepared)
    if workflow.trees.git_in(client, "status", "--porcelain"):
        return False
    files, dirs = _selected(client / "Assets/GameRes/FairyRes", proof["packages"])
    if (_inventory(files) != prepared["before_files"] or sorted(dirs) != prepared["before_directories"]
            or _guid_digest(_metadata_guids(client)) != prepared["before_global_guid_digest"]):
        return False
    workflow.fence()
    with workflow.ledger._transaction():
        _live(workflow.ledger, workflow.item_id, workflow.token, ("Farm-Client",))
        workflow.ledger.require_no_reservation(workflow.item_id)
        workflow.ledger._audit(workflow.item_id, "fgui_install_abandoned", "verified unchanged before state",
            {"installation_id": proof["installation_id"], "receipt_id": proof["receipt_id"],
             "prepared_sha256": prepared["prepared_sha256"]})
    return True


def _completed_install(workflow, exported, prepared):
    """Read-only recovery of a complete write phase bound to a pre-write audit.

    Private journals alone cannot authorize dirty work. Partial writes and legacy
    unbound journals remain preserved for operator investigation.
    """
    client, proof = _prepared_identity(workflow, exported, prepared)
    recovery = workflow.paths.runs / workflow.item_id / "ui-installations" / proof["installation_id"]
    try:
        journal = json.loads(_bytes(recovery / "journal.json", 4 << 20).decode("utf-8"))
    except (OSError, ValueError):
        raise LedgerError("UI installation recovery journal is unavailable; preserve recovery evidence") from None
    before = [{"file": entry["file"], "backup": str(i), "sha256": entry["sha256"]}
              for i, entry in enumerate(prepared["before_files"])]
    plan = proof["plan"]
    completed = ([{"mkdir": n} for n in sorted(plan["created_directories"], key=lambda n: (n.count("/"), n))]
                 + [{"write": entry["file"]} for entry in plan["writes"]]
                 + [{"delete": n} for n in sorted(plan["deletes"], key=lambda n: (-n.count("/"), n))]
                 + [{"rmdir": n} for n in sorted(plan["deleted_directories"], key=lambda n: (-n.count("/"), n))])
    if journal != {**plan, "state": "complete", "completed": completed, "before": before}:
        raise LedgerError("UI installation recovery requires its exact complete journal; preserve recovery evidence")
    for i, entry in enumerate(prepared["before_files"]):
        try:
            data = _bytes(recovery / str(i), MAX_FILE_BYTES)
        except OSError:
            raise LedgerError("UI installation recovery backup is unavailable; preserve recovery evidence") from None
        if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise LedgerError("UI installation recovery backup changed; preserve recovery evidence")
    assert_installed(workflow, proof)
    # Recheck Git ownership after the potentially long asset/GUID inventory.
    _prepared_identity(workflow, exported, prepared)
    if workflow.trees.git_in(client, "diff", "--cached", "--name-only", "--no-ext-diff", "--no-textconv", "-z"):
        raise LedgerError("UI installation recovery requires an unchanged index; preserve recovery evidence")
    changed = set()
    for args in (("diff", "--name-only", "--no-renames", "--no-ext-diff", "--no-textconv", "-z"),
                 ("ls-files", "--others", "--exclude-standard", "-z")):
        changed.update(filter(None, workflow.trees.git_in(client, *args).split("\0")))
    allowed = {"Assets/GameRes/FairyRes/" + name
               for name in [*(entry["file"] for entry in plan["writes"]), *plan["deletes"]]}
    if changed - allowed:
        raise LedgerError("UI installation recovery found unrelated Client changes; preserve recovery evidence")
    workflow.fence()
    return proof


def _certify(workflow, exported, proof, *, prepared=None):
    workflow.fence(refresh=True)
    if workflow.retained_export(proof["receipt_id"]) != exported:
        raise LedgerError("UI export or authority changed during installation; preserve recovery evidence")
    client, branch, baseline = _client(workflow)
    if (branch != proof["branch"] or baseline != proof["baseline"]
            or workflow.trees.git_in(client, "rev-parse", "HEAD") != proof["initial_head"]):
        raise LedgerError("Client ownership/HEAD changed during UI installation")
    if prepared is not None:
        _completed_install(workflow, exported, prepared)
    else:
        assert_installed(workflow, proof)
    with workflow.ledger._transaction():
        _live(workflow.ledger, workflow.item_id, workflow.token, ("Farm-Client",))
        workflow.ledger.require_no_reservation(workflow.item_id)
        if installation_record(workflow.ledger, workflow.item_id, proof["receipt_id"]) is not None:
            raise LedgerError("UI installation record already exists; preserve concurrent recovery evidence")
        workflow.ledger._audit(workflow.item_id, "fgui_install_complete", details=proof)
    return proof


def install(workflow, receipt_id):
    workflow._stage("Farm-Client")
    exported = workflow.retained_export(receipt_id)
    client, branch, baseline = _client(workflow)
    previous = installation_record(workflow.ledger, workflow.item_id, receipt_id)
    if previous is not None:
        return assert_installed(workflow, previous)
    workflow.ledger.require_no_reservation(workflow.item_id)
    selected = {p["name"]: p["id"] for p in exported["publisher"]["export"]["packages"]}
    stage = workflow.paths.runs / workflow.item_id / "ui-exports" / receipt_id / "staging"
    snapshot = snapshot_export(stage, selected)
    if snapshot.evidence() != exported["publisher"]["export"]:
        raise LedgerError("private UI staging differs from the immutable controller export")
    prepared = _prepared_record(workflow, receipt_id)
    if prepared is not None and not _abandon_unapplied(workflow, exported, prepared):
        proof = _completed_install(workflow, exported, prepared)
        return _certify(workflow, exported, proof, prepared=prepared)
    initial_head = workflow.trees.git_in(client, "rev-parse", "HEAD")
    if workflow.trees.git_in(client, "status", "--porcelain"):
        raise LedgerError("first UI installation requires a clean owned Client at its pinned baseline; preserve interrupted work")
    if initial_head != baseline:
        records = installation_history(workflow.ledger, workflow.item_id)
        if not records:
            raise LedgerError("first UI installation requires its pinned baseline")
        last = records[-1]
        if not set(last["packages"]).issubset(exported["authority"]["changed_packages"]):
            raise LedgerError("a correction must retain all previously installed changed packages")
        from .fgui_scope import installed_scope
        installed_scope(workflow, last)
    dependencies = {p["name"]: [d["name"] for d in p["dependencies"]]
                    for p in exported["publisher"]["export"]["packages"]
                    if p["name"] in exported["authority"]["changed_packages"]}
    for name, deps in dependencies.items():
        if (name != "Common" and not (client / "Assets/GameRes/FairyRes" / name).exists()
                and "Common" not in deps):
            raise LedgerError("a new UI package must declare its required Common dependency")
    plan = prepare_install(snapshot, client, set(exported["authority"]["changed_packages"]))
    identity = uuid4().hex
    recovery = workflow.paths.runs / workflow.item_id / "ui-installations" / identity
    _plain_path(recovery.parent); recovery.parent.mkdir(parents=True, exist_ok=True); _plain_path(recovery.parent)
    def fence():
        workflow.fence()
        _, actual_branch, actual_base = _client(workflow)
        if (actual_branch != branch or actual_base != baseline
                or workflow.trees.git_in(client, "rev-parse", "HEAD") != initial_head):
            raise LedgerError("Client ownership/HEAD changed during UI installation")
    fence()
    # Derive the expected final GUID count before mutation from this exact plan.
    counts = Counter(dict(plan.before_global_guids))
    for files, direction in ((plan.before_files, -1), (plan.after_files, 1)):
        for name, data in files:
            if name.endswith(".meta"):
                values = _GUID.findall(data.decode("utf-8")); counts[values[0].lower()] += direction
    proof = {"format": "farmbot-fgui-client-v1", "installation_id": identity, "receipt_id": receipt_id,
             "branch": branch, "baseline": baseline, "source_head": exported["authority"]["head"],
             "initial_head": initial_head, "dependencies": dependencies,
             "packages": list(plan.packages), "plan": plan.evidence(), "files": _inventory(dict(plan.after_files)),
             "directories": sorted(plan.after_dirs), "global_guid_digest": _guid_digest(+counts)}
    proof["installation_sha256"] = hashlib.sha256(_canonical(proof).encode("utf-8")).hexdigest()
    prepared = {"format": "farmbot-fgui-client-prepared-v1", "proof": proof,
                "receipt_sha256": exported["receipt_sha256"], "before_files": _inventory(dict(plan.before_files)),
                "before_directories": sorted(plan.before_dirs),
                "before_global_guid_digest": _guid_digest(Counter(dict(plan.before_global_guids)))}
    prepared["prepared_sha256"] = hashlib.sha256(_canonical(prepared).encode("utf-8")).hexdigest()
    with workflow.ledger._transaction():
        _live(workflow.ledger, workflow.item_id, workflow.token, ("Farm-Client",))
        if _prepared_record(workflow, receipt_id) is not None:
            raise LedgerError("UI installation plan already exists; preserve concurrent recovery evidence")
        workflow.ledger._audit(workflow.item_id, "fgui_install_prepared", details=prepared)
    applied = apply_install(plan, recovery, fence=fence)
    if applied != plan.evidence():
        raise LedgerError("UI installer returned evidence different from its immutable plan")
    return _certify(workflow, exported, proof, prepared=prepared)
