"""The service heartbeat: what `serve` is doing, for the status monitor to read (docs/operating-contract.md).

`serve` is the only writer. The file sits in the state root, which Codex workers are not given, but a Claude
worker has no OS sandbox, so the reader treats it as untrusted display data: bounded, regular files only, and
every field it uses validated. Unknown keys and loops are ignored so an older monitor can read a newer beat.
"""
import json
import re
import subprocess
import threading
import time

from .launcher import _read_worker_file, _write_worker_file

SCHEMA_VERSION = 1
INTERVAL = 5.0
STALE_AFTER = 60.0
MAX_BYTES = 64 * 1024
LOOPS = ("receive", "schedule", "pool", "lifecycle", "progress", "resource_recovery")
PHASES = ("starting", "serving", "stopped")
OUTCOMES = ("accepted", "duplicate", "ignored", "rejected", "malformed", "failed")
_ERROR_TYPE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,63}")
_EVENT_TYPE = re.compile(r"[A-Za-z]{1,40}")
_RUNTIME = re.compile(r"[a-z][a-z0-9_-]{0,31}")
_REVISION = re.compile(r"[0-9a-f]{7,40}")
_COMMIT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
_COUNT_LIMIT = 10 ** 12
MAX_WORKERS = 64
_ITEM_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def webhook_outcome(status, result):
    """One /webhook POST's category, from the receiver's HTTP status and result text."""
    if status == 200:
        return result if result in ("duplicate", "ignored") else "accepted"
    if status in (401, 403):
        return "rejected"
    if status in (400, 408, 413):
        return "malformed"
    return "failed"


def _empty_loop():
    return {"started_at": None, "finished_at": None, "consecutive_errors": 0, "error_type": None, "error_at": None}


class Heartbeat:
    """In-memory state of one serving process. Loop threads record into it; the heartbeat thread writes it."""

    def __init__(self, *, runtime, revision=(None, None), workers=None, clock=time.time):
        self.clock = clock
        self._lock = threading.Lock()
        self._runtime = runtime
        # A zero-argument callable such as Launcher.running: the worker processes serve is managing.
        self._workers = workers
        self._revision, self._dirty = revision
        self._phase = "starting"
        self._started_at = clock()
        self._stopped_at = None
        self._loops = {}
        self._webhooks = {"last_at": None, "last_type": None, "last_rejected_at": None,
                          "counts": dict.fromkeys(OUTCOMES, 0)}

    def set_phase(self, phase):
        if phase not in PHASES:
            raise ValueError(f"unknown heartbeat phase: {phase}")
        with self._lock:
            self._phase = phase
            if phase == "stopped":
                self._stopped_at = self.clock()

    def loop_started(self, name):
        with self._lock:
            self._loops.setdefault(name, _empty_loop())["started_at"] = self.clock()

    def loop_finished(self, name):
        with self._lock:
            record = self._loops.setdefault(name, _empty_loop())
            record["finished_at"] = self.clock()
            record["consecutive_errors"] = 0

    def loop_failed(self, name, exc):
        with self._lock:
            record = self._loops.setdefault(name, _empty_loop())
            record["finished_at"] = record["error_at"] = self.clock()
            record["consecutive_errors"] += 1
            record["error_type"] = type(exc).__name__

    def webhook(self, kind, status, result):
        outcome = webhook_outcome(status, result)
        with self._lock:
            now = self.clock()
            self._webhooks["counts"][outcome] += 1
            self._webhooks["last_at"] = now
            # The type comes from an unauthenticated body; keep only a plain word.
            self._webhooks["last_type"] = kind if isinstance(kind, str) and _EVENT_TYPE.fullmatch(kind) else None
            if outcome == "rejected":
                self._webhooks["last_rejected_at"] = now

    def _worker_snapshot(self):
        """The workers serve is managing, as start and budget-deadline times only, never PIDs; None when the
        source cannot say, which the monitor reads as unknown rather than as no workers."""
        if self._workers is None:
            return None
        try:
            return {str(item_id): {"started_at": float(handle.started_at), "deadline": float(handle.deadline)}
                    for item_id, handle in dict(self._workers()).items()}
        except Exception:  # a display snapshot must never stop the heartbeat thread
            return None

    def payload(self):
        workers = self._worker_snapshot()
        with self._lock:
            return {"schema_version": SCHEMA_VERSION, "phase": self._phase, "started_at": self._started_at,
                    "written_at": self.clock(), "stopped_at": self._stopped_at, "revision": self._revision,
                    "dirty": self._dirty, "runtime": self._runtime, "workers": workers,
                    "loops": {name: dict(record) for name, record in self._loops.items()},
                    "webhooks": {**self._webhooks, "counts": dict(self._webhooks["counts"])}}

    def write(self, path):
        """Replace the file at `path`, never opening or writing through what is there: a worker may reach it."""
        _write_worker_file(path, json.dumps(self.payload(), ensure_ascii=False, allow_nan=False))


