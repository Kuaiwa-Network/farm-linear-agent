"""Reconstruct a legacy complete install from immutable Git and certified bytes.

The journal locates retained evidence; it supplies no authority. Only a first
install replacing existing artifacts, with all metadata unchanged, is supported.
Nothing in the Client checkout or retained recovery directory is written here.
"""
from collections import Counter
import hashlib
import json
import re

from .fgui_export import MAX_BYTES, MAX_FILES, MAX_FILE_BYTES
from .fgui_install import InstallPlan, MAX_METADATA_FILES, _GUID, _bytes
from .fgui_records import _canonical
from .fgui_source import _POINTER, _git, _tree
from .ledger import LedgerError
from .preview_upload import _plain_path


def reconstruct(workflow, exported, snapshot, client, branch, baseline):
    from .fgui_client import _guid_digest, _inventory, installation_history

    def refuse():
        raise LedgerError("first UI installation requires a clean owned Client at its pinned baseline; "
                          "legacy UI installation cannot be proven; preserve recovery evidence")

    if (workflow.trees.git_in(client, "rev-parse", "HEAD") != baseline
            or installation_history(workflow.ledger, workflow.item_id)):
        refuse()
    root = workflow.paths.runs / workflow.item_id / "ui-installations"
    _plain_path(root)
    if not root.is_dir():
        raise LedgerError("first UI installation requires a clean owned Client at its pinned baseline; preserve interrupted work")
    entries = list(root.iterdir())
    if len(entries) != 1 or not re.fullmatch(r"[0-9a-f]{32}", entries[0].name):
        refuse()
    recovery = entries[0]
    _plain_path(recovery)
    raw_journal = _bytes(recovery / "journal.json", 4 << 20)
    try:
        journal = json.loads(raw_journal.decode("utf-8"))
    except (ValueError, UnicodeError):
        refuse()
    if not isinstance(journal, dict) or journal.get("state") != "complete":
        refuse()
    packages = tuple(sorted(exported["authority"]["changed_packages"]))
    prefix = "Assets/GameRes/FairyRes/"
    paths = [prefix + name for package in packages for name in (package, package + ".meta")]
    tracked = _tree(_git(workflow.trees, client,
        ["ls-tree", "-r", "-z", "--full-tree", baseline, "--", *paths], repo="Farm-Client"))
    if len(tracked) > MAX_FILES:
        refuse()
    before, directories, total = {}, set(packages), 0
    for index, name in enumerate(sorted(tracked)):
        relative = name.removeprefix(prefix)
        if (tracked[name][0] != "100644" or not name.startswith(prefix)
                or not any(relative == p + ".meta" or relative.startswith(p + "/") for p in packages)):
            refuse()
        # Do not trust the journal's filenames, hashes, ordering or backup paths.
        data = _bytes(recovery / str(index), MAX_FILE_BYTES)
        total += len(data)
        if total > MAX_BYTES:
            refuse()
        raw = _git(workflow.trees, client, ["show", baseline + ":" + name],
                   repo="Farm-Client", limit=MAX_FILE_BYTES)
        pointer = _POINTER.fullmatch(raw)
        if pointer:
            if len(data) != int(pointer[2]) or hashlib.sha256(data).hexdigest() != pointer[1].decode("ascii"):
                refuse()
        elif data != raw:
            # Native Windows checkouts may retain CRLF in unchanged Unity metadata.
            if not name.endswith(".meta") or data.replace(b"\r\n", b"\n") != raw.replace(b"\r\n", b"\n"):
                refuse()
        before[relative] = data
        parts = relative.split("/")
        directories.update("/".join(parts[:n]) for n in range(1, len(parts)))
    outputs = {a.package + "/" + a.relative: a.data for a in snapshot.artifacts if a.package in packages}
    if (set(outputs) != {n for n in before if not n.endswith(".meta")}
            or not all(p + ".meta" in before for p in packages)):
        refuse()  # No new files, deleted artifacts, new GUIDs or directory changes.
    after = {**before, **outputs}
    committed_guids = _git(workflow.trees, client, ["grep", "-h", "-I", "-E",
        "^guid:[[:space:]]*[[:xdigit:]]{32}[[:space:]]*$", baseline, "--", ":(glob)Assets/**/*.meta"],
        repo="Farm-Client", limit=MAX_METADATA_FILES * 100).decode("utf-8")
    counts = Counter(value.lower() for value in _GUID.findall(committed_guids))
    digest = _guid_digest(counts)
    plan = InstallPlan(client, packages, tuple(sorted(before.items())), tuple(sorted(directories)),
                       tuple(sorted(after.items())), tuple(sorted(directories)), tuple(sorted(counts.items())))
    dependencies = {p["name"]: [d["name"] for d in p["dependencies"]]
                    for p in exported["publisher"]["export"]["packages"] if p["name"] in packages}
    proof = {"format": "farmbot-fgui-client-v1", "installation_id": recovery.name,
             "receipt_id": exported["receipt_id"], "branch": branch, "baseline": baseline,
             "source_head": exported["authority"]["head"], "initial_head": baseline,
             "dependencies": dependencies, "packages": list(packages), "plan": plan.evidence(),
             "files": _inventory(after), "directories": sorted(directories), "global_guid_digest": digest,
             "recovery": {"method": "legacy-baseline-existing-files-v1",
                          "journal_sha256": hashlib.sha256(raw_journal).hexdigest()}}
    proof["installation_sha256"] = hashlib.sha256(_canonical(proof).encode("utf-8")).hexdigest()
    prepared = {"format": "farmbot-fgui-client-prepared-v1", "proof": proof,
                "receipt_sha256": exported["receipt_sha256"], "before_files": _inventory(before),
                "before_directories": sorted(directories), "before_global_guid_digest": digest}
    # This is a reconstruction used for validation, never a backdated pre-write audit.
    prepared["prepared_sha256"] = hashlib.sha256(_canonical(prepared).encode("utf-8")).hexdigest()
    workflow.fence()
    return prepared
