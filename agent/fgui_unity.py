"""Actual package loading in this UI job's verified interactive Unity slot.

The command probes through its own pinned MCP session. Worker text/files cannot
substitute for the loaded package results or the before/after identity checks.
Only newly registered probe packages are removed, in a finally block.
"""
import hashlib
from pathlib import Path

from .fgui_client import _client, installation_record
from .fgui_records import _canonical, _live
from .fgui_scope import verify_candidate
from .ledger import LedgerError
from .preview_upload import _plain_path
from .slots import UnityIdentity, slot_entry
from .uploads import _read_regular


def loading_source(packages, descriptors):
    from .fgui_export import _package
    if not isinstance(packages, dict) or not 0 < len(packages) <= 100:
        raise LedgerError("Unity UI loading requires bounded actual package identities")
    for name, identity in packages.items():
        _package(name, identity)
    values = ",\n".join('{"' + name + '", "' + identity + '"}' for name, identity in sorted(packages.items()))
    if not isinstance(descriptors, dict) or set(descriptors) != set(packages) or any(not isinstance(digest, str) or len(digest) != 64
            or any(c not in '0123456789abcdef' for c in digest) for digest in descriptors.values()):
        raise LedgerError("Unity UI descriptor hashes are invalid")
    hashes = ",\n".join('{"' + name + '", "' + digest + '"}' for name, digest in sorted(descriptors.items()))
    template = Path(__file__).with_name("probes") / "fgui-loading.cs.txt"
    return template.read_text(encoding="utf-8").replace("/* FARM_PACKAGE_IDENTITIES */", values).replace("/* FARM_DESCRIPTOR_HASHES */", hashes)


def _reservation(workflow, commit):
    workflow.fence()
    reservation = workflow.ledger.active_reservation(workflow.item_id)
    if (not reservation or reservation["state"] != "active" or reservation["mode"] != "interactive"
            or reservation["kind"] != "unity_slot" or reservation["commit_sha"] != commit):
        raise LedgerError("UI loading requires this job's active interactive reservation at the exact committed candidate")
    slot = workflow.ledger.slot(reservation["resource"])
    if (not slot or slot["state"] != "interactive_busy" or not slot.get("instance")
            or slot.get("parked_commit") != commit):
        raise LedgerError("UI loading requires the controller's committed and connected Unity slot")
    entries = [slot_entry(entry) for entry in workflow.config.slots if entry.get("id") == slot["slot_id"]]
    if len(entries) != 1 or not entries[0]["build_target"]:
        raise LedgerError("UI loading requires the configured slot's explicit build target")
    entry = entries[0]
    folder = Path(entry["folder"]) if entry["folder"] else workflow.paths.editors / ("slot-" + slot["slot_id"].split(":")[-1])
    if (entry["kind"] != "unity_slot" or entry["repo"] != "Farm-Client"
            or Path(slot["folder"]).resolve() != folder.resolve() or slot["mcp_address"] != entry["mcp_address"]):
        raise LedgerError("UI loading slot identity differs from its current configured Client slot")
    return reservation, slot, entries[0]


def verify_loading(workflow, receipt_id):
    scope = verify_candidate(workflow, receipt_id)
    reservation, slot, entry = _reservation(workflow, scope["commit"])
    # Current exported dependency closure is validated privately, but only changed
    # packages were installed. Every closure descriptor is read from the actual
    # slot; loading verifies the Client's unchanged dependencies as well.
    exported = workflow.retained_export(receipt_id)
    packages = {p["name"]: p["id"] for p in exported["publisher"]["export"]["packages"]}
    client_root, _, _ = _client(workflow)
    descriptors = {}
    for name, identity in packages.items():
        path = client_root / "Assets/GameRes/FairyRes" / name / (name + "_fui.bytes")
        _plain_path(path)
        raw = _read_regular(path, 16 << 20)
        from .fgui_export import descriptor
        descriptor(raw, name, identity)  # an unhydrated pointer cannot establish runtime identity
        descriptors[name] = hashlib.sha256(raw).hexdigest()
    source = loading_source(packages, descriptors)
    identity = UnityIdentity(Path(__file__).with_name("probes") / "editor-readiness.cs.txt")
    target = {"repository": slot["folder"], "commit_sha": scope["commit"], "build_target": entry["build_target"]}
    before = identity.probe(slot, target)
    if before.get("aggregate") != "match":
        raise LedgerError("Unity UI loading has no verified pre-probe Editor/source identity")
    workflow.fence(refresh=True)
    if _reservation(workflow, scope["commit"])[0]["reservation_id"] != reservation["reservation_id"]:
        raise LedgerError("UI Unity reservation changed before loading")
    client = identity._client(slot)
    result = client.call_tool("execute_code", {"action": "execute", "code": source, "safety_checks": True}).get("result")
    after = identity.probe(slot, target)
    workflow.fence(refresh=True)
    current, actual_slot, _ = _reservation(workflow, scope["commit"])
    if (current["reservation_id"] != reservation["reservation_id"] or actual_slot != slot
            or after.get("aggregate") != "match"
            or before.get("source_after") != after.get("source_before")):
        raise LedgerError("Unity UI loading requires unchanged verified Editor/source and reservation identity")
    if (not isinstance(result, dict) or set(result) != {"packages", "cleanup_complete"}
            or result["cleanup_complete"] is not True or not isinstance(result["packages"], list)):
        raise LedgerError("Unity UI loading has incomplete actual package or cleanup evidence")
    observed = {}
    for package in result["packages"]:
        if (not isinstance(package, dict) or set(package) != {"name", "id", "items", "disk_assets", "descriptor_sha256"}
                or package.get("name") in observed or packages.get(package.get("name")) != package.get("id")
                or descriptors.get(package.get("name")) != package.get("descriptor_sha256")
                or type(package.get("items")) is not int or not 0 < package["items"] <= 10000
                or type(package.get("disk_assets")) is not int or not 0 <= package["disk_assets"] <= package["items"]):
            raise LedgerError("Unity UI loading returned mismatched or malformed package evidence")
        observed[package["name"]] = package["id"]
    if observed != packages:
        raise LedgerError("Unity UI loading did not verify every actual export/dependency package")
    installed = installation_record(workflow.ledger, workflow.item_id, receipt_id)
    # Do not turn a probe of an old installation into readiness after a correction.
    if verify_candidate(workflow, receipt_id) != scope:
        raise LedgerError("UI candidate changed during actual Unity loading")
    proof = {"format": "farmbot-fgui-unity-v1", "receipt_id": receipt_id,
             "installation_sha256": installed["installation_sha256"], "commit": scope["commit"],
             "source_head": scope["source_head"], "reservation_id": reservation["reservation_id"],
             "slot_id": slot["slot_id"], "probe_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
             "packages": result["packages"], "cleanup_complete": True}
    proof["unity_sha256"] = hashlib.sha256(_canonical(proof).encode("utf-8")).hexdigest()
    with workflow.ledger._transaction():
        _live(workflow.ledger, workflow.item_id, workflow.token, ("Farm-Client",))
        current = workflow.ledger.active_reservation(workflow.item_id)
        if not current or current["state"] != "active" or current["reservation_id"] != reservation["reservation_id"]:
            raise LedgerError("UI Unity reservation ended before recording its result")
        workflow.ledger._audit(workflow.item_id, "fgui_unity_complete", details=proof)
    return proof
