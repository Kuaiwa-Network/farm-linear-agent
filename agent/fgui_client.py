"""Install only controller-certified UI exports into the pinned owned Client.

Interrupted writes retain the installer's journal. Neither a worker checkpoint
nor a dirty/changed branch can replace the controller's baseline or install proof.
"""
from collections import Counter
import hashlib
import json
from uuid import uuid4

from .fgui_export import snapshot_export
from .fgui_install import _GUID, _metadata_guids, _selected, apply_install, prepare_install
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
    client = workflow.paths.worktrees / workflow.item_id / "Farm-Client"
    _plain_path(client)
    if workflow.trees.git_in(client, "branch", "--show-current") != branch:
        raise LedgerError("UI installation requires the controller's exact owned Client issue branch")
    return client, branch, item["target"]["commit_sha"]


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


def install(workflow, receipt_id):
    workflow._stage("Farm-Client")
    exported = workflow.retained_export(receipt_id)
    client, branch, baseline = _client(workflow)
    previous = installation_record(workflow.ledger, workflow.item_id, receipt_id)
    if previous is not None:
        return assert_installed(workflow, previous)
    workflow.ledger.require_no_reservation(workflow.item_id)
    initial_head = workflow.trees.git_in(client, "rev-parse", "HEAD")
    if workflow.trees.git_in(client, "status", "--porcelain"):
        raise LedgerError("first UI installation requires a clean owned Client at its pinned baseline; preserve interrupted work")
    if initial_head != baseline:
        records = installation_records(workflow.ledger, workflow.item_id)
        if not records:
            raise LedgerError("first UI installation requires its pinned baseline")
        last = records[-1]
        if not set(last["packages"]).issubset(exported["authority"]["changed_packages"]):
            raise LedgerError("a correction must retain all previously installed changed packages")
        from .fgui_scope import installed_scope
        installed_scope(workflow, last)
    selected = {p["name"]: p["id"] for p in exported["publisher"]["export"]["packages"]}
    stage = workflow.paths.runs / workflow.item_id / "ui-exports" / receipt_id / "staging"
    snapshot = snapshot_export(stage, selected)
    if snapshot.evidence() != exported["publisher"]["export"]:
        raise LedgerError("private UI staging differs from the immutable controller export")
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
    applied = apply_install(plan, recovery, fence=fence)
    if applied != plan.evidence():
        raise LedgerError("UI installer returned evidence different from its immutable plan")
    # The apply helper already rechecks the full global inventory. Derive its
    # certified final count from that exact plan, avoiding another expensive scan.
    counts = Counter(dict(plan.before_global_guids))
    for files, direction in ((plan.before_files, -1), (plan.after_files, 1)):
        for name, data in files:
            if name.endswith(".meta"):
                values = _GUID.findall(data.decode("utf-8")); counts[values[0].lower()] += direction
    proof = {"format": "farmbot-fgui-client-v1", "installation_id": identity, "receipt_id": receipt_id,
             "branch": branch, "baseline": baseline, "source_head": exported["authority"]["head"],
             "initial_head": initial_head, "dependencies": dependencies,
             "packages": list(plan.packages), "plan": applied, "files": _inventory(dict(plan.after_files)),
             "directories": sorted(plan.after_dirs), "global_guid_digest": _guid_digest(+counts)}
    proof["installation_sha256"] = hashlib.sha256(_canonical(proof).encode("utf-8")).hexdigest()
    workflow.fence(refresh=True)
    if workflow.retained_export(receipt_id) != exported:
        raise LedgerError("UI export or authority changed during installation; preserve recovery evidence")
    fence()
    with workflow.ledger._transaction():
        _live(workflow.ledger, workflow.item_id, workflow.token, ("Farm-Client",))
        if installation_record(workflow.ledger, workflow.item_id, receipt_id) is not None:
            raise LedgerError("UI installation record already exists; preserve concurrent recovery evidence")
        workflow.ledger._audit(workflow.item_id, "fgui_install_complete", details=proof)
    return proof
