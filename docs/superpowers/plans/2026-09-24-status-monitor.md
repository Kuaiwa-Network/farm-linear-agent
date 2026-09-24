# Office Status Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only Chinese status page, served on the office LAN by a separate `monitor` process, showing what FarmBot is doing and whether it is running.

**Architecture:** `serve` rewrites a small heartbeat file (phase, loop timings, revision, webhook counts) every 5 s. A new `python3 -m agent.service monitor` process on the same host reads the ledger strictly read-only, reads the heartbeat defensively, probes the receiver's `/health` on loopback, and serves a static page plus `/api/status` JSON from an allowlist. It never writes, signals, locks or calls anything else, so it can report a stopped or wedged service without touching production.

**Tech Stack:** Python 3.13 standard library only (`http.server`, `sqlite3`, `urllib`, `subprocess`), `unittest`, vanilla JavaScript/CSS with no external requests, launchd on macOS, Task Scheduler on Windows.

**Spec:** `docs/superpowers/specs/2026-09-24-status-monitor-design.md`. Read it before starting; this plan argues from it.

## Global Constraints

- Standard library only. Follow the existing `unittest` patterns; tests use temporary state, local fixtures and loopback listeners only.
- Run commands from the repository root. macOS: `python3`; Windows: the configured Python. CI runs macOS and Windows with Python 3.13.
- The monitor never constructs `Ledger`, never touches `.controller.lock`, writes no file or database, signals and inspects no process, and makes no request other than `GET http://127.0.0.1:<receiver port>/health`, with proxies disabled.
- Heartbeat: `<local_root>/service-heartbeat.json` (`Paths.heartbeat`), `schema_version` 1, written every 5 s by replacement (`_write_worker_file`), read through `_read_worker_file`, capped at 64 KiB, stale when 60 s or more old.
- `monitor` config block: `bind` (IPv4 literal, default `127.0.0.1`), `port` (1–65535, default 8780, different from the receiver `port`), `hostnames` (at most 16 lowercase DNS names, default none). Unknown keys inside it are rejected.
- JSON is an allowlist of the spec's fields. Never emit issue descriptions, comments or labels, checkpoints, evidence prose, pending questions, inbox or worker messages, logs, host paths, PIDs, tokens or token hashes, config values beyond the instance fields, or raw stored errors.
- Limits: snapshot cache 2 s; page poll 5 s; `/health` timeout 2 s; request socket timeout 5 s; recent history 7 days and 30 entries; titles 200 characters; stages 120 characters.
- Attention thresholds: cleanup pending 600 s; issue status failures 3; cancel pending 300 s; webhook rejection window 900 s; loop errors 3; loop limits receive 120 s, lifecycle 600 s, progress 600 s, schedule 1800 s, resource_recovery 1800 s, pool 5400 s.
- Page: `lang="zh-CN"`. Every value is rendered with `textContent` or text nodes, never `innerHTML`. Links only for `https://linear.app/` and `https://github.com/`. No inline script or style (CSP `script-src 'self'; style-src 'self'`).
- Cross-platform: `pathlib`, explicit UTF-8, subprocess argument lists. Test paths with spaces and non-ASCII characters. POSIX-only tests (FIFO, symlink) must be skipped on Windows with a reason.
- The repository is public. Put no hostnames, LAN addresses, credential locations or private paths in code, docs, commits or PR text. `farmbot-host.local`, `192.0.2.x` and `C:\FarmBot\...` are the only example names used.
- Commits: imperative sentence subject, a short body when useful, and the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never push, open a PR, run TestBot live, or touch a production host without the user's go-ahead at that time.

## File map

| File | Status | Responsibility |
|---|---|---|
| `agent/readonly_db.py` | new | `snapshot_connection(path)`: read-only, `query_only`, one read transaction |
| `agent/doctor.py` | modify | Use `snapshot_connection`; behaviour unchanged |
| `agent/config.py` | modify | `monitor` block with validation, `MONITOR_DEFAULTS`, `monitor_settings()`, `Paths.heartbeat` |
| `agent/heartbeat.py` | new | `Heartbeat` recorder, `write`/`read`/`validate`, `webhook_outcome`, `source_revision` |
| `agent/service.py` | modify | `_serve` writes the heartbeat and times loop work; the `monitor` subcommand |
| `agent/receiver.py` | modify | Count every `/webhook` outcome; module-level `ExclusiveServer` shared with the monitor |
| `agent/monitor_view.py` | new | `build_status(...)`: the `/api/status` document, verdict and attention rules |
| `agent/monitor_static/index.html`, `monitor.css`, `monitor.js` | new | The page |
| `agent/monitor.py` | new | `probe_health`, `allowed_host`, `make_monitor_server`, `run` |
| `agent/deploy.py` | modify | Third launchd agent when a `monitor` block exists |
| `tests/test_readonly_db.py`, `tests/test_heartbeat.py`, `tests/test_monitor_view.py`, `tests/test_monitor.py` | new | Tests for the new units |
| `tests/test_service.py`, `tests/test_receiver.py`, `tests/test_deploy.py` | modify | Heartbeat, webhook counting and launchd regressions |
| `README.md`, `docs/operating-contract.md`, `AGENTS.md`, `docs/development-workflow.md`, `config/development.example.json` | modify | Documentation |

---

### Task 1: Shared read-only ledger connection

**Files:**
- Create: `agent/readonly_db.py`
- Modify: `agent/doctor.py` (imports and the connection lines at the top of `_snapshot`)
- Test: `tests/test_readonly_db.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `agent.readonly_db.snapshot_connection(path, *, timeout=2.0)`, a context manager yielding a `sqlite3.Connection` with `row_factory = sqlite3.Row`, `PRAGMA query_only=ON`, and an open read transaction. It raises `sqlite3.OperationalError` for a missing file and never creates it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_readonly_db.py`:

```python
"""snapshot_connection reads a ledger without creating, migrating or writing it."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent.ledger import Ledger
from agent.readonly_db import snapshot_connection


class SnapshotConnectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # Spaces and non-ASCII: the URI form must percent-encode them on every OS.
        self.path = Path(self.tmp.name) / "state 状态" / "ledger.sqlite3"

    def test_a_missing_ledger_is_not_created(self):
        with self.assertRaises(sqlite3.OperationalError):
            with snapshot_connection(self.path):
                pass
        self.assertFalse(self.path.exists())

    def test_rows_read_by_name_and_writes_are_refused(self):
        Ledger(self.path).close()
        before = self.path.read_bytes()
        with snapshot_connection(self.path) as db:
            tables = {row["name"] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("work_items", tables)
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("DELETE FROM work_items")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.path.with_name(self.path.name + "-journal").exists())

    def test_the_connection_is_closed_after_the_block(self):
        Ledger(self.path).close()
        with snapshot_connection(self.path) as db:
            pass
        with self.assertRaises(sqlite3.ProgrammingError):
            db.execute("SELECT 1")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest discover -s tests -p 'test_readonly_db.py' -v`
Expected: ERROR, `ModuleNotFoundError: No module named 'agent.readonly_db'`.

- [ ] **Step 3: Write the implementation**

Create `agent/readonly_db.py`:

```python
"""Read-only ledger snapshots, shared by `doctor` and the status monitor.

Never construct Ledger in a reader: its constructor creates the database and runs migrations.
"""
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3


@contextmanager
def snapshot_connection(path, *, timeout=2.0):
    """Yield a connection that can only read, holding one read transaction for the whole block.

    `mode=ro` refuses to create a missing file and `query_only` refuses writes even from a bug; the
    transaction keeps every query on the same snapshot. Reading a WAL ledger whose writer has closed can
    leave empty `-wal`/`-shm` files beside it; the ledger file itself is never modified.
    """
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=timeout)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        yield db
```

In `agent/doctor.py`, remove `from contextlib import closing`, and add `from .readonly_db import snapshot_connection` below `from .config import Paths, load_config`. Then replace:

```python
def _snapshot(path):
    # mode=ro prevents creation; a transaction keeps all tables on the same snapshot.
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        tables = {row["name"] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
```

with:

```python
def _snapshot(path):
    # One read transaction keeps all tables on the same snapshot; nothing here can create or migrate it.
    with snapshot_connection(path) as db:
        tables = {row["name"] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
```

Keep the rest of `_snapshot`'s body as it is. `import sqlite3` stays, because `diagnose` catches `sqlite3.Error`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s tests -p 'test_readonly_db.py' -v`
Expected: 3 tests, OK.
Run: `python3 -m unittest discover -s tests -p 'test_doctor.py' -v`
Expected: every existing doctor test passes, unchanged.

- [ ] **Step 5: Commit**

```bash
git add agent/readonly_db.py agent/doctor.py tests/test_readonly_db.py
git commit -F - <<'EOF'
Share doctor's read-only ledger connection

The status monitor needs the same guarantee doctor has: mode=ro, query_only
and one read transaction, never a Ledger that would migrate the database.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 2: `monitor` config block and heartbeat path

**Files:**
- Modify: `agent/config.py` (imports, constants, `Config` field and validation, `monitor_settings`, `Paths`)
- Test: create `tests/test_monitor.py` (later tasks add classes to it)

**Interfaces:**
- Consumes: nothing new.
- Produces: `Config.monitor: dict` (validated at construction, empty by default); `agent.config.MONITOR_DEFAULTS`; `agent.config.monitor_settings(config) -> {"bind": str, "port": int, "hostnames": tuple[str, ...]}`; `Paths(config).heartbeat == Path(config.local_root) / "service-heartbeat.json"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_monitor.py`:

```python
"""The office status monitor: its configuration, its HTTP surface and its read-only guarantees."""
import json
import tempfile
import unittest
from pathlib import Path

from agent.config import Config, Paths, load_config, monitor_settings


def config(**changes):
    return Config(**({"client_id": "c", "client_secret": "s", "webhook_secret": "w", "port": 8765} | changes))


class MonitorConfigTests(unittest.TestCase):
    def test_defaults_keep_the_monitor_on_loopback(self):
        self.assertEqual(monitor_settings(config()), {"bind": "127.0.0.1", "port": 8780, "hostnames": ()})

    def test_a_lan_block_loads_from_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config 配置.json"
            path.write_text(json.dumps({"client_id": "c", "client_secret": "s", "webhook_secret": "w",
                                        "monitor": {"bind": "0.0.0.0", "port": 8781,
                                                    "hostnames": ["farmbot-host.local"]}}), encoding="utf-8")
            loaded = load_config(path, secure_permissions=False)
        self.assertEqual(monitor_settings(loaded),
                         {"bind": "0.0.0.0", "port": 8781, "hostnames": ("farmbot-host.local",)})

    def test_invalid_blocks_are_refused(self):
        for block in ([], {"bind": "localhost"}, {"bind": "::"}, {"bind": "127.000.0.1"}, {"bind": 127},
                      {"port": 8765}, {"port": 0}, {"port": 70000}, {"port": "8780"}, {"port": True},
                      {"hostnames": "farmbot.local"}, {"hostnames": ["Farmbot.local"]}, {"hostnames": ["a b"]},
                      {"hostnames": ["x"] * 17}, {"host": "0.0.0.0"}):
            with self.subTest(block=block), self.assertRaises(ValueError):
                config(monitor=block)

    def test_the_heartbeat_lives_in_the_state_root_beside_the_controller_lock(self):
        root = Path(tempfile.gettempdir()) / "farm root 状态"
        self.assertEqual(Paths(config(local_root=root)).heartbeat, root / "service-heartbeat.json")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest discover -s tests -p 'test_monitor.py' -v`
Expected: ERROR, `ImportError: cannot import name 'monitor_settings' from 'agent.config'`.

- [ ] **Step 3: Write the implementation**

In `agent/config.py`:

1. Add `import ipaddress` to the imports.
2. Below `REQUIRED = (...)`, add:

```python
# The status monitor's listener. Loopback unless a host config opts into the office LAN; serve and workers
# never read this block, so editing it needs a monitor restart only.
MONITOR_DEFAULTS = {"bind": "127.0.0.1", "port": 8780, "hostnames": ()}
# One DNS name: dot-separated labels of lowercase letters, digits and inner hyphens.
_HOSTNAME = re.compile(r"(?=.{1,253}\Z)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*")
```

3. In `class Config`, add the field after `codex_workers`:

```python
    monitor: dict = field(default_factory=dict)
```

4. At the end of `__post_init__`, add `self._validate_monitor()`, and add this method to `Config`:

```python
    def _validate_monitor(self):
        if not isinstance(self.monitor, dict) or set(self.monitor) - set(MONITOR_DEFAULTS):
            raise ValueError("monitor accepts bind, port and hostnames only")
        bind = self.monitor.get("bind", MONITOR_DEFAULTS["bind"])
        try:
            valid = isinstance(bind, str) and str(ipaddress.IPv4Address(bind)) == bind
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("monitor.bind must be an IPv4 address such as 127.0.0.1 or 0.0.0.0")
        port = self.monitor.get("port", MONITOR_DEFAULTS["port"])
        if type(port) is not int or not 1 <= port <= 65535 or port == self.port:
            raise ValueError("monitor.port must be a TCP port other than the receiver's")
        hostnames = self.monitor.get("hostnames", [])
        if (not isinstance(hostnames, list) or len(hostnames) > 16
                or any(not isinstance(name, str) or not _HOSTNAME.fullmatch(name) for name in hostnames)):
            raise ValueError("monitor.hostnames must list at most 16 lowercase DNS names")
```

5. Below the `Config` class, add:

```python
def monitor_settings(config):
    """The monitor's listener with defaults applied; Config has already validated the block."""
    settings = {**MONITOR_DEFAULTS, **config.monitor}
    settings["hostnames"] = tuple(settings["hostnames"])
    return settings
```

6. In `Paths.__init__`, add:

```python
        self.heartbeat = Path(config.local_root) / "service-heartbeat.json"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s tests -p 'test_monitor.py' -v`
Expected: 4 tests, OK.
Run: `python3 -m unittest discover -s tests -p 'test_worker_models.py' -v` and `python3 -m unittest discover -s tests -p 'test_environment.py' -v`
Expected: all pass. They cover the other `Config` validation.

- [ ] **Step 5: Commit**

```bash
git add agent/config.py tests/test_monitor.py
git commit -F - <<'EOF'
Add the status monitor's config block and heartbeat path

The monitor listens on loopback unless a host config names a LAN address;
its port can never be the receiver's, which the tunnel forwards.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 3: Heartbeat recorder and file

**Files:**
- Create: `agent/heartbeat.py`
- Test: `tests/test_heartbeat.py`

**Interfaces:**
- Consumes: `agent.launcher._read_worker_file(path)` (bounded, non-blocking, regular files only; raises `OSError` otherwise) and `agent.launcher._write_worker_file(path, text)` (writes a new file and `os.replace`s it over `path`).
- Produces (all in `agent.heartbeat`):
  - Constants `SCHEMA_VERSION = 1`, `INTERVAL = 5.0`, `STALE_AFTER = 60.0`, `MAX_BYTES = 65536`, `LOOPS = ("receive", "schedule", "pool", "lifecycle", "progress", "resource_recovery")`, `PHASES = ("starting", "serving", "stopped")`, and `OUTCOMES = ("accepted", "duplicate", "ignored", "rejected", "malformed", "failed")`.
  - `webhook_outcome(status: int, result: str) -> str`.
  - `class Heartbeat(*, runtime: str, revision=(None, False), clock=time.time)`, with methods `set_phase(phase)`, `loop_started(name)`, `loop_finished(name)`, `loop_failed(name, exc)`, `webhook(kind, status, result)`, `payload() -> dict` and `write(path)`.
  - `validate(raw) -> dict` with keys `phase`, `started_at`, `written_at`, `stopped_at`, `revision`, `dirty`, `runtime`, `loops` (`{name: {started_at, finished_at, consecutive_errors, error_type, error_at}}`) and `webhooks` (`{last_at, last_type, last_rejected_at, counts}`).
  - `read(path, *, now) -> (state, beat_or_None)`, where the state is `fresh`, `stale`, `missing` or `unreadable`.
  - `source_revision(root) -> (str_or_None, bool)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_heartbeat.py`:

