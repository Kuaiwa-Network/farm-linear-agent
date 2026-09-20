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
