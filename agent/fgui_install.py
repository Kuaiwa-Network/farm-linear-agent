"""Prepared package-only Client installation with immutable plans and recovery.

No worker command/grant selects this helper yet. The future caller must own the
Client checkout, authorize the changed package set, and supply a claim/Stop fence
for every mutation. This is not directory-atomic installation or automatic rollback.
"""
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

from .fgui_export import ExportSnapshot, MAX_BYTES, MAX_FILES, MAX_FILE_BYTES, _relative, descriptor
from .ledger import LedgerError
from .preview_upload import _plain_path
from .uploads import _read_regular

MAX_METADATA_BYTES = 64 << 10
MAX_METADATA_FILES = 50000
MAX_DELETE_COUNT = 200
MAX_DELETE_RATIO = 0.8
_GUID = re.compile(r"^guid: ([0-9a-fA-F]{32})\r?$", re.M)
_IMPORTERS = {"folder": "DefaultImporter", "descriptor": "TextScriptImporter",
              "atlas": "TextureImporter", "sound": "AudioImporter"}


def _name(value):
    # Metadata belongs to its ordinary file/directory; it is never export input.
    _relative(value[:-5] if value.endswith(".meta") else value)
    return value


def _bytes(path, limit):
    _plain_path(path)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise LedgerError("Client installation requires unlinked regular files")
    data = _read_regular(path, limit)
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns,
            before.st_nlink) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
                                after.st_ctime_ns, after.st_nlink):
        raise LedgerError("Client input changed while reading")
    _plain_path(path)
    return data


def _walk(root, *, metadata_only=False):
    files, dirs = {}, set()
    entries = total_bytes = 0

    def visit(path, depth):
        nonlocal entries, total_bytes
        _plain_path(path)
        if depth > 32:
            raise LedgerError("Client input directory depth exceeds bounds")
        dirs.add(path.relative_to(root).as_posix())
        names = sorted(path.iterdir(), key=lambda p: p.name)
        folded = set()
        for child in names:
            entries += 1
            if entries > (100000 if metadata_only else MAX_FILES * 2):
                raise LedgerError("Client input inventory exceeds bounds")
            _plain_path(child)
            if child.name.casefold() in folded:
                raise LedgerError("Client input contains case-colliding entries")
            folded.add(child.name.casefold())
            value = child.stat()
            if stat.S_ISDIR(value.st_mode):
                visit(child, depth + 1)
            elif stat.S_ISREG(value.st_mode):
                if metadata_only and not child.name.endswith(".meta"):
                    continue
                if len(files) >= (MAX_METADATA_FILES if metadata_only else MAX_FILES):
                    raise LedgerError("Client input file count exceeds bounds")
                total_bytes += value.st_size
                if total_bytes > MAX_BYTES:
                    raise LedgerError("Client input aggregate bytes exceed bounds")
                relative = child.relative_to(root).as_posix()
                if not metadata_only:
                    _name(relative)
                files[relative] = _bytes(child, MAX_METADATA_BYTES if child.name.endswith(".meta") else MAX_FILE_BYTES)
            else:
                raise LedgerError("Client input contains a non-ordinary entry")
        _plain_path(path)
        if tuple(p.name for p in sorted(path.iterdir(), key=lambda p: p.name)) != tuple(p.name for p in names):
            raise LedgerError("Client directory membership changed while reading")
    visit(root, 0)
    if sum(len(b) for b in files.values()) > MAX_BYTES:
        raise LedgerError("Client input aggregate bytes exceed bounds")
    return files, dirs


