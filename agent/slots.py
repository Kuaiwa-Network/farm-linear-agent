"""The Unity slot pool: long-lived project folders Unity is pointed at, and nothing else (spec §7).

A slot is a detached worktree of FarmBot's own clone with a built Library/. Task worktrees are for code and
never open Unity. Everything host-specific lives in the slot's configuration entry or in agent/unity.py.
"""
import hashlib
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
import signal
import subprocess
import time
from uuid import uuid4
import urllib.parse
from pathlib import Path

from contextlib import contextmanager
from .identity import CHECKS, collect, quiet, ready
from .kw_ops import child_environment
from .launcher import _read_worker_text, _write_worker_file
from .ledger import Ledger, LedgerError
from .unity import (UnityError, batch_test_command, editor_holds_project, editor_path, other_editor_project,
                    read_results)
from .unity_mcp import UnityMcp
from .worktrees import FetchRaced, WorktreeError

# What a slot path's git work raises: _git raises subprocess.TimeoutExpired rather than WorktreeError when a call
# outlives its timeout, and a filesystem step raises OSError. switch, park and ensure turn each into a git-stage
# SlotError, because anything else escapes the pool with the slot `switching` and is retried every pass.
GIT_FAILURES = (WorktreeError, subprocess.TimeoutExpired, OSError)


class UnityRefreshPending(Exception):
    """The refresh may have executed; reconcile it by reading, never replaying it."""


DEFAULTS = {
    "kind": "unity_slot",
    "repo": "Farm-Client",
    # The plugin's own default local base URL; per-slot addressing is set_active_instance, not a second port.
    "mcp_address": "http://127.0.0.1:8080/mcp",
    "folder": None,          # defaults to <editors_root>/slot-<the id's suffix>, e.g. .local/editors/slot-1
    "instance": None,        # the Editor instance id Name@hash; discovered on the first interactive attach
    "unity": None,           # absolute Editor path; discovered from the project version when absent
    "build_target": None,    # the enum name the identity probe expects, e.g. StandaloneOSX or Android
    "build_target_argument": None,   # the -buildTarget spelling, e.g. OSXUniversal
    "test_platform": "EditMode",
    "test_assemblies": ("HotUpdate.Tests",),
    "account": None,         # the dedicated 公共测试服 account; supplied by the operator, never by code
    # Both seeded from Task 0 Step 5's measured numbers with headroom. The recompiles measured 5.7-9.6s, so
    # 300s is the pool's own deadline well above them; the Editor was gone 1s after SIGTERM, so 60s is
    # already extravagant — it was 120 when the close was expected to wait on a lockfile that never goes.
    "quiet_timeout": 300,
    # Native Windows production measured a 170-second startup; the original
    # 120-second deadline was based on a much smaller Mac rehearsal.
    "start_timeout": 300,
    "close_timeout": 60,
    # Task 0 Step 4 measured the EditMode suite at 19 s, and Step 6 at 105 s for the same suite under
    # launchd's Background band, so half an hour is two orders of magnitude of headroom — and still a
    # deadline, which is what the addendum's Editor ran past for 25 minutes with nothing to stop it.
    "batch_timeout": 1800,
}


class SlotError(RuntimeError):
    STAGES = ("git", "editor", "compile", "probe")
    # A mistake in FarmBot's own code reaches the collaborator handlers below as an ordinary exception — an
    # AttributeError on an mcp that was never wired, say — and would otherwise be reported to the operator
    # as "the Editor never went quiet". The stage, and therefore the hold, is the same either way; only the
    # blame changes, because `recover-slot` cannot fix a bug in this file.
    FARMBOT_FAULTS = (AttributeError, TypeError, NameError, KeyError, IndexError, ImportError)

    def __init__(self, message, stage="git", fault="external"):
        super().__init__(message)
        if stage not in self.STAGES:
            # SlotPool._switch_failed routes on this string by equality, so a typo at a raise site would fall
            # through the probe arm and *release* a slot spec §7 says to hold — silently, on the only slot
            # there is. Construction is the one place that can catch it, and it is what STAGES is for.
            raise ValueError(f"stage must be one of {self.STAGES}, not {stage!r}")
        # "git" may be retried once at the tail of the queue; "compile" fails the item and leaves
        # the slot in the pool. Editor preparation and "probe" failures quarantine it for
        # bounded controller recovery; see _switch_failed for the preparation/run distinction.
        self.stage = stage
        # "external" is git, Unity, the host or the operator; "farmbot" is this codebase's own bug.
        self.fault = fault


def slot_entry(raw):
    """Apply defaults to one config slot entry. Unknown keys are refused rather than silently ignored."""
    unknown = set(raw) - set(DEFAULTS) - {"id"}
    if unknown:
        raise SlotError(f"unknown slot configuration keys: {sorted(unknown)}")
    if not isinstance(raw.get("id"), str) or not raw["id"].strip():
        raise SlotError("a slot entry needs an id such as unity_slot:1")
    entry = {**DEFAULTS, **raw}
    if type(entry["start_timeout"]) not in (int, float) or not 0 < entry["start_timeout"] <= 600:
        raise SlotError("slot start_timeout must be a number greater than 0 and at most 600 seconds")
    entry["test_assemblies"] = tuple(entry["test_assemblies"])
    return entry


def fixed_editor_count(settings, entries):
    """An absent block retains legacy execution for installations awaiting migration."""
    if settings == {}:
        return 0
    if (not isinstance(settings, dict) or set(settings) != {"editors"} or not isinstance(entries, list)
            or type(settings["editors"]) is not int or not 1 <= settings["editors"] <= len(entries)):
        raise ValueError("unity_pool requires editors between 1 and the configured slot count")
    selected = [slot_entry(entry) for entry in entries[:settings["editors"]]]
    if any(entry["kind"] != "unity_slot" for entry in selected) or len({entry["id"] for entry in selected}) != len(selected):
        raise ValueError("unity_pool requires distinct Unity slots")
    return settings["editors"]