```python
"""The heartbeat serve writes and the status monitor reads: recorded honestly, read defensively."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent import heartbeat
from agent.heartbeat import Heartbeat, read, source_revision, webhook_outcome


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class HeartbeatRecordTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.beat = Heartbeat(runtime="codex", revision=("a3f9f77c1d2e", False), clock=self.clock)

    def test_a_new_heartbeat_is_starting_with_no_loops(self):
        payload = self.beat.payload()
        self.assertEqual((payload["phase"], payload["started_at"], payload["stopped_at"]), ("starting", 1000.0, None))
        self.assertEqual(payload["loops"], {})
        self.assertEqual(payload["webhooks"]["counts"], dict.fromkeys(heartbeat.OUTCOMES, 0))

    def test_loops_record_work_and_errors_reset_after_a_success(self):
        self.beat.loop_started("schedule")
        self.clock.now = 1002.0
        self.beat.loop_failed("schedule", RuntimeError("private detail"))
        self.beat.loop_started("schedule")
        self.clock.now = 1003.0
        self.beat.loop_failed("schedule", TimeoutError())
        record = self.beat.payload()["loops"]["schedule"]
        self.assertEqual((record["consecutive_errors"], record["error_type"], record["error_at"]),
                         (2, "TimeoutError", 1003.0))
        self.clock.now = 1004.0
        self.beat.loop_started("schedule")
        self.clock.now = 1005.0
        self.beat.loop_finished("schedule")
        record = self.beat.payload()["loops"]["schedule"]
        self.assertEqual((record["started_at"], record["finished_at"], record["consecutive_errors"]),
                         (1004.0, 1005.0, 0))
        self.assertEqual(record["error_type"], "TimeoutError")  # the last error stays visible after recovery
        self.assertNotIn("private detail", json.dumps(self.beat.payload()))

    def test_webhook_outcomes_are_classified_and_rejections_timestamped(self):
        cases = [(200, "accepted", "accepted"), (200, "stop received", "accepted"), (200, "duplicate", "duplicate"),
                 (200, "ignored", "ignored"), (401, "invalid signature", "rejected"),
                 (403, "identity mismatch", "rejected"), (400, "invalid json", "malformed"),
                 (408, "body timeout", "malformed"), (413, "invalid body size", "malformed"),
                 (500, "receiver error", "failed")]
        for status, result, outcome in cases:
            with self.subTest(status=status, result=result):
                self.assertEqual(webhook_outcome(status, result), outcome)
        self.clock.now = 1010.0
        self.beat.webhook("Issue", 200, "ignored")
        self.clock.now = 1020.0
        self.beat.webhook("<script>", 401, "invalid signature")
        hooks = self.beat.payload()["webhooks"]
        self.assertEqual((hooks["last_at"], hooks["last_rejected_at"], hooks["last_type"]), (1020.0, 1020.0, None))
        self.assertEqual((hooks["counts"]["ignored"], hooks["counts"]["rejected"]), (1, 1))

    def test_stopping_records_when_and_unknown_phases_are_refused(self):
        self.clock.now = 1100.0
        self.beat.set_phase("stopped")
        payload = self.beat.payload()
        self.assertEqual((payload["phase"], payload["stopped_at"]), ("stopped", 1100.0))
        with self.assertRaises(ValueError):
            self.beat.set_phase("paused")


class HeartbeatFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "state 状态" / "service-heartbeat.json"
        self.path.parent.mkdir()
        self.clock = Clock()

    def payload(self, **changes):
        return Heartbeat(runtime="codex", clock=self.clock).payload() | changes

    def write(self, value):
        self.path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")

    def test_a_written_beat_reads_back_fresh_then_stale(self):
        beat = Heartbeat(runtime="codex", revision=("a3f9f77c1d2e", True), clock=self.clock)
        beat.loop_started("receive")
        beat.webhook("AgentSessionEvent", 200, "accepted")
        beat.write(self.path)
        state, read_back = read(self.path, now=1030.0)
        self.assertEqual(state, "fresh")
        self.assertEqual((read_back["revision"], read_back["dirty"], read_back["runtime"]),
                         ("a3f9f77c1d2e", True, "codex"))
        self.assertEqual(read_back["loops"]["receive"]["started_at"], 1000.0)
        self.assertEqual(read_back["webhooks"]["counts"]["accepted"], 1)
        self.assertEqual(read(self.path, now=1000.0 + heartbeat.STALE_AFTER)[0], "stale")

    def test_missing_and_hostile_files_are_never_healthy(self):
        self.assertEqual(read(self.path, now=1000.0), ("missing", None))
        nan = json.dumps(self.payload()).replace('"written_at": 1000.0', '"written_at": NaN')
        hostile = ["not json", "[]", nan, "[" * 20_000, self.payload(schema_version=2),
                   self.payload(phase="paused"), self.payload(written_at=True), self.payload(written_at=1e300),
                   self.payload(revision="HEAD~1"), self.payload(runtime="<b>codex</b>"), self.payload(dirty="no"),
                   self.payload(loops={"schedule": {"consecutive_errors": -1}}),
                   self.payload(loops={"schedule": {"consecutive_errors": 0, "error_type": "</script>"}}),
                   self.payload(webhooks={"counts": {"rejected": "many"}})]
        for value in hostile:
            with self.subTest(value=str(value)[:60]):
                self.write(value)
                self.assertEqual(read(self.path, now=1000.0), ("unreadable", None))
        self.path.write_bytes(b"\xff\xfe")
        self.assertEqual(read(self.path, now=1000.0), ("unreadable", None))

    def test_an_oversized_file_is_unreadable(self):
        self.write(self.payload(padding="x" * heartbeat.MAX_BYTES))
        self.assertEqual(read(self.path, now=1000.0), ("unreadable", None))

    def test_unknown_keys_and_loops_are_ignored_for_forward_compatibility(self):
        payload = self.payload(extra={"nested": True})
        payload["loops"] = {"future_loop": {"anything": 1},
                            "receive": {"started_at": 999.0, "finished_at": None, "consecutive_errors": 0,
                                        "error_type": None, "error_at": None}}
        self.write(payload)
        state, beat = read(self.path, now=1000.0)
        self.assertEqual(state, "fresh")
        self.assertEqual(list(beat["loops"]), ["receive"])

    @unittest.skipIf(os.name == "nt", "FIFOs and O_NOFOLLOW symlink refusal are POSIX")
    def test_a_fifo_or_symlink_is_unreadable_without_waiting(self):
        os.mkfifo(self.path)
        self.assertEqual(read(self.path, now=1000.0), ("unreadable", None))
        self.path.unlink()
        target = Path(self.tmp.name) / "elsewhere.json"
        target.write_text(json.dumps(self.payload()), encoding="utf-8")
        self.path.symlink_to(target)
        self.assertEqual(read(self.path, now=1000.0), ("unreadable", None))

    @unittest.skipIf(os.name == "nt", "FIFOs are POSIX")
    def test_write_replaces_a_symlink_or_fifo_instead_of_opening_it(self):
        target = Path(self.tmp.name) / "elsewhere.json"
        target.write_text("untouched", encoding="utf-8")
        self.path.symlink_to(target)
        Heartbeat(runtime="codex", clock=self.clock).write(self.path)
        self.assertFalse(self.path.is_symlink())
        self.assertEqual(target.read_text(encoding="utf-8"), "untouched")
        self.path.unlink()
        os.mkfifo(self.path)
        Heartbeat(runtime="codex", clock=self.clock).write(self.path)  # opening the FIFO would block forever
        self.assertEqual(read(self.path, now=1000.0)[0], "fresh")

    def test_a_failed_replace_leaves_no_temporary_file(self):
        with patch("agent.launcher.os.replace", side_effect=PermissionError("sharing violation")):
            with self.assertRaises(PermissionError):
                Heartbeat(runtime="codex", clock=self.clock).write(self.path)
        self.assertEqual(list(self.path.parent.iterdir()), [])


class SourceRevisionTests(unittest.TestCase):
    def test_a_checkout_reports_its_commit_and_whether_tracked_files_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "checkout 检出"
            root.mkdir()

            def git(*args):
                return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root,
                                      check=True, capture_output=True, text=True).stdout.strip()

            git("init", "-q", "-b", "main", ".")
            (root / "tracked.txt").write_text("one", encoding="utf-8")
            git("add", ".")
            git("commit", "-qm", "init")
            head = git("rev-parse", "HEAD")
            self.assertEqual(source_revision(root), (head[:12], False))
            (root / "untracked.txt").write_text("ignored", encoding="utf-8")
            self.assertEqual(source_revision(root), (head[:12], False))
            (root / "tracked.txt").write_text("two", encoding="utf-8")
            self.assertEqual(source_revision(root), (head[:12], True))

    def test_outside_a_checkout_or_without_git_there_is_no_revision(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"GIT_CEILING_DIRECTORIES": str(Path(tmp).resolve().parent)}):
            self.assertEqual(source_revision(Path(tmp)), (None, False))
        with patch("agent.heartbeat.subprocess.run", side_effect=FileNotFoundError("git")):
            self.assertEqual(source_revision(Path.cwd()), (None, False))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s tests -p 'test_heartbeat.py' -v`
Expected: ERROR, `ImportError: cannot import name 'heartbeat' from 'agent'` (the module does not exist yet).

- [ ] **Step 3: Write the implementation**

Create `agent/heartbeat.py`:

```python
"""The service heartbeat: what `serve` is doing, for the status monitor to read (docs/operating-contract.md).

`serve` is the only writer. The file sits in the state root, which Codex workers are not given, but a Claude
worker has no OS sandbox, so the reader treats it as untrusted display data: bounded, regular files only, and
every field it uses validated. Unknown keys and loops are ignored so an older monitor can read a newer beat.
"""
import json
import math
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

    def __init__(self, *, runtime, revision=(None, False), clock=time.time):
        self.clock = clock
        self._lock = threading.Lock()
        self._runtime = runtime
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

    def payload(self):
        with self._lock:
            return {"schema_version": SCHEMA_VERSION, "phase": self._phase, "started_at": self._started_at,
                    "written_at": self.clock(), "stopped_at": self._stopped_at, "revision": self._revision,
                    "dirty": self._dirty, "runtime": self._runtime,
                    "loops": {name: dict(record) for name, record in self._loops.items()},
                    "webhooks": {**self._webhooks, "counts": dict(self._webhooks["counts"])}}

    def write(self, path):
        """Replace the file at `path`, never opening or writing through what is there: a worker may reach it."""
        _write_worker_file(path, json.dumps(self.payload(), ensure_ascii=False, allow_nan=False))


def _time(value, *, optional=False):
    if value is None and optional:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value < 1e11:
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


def validate(raw):
    """The fields the monitor uses, checked one by one; ValueError for anything else."""
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("heartbeat schema")
    if raw.get("phase") not in PHASES or type(raw.get("dirty")) is not bool:
        raise ValueError("heartbeat phase")
    loops = raw.get("loops")
    if not isinstance(loops, dict):
        raise ValueError("heartbeat loops")
    return {"phase": raw["phase"], "started_at": _time(raw.get("started_at")),
            "written_at": _time(raw.get("written_at")), "stopped_at": _time(raw.get("stopped_at"), optional=True),
            "revision": _matching(_REVISION, raw.get("revision")), "dirty": raw["dirty"],
            "runtime": _matching(_RUNTIME, raw.get("runtime"), optional=False),
            "loops": {name: _loop(loops[name]) for name in LOOPS if name in loops},
            "webhooks": _webhooks(raw.get("webhooks"))}


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
    """(first 12 characters of HEAD, whether tracked files differ) for the checkout at `root`, or (None, False)
    when Git is missing, times out or `root` is not a checkout."""
    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, encoding="utf-8",
                              errors="replace", timeout=5, check=True).stdout

    try:
        head = git("rev-parse", "HEAD").strip()
        dirty = bool(git("status", "--porcelain", "--untracked-files=no").strip())
    except (OSError, subprocess.SubprocessError):
        return None, False
    return (head[:12], dirty) if _COMMIT.fullmatch(head) else (None, False)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s tests -p 'test_heartbeat.py' -v`
Expected: all tests OK on macOS. On Windows the FIFO/symlink tests are skipped with their reason.

- [ ] **Step 5: Commit**

```bash
git add agent/heartbeat.py tests/test_heartbeat.py
git commit -F - <<'EOF'
Add the service heartbeat the status monitor reads

serve records loop work, phase, revision and webhook outcomes and replaces
one small file; the reader treats that file as untrusted display data.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 4: `serve` writes the heartbeat and the receiver counts webhooks

**Files:**
- Modify: `agent/service.py` (imports and `_serve`)
- Modify: `agent/receiver.py` (`make_server`'s handler and the server attribute)
- Test: `tests/test_service.py` (`ServeTests`, `LoopGuardTests`), `tests/test_receiver.py` (`HttpTests`)

**Interfaces:**
- Consumes: `Heartbeat`, `INTERVAL`, `source_revision`, `read` and `LOOPS` from Task 3; `Paths.heartbeat` from Task 2.
- Produces:
  - `components.server.heartbeat`: the serving process's `Heartbeat`, and `None` on a server from `make_server`.
  - `_serve` writes `Paths(config).heartbeat` with phase `starting` before `pool.ensure()`, then `serving` every 5 s, then `stopped` on shutdown.
  - Each loop body returns its pause in seconds, and the heartbeat times only the work.

- [ ] **Step 1: Write the failing tests**

In `tests/test_service.py`, add `from agent.heartbeat import LOOPS, read` to the imports, and add these methods to `ServeTests`:

```python
    def wait_for_beat(self, path, predicate):
        deadline = time.time() + 20
        while time.time() < deadline:
            beat = read(path, now=time.time())[1]
            if beat is not None and predicate(beat):
                return beat
            time.sleep(0.05)
        self.fail(f"no matching heartbeat at {path}")

    def test_serve_reports_starting_serving_and_stopped_in_its_heartbeat(self):
        release = threading.Event()
        pool = SimpleNamespace(ensure=lambda: release.wait(20), tick=lambda: None, close=lambda: None)
        components = self.c._replace(pool=pool)
        path = Paths(components.config).heartbeat
        problems = []

        def run():
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    serve(components=components)
            except BaseException as exc:
                problems.append(exc)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 20)
        self.addCleanup(release.set)
        self.wait_for_beat(path, lambda beat: beat["phase"] == "starting")  # written while ensure() still runs
        release.set()
        self.assertTrue(self.wait_for_health(), problems)
        self.addCleanup(self.c.server.shutdown)
        self.wait_for_beat(path, lambda beat: beat["phase"] == "serving")
        deadline = time.time() + 20
        while set(LOOPS) - set(self.c.server.heartbeat.payload()["loops"]) and time.time() < deadline:
            time.sleep(0.05)
        self.c.server.shutdown()
        thread.join(timeout=20)
        self.assertFalse(thread.is_alive())
        self.assertEqual(problems, [])
        state, beat = read(path, now=time.time())
        self.assertEqual((state, beat["phase"]), ("fresh", "stopped"))
        self.assertIsNotNone(beat["stopped_at"])
        self.assertEqual(set(beat["loops"]), set(LOOPS))

    def test_a_heartbeat_that_cannot_be_written_never_stops_serving(self):
        problems = []

        def run():
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    serve(components=self.c)
            except BaseException as exc:
                problems.append(exc)

        with patch("agent.heartbeat._write_worker_file", side_effect=PermissionError("sharing violation")):
            thread = threading.Thread(target=run, daemon=True)
            thread.start()
            self.addCleanup(thread.join, 20)
            self.assertTrue(self.wait_for_health(), problems)
            self.addCleanup(self.c.server.shutdown)
            self.c.server.shutdown()
            thread.join(timeout=20)
        self.assertFalse(thread.is_alive())
        self.assertEqual(problems, [])
        self.assertFalse(Paths(self.c.config).heartbeat.exists())
```

At the end of `LoopGuardTests.test_a_raising_loop_body_is_logged_and_the_loop_keeps_running`, add:

```python
        record = server.heartbeat.payload()["loops"]["receive"]
        self.assertEqual((record["error_type"], record["consecutive_errors"]), ("RuntimeError", 0))
```

In `tests/test_receiver.py`, add `import contextlib`, `import io`, `import threading` and `import time` to the imports, and add to `HttpTests`:

```python
    def serve_http(self, heartbeat):
        server = make_server(self.receiver, port=0)
        server.heartbeat = heartbeat
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}/webhook"

    def signed(self, body):
        return {"Linear-Signature": hmac.new(b"signing-secret", body, hashlib.sha256).hexdigest()}

    def test_every_webhook_outcome_is_counted_on_the_server_heartbeat(self):
        from agent.heartbeat import Heartbeat
        beat = Heartbeat(runtime="fake")
        url = self.serve_http(beat)
        body = json.dumps(self.event(webhookTimestamp=int(time.time() * 1000))).encode()
        with contextlib.redirect_stdout(io.StringIO()):
            with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=self.signed(body)),
                                        timeout=5) as response:
                self.assertEqual(json.load(response)["status"], "accepted")
            for request, code in ((urllib.request.Request(url, data=body), 401),
                                  (urllib.request.Request(url, data=b"{", headers=self.signed(b"{")), 400)):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(request, timeout=5)
                self.assertEqual(caught.exception.code, code)
                caught.exception.close()
        hooks = beat.payload()["webhooks"]
        self.assertEqual((hooks["counts"]["accepted"], hooks["counts"]["rejected"], hooks["counts"]["malformed"]),
                         (1, 1, 1))
        self.assertIsNotNone(hooks["last_rejected_at"])

    def test_a_failing_counter_never_changes_a_webhook_answer(self):
        url = self.serve_http(Mock(webhook=Mock(side_effect=RuntimeError("counter broke"))))
        body = json.dumps(self.event(webhookTimestamp=int(time.time() * 1000))).encode()
        with contextlib.redirect_stdout(io.StringIO()):
            with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=self.signed(body)),
                                        timeout=5) as response:
                self.assertEqual(json.load(response)["status"], "accepted")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s tests -p 'test_service.py' -k heartbeat -v`
Expected: FAIL/ERROR. The service writes no heartbeat, and `self.c.server` has no `heartbeat` attribute.
Run: `python3 -m unittest discover -s tests -p 'test_receiver.py' -k counter -v` and `... -k counted -v`
Expected: FAIL, because the counts stay at 0.

- [ ] **Step 3: Write the implementation**

In `agent/receiver.py`, inside `make_server`'s `Handler`, add this method and route every `/webhook` answer through it:

```python
        def answer_webhook(self, kind, status, message):
            heartbeat = getattr(self.server, "heartbeat", None)
            if heartbeat is not None:
                try:
                    heartbeat.webhook(kind, status, message)
                except Exception:
                    pass  # counting feeds the status page and must never change a webhook's answer
            self.respond(status, message)
