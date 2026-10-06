"""Read-only, immutable FairyGUI export inventory for the reviewed Unity v7 format.

This module runs no publisher, installs no Client assets and grants no worker
authority. The caller supplies its selected package identities and fresh private
staging after the publisher has quiesced. Acceptance covers container/inventory
integrity; the Client guards and actual Unity loading remain separate checks.
"""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import stat
import struct

from .ledger import LedgerError
from .preview_upload import _plain_path, read_image
from .uploads import _read_regular

MAX_PACKAGES = 100
MAX_FILES = 1000
MAX_BYTES = 512 << 20
MAX_FILE_BYTES = 64 << 20
MAX_DESCRIPTOR_BYTES = 16 << 20
MAX_STRINGS = 32768
MAX_STRING_BYTES = 4 << 20
MAX_DEPTH = 16
_DISK_TYPES = {2: "sound", 4: "atlas", 7: "misc"}
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
             *(f"lpt{i}" for i in range(1, 10))}


def _relative(value):
    if (not isinstance(value, str) or not value or len(value) > 512
            or len(value.split("/")) > MAX_DEPTH
            or any(c in value for c in '\\:<>"|?*') or any(ord(c) < 32 for c in value)):
        raise LedgerError("export artifact name is not a portable relative path")
    for part in value.split("/"):
        if (not part or part in (".", "..") or part.startswith(".")
                or part.endswith((".", " ")) or part.split(".")[0].casefold() in _RESERVED
                or part.casefold().endswith(".meta")):
            raise LedgerError("export artifact name is not a portable relative path")
    return value


def _package(name, identity):
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name):
        raise LedgerError("export package name is invalid")
    _relative(name)
    if not isinstance(identity, str) or not re.fullmatch(r"[a-z0-9]{8}", identity):
        raise LedgerError("export package identity is invalid")


class _Reader:
    def __init__(self, data, start=0, end=None):
        self.data, self.pos = data, start
        self.end = len(data) if end is None else end

    def take(self, length):
        if not isinstance(length, int) or length < 0 or length > self.end - self.pos:
            raise LedgerError("export descriptor is truncated or has an invalid length")
        value = self.data[self.pos:self.pos + length]
        self.pos += length
        return value

    def number(self, fmt):
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0]

    def byte(self):
        return self.number(">B")

    def boolean(self):
        value = self.byte()
        if value not in (0, 1):
            raise LedgerError("export descriptor has an invalid boolean")
        return bool(value)

    def u16(self):
        return self.number(">H")

    def count(self, fmt, maximum):
        count = self.number(fmt)
        if not 0 <= count <= maximum:
            raise LedgerError("export descriptor count exceeds bounds")
        return count

    def text(self, length=None):
        length = self.u16() if length is None else length
        if not 0 <= length <= MAX_STRING_BYTES:
            raise LedgerError("export descriptor string exceeds bounds")
        try:
            return self.take(length).decode("utf-8", errors="strict")
        except UnicodeError:
            raise LedgerError("export descriptor string is not UTF-8") from None

    def string(self, table):
        index = self.u16()
        if index == 65534:
            return None
        if index == 65533:
            return ""
        if index >= len(table):
            raise LedgerError("export descriptor string index is invalid")
        return table[index]

    def finished(self):
        if self.pos != self.end:
            raise LedgerError("export descriptor block has unexpected trailing data")


@dataclass(frozen=True)
class DiskItem:
    identity: str
    kind: str
    relative: str
    width: int
    height: int


@dataclass(frozen=True)
class Descriptor:
    name: str
    identity: str
    dependencies: tuple
    files: tuple
    item_count: int
    ambiguous_names: tuple


