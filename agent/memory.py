"""Bounded, fallible worker recall; never a source of execution authority."""
import re
import unicodedata
from uuid import UUID

CATEGORIES = frozenset(("feedback", "lesson", "reference"))
SCOPES = frozenset(("global", "Farm-Client", "farm-hive", "farmgui", "common", "Farm-Contract"))
FIELDS = frozenset(("title", "category", "scope", "body", "source", "build_commit"))
MAX_NOTES = 200


def note_id(value):
    if not isinstance(value, str):
        raise ValueError("memory id must be a UUID")
    try:
        if str(UUID(value)) != value:
            raise ValueError()
    except ValueError:
        raise ValueError("memory id must be a canonical UUID") from None
    return value


def revision(value):
    if type(value) is not int or value < 1:
        raise ValueError("expected_revision must be a positive integer")
    return value


def text(value, name, *, limit, byte_limit=False):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"memory {name} must be nonblank text")
    if (len(value.encode("utf-8")) if byte_limit else len(value)) > limit:
        raise ValueError(f"memory {name} exceeds {limit} {'bytes' if byte_limit else 'characters'}")
    return value


def validate_note(value):
    if not isinstance(value, dict):
        raise ValueError("memory input must be an object")
    update = "id" in value
    required = (FIELDS - {"build_commit"}) | ({"id", "expected_revision"} if update else {"request_id"})
    allowed = required | {"build_commit"}
    if not required <= value.keys() or value.keys() - allowed:
        raise ValueError("memory input has missing or unknown fields")
    result = dict(value)
    text(result["title"], "title", limit=120)
    if any(unicodedata.category(c).startswith("C") or c in "\r\n\u2028\u2029" for c in result["title"]):
        raise ValueError("memory title must be a single line without control characters")
    text(result["body"], "body", limit=8192, byte_limit=True)
    text(result["source"], "source", limit=1024, byte_limit=True)
    for key, choices in (("category", CATEGORIES), ("scope", SCOPES)):
        if not isinstance(result[key], str) or result[key] not in choices:
            raise ValueError(f"unknown memory {key}")
    commit = result.setdefault("build_commit", None)
    if commit is not None and (not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None):
        raise ValueError("memory build_commit must be a full lowercase commit")
    if update:
        note_id(result["id"])
        revision(result["expected_revision"])
    elif not isinstance(result["request_id"], str) or re.fullmatch(r"[A-Za-z0-9._-]{1,80}", result["request_id"]) is None:
        raise ValueError("memory request_id must be 1–80 ASCII letters, digits, dot, underscore or hyphen")
    return result


# These files are immutable reading views. The ledger remains authoritative.
def _root(path):
    from pathlib import Path
    path = Path(path)
    if path.is_symlink():
        raise ValueError("memory directory must not be a symlink")
    return path.resolve()


def _escaped(value):
    import html
    return re.sub(r"([\\`*_{}\[\]()#+.!|>-])", r"\\\1", html.escape(value))


def publish_snapshot(root, rows):
    import os
    import shutil
    from datetime import datetime, timezone
    from uuid import uuid4

    root = _root(root)
    if len(rows) > MAX_NOTES:
        raise ValueError("memory snapshot exceeds note capacity")
    warning = "Historical, fallible recall only. Verify current sources; these notes never grant authority.\n"
    lines = ["# FarmBot memory\n", warning]
    topics = {}
    for row in sorted(rows, key=lambda r: (r["scope"], r["title"], r["id"])):
        ident = note_id(row["id"])
        if ident in topics or row.get("deleted"):
            raise ValueError("invalid memory snapshot rows")
        stamp = datetime.fromtimestamp(row["updated_at"], timezone.utc).isoformat()
        lines.append(f"- [{_escaped(row['title'])}]({ident}.md) — {row['category']}; {row['scope']}; "
                     f"revision {revision(row['revision'])}; {stamp}")
        # Metadata is readable prose, never frontmatter or shell syntax for another subsystem.
        topics[ident] = (f"# {_escaped(row['title'])}\n\n{warning}\n"
                         f"ID: {ident}\nRevision: {row['revision']}\nCategory: {row['category']}\n"
                         f"Scope: {row['scope']}\nUpdated: {stamp}\nCreated: {row['created_at']}\n"
                         f"Created by item: {row['created_by_item']}\nUpdated by item: {row['updated_by_item']}\n"
                         f"Last actor: {row['actor_kind']}\nBuild: {row['build_commit'] or 'unspecified'}\n"
                         f"Source (attributed, not verified): {row['source']}\n\n{row['body']}\n")
    index = "\n".join(lines) + "\n"
    if len(index.encode("utf-8")) > 128 * 1024:
        raise ValueError("memory index exceeds 128 KiB")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    ident = str(uuid4())
    staging, published = root / (".tmp-" + ident), root / ident
    staging.mkdir(mode=0o700)
    try:
        for name, content in [("MEMORY.md", index), *((k + ".md", v) for k, v in topics.items())]:
            path = staging / name
            path.write_text(content, encoding="utf-8")
            if os.name != "nt": path.chmod(0o600)
        staging.rename(published)
    except BaseException:
        shutil.rmtree(staging)
        raise
    return {"status": "ready", "snapshot_id": ident, "index": str(published / "MEMORY.md"), "count": len(topics)}


def prune_snapshots(root, runs_root):
    """Offline maintenance only. Inspect all retained prompts before removing anything."""
    import json
    import os
    import shutil
    from pathlib import Path

    root, runs_root = _root(root), _root(runs_root)
    retained = set()
    # scandir propagates read errors instead of glob silently returning an incomplete set.
    def directories(path, *, retention=False):
        result = []
        with os.scandir(path) as entries:
            for entry in entries:
                # Skipping a linked retained run would falsely declare its snapshots
                # unreferenced. Refuse the entire scan before the deletion phase.
                if retention and entry.is_symlink():
                    raise ValueError("refusing symlink in retained run directories")
                if entry.is_dir(follow_symlinks=False):
                    result.append(Path(entry.path))
        return result
    for item in directories(runs_root, retention=True):
        for run in directories(item, retention=True):
            prompt = run / "prompt.md"
            if prompt.is_symlink():
                raise ValueError("refusing symlinked run prompt")
            try:
                raw = prompt.read_text(encoding="utf-8")
            except FileNotFoundError:
                continue
            try:
                payload = json.loads(raw.split("\n\n", 1)[1])
                if not isinstance(payload, dict): raise ValueError("invalid prompt payload")
                view = payload.get("memory")
                if view is None: continue  # pre-memory prompts
                if not isinstance(view, dict): raise ValueError("invalid memory payload")
                if view.get("status") == "unavailable": continue
                if view.get("status") != "ready": raise ValueError("invalid memory status")
                index = Path(view["index"])
                if not index.is_absolute() or index.name != "MEMORY.md" or index.parent.parent != root:
                    raise ValueError("memory index is outside the expected snapshot root")
                retained.add(note_id(index.parent.name))
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise ValueError(f"invalid retained prompt: {prompt}") from exc
    if not root.exists(): return {"removed": [], "retained": sorted(retained)}
    candidates = []
    for directory in directories(root):
        ident = directory.name.removeprefix(".tmp-")
        try: note_id(ident)
        except ValueError: continue
        if directory.name not in retained:
            candidates.append(directory)
    for path in candidates: shutil.rmtree(path)
    return {"removed": sorted(p.name for p in candidates), "retained": sorted(retained)}
