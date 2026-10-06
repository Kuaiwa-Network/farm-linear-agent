"""Owned committed UI source identity without executing Git clean filters.

Controller clone/worktree checks precede every Git read. Text normalization is
Git's; LFS identity is checked against raw pointer objects and materialized bytes.
No network hydration, credential callback, source mutation or worker grant here.
"""
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess

from .fgui_approval import source_digest
from .fgui_export import _package, _RESERVED
from .fgui_publisher import _environment, _xml
from .ledger import LedgerError
from .preview_upload import _plain_path
from .uploads import _read_regular
from .worktrees import HOOKS_OFF, READ_ONLY_GIT

MAX_SOURCE_FILES = 20000
MAX_SOURCE_BYTES = 512 << 20
MAX_SOURCE_FILE_BYTES = 64 << 20
_POINTER = re.compile(rb"version https://git-lfs.github.com/spec/v1\r?\noid sha256:([0-9a-f]{64})\r?\nsize ([0-9]+)\r?\n?\Z")


def _name(name):
    source_digest({name: "a" * 64})
    if (any(c in name for c in '<>"|?*')
            or any(part.endswith((".", " ")) or part.split(".")[0].casefold() in _RESERVED
                   or part.casefold() == ".git" for part in name.split("/"))):
        raise LedgerError("UI source name is not portable to native Windows")
    return name