def descriptor(data, name, identity):
    """Parse bounded v7 package/dependency/item inventory like Client UIPackage.

    Full component/animation/font raw payload interpretation is intentionally left
    to Unity; their bounded record lengths are checked here. Unknown container
    versions or disk-bearing types must be modeled before they can be accepted.
    """
    _package(name, identity)
    if not isinstance(data, bytes) or not data or len(data) > MAX_DESCRIPTOR_BYTES:
        raise LedgerError("export descriptor exceeds bounds")
    r = _Reader(data)
    if r.take(4) != b"FGUI":
        raise LedgerError("export descriptor has invalid magic or is an LFS pointer")
    if r.number(">i") != 7:
        raise LedgerError("export descriptor version is not the reviewed Unity v7 format")
    if r.boolean():
        raise LedgerError("compressed export descriptors are not supported by the Client guards")
    if r.text() != identity or r.text() != name:
        raise LedgerError("export descriptor does not match the selected package identity")
    r.take(20)
    index = r.pos
    count = r.count(">B", 6)
    if count != 6:
        raise LedgerError("export descriptor index does not match the reviewed Unity layout")
    short = r.boolean()
    offsets = [r.number(">H" if short else ">i") for _ in range(count)]
    starts = [index + offset for offset in offsets if offset]
    if (any(offset < 0 for offset in offsets) or len(set(starts)) != len(starts)
            or any(start < r.pos or start >= len(data) for start in starts)):
        raise LedgerError("export descriptor block offsets are invalid or overlap")
    ordered = sorted(starts) + [len(data)]

    def block(number, *, required=True):
        if not offsets[number]:
            if required:
                raise LedgerError("export descriptor is missing a required block")
            return None
        start = index + offsets[number]
        return _Reader(data, start, ordered[ordered.index(start) + 1])

    strings = block(4)
    table = [strings.text() for _ in range(strings.count(">i", MAX_STRINGS))]
    strings.finished()
    overrides = block(5, required=False)
    if overrides:
        seen = set()
        for _ in range(overrides.count(">i", len(table))):
            idx = overrides.u16()
            if idx >= len(table) or idx in seen:
                raise LedgerError("export descriptor string override is invalid or duplicate")
            seen.add(idx)
            table[idx] = overrides.text(overrides.number(">i"))
        overrides.finished()

    deps = block(0)
    dependencies, dep_ids, dep_names = [], set(), set()
    for _ in range(deps.count(">h", MAX_PACKAGES)):
        did, dname = deps.string(table), deps.string(table)
        _package(dname, did)
        if did == identity or dname == name or did in dep_ids or dname.casefold() in dep_names:
            raise LedgerError("export descriptor dependency is self-referential or duplicate")
        dep_ids.add(did)
        dep_names.add(dname.casefold())
        dependencies.append((dname, did))
    branches = deps.count(">h", 100)
    for _ in range(branches):
        if not deps.string(table):
            raise LedgerError("export descriptor branch identity is missing")
    deps.finished()

    items = block(1)
    total = items.count(">h", 32767)
    ids, names, files, disk_files, ambiguous = set(), set(), set(), [], set()
    item_types, atlas_sizes = {}, {}
    for _ in range(total):
        record = _Reader(data, items.pos + 4, items.pos + 4 + items.count(">i", MAX_DESCRIPTOR_BYTES))
        if record.end > items.end:
            raise LedgerError("export descriptor item exceeds its block")
        kind = record.byte()
        item_id, item_name = record.string(table), record.string(table)
        record.string(table)  # source path, never a staging read/write path
        file = record.string(table)
        record.boolean()
        width, height = record.number(">i"), record.number(">i")
        if (not item_id or not re.fullmatch(r"[A-Za-z0-9_]{1,128}", item_id)
                or item_id in ids or min(width, height) < 0 or max(width, height) > 65536):
            raise LedgerError("export descriptor item identity or dimensions are invalid")
        ids.add(item_id)
        item_types[item_id] = kind
        if kind == 4:
            atlas_sizes[item_id] = (width, height)
        if kind not in (0, 1, 2, 3, 4, 5, 7):
            raise LedgerError("export descriptor contains an unmodeled item type")
        if kind == 0:
            scale = record.byte()
            if scale not in (0, 1, 2):
                raise LedgerError("export descriptor image scale is invalid")
            if scale == 1:
                record.take(20)
            record.boolean()
        elif kind in (1, 3, 5):
            if kind == 1:
                record.boolean()
            elif kind == 3:
                record.byte()
            record.take(record.count(">i", MAX_DESCRIPTOR_BYTES))
        branch = record.string(table)
        effective_name = (branch + "/" if branch else "") + (item_name or "")
        if item_name is not None:
            if effective_name in names:
                # Existing Common exports contain repeated names in different
                # source paths. UIPackage addresses IDs unambiguously but its
                # name map overwrites; report that ambiguity instead of claiming
                # name-based lookup is unique or rewriting inherited source.
                ambiguous.add(effective_name)
            names.add(effective_name)
        branch_count = record.byte()
        for _ in range(branch_count if branches else min(branch_count, 1)):
            if not record.string(table):
                raise LedgerError("export descriptor item branch is missing")
        for _ in range(record.byte()):
            if not record.string(table):
                raise LedgerError("export descriptor high-resolution identity is missing")
        # UIPackage skips to nextPos: bounded opaque/future item data is not decoded.
        items.pos = record.end
        if kind in _DISK_TYPES:
            relative = _relative(name + "_" + _relative(file))
            if relative.casefold() in files or relative.casefold() == (name + "_fui.bytes").casefold():
                raise LedgerError("export descriptor disk artifact is duplicate")
            files.add(relative.casefold())
            disk_files.append(DiskItem(item_id, _DISK_TYPES[kind], relative, width, height))
        elif file:
            raise LedgerError("export descriptor contains an unmodeled disk-bearing item")
    items.finished()
    sprites = block(2)
    sprite_ids = set()
    for _ in range(sprites.count(">h", 32767)):
        length = sprites.u16()
        record = _Reader(data, sprites.pos, sprites.pos + length)
        if record.end > sprites.end:
            raise LedgerError("export descriptor sprite exceeds its block")
        sprite_id, atlas_id = record.string(table), record.string(table)
        if not sprite_id or sprite_id in sprite_ids or item_types.get(atlas_id) != 4:
            raise LedgerError("export descriptor sprite identity or atlas reference is invalid")
        sprite_ids.add(sprite_id)
        x, y, w, h = (record.number(">i") for _ in range(4))
        aw, ah = atlas_sizes[atlas_id]
        if min(x, y, w, h) < 0 or x + w > aw or y + h > ah:
            raise LedgerError("export descriptor sprite exceeds atlas bounds")
        record.boolean()  # rotated
        if record.boolean():
            record.take(16)  # trim offset and original size, interpreted by Unity
        sprites.pos = record.end
    sprites.finished()
    hit_tests = block(3, required=False)
    if hit_tests:
        for _ in range(hit_tests.count(">h", 32767)):
            length = hit_tests.count(">i", MAX_DESCRIPTOR_BYTES)
            record = _Reader(data, hit_tests.pos, hit_tests.pos + length)
            if record.end > hit_tests.end or item_types.get(record.string(table)) != 0:
                raise LedgerError("export descriptor pixel hit-test record is invalid")
            hit_tests.pos = record.end
        hit_tests.finished()
    return Descriptor(name, identity, tuple(dependencies), tuple(disk_files), total, tuple(sorted(ambiguous)))