def _time(value, *, optional=False):
    if value is None and optional:
        return None
    # The comparison also refuses NaN and the infinities. math.isfinite would raise OverflowError, which is not a
    # ValueError, for a JSON integer too large for a float.
    if type(value) not in (int, float) or not 0 <= value < 1e11:
        raise ValueError("heartbeat time")
    return float(value)


def _count(value):
    if type(value) is not int or not 0 <= value < _COUNT_LIMIT:
        raise ValueError("heartbeat count")
    return value


def _matching(pattern, value, *, optional=True):
    if value is None and optional:
        return None
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError("heartbeat text")
    return value


def _loop(raw):
    if not isinstance(raw, dict):
        raise ValueError("heartbeat loop")
    return {"started_at": _time(raw.get("started_at"), optional=True),
            "finished_at": _time(raw.get("finished_at"), optional=True),
            "consecutive_errors": _count(raw.get("consecutive_errors")),
            "error_type": _matching(_ERROR_TYPE, raw.get("error_type")),
            "error_at": _time(raw.get("error_at"), optional=True)}


def _webhooks(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("counts"), dict):
        raise ValueError("heartbeat webhooks")
    return {"last_at": _time(raw.get("last_at"), optional=True),
            "last_type": _matching(_EVENT_TYPE, raw.get("last_type")),
            "last_rejected_at": _time(raw.get("last_rejected_at"), optional=True),
            "counts": {outcome: _count(raw["counts"].get(outcome, 0)) for outcome in OUTCOMES}}


def _worker_records(raw):
    if raw is None:
        return None
    if not isinstance(raw, dict) or len(raw) > MAX_WORKERS:
        raise ValueError("heartbeat workers")
    workers = {}
    for item_id, record in raw.items():
        if not isinstance(item_id, str) or not _ITEM_ID.fullmatch(item_id) or not isinstance(record, dict):
            raise ValueError("heartbeat worker")
        workers[item_id] = {"started_at": _time(record.get("started_at")), "deadline": _time(record.get("deadline"))}
    return workers


def validate(raw):
    """The fields the monitor uses, checked one by one; ValueError for anything else."""
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("heartbeat schema")
    dirty = raw.get("dirty")  # null, like the revision, when Git could not say
    if raw.get("phase") not in PHASES or not (dirty is None or type(dirty) is bool):
        raise ValueError("heartbeat phase")
    loops = raw.get("loops")
    if not isinstance(loops, dict):
        raise ValueError("heartbeat loops")
    return {"phase": raw["phase"], "started_at": _time(raw.get("started_at")),
            "written_at": _time(raw.get("written_at")), "stopped_at": _time(raw.get("stopped_at"), optional=True),
            "revision": _matching(_REVISION, raw.get("revision")), "dirty": dirty,
            "runtime": _matching(_RUNTIME, raw.get("runtime"), optional=False),
            "loops": {name: _loop(loops[name]) for name in LOOPS if name in loops},
            "webhooks": _webhooks(raw.get("webhooks")), "workers": _worker_records(raw.get("workers"))}


def read(path, *, now):
    """(state, beat): state is fresh, stale, missing or unreadable; beat is validate()'s result or None."""
    try:
        data = _read_worker_file(path)
    except FileNotFoundError:
        return "missing", None
    except OSError:
        return "unreadable", None
    if len(data) > MAX_BYTES:
        return "unreadable", None
    try:
        beat = validate(json.loads(data.decode("utf-8")))
    except (ValueError, TypeError, RecursionError):  # JSON and UTF-8 decoding errors are ValueErrors
        return "unreadable", None
    return ("fresh" if abs(now - beat["written_at"]) < STALE_AFTER else "stale"), beat


def source_revision(root):
    """(first 12 characters of HEAD, whether tracked files differ) for the checkout at `root`, or (None, None)
    when Git is missing, times out or `root` is not a checkout."""
    def git(*args):
        # Without --no-optional-locks, status refreshes and rewrites .git/index under index.lock: a write the
        # monitor must not make, and a lock an operator's git command in the same checkout could collide with.
        return subprocess.run(["git", "--no-optional-locks", "-C", str(root), *args], capture_output=True,
                              encoding="utf-8", errors="replace", timeout=5, check=True).stdout

    try:
        head = git("rev-parse", "HEAD").strip()
        dirty = bool(git("status", "--porcelain", "--untracked-files=no").strip())
    except (OSError, subprocess.SubprocessError):
        return None, None
    return (head[:12], dirty) if _COMMIT.fullmatch(head) else (None, None)