```

Replace `do_POST` with:

```python
        def do_POST(self):
            if self.path != "/webhook":
                return self.respond(404, "not found")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self.answer_webhook(None, 400, "invalid length")
            if length <= 0 or length > MAX_BODY:
                return self.answer_webhook(None, 413, "invalid body size")
            self.connection.settimeout(3)
            try:
                raw = self.rfile.read(length)
                if len(raw) != length:
                    return self.answer_webhook(None, 400, "incomplete body")
                status, message = self.server.receiver.receive(raw, self.headers.get("Linear-Signature"))
            except TimeoutError:
                return self.answer_webhook(None, 408, "body timeout")
            except Exception:
                return self.answer_webhook(None, 500, "receiver error")
            # One line per delivery so an ignored or rejected event is visible in the service log; never the
            # body. Written before the response, so a caller holding its reply knows the line exists.
            try:
                event = json.loads(raw)
                kind = event.get("type") if isinstance(event, dict) else None
                action = event.get("action") if isinstance(event, dict) else None
            except (ValueError, UnicodeError):
                kind = action = None
            print(json.dumps({"event": "webhook", "status": status, "result": message, "type": kind, "action": action}), flush=True)
            self.answer_webhook(kind, status, message)
```

After `server.receiver = receiver`, add:

```python
    server.heartbeat = None  # a serving process installs its Heartbeat here (service._serve)
```

In `agent/service.py`, add `from .heartbeat import Heartbeat, INTERVAL as HEARTBEAT_INTERVAL, source_revision` below the `from .environment import ...` line. Then make the following exact replacements in `_serve`. Everything not shown stays as it is, including the `terminate` handler, the `SlotError` handler and the `ready` line.

1. Replace the opening of `_serve`, from `def _serve(components):` through `schedule_once`:

```python
def _serve(components):
    stop = threading.Event()

    def guarded(name, body):
        """One loop iteration never kills its thread: log the kind of failure and back off."""
        def loop():
            while not stop.is_set():
                try:
                    body()
                except Exception as exc:
                    print(json.dumps({"event": "loop_error", "loop": name, "error": type(exc).__name__}), flush=True)
                    stop.wait(1.0)
        return loop

    def receive_once():
        if not components.receiver.process_one():
            stop.wait(0.1)

    def schedule_once():
        components.scheduler.tick()
        stop.wait(1.0)
```

with:

```python
def _serve(components):
    stop = threading.Event()
    heartbeat = Heartbeat(runtime=components.launcher.runtime.name, revision=source_revision(ROOT))
    heartbeat_path = Paths(components.config).heartbeat
    # The receiver's handler counts each /webhook outcome into it (receiver.make_server).
    components.server.heartbeat = heartbeat

    def beat():
        try:
            heartbeat.write(heartbeat_path)
        except OSError:
            pass  # a skipped beat shows on the monitor as age, never as a stopped service

    def beat_loop():
        while not stop.wait(HEARTBEAT_INTERVAL):
            beat()

    def guarded(name, work):
        """One loop iteration never kills its thread: log the kind of failure and back off.

        `work` returns how long to pause before the next iteration. The heartbeat times the work and not the
        pause, so a loop that sleeps between ticks is not reported as busy."""
        def loop():
            while not stop.is_set():
                heartbeat.loop_started(name)
                try:
                    pause = work()
                except Exception as exc:
                    heartbeat.loop_failed(name, exc)
                    print(json.dumps({"event": "loop_error", "loop": name, "error": type(exc).__name__}), flush=True)
                    pause = 1.0
                else:
                    heartbeat.loop_finished(name)
                stop.wait(pause)
        return loop

    def receive_once():
        return 0.0 if components.receiver.process_one() else 0.1

    def schedule_once():
        components.scheduler.tick()
        return 1.0
```

2. In `pool_once`, keep its comment and replace `stop.wait(2.0)` with `return 2.0`.

3. Replace the three optional loop bodies:

```python
        def reconcile_once():
            result = components.lifecycle.tick()
            stop.wait(0.1 if result["checked"] else 1.0)
```

```python
        def progress_once():
            sent = components.progress.tick()
            stop.wait(0.1 if sent else 5.0)
```

```python
        def recover_resources_once():
            components.recovery.tick()
            stop.wait(15.0)
```

with:

```python
        def reconcile_once():
            return 0.1 if components.lifecycle.tick()["checked"] else 1.0
```

```python
        def progress_once():
            return 0.1 if components.progress.tick() else 5.0
```

```python
        def recover_resources_once():
            components.recovery.tick()
            return 15.0
```

4. Immediately before `main_thread = threading.current_thread() is threading.main_thread()`, add:

```python
    beat_thread = threading.Thread(target=beat_loop, daemon=True)
```

5. Replace:

```python
            signal.signal(signal.SIGTERM, terminate)
        try:
            components.pool.ensure()
```

with:

```python
            signal.signal(signal.SIGTERM, terminate)
        # The first beat precedes ensure(): preparing slots can take minutes, and the monitor shows 正在启动.
        beat()
        beat_thread.start()
        try:
            components.pool.ensure()