def _metadata(data, kind):
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeError:
        raise LedgerError("Client importer metadata is not UTF-8") from None
    guid = _GUID.findall(text)
    importer = _IMPORTERS.get(kind)
    if (importer is None or len(guid) != 1 or len(re.findall(r"^guid:", text, re.M)) != 1
            or len(re.findall(r"^fileFormatVersion: 2\r?$", text, re.M)) != 1
            or re.findall(r"^([A-Za-z][A-Za-z0-9]*Importer):\r?$", text, re.M) != [importer]):
        raise LedgerError("Client importer metadata has an invalid GUID, format or importer")
    fields = ["externalObjects", "userData", "assetBundleName", "assetBundleVariant"]
    if kind == "folder":
        if len(re.findall(r"^folderAsset: yes\r?$", text, re.M)) != 1:
            raise LedgerError("Client folder metadata is incomplete")
    elif re.search(r"^folderAsset:", text, re.M):
        raise LedgerError("Client asset metadata cannot declare a folder")
    if kind == "atlas":
        fields += ["serializedVersion", "mipmaps", "textureSettings", "platformSettings", "spriteSheet",
                   "maxTextureSize", "textureType", "alphaIsTransparency"]
        for field in ("internalIDToNameTable",):
            if not re.search(r"^  " + field + r": \[\]\r?$", text, re.M):
                raise LedgerError("Client atlas template has unmodeled sprite registrations")
        if not re.search(r"^    sprites: \[\]\r?$", text, re.M):
            raise LedgerError("Client atlas template has unmodeled sprite registrations")
    elif kind == "sound":
        fields += ["serializedVersion", "defaultSettings", "platformSettingOverrides", "forceToMono",
                   "normalize", "loadInBackground", "ambisonic", "3D"]
    for field in fields:
        if len(re.findall(r"^  " + field + r":.*\r?$", text, re.M)) != 1:
            raise LedgerError("Client importer metadata is incomplete")
    return guid[0].lower(), text


def _metadata_guids(client):
    files, _ = _walk(client / "Assets", metadata_only=True)
    counts = Counter()
    for data in files.values():
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeError:
            raise LedgerError("Client metadata GUID inventory is not UTF-8") from None
        values = _GUID.findall(text)
        if len(values) != 1 or len(re.findall(r"^guid:", text, re.M)) != 1:
            raise LedgerError("Client metadata GUID inventory is invalid")
        counts[values[0].lower()] += 1
    return counts


def _selected(root, packages):
    files, dirs = {}, set()
    folded = {p.name.casefold(): p.name for p in root.iterdir()}
    for package in packages:
        _relative(package)
        for name in (package, package + ".meta"):
            if name.casefold() in folded and folded[name.casefold()] != name:
                raise LedgerError("Client package destination has a case collision")
        path = root / package
        _plain_path(path)
        meta = root / (package + ".meta")
        _plain_path(meta)
        if path.exists():
            if not path.is_dir() or not meta.is_file():
                raise LedgerError("Client package destination or folder metadata is invalid")
            contents, folders = _walk(path)
            files.update({package + "/" + n: b for n, b in contents.items()})
            dirs.update(package if n == "." else package + "/" + n for n in folders)
            files[package + ".meta"] = _bytes(meta, MAX_METADATA_BYTES)
        elif meta.exists():
            raise LedgerError("Client new package collides with orphan folder metadata")
        if len(files) > MAX_FILES or sum(len(b) for b in files.values()) > MAX_BYTES:
            raise LedgerError("Client selected package inventory exceeds bounds")
    return files, dirs