@dataclass(frozen=True)
class Artifact:
    relative: str
    package: str
    kind: str
    data: bytes

    def evidence(self):
        return {"file": self.relative, "package": self.package, "kind": self.kind,
                "bytes": len(self.data), "sha256": hashlib.sha256(self.data).hexdigest()}


@dataclass(frozen=True)
class ExportSnapshot:
    packages: tuple
    artifacts: tuple

    def evidence(self):
        return {"format": "farmbot-fgui-export-v1", "descriptor_version": 7, "compressed": False,
                "packages": [{"name": p.name, "id": p.identity, "items": p.item_count,
                              "ambiguous_names": list(p.ambiguous_names),
                              "dependencies": [{"name": n, "id": i} for n, i in p.dependencies]}
                             for p in self.packages],
                "artifacts": [a.evidence() for a in self.artifacts]}


def _inventory(root):
    """No symlink-following walk; record file identities and directory membership."""
    files, dirs, folded = {}, {}, set()

    def visit(folder, depth):
        _plain_path(folder)
        if depth > MAX_DEPTH:
            raise LedgerError("export staging depth exceeds bounds")
        entries = sorted(folder.iterdir(), key=lambda p: p.name)
        dirs[folder.relative_to(root).as_posix()] = tuple(p.name for p in entries)
        if len(dirs) + len(files) + len(entries) > MAX_FILES * 2:
            raise LedgerError("export staging inventory exceeds bounds")
        for path in entries:
            _plain_path(path)
            name = _relative(path.relative_to(root).as_posix())
            if name.casefold() in folded:
                raise LedgerError("export staging contains case-colliding paths")
            folded.add(name.casefold())
            value = path.stat()
            if stat.S_ISDIR(value.st_mode):
                if not any(path.iterdir()):
                    raise LedgerError("export staging contains an unexpected empty directory")
                visit(path, depth + 1)
            elif stat.S_ISREG(value.st_mode) and value.st_nlink == 1:
                if not 0 < value.st_size <= MAX_FILE_BYTES or len(files) >= MAX_FILES:
                    raise LedgerError("export staging file exceeds bounds")
                files[name] = (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
                               value.st_ctime_ns, value.st_nlink)
            else:
                raise LedgerError("export staging requires unlinked regular files")
    visit(root, 0)
    if not files or sum(value[2] for value in files.values()) > MAX_BYTES:
        raise LedgerError("export staging aggregate bytes exceed bounds or no files exist")
    return files, dirs