```

6. Replace:

```python
        for thread in threads:
            thread.start()
        print(json.dumps({"event": "ready",
```

with:

```python
        for thread in threads:
            thread.start()
        heartbeat.set_phase("serving")
        beat()
        print(json.dumps({"event": "ready",
```

7. In the `finally` block, replace:

```python
                    thread.join()
            components.receiver.close()
```

with:

```python
                    thread.join()
            if beat_thread.ident is not None:
                beat_thread.join()
            heartbeat.set_phase("stopped")
            beat()
            components.receiver.close()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s tests -p 'test_service.py' -v`
Expected: all tests pass, including the new heartbeat tests, the loop guard, and the POSIX SIGTERM subprocess tests (run with `-W error`).
Run: `python3 -m unittest discover -s tests -p 'test_receiver.py' -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add agent/service.py agent/receiver.py tests/test_service.py tests/test_receiver.py
git commit -F - <<'EOF'
Write a heartbeat from serve and count webhook outcomes

The heartbeat is written before slot preparation, every five seconds while
serving and once more on clean shutdown. Loops now time their work rather
than their pause, and a failed write never stops the service.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 5: The status document (`monitor_view`)

**Files:**
- Create: `agent/monitor_view.py`
- Test: `tests/test_monitor_view.py`

**Interfaces:**
- Consumes: `snapshot_connection` (Task 1), and `LOOPS`, `Heartbeat` and `validate` (Task 3). `agent.resource_recovery.MAX_REPAIR_ATTEMPTS` already exists.
- Produces:
  - `agent.monitor_view.build_status(ledger_path, *, heartbeat, health, instance, monitor_revision, now, failing_since=None) -> dict`. Its arguments: `heartbeat` is `read()`'s `(state, beat)`; `health` is `{"ok", "status", "latency_ms", "error_type", "checked_at"}`; `instance` is `{"environment", "instance_id", "bot_name", "host"}`; `monitor_revision` is `(revision, dirty)`.
  - Constants `VERDICTS`, `DISPLAY_STATES`, `OUTCOMES`, `ATTENTION_CODES`, `RECENT_LIMIT`, `CLEANUP_GRACE` and `LOOP_LIMITS`, which Task 6's label test and Task 7 use.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_monitor_view.py`:

```python
"""The /api/status document: what it shows, what it never shows, and how it judges the service."""
from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent.heartbeat import Heartbeat, validate
from agent.ledger import Ledger
from agent.monitor_view import CLEANUP_GRACE, LOOP_LIMITS, RECENT_LIMIT, build_status
from agent.resource_recovery import MAX_REPAIR_ATTEMPTS, RecoveryStore
from test_ledger import PIN, comment, issue

NOW = 1_000_000.0
HEALTHY = {"ok": True, "status": 200, "latency_ms": 3, "error_type": None, "checked_at": NOW}
DOWN = {"ok": False, "status": None, "latency_ms": 2000, "error_type": "ConnectionRefusedError", "checked_at": NOW}
INSTANCE = {"environment": "production", "instance_id": "default", "bot_name": "FarmBot", "host": "farm-host"}


def beat(phase="serving", written_at=NOW - 1, *, loops=None, rejected_at=None, rejected=0, stopped_at=None):
    payload = Heartbeat(runtime="codex", revision=("a3f9f77c1d2e", False), clock=lambda: NOW - 100).payload()
    payload.update(phase=phase, written_at=written_at, stopped_at=stopped_at, loops=loops or {})
    payload["webhooks"]["last_rejected_at"] = rejected_at
    payload["webhooks"]["counts"]["rejected"] = rejected
    return validate(payload)


def loop(started_at, finished_at, errors=0, error_type=None):
    return {"started_at": started_at, "finished_at": finished_at, "consecutive_errors": errors,
            "error_type": error_type, "error_at": finished_at if errors else None}


class ViewBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "state 状态" / "ledger.sqlite3"
        self.clock = NOW - 3600
        self.ledger = Ledger(self.path, clock=lambda: self.clock, lease_seconds=3600)
        self.addCleanup(self.ledger.close)
        self.count = 0

    def job(self, skill="fix", **changes):
        self.count += 1
        n = self.count
        issue_id = f"10000000-0000-4000-8000-{n:012d}"
        self.ledger.observe_issue(issue(**({"id": issue_id, "identifier": f"FARM-{n}", "title": f"标题 {n}",
                                            "url": f"https://linear.app/k/issue/FARM-{n}"} | changes)))
        self.ledger.ensure_session(f"session-{n}", issue_id, delegation=True)
        return self.ledger.create_work_item(issue_id=issue_id, session_id=f"session-{n}", skill=skill, target=PIN)

    def claim(self, item):
        self.ledger.set_worker(item["id"], 515151, "farm-host")
        return self.ledger.claim(item["id"], worker_id="test")

    def sql(self, statement, *values):
        self.ledger.connection.execute(statement, values)

    def status(self, heartbeat=("missing", None), health=HEALTHY, failing_since=None, now=NOW):
        return build_status(self.path, heartbeat=heartbeat, health=health, instance=INSTANCE,
                            monitor_revision=("b1d5bd4a0c11", False), now=now, failing_since=failing_since)

    def active(self, document, identifier):
        return next(job for job in document["active"] if job["identifier"] == identifier)


class ActiveWorkTests(ViewBase):
    def test_running_and_waiting_jobs_show_identity_stage_and_timing(self):
        running, waiting = self.job(), self.job(skill="chat")
        self.clock = NOW - 1800
        claim = self.claim(running)
        self.sql("UPDATE work_items SET stage='verify', root_repo='farmgui' WHERE id=?", running["id"])
        self.clock = NOW - 1200
        answer = self.claim(waiting)
        self.clock = NOW - 900
        self.ledger.await_input(waiting["id"], answer["token"], "private question")
        self.clock = NOW - 600
        self.ledger.note(running["id"], "checkpoint", "saved")
        self.clock = NOW - 60
        self.ledger.renew(running["id"], claim["token"])  # moves updated_at; state_since must not follow it
        document = self.status()
        job = self.active(document, "FARM-1")
        self.assertEqual({key: job[key] for key in ("title", "url", "skill", "state", "display_state", "stage", "repo")},
                         {"title": "标题 1", "url": "https://linear.app/k/issue/FARM-1", "skill": "fix",
                          "state": "running", "display_state": "running", "stage": "verify", "repo": "farmgui"})
        self.assertEqual((job["state_since"], job["checkpoint_at"]), (NOW - 1800, NOW - 600))
        chat = self.active(document, "FARM-2")
        self.assertEqual((chat["display_state"], chat["state_since"], chat["checkpoint_at"]),
                         ("awaiting_input", NOW - 900, None))
        self.assertEqual(document["counts"], {"queued": 0, "running": 1, "awaiting_input": 1, "awaiting_resource": 0})

    def test_queued_work_is_split_into_launching_switching_retry_and_queue_position(self):
        first, second, launching, switching, retrying = (self.job() for _ in range(5))
        self.sql("UPDATE work_items SET priority=1 WHERE id=?", second["id"])
        self.ledger.set_worker(launching["id"], 5151, "farm-host")
        self.sql("UPDATE work_items SET worker_pid=5252, next_root_repo='farm-hive' WHERE id=?", switching["id"])
        self.sql("UPDATE work_items SET retry_not_before=? WHERE id=?", NOW + 300, retrying["id"])
        states = {job["identifier"]: (job["display_state"], job["queue_position"], job["retry_at"])
                  for job in self.status()["active"]}
        self.assertEqual(states, {"FARM-1": ("queued", 2, None), "FARM-2": ("queued", 1, None),
                                  "FARM-3": ("launching", None, None), "FARM-4": ("switching_repo", None, None),
                                  "FARM-5": ("retry_wait", None, NOW + 300)})
        self.assertEqual(self.active(self.status(), "FARM-1")["state_since"], NOW - 3600)  # created, never requeued

    def test_unity_waits_show_their_reservation_queue_position_and_recovery(self):
        jobs = [self.job() for _ in range(3)]
        for job in jobs:
            self.ledger.await_resource(job["id"], self.claim(job)["token"], "unity_slot", "batch")
        self.sql("UPDATE work_items SET stage='waiting_for_recovery' WHERE id=?", jobs[2]["id"])
        document = self.status()
        states = {job["identifier"]: (job["display_state"], job["queue_position"]) for job in document["active"]}
        self.assertEqual(states, {"FARM-1": ("awaiting_resource", 1), "FARM-2": ("awaiting_resource", 2),
                                  "FARM-3": ("waiting_for_recovery", 3)})
        self.assertEqual(document["unity_queue"], 3)

    def test_only_linear_issue_links_and_github_pull_requests_are_emitted(self):
        safe, unsafe = self.job(), self.job(url="javascript:alert(1)")
        for url in ("https://github.com/Kuaiwa-Network/farmgui/pull/512", "https://evil.example/owner/repo/pull/1",
                    "https://github.com/Kuaiwa-Network/farmgui/pull/512?x=<script>"):
            self.sql("INSERT INTO published_prs(issue_id,url,generation,created_at) VALUES(?,?,0,?)",
                     safe["issue_id"], url, NOW)
        document = self.status()
        self.assertEqual(self.active(document, "FARM-1")["prs"],
                         [{"url": "https://github.com/Kuaiwa-Network/farmgui/pull/512", "label": "farmgui#512"}])
        self.assertIsNone(self.active(document, "FARM-2")["url"])

    def test_long_text_is_clamped(self):
        item = self.job(title="长" * 500)
        self.sql("UPDATE work_items SET stage=? WHERE id=?", "s" * 500, item["id"])
        job = self.status()["active"][0]
        self.assertEqual((len(job["title"]), len(job["stage"])), (200, 120))


class HistoryTests(ViewBase):
    def test_recent_work_is_the_newest_thirty_of_the_last_seven_days(self):
        items = [self.job() for _ in range(RECENT_LIMIT + 2)]
        for index, item in enumerate(items):
            self.sql("UPDATE work_items SET state='delivered', updated_at=? WHERE id=?", NOW - 3600 * (index + 1), item["id"])
        self.sql("UPDATE work_items SET updated_at=? WHERE id=?", NOW - 8 * 86400, items[0]["id"])
        recent = self.status()["recent"]
        self.assertEqual(len(recent), RECENT_LIMIT)
        self.assertEqual(recent[0]["identifier"], "FARM-2")
        self.assertNotIn("FARM-1", [job["identifier"] for job in recent])

    def test_outcomes_distinguish_no_change_and_mark_retried_work(self):
        delivered, unchanged, failed, cancelled, blocked = (self.job() for _ in range(5))
        for item, state, evidence in ((delivered, "delivered", '{"prs": ["https://github.com/o/r/pull/1"]}'),
                                      (unchanged, "delivered", '{"no_change": "already fixed"}'),
                                      (failed, "failed", "{}"), (cancelled, "cancelled", "{}"),
                                      (blocked, "blocked", "{}")):
            self.sql("UPDATE work_items SET state=?, evidence=?, updated_at=? WHERE id=?", state, evidence, NOW - 60, item["id"])
        successor = self.job()
        self.sql("UPDATE work_items SET predecessor_id=? WHERE id=?", failed["id"], successor["id"])
        recent = {job["identifier"]: (job["outcome"], job["retried"]) for job in self.status()["recent"]}
        self.assertEqual(recent, {"FARM-1": ("delivered", False), "FARM-2": ("no_change", False),
                                  "FARM-3": ("failed", True), "FARM-4": ("cancelled", False),
                                  "FARM-5": ("blocked", False)})


class SlotTests(ViewBase):
    def slot(self, slot_id):
        self.ledger.ensure_slot(slot_id, kind="unity_slot", host="farm-host",
                                folder=str(Path(self.tmp.name) / slot_id.replace(":", "-")))

    def test_slots_show_their_holder_mode_commit_and_recovery(self):
        self.slot("unity_slot:1")
        self.slot("unity_slot:2")
        holder = self.job()
        self.ledger.await_resource(holder["id"], self.claim(holder)["token"], "unity_slot", "interactive")
        granted = self.ledger.acquire("unity_slot", owner="pool", host="farm-host")
        self.ledger.set_slot_state(granted["resource"], "interactive_busy", parked_commit="9f2e1c0" + "0" * 33)
        self.ledger.set_slot_state("unity_slot:2", "held")
        store = RecoveryStore(self.ledger)
        store.discover("farm-host")
        recovery = store.begin(store.pending("farm-host")[0]["id"])
        store.failed(recovery["id"], recovery["attempts"], "private repair error")
        slots = {slot["slot_id"]: slot for slot in self.status()["slots"]}
        self.assertEqual(slots["unity_slot:1"], {"slot_id": "unity_slot:1", "kind": "unity_slot",
                                                 "state": "interactive_busy", "commit": "9f2e1c0",
                                                 "holder": "FARM-1", "mode": "interactive", "recovery": None})
        self.assertEqual(slots["unity_slot:2"]["recovery"],
                         {"state": "pending", "attempts": 1, "max_attempts": MAX_REPAIR_ATTEMPTS})


class AttentionTests(ViewBase):
    def found(self, document):
        return {(item["code"], item["subject"]) for item in document["attention"]}

    def test_ledger_conditions_raise_attention_only_past_their_thresholds(self):
        cleaned, fresh_cleanup, flaky, broken, expired = (self.job() for _ in range(5))
        self.sql("INSERT INTO job_cleanup(item_id, worker_pid, updated_at) VALUES(?,?,?)",
                 cleaned["id"], 1, NOW - CLEANUP_GRACE - 1)
        self.sql("INSERT INTO job_cleanup(item_id, worker_pid, updated_at) VALUES(?,?,?)",
                 fresh_cleanup["id"], 1, NOW - CLEANUP_GRACE + 30)
        self.sql("UPDATE issue_checks SET error='private', failures=2, checked_at=? WHERE issue_id=?", NOW - 5, flaky["issue_id"])
        self.sql("UPDATE issue_checks SET error='private', failures=3, checked_at=? WHERE issue_id=?", NOW - 5, broken["issue_id"])
        self.clock = NOW - 4000  # with a one-hour lease, this claim expired 400 seconds ago
        self.claim(expired)
        document = self.status()
        self.assertEqual(self.found(document), {("cleanup_pending", "FARM-1"), ("issue_status_error", "FARM-4"),
                                                ("lease_expired", "FARM-5")})
        self.assertEqual(document["verdict"], "attention")

    def test_a_busy_slot_without_a_reservation_and_a_slow_cancellation_are_flagged(self):
        for slot in ("unity_slot:1", "unity_slot:2"):
            self.ledger.ensure_slot(slot, kind="unity_slot", host="farm-host",
                                    folder=str(Path(self.tmp.name) / slot.replace(":", "-")))
        self.ledger.set_slot_state("unity_slot:1", "batch_busy")
        item = self.job()
        self.ledger.await_resource(item["id"], self.claim(item)["token"], "unity_slot", "batch")
        self.ledger.acquire("unity_slot", owner="pool", host="farm-host")  # slot 1 is busy, so slot 2
        self.clock = NOW - 400
        self.ledger.cancel(item["id"], "Linear stop")  # the active reservation becomes cancel_requested
        found = self.found(self.status())
        self.assertIn(("slot_without_reservation", "unity_slot:1"), found)
        self.assertIn(("reservation_cancel_pending", "FARM-1"), found)
        self.assertNotIn(("slot_without_reservation", "unity_slot:2"), found)


class ServiceTests(ViewBase):
    def test_the_verdict_follows_the_spec_table(self):
        self.job()
        cases = [
            ("stopped", ("stale", beat("stopped", NOW - 300, stopped_at=NOW - 300)), DOWN, NOW - 300),
            ("starting", ("fresh", beat("starting")), DOWN, NOW - 100),
            ("unresponsive", ("stale", beat("serving", NOW - 120)), DOWN, NOW - 120),
            ("unresponsive", ("missing", None), DOWN, NOW - 30),
            ("unresponsive", ("unreadable", None), DOWN, NOW - 30),
            ("attention", ("fresh", beat("serving")), DOWN, NOW - 30),
            ("attention", ("stale", beat("serving", NOW - 120)), HEALTHY, NOW - 120),
            ("attention", ("fresh", beat("stopped", NOW - 2, stopped_at=NOW - 2)), HEALTHY, NOW - 2),
            ("attention", ("unreadable", None), HEALTHY, None),
            ("ok", ("fresh", beat("serving")), HEALTHY, None),
            ("ok", ("missing", None), HEALTHY, None),
        ]
        for verdict, heartbeat, health, since in cases:
            with self.subTest(verdict=verdict, heartbeat=heartbeat[0], health=health["ok"]):
                document = self.status(heartbeat=heartbeat, health=health, failing_since=NOW - 30)
                self.assertEqual((document["verdict"], document["verdict_since"]), (verdict, since))

    def test_loops_and_webhooks_raise_attention_from_a_fresh_heartbeat(self):
        loops = {"receive": loop(NOW - 2, NOW - 1),
                 "schedule": loop(NOW - LOOP_LIMITS["schedule"] - 5, NOW - LOOP_LIMITS["schedule"] - 10),
                 "pool": loop(NOW - 600, NOW - 700),
                 "lifecycle": loop(NOW - 5, NOW - 4, errors=3, error_type="TimeoutError")}
        document = self.status(heartbeat=("fresh", beat(loops=loops, rejected_at=NOW - 60, rejected=4)))
        self.assertEqual({entry["name"]: entry["state"] for entry in document["service"]["loops"]},
                         {"receive": "idle", "schedule": "stalled", "pool": "busy", "lifecycle": "erroring"})
        self.assertEqual({(item["code"], item["subject"], item["count"]) for item in document["attention"]},
                         {("loop_stalled", "schedule", None), ("loop_erroring", "lifecycle", 3),
                          ("webhook_rejected", None, 4)})
        self.assertEqual(self.status(heartbeat=("fresh", beat(rejected_at=NOW - 901, rejected=4)))["attention"], [])

    def test_linear_and_agent_session_times_come_from_the_ledger(self):
        first, second = self.job(), self.job()
        self.sql("UPDATE issue_checks SET checked_at=? WHERE issue_id=?", NOW - 30, first["issue_id"])
        self.sql("UPDATE issue_checks SET checked_at=?, error='private', failures=1 WHERE issue_id=?", NOW - 5, second["issue_id"])
        self.sql("CREATE TABLE webhook_events (event_key TEXT PRIMARY KEY, session_id TEXT NOT NULL, ack_id TEXT NOT NULL, "
                 "status TEXT NOT NULL, payload TEXT, received_at REAL NOT NULL, completed_at REAL, error TEXT)")
        self.sql("INSERT INTO webhook_events VALUES('k','s','a','done',NULL,?,NULL,NULL)", NOW - 840)
        service = self.status()["service"]
        self.assertEqual(service["linear"], {"last_ok_at": NOW - 30, "failing_issues": 1})
        self.assertEqual(service["agent_event_at"], NOW - 840)


class OlderLedgerTests(unittest.TestCase):
    def build(self, path):
        return build_status(path, heartbeat=("missing", None), health=HEALTHY, instance=INSTANCE,
                            monitor_revision=(None, False), now=NOW)

    def test_a_ledger_with_only_the_required_tables_still_lists_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old ledger.sqlite3"
            with closing(sqlite3.connect(path)) as db:
                db.executescript("""
                    CREATE TABLE issues (id TEXT PRIMARY KEY, metadata TEXT NOT NULL);
                    CREATE TABLE work_items (id TEXT PRIMARY KEY, issue_id TEXT, skill TEXT, state TEXT, stage TEXT,
                                             created_at REAL, updated_at REAL);""")
                db.execute("INSERT INTO issues VALUES(?,?)", ("issue-1", json.dumps(
                    {"identifier": "FARM-9", "title": "旧账本", "url": "https://linear.app/k/issue/FARM-9"})))
                db.execute("INSERT INTO work_items VALUES('item-1','issue-1','fix','running','intake',?,?)", (NOW - 60, NOW - 60))
                db.commit()
            document = self.build(path)
        self.assertTrue(document["ledger"]["ok"])
        self.assertEqual([job["identifier"] for job in document["active"]], ["FARM-9"])
        self.assertIsNone(document["slots"])
        self.assertIsNone(document["unity_queue"])
        self.assertEqual(set(document["ledger"]["missing_optional"]),
                         {"audit", "published_prs", "slots", "reservations", "resource_recoveries", "job_cleanup",
                          "issue_checks", "webhook_events"})
        self.assertEqual(document["verdict"], "ok")

    def test_a_missing_ledger_or_required_table_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.sqlite3"
            missing = self.build(path)
            self.assertFalse(path.exists())
            with closing(sqlite3.connect(path)) as db:
                db.execute("CREATE TABLE issues (id TEXT PRIMARY KEY, metadata TEXT NOT NULL)")
                db.commit()
            partial = self.build(path)
        self.assertEqual((missing["verdict"], missing["ledger"]["error_type"]), ("unknown", "OperationalError"))
        self.assertEqual((partial["verdict"], partial["ledger"]["error_type"]), ("unknown", "SchemaMismatch"))


class PrivacyTests(ViewBase):
    def test_private_ledger_content_never_reaches_the_document(self):
        item = self.job(description="PRIVATE-DESCRIPTION", comments=[comment(body="PRIVATE-COMMENT")])
        claim = self.claim(item)
        self.ledger.push_inbox(item["id"], "PRIVATE-INBOX")
        self.sql("UPDATE work_items SET checkpoint=?, evidence=? WHERE id=?", json.dumps({"progress": "PRIVATE-CHECKPOINT"}),
                 json.dumps({"summary": "PRIVATE-EVIDENCE"}), item["id"])
        self.sql("UPDATE issue_checks SET error=?, failures=9, checked_at=? WHERE issue_id=?",
                 "PRIVATE-ERROR /srv/private/secret", NOW, item["issue_id"])
        self.sql("INSERT INTO job_cleanup(item_id, worker_pid, error, updated_at) VALUES(?,?,?,?)",
                 item["id"], 424242, "PRIVATE-CLEANUP", NOW - 3600)
        self.ledger.ensure_slot("unity_slot:1", kind="unity_slot", host="farm-host",
                                folder=str(Path(self.tmp.name) / "PRIVATE-FOLDER"))
        waiting = self.job()
        answer = self.claim(waiting)
        self.ledger.await_input(waiting["id"], answer["token"], "PRIVATE-QUESTION")
        token_hash = self.ledger.connection.execute("SELECT token FROM work_items WHERE id=?", (item["id"],)).fetchone()[0]
        text = json.dumps(self.status(heartbeat=("fresh", beat())), ensure_ascii=False)
        for secret in ("PRIVATE-DESCRIPTION", "PRIVATE-COMMENT", "PRIVATE-INBOX", "PRIVATE-CHECKPOINT",
                       "PRIVATE-EVIDENCE", "PRIVATE-ERROR", "/srv/private", "PRIVATE-CLEANUP", "PRIVATE-FOLDER",
                       "PRIVATE-QUESTION", "424242", "515151", claim["token"], token_hash, answer["token"], "session-1"):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, text)

    def test_building_the_document_never_modifies_the_ledger(self):
        self.job()
        self.ledger.close()  # no writer, as when serve is stopped: the WAL files have gone
        before = (self.path.stat().st_mtime_ns, self.path.read_bytes())
        self.status(heartbeat=("fresh", beat()))
        self.assertEqual((self.path.stat().st_mtime_ns, self.path.read_bytes()), before)
        self.assertFalse(self.path.with_name(self.path.name + "-journal").exists())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s tests -p 'test_monitor_view.py' -v`
Expected: ERROR, `ModuleNotFoundError: No module named 'agent.monitor_view'`.

- [ ] **Step 3: Write the implementation**

Create `agent/monitor_view.py`:

```python
"""The status monitor's /api/status document, read from one ledger without changing it.

Every emitted field is chosen here, from an allowlist: rows are never serialized whole, and issues.metadata,
which holds full descriptions and comments, is only asked for its identifier, title and URL. An older ledger
without optional tables or columns still produces a document; only the sections that need them are empty.
"""
import math
import re
import sqlite3

from .heartbeat import LOOPS
from .readonly_db import snapshot_connection
from .resource_recovery import MAX_REPAIR_ATTEMPTS

SCHEMA_VERSION = 1
ACTIVE = ("queued", "running", "awaiting_input", "awaiting_resource")
TERMINAL = ("delivered", "blocked", "failed", "cancelled")
VERDICTS = ("ok", "attention", "starting", "unresponsive", "stopped", "unknown")
DISPLAY_STATES = ("queued", "launching", "switching_repo", "retry_wait", "running", "awaiting_input",
                  "awaiting_resource", "waiting_for_recovery")
OUTCOMES = ("delivered", "no_change", "blocked", "failed", "cancelled")
ATTENTION_CODES = ("slot_held", "slot_without_reservation", "lease_expired", "cleanup_pending",
                   "issue_status_error", "reservation_cancel_pending", "loop_erroring", "loop_stalled",
                   "webhook_rejected", "receiver_unreachable", "heartbeat_stale", "heartbeat_unreadable")
RECENT_SECONDS = 7 * 86400
RECENT_LIMIT = 30
CLEANUP_GRACE = 600
STATUS_FAILURES = 3
CANCEL_GRACE = 300
REJECT_WINDOW = 900
LOOP_ERRORS = 3
LOOP_LIMITS = {"receive": 120, "lifecycle": 600, "progress": 600, "schedule": 1800,
               "resource_recovery": 1800, "pool": 5400}
KNOWN_TABLES = ("work_items", "issues", "audit", "published_prs", "slots", "reservations",
                "resource_recoveries", "job_cleanup", "issue_checks", "webhook_events")
REQUIRED = {"work_items": ("id", "issue_id", "skill", "state", "stage", "created_at", "updated_at"),
            "issues": ("id", "metadata")}
OPTIONAL_ITEM_COLUMNS = ("priority", "worker_pid", "next_root_repo", "root_repo", "retry_not_before",
                         "lease_expires_at")
SLOT_FIELDS = ("slot_id", "kind", "state", "commit", "holder", "mode", "recovery")
_PR = re.compile(r"https://github\.com/([A-Za-z0-9_.-]{1,100})/([A-Za-z0-9_.-]{1,100})/pull/([1-9][0-9]{0,9})")
_ISSUE_FIELDS = ", ".join(f"CASE WHEN json_valid(i.metadata) THEN json_extract(i.metadata,'$.{name}') END AS {name}"
                          for name in ("identifier", "title", "url"))


class SchemaMismatch(ValueError):
    """The ledger lacks a table or column the monitor cannot do without."""


def _marks(values):
    return ",".join("?" * len(values))


def _time(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def _text(value, limit):
    return value[:limit] if isinstance(value, str) and value else None


def _linear_url(value):
    if (isinstance(value, str) and len(value) <= 500 and value.startswith("https://linear.app/")
            and not any(character.isspace() for character in value)):
        return value
    return None


def _pr(url):
    match = _PR.fullmatch(url) if isinstance(url, str) else None
    return {"url": url, "label": f"{match[2]}#{match[3]}"} if match else None


def _schema(db):
    tables = {row["name"] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    # Only names from KNOWN_TABLES reach PRAGMA: a table a worker created is never interpolated into SQL.
    schema = {name: {row["name"] for row in db.execute(f"PRAGMA table_info({name})")}
              for name in KNOWN_TABLES if name in tables}
    missing = sorted({table if table not in schema else f"{table}.{column}"
                      for table, columns in REQUIRED.items() for column in columns
                      if column not in schema.get(table, ())})
    if missing:
        raise SchemaMismatch(", ".join(missing))
    return schema


def _has(schema, table, *columns):
    return table in schema and set(columns) <= schema[table]


def _identifiers(db, item_ids):
    if not item_ids:
        return {}
    rows = db.execute(f"""SELECT w.id, {_ISSUE_FIELDS} FROM work_items w JOIN issues i ON i.id=w.issue_id
        WHERE w.id IN ({_marks(item_ids)})""", item_ids)
    return {row["id"]: _text(row["identifier"], 32) for row in rows}


def _active_rows(db, schema):
    extra = [name for name in OPTIONAL_ITEM_COLUMNS if name in schema["work_items"]]
    columns = ", ".join(f"w.{name}" for name in ("id", "issue_id", "skill", "state", "stage", "created_at", *extra))
    rows = [dict(row) for row in db.execute(
        f"""SELECT {columns}, {_ISSUE_FIELDS} FROM work_items w JOIN issues i ON i.id=w.issue_id
            WHERE w.state IN ({_marks(ACTIVE)}) ORDER BY w.created_at, w.id""", ACTIVE)]
    for row in rows:
        for name in OPTIONAL_ITEM_COLUMNS:
            row.setdefault(name, None)
    return rows


def _audit_times(db, schema, item_ids):
    if not item_ids or not _has(schema, "audit", "item_id", "kind", "created_at"):
        return {}
    kinds = ("checkpoint", *ACTIVE)
    rows = db.execute(f"""SELECT item_id, kind, MAX(created_at) AS at FROM audit
        WHERE item_id IN ({_marks(item_ids)}) AND kind IN ({_marks(kinds)}) GROUP BY item_id, kind""",
                      (*item_ids, *kinds))
    return {(row["item_id"], row["kind"]): _time(row["at"]) for row in rows}


def _pull_requests(db, schema, issue_ids):
    if not issue_ids or not _has(schema, "published_prs", "issue_id", "url"):
        return {}
    order = "created_at, url" if "created_at" in schema["published_prs"] else "url"
    found = {}
    for row in db.execute(f"SELECT issue_id, url FROM published_prs WHERE issue_id IN ({_marks(issue_ids)}) "
                          f"ORDER BY {order}", issue_ids):
        pr = _pr(row["url"])
        if pr:
            found.setdefault(row["issue_id"], []).append(pr)
    return found


def _unity_queue(db, schema):
    if not _has(schema, "reservations", "item_id", "state", "sequence"):
        return None
    return [row["item_id"] for row in
            db.execute("SELECT item_id FROM reservations WHERE state='queued' ORDER BY sequence")]


def _display_state(row, now):
    if row["state"] == "queued":
        if row["worker_pid"] is not None:
            return "switching_repo" if row["next_root_repo"] else "launching"
        if (_time(row["retry_not_before"]) or 0) > now:
            return "retry_wait"
        return "queued"
    if row["state"] == "awaiting_resource" and row["stage"] == "waiting_for_recovery":
        return "waiting_for_recovery"
    return row["state"]


def _jobs(rows, times, prs, unity_queue, now):
    waiting = sorted((row for row in rows if _display_state(row, now) == "queued"),
                     key=lambda row: (row["priority"] if type(row["priority"]) is int else 0,
                                      _time(row["created_at"]) or 0, row["id"]))
    positions = {row["id"]: index for index, row in enumerate(waiting, 1)}
    unity = {item_id: index for index, item_id in enumerate(unity_queue, 1)}
    jobs = []
    for row in rows:
        display = _display_state(row, now)
        position = (positions.get(row["id"]) if display == "queued"
                    else unity.get(row["id"]) if row["state"] == "awaiting_resource" else None)
        jobs.append({"identifier": _text(row["identifier"], 32), "title": _text(row["title"], 200),
                     "url": _linear_url(row["url"]), "skill": _text(row["skill"], 32), "state": row["state"],
                     "display_state": display, "stage": _text(row["stage"], 120),
                     "repo": _text(row["root_repo"], 64), "created_at": _time(row["created_at"]),
                     "state_since": times.get((row["id"], row["state"])) or _time(row["created_at"]),
                     "checkpoint_at": times.get((row["id"], "checkpoint")),
                     "retry_at": _time(row["retry_not_before"]) if display == "retry_wait" else None,
                     "queue_position": position, "prs": prs.get(row["issue_id"], [])})
    return jobs


def _recent_rows(db, schema, now):
    columns = schema["work_items"]
    no_change = ("CASE WHEN json_valid(w.evidence) THEN json_extract(w.evidence,'$.no_change') IS NOT NULL ELSE 0 END"
                 if "evidence" in columns else "0")
    retried = "EXISTS (SELECT 1 FROM work_items s WHERE s.predecessor_id=w.id)" if "predecessor_id" in columns else "0"
    return [dict(row) for row in db.execute(
        f"""SELECT w.id, w.issue_id, w.skill, w.state, w.updated_at, {no_change} AS no_change,
            {retried} AS retried, {_ISSUE_FIELDS}
            FROM work_items w JOIN issues i ON i.id=w.issue_id
            WHERE w.state IN ({_marks(TERMINAL)}) AND w.updated_at >= ?
            ORDER BY w.updated_at DESC, w.id LIMIT ?""", (*TERMINAL, now - RECENT_SECONDS, RECENT_LIMIT))]


def _history(rows, prs):
    return [{"identifier": _text(row["identifier"], 32), "title": _text(row["title"], 200),
             "url": _linear_url(row["url"]), "skill": _text(row["skill"], 32),
             "outcome": "no_change" if row["state"] == "delivered" and row["no_change"] else row["state"],
             "finished_at": _time(row["updated_at"]), "retried": bool(row["retried"]),
             "prs": prs.get(row["issue_id"], [])} for row in rows]


def _slots(db, schema):
    """Internal slot records; `updated_at` and `reserved` feed attention and are dropped before output."""
    if not _has(schema, "slots", "slot_id", "kind", "state"):
        return None
    optional = [name for name in ("parked_commit", "updated_at") if name in schema["slots"]]
    holders = {}
    if _has(schema, "reservations", "resource", "mode", "item_id", "state"):
        for row in db.execute("SELECT resource, mode, item_id FROM reservations "
                              "WHERE state IN ('active','cancel_requested')"):
            holders[row["resource"]] = row
    names = _identifiers(db, list(dict.fromkeys(row["item_id"] for row in holders.values())))
    recoveries = {}
    if _has(schema, "resource_recoveries", "slot_id", "state", "attempts"):
        for row in db.execute("SELECT slot_id, state, attempts FROM resource_recoveries "
                              "WHERE state != 'recovered' ORDER BY rowid"):
            recoveries[row["slot_id"]] = {"state": _text(row["state"], 32),
                                          "attempts": row["attempts"] if type(row["attempts"]) is int else None,
                                          "max_attempts": MAX_REPAIR_ATTEMPTS}
    slots = []
    select = ", ".join(("slot_id", "kind", "state", *optional))
    for row in db.execute(f"SELECT {select} FROM slots ORDER BY slot_id"):
        row = dict(row)
        holder = holders.get(row["slot_id"])
        slots.append({"slot_id": _text(row["slot_id"], 64), "kind": _text(row["kind"], 32),
                      "state": _text(row["state"], 32), "commit": _text(row.get("parked_commit"), 7),
                      "holder": names.get(holder["item_id"]) if holder else None,
                      "mode": _text(holder["mode"], 16) if holder else None,
                      "recovery": recoveries.get(row["slot_id"]),
                      "updated_at": _time(row.get("updated_at")), "reserved": holder is not None})
    return slots


def _ledger_attention(db, schema, rows, slots, now):
    items = []

    def add(code, subject, since, count=None):
        items.append({"code": code, "subject": subject, "since": _time(since), "count": count})

    for slot in slots or ():
        if slot["state"] == "held":
            add("slot_held", slot["slot_id"], slot["updated_at"], (slot["recovery"] or {}).get("attempts"))
        elif slot["state"] in ("interactive_busy", "batch_busy") and not slot["reserved"]:
            add("slot_without_reservation", slot["slot_id"], slot["updated_at"])
    for row in rows:
        lease = _time(row["lease_expires_at"])
        if row["state"] == "running" and lease is not None and lease <= now:
            add("lease_expired", _text(row["identifier"], 32), lease)
    if _has(schema, "job_cleanup", "item_id", "done", "updated_at"):
        pending = [dict(row) for row in db.execute(
            "SELECT item_id, updated_at FROM job_cleanup WHERE done=0 AND updated_at <= ? ORDER BY updated_at",
            (now - CLEANUP_GRACE,))]
        names = _identifiers(db, [row["item_id"] for row in pending])
        for row in pending:
            add("cleanup_pending", names.get(row["item_id"]), row["updated_at"])
    if _has(schema, "issue_checks", "issue_id", "error", "failures", "checked_at"):
        for row in db.execute(f"""SELECT c.checked_at, c.failures, {_ISSUE_FIELDS} FROM issue_checks c
                JOIN issues i ON i.id=c.issue_id WHERE c.error IS NOT NULL AND c.failures >= ?
                ORDER BY c.checked_at""", (STATUS_FAILURES,)):
            add("issue_status_error", _text(row["identifier"], 32), row["checked_at"],
                row["failures"] if type(row["failures"]) is int else None)
    if _has(schema, "reservations", "item_id", "state"):
        for row in db.execute(f"""SELECT w.updated_at, {_ISSUE_FIELDS} FROM reservations r
                JOIN work_items w ON w.id=r.item_id JOIN issues i ON i.id=w.issue_id
                WHERE r.state='cancel_requested' AND w.updated_at <= ? ORDER BY w.updated_at""",
                              (now - CANCEL_GRACE,)):
            add("reservation_cancel_pending", _text(row["identifier"], 32), row["updated_at"])
    return items


def _read_ledger(db, now):
    schema = _schema(db)
    rows = _active_rows(db, schema)
    recent = _recent_rows(db, schema, now)
    issue_ids = list(dict.fromkeys([row["issue_id"] for row in rows] + [row["issue_id"] for row in recent]))
    prs = _pull_requests(db, schema, issue_ids)
    queue = _unity_queue(db, schema)
    slots = _slots(db, schema)
    counts = dict.fromkeys(ACTIVE, 0)
    for row in rows:
        counts[row["state"]] += 1
    service = {}
    if _has(schema, "webhook_events", "received_at"):
        service["agent_event_at"] = _time(db.execute("SELECT MAX(received_at) FROM webhook_events").fetchone()[0])
    if _has(schema, "issue_checks", "checked_at", "error"):
        row = db.execute("""SELECT MAX(CASE WHEN error IS NULL AND checked_at > 0 THEN checked_at END) AS ok,
            COUNT(error) AS failing FROM issue_checks""").fetchone()
        service["linear"] = {"last_ok_at": _time(row["ok"]), "failing_issues": row["failing"]}
    sections = {"counts": counts,
                "active": _jobs(rows, _audit_times(db, schema, [row["id"] for row in rows]), prs, queue or [], now),
                "slots": None if slots is None else [{key: slot[key] for key in SLOT_FIELDS} for slot in slots],
                "unity_queue": None if queue is None else len(queue),
                "recent": _history(recent, prs)}
    missing = [name for name in KNOWN_TABLES if name not in schema]
    return sections, service, _ledger_attention(db, schema, rows, slots, now), missing


def _loop_state(name, record, now):
    started, finished = record["started_at"], record["finished_at"]
    busy = started is not None and (finished is None or started > finished)
    if record["consecutive_errors"] >= LOOP_ERRORS:
        return "erroring"
    if busy and now - started > LOOP_LIMITS.get(name, max(LOOP_LIMITS.values())):
        return "stalled"
    return "busy" if busy else "idle"


def _service(beat_state, beat, health, now):
    fields = ("phase", "written_at", "started_at", "stopped_at", "revision", "dirty", "runtime")
    heartbeat = {"state": beat_state, **{key: (beat[key] if beat else None) for key in fields}}
    loops = ([{"name": name, "state": _loop_state(name, beat["loops"][name], now), **beat["loops"][name]}
              for name in LOOPS if name in beat["loops"]] if beat else [])
    return {"health": health, "heartbeat": heartbeat, "loops": loops,
            "webhooks": beat["webhooks"] if beat else None, "agent_event_at": None, "linear": None}


def _service_attention(beat_state, beat, health, now, failing_since):
    items = []

    def add(code, subject, since, count=None):
        items.append({"code": code, "subject": subject, "since": _time(since), "count": count})

    phase = beat["phase"] if beat else None
    if not health["ok"] and beat_state == "fresh" and phase == "serving":
        add("receiver_unreachable", None, failing_since)
    if health["ok"] and beat_state == "unreadable":
        add("heartbeat_unreadable", None, None)
    elif health["ok"] and beat is not None and not (beat_state == "fresh" and phase in ("serving", "starting")):
        add("heartbeat_stale", None, beat["written_at"])
    if beat is not None and beat_state == "fresh":
        for name in LOOPS:
            record = beat["loops"].get(name)
            if record is None:
                continue
            state = _loop_state(name, record, now)
            if state == "erroring":
                add("loop_erroring", name, record["error_at"], record["consecutive_errors"])
            elif state == "stalled":
                add("loop_stalled", name, record["started_at"])
        rejected = beat["webhooks"]["last_rejected_at"]
        if rejected is not None and now - rejected < REJECT_WINDOW:
            add("webhook_rejected", None, rejected, beat["webhooks"]["counts"]["rejected"])
    return items


def _verdict(ledger_ok, beat_state, beat, health, failing_since, attention):
    phase = beat["phase"] if beat else None
    if not ledger_ok:
        return "unknown", None
    if phase == "stopped" and not health["ok"]:
        return "stopped", beat["stopped_at"]
    if beat_state == "fresh" and phase == "starting":
        return "starting", beat["started_at"]
    if not health["ok"] and beat_state != "fresh":
        return "unresponsive", beat["written_at"] if beat else _time(failing_since)
    if attention:
        times = [item["since"] for item in attention if item["since"] is not None]
        return "attention", min(times) if times else None
    return "ok", None


def build_status(ledger_path, *, heartbeat, health, instance, monitor_revision, now, failing_since=None):
    """The /api/status document. `heartbeat` is heartbeat.read()'s (state, beat) and `health` is
    monitor.probe_health()'s result; `failing_since` is when the monitor first saw the current run of
    /health failures."""
    beat_state, beat = heartbeat
    document = {"schema_version": SCHEMA_VERSION, "generated_at": now, "verdict": None, "verdict_since": None,
                "instance": instance, "monitor": {"revision": monitor_revision[0], "dirty": monitor_revision[1]},
                "service": _service(beat_state, beat, health, now), "counts": dict.fromkeys(ACTIVE, 0),
                "active": [], "slots": None, "unity_queue": None, "recent": [], "attention": [],
                "ledger": {"ok": False, "error_type": None, "missing_optional": []}}
    attention = []
    try:
        with snapshot_connection(ledger_path) as db:
            sections, service, ledger_attention, missing = _read_ledger(db, now)
    except (sqlite3.Error, OSError, ValueError) as exc:
        document["ledger"]["error_type"] = type(exc).__name__
    else:
        document.update(sections)
        document["service"].update(service)
        document["ledger"].update(ok=True, missing_optional=missing)
        attention.extend(ledger_attention)
    attention.extend(_service_attention(beat_state, beat, health, now, failing_since))
    document["attention"] = attention
    document["verdict"], document["verdict_since"] = _verdict(document["ledger"]["ok"], beat_state, beat,
                                                             health, failing_since, attention)
    return document
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s tests -p 'test_monitor_view.py' -v`
Expected: all tests OK. If a ledger API call in a fixture fails, fix the fixture to use the real API. Never weaken an assertion about what must not appear, or about read-only behaviour.

- [ ] **Step 5: Commit**

```bash
git add agent/monitor_view.py tests/test_monitor_view.py
git commit -F - <<'EOF'
Build the status monitor's document from a read-only ledger

Active work, Unity slots, recent results, attention items and the verdict
come from an explicit field list; older ledgers without optional tables
still produce a document.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 6: The page (`monitor_static`)

**Files:**
- Create: `agent/monitor_static/index.html`, `agent/monitor_static/monitor.css`, `agent/monitor_static/monitor.js`
- Test: `tests/test_monitor.py` (add `PageTests`)

**Interfaces:**
- Consumes: the `/api/status` shape from Task 5, and `VERDICTS`, `DISPLAY_STATES`, `OUTCOMES`, `ATTENTION_CODES`, `LOOPS` and `Ledger.SLOT_STATES` for the label test.
- Produces: the three static files that Task 7 serves at `/`, `/monitor.css` and `/monitor.js`.

- [ ] **Step 1: Write the failing tests**

Add to the imports of `tests/test_monitor.py`: `import re`, `from agent.heartbeat import LOOPS`, `from agent.ledger import Ledger`, and `from agent.monitor_view import ATTENTION_CODES, DISPLAY_STATES, OUTCOMES, VERDICTS`. Then append:

```python
class PageTests(unittest.TestCase):
    STATIC = Path(__file__).resolve().parents[1] / "agent" / "monitor_static"

    def text(self, name):
        return (self.STATIC / name).read_text(encoding="utf-8")

    def test_the_page_loads_only_its_own_assets_and_has_no_inline_code(self):
        html = self.text("index.html")
        self.assertIn('<html lang="zh-CN">', html)
        self.assertEqual(re.findall(r"<script\b([^>]*)>(.*?)</script>", html, re.S), [(' src="/monitor.js" defer', "")])
        self.assertEqual(re.findall(r'<link\b[^>]*href="([^"]+)"', html), ["/monitor.css"])
        self.assertNotRegex(html, r"\sstyle=|\son[a-z]+=|https?://")

    def test_the_script_never_turns_data_into_markup(self):
        script = self.text("monitor.js")
        for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
                          "new Function", "setAttribute", ".style."):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, script)

    def test_every_code_the_status_view_emits_has_a_label(self):
        script = self.text("monitor.js")
        for code in (*VERDICTS, *DISPLAY_STATES, *OUTCOMES, *ATTENTION_CODES, *LOOPS, *Ledger.SLOT_STATES):
            with self.subTest(code=code):
                self.assertRegex(script, rf"\b{code}:")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s tests -p 'test_monitor.py' -k PageTests -v`
Expected: ERROR, `FileNotFoundError` for `agent/monitor_static/index.html`.

- [ ] **Step 3: Write the page**

Create `agent/monitor_static/index.html`:

```html
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>FarmBot 状态</title>
<link rel="stylesheet" href="/monitor.css">
<script src="/monitor.js" defer></script>
</head>
<body>
<header class="top">
  <div>
    <h1 id="title">FarmBot 状态</h1>
    <p class="muted" id="subtitle">正在连接监控服务…</p>
  </div>
  <div class="verdict-box">
    <span id="verdict" class="pill neutral">…</span>
    <p class="muted" id="updated"></p>
  </div>
</header>
<div id="banner" class="banner" hidden></div>
<main id="content">
  <section class="counts" id="counts"></section>
  <section class="card attention" id="attention" hidden>
    <h2>需要关注<span id="attention-count"></span></h2>
    <div class="rows" id="attention-rows"></div>
  </section>
  <section class="card"><h2>服务</h2><div class="rows" id="service-rows"></div></section>
  <section class="card"><h2>正在处理</h2><div class="rows" id="active-rows"></div></section>
  <section class="card"><h2>Unity 槽位</h2><div class="rows" id="slot-rows"></div></section>
  <section class="card"><h2>最近 7 天</h2><div class="rows" id="recent-rows"></div></section>
</main>
<footer class="muted">只读页面：不能在这里停止或重试工作，请在 Linear 中操作。原始数据：<a class="link" href="/api/status">/api/status</a></footer>
</body>
</html>
```

Create `agent/monitor_static/monitor.css`:

```css
:root {
  color-scheme: light dark;
  --bg: #f6f5f1; --card: #ffffff; --text: #1f1e1c; --muted: #6b6a66; --line: rgba(0, 0, 0, 0.1);
  --accent: #185fa5;
  --ok-bg: #eaf3de; --ok: #27500a; --warn-bg: #faeeda; --warn: #633806;
  --bad-bg: #fcebeb; --bad: #791f1f; --info-bg: #e6f1fb; --info: #0c447c;
  --neutral-bg: #f1efe8; --neutral: #444441;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #1d1d1b; --card: #262624; --text: #ecebe6; --muted: #a3a29c; --line: rgba(255, 255, 255, 0.12);
    --accent: #85b7eb;
    --ok-bg: #173404; --ok: #c0dd97; --warn-bg: #412402; --warn: #fac775;
    --bad-bg: #501313; --bad: #f7c1c1; --info-bg: #042c53; --info: #b5d4f4;
    --neutral-bg: #2c2c2a; --neutral: #d3d1c7;
  }
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
body {
  margin: 0 auto; max-width: 960px; padding: 16px; background: var(--bg); color: var(--text);
  font: 14px/1.6 -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif;
}
h1 { font-size: 20px; font-weight: 500; margin: 0; }
h2 { font-size: 13px; font-weight: 500; color: var(--muted); margin: 0 0 6px; }
p { margin: 0; }
.muted { color: var(--muted); font-size: 12px; }
.top { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 12px; }
.verdict-box { text-align: right; }
.pill { display: inline-block; padding: 2px 8px; border-radius: 6px; font-size: 12px; white-space: nowrap; }
.pill.ok { background: var(--ok-bg); color: var(--ok); }
.pill.warn { background: var(--warn-bg); color: var(--warn); }
.pill.bad { background: var(--bad-bg); color: var(--bad); }
.pill.info { background: var(--info-bg); color: var(--info); }
.pill.neutral { background: var(--neutral-bg); color: var(--neutral); }
.banner { background: var(--bad-bg); color: var(--bad); border-radius: 8px; padding: 8px 12px; margin-bottom: 12px; }
.counts { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-bottom: 12px; }
.tile { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; }
.number { font-size: 22px; font-weight: 500; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 12px 16px; margin-bottom: 12px; }
.card.attention { border-color: var(--warn); }
.row { display: grid; gap: 8px; align-items: center; padding: 8px 0; border-top: 1px solid var(--line); }
.rows > .row:first-child { border-top: 0; }
.row.four { grid-template-columns: 96px minmax(0, 1fr) auto 140px; }
.row.two { grid-template-columns: 110px minmax(0, 1fr); }
.row.pair { grid-template-columns: minmax(0, 1fr) auto; }
.title { overflow-wrap: anywhere; }
.side { text-align: right; }
.pills { display: flex; flex-wrap: wrap; gap: 6px; }
.prs { font-size: 12px; }
.empty { padding: 8px 0; }
.link { color: var(--accent); text-decoration: none; }
.link:hover { text-decoration: underline; }
.stale { opacity: 0.55; }
footer { margin: 16px 0; }
@media (max-width: 640px) {
  .counts { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .row.four { grid-template-columns: minmax(0, 1fr) auto; }
  .row.four > .main { grid-column: 1 / -1; order: 3; }
  .row.four > .side { grid-column: 1 / -1; order: 4; text-align: left; }
  .row.two { grid-template-columns: 1fr; }
}
```

Create `agent/monitor_static/monitor.js`:

```js
"use strict";
// FarmBot's read-only status page. Every value from /api/status reaches the page through textContent or a
// text node; links are made only for Linear and GitHub URLs. The JSON stays language-neutral; labels live here.
(() => {
  const POLL_MS = 5000;
  const BUSY_NOTICE_SECONDS = 5;
  const SAFE_LINK = /^https:\/\/(linear\.app|github\.com)\/\S*$/;
  const VERDICT = {
    ok: ["正常", "ok"], attention: ["需要关注", "warn"], starting: ["正在启动", "info"],
    unresponsive: ["无响应", "bad"], stopped: ["已停止", "bad"], unknown: ["未知", "neutral"],
  };
  const SKILL = {fix: "修复", chat: "对话"};
  const STATE = {
    queued: ["排队中", "neutral"], launching: ["正在启动", "info"], switching_repo: ["切换仓库", "info"],
    retry_wait: ["等待重试", "neutral"], running: ["处理中", "info"], awaiting_input: ["等待回复", "warn"],
    awaiting_resource: ["等待 Unity", "neutral"], waiting_for_recovery: ["等待 Unity 修复", "warn"],
  };
  const OUTCOME = {
    delivered: ["已交付", "ok"], no_change: ["无需改动", "ok"], blocked: ["受阻", "warn"],
    failed: ["失败", "bad"], cancelled: ["已停止", "neutral"],
  };
  const LOOP = {
    receive: "接收", schedule: "调度", pool: "Unity 池", lifecycle: "状态同步", progress: "进度汇报",
    resource_recovery: "资源恢复",
  };
  const SLOT = {
    idle_closed: ["空闲", "neutral"], idle_open: ["空闲（编辑器已打开）", "ok"], switching: ["切换中", "info"],
    interactive_busy: ["交互测试中", "info"], batch_busy: ["批量测试中", "info"], held: ["已隔离", "bad"],
  };
  const ATTENTION = {
    slot_held: (a) => `${a.subject} 已隔离，控制器正在自动修复${a.count ? `（第 ${a.count} 次）` : ""}`,
    slot_without_reservation: (a) => `${a.subject} 显示忙碌，但没有对应的占用记录`,
    lease_expired: (a) => `${a.subject || "一项工作"} 的租约已过期，worker 可能已退出`,
    cleanup_pending: (a) => `${a.subject || "一项工作"} 的清理尚未完成，已保留恢复证据`,
    issue_status_error: (a) => `${a.subject || "一个 issue"} 的 Linear 状态读取连续失败 ${a.count} 次`,
    reservation_cancel_pending: (a) => `${a.subject || "一项工作"} 的 Unity 占用正在取消，尚未完成`,
    loop_erroring: (a) => `${LOOP[a.subject] || a.subject} 循环连续出错 ${a.count} 次`,
    loop_stalled: (a) => `${LOOP[a.subject] || a.subject} 循环单次运行时间过长`,
    webhook_rejected: (a) => `最近有 webhook 被拒绝（签名、时间或身份不符；启动以来共 ${a.count} 个）`,
    receiver_unreachable: () => "接收器 /health 没有响应，但服务心跳正常",
    heartbeat_stale: () => "服务心跳已过期，但 /health 仍有响应",
    heartbeat_unreadable: () => "服务心跳文件无法读取",
  };

  let doc = null;
  let fetchedAt = 0;
  let lastGood = null;
  let failingSince = null;

  const byId = (id) => document.getElementById(id);

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function cell(className, ...parts) {
    const node = el("div", className);
    for (const part of parts) {
      if (part !== null && part !== undefined && part !== "") node.append(part);  // strings become text nodes
    }
    return node;
  }

  function row(className, ...cells) {
    const node = el("div", `row ${className}`);
    node.append(...cells);
    return node;
  }

  function pill(label, tone) {
    return el("span", `pill ${tone || "neutral"}`, label);
  }

  function link(url, text) {
    if (typeof url !== "string" || !SAFE_LINK.test(url)) return el("span", "", text);
    const anchor = el("a", "link", text);
    anchor.href = url;
    anchor.target = "_blank";
    anchor.rel = "noopener noreferrer";
    return anchor;
  }

  const hostNow = () => doc.generated_at + (Date.now() - fetchedAt) / 1000;

  function duration(seconds) {
    const s = Math.max(0, Math.round(seconds));
    if (s < 60) return `${s} 秒`;
    if (s < 3600) return `${Math.floor(s / 60)} 分钟`;
    if (s < 86400) return `${Math.floor(s / 3600)} 小时 ${Math.floor((s % 3600) / 60)} 分钟`;
    return `${Math.floor(s / 86400)} 天 ${Math.floor((s % 86400) / 3600)} 小时`;
  }

  function ago(t) {
    if (typeof t !== "number") return "";
    const seconds = hostNow() - t;
    return seconds < 5 ? "刚刚" : `${duration(seconds)}前`;
  }

  function since(t) {
    return typeof t === "number" ? duration(hostNow() - t) : "";
  }

  function clock(t) {
    if (typeof t !== "number") return "";
    const date = new Date(t * 1000);
    const time = date.toLocaleTimeString("zh-CN", {hour: "2-digit", minute: "2-digit", hour12: false});
    return date.toDateString() === new Date().toDateString()
      ? time : `${date.getMonth() + 1}月${date.getDate()}日 ${time}`;
  }

  function prLinks(prs) {
    const node = el("div", "prs");
    prs.forEach((pr, index) => {
      if (index) node.append(" · ");
      node.append(link(pr.url, `PR ${pr.label}`));
    });
    return node;
  }

  function renderHeader() {
    // A lost connection overrides the last verdict: the tab title must not keep saying 正常.
    const lost = failingSince !== null;
    const [label, tone] = lost ? ["连接中断", "bad"] : VERDICT[doc.verdict] || VERDICT.unknown;
    const from = lost ? failingSince / 1000 : doc.verdict !== "ok" ? doc.verdict_since : null;
    const bot = doc.instance.bot_name || "FarmBot";
    const beat = doc.service.heartbeat;
    document.title = `${label} · ${bot} 状态`;
    byId("title").textContent = `${bot} 状态`;
    const parts = [doc.instance.environment, doc.instance.host];
    if (beat.revision) parts.push(`版本 ${beat.revision.slice(0, 7)}${beat.dirty ? "（有未提交改动）" : ""}`);
    if (beat.state === "fresh" && beat.phase === "serving") parts.push(`已运行 ${since(beat.started_at)}`);
    byId("subtitle").textContent = parts.filter(Boolean).join(" · ");
    const verdict = byId("verdict");
    verdict.className = `pill ${tone}`;
    verdict.textContent = typeof from === "number" ? `${label}（自 ${clock(from)}）` : label;
    byId("updated").textContent = `更新于 ${ago(doc.generated_at)} · 每 5 秒刷新`;
  }

  function renderCounts(work) {
    const tiles = [["处理中", work.counts.running], ["排队", work.counts.queued],
                   ["等待回复", work.counts.awaiting_input], ["等待 Unity", work.counts.awaiting_resource]];
    byId("counts").replaceChildren(...tiles.map(([label, value]) =>
      cell("tile", el("div", "muted", label), el("div", "number", value))));
  }

  function renderAttention() {
    const items = doc.attention || [];
    byId("attention").hidden = items.length === 0;
    byId("attention-count").textContent = items.length ? ` · ${items.length}` : "";
    byId("attention-rows").replaceChildren(...items.map((item) =>
      row("pair", cell("main", (ATTENTION[item.code] || (() => item.code))(item)),
          cell("side muted", item.since ? since(item.since) : ""))));
  }

  function loopPill(loop) {
    const name = LOOP[loop.name] || loop.name;
    const busyFor = typeof loop.started_at === "number" ? hostNow() - loop.started_at : 0;
    if (loop.state === "erroring") return pill(`${name} 出错 ${loop.error_type || ""}`.trim(), "bad");
    if (loop.state === "stalled") return pill(`${name} 已运行 ${duration(busyFor)}`, "warn");
    if (loop.state === "busy" && busyFor >= BUSY_NOTICE_SECONDS) return pill(`${name} 忙碌 ${duration(busyFor)}`, "info");
    return pill(`${name} ${ago(loop.finished_at ?? loop.started_at)}`, "ok");
  }

  function renderService() {
    const service = doc.service;
    const rows = [];
    const health = service.health;
    const detail = health.ok ? `${health.latency_ms} ms` : health.error_type || (health.status ? `HTTP ${health.status}` : "");
    rows.push(row("two", cell("label muted", "接收器 /health"),
      cell("main", health.ok ? pill("正常", "ok") : pill("无响应", "bad"), " ", el("span", "muted", detail))));
    const beat = service.heartbeat;
    const stopped = beat.phase === "stopped" ? `已停止（${clock(beat.stopped_at)}）` : null;
    const beatText = {
      fresh: stopped || `${ago(beat.written_at)}写入`,
      stale: stopped || `已过期（最后 ${clock(beat.written_at)}）`,
      missing: "此版本未提供", unreadable: "无法读取",
    }[beat.state] || "";
    rows.push(row("two", cell("label muted", "服务心跳"), cell("main", beatText)));
    if (service.loops.length) {
      const pills = el("div", "pills");
      pills.append(...service.loops.map(loopPill));
      rows.push(row("two", cell("label muted", "后台循环"), cell("main", pills)));
    } else if (beat.state === "missing") {
      rows.push(row("two", cell("label muted", "后台循环"), cell("main muted", "此版本未提供")));
    }
    const hooks = service.webhooks;
    const bits = [];
    if (hooks) bits.push(hooks.last_at ? `最近收到 ${ago(hooks.last_at)}${hooks.last_type ? `（${hooks.last_type}）` : ""}` : "启动以来尚未收到");
    if (service.agent_event_at) bits.push(`会话事件 ${ago(service.agent_event_at)}`);
    if (hooks) bits.push(`拒绝 ${hooks.counts.rejected}`);
    if (bits.length) rows.push(row("two", cell("label muted", "Webhook"), cell("main", bits.join(" · "))));
    if (service.linear) {
      const read = service.linear.last_ok_at ? `状态读取成功 ${ago(service.linear.last_ok_at)}` : "尚无成功的状态读取";
      rows.push(row("two", cell("label muted", "Linear"), cell("main", `${read} · 失败 ${service.linear.failing_issues}`)));
    }
    byId("service-rows").replaceChildren(...rows);
  }

  function renderActive(work) {
    const target = byId("active-rows");
    if (!work.active.length) return target.replaceChildren(el("div", "empty muted", "目前没有进行中的工作"));
    target.replaceChildren(...work.active.map((job) => {
      const [label, tone] = STATE[job.display_state] || [job.display_state, "neutral"];
      const meta = [SKILL[job.skill] || job.skill, job.repo, job.stage && `阶段 ${job.stage}`].filter(Boolean).join(" · ");
      const main = cell("main", el("div", "title", job.title || ""), el("div", "muted", meta));
      if (job.prs.length) main.append(prLinks(job.prs));
      let state = label;
      if (job.queue_position) state += ` · 第 ${job.queue_position} 位`;
      if (job.retry_at) state += ` · ${clock(job.retry_at)}`;
      const timing = cell("side muted", `${job.state === "running" ? "已处理" : "已等待"} ${since(job.state_since)}`);
      if (job.checkpoint_at) timing.append(el("div", "", `检查点 ${ago(job.checkpoint_at)}`));
      return row("four", cell("ident", link(job.url, job.identifier || "?")), main,
                 cell("state", pill(state, tone)), timing);
    }));
  }

  function renderSlots(work) {
    const target = byId("slot-rows");
    if (work.slots === null) return target.replaceChildren(el("div", "empty muted", "此版本未提供"));
    if (!work.slots.length) return target.replaceChildren(el("div", "empty muted", "未配置 Unity 槽位"));
    const rows = work.slots.map((slot) => {
      const [label, tone] = SLOT[slot.state] || [slot.state, "neutral"];
      const detail = [slot.mode, slot.commit && `提交 ${slot.commit}`].filter(Boolean).join(" · ");
      const side = slot.recovery
        ? cell("side muted", `修复中（第 ${slot.recovery.attempts}/${slot.recovery.max_attempts} 次）`)
        : cell("side", slot.holder || "");
      return row("four", cell("ident", slot.slot_id), cell("main muted", detail), cell("state", pill(label, tone)), side);
    });
    if (work.unity_queue) rows.push(row("two", cell("label muted", "排队"), cell("main", `${work.unity_queue} 项工作在等待 Unity 槽位`)));
    target.replaceChildren(...rows);
  }

  function renderRecent(work) {
    const target = byId("recent-rows");
    if (!work.recent.length) return target.replaceChildren(el("div", "empty muted", "最近 7 天没有完成的工作"));
    target.replaceChildren(...work.recent.map((job) => {
      const [label, tone] = OUTCOME[job.outcome] || [job.outcome, "neutral"];
      const main = cell("main", el("div", "title", job.title || ""), el("div", "muted", SKILL[job.skill] || job.skill || ""));
      if (job.prs.length) main.append(prLinks(job.prs));
      const state = cell("state", pill(label, tone));
      if (job.retried) state.append(el("div", "muted", "已重试"));
      return row("four", cell("ident", link(job.url, job.identifier || "?")), main, state,
                 cell("side muted", clock(job.finished_at)));
    }));
  }

  function renderBanner() {
    const messages = [];
    if (failingSince !== null) messages.push(`与监控的连接已中断（自 ${clock(failingSince / 1000)}），正在重试。`);
    if (!doc) {
      messages.push("正在连接监控服务。");
    } else if (!doc.ledger.ok) {
      const error = doc.ledger.error_type || "未知错误";
      messages.push(lastGood ? `账本暂时无法读取（${error}），工作数据停留在 ${clock(lastGood.generated_at)}。`
                             : `账本暂时无法读取（${error}）。`);
    }
    const banner = byId("banner");
    banner.hidden = messages.length === 0;
    banner.textContent = messages.join(" ");
    byId("content").classList.toggle("stale", failingSince !== null || Boolean(doc && !doc.ledger.ok));
  }

  function render() {
    if (doc) {
      const work = doc.ledger.ok ? doc : lastGood || doc;
      renderHeader();
      renderCounts(work);
      renderAttention();
      renderService();
      renderActive(work);
      renderSlots(work);
      renderRecent(work);
    }
    renderBanner();
  }

  async function poll() {
    try {
      const response = await fetch("/api/status", {cache: "no-store"});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      doc = await response.json();
      fetchedAt = Date.now();
      failingSince = null;
      if (doc.ledger.ok) lastGood = doc;
    } catch (error) {
      if (failingSince === null) failingSince = Date.now();
    }
    render();
    setTimeout(poll, POLL_MS);
  }

  setInterval(render, 1000);
  poll();
})();
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s tests -p 'test_monitor.py' -k PageTests -v`
Expected: 3 tests OK. The page is checked visually in Task 7, once a server can serve it.

- [ ] **Step 5: Commit**

```bash
git add agent/monitor_static tests/test_monitor.py
git commit -F - <<'EOF'
Add the status monitor's page

A Chinese, read-only page that polls /api/status every five seconds and
renders every value as text; it keeps the last good data visible when the
monitor or the ledger cannot be read.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 7: The monitor server and command

**Files:**
- Create: `agent/monitor.py`
- Modify: `agent/receiver.py` (move `ExclusiveServer` out of `make_server` to module level)
- Modify: `agent/service.py` (`monitor` subcommand)
- Test: `tests/test_monitor.py` (add `HostCheckTests`, `ProbeHealthTests`, `MonitorServerTests`, `MonitorRunTests`)

**Interfaces:**
- Consumes: `monitor_settings`, `Paths`, `ROOT` and `load_config` (Task 2); `read` and `source_revision` (Task 3); `build_status` (Task 5); the static files (Task 6); `agent.environment.check_ownership` (existing, read-only).
- Produces:
  - `agent.receiver.ExclusiveServer`.
  - `agent.monitor.probe_health(port, *, timeout=2.0, now=None) -> {"ok", "status", "latency_ms", "error_type", "checked_at"}`.
  - `agent.monitor.allowed_host(header, hostnames) -> True | False | None`.
  - `agent.monitor.make_monitor_server(config, *, port=None, clock=time.time)`.
  - `agent.monitor.run(config_path=None) -> int`, and `agent.monitor.SNAPSHOT_TTL`.
  - `python3 -m agent.service monitor [--config PATH]`.

- [ ] **Step 1: Write the failing tests**

Add to the imports of `tests/test_monitor.py`: `import contextlib`, `import http.client`, `import io`, `import os`, `import socket`, `import threading`, `import time`, `from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer`, `from unittest.mock import patch`, `from agent import monitor`, `from agent.monitor import allowed_host, make_monitor_server, probe_health, run` and `from agent.service import main`. Then append:

```python
def fake_receiver(status=200, delay=0.0):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            time.sleep(delay)
            body = b'{"status": "FarmBot ready"}'
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def closed_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class HostCheckTests(unittest.TestCase):
    def test_ip_literals_localhost_and_configured_names_are_allowed(self):
        for header in ("127.0.0.1:8780", "192.0.2.10", "LOCALHOST:8780", "localhost.", "[::1]:8780",
                       "farmbot-host.local:8780"):
            with self.subTest(header=header):
                self.assertTrue(allowed_host(header, ("farmbot-host.local",)))
        for header in ("evil.example", "127.0.0.1.nip.io:8780", "farmbot-host.local.evil.example", "[::1"):
            with self.subTest(header=header):
                self.assertFalse(allowed_host(header, ("farmbot-host.local",)))
        self.assertIsNone(allowed_host(None, ()))
        self.assertIsNone(allowed_host(" ", ()))


class ProbeHealthTests(unittest.TestCase):
    def serve(self, **kwargs):
        server = fake_receiver(**kwargs)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def test_only_a_200_counts_as_answering(self):
        self.assertTrue(probe_health(self.serve())["ok"])
        failing = probe_health(self.serve(status=500))
        self.assertEqual((failing["ok"], failing["status"]), (False, 500))
        refused = probe_health(closed_port())
        self.assertEqual((refused["ok"], refused["status"]), (False, None))
        # Windows retries a refused loopback connect and can reach the timeout first.
        self.assertIn(refused["error_type"], ("ConnectionRefusedError", "TimeoutError"))

    def test_a_hung_receiver_times_out(self):
        slow = probe_health(self.serve(delay=1.0), timeout=0.2)
        self.assertEqual((slow["ok"], slow["error_type"]), (False, "TimeoutError"))
        self.assertLess(slow["latency_ms"], 1000)

    def test_proxy_settings_cannot_intercept_the_probe(self):
        port = self.serve()
        proxy = f"http://127.0.0.1:{closed_port()}"
        with patch.dict(os.environ, {"http_proxy": proxy, "HTTP_PROXY": proxy, "no_proxy": "", "NO_PROXY": ""}):
            self.assertTrue(probe_health(port)["ok"])


class MonitorServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "state 状态"
        self.now = [1_000_000.0]
        self.config = config(local_root=self.root, port=closed_port(), monitor={"hostnames": ["farmbot-host.local"]})
        Ledger(Paths(self.config).ledger).close()
        self.server = make_monitor_server(self.config, port=0, clock=lambda: self.now[0])
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method="GET", path="/", host="127.0.0.1"):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        self.addCleanup(connection.close)
        connection.putrequest(method, path, skip_host=True)
        if host is not None:
            connection.putheader("Host", host)
        connection.endheaders()
        response = connection.getresponse()
        return response, response.read()

    def test_the_page_and_its_assets_are_served_with_security_headers(self):
        for path, kind in (("/", "text/html"), ("/monitor.js", "text/javascript"), ("/monitor.css", "text/css")):
            with self.subTest(path=path):
                response, body = self.request(path=path)
                self.assertEqual(response.status, 200)
                self.assertTrue(response.getheader("Content-Type").startswith(kind))
                policy = response.getheader("Content-Security-Policy")
                self.assertIn("script-src 'self'", policy)
                self.assertIn("frame-ancestors 'none'", policy)
                for name, value in (("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"),
                                    ("X-Frame-Options", "DENY"), ("Cache-Control", "no-store")):
                    self.assertEqual(response.getheader(name), value)
                self.assertGreater(len(body), 0)

    def test_status_json_reports_the_instance_and_an_unresponsive_service(self):
        response, body = self.request(path="/api/status")
        document = json.loads(body)
        self.assertEqual(response.getheader("Content-Type"), "application/json; charset=utf-8")
        self.assertEqual(document["instance"]["bot_name"], "FarmBot")
        self.assertEqual((document["verdict"], document["verdict_since"]), ("unresponsive", 1_000_000.0))

    def test_head_matches_get_without_a_body(self):
        response, body = self.request("HEAD", "/")
        self.assertEqual((response.status, body), (200, b""))
        self.assertGreater(int(response.getheader("Content-Length")), 0)

    def test_unknown_paths_methods_and_hosts_are_refused(self):
        self.assertEqual(self.request(path="/api/status/../../config.json")[0].status, 404)
        self.assertEqual(self.request(path="/.git/config")[0].status, 404)
        for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS"):
            with self.subTest(method=method):
                response, _ = self.request(method, "/api/status")
                self.assertEqual((response.status, response.getheader("Allow")), (405, "GET, HEAD"))
        self.assertEqual(self.request(host="evil.example")[0].status, 421)
        self.assertEqual(self.request(host="farmbot-host.local:8780")[0].status, 200)
        self.assertEqual(self.request(host=None)[0].status, 400)

    def test_the_status_is_built_at_most_once_per_snapshot_interval(self):
        calls = []
        real = monitor.build_status

        def counting(*args, **kwargs):
            calls.append(kwargs["now"])
            return real(*args, **kwargs)

        with patch("agent.monitor.build_status", side_effect=counting):
            threads = [threading.Thread(target=self.request, kwargs={"path": "/api/status"}) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(20)
            self.assertEqual(len(calls), 1)
            self.now[0] += monitor.SNAPSHOT_TTL + 0.5
            self.request(path="/api/status")
        self.assertEqual(len(calls), 2)

    def test_unresponsive_stays_anchored_to_the_first_observed_failure(self):
        first = json.loads(self.request(path="/api/status")[1])
        self.now[0] += 10
        later = json.loads(self.request(path="/api/status")[1])
        self.assertEqual((first["verdict_since"], later["verdict_since"], later["generated_at"]),
                         (1_000_000.0, 1_000_000.0, 1_000_010.0))

    def test_serving_status_never_modifies_the_ledger_or_the_state_root(self):
        ledger = Paths(self.config).ledger
        before = (ledger.stat().st_mtime_ns, ledger.read_bytes())
        self.request(path="/api/status")
        self.assertEqual((ledger.stat().st_mtime_ns, ledger.read_bytes()), before)
        self.assertFalse(ledger.with_name(ledger.name + "-journal").exists())
        self.assertFalse(Paths(self.config).heartbeat.exists())
        self.assertFalse((self.root / ".controller.lock").exists())


class MonitorRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write_config(self, **changes):
        path = self.root / "config 配置.json"
        path.write_text(json.dumps({"client_id": "c", "client_secret": "s", "webhook_secret": "w", "port": 8765,
                                    "local_root": str(self.root / "state")} | changes), encoding="utf-8")
        return path

    def run_monitor(self, path):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = run(path)
        return code, [json.loads(line) for line in out.getvalue().splitlines()]

    def test_refusals_exit_one_with_one_json_line(self):
        code, lines = self.run_monitor(self.write_config(monitor={"port": 8765}))
        self.assertEqual((code, lines[0]["event"], lines[0]["error"]), (1, "monitor_failed", "ValueError"))
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen()
            code, lines = self.run_monitor(self.write_config(monitor={"port": busy.getsockname()[1]}))
        self.assertEqual((code, lines[0]["event"]), (1, "monitor_failed"))
        (self.root / "state").mkdir()
        (self.root / "state" / "environment.json").write_text(json.dumps({"version": 1, "instance_id": "someone-else"}),
                                                              encoding="utf-8")
        code, lines = self.run_monitor(self.write_config(environment="development", instance_id="dev-mac",
                                                         expected_bot_name="TestBot", expected_app_user_id="app",
                                                         expected_organization_id="org"))
        self.assertEqual((code, lines[0]["event"], lines[0]["error"]), (1, "monitor_failed", "RuntimeError"))

    def test_the_service_cli_dispatches_monitor(self):
        with patch("agent.monitor.run", return_value=0) as monitor_run:
            self.assertEqual(main(["monitor", "--config", "selected.json"]), 0)
        monitor_run.assert_called_once_with("selected.json")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s tests -p 'test_monitor.py' -v`
Expected: ERROR at import: `ModuleNotFoundError: No module named 'agent.monitor'`.

- [ ] **Step 3: Write the implementation**

In `agent/receiver.py`, move the server class out of `make_server` to module level, above `make_server`:

```python
class ExclusiveServer(ThreadingHTTPServer):
    """No port sharing on Windows and no reverse-DNS lookup at bind; the receiver and the status monitor use it.

    HTTPServer adds a reverse-DNS lookup after binding; a slow host resolver must not gate startup."""
    allow_reuse_address = os.name != "nt"
    daemon_threads = True

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address
```

Delete the nested `class ExclusiveServer` from `make_server`. Its creation line stays `server = ExclusiveServer(("127.0.0.1", port), Handler)`. Remove the now-redundant `server.daemon_threads = True`.

Create `agent/monitor.py`:

```python
"""The office status monitor: a read-only page about one FarmBot instance (docs/operating-contract.md).

It runs beside `serve` on the same host, never inside it, so it can still report a service that has stopped
or wedged. It writes nothing, signals nothing, never takes the controller lock, and makes no request but the
receiver's /health on loopback.
"""
import http.client
import ipaddress
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from .config import Paths, ROOT, load_config, monitor_settings
from .environment import check_ownership
from .heartbeat import read as read_heartbeat, source_revision
from .monitor_view import build_status
from .receiver import ExclusiveServer

STATIC = Path(__file__).resolve().parent / "monitor_static"
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/monitor.js": ("monitor.js", "text/javascript; charset=utf-8"),
          "/monitor.css": ("monitor.css", "text/css; charset=utf-8")}
SNAPSHOT_TTL = 2.0
HEALTH_TIMEOUT = 2.0
REQUEST_TIMEOUT = 5
HEADERS = (("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"),
           ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"),
           ("X-Frame-Options", "DENY"), ("Cache-Control", "no-store"))
# An empty ProxyHandler: a desktop proxy's environment variables must not carry a loopback probe elsewhere.
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def probe_health(port, *, timeout=HEALTH_TIMEOUT, now=None):
    """GET the receiver's /health on loopback. Only a 200 counts as answering, and that proves no more."""
    started = time.monotonic()
    result = {"ok": False, "status": None, "latency_ms": None, "error_type": None,
              "checked_at": time.time() if now is None else now}
    try:
        with _DIRECT.open(f"http://127.0.0.1:{port}/health", timeout=timeout) as response:
            result["status"] = response.status
    except urllib.error.HTTPError as exc:
        result["status"] = exc.code
        exc.close()
    except (OSError, http.client.HTTPException) as exc:
        reason = getattr(exc, "reason", None)
        result["error_type"] = type(reason if isinstance(reason, BaseException) else exc).__name__
    result["latency_ms"] = round((time.monotonic() - started) * 1000)
    result["ok"] = result["status"] == 200
    return result


def allowed_host(header, hostnames):
    """True for an IP literal, localhost or a configured name; False otherwise; None when there is no Host.

    DNS rebinding needs a hostname the attacker controls, so refusing unknown names is the whole defense."""
    if not header or not header.strip():
        return None
    host = header.strip().lower()
    if host.startswith("["):
        end = host.find("]")
        if end < 0:
            return False
        name = host[1:end]
    else:
        name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    name = name.rstrip(".")
    if name == "localhost" or name in hostnames:
        return True
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return False
    return True


def make_monitor_server(config, *, port=None, clock=time.time):
    """A server for this config's monitor listener; `port` overrides it (tests pass 0)."""
    settings = monitor_settings(config)
    paths = Paths(config)
    assets = {route: ((STATIC / name).read_bytes(), kind) for route, (name, kind) in ASSETS.items()}
    instance = {"environment": config.environment, "instance_id": config.instance_id,
                "bot_name": config.expected_bot_name[:64], "host": str(config.host)[:64]}
    revision = source_revision(ROOT)
    lock = threading.Lock()
    cache = {"at": None, "body": None, "failing_since": None}

    def status_body():
        # Serialized: however many teammates poll, the ledger is read at most once per SNAPSHOT_TTL.
        with lock:
            now = clock()
            if cache["body"] is not None and 0 <= now - cache["at"] < SNAPSHOT_TTL:
                return cache["body"]
            health = probe_health(config.port, now=now)
            if health["ok"]:
                cache["failing_since"] = None
            elif cache["failing_since"] is None:
                cache["failing_since"] = now
            document = build_status(paths.ledger, heartbeat=read_heartbeat(paths.heartbeat, now=now),
                                    health=health, instance=instance, monitor_revision=revision, now=now,
                                    failing_since=cache["failing_since"])
            cache["at"] = now
            cache["body"] = json.dumps(document, ensure_ascii=False, allow_nan=False).encode("utf-8")
            return cache["body"]

    class Handler(BaseHTTPRequestHandler):
        timeout = REQUEST_TIMEOUT
        server_version = "FarmBotMonitor"
        sys_version = ""

        def log_message(self, *_args):
            pass

        def send(self, status, body, kind="text/plain; charset=utf-8", extra=()):
            self.send_response(status)
            for name, value in (*HEADERS, *extra):
                self.send_header(name, value)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self):
            allowed = allowed_host(self.headers.get("Host"), settings["hostnames"])
            if allowed is None:
                return self.send(400, b"missing host\n")
            if not allowed:
                return self.send(421, b"unknown host\n")
            path = self.path.split("?", 1)[0]
            if path == "/api/status":
                return self.send(200, status_body(), "application/json; charset=utf-8")
            if path in assets:
                body, kind = assets[path]
                return self.send(200, body, kind)
            return self.send(404, b"not found\n")

        do_HEAD = do_GET

        def refuse(self):
            self.send(405, b"method not allowed\n", extra=(("Allow", "GET, HEAD"),))

        do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_TRACE = do_CONNECT = refuse

    return ExclusiveServer((settings["bind"], settings["port"] if port is None else port), Handler)


def run(config_path=None):
    """The `monitor` command: exit 1 with one JSON line when it cannot start, else serve until interrupted."""
    try:
        config = load_config(config_path, secure_permissions=False)
        check_ownership(config)
        server = make_monitor_server(config)
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        print(json.dumps({"event": "monitor_failed", "error": type(exc).__name__, "detail": str(exc)[:300]},
                         ensure_ascii=False), flush=True)
        return 1
    settings = monitor_settings(config)
    print(json.dumps({"event": "monitor_ready", "listen": f"http://{settings['bind']}:{server.server_address[1]}",
                      "instance": config.instance_id}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
```

In `agent/service.py` `main()`, add `"monitor"` to the end of the `command` choices list, and dispatch it immediately after `args = parser.parse_args(argv)`:

```python
    if args.command == "monitor":
        from .monitor import run
        return run(args.config)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s tests -p 'test_monitor.py' -v`
Expected: all tests OK.
Run: `python3 -m unittest discover -s tests -p 'test_receiver.py' -v` and `python3 -m unittest discover -s tests -p 'test_service.py' -v`
Expected: all pass, including `test_loopback_listener_starts_and_serves_health_without_reverse_dns`.

- [ ] **Step 5: Check the page by eye**

This is a manual check. Do not commit the script. Save it outside the repository, for example as `$TMPDIR/farmbot-monitor-demo.py`:

```python
"""Serve the status page for a throwaway state root with sample work. Manual check only; never commit."""
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agent.config import Config, Paths
from agent.heartbeat import LOOPS, Heartbeat
from agent.ledger import Ledger
from agent.monitor import make_monitor_server

NOW = time.time()
PIN = {"repository": "Farm-Client", "requested_ref": "main", "commit_sha": "a" * 40,
       "server_environment": "公共测试服", "selected_at": "2026-09-24T00:00:00+00:00"}


def issue(n, title):
    return {"id": f"10000000-0000-4000-8000-{n:012d}", "identifier": f"FARM-{n}",
            "team_id": "20000000-0000-4000-8000-000000000001", "url": f"https://linear.app/example/issue/FARM-{n}",
            "branch_name": f"farmbot/farm-{n}", "title": title, "description": "", "status": "Todo",
            "status_type": "unstarted", "labels": ["Bug"], "priority": 2, "archived": False, "delegate_id": None,
            "attachments": [], "comments": [], "detail_complete": True, "comments_complete": True}


class Health(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


root = Path(tempfile.mkdtemp(prefix="farmbot monitor demo "))
health = ThreadingHTTPServer(("127.0.0.1", 0), Health)
threading.Thread(target=health.serve_forever, daemon=True).start()
config = Config("c", "s", "w", local_root=root, port=health.server_address[1], host="demo-host", monitor={"port": 8799})
ledger = Ledger(Paths(config).ledger, clock=lambda: NOW - 1800, lease_seconds=7200)
titles = ["背包界面在小屏上被裁切", "好友列表偶尔显示为空", "种子商店价格显示错误", "宠物喂食动画卡住", "任务奖励重复发放"]
items = []
for n, title in enumerate(titles, 1):
    ledger.observe_issue(issue(n, title))
    ledger.ensure_session(f"session-{n}", issue(n, title)["id"], delegation=True)
    items.append(ledger.create_work_item(issue_id=issue(n, title)["id"], session_id=f"session-{n}",
                                         skill="chat" if n == 2 else "fix", target=PIN))
ledger.set_worker(items[0]["id"], 111, "demo-host")
ledger.claim(items[0]["id"], worker_id="demo")
ledger.connection.execute("UPDATE work_items SET stage='verify', root_repo='farmgui' WHERE id=?", (items[0]["id"],))
ledger.set_worker(items[1]["id"], 222, "demo-host")
token = ledger.claim(items[1]["id"], worker_id="demo")["token"]
ledger.await_input(items[1]["id"], token, "需要确认的问题")
for item, state in ((items[3], "delivered"), (items[4], "failed")):
    ledger.connection.execute("UPDATE work_items SET state=?, updated_at=? WHERE id=?", (state, NOW - 3600, item["id"]))
ledger.connection.execute("INSERT INTO published_prs(issue_id,url,generation,created_at) VALUES(?,?,0,?)",
                          (items[3]["issue_id"], "https://github.com/example/farmgui/pull/509", NOW - 3600))
ledger.ensure_slot("unity_slot:1", kind="unity_slot", host="demo-host", folder=str(root / "editors" / "slot-1"))
ledger.close()
beat = Heartbeat(runtime="codex", revision=("a3f9f77c1d2e", False))
beat.set_phase("serving")


def keep_beating():
    while True:
        for name in LOOPS:
            beat.loop_started(name)
            beat.loop_finished(name)
        beat.write(Paths(config).heartbeat)
        time.sleep(5)


threading.Thread(target=keep_beating, daemon=True).start()
server = make_monitor_server(config)
print(f"http://127.0.0.1:{server.server_address[1]}/   state root: {root}", flush=True)
server.serve_forever()
```

Run it from the repository root: `PYTHONPATH=. python3 "$TMPDIR/farmbot-monitor-demo.py"`. Open the printed URL in a browser and check each of these:
- The verdict reads 正常. The counts show 1 处理中, 1 排队, 1 等待回复 and 0 等待 Unity.
- 正在处理 lists FARM-1 to FARM-3. 最近 7 天 shows FARM-4 with `PR farmgui#509`, and FARM-5 as 失败.
- The layout holds in the light theme, in the dark theme, and at 375 px width.
- The browser console shows no CSP violations.

Stop the script with Ctrl-C. Delete the printed state root.

- [ ] **Step 6: Commit**

```bash
git add agent/monitor.py agent/receiver.py agent/service.py tests/test_monitor.py
git commit -F - <<'EOF'
Serve the status monitor as its own read-only command

python3 -m agent.service monitor answers only GET and HEAD for its page and
/api/status, checks the Host header against IP literals and configured
names, builds the document at most every two seconds, and refuses to start
on a bad config, a foreign state root or a busy port.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 8: launchd agent for the monitor

**Files:**
- Modify: `agent/deploy.py` (constant, `labels`, `install`)
- Test: `tests/test_deploy.py`

**Interfaces:**
- Consumes: `Config.monitor` from Task 2.
- Produces: `agent.deploy.MONITOR_AGENT == "com.kuaiwa.farmbot.monitor"`, a `"monitor"` key in `labels(config)`, and an `install()` that writes that agent only when `config.monitor` is non-empty. `AGENTS` stays `{"serve", "tunnel"}`, because every install writes those two.

- [ ] **Step 1: Write the failing test**

In `tests/test_deploy.py`, change the import to `from agent.deploy import AGENTS, MONITOR_AGENT, install, missing_tools, plist, tunnel_arguments`, and add to `DeployTests`:

```python
    def test_the_monitor_agent_is_written_only_for_a_config_with_a_monitor_block(self):
        target = self.root / "LaunchAgents"
        self.assertEqual(sorted(install(self.config, target)), sorted(AGENTS.values()))
        configured = replace(self.config, monitor={"bind": "0.0.0.0", "port": 8780})
        configured.source_path = self.root / "profile 配置.json"
        written = install(configured, target, python="/usr/bin/python3")
        job = plistlib.loads(written[MONITOR_AGENT].read_bytes())
        self.assertEqual(job["ProgramArguments"], ["/usr/bin/python3", "-u", "-m", "agent.service", "monitor",
                                                   "--config", str(configured.source_path.resolve())])
        self.assertTrue(job["RunAtLoad"] and job["KeepAlive"])
        profile = replace(configured, environment="development", instance_id="mac-dev",
                          local_root=self.root / "dev", expected_app_user_id="app", expected_organization_id="org")
        self.assertIn("com.kuaiwa.farmbot.development.mac-dev.monitor", install(profile, target))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest discover -s tests -p 'test_deploy.py' -v`
Expected: ERROR, `ImportError: cannot import name 'MONITOR_AGENT'`.

- [ ] **Step 3: Write the implementation**

In `agent/deploy.py`, below `AGENTS`, add:

```python
# Written only when the host config has a monitor block, so it is not part of AGENTS, which every install writes.
MONITOR_AGENT = "com.kuaiwa.farmbot.monitor"
```

Change `labels`:

```python
def labels(config):
    """Keep legacy installations stable and separate explicitly named profiles."""
    if config.environment == "legacy":
        return {**AGENTS, "monitor": MONITOR_AGENT}
    prefix = f"com.kuaiwa.farmbot.{config.environment}.{config.instance_id}"
    return {kind: f"{prefix}.{kind}" for kind in (*AGENTS, "monitor")}
```

In `install`, replace the block from `serve = [python, "-u", "-m", "agent.service", "serve"]` through the `jobs = {...}` line with:

```python
    config_path = config_path or config.source_path
    selected = ["--config", str(Path(config_path).expanduser().resolve())] if config_path else []
    names = labels(config)
    jobs = {names["serve"]: [python, "-u", "-m", "agent.service", "serve", *selected],
            names["tunnel"]: tunnel_arguments(config, cloudflared)}
    if config.monitor:
        jobs[names["monitor"]] = [python, "-u", "-m", "agent.service", "monitor", *selected]
```

Keep the existing comment about naming the config explicitly above `config_path = ...`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s tests -p 'test_deploy.py' -v`
Expected: all tests OK. The existing "eight plists" test is unchanged because its configs have no `monitor` block.

- [ ] **Step 5: Commit**

```bash
git add agent/deploy.py tests/test_deploy.py
git commit -F - <<'EOF'
Install a launchd agent for the status monitor when configured

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 9: Documentation

**Files:**
- Modify: `README.md`, `docs/operating-contract.md`, `AGENTS.md`, `docs/development-workflow.md`, `config/development.example.json`

**Interfaces:**
- Consumes: the behaviour built in Tasks 1–8.
- Produces: operator documentation. No code.

- [ ] **Step 1: Add the README section**

Insert this section after "## AI/operator diagnostics" and before "## Issue closure and cancelled work":

````markdown
## Office status monitor

A read-only web page for the team, served by a separate process beside `serve` on the same host:

```bash
python3 -m agent.service monitor --config /absolute/path/to/config.json
```

It listens on `127.0.0.1:8780` unless the host config's `monitor` block says otherwise, so exposing it on
the office network is explicit:

```json
"monitor": {"bind": "0.0.0.0", "port": 8780, "hostnames": ["farmbot-host.local"]}
```

`bind` is an IPv4 address. `port` must differ from the receiver's `port`, so the Cloudflare tunnel, which
forwards only the receiver, never carries the monitor. Requests must name the host by IP address,
`localhost` or one of `hostnames`. `serve` and workers ignore this block: after changing it, restart only
the monitor. Teammates open `http://<host>:8780/`.

The page is in Chinese, refreshes every five seconds and cannot stop, retry or change anything. It shows
the service verdict, work in progress with Linear and PR links, Unity slots, attention items and the last
seven days of results. It never shows issue descriptions, comments, checkpoints, questions, logs, paths,
PIDs, tokens or raw errors; use `doctor` on the host for detail. There is no login: anyone who can reach
the port can read issue identifiers, titles and job states.

The monitor reads the ledger read-only, probes the receiver's `/health` on loopback and reads
`<local_root>/service-heartbeat.json`. `serve` rewrites that file every five seconds with its phase, loop
timings, revision and webhook counts, so the page still reports a stopped or wedged service. A service
revision that writes no heartbeat shows its loop rows as 此版本未提供. `/health` proves only that the
receiver answers; the page is not proof of Linear, tunnel or Unity health beyond the signals it lists.

`install-launchd` adds a third agent for the monitor when the config has a `monitor` block. On Windows,
run the monitor once in a console to see its `monitor_ready` line. Then, as an administrator, allow its
port on the office network and start it at logon as the same user as `serve`:

```powershell
New-NetFirewallRule -DisplayName "FarmBot monitor" -Direction Inbound -Protocol TCP -LocalPort 8780 `
  -Profile Private -RemoteAddress LocalSubnet -Action Allow
$action = New-ScheduledTaskAction -Execute "C:\Path\To\python.exe" `
  -Argument '-u -m agent.service monitor --config "C:\FarmBot\config.json"' -WorkingDirectory "C:\FarmBot\monitor"
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 `
  -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "FarmBot monitor" -Action $action `
  -Trigger (New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME") -Settings $settings
```

Use the configured Python and the directory holding the revision that runs the monitor. The firewall
rule needs the office network classified as Private. Check restart-on-failure on the host once. When the
monitor runs from a different directory than `serve`, the config needs an absolute `local_root`, because
the default is the checkout running the command. `doctor --config` run from the monitor's directory must
report the service's actual ledger path.
````

- [ ] **Step 2: Add the operating contract section**

Append to `docs/operating-contract.md`:

```markdown
## Status monitor

`python3 -m agent.service monitor` serves a read-only Chinese status page and `/api/status` JSON for one
instance, as a separate process on the host running `serve`. It binds the config's `monitor.bind`
(default `127.0.0.1`) and `monitor.port` (default 8780, never the receiver's port). It answers only
`GET` and `HEAD`, and only when the `Host` header is an IP literal, `localhost` or a configured
`monitor.hostnames` entry. There is no authentication. Exposing it beyond loopback is an explicit
configuration choice, and anyone who can reach it can read issue identifiers, titles, job and slot
states and PR links.

It opens the ledger with SQLite `mode=ro` and `query_only`, never constructs `Ledger` and never takes the
controller lock. It writes nothing, signals and inspects no processes, and makes no request other than
`GET http://127.0.0.1:<port>/health` with proxies disabled. The JSON is an allowlist. It contains no
descriptions, comments, checkpoints, questions, inbox or worker messages, evidence prose, logs, paths,
PIDs, tokens, config values or raw stored errors. Older ledgers without optional tables are read
without migration.

`serve` writes `<local_root>/service-heartbeat.json` by replacement:
- when it starts, with phase `starting`, before slot preparation;
- every five seconds, with phase `serving` once its loops run;
- on clean shutdown, with phase `stopped`.

The heartbeat records each loop's work timing and consecutive errors, the revision, and in-memory
webhook outcome counts. A failed write is skipped and never affects serving. The monitor reads the file
as untrusted data (a regular file, at most 64 KiB, validated) and treats it as stale after 60 seconds.
A worker able to write the state root could forge it, so `/health` remains an independent signal.

Verdicts:

| Verdict | Meaning |
|---|---|
| 已停止 | A stopped heartbeat and no `/health` |
| 正在启动 | A fresh heartbeat with phase `starting` |
| 无响应 | No `/health` and no fresh heartbeat |
| 需要关注 | A half-alive service, erroring or overlong loops, recent webhook rejections, held or orphaned slots, expired leases, cleanup pending for over 10 minutes, repeated Linear status failures, or a reservation cancellation not yet settled |
| 正常 | None of the above |
| 未知 | The ledger cannot be read |

A verdict is evidence about these checks only, not proof that Linear, the tunnel or Unity work.
```

- [ ] **Step 3: Update AGENTS.md, the development workflow and the example profile**

In `AGENTS.md` "Project map", after the `agent/deploy.py`, `agent/doctor.py` line, add:

```markdown
- `agent/monitor.py`, `agent/monitor_view.py`, `agent/monitor_static/`, `agent/heartbeat.py`,
  `agent/readonly_db.py`: read-only office status monitor, the heartbeat `serve` writes for it, and the
  read-only ledger snapshots it shares with `doctor`.
```

In `docs/development-workflow.md`, at the end of "Setting up TestBot" step 7 (**Run.**), add:

```markdown
   To watch TestBot from another device on the office network, give the profile a
   `"monitor": {"bind": "0.0.0.0", "port": 8781}` block and run
   `python3 -m agent.service monitor --config /absolute/profile.json` beside the controller; see the
   README's office status monitor section. macOS may ask whether Python may accept incoming connections.
   Edit the profile with a command that prints nothing from it, never with a tool that echoes file
   contents.
```

In `config/development.example.json`, add this line after `"port": 8766,`:

```json
  "monitor": {"bind": "127.0.0.1", "port": 8781},
```

- [ ] **Step 4: Check the documentation**

Run: `python3 -c "import json; json.load(open('config/development.example.json', encoding='utf-8'))"`
Expected: no output. The template must stay valid JSON.
Run: `git diff --check`
Expected: no output.
Read each changed section once in rendered Markdown. Check that every command and file name matches the code, and that no private hostname, address or path appears.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/operating-contract.md AGENTS.md docs/development-workflow.md config/development.example.json
git commit -F - <<'EOF'
Document the office status monitor

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 10: Final verification and handoff

**Files:** none new.

- [ ] **Step 1: Run the full offline suite**

Run: `python3 -m unittest discover -s tests -v 2>&1 | tail -5`
Expected: `OK (skipped=N)`. The baseline before this plan was 743 tests with 11 skips. The count grows by the new tests. Skips must still be only Windows-only checks on macOS. Report the exact totals. Do not present macOS results as Windows verification.

- [ ] **Step 2: Check the diff for whitespace and public-repository leaks**

Run: `git diff --check $(git merge-base HEAD origin/main)`
Expected: no output.
Run: `git diff $(git merge-base HEAD origin/main) -- . ':(exclude)docs/superpowers' | grep -nE '192\.168\.|(^|[^0-9])10\.[0-9]+\.[0-9]+\.[0-9]+|172\.(1[6-9]|2[0-9]|3[01])\.|trycloudflare|/Users/|C:\\Users\\' || true`
Expected: no hits. The design and plan documents are excluded because this command line appears in them. The only example names in the diff should be `farmbot-host.local`, `192.0.2.x` and `C:\FarmBot\...`.

- [ ] **Step 3: Review against the spec**

Walk through every section of `docs/superpowers/specs/2026-09-24-status-monitor-design.md` and point to the task that implements it. Fix any gap before handing off.

- [ ] **Step 4: Live check with TestBot (only with the user's go-ahead)**

Ask the user first: this starts real services on the operator's machine. With approval:
1. Add `"monitor": {"bind": "0.0.0.0", "port": 8781}` to the TestBot profile with a command that prints nothing from the file.
2. Start the TestBot controller the usual way, then run `python3 -m agent.service monitor --config <profile>` in another terminal.
3. Open the page from a phone on the office Wi-Fi and check that it reads 正常 with loop rows.
4. Stop the controller and check that the page turns 无响应 or 已停止 within about a minute. Restart it and check that it returns to 正常.
5. If the user chooses an issue, @TestBot on it and watch the job appear and progress.

- [ ] **Step 5: Open the pull request (only with the user's go-ahead)**

Push the branch and open a PR against `main`. Summarize the behaviour and the verification actually run. Name what was not verified, such as Windows host acceptance. End the body with the Claude Code attribution line. Keep private hostnames, addresses and paths out of the PR text.

---

## Production rollout (operator, separately authorized; not part of this plan's execution)

1. Place the merged revision in its own directory on the Windows production host. It runs only `monitor`; production `serve` stays on its accepted revision.
2. Confirm that production's revision ignores unknown top-level config keys. Its `agent/config.py` loader must filter keys to `Config` fields.
3. Make sure the production config has an absolute `local_root`. If it has none, add exactly the path production already uses. From the monitor directory, `doctor --config` must then report production's actual ledger path.
4. Add the `monitor` block, and validate the edited file with `doctor --config` before anything else reads it. Production needs no restart.
5. Create the firewall rule and the logon scheduled task from the README, running as the same user as `serve`. Check restart-on-failure once.
6. From another office machine, check that the page loads, that its jobs match `doctor`, and that loop rows read 此版本未提供 until production is upgraded.
7. Loop heartbeats appear after production's next normal upgrade, through the regular release process.