class SlotPool:
    BUSY = ("interactive_busy", "batch_busy", "switching", "held")

    BUSY_FOR = {"interactive": "interactive_busy", "batch": "batch_busy"}

    @staticmethod
    def source_candidate_failure(observation):
        checks = observation.get('checks') or {}
        return (checks.get('source_clean') == 'mismatch'
                and all(checks.get(key) == 'match' for key in CHECKS if key != 'source_clean'))

    def __init__(self, ledger, worktrees, entries, *, host, editors_root, unity=None, mcp=None, clock=time.time,
                 sleep=None, editor_scan=None, editor_pid=None, state_dir=None, owner="pool",
                 run_unsandboxed=None, concurrent_batches=False, admission=None, keep_open=False, worker_acquire=None,
                 prepare_integration=None):
        # `ledger` may be a Ledger or a zero-argument factory. The pool runs on its own thread beside the
        # receiver's and the scheduler's, and two threads on one sqlite3.Connection do not get two
        # transactions: _transaction() is a bare BEGIN IMMEDIATE/COMMIT, so one thread's BEGIN can land
        # inside the other's and either COMMIT would apply to the other's half-written work. The receiver
        # already takes a factory for exactly this, and service.build passes the pool one too. A pool handed
        # a Ledger *object* borrows it and must not close it — that one belongs to the scheduler.
        self._owns_ledger = callable(ledger) and not isinstance(ledger, Ledger)
        if concurrent_batches and not keep_open and not self._owns_ledger:
            raise ValueError("concurrent batches require a separate Ledger connection factory")
        self._ledger_factory = ledger if self._owns_ledger else None
        self.ledger = ledger() if self._owns_ledger else ledger
        self.worktrees = worktrees
        self.entries = {entry["id"]: entry for entry in entries}
        self.host = host
        self.editors_root = Path(editors_root)
        self.unity = unity
        self.mcp = mcp
        self.clock = clock
        # All three are injected so that no test waits on a real clock or shells out to pgrep: a developer
        # with their own Unity Editor open would otherwise fail every switch test on their machine.
        self.sleep = sleep or time.sleep
        self.editor_scan = editor_scan or (lambda folder: other_editor_project(
            folder, allowed_projects=[self.folder(entry) for entry in self.entries.values()], strict=True))
        self.editor_pid = editor_pid or (lambda folder: editor_holds_project(folder, strict=keep_open))
        # A callable taking an item id and returning that item's private directory: Launcher.state_dir in
        # production. The reservation token is written there and nowhere else.
        self.state_dir = state_dir
        # Launcher.run_unsandboxed in production, injected so no test in this suite starts a process it did
        # not write. A pool built without one refuses a batch grant rather than silently handing a worker a
        # run that never happened.
        self.run_unsandboxed = run_unsandboxed
        self.owner = owner
        self.admission = admission
        self.keep_open = keep_open
        self.worker_acquire = worker_acquire
        self.prepare_integration = prepare_integration
        self._process_unknown_since = {}
        self._fixed_prepared = False
        self._maintenance_observation = None
        self.last_observation = None
        self._batches = {}
        self._batch_executor = (ThreadPoolExecutor(max_workers=max(1, len(self.entries)),
                                                  thread_name_prefix="farmbot-unity")
                                  if concurrent_batches and not keep_open and len(self.entries) > 1 else None)
        self._draining_batches = False

    @staticmethod
    def _collaborator_error(slot_id, exc, *, stage, doing):
        """Turn a collaborator's failure into the SlotError the operator reads.

        The stage — and therefore the hold — is the same whichever side misbehaved: releasing a slot whose
        state is unknown risks a second Editor on the folder, which is the corruption slots exist to
        prevent. What changes is the blame. A programming error in FarmBot's own code arrives here as an
        ordinary exception, and calling it "the Editor never went quiet" sends an operator to `recover-slot`
        for something recover-slot cannot fix. The exception is repr'd rather than str'd because its *type*
        is the diagnostic that separates the two cases.
        """
        fault = "farmbot" if isinstance(exc, SlotError.FARMBOT_FAULTS) else "external"
        lead = (f"FarmBot bug while {doing}; this is not a Unity fault and recover-slot will not fix it"
                if fault == "farmbot" else f"{doing} failed")
        return SlotError(f"{slot_id}: {lead}: {exc!r}", stage=stage, fault=fault)

    def folder(self, entry):
        """<editors_root>/slot-<n> for the id unity_slot:<n>. The `folder` key is the escape hatch; the
        default name is pinned by a test, because the spec amendment, Task 11 and every assertion in
        tests/test_slots.py name `slot-1` and a different spelling would silently fail all of them."""
        return Path(entry["folder"]) if entry["folder"] else self.editors_root / f"slot-{entry['id'].split(':', 1)[-1]}"

    def ensure(self):
        """Register every configured slot and make sure its folder is usable. Idempotent, and a restart never
        moves a folder something else is using."""
        if self.keep_open:
            self._fixed_prepared = False
            folders = [self.folder(entry).resolve() for entry in self.entries.values()]
            if len(set(folders)) != len(folders):
                raise SlotError("fixed Unity slots must use distinct project folders")
            outside = [slot for slot in self.ledger.slots(kind="unity_slot", host=self.host)
                       if slot["slot_id"] not in self.entries and slot["state"] != "idle_closed"]
            if outside or any(r["kind"] == "unity_slot" and r["mode"] == "batch" and r["host"] == self.host
                              for r in self.ledger.reservations(states=("active", "cancel_requested"))):
                raise SlotError("settle active or open Unity resources before changing the fixed pool")
            self.ledger.interactive_unity_requests(self.host)
        views = []
        for slot_id, entry in self.entries.items():
            folder = self.folder(entry)
            # str(): Ledger.ensure_slot validates its folder as text before it stores it, so a Path is refused.
            record = self.ledger.ensure_slot(slot_id, kind=entry["kind"], host=self.host, folder=str(folder),
                                             mcp_address=entry["mcp_address"], account=entry["account"],
                                             instance=entry["instance"])
            if record["state"] in self.BUSY:
                from .resource_recovery import RecoveryStore
                RecoveryStore(self.ledger).interrupted_handoff(slot_id, self.host)
                record = self.ledger.slot(slot_id)
                if self.keep_open and record["state"] == "switching" and not self.ledger.active_reservation_on(slot_id):
                    last = self.ledger.connection.execute("""SELECT kind FROM audit WHERE item_id=?
                        AND kind IN ('unity_editor_start','unity_editor_ready') ORDER BY id DESC LIMIT 1""",
                        (slot_id,)).fetchone()
                    if last and last["kind"] == "unity_editor_start":
                        with self.ledger._transaction():
                            RecoveryStore(self.ledger).request_in_transaction(slot_id,
                                "persistent Editor startup interrupted by controller restart", recovery_kind="setup")
                        record = self.ledger.slot(slot_id)
                views.append(record)
                continue
            fresh = not folder.exists()
            try:
                commit = self.worktrees.resolve_commit(entry["repo"])
                self.worktrees.add_slot(entry["repo"], folder, commit)
                # add_slot creates a folder at `commit` but deliberately does not move one that already
                # exists: a restart must not cost a Unity reimport. The parked commit is therefore read back
                # from the folder, never assumed to be the head just resolved.
                parked = self.worktrees.head(folder)
                pointers = self.worktrees.pointers_remain(folder)
            except GIT_FAILURES as exc:
                # All four parts of slot creation are inside this guard, because the contract is that a slot
                # which cannot be made usable fails as a SlotError naming the slot: a bare WorktreeError at
                # service start does not say which folder the operator has to go and look at.
                raise SlotError(f"{slot_id}: could not prepare {folder}: {exc}") from exc
            if pointers:
                raise SlotError(f"{slot_id}: {folder} still holds git-lfs pointer files; Unity would import "
                                f"them as corrupt assets. Fetch or seed the LFS objects before starting.")
            if fresh or record["state"] == "idle_closed":
                record = self.ledger.set_slot_state(slot_id, "idle_closed", parked_commit=parked)
            views.append(record)
        if self.keep_open:
            self._fixed_prepared = True
            self.warm_editors()
            views = [self.ledger.slot(entry) for entry in self.entries]
        return views

    def warm_editors(self):
        """Open each owned project once; a failed start uses the existing bounded recovery lane."""
        from .maintenance import Maintenance
        if Maintenance(self.ledger).requested():
            return 0
        if not self.keep_open or not self._fixed_prepared:
            return 0
        # Do not immediately spend budget that the previous tick reclaimed for a server.
        from .integration import server_pending
        waiting = server_pending(self.ledger, single_only=getattr(self, 'single_only', False)) if self.admission else []
        if waiting:
            from .integration import get as integration_request
            if (not integration_request(self.ledger, waiting[0]['reservation_id']) or
                    any(s['state']!='idle_closed' for s in self.ledger.slots(kind='unity_slot', host=self.host))):
                return 0
        from contextlib import nullcontext
        from .resource_recovery import RecoveryStore
        opened = 0
        for slot_id, entry in self.entries.items():
            slot = self.ledger.slot(slot_id)
            if slot is None or self.ledger.active_reservation_on(slot_id):
                continue
            if slot["state"] == "idle_open":
                try:
                    is_open = self.editor_is_open(slot)
                    self._process_unknown_since.pop(slot_id, None)
                    if is_open:
                        continue
                except UnityError as exc:
                    if slot_id not in self._process_unknown_since:
                        self.ledger.note(slot_id, 'unity_process_observation_delayed', str(exc),
                                         {'retry_seconds': 30})
                    since = self._process_unknown_since.setdefault(slot_id, self.clock())
                    if self.clock() - since < 30:
                        continue  # no grant or process control while ownership is unknown
                    with self.ledger._transaction():
                        RecoveryStore(self.ledger).request_in_transaction(slot_id,
                            f"persistent Editor process observation unavailable: {exc}", recovery_kind="setup")
                    continue  # an unknown process is never closed, reopened or granted
                self.ledger.set_slot_state(slot_id, "idle_closed")
            elif slot["state"] != "idle_closed":
                continue
            # The admission lock covers recording 'switching', so the server pool cannot spend the
            # same memory budget while this Editor starts outside a worker's process containment.
            with self.admission.lock if self.admission else nullcontext():
                if self.admission and not self.admission.can_start_editor(self.ledger, host=self.host):
                    continue
                slot = self.ledger.begin_editor_warmup(slot_id, host=self.host)
            if slot is None:
                continue
            self.last_observation = None
            try:
                commit = self.worktrees.head(Path(slot["folder"]))
                self.ledger.set_slot_state(slot_id, "switching", parked_commit=commit)
                self.switch(slot_id, commit, "interactive")
            except Exception as exc:
                observation = self.last_observation or {}
                if (isinstance(exc, SlotError) and exc.stage == "probe"
                        and self.source_candidate_failure(observation)):
                    RecoveryStore(self.ledger).quarantine_source(slot_id,
                        "persistent Editor startup changed committed source; preserve inputs and recover a clean baseline",
                        observation)
                    continue
                with self.ledger._transaction():
                    RecoveryStore(self.ledger).request_in_transaction(slot_id,
                        f"persistent Editor preparation failed: {type(exc).__name__}: {exc}", recovery_kind="setup")
            else:
                self.ledger.set_slot_state(slot_id, "idle_open", parked_commit=commit)
                self.ledger.note(slot_id, 'unity_editor_verified', 'persistent Editor baseline verified',
                                 {'observation': self.last_observation})
                self.ledger._audit(slot_id, "unity_editor_ready", "persistent Editor ready")
                opened += 1
                if waiting:
                    break  # A combined request needs one Editor, not the full warm pool.
        return opened

    def token_path(self, item_id):
        return Path(self.state_dir(item_id)) / "reservation.token"

    def _write_token(self, item_id, reservation):
        """The token goes to a file, never onto a command line: arguments are visible to every process on
        the host (spec §15). The dispatch payload carries this path, never the secret. The path is in the
        worker's state directory, so the file replaces whatever is there rather than being opened through it."""
        path = self.token_path(item_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_worker_file(path, reservation["token"], mode=0o600)
        return path

    def _read_token(self, item_id):
        """None when the worker left something unusable there, which holds the slot: raising would stop every
        tick before it settled, granted or parked anything."""
        try:
            return _read_worker_text(self.token_path(item_id)).strip()
        except (OSError, ValueError):
            return None

    def tick(self):
        """The pool thread's whole loop body. Everything slow lives here and nothing here holds a ledger
        transaction across a git command, a subprocess or an MCP call.

        Completed executions are handed over first. Settlement and parking preserve
        quiescence checks; a newly parked slot becomes available on the next tick.
        """
        if self.keep_open and not self._fixed_prepared:
            return {"settled": 0, "granted": 0, "parked": 0}
        completed = self._finish_batches()
        from .maintenance import Maintenance
        maintaining = Maintenance(self.ledger).requested()
        if self.keep_open and not maintaining:
            self.ledger.interactive_unity_requests(self.host)
            self.warm_editors()
        if not maintaining:
            self.close_idle_for_memory()
        result = {"settled": self.settle(), "granted": completed + self.grant(), "parked": self.park_idle()}
        if maintaining:
            self._observe_maintenance()
        return result

    def _observe_maintenance(self):
        """Probe on the pool thread, never under the scheduler lock or on its DB connection."""
        from .maintenance import request
        current = request(self.ledger.connection)
        previous = self._maintenance_observation
        if not current or (previous and previous['request_id'] == current['request_id']
                           and 0 <= self.clock() - previous['checked_at'] < 5):
            return
        blockers = []
        for slot in self.ledger.slots(kind='unity_slot', host=self.host):
            try:
                if slot['state'] == 'idle_open':
                    quiet = self.mcp is not None and self.mcp.quiescent(slot, 'interactive')
                elif slot['state'] == 'idle_closed':
                    quiet = not self.editor_is_open(slot)
                else:
                    quiet = False
            except Exception:
                quiet = False
            if not quiet:
                blockers.append({'code': 'resource_probe', 'subject': slot['slot_id']})
        # Assignment replaces the entire immutable snapshot; the scheduler only reads it.
        self._maintenance_observation = {'request_id': current['request_id'],
                                        'checked_at': self.clock(), 'blockers': blockers}

    def maintenance_blockers(self, current):
        observed = self._maintenance_observation
        if (not observed or observed['request_id'] != current['request_id']
                or not 0 <= self.clock() - observed['checked_at'] <= 15):
            return [{'code': 'resource_probe', 'subject': 'unity_pool'}]
        return list(observed['blockers'])

    def close_idle_for_memory(self):
        """Configured heavy-resource admission may reclaim idle Editors, after the ordinary ownership probe."""
        if self.admission is None:
            return
        queued = self.ledger.reservations(states=("queued",))
        if not queued:
            return
        from .integration import server_pending
        waiting = bool(server_pending(self.ledger, single_only=getattr(self, 'single_only', False)))
        if not waiting and self.admission.last_reason is None:
            return
        for slot in self.ledger.slots(kind="unity_slot", host=self.host):
            if self.keep_open and slot['slot_id'] not in self.entries:
                continue
            if slot["state"] != "idle_open" or self.ledger.active_reservation_on(slot["slot_id"]):
                continue
            with self.admission.lock:
                pending = server_pending(self.ledger, single_only=getattr(self, 'single_only', False))
                if pending:
                    # A combined request needs one Editor retained; close only surplus Editors.
                    from .integration import get as integration_request
                    combined = integration_request(self.ledger, pending[0]['reservation_id'])
                    if combined and len([s for s in self.ledger.slots(kind='unity_slot', host=self.host)
                                         if s['state']!='idle_closed']) <= 1:
                        break
                    detail = self.admission.assess(self.ledger, self.host,
                                                  self.admission.settings['server_mib'] if combined else
                                                  self.admission.cost(pending[0]), kind='server_slot')
                    if not set(detail['blockers']) & {'reservation_budget', 'physical_memory', 'commit_headroom'}:
                        break
                elif self.keep_open:
                    break
                slot = self.ledger.begin_idle_editor_close(slot['slot_id'], host=self.host)
            if slot is None:
                continue
            try:
                if self.mcp is None or not self.mcp.quiescent(slot, 'interactive'):
                    raise SlotError('idle Editor quiescence could not be verified')
                self.close_editor(slot)
                self.park(slot["slot_id"], "batch")
            except Exception:
                self.ledger.set_slot_state(slot["slot_id"], "held")

    def _run_batch_on_connection(self, reservation):
        # Only execution leaves the pool thread. Git, MCP preparation, reservation
        # hand-over and parking remain serialized there. Each batch owns its SQLite
        # connection; sharing the pool's connection would interleave transactions.
        batch = SlotPool(self._ledger_factory, self.worktrees, list(self.entries.values()),
                         host=self.host, editors_root=self.editors_root, unity=self.unity,
                         mcp=self.mcp, clock=self.clock, sleep=self.sleep,
                         editor_scan=self.editor_scan, editor_pid=self.editor_pid,
                         state_dir=self.state_dir, owner=self.owner,
                         run_unsandboxed=self.run_unsandboxed)
        try:
            return batch.run_batch(batch.ledger.slot(reservation["resource"]), reservation)
        finally:
            batch.close()

    def _finish_batches(self):
        completed = 0
        for slot_id, (reservation, future) in list(self._batches.items()):
            if not future.done():
                continue
            try:
                try:
                    future.result()
                except SlotError as exc:
                    self._switch_failed(reservation, exc, recovery_kind="execution")
                except Exception as exc:
                    self._give_the_slot_back(reservation, f"hand-over failed after the switch: {exc!r}"[:400], True)
                else:
                    completed += int(self._resume_hand_over(reservation))
            finally:
                del self._batches[slot_id]
        return completed

    def drain_batches(self):
        """Join executions and finish their hand-overs before closing the ledger/root lock.

        Service shutdown fences and stops owned runs first. Its heartbeat continues
        while this method waits, and no new batch is admitted after draining starts.
        """
        self._draining_batches = True
        if self._batch_executor is not None:
            self._batch_executor.shutdown(wait=True)
        return self._finish_batches()

    def grant(self):
        """`granted` counts hand-overs that reached a worker, not acquisitions that were attempted. A
        switch that failed and a Stop that landed mid-switch both give the slot back, so counting them
        would make tick()'s own report disagree with the ledger.

        Concurrent batch execution is bounded by configured slots. Preparation remains
        on this thread, and an in-flight execution cannot be settled or reacquired.
        Synchronous hand-overs retain one acquire per kind per tick, including retries.
        """
        granted = 0
        from .maintenance import Maintenance
        if Maintenance(self.ledger).requested():
            return granted
        if self._draining_batches:
            return granted
        if self.keep_open and not self._fixed_prepared:
            return granted
        if self.keep_open:
            self.ledger.interactive_unity_requests(self.host)
        excluded = set(self._batches) if self.keep_open else self._batches
        if self.keep_open:
            excluded.update(self._process_unknown_since)
            excluded.update(s["slot_id"] for s in self.ledger.slots(kind="unity_slot", host=self.host)
                            if s["slot_id"] not in self.entries or s["state"] != "idle_open")
        for kind in sorted({entry["kind"] for entry in self.entries.values()}):
            for _ in range(len(self.entries)):
                acquire = (self.admission.acquire if self.admission else
                           lambda ledger, kind, **options: ledger.acquire(kind, **options))
                options = dict(owner=self.owner, host=self.host, exclude_resources=excluded)
                reservation = (self.worker_acquire(self.ledger, acquire, kind, **options) if self.worker_acquire else
                               acquire(self.ledger, kind, **options))
                if reservation is None:
                    break
                granted += int(self._hand_over(reservation))
                if reservation["resource"] not in self._batches:
                    break  # interactive, synchronous batch or preparation failure
        return granted

    def _hand_over(self, reservation):
        """True when the item really has the slot: the switch succeeded, the token is on disk and resume()
        put the item back in the queue, where the existing launch loop picks it up on the next tick.

        Everything after the switch is inside one try/finally, and that is the point rather than a tidy-up.
        Once switch() returns, the slot is `*_busy` and the reservation is `active` while the item is still
        `awaiting_resource` — and `reservations_to_settle` excludes `awaiting_resource`. So anything raising
        in this window and escaping into serve()'s guarded loop leaves a state the pool cannot recover from
        by itself: nothing ever settles that reservation, no slot ever goes `held`, and the operator gets a
        `loop_error` line every two seconds and no `recover-slot` signal at all — on the only slot there is.
        The window is small but every step in it can really fail: an unwritable or full `state_dir`, or a
        pool built with `state_dir=None`, which __init__ accepts.
        """
        item_id, slot_id = reservation["item_id"], reservation["resource"]
        from .integration import get as integration_request
        combined = integration_request(self.ledger, reservation['reservation_id'])
        if combined:
            try:
                if self.prepare_integration is None:
                    raise SlotError('combined server pool is unavailable')
                self.prepare_integration(reservation)
                if self.ledger.reservation(reservation['reservation_id'])['state'] != 'active':
                    self.ledger.release(reservation['reservation_id'], reservation['token'], 'stopped during combined setup')
                    return False
                # The reads root is outside worker write roots: the continuing worker's resource-status and
                # run-integration only validate this snapshot, so it must exist before readiness.
                self.worktrees.read_candidate('farm-hive', item_id, combined['plan']['server_commit'])
                self.switch(slot_id, reservation['commit_sha'], 'interactive')
                return self._resume_hand_over(reservation, self.last_observation)
            except Exception as exc:
                self.ledger.set_slot_state(combined['server_slot'], 'held')
                self.ledger.set_slot_state(slot_id, 'held')
                self.ledger.note(item_id, 'integration_setup_failed', str(exc)[:400])
                if self.ledger.item(item_id)['state'] in ('queued','awaiting_resource'):
                    self.ledger.fail_queued(item_id, 'combined verification setup failed; preserve both resources for recovery')
                return False
        self.last_observation = None
        if (reservation['kind'] == 'unity_slot' and self.ledger.item(item_id)['skill'] in ('work', 'feature', 'fix')
                and self.ledger.reservation_comparison(reservation['reservation_id'])):
            from .equal_tree_verification import select_candidate
            try:
                reservation = select_candidate(self.ledger, self.worktrees, reservation)
                if reservation['state'] != 'active':
                    return False
            except GIT_FAILURES as exc:
                self._switch_failed(reservation, SlotError(f'cannot verify pinned source trees: {exc}', stage='git'))
                return False
        if self.keep_open:
            from .resource_recovery import RecoveryStore
            entry = self.entries[slot_id]
            peers = [key for key, value in self.entries.items()
                     if (value['repo'], value['build_target']) == (entry['repo'], entry['build_target'])]
            known = RecoveryStore(self.ledger).known_candidate(self.host, peers, reservation['commit_sha'])
            if known:
                self.ledger.hold(reservation['reservation_id'],
                                 f"Candidate previously failed Unity preparation: {known['reason'][:300]}",
                                 recovery_kind='candidate')
                return False
        try:
            self.switch(slot_id, reservation["commit_sha"], reservation["mode"])
        except SlotError as exc:
            self._switch_failed(reservation, exc)
            return False
        if reservation["mode"] == "batch":
            if self._batch_executor is not None:
                try:
                    future = self._batch_executor.submit(self._run_batch_on_connection, reservation)
                except Exception as exc:
                    self._give_the_slot_back(reservation, f"could not start batch execution: {exc!r}"[:400], True)
                else:
                    self._batches[slot_id] = (reservation, future)
                return False  # only a completed run may resume its worker
            # The run itself, here and not in the worker. Under Codex's workspace-write seatbelt the Editor
            # hangs for ever on a denied Mach lookup (Task 0's addendum), so the pool performs the run on
            # this thread, outside the sandbox, and the fresh worker is handed the results file instead.
            try:
                self.run_batch(self.ledger.slot(slot_id), reservation)
            except SlotError as exc:
                self._switch_failed(reservation, exc, recovery_kind='execution')
                return False
            except Exception as exc:
                # An unwritable state_dir, or a pool built with state_dir=None, which __init__ accepts. The
                # slot never misbehaved, so it goes back to the pool by the same route the rest of the
                # hand-over window uses rather than being held against an operator who cannot fix it there.
                self._give_the_slot_back(reservation,
                                         f"hand-over failed after the switch: {exc!r}"[:400], True)
                return False
        return self._resume_hand_over(reservation, self.last_observation)

    def _resume_hand_over(self, reservation, observation=None):
        item_id, slot_id = reservation["item_id"], reservation["resource"]
        current = self.ledger.reservation(reservation["reservation_id"])
        if current is None or current["state"] != "active":
            self._give_the_slot_back(reservation, "item stopped during its batch hand-over", False)
            return False
        handed = False
        # The defaults cover the one path that reaches the finally without passing an except arm: a
        # BaseException, which `guarded` does not catch either and which would otherwise take the pool
        # thread down with the slot held by a live reservation.
        reason, fail_item = "hand-over abandoned after the switch", True
        try:
            if observation is not None:
                self.ledger.record_identity(item_id, reservation["reservation_id"], slot_id,
                                            observation)
            self._write_token(item_id, reservation)
            try:
                from .execution import single, resource_ready
                if single(self.ledger.item(item_id)):
                    resource_ready(self.ledger, reservation)
                else:
                    self.ledger.resume(item_id, f"{slot_id} acquired ({reservation['mode']})",
                                       stage="unity-worker-ready" if reservation["mode"] == "interactive" else None)
            except LedgerError:
                # A Stop landed during the multi-minute switch, so the item is already cancelled and
                # resume() refuses it. The item needs nothing here: it is already terminal.
                reason, fail_item = "item cancelled mid-switch", False
            else:
                handed = True
        except Exception as exc:
            reason = f"hand-over failed after the switch: {exc!r}"[:400]
        finally:
            if not handed:
                self._give_the_slot_back(reservation, reason, fail_item)
        return handed

    def _give_the_slot_back(self, reservation, reason, fail_item):
        """Every exit from the hand-over window that is not a hand-over. park_idle returns the slot to main
        on the same pass, so the pool is whole again by the end of the tick that broke it."""
        self._forget_token(reservation["item_id"])
        self.ledger.release(reservation["reservation_id"], reservation["token"], reason)
        if fail_item:
            # The slot is fine and goes back to the pool, but the item must not be left waiting: nothing
            # re-queues its reservation, and reservations_to_settle cannot see an `awaiting_resource` item,
            # so it would wait for a slot that is never handed to it and no report would ever be owed.
            self._preparation_failed(reservation, reason)

    def _preparation_failed(self, reservation, reason):
        from .execution import single
        if single(self.ledger.item(reservation['item_id'])):
            with self.ledger._transaction():
                self.ledger.connection.execute("UPDATE work_items SET stage='resource-preparation-failed',needs_resource=NULL "
                    "WHERE id=? AND state='running'", (reservation['item_id'],))
                self.ledger._audit(reservation['item_id'], 'resource_preparation_failed', reason)
        else:
            self.ledger.fail_queued(reservation['item_id'], reason)

    def _forget_token(self, item_id):
        """Best effort, because the give-back path runs after a failure that may be the very reason the
        token path cannot be computed (`state_dir=None`) or the file cannot be removed."""
        try:
            self.token_path(item_id).unlink(missing_ok=True)
        except Exception:
            return False
        return True

    def _switch_failed(self, reservation, exc, *, recovery_kind='setup'):
        reason = str(exc)[:400]
        if self.keep_open and (exc.stage == 'compile' or
                (exc.stage == 'probe' and self.source_candidate_failure(self.last_observation or {}))):
            self.ledger.hold(reservation['reservation_id'], reason, recovery_kind='candidate')
            return
        if exc.stage == "probe" or (exc.stage == "editor" and recovery_kind == "setup"):
            # Preparation failures have their own bounded budget: no worker has
            # received this grant yet. A batch run that already began is execution.
            # A startup deadline does not prove the Editor exited: it may still
            # be importing without MCP. Keep the requested commit and source
            # untouched until recovery fences the old attempt and owns repair.
            # Returning through park() here would force-checkout main beneath
            # that live importer and lose its changes before they can be archived.
            self.ledger.hold(reservation["reservation_id"], reason, recovery_kind=recovery_kind)
            return
        self.ledger.release(reservation["reservation_id"], reservation["token"], reason)
        try:
            # Through park(), never a direct set_slot_state(..., "idle_closed"). By the time a switch fails
            # the checkout has already moved the folder to the failed pin, and on an interactive grant
            # open_editor may already have started the Editor — so asserting "idle_closed" here writes the
            # same lie park_idle was fixed not to write: a row saying no Editor is open on a folder that has
            # one, with parked_commit still naming a commit the folder is not on. park() answers both
            # questions instead of assuming them, and returns the folder to main on the way.
            self.park(reservation["resource"], reservation["mode"])
        except SlotError:
            # Only the git stage reaches here, and its own checkout is what just failed, so the folder
            # cannot be returned to main. Spec §7 keeps a git or editor failure retryable at the tail, so
            # the slot stays in the pool rather than being held: the retry is what finds out whether the
            # failure was transient. The state is still asked rather than asserted.
            slot = self.ledger.slot(reservation["resource"])
            self.ledger.set_slot_state(reservation["resource"], self._idle_state(slot, reservation["mode"]))
        if exc.stage == "compile":
            # The slot is fine; this commit does not build. Fail the item and leave the slot in the pool.
            self._preparation_failed(reservation, f"compile errors at the pinned commit: {reason}")
        elif reservation["attempts"] >= 1:
            self._preparation_failed(reservation, f"verification gap: {reason}")
        else:
            self.ledger.requeue_reservation(reservation["reservation_id"], reason)

    def settle(self):
        """A slot is only useful to a running worker. Anything else is probed and then released or held.

        Nothing here is on a timer (global constraint). Three things make the gate fail and all three hold the
        slot: the quiescence check says no, the quiescence check raises, or the token file is gone. A batch
        reservation's quiescence is not a constant — it is the confirmation that no Unity process holds the
        folder — and only once that passes may the stale lock be cleared and the slot checked out, because
        `git checkout --force` plus `git lfs checkout` over a live Editor's Library/ is the corruption slots
        exist to prevent.
        """
        settled = 0
        for reservation in self.ledger.reservations_to_settle():
            if reservation["kind"] != "unity_slot":
                continue
            if reservation["resource"] in self._batches:
                continue  # execution/evidence must finish before cancellation settles
            slot = self.ledger.slot(reservation["resource"])
            token = self._read_token(reservation["item_id"])
            if token is None:
                self.ledger.hold(reservation["reservation_id"], "reservation token file is missing")
                continue
            try:
                # `is_quiet`, not `quiet`: agent.identity.quiet is imported at module level and read by
                # UnityIdentity.wait_quiet, and a local of that name here reads as the predicate itself.
                is_quiet = self.mcp.quiescent(slot, reservation["mode"]) if self.mcp is not None else True
            except Exception as exc:
                is_quiet = False
                reason = f"quiescence probe raised {type(exc).__name__}"
            else:
                reason = "quiescent" if is_quiet else "not quiescent"
            if not is_quiet:
                self.ledger.hold(reservation["reservation_id"], reason)
                continue
            if reservation["mode"] == "batch":
                # Never `lambda: False`, however convincing the quiescence check above looks. That check is
                # `True` by default when the pool has no MCP client at all, so asserting "gone" here would
                # delete the lock of a live Editor and hand the folder to the next `Unity -batchmode` — the
                # corruption slots exist to prevent, and the same hole clear_stale_lock's docstring already
                # closes in the batch branch of switch(), in close_editor and in park.
                self.clear_stale_lock(slot["folder"], lambda: self.editor_is_open(slot))
            self.ledger.release(reservation["reservation_id"], token, reason)
            self.token_path(reservation["item_id"]).unlink(missing_ok=True)
            settled += 1
        return settled

    def park_idle(self):
        """After the last release, the slot goes back to main so Library/ tracks main in small steps (spec §7).

        `switching` alone is not enough to say a slot is free: `acquire` sets it too, so a slot that is at
        this moment being handed to a worker looks exactly like one on its way back. `active_reservation_on`
        is the question that tells them apart — without it park_idle would return a slot to the pool while a
        live reservation still owned it, and the next acquire would hit the partial unique index.

        The departing mode comes from the ledger and never from a note this object made when it settled the
        reservation itself. settle() is the exceptional path: the ordinary way a slot comes back is the
        worker's own `release-resource`, which runs in the worker's process, so the pool sees no release to
        take a note on and would park every interactive slot as `idle_closed` — spec §7 says the Editor
        stays open after an interactive run, and the state that says so is what the next request's
        scheduling preference and close-before-batch both read.
        """
        parked = 0
        for slot in self.ledger.slots(host=self.host):
            if slot["kind"] != "unity_slot":
                continue
            if self.keep_open and slot["slot_id"] not in self.entries:
                continue
            if slot["slot_id"] in self._batches:
                continue
            if slot["state"] != "switching" or self.ledger.active_reservation_on(slot["slot_id"]):
                continue
            departing = self.ledger.last_reservation_on(slot["slot_id"])
            mode = departing["mode"] if departing else "batch"
            try:
                self.park(slot["slot_id"], mode)
            except SlotError:
                self.ledger.set_slot_state(slot["slot_id"], "held")
                continue
            parked += 1
        return parked

    def close(self):
        self.drain_batches()
        if self._owns_ledger:
            self.ledger.close()

    def switch(self, slot_id, commit, mode):
        """Spec §7's sequence, in order. The caller has already marked the slot 'switching' by acquiring it.

        `was_open` therefore cannot come from the slot row — `acquire` has already overwritten its state with
        the transient 'switching'. It is a question about the host: is a Unity process alive on this folder?
        See `editor_is_open` for why it is not a question about Temp/UnityLockfile.
        """
        if self.keep_open and mode != "interactive":
            raise SlotError("the fixed Unity pool accepts interactive reservations only")
        if self.keep_open and slot_id not in self.entries:
            raise SlotError("slot is outside the configured fixed Unity pool")
        slot = self.ledger.slot(slot_id)
        if slot is None:
            raise SlotError(f"unknown slot: {slot_id}")
        folder = Path(slot["folder"])
        entry = self.entries.get(slot_id, DEFAULTS)
        try:
            was_open = bool(self.mcp is not None and self.editor_is_open(slot))
        except UnityError as exc:
            raise SlotError(f"{slot_id}: Editor process observation unavailable: {exc}", stage="probe") from exc
        if self.keep_open and not was_open and self.ledger.active_reservation_on(slot_id):
            raise SlotError(f"{slot_id}: persistent Editor exited before hand-over", stage="editor")
        # Other project folders can coexist. Process checks apply to this folder;
        # UnityIdentity pins every MCP call to its verified project instance.
        try:
            try:
                self.worktrees.fetch(entry["repo"])   # the pin may post-date the clone's last fetch
            except FetchRaced:
                # Another fetch into the clone, a worker's from its sandbox, moved the refs this one meant to.
                # The pin is all this fetch was for, so the clone holding it settles the switch.
                if not self.worktrees.has_commit(entry["repo"], commit):
                    raise
            if not self.worktrees.slot_clean(folder):
                raise SlotError(f"{slot_id}: tracked files are dirty; a human edited the slot folder")
            if mode == "batch" and was_open:
                # spec §7: a batch request on an idle-open slot is accepted "after a graceful close".
                self.close_editor(slot)
            self.worktrees.checkout_commit(folder, commit)
            if self.worktrees.pointers_remain(folder):
                raise SlotError(f"{slot_id}: git-lfs pointers survived the checkout")
        except GIT_FAILURES as exc:
            raise SlotError(f"{slot_id}: git stage failed: {exc}", stage="git") from exc
        if mode == "batch":
            # A batch run needs no MCP at all; its result is a results file and an exit code (spec §7). The
            # only thing it needs from the pool is a folder no dead Editor still claims. The liveness check
            # is the process listing and never `lambda: False`: spec §7 removes the lock "only after
            # confirming the process is gone", and `was_open` above is guarded by `self.mcp is not None`, so
            # a pool built without an MCP client would otherwise assert "gone" without ever asking.
            self._clear_stale_lock(slot)
            return self.ledger.set_slot_state(slot_id, self.BUSY_FOR[mode], parked_commit=commit,
                                              last_switch_at=self.clock())
        instance = slot.get("instance")
        if not was_open:
            # A crash or an unreaped close leaves Unity's lock behind (Task 0 Step 5: still present at
            # +32 s). Nothing holds this folder, so the litter goes before the new Editor can meet it —
            # otherwise a surviving lock blocks the start and holds the only slot at the probe stage after
            # any crash. Same guard as everywhere else: the process listing decides, not an assertion.
            self._clear_stale_lock(slot)
            instance = self.open_editor(slot, entry)
        try:
            self.refresh_and_wait(slot, entry["quiet_timeout"])
        except SlotError:
            raise
        except Exception as exc:
            raise self._collaborator_error(slot_id, exc, stage="probe",
                                           doing="waiting for the Editor to go quiet after the refresh") from exc
        errors = self.mcp.console_errors_since(slot, commit)
        if errors:
            # Not a probe failure: the slot is fine, the commit does not compile. Keep the slot in the pool.
            raise SlotError(f"{slot_id}: {len(errors)} compile error(s) at {commit[:7]}: {errors[0]}",
                            stage="compile")
        if instance and instance != slot.get("instance"):
            self.ledger.set_slot_state(slot_id, "switching", instance=instance)
            slot = self.ledger.slot(slot_id)
        try:
            observation = self.mcp.probe(slot, {"commit_sha": commit,
                                                "build_target": entry.get("build_target"),
                                                "repository": str(folder)})
        except Exception as exc:
            raise self._collaborator_error(slot_id, exc, stage="probe",
                                           doing="running the identity probe") from exc
        self.last_observation = observation
        if observation.get("aggregate") != "match":
            raise SlotError(f"{slot_id}: identity probe did not match: {observation.get('checks')}",
                            stage="probe")
        # A released reservation no longer supplies recovery's target. Record the
        # verified candidate now: parking may fail on importer metadata before
        # it can update the old idle commit.
        return self.ledger.set_slot_state(slot_id, self.BUSY_FOR[mode], parked_commit=commit,
                                          last_switch_at=self.clock())

    def run_batch(self, slot, reservation):
        """The batch run itself, performed by the launcher outside any sandbox and never by the worker.

        The argv comes from the slot entry and the item's state directory only. That is the security
        property this redesign rests on: only controller-composed Unity runs escape
        the worker seatbelt, and no part of their command line is a value a worker chose.
        """
        if self.keep_open:
            raise SlotError("batch execution is disabled for the fixed Unity pool")
        if self.run_unsandboxed is None:
            raise SlotError(f"{slot['slot_id']}: no unsandboxed runner; a batch slot cannot be granted",
                            stage="editor")
        entry = self.entries.get(slot["slot_id"], DEFAULTS)
        state_dir = Path(self.state_dir(reservation["item_id"]))
        state_dir.mkdir(parents=True, exist_ok=True)
        evidence_dir = state_dir / "unity" / reservation["reservation_id"]
        evidence_dir.mkdir(parents=True, exist_ok=True)
        results, log = evidence_dir / "unity-tests.xml", evidence_dir / "unity-editor.log"
        results.unlink(missing_ok=True)   # never let a previous run's file be read as this run's evidence
        try:
            editor = editor_path(slot["folder"], override=entry.get("unity"))
        except UnityError as exc:
            # The message names every path tried. One re-queue at the tail is wasted on a misconfigured
            # host, and the second failure records the paths where an operator will read them.
            raise SlotError(f"{slot['slot_id']}: {exc}", stage="editor") from exc
        argv = batch_test_command(
            editor, slot["folder"], results=results, log=log,
            test_platform=entry.get("test_platform", "EditMode"),
            assemblies=tuple(entry.get("test_assemblies", ())),
            build_target=entry.get("build_target_argument"))
        # The last moment at which not starting is still free. Scheduler.stop can only kill an Editor that
        # has been registered, and the window before that registration is not the microsecond between Popen
        # and the dict write — it is `acquire` to here, and it contains the whole of switch(): a detached
        # checkout of a 3.7 GB repository, `git lfs checkout`, and on an open slot a graceful close. That is
        # minutes, on the normal path of every batch grant. A Stop landing in it used to find nothing to
        # kill and the Editor would then grind for up to batch_timeout — half an hour — on the host's only
        # slot, for an item nobody is waiting for any more.
        #
        # Returns rather than raises, deliberately: every arm of _switch_failed is wrong for a Stop. "probe"
        # would hold the slot until an operator ran recover-slot, and the others would either requeue the
        # reservation — handing the pool a second multi-minute switch for the same dead item — or fail an
        # item that is already terminal. Returning lets the hand-over reach resume()'s LedgerError, which is
        # the path that already handles a Stop landing mid-switch: release the reservation, fail nothing,
        # and park_idle returns the slot on the same tick.
        current = self.ledger.reservation(reservation["reservation_id"])
        if current is None or current["state"] != "active":
            return None
        # log=None: the Editor's own -logFile already points at `log`, and a second capture of the same
        # stream would give the worker two files that disagree about where the run stopped.
        # owner=: the only thread that could otherwise reach this Editor is this one, and it is about to
        # block in wait() for up to batch_timeout. Registering it under the item is what lets Scheduler.stop
        # and a service shutdown kill it.
        from .maintenance import Maintenance
        maintenance_deferred = False

        def deferred_summary():
            summary = {'state': 'deferred_maintenance', 'exit_code': None,
                       'commit_sha': reservation['commit_sha'], 'reservation_id': reservation['reservation_id'],
                       'results_file': None, 'log_file': None,
                       'result': 'Maintenance requested before batch launch; no test ran. Resume this exact pin.'}
            serialized = json.dumps(summary, indent=2)
            _write_worker_file(evidence_dir / 'unity-batch.json', serialized)
            _write_worker_file(state_dir / 'unity-batch.json', serialized)
            return summary

        def cancelled():
            nonlocal maintenance_deferred
            # An operator retry can clear the item's Stop fence while this old pool
            # invocation is still pending. Validate this reservation at the spawn
            # boundary, on the pool thread and its own SQLite connection.
            current = self.ledger.reservation(reservation["reservation_id"])
            maintenance_deferred = Maintenance(self.ledger).requested()
            return maintenance_deferred or current is None or current["state"] != "active"

        if Maintenance(self.ledger).requested():
            return deferred_summary()
        run = self.run_unsandboxed(argv, cwd=slot["folder"], timeout=entry["batch_timeout"], log=None,
                                   owner=reservation["item_id"], cancelled=cancelled)
        if maintenance_deferred:
            return deferred_summary()
        summary = {"state": "ran", "exit_code": run.returncode, "seconds": round(run.seconds, 1),
                   "commit_sha": reservation["commit_sha"], "reservation_id": reservation["reservation_id"],
                   "results_file": str(results), "log_file": str(log),
                   "total": None, "passed": None, "failed": None, "result": None}
        try:
            # Task 0 Step 4, the contract: the exit code is advisory and actively misleading — exit 0 means
            # *nothing ran*. The file decides, and total == 0 is a verification gap, not a pass.
            summary.update(read_results(results))
            if summary["total"] == 0:
                summary["state"] = "gap"
        except UnityError as exc:
            summary.update({"state": "gap", "result": str(exc)[:200]})
        if run.timed_out:
            summary["state"] = "timeout"
        # Written before the raise below, not after it: the operator who reads a held slot needs the record
        # of what happened on it, and a summary that only exists on the happy path is the one nobody has.
        serialized = json.dumps(summary, indent=2)
        _write_worker_file(evidence_dir / "unity-batch.json", serialized)
        _write_worker_file(state_dir / "unity-batch.json", serialized)
        if run.timed_out:
            self.clear_stale_lock(slot["folder"], lambda: self.editor_pid(slot["folder"]) is not None)
            if self.editor_pid(slot["folder"]) is not None:
                # A Unity the pool could not kill still holds the folder. Spec §7: a failing probe holds the
                # slot for the operator's recover-slot; there is no second slot to try.
                raise SlotError(f"{slot['slot_id']}: the batch Editor outlived its "
                                f"{entry['batch_timeout']}s deadline and still holds the folder",
                                stage="probe")
        return summary

    def editor_is_open(self, slot):
        """A live Unity process on this folder — never Temp/UnityLockfile.

        The lock file looks like the obvious answer and is the wrong one. Task 0 Step 5 measured it **still
        present at +32 s** after the Editor process was gone, and Task 4's own close_editor exists to delete
        it by hand for exactly that reason. Reading it here would make a stale lock say "open": switch()
        would skip open_editor, refresh() would hit an endpoint with no Editor behind it, wait_for_quiet
        would time out, and the single slot would end held at the probe stage after any crash, unreaped
        close or service restart. `agent.unity.editor_holds_project` asks the process listing instead and
        returns a pid; it is injected as `editor_pid` so no test in this suite shells out to pgrep."""
        return self.editor_pid(slot["folder"]) is not None

    def open_editor(self, slot, entry):
        """Start the Editor and wait until the MCP endpoint answers and names this folder's instance. The
        slot's startup deadline bounds the wait; the returned instance id is what the worker
        pins its own MCP session with."""
        try:
            from .unity_cache import archive_relocated_bee
            archive_relocated_bee(slot["folder"], editor_open=lambda: self.editor_is_open(slot))
            return self.mcp.start(slot, entry)
        except Exception as exc:
            raise self._collaborator_error(slot["slot_id"], exc, stage="editor",
                                           doing="starting the Editor") from exc

    def close_editor(self, slot, timeout=None):
        """Close the Editor and leave the folder and the port fit for the next run. Three parts, all measured
        in Task 0 Step 5, none of them optional:

        1. SIGTERM the Editor pid. `EditorApplication.Exit(0)` through execute_code is **not** used: it
           returns "exiting" in 0.1 s and the Editor keeps running, fully responsive, indefinitely. The
           signal works and the process was gone in 1 s.
        2. Remove Temp/UnityLockfile by hand, on *every* close and not only after a crash. Unity leaves it
           behind on an ordinary close (still present at +32 s), so there is nothing to wait for — an
           earlier draft of this method waited for it to disappear, which would have hung for ever on every
           batch run that followed an interactive one. Starting `Unity -batchmode` on a folder whose lock
           survives is the corruption the slot design exists to avoid.
        3. Reap the uvx MCP server from <slot>/Library/MCPForUnity/RunState/mcp_http_<port>.pid, where
           <port> is the slot's own mcp_address port (8080 here). It outlives the Editor, keeps that port
           bound and still answers HTTP 200, so the next open_editor would attach to a server whose Editor
           is gone and read somebody else's state — or none.
        """
        timeout = self.entries.get(slot["slot_id"], DEFAULTS)["close_timeout"] if timeout is None else timeout
        folder = Path(slot["folder"])
        try:
            self.mcp.terminate(slot, timeout)
        except Exception as exc:
            raise self._collaborator_error(slot["slot_id"], exc, stage="editor",
                                           doing=f"stopping the Editor within {timeout}s of SIGTERM") from exc
        # terminate() is supposed to have confirmed the pid is gone, but it is asked again rather than
        # asserted: `lambda: False` here would delete a live Editor's lock whenever terminate returned
        # without actually killing anything, and that file is what enforces one Editor per folder.
        self._clear_stale_lock(slot)
        if (folder / "Temp" / "UnityLockfile").exists():
            # Deliberately the file and not editor_is_open: what is checked here is that the litter Unity
            # leaves behind is actually gone, because the next `Unity -batchmode` on a folder whose lock
            # survives is the corruption slots prevent. Either the unlink failed or the Editor is still
            # alive, and the operator has to look at the folder in both cases.
            raise SlotError(f"{slot['slot_id']}: Temp/UnityLockfile is still there after the close; either "
                            f"the Editor survived the SIGTERM or the file could not be removed",
                            stage="editor")
        try:
            # HTTP instance selection is per session; multiple pool editors can share a broker.
            # Closing one editor must not disconnect a worker using another configured slot.
            peers = [other for other in self.ledger.slots(host=self.host)
                     if other["slot_id"] != slot["slot_id"] and other["slot_id"] in self.entries
                     and other["mcp_address"] == slot["mcp_address"]]
            shared_open = any(self.editor_is_open(other) for other in peers)
            if not shared_open:
                try:
                    shared_open = bool(self.another_editor_running(folder))
                except UnityError:
                    # Unknown consumers cannot justify terminating a shared broker.
                    shared_open = True
            if not shared_open:
                # The first editor owns the pidfile even if another editor is last to close.
                for owner in [slot, *peers]:
                    self.mcp.reap_server(owner)
        except Exception as exc:
            # A surviving server does fail the close, and deliberately so: the brief's own comment here read
            # "not worth failing the close over", which contradicted the raise below it. It IS worth it —
            # the next open_editor would attach to a server whose Editor is gone and read somebody else's
            # state, and the failure is far clearer here, where the slot id and the address are in hand.
            raise self._collaborator_error(
                slot["slot_id"], exc, stage="editor",
                doing=f"reaping the MCP server on {slot['mcp_address']} that outlived the Editor") from exc
        return True

    def refresh_and_wait(self, slot, timeout):
        """Wait across reloads before the Console boundary and after one refresh."""
        deadline = self.clock() + timeout
        self.wait_for_quiet(slot, timeout)
        try:
            self.mcp.refresh(slot)
        except UnityRefreshPending:
            # UnityIdentity retains the request time. A lost response is resolved
            # only by a later compiler/reload observation, never a second command.
            pass
        self.wait_for_quiet(slot, max(1.0, deadline - self.clock()))

    def wait_for_quiet(self, slot, timeout):
        """spec §7's 'wait for compilation'. refresh_unity returns at once, so probing straight afterwards
        normally observes is_compiling or is_domain_reload_pending and aggregates to unknown — which this plan
        routes to a permanent hold.

        Task 0 Step 5 measured three unfocused forced recompiles at 9.6 / 7.7 / 5.7 s, so the deadline is the
        only thing that decides: a sample is 'stalled' when it crosses the absolute timeout, never because it
        differs from another sample by some ratio. A no-op refresh is legitimately sub-second and would make
        any ratio rule fire on a healthy Editor. App Nap does not stall an unfocused Editor on this Mac.

        The loop polls on *any* collaborator failure and not only on TimeoutError, because `wait_quiet` is a
        listing call on a fresh slot: `switch` writes slots.instance only after the console read, so
        `UnityIdentity._client` re-discovers the instance from `mcpforunity://instances` on every sample, and
        an Editor is briefly absent from that listing during the domain reload `refresh_unity` triggers.
        `discover_instance` raises SlotError(stage="editor") there, which used to abort the switch on the one
        thing this loop exists to wait out — a requeue, or a failed item on the second try, per domain reload
        on the single-slot host. A bug in FarmBot's own code is not polled for: it answers the same way every
        second, and the operator needs its type rather than a 300-second "never went quiet".
        """
        deadline = self.clock() + timeout
        while True:
            try:
                self.mcp.wait_quiet(slot, max(1.0, deadline - self.clock()))
                return
            except SlotError.FARMBOT_FAULTS:
                raise
            except Exception as exc:
                if self.clock() < deadline:
                    self.sleep(1.0)
                    continue
                if isinstance(exc, TimeoutError):
                    raise SlotError(f"{slot['slot_id']}: still not quiet after {timeout}s: {exc}", stage="probe") from exc
                raise self._collaborator_error(slot["slot_id"], exc, stage="probe",
                                               doing=f"waiting for the Editor to go quiet within {timeout}s") from exc

    def another_editor_running(self, folder):
        """An outside Editor may share the broker; never reap it while one exists.

        The process listing lives in agent/unity.py, which is the only module allowed to know a host, and the
        whole call is injectable (`editor_scan`) so that no test in this suite shells out to pgrep — a
        developer with their own Editor open would otherwise fail every switch test on their machine.
        """
        return self.editor_scan(folder)

    def park(self, slot_id, mode):
        """When a slot becomes idle it goes back to the remote default head, so Library/ tracks main in small
        steps and the slot is ready for a baseline run (spec §7).

        The departing mode is an argument, not something read back from the slot row: in production park() is
        reached from park_idle(), by which time Ledger.release has already set the slot to 'switching', so a
        ternary on slot['state'] would always choose idle_closed and spec §7's "after an interactive run the
        Editor stays open, parked on main" would be unimplemented — while a unit test calling park() directly
        from batch_busy would still pass and hide it.
        """
        slot = self.ledger.slot(slot_id)
        if slot is None:
            raise SlotError(f"unknown slot: {slot_id}")
        entry = self.entries.get(slot_id, DEFAULTS)
        folder = Path(slot["folder"])
        try:
            if self.keep_open:
                # Retain the last verified candidate; returning to main between queued tests would
                # cause an unnecessary import/compile round before the next exact-commit switch.
                if not self.worktrees.slot_clean(folder):
                    raise SlotError(f"{slot_id}: dirty slot source retained for recovery")
                commit = self.worktrees.head(folder)
            else:
                commit = self.worktrees.resolve_commit(entry["repo"])
            if self.worktrees.head(folder) != commit:
                self.worktrees.checkout_commit(folder, commit)
        except GIT_FAILURES as exc:
            raise SlotError(f"{slot_id}: could not park on the default branch: {exc}", stage="git") from exc
        if self.keep_open:
            self.ledger.set_slot_state(slot_id, "switching", parked_commit=commit)
        state = self._idle_state(slot, mode)
        if state == "idle_closed":
            # Never `lambda: False`. spec §7 removes the lock "only after confirming the process is gone",
            # and a departing mode of "batch" says nothing about the host: park(slot, "batch") against a
            # live Editor would delete the very file that enforces Unity's one-Editor-per-folder guarantee.
            self._clear_stale_lock(slot)
        return self.ledger.set_slot_state(slot_id, state, parked_commit=commit, last_switch_at=self.clock())

    def _idle_state(self, slot, mode):
        """What an idle slot's row should say, asked and never assumed: idle_open only when an interactive
        run left an Editor that is really still on the folder. One spelling, because park() and the
        give-back in _switch_failed must not disagree about it — they used to, and the disagreement was
        `idle_closed` written over a live Editor."""
        try:
            return "idle_open" if mode == "interactive" and self.editor_is_open(slot) else "idle_closed"
        except UnityError as exc:
            raise SlotError(f"{slot['slot_id']}: Editor process observation unavailable: {exc}", stage="probe") from exc

    def _clear_stale_lock(self, slot):
        """clear_stale_lock on the slot's folder, with the process listing deciding. An unlink that fails (on
        Windows, a sharing violation) leaves the folder claimed, which close_editor already treats as an
        editor-stage failure: the slot is held rather than released, and no OSError escapes the pool."""
        try:
            return self.clear_stale_lock(slot["folder"], lambda: self.editor_is_open(slot))
        except OSError as exc:
            raise SlotError(f"{slot['slot_id']}: Temp/UnityLockfile could not be removed: {exc}",
                            stage="editor") from exc

    @staticmethod
    def clear_stale_lock(folder, alive):
        """An Editor that died mid-run leaves Unity's lock file behind; it is removed only after the process
        is confirmed gone (spec §7 line 353).

        `alive` is a real check at every call site and never `lambda: False`. Asserting it instead of asking
        deletes a *live* Editor's lock, and that file is what enforces Unity's one-Editor-per-folder
        guarantee — losing it risks two Editors on one folder, the corruption slots exist to prevent. The
        callers are the batch branch of switch(), the start path of switch() before open_editor(),
        close_editor() after its SIGTERM, park() on the closed path, and settle() after a batch release;
        every one of them passes `lambda: self.editor_is_open(slot)`, so the process listing decides each
        time. settle() is the one that looks like an exception and is not: its quiescence check has just
        asked the same question, but that check is `True` by default when the pool has no MCP client."""
        lock = Path(folder) / "Temp" / "UnityLockfile"
        if not lock.exists() or alive():
            return False
        lock.unlink()
        return True


class UnityIdentity:
    """The pool's `mcp` collaborator in production: one MCP session per call, pinned to the slot's instance,
    plus the Editor's own life cycle, because starting and closing it is what the state table in spec §7
    requires and nothing else in FarmBot does it.

    The endpoint is not a constructor argument: it is read per call from the slot row's `mcp_address`, so
    one instance serves every slot.
    """

    def __init__(self, probe_path, timeout=120, start_timeout=300, clock=time.time, sleep=time.sleep,
                 token_env=None, secret_env=None, console_evidence_root=None):
        self.probe_path = Path(probe_path)
        self.timeout = timeout
        self.token_env = token_env
        self.secret_env = secret_env
        self.console_evidence_root = Path(console_evidence_root) if console_evidence_root else None
        # A Windows slot measured 170 seconds to load. Each configured slot may
        # override the five-minute default without changing MCP request timeouts.
        self.start_timeout = start_timeout
        self.clock = clock
        self.sleep = sleep
        self._refresh_pending = {}

    def _client(self, slot):
        """One MCP session, pinned to this slot's Editor before anything else is asked of it.

        The pin is never skipped, and that is the whole point of the method. `SlotPool.switch` writes
        slots.instance only *after* the console read, so on a fresh slot's first interactive switch
        `refresh`, `wait_quiet` and `console_errors_since` all arrive with a NULL row — precisely the three
        calls that decide whether the pinned commit compiled. Skipping the pin there, as an earlier draft
        did, lets the compile gate read whichever Editor the server routes to by default. Refusing instead
        would be no better: it would break that first switch outright, because the id genuinely is not in the
        row yet. So a missing id is resolved from the live server exactly as `start` resolved it, and the
        session is pinned either way. Even a recorded id must still match this
        folder in the live listing before any editor command is sent.
        """
        client = UnityMcp(slot["mcp_address"], timeout=self.timeout)
        instance = self.discover_instance(slot, client=client)
        if slot.get("instance") and slot["instance"] != instance:
            raise SlotError(f"{slot['slot_id']}: recorded instance does not match the configured project", stage="probe")
        client.select_instance(instance)
        return client

    def discover_instance(self, slot, *, client=None):
        """The instance id for this folder, read from the live server rather than assumed.

        Nothing else writes slots.instance, and without it the whole interactive path is dead: collect()
        passes the instance to ready(), which compares it against unity.instance_id, so a NULL instance makes
        ready() False, the aggregate unknown and every interactive switch a probe-stage hold. The plugin
        derives the id as SHA1(Application.dataPath) truncated to 16 hex characters
        (Editor/Helpers/ProjectIdentityUtility.cs) and Task 0 Step 5 settled the exact input:
        sha1("<folder>/Assets")[:16] — no trailing slash — reproduced the live slot-1@54462c1bfe7b5261
        exactly, while sha1("<folder>") and sha1("<folder>/Assets/") did not.

        A reported path is still preferred over that rule, because a folder that is moved or symlinked would
        make a recomputed hash confidently wrong. But the installed server settles it: in
        mcpforunityserver 10.1.0's services/resources/unity_instances.py the HTTP branch — the transport
        FarmBot uses — builds each entry as id/name/hash/unity_version/connected_at/session_id, and its own
        docstring marks `path` "stdio only". A path match alone would therefore never fire here, and every
        interactive switch would spin out `start`'s deadline. The hash is the documented fallback, and
        it stays safe because it is only ever used to pick an id out of the list the server itself returned:
        a folder the Editor does not actually hold produces no match and raises, rather than addressing
        somebody else's Editor.
        """
        folder = Path(slot["folder"]).resolve()
        client = client or UnityMcp(slot["mcp_address"], timeout=self.timeout)
        listed = client.read_resource("mcpforunity://instances").get("instances", [])
        for entry in listed:
            for key in ("projectPath", "dataPath", "path"):
                value = entry.get(key)
                if value and Path(value).resolve() in (folder, folder / "Assets"):
                    return entry.get("id")
        # Both spellings, because the Editor hashes its own Application.dataPath: that is the -projectPath
        # `start` handed it, which is the slot row's text, but a host whose slot path runs through a symlink
        # (/var -> /private/var on this Mac) would have Unity report the resolved one.
        # Unity's Application.dataPath uses forward slashes on Windows as well.
        digests = {hashlib.sha1((path / "Assets").as_posix().encode("utf-8")).hexdigest()[:16]
                   for path in (Path(slot["folder"]), folder)}
        for entry in listed:
            if entry.get("hash") in digests or str(entry.get("id", "")).rpartition("@")[2] in digests:
                return entry.get("id")
        raise SlotError(f"no connected Editor instance reports {folder}", stage="editor")

    def start(self, slot, entry):
        """Start the Editor for an interactive run and wait until the MCP server reports this folder.

        The endpoint is served by a uvx process, not by the Editor itself, but Task 0 Step 5 established that
        the Editor **starts it** — with --project-scoped-tools and a per-slot pidfile — so no operator action
        and no separate agent is needed. What must still read as an editor-stage failure rather than an
        identity mismatch is the other direction: a server left behind by a previous Editor still answers on
        that address, which is why close_editor reaps it and why the message below names both possibilities.
        """
        command = [str(editor_path(slot["folder"], override=entry.get("unity"))), "-projectPath",
                   str(slot["folder"]), "-logFile", str(Path(slot["folder"]) / "Logs" / "farmbot-editor.log")]
        subprocess.Popen(command, start_new_session=True, env=child_environment(self.token_env, secret_env=self.secret_env),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        start_timeout = entry.get("start_timeout", self.start_timeout)
        deadline = self.clock() + start_timeout
        while True:
            try:
                return self.discover_instance(slot)
            except Exception:
                if self.clock() >= deadline:
                    raise SlotError(f"{slot['slot_id']}: no MCP instance after {start_timeout}s; "
                                    f"Editor startup or MCP connection did not finish at {slot['mcp_address']}",
                                    stage="editor")
                self.sleep(5.0)

    def terminate(self, slot, timeout):
        """Task 0 Step 5 settled this: `EditorApplication.Exit(0)` through execute_code returns "exiting" and
        the Editor keeps running, so it is not used at all. SIGTERM the pid that holds the folder — measured
        gone in 1 s — and confirm it. Removing Temp/UnityLockfile is the pool's job, not this method's,
        because Unity leaves it behind even here."""
        pid = editor_holds_project(slot["folder"], strict=True)
        if pid is None:
            return
        os.kill(pid, signal.SIGTERM)
        deadline = self.clock() + timeout
        while True:
            try:
                if editor_holds_project(slot['folder'], strict=True) is None:
                    return
            except UnityError:
                # A quitting Windows process can briefly lose its command line.
                # Unknown remains fenced until a later strict observation proves exit.
                if self.clock() >= deadline:
                    raise
            if self.clock() >= deadline:
                raise SlotError(f"Unity pid {pid} still holds {slot['folder']} {timeout}s after SIGTERM",
                                stage="editor")
            self.sleep(1.0)

    @contextmanager
    def suspend_imports(self, slot):
        """Only an idle, reservation-detached Editor may enter this controller-owned section."""
        client = self._client(slot)
        def execute(method):
            result = client.call_tool('execute_code', {'action': 'execute', 'safety_checks': True,
                'code': f'UnityEditor.AssetDatabase.{method}(); return "{method}";'})
            if result.get('result') != method:
                raise SlotError(f'Editor did not confirm {method}', stage='editor')
        execute('DisallowAutoRefresh')
        try:
            yield
        finally:
            execute('AllowAutoRefresh')

    def recovery_snapshot(self, slot):
        client = self._client(slot)
        state = client.read_resource('mcpforunity://editor/state')
        snapshot = {'state': state}
        job_id = (state.get('tests') or {}).get('current_job_id')
        if job_id:
            snapshot['job'] = client.call_tool('get_test_job', {'job_id': job_id, 'wait_timeout': 0})
        try:
            snapshot['console'] = client.call_tool('read_console', {'action': 'get', 'types': ['error'],
                                                                   'count': '10', 'include_stacktrace': True})
        except Exception as exc:
            # Auxiliary diagnostics must not turn a progressing test into an
            # unavailable editor and eventually trigger a false watchdog hold.
            snapshot['console_error'] = f'{type(exc).__name__}: {exc}'[:500]
        return snapshot

    def cancel_tests(self, slot):
        # Exiting Play Mode is the supported cooperative stop available across
        # the deployed Unity test-framework versions. Never clear a live job.
        return self._client(slot).call_tool('manage_editor', {'action': 'stop'})

    def reap_server(self, slot):
        """The uvx MCP server is a child of the Editor but outlives it: after the kill it still held port
        8080 and answered HTTP 200 (Task 0 Step 5). Its pid is in the slot's own RunState pidfile, which is
        also why one host can eventually run two slots. A missing or stale pidfile is not an error.

        The pidfile's name is **derived from the port**, not hardcoded: the spike saw
        `--pidfile <slot>/Library/MCPForUnity/RunState/mcp_http_8080.pid` beside
        `--http-url http://127.0.0.1:8080`, and `mcp_address` is a configuration key (DEFAULTS, Task 3). A
        second slot on another port would otherwise never be reaped, and a stale server holding that port
        is exactly what this method exists to prevent."""
        port = urllib.parse.urlsplit(slot["mcp_address"]).port or 8080
        pidfile = (Path(slot["folder"]) / "Library" / "MCPForUnity" / "RunState"
                   / f"mcp_http_{port}.pid")
        try:
            pid = int(pidfile.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        pidfile.unlink(missing_ok=True)

    def refresh(self, slot):
        client = self._client(slot)
        state = client.read_resource('mcpforunity://editor/state')
        if (state.get('tests') or {}).get('is_running') is not False:
            raise SlotError('Console boundary requires a verified inactive test runner', stage='probe')
        play = (state.get('editor') or {}).get('play_mode') or {}
        if play.get('is_playing') is not False or play.get('is_changing') is not False:
            raise SlotError('Console boundary requires verified Edit Mode', stage='probe')
        previous = client.call_tool('read_console',
                                   {'action': 'get', 'types': ['error'], 'count': 50,
                                    'include_stacktrace': True})
        self._console_lines(slot, previous)  # refuse an unknown payload before clearing anything
        root = self.console_evidence_root or Path(slot['folder']) / 'Logs' / 'farmbot-console'
        root.mkdir(parents=True, exist_ok=True)
        (root / f'{uuid4().hex}.json').write_text(json.dumps(
            {'slot_id': slot['slot_id'], 'instance': slot.get('instance'),
             'observed_at': self.clock(), 'console': previous}, ensure_ascii=False, indent=2), encoding='utf-8')
        client.call_tool('read_console', {'action': 'clear'})
        # A reused, already-broken candidate may produce no new compiler output on
        # a no-op refresh. Request compilation so clearing history cannot hide it.
        pending = {'started_ms': int(self.clock() * 1000), 'error': None}
        self._refresh_pending[slot['slot_id']] = pending
        try:
            # A request awaiting readiness inside Unity loses its callback/socket
            # during domain reload. Return before reload and poll from the host.
            client.call_tool('refresh_unity', {'compile': 'request', 'wait_for_ready': False})
        except (OSError, ValueError) as exc:
            pending['error'] = f'{type(exc).__name__}: {exc}'
            raise UnityRefreshPending(pending['error']) from exc

    def wait_quiet(self, slot, timeout):
        """One sample of editor/state. The pool's wait_for_quiet owns the loop and the deadline; this raises
        TimeoutError when the Editor is still compiling, reloading, importing or running tests."""
        state = self._client(slot).read_resource("mcpforunity://editor/state")
        if not quiet(state):
            raise TimeoutError("editor is not quiet")
        pending = self._refresh_pending.get(slot['slot_id'])
        if pending:
            # An async request can return before compilation even starts. Old idle
            # state is not completion, including when the command response was lost.
            compilation = state.get('compilation') or {}
            observed = [compilation.get('last_compile_finished_unix_ms'),
                        compilation.get('last_domain_reload_after_unix_ms')]
            if not any(type(value) in (int, float) and math.isfinite(value)
                       and pending['started_ms'] <= value <= self.clock() * 1000
                       for value in observed):
                raise TimeoutError('no completed compilation/reload observed after refresh request'
                                   + (f'; {pending["error"]}' if pending['error'] else ''))
            del self._refresh_pending[slot['slot_id']]

    CONSOLE_KEYS = ("lines", "items", "entries")

    def console_errors_since(self, slot, marker):
        """spec §7's 'zero Console errors'. Reads the Editor's error entries and returns the offending ones.

        The payload shape is the installed plugin's rather than a guess. With `count` and no paging, the C#
        handler returns the formatted entries as a **bare list** under `data`
        (com.coplaydev.unity-mcp/Editor/Tools/ReadConsole.cs), so the brief's `result.get("lines", [])`
        would have raised AttributeError on a list at the first interactive switch — and `SlotPool.switch`
        does not wrap this call, so it would not even have become a SlotError with a stage. Under paging the
        same entries arrive as `items`, and the server's own stacktrace stripper also knows `lines`, so all
        three spellings are accepted. An entry is a string in the default 'plain' format and a dict with a
        `message` in 'json'/'detailed'.

        A shape none of that covers raises rather than returning []: silently reporting a clean console is
        how a commit that does not compile reaches a worker with the pool's blessing.

        refresh() archives and clears history before requesting compilation. This
        reads only the new check's errors. Read-only late-start validation does not
        establish a new boundary and conservatively inspects retained errors.
        """
        result = self._client(slot).call_tool("read_console",
                                              {"action": "get", "types": ["error"], "count": 50})
        return [line for line in self._console_lines(slot, result) if "error CS" in line or "Exception" in line]

    def _console_lines(self, slot, result):
        if isinstance(result, list):
            entries = result
        elif isinstance(result, dict):
            entries = next((result[key] for key in self.CONSOLE_KEYS if isinstance(result.get(key), list)), None)
            if entries is None:
                raise SlotError(f"{slot['slot_id']}: read_console returned no recognisable entry list "
                                f"({sorted(result)}); FarmBot cannot certify the Console clean and must be "
                                f"taught this shape — recover-slot will not help", stage="probe",
                                fault="farmbot")
        else:
            # Both raises are "farmbot", not "external". Unity answered; it is this reader that does not
            # understand the answer, and that is precisely the distinction `fault` exists to draw: sending
            # an operator to recover-slot for a payload FarmBot has not been taught wastes their time.
            raise SlotError(f"{slot['slot_id']}: read_console returned {type(result).__name__}, not entries",
                            stage="probe", fault="farmbot")
        lines = [entry if isinstance(entry, str) else str(entry.get("message", entry))
                 for entry in entries if isinstance(entry, (str, dict))]
        return lines

    def probe(self, slot, target):
        client = self._client(slot)
        return collect(client, repository=target["repository"], instance=slot.get("instance"),
                       expected={"build_target": target.get("build_target"),
                                 "commit_sha": target["commit_sha"],
                                 "assemblies": ("HotUpdate", "AOTScripts", "Nova.Runtime", "MCPForUnity.Editor")},
                       probe_source=self.probe_path.read_text(encoding="utf-8"))

    def quiescent(self, slot, mode):
        """Spec §7's release predicate.

        In interactive mode: the Editor is the one this slot pinned, it is back in Edit Mode, and nothing is
        compiling, reloading, importing or running tests. The sample is no longer required to be recent — see
        `identity.ready`, where that bound refused idle Editors — and the liveness it was standing in for is
        supplied by the read itself, which an Editor that cannot reach its main thread never answers at all.

        In batch mode it is **not** a constant True — the batch release is defined as the Editor process
        being gone and its results file written, and returning True regardless would let park_idle run
        `git checkout --force` and `git lfs checkout` over a folder that still held Temp/UnityLockfile and a
        half-written Library/. That is precisely the corruption slots exist to avoid, and it would read as a
        project problem rather than a pool bug.
        """
        if mode == "batch":
            # A live Unity on this folder holds the slot; a dead one leaves only a stale lock, which
            # SlotPool.settle clears through clear_stale_lock before anything checks the folder out.
            return not editor_holds_project(slot["folder"])
        state = self._client(slot).read_resource("mcpforunity://editor/state")
        return ready(state, slot.get("instance"))