@dataclass(frozen=True)
class InstallPlan:
    client: Path
    packages: tuple
    before_files: tuple
    before_dirs: tuple
    after_files: tuple
    after_dirs: tuple
    before_global_guids: tuple

    def evidence(self):
        before, after = dict(self.before_files), dict(self.after_files)
        return {"format": "farmbot-fgui-install-v1", "packages": list(self.packages),
                "writes": [{"file": n, "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}
                           for n, b in self.after_files if before.get(n) != b],
                "deletes": sorted(set(before) - set(after)),
                "created_directories": sorted(set(self.after_dirs) - set(self.before_dirs)),
                "deleted_directories": sorted(set(self.before_dirs) - set(self.after_dirs))}


def prepare_install(snapshot, client, changed_packages, *, new_guid=None):
    """Plan only the explicitly changed packages; unchanged dependencies stay private."""
    if not isinstance(snapshot, ExportSnapshot):
        raise LedgerError("Client installation requires an immutable validated export")
    client = Path(client)
    root = client / "Assets/GameRes/FairyRes"
    _plain_path(client); _plain_path(root)
    if not root.is_dir():
        raise LedgerError("Client installation requires the existing FairyRes root")
    package_map = {p.name: p.identity for p in snapshot.packages}
    if (len(package_map) != len(snapshot.packages) or len(snapshot.artifacts) > MAX_FILES
            or any(not isinstance(a.data, bytes) or not 0 < len(a.data) <= MAX_FILE_BYTES for a in snapshot.artifacts)
            or sum(len(a.data) for a in snapshot.artifacts) > MAX_BYTES):
        raise LedgerError("Client export snapshot exceeds bounds or has duplicate identities")
    if (not isinstance(changed_packages, (list, tuple, set, frozenset)) or not changed_packages
            or len(set(changed_packages)) != len(changed_packages)
            or any(name not in package_map for name in changed_packages)):
        raise LedgerError("Client installation requires an explicit changed-package subset")
    packages = tuple(sorted(changed_packages))
    for package in snapshot.packages:
        artifacts = {a.relative: a for a in snapshot.artifacts if a.package == package.name}
        desc_name = package.name + "_fui.bytes"
        if desc_name not in artifacts or descriptor(artifacts[desc_name].data, package.name, package.identity) != package:
            raise LedgerError("Client export descriptor snapshot identity changed")
        expected = {desc_name: "descriptor", **{i.relative: i.kind for i in package.files}}
        if (len(artifacts) != sum(a.package == package.name for a in snapshot.artifacts)
                or set(expected) != set(artifacts) or any(artifacts[n].kind != kind for n, kind in expected.items())):
            raise LedgerError("Client export snapshot inventory changed")
    if any(a.package not in package_map for a in snapshot.artifacts):
        raise LedgerError("Client export snapshot has an unexpected package")
    before, before_dirs = _selected(root, packages)
    if any(data.startswith(b"version https://git-lfs.github.com/spec/v1") for name, data in before.items() if not name.endswith(".meta")):
        raise LedgerError("Client selected package inputs must be hydrated before comparison")
    guids = _metadata_guids(client)
    # Sibling templates are metadata only; never hydrate or copy an unrelated package.
    sibling_meta, _ = _walk(root, metadata_only=True)
    if _selected(root, packages) != (before, before_dirs):
        raise LedgerError("Client selected packages changed during planning")
    after, after_dirs, kinds = {}, set(), {}
    for a in snapshot.artifacts:
        if a.package not in packages:
            continue
        if a.kind not in ("descriptor", "atlas", "sound"):
            raise LedgerError("Client installation has no reviewed metadata model for this artifact type")
        if a.kind == "sound" and Path(a.relative).suffix.lower() not in (".mp3", ".wav", ".ogg", ".aif", ".aiff"):
            raise LedgerError("Client sound artifact has an unmodeled importer suffix")
        _relative(a.relative)
        destination = a.package + "/" + a.relative
        after[destination] = a.data
        kinds[destination] = a.kind
        parent = Path(destination).parent
        while parent.as_posix() != ".":
            after_dirs.add(parent.as_posix()); parent = parent.parent
    for folder in after_dirs:
        kinds[folder] = "folder"
    if set(after).intersection(before_dirs) or set(after_dirs).intersection(before):
        raise LedgerError("Client installation cannot replace a file with a directory or vice versa")
    generate = new_guid or (lambda: uuid.uuid4().hex)
    reserved = set(guids)
    for name, kind in sorted(kinds.items()):
        meta_name = name + ".meta"
        if meta_name in before:
            guid, _ = _metadata(before[meta_name], kind)
            if guids[guid] != 1:
                raise LedgerError("Client affected metadata GUID is not globally unique")
            after[meta_name] = before[meta_name]
            continue
        if name in before or name in before_dirs:
            raise LedgerError("Client existing artifact or folder is missing its metadata")
        package = name.split("/")[0]
        candidates = sorted(sibling_meta, key=lambda n: (not n.startswith(package + "/"), n))
        template = None
        for candidate in candidates:
            # Only a live sibling file/folder can supply an importer template.
            if not (root / candidate[:-5]).exists():
                continue
            data = sibling_meta[candidate]
            if not re.search(rb"^" + _IMPORTERS[kind].encode("ascii") + rb":\r?$", data, re.M):
                continue  # Other importer classes are not templates of this kind.
            if kind == "folder" and not re.search(rb"^folderAsset: yes\r?$", data, re.M):
                continue
            guid, text = _metadata(data, kind)
            if guids[guid] == 1:
                template = text
                break
        if template is None:
            raise LedgerError("Client installation lacks a complete unique sibling importer template")
        fresh = generate()
        if not isinstance(fresh, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", fresh) or fresh.lower() in reserved:
            raise LedgerError("Client new metadata requires a fresh unique GUID")
        reserved.add(fresh.lower())
        match = _GUID.search(template)
        text = template[:match.start(1)] + fresh.lower() + template[match.end(1):]
        after[meta_name] = text.encode("utf-8")
    for package in packages:
        old = {n for n in before if n.startswith(package + "/")}
        removed = old - set(after)
        if len(removed) > MAX_DELETE_COUNT or (old and len(removed) / len(old) > MAX_DELETE_RATIO):
            raise LedgerError("Client package mirror exceeds the deletion safety limit; a scope ruling is required")
    if len(after) > MAX_FILES or sum(len(b) for b in after.values()) > MAX_BYTES:
        raise LedgerError("Client planned package outputs exceed bounds")
    if _metadata_guids(client) != guids:
        raise LedgerError("Client global metadata GUID inventory changed during planning")
    return InstallPlan(client, packages, tuple(sorted(before.items())), tuple(sorted(before_dirs)),
                       tuple(sorted(after.items())), tuple(sorted(after_dirs)), tuple(sorted(guids.items())))


def apply_install(plan, recovery_root, *, fence):
    """Apply a reviewed plan with per-mutation claim/Stop fences and retained journal.

    The caller must arrange exclusive ownership/quiescence of this Client checkout.
    Recovery stores the old bytes and completed operation names. On interruption no
    automatic rollback, deletion of evidence, or directory-atomic success is claimed.
    """
    if not isinstance(plan, InstallPlan) or not callable(fence):
        raise LedgerError("Client installation requires an owned plan and explicit claim fence")
    root = plan.client / "Assets/GameRes/FairyRes"
    recovery = Path(recovery_root)
    _plain_path(root); _plain_path(recovery)
    if (recovery.is_relative_to(plan.client) or plan.client.is_relative_to(recovery)
            or recovery.exists()):
        raise LedgerError("Client recovery must be a new private directory outside the checkout")
    before, after = dict(plan.before_files), dict(plan.after_files)
    for collection in (plan.before_files, plan.after_files):
        if (len(collection) != len(dict(collection)) or len(collection) > MAX_FILES
                or sum(len(b) for _, b in collection) > MAX_BYTES):
            raise LedgerError("Client installation plan exceeds bounds or has duplicate files")
        for name, data in collection:
            _name(name)
            if (not isinstance(data, bytes) or len(data) > MAX_FILE_BYTES
                    or not any(name == p + ".meta" or name.startswith(p + "/") for p in plan.packages)):
                raise LedgerError("Client installation plan escapes its selected packages")
    for name in (*plan.before_dirs, *plan.after_dirs):
        _relative(name)
        if not any(name == p or name.startswith(p + "/") for p in plan.packages):
            raise LedgerError("Client installation directories escape their selected packages")
    fence()
    if _selected(root, plan.packages) != (before, set(plan.before_dirs)):
        raise LedgerError("Client packages changed after the installation plan")
    global_guids = Counter(dict(plan.before_global_guids))
    if _metadata_guids(plan.client) != global_guids:
        raise LedgerError("Client global metadata GUID inventory changed after the plan")
    recovery.mkdir(parents=True, exist_ok=False)
    journal = {**plan.evidence(), "state": "prepared", "completed": [],
               "before": [{"file": n, "backup": str(i), "sha256": hashlib.sha256(b).hexdigest()}
                          for i, (n, b) in enumerate(plan.before_files)]}

    def record():
        data = (json.dumps(journal, indent=2) + "\n").encode("utf-8")
        temporary = recovery / ("journal-" + uuid.uuid4().hex + ".tmp")
        _plain_path(recovery)
        with temporary.open("xb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        _plain_path(recovery / "journal.json")
        os.replace(temporary, recovery / "journal.json")

    try:
        for i, (_, data) in enumerate(plan.before_files):
            fence()
            _plain_path(recovery)
            with (recovery / str(i)).open("xb") as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
        fence(); record()
        if _selected(root, plan.packages) != (before, set(plan.before_dirs)):
            raise LedgerError("Client packages changed during recovery preparation")
        journal["state"] = "applying"; record()
        for name in sorted(set(plan.after_dirs) - set(plan.before_dirs), key=lambda n: (n.count("/"), n)):
            fence()
            path = root / _name(name); _plain_path(path)
            path.mkdir(exist_ok=False)
            journal["completed"].append({"mkdir": name}); record()
        for name, data in plan.after_files:
            if before.get(name) == data:
                continue
            fence()
            path = root / _name(name); _plain_path(path)
            if name in before:
                if _bytes(path, MAX_FILE_BYTES) != before[name]:
                    raise LedgerError("Client destination changed before replacement")
            elif path.exists():
                raise LedgerError("Client new artifact destination appeared during installation")
            temporary = path.parent / (".farmbot-install-" + uuid.uuid4().hex)
            with temporary.open("xb") as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            fence(); _plain_path(path)
            if (name in before and _bytes(path, MAX_FILE_BYTES) != before[name]) or (name not in before and path.exists()):
                raise LedgerError("Client destination changed during temporary preparation")
            os.replace(temporary, path)
            journal["completed"].append({"write": name}); record()
        for name in sorted(set(before) - set(after), key=lambda n: (-n.count("/"), n)):
            fence()
            path = root / _name(name); _plain_path(path)
            if _bytes(path, MAX_FILE_BYTES) != before[name]:
                raise LedgerError("Client stale artifact changed before removal")
            path.unlink()
            journal["completed"].append({"delete": name}); record()
        for name in sorted(set(plan.before_dirs) - set(plan.after_dirs), key=lambda n: (-n.count("/"), n)):
            fence()
            path = root / _name(name); _plain_path(path)
            path.rmdir()  # Refuse rather than recursively deleting a new/unknown entry.
            journal["completed"].append({"rmdir": name}); record()
        fence()
        if _selected(root, plan.packages) != (after, set(plan.after_dirs)):
            raise LedgerError("Client installed package inventory does not match the plan")
        for files, direction in ((before, -1), (after, 1)):
            for name, data in files.items():
                if name.endswith(".meta"):
                    values = _GUID.findall(data.decode("utf-8", errors="strict"))
                    if len(values) != 1:
                        raise LedgerError("Client installed metadata GUID evidence is invalid")
                    global_guids[values[0].lower()] += direction
        if _metadata_guids(plan.client) != +global_guids:
            raise LedgerError("Client global metadata GUID inventory changed during installation")
        journal["state"] = "complete"; record()
        return plan.evidence()
    except Exception:
        journal["state"] = "interrupted"
        try:
            record()
        except (OSError, LedgerError):
            pass
        raise