def snapshot_export(root, packages):
    """Return exact immutable output bytes, never paths for a later unguarded copy.

    `packages` maps every selected export package name to its reviewed manifest ID,
    including dependencies exported for verification only. Only the future caller's
    separately selected changed packages may be installed into Client.
    """
    root = Path(root)
    _plain_path(root)
    if not isinstance(packages, dict) or not 0 < len(packages) <= MAX_PACKAGES:
        raise LedgerError("export requires an explicit bounded package identity map")
    packages = dict(packages)
    names, ids = set(), set()
    for name, identity in packages.items():
        _package(name, identity)
        if name.casefold() in names or identity in ids:
            raise LedgerError("export selected package names or identities are duplicate")
        names.add(name.casefold())
        ids.add(identity)
    try:
        before = _inventory(root)
        data = {name: _read_regular(root / name, MAX_FILE_BYTES) for name in before[0]}
        if _inventory(root) != before:
            raise LedgerError("export staging changed during snapshot")
        if any(not b or b.startswith(b"version https://git-lfs.github.com/spec/v1") for b in data.values()):
            raise LedgerError("export staging contains an empty file or unhydrated LFS pointer")
        parsed, expected, dimensions = [], {}, {}
        for name, identity in sorted(packages.items()):
            desc_name = name + "_fui.bytes"
            if desc_name not in data:
                raise LedgerError("export staging is missing a selected descriptor")
            package = descriptor(data[desc_name], name, identity)
            parsed.append(package)
            if desc_name.casefold() in {p.casefold() for p in expected}:
                raise LedgerError("export package artifact inventories collide")
            expected[desc_name] = (name, "descriptor")
            for item in package.files:
                if item.relative.casefold() in {p.casefold() for p in expected}:
                    raise LedgerError("export package artifact inventories collide")
                expected[item.relative] = (name, item.kind)
                if item.kind == "atlas":
                    dimensions[item.relative] = (item.width, item.height)
        if set(data) != set(expected):
            raise LedgerError("export staging has missing or orphan artifacts")
        for package in parsed:
            for name, identity in package.dependencies:
                if packages.get(name) != identity:
                    raise LedgerError("export dependency is not present with its expected identity")
        graph = {p.name: [name for name, _ in p.dependencies] for p in parsed}
        visited, active = set(), set()

        def visit(name):
            if name in active:
                raise LedgerError("export dependency graph contains a cycle")
            if name not in visited:
                active.add(name)
                for dependency in graph[name]:
                    visit(dependency)
                active.remove(name)
                visited.add(name)

        for name in graph:
            visit(name)
        artifacts = []
        for name, (package, kind) in sorted(expected.items()):
            if kind == "atlas":
                image = read_image(root / name, root)
                if (image.content_type != "image/png" or image.data != data[name]
                        or (image.width, image.height) != dimensions[name]):
                    raise LedgerError("export atlas must be an unchanged fully decoded PNG")
            artifacts.append(Artifact(name, package, kind, data[name]))
        if _inventory(root) != before:
            raise LedgerError("export staging changed during validation")
        return ExportSnapshot(tuple(parsed), tuple(artifacts))
    except OSError:
        raise LedgerError("export staging cannot be read as bounded ordinary files") from None