def _git(trees, path, args, data=None, *, limit=4 << 20):
    # Explicit repo identity prevents the worker-controlled .git pointer or
    # commondir from selecting another clone, config or process hook.
    entry = trees.worktree_entry("farmgui", path)
    clone = trees._clone("farmgui")
    env = {**_environment(), "GIT_DIR": str(entry), "GIT_COMMON_DIR": str(clone), "GIT_WORK_TREE": str(path),
           "GIT_NO_LAZY_FETCH": "1", "GIT_NO_REPLACE_OBJECTS": "1", "GIT_CONFIG_COUNT": "0",
           "GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1"}
    native = ("-c", "core.longpaths=true") if os.name == "nt" else ()
    try:
        result = subprocess.run(["git", *HOOKS_OFF, *native, *READ_ONLY_GIT, *args], cwd=path, env=env,
                                input=data, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        raise LedgerError("UI source Git read is unavailable or timed out") from None
    if result.returncode or len(result.stdout) > limit:
        raise LedgerError("UI source Git read failed or exceeded its bounded inventory")
    return result.stdout


def _tree(data):
    result = {}
    folded = set()
    for entry in filter(None, data.split(b"\0")):
        try:
            metadata, raw = entry.split(b"\t", 1)
            mode, kind, oid = metadata.decode("ascii").split(" ")
            name = _name(raw.decode("utf-8"))
        except (ValueError, UnicodeError):
            raise LedgerError("UI source tree inventory is malformed") from None
        if mode not in ("100644", "100755") or kind != "blob" or not re.fullmatch(r"[0-9a-f]{40}", oid):
            raise LedgerError("UI source requires regular tracked blobs, never links or submodules")
        if name.casefold() in folded or len(result) >= MAX_SOURCE_FILES:
            raise LedgerError("UI source names collide or exceed the file budget")
        folded.add(name.casefold()); result[name] = (mode, oid)
    if not result:
        raise LedgerError("UI source tracked inventory is empty")
    return result


def _index(data):
    result = {}
    for entry in filter(None, data.split(b"\0")):
        try:
            metadata, raw = entry.split(b"\t", 1)
            mode, oid, stage = metadata.decode("ascii").split(" ")
            name = _name(raw.decode("utf-8"))
        except (ValueError, UnicodeError):
            raise LedgerError("UI source index inventory is malformed") from None
        if stage != "0" or name in result:
            raise LedgerError("UI source index is conflicted or duplicate")
        result[name] = (mode, oid)
    return result


def _pointers(trees, path, entries, names):
    """One bounded raw-object batch for selected LFS blobs, not a filter process."""
    if not names:
        return {}
    requests = ("\n".join(entries[n][1] for n in names) + "\n").encode("ascii")
    sizes = _git(trees, path, ["cat-file", "--batch-check"], requests).decode("ascii").splitlines()
    if len(sizes) != len(names):
        raise LedgerError("UI source LFS object inventory is incomplete")
    expected = []
    for name, row in zip(names, sizes):
        values = row.split(" ")
        if len(values) != 3 or values[:2] != [entries[name][1], "blob"] or not values[2].isdigit() or not 0 < int(values[2]) <= 1024:
            raise LedgerError("UI source LFS object is missing or is not a bounded pointer")
        expected.append(int(values[2]))
    data = _git(trees, path, ["cat-file", "--batch"], requests, limit=sum(expected) + len(names) * 100)
    result = {}; position = 0
    for name, size in zip(names, expected):
        end = data.find(b"\n", position)
        if end < 0 or data[position:end] != f"{entries[name][1]} blob {size}".encode("ascii"):
            raise LedgerError("UI source LFS batch identity changed")
        raw = data[end + 1:end + 1 + size]; position = end + size + 2
        if data[position - 1:position] != b"\n":
            raise LedgerError("UI source LFS batch is truncated")
        match = _POINTER.fullmatch(raw)
        if not match or int(match[2]) > MAX_SOURCE_FILE_BYTES:
            raise LedgerError("UI source LFS pointer is malformed or exceeds the file budget")
        result[name] = (raw, match[1].decode("ascii"), int(match[2]))
    if position != len(data):
        raise LedgerError("UI source LFS batch has unexpected data")
    return result


def _stamp(path):
    _plain_path(path)
    value = path.stat()
    if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1 or value.st_size > MAX_SOURCE_FILE_BYTES:
        raise LedgerError("UI source requires bounded unlinked regular files")
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns, value.st_nlink


def owned_source(trees, item_id, branch, packages, *, expected_head):
    """Verify a clean owned commit and full hash map, with hydrated scoped LFS.

    Unselected LFS inputs may remain pointers. Every selected package/dependency
    requires hydrated bytes; every materialized LFS file must match its raw pointer.
    Staged, untracked or concurrent changes refuse success. Ignored publisher
    `.objs` cache is not source, while watched output is refused by the publisher.
    """
    if not isinstance(expected_head, str) or not re.fullmatch(r"[0-9a-f]{40}", expected_head):
        raise LedgerError("UI source requires the reviewed full commit")
    if not isinstance(packages, dict) or not 0 < len(packages) <= 100:
        raise LedgerError("UI source requires explicit scoped package identities")
    for name, identity in packages.items():
        _package(name, identity)
    path = trees.worktrees_root.resolve() / item_id / "farmgui"
    _plain_path(path)
    if path.resolve() != path or not path.is_dir() or path.parent.parent != trees.worktrees_root.resolve():
        raise LedgerError("UI source must be this item's owned farmgui checkout")
    def identity():
        head = _git(trees, path, ["rev-parse", "HEAD"]).decode("ascii").strip()
        actual = _git(trees, path, ["symbolic-ref", "--quiet", "--short", "HEAD"]).decode("utf-8").strip()
        if head != expected_head or actual != branch:
            raise LedgerError("UI source must retain its reviewed commit and issue branch")
        return _tree(_git(trees, path, ["ls-tree", "-r", "-z", "--full-tree", "HEAD"]))
    entries = identity()
    if _index(_git(trees, path, ["ls-files", "--stage", "-z"])) != entries:
        raise LedgerError("UI source index differs from its reviewed committed tree")
    if _git(trees, path, ["ls-files", "--others", "--exclude-standard", "-z"]):
        raise LedgerError("UI source has untracked changes")
    names = sorted(entries)
    raw = ("\0".join(names) + "\0").encode("utf-8")
    attrs = _git(trees, path, ["check-attr", "filter", "--stdin", "-z"], raw).split(b"\0")
    if attrs[-1] != b"" or len(attrs) != len(names) * 3 + 1:
        raise LedgerError("UI source filter inventory is incomplete")
    lfs = []
    for name, i in zip(names, range(0, len(attrs) - 1, 3)):
        if attrs[i:i + 2] != [name.encode("utf-8"), b"filter"] or attrs[i + 2] not in (b"lfs", b"unspecified", b"unset"):
            raise LedgerError("UI source contains an unreviewed Git clean filter")
        if attrs[i + 2] == b"lfs":
            lfs.append(name)
    pointers = _pointers(trees, path, entries, lfs)
    stamps, hashes, total = {}, {}, 0
    for name in names:
        file = path / name; stamps[name] = _stamp(file); total += stamps[name][2]
        if os.name != "nt" and bool(file.stat().st_mode & stat.S_IXUSR) != (entries[name][0] == "100755"):
            raise LedgerError("UI source tracked executable mode differs from its committed tree")
        if total > MAX_SOURCE_BYTES:
            raise LedgerError("UI source aggregate bytes exceed the budget")
        data = _read_regular(file, MAX_SOURCE_FILE_BYTES)
        if _stamp(file) != stamps[name] or len(data) != stamps[name][2]:
            raise LedgerError("UI source changed during snapshot")
        hashes[name] = hashlib.sha256(data).hexdigest()
        if name in pointers:
            raw_pointer, digest, size = pointers[name]
            if data == raw_pointer:
                if any(name.startswith("assets/" + package + "/") for package in packages):
                    raise LedgerError("UI source selected package inputs must be hydrated")
            elif hashes[name] != digest or len(data) != size:
                raise LedgerError("UI source materialized LFS input differs from its committed pointer")
    # Disabled LFS process/clean filters leave Git's safe native CRLF/text
    # normalization intact. Unknown filters were refused above, never executed.
    objects = _git(trees, path, ["hash-object", "--stdin-paths"], ("\n".join(names) + "\n").encode("utf-8")).decode("ascii").splitlines()
    if len(objects) != len(names) or any(oid != entries[name][1] for name, oid in zip(names, objects) if name not in pointers):
        raise LedgerError("UI source has uncommitted tracked changes")
    if identity() != entries or _index(_git(trees, path, ["ls-files", "--stage", "-z"])) != entries:
        raise LedgerError("UI source Git identity changed during snapshot")
    if any(_stamp(path / name) != value for name, value in stamps.items()):
        raise LedgerError("UI source changed during Git normalization")
    if _git(trees, path, ["ls-files", "--others", "--exclude-standard", "-z"]):
        raise LedgerError("UI source untracked inventory changed during snapshot")
    return {"head": expected_head, "files": hashes}


def changed_packages(trees, item_id, branch, *, head, base):
    """Package identities from the owned commit's actual diff against trusted main.

    The caller obtains `base` from current repository metadata and verifies this
    source draft's exact HEAD. Missing base history needs a normal worker fetch;
    this helper never hydrates, fetches, modifies source or guesses a PR base.
    Removed packages and source changes without a package need a scope ruling.
    Non-package source paths remain explicit evidence for the caller's review.
    """
    if any(not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{40}", v) for v in (head, base)):
        raise LedgerError("UI source diff requires full reviewed HEAD and trusted main commits")
    path = trees.worktrees_root.resolve() / item_id / "farmgui"
    _plain_path(path)
    if (_git(trees, path, ["rev-parse", "HEAD"]).decode("ascii").strip() != head
            or _git(trees, path, ["symbolic-ref", "--quiet", "--short", "HEAD"]).decode("utf-8").strip() != branch):
        raise LedgerError("UI source diff must retain its reviewed commit and issue branch")
    merge_base = _git(trees, path, ["merge-base", base, head]).decode("ascii").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", merge_base):
        raise LedgerError("UI source diff has no unique trusted merge base")
    raw = _git(trees, path, ["diff", "--name-only", "-z", "--no-renames", "--no-ext-diff", "--no-textconv", merge_base, head])
    try:
        names = [_name(n.decode("utf-8")) for n in raw.split(b"\0") if n]
    except UnicodeError:
        raise LedgerError("UI source diff paths are malformed") from None
    if len(names) > MAX_SOURCE_FILES or len(names) != len(set(names)):
        raise LedgerError("UI source diff inventory is duplicate or exceeds bounds")
    packages, other = {}, []
    for name in names:
        parts = name.split("/")
        if len(parts) < 3 or parts[0] != "assets":
            other.append(name); continue
        package = parts[1]
        if package in packages:
            continue
        manifest = _git(trees, path, ["cat-file", "blob", head + ":assets/" + package + "/package.xml"], limit=2 << 20)
        identity = _xml(manifest).get("id")
        _package(package, identity); packages[package] = identity
        if len(packages) > 100 or len(packages) != len(set(packages.values())):
            raise LedgerError("UI source changed package inventory is duplicate or exceeds bounds")
    if not packages:
        raise LedgerError("UI source diff has no changed package; an explicit scope ruling is required")
    return {"head": head, "main": base, "merge_base": merge_base,
            "packages": dict(sorted(packages.items())), "files": names, "other_source_changes": other}
