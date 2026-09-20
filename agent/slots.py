"""The Unity slot pool: long-lived project folders Unity is pointed at, and nothing else (spec §7).

A slot is a detached worktree of FarmBot's own clone with a built Library/. Task worktrees are for code and
never open Unity. Everything host-specific lives in the slot's configuration entry or in agent/unity.py.
"""
import hashlib
import os
import signal
import subprocess
import time
import urllib.parse
from pathlib import Path

from .identity import collect, quiet, ready
from .unity import editor_holds_project, editor_path, other_editor_project
from .unity_mcp import UnityMcp
from .worktrees import WorktreeError

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
    "close_timeout": 60,
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
        # "git" and "editor" may be retried once at the tail of the queue; "compile" fails the item and leaves
        # the slot in the pool; "probe" holds the slot for the operator's recover-slot (spec §7).
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
    entry["test_assemblies"] = tuple(entry["test_assemblies"])
    return entry


class SlotPool:
    BUSY = ("interactive_busy", "batch_busy", "switching", "held")

    BUSY_FOR = {"interactive": "interactive_busy", "batch": "batch_busy"}

    def __init__(self, ledger, worktrees, entries, *, host, editors_root, unity=None, mcp=None, clock=time.time,
                 sleep=None, editor_scan=None, editor_pid=None):
        self.ledger = ledger
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
        self.editor_scan = editor_scan or other_editor_project
        self.editor_pid = editor_pid or editor_holds_project
        self.last_observation = None

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
        views = []
        for slot_id, entry in self.entries.items():
            folder = self.folder(entry)
            # str(): Ledger.ensure_slot validates its folder as text before it stores it, so a Path is refused.
            record = self.ledger.ensure_slot(slot_id, kind=entry["kind"], host=self.host, folder=str(folder),
                                             mcp_address=entry["mcp_address"], account=entry["account"],
                                             instance=entry["instance"])
            if record["state"] in self.BUSY:
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
            except WorktreeError as exc:
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
        return views

    def switch(self, slot_id, commit, mode):
        """Spec §7's sequence, in order. The caller has already marked the slot 'switching' by acquiring it.

        `was_open` therefore cannot come from the slot row — `acquire` has already overwritten its state with
        the transient 'switching'. It is a question about the host: is a Unity process alive on this folder?
        See `editor_is_open` for why it is not a question about Temp/UnityLockfile.
        """
        slot = self.ledger.slot(slot_id)
        if slot is None:
            raise SlotError(f"unknown slot: {slot_id}")
        folder = Path(slot["folder"])
        entry = self.entries.get(slot_id, DEFAULTS)
        was_open = bool(self.mcp is not None and self.editor_is_open(slot))
        other = self.another_editor_running(folder)
        if other:
            # The licence is a paid serial and resolves fine both foreground and under launchd (Task 0 Step
            # 6), but two Editors contending for it was the one case the spike could not run, because the
            # operator had none open. Until that is measured, do not discover it during a graded item: a
            # lost licence race reports as something that reads like project corruption, so hold the slot
            # with a sentence a human can act on instead.
            raise SlotError(f"{slot_id}: another Unity Editor is open on {other}; close it, then "
                            f"`recover-slot --slot {slot_id}`", stage="probe")
        try:
            self.worktrees.fetch(entry["repo"])   # the pin may post-date the clone's last fetch
            if not self.worktrees.slot_clean(folder):
                raise SlotError(f"{slot_id}: tracked files are dirty; a human edited the slot folder")
            if mode == "batch" and was_open:
                # spec §7: a batch request on an idle-open slot is accepted "after a graceful close".
                self.close_editor(slot)
            self.worktrees.checkout_commit(folder, commit)
            if self.worktrees.pointers_remain(folder):
                raise SlotError(f"{slot_id}: git-lfs pointers survived the checkout")
        except WorktreeError as exc:
            raise SlotError(f"{slot_id}: git stage failed: {exc}", stage="git") from exc
        if mode == "batch":
            # A batch run needs no MCP at all; its result is a results file and an exit code (spec §7). The
            # only thing it needs from the pool is a folder no dead Editor still claims. The liveness check
            # is the process listing and never `lambda: False`: spec §7 removes the lock "only after
            # confirming the process is gone", and `was_open` above is guarded by `self.mcp is not None`, so
            # a pool built without an MCP client would otherwise assert "gone" without ever asking.
            self.clear_stale_lock(folder, lambda: self.editor_is_open(slot))
            return self.ledger.set_slot_state(slot_id, self.BUSY_FOR[mode], last_switch_at=self.clock())
        instance = slot.get("instance")
        if not was_open:
            # A crash or an unreaped close leaves Unity's lock behind (Task 0 Step 5: still present at
            # +32 s). Nothing holds this folder, so the litter goes before the new Editor can meet it —
            # otherwise a surviving lock blocks the start and holds the only slot at the probe stage after
            # any crash. Same guard as everywhere else: the process listing decides, not an assertion.
            self.clear_stale_lock(folder, lambda: self.editor_is_open(slot))
            instance = self.open_editor(slot, entry)
        try:
            self.mcp.refresh(slot)
            self.wait_for_quiet(slot, entry["quiet_timeout"])
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
        if observation.get("aggregate") != "match":
            raise SlotError(f"{slot_id}: identity probe did not match: {observation.get('checks')}",
                            stage="probe")
        self.last_observation = observation
        return self.ledger.set_slot_state(slot_id, self.BUSY_FOR[mode], last_switch_at=self.clock())

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
        timeout is the cold-start figure Task 0 Step 5 measured; the returned instance id is what the worker
        pins its own MCP session with."""
        try:
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
        self.clear_stale_lock(folder, lambda: self.editor_is_open(slot))
        if (folder / "Temp" / "UnityLockfile").exists():
            # Deliberately the file and not editor_is_open: what is checked here is that the litter Unity
            # leaves behind is actually gone, because the next `Unity -batchmode` on a folder whose lock
            # survives is the corruption slots prevent. Either the unlink failed or the Editor is still
            # alive, and the operator has to look at the folder in both cases.
            raise SlotError(f"{slot['slot_id']}: Temp/UnityLockfile is still there after the close; either "
                            f"the Editor survived the SIGTERM or the file could not be removed",
                            stage="editor")
        try:
            self.mcp.reap_server(slot)
        except Exception as exc:
            # A surviving server does fail the close, and deliberately so: the brief's own comment here read
            # "not worth failing the close over", which contradicted the raise below it. It IS worth it —
            # the next open_editor would attach to a server whose Editor is gone and read somebody else's
            # state, and the failure is far clearer here, where the slot id and the address are in hand.
            raise self._collaborator_error(
                slot["slot_id"], exc, stage="editor",
                doing=f"reaping the MCP server on {slot['mcp_address']} that outlived the Editor") from exc
        return True

    def wait_for_quiet(self, slot, timeout):
        """spec §7's 'wait for compilation'. refresh_unity returns at once, so probing straight afterwards
        normally observes is_compiling or is_domain_reload_pending and aggregates to unknown — which this plan
        routes to a permanent hold.

        Task 0 Step 5 measured three unfocused forced recompiles at 9.6 / 7.7 / 5.7 s, so the deadline is the
        only thing that decides: a sample is 'stalled' when it crosses the absolute timeout, never because it
        differs from another sample by some ratio. A no-op refresh is legitimately sub-second and would make
        any ratio rule fire on a healthy Editor. App Nap does not stall an unfocused Editor on this Mac."""
        deadline = self.clock() + timeout
        while True:
            try:
                self.mcp.wait_quiet(slot, max(1.0, deadline - self.clock()))
                return
            except TimeoutError:
                if self.clock() >= deadline:
                    raise SlotError(f"{slot['slot_id']}: still not quiet after {timeout}s", stage="probe")
                self.sleep(1.0)

    def another_editor_running(self, folder):
        """A Unity Editor on this host that is not this slot's. Returns the other project path, or None.

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
            commit = self.worktrees.resolve_commit(entry["repo"])
            if self.worktrees.head(folder) != commit:
                self.worktrees.checkout_commit(folder, commit)
        except WorktreeError as exc:
            raise SlotError(f"{slot_id}: could not park on the default branch: {exc}", stage="git") from exc
        state = "idle_open" if mode == "interactive" and self.editor_is_open(slot) else "idle_closed"
        if state == "idle_closed":
            # Never `lambda: False`. spec §7 removes the lock "only after confirming the process is gone",
            # and a departing mode of "batch" says nothing about the host: park(slot, "batch") against a
            # live Editor would delete the very file that enforces Unity's one-Editor-per-folder guarantee.
            self.clear_stale_lock(folder, lambda: self.editor_is_open(slot))
        return self.ledger.set_slot_state(slot_id, state, parked_commit=commit, last_switch_at=self.clock())

    @staticmethod
    def clear_stale_lock(folder, alive):
        """An Editor that died mid-run leaves Unity's lock file behind; it is removed only after the process
        is confirmed gone (spec §7 line 353).

        `alive` is a real check at every call site and never `lambda: False`. Asserting it instead of asking
        deletes a *live* Editor's lock, and that file is what enforces Unity's one-Editor-per-folder
        guarantee — losing it risks two Editors on one folder, the corruption slots exist to prevent. The
        callers are the batch branch of switch(), the start path of switch() before open_editor(),
        close_editor() after its SIGTERM, park() on the closed path, and settle() in Task 6; every one of
        them passes `lambda: self.editor_is_open(slot)`, so the process listing decides each time."""
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

    def __init__(self, probe_path, timeout=120, start_timeout=120, clock=time.time, sleep=time.sleep):
        self.probe_path = Path(probe_path)
        self.timeout = timeout
        # Task 0 Step 5 measured cold Editor start to a usable MCP endpoint at 16 s. 120 is generous headroom
        # over a measured number; the 600 an earlier draft carried was a guess at an unmeasured one.
        self.start_timeout = start_timeout
        self.clock = clock
        self.sleep = sleep

    def _client(self, slot):
        client = UnityMcp(slot["mcp_address"], timeout=self.timeout)
        if slot.get("instance"):
            client.select_instance(slot["instance"])
        return client

    def discover_instance(self, slot):
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
        listed = UnityMcp(slot["mcp_address"], timeout=self.timeout)\
            .read_resource("mcpforunity://instances").get("instances", [])
        for entry in listed:
            for key in ("projectPath", "dataPath", "path"):
                value = entry.get(key)
                if value and Path(value).resolve() in (folder, folder / "Assets"):
                    return entry.get("id")
        # Both spellings, because the Editor hashes its own Application.dataPath: that is the -projectPath
        # `start` handed it, which is the slot row's text, but a host whose slot path runs through a symlink
        # (/var -> /private/var on this Mac) would have Unity report the resolved one.
        digests = {hashlib.sha1(f"{path}/Assets".encode("utf-8")).hexdigest()[:16]
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
        subprocess.Popen(command, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = self.clock() + self.start_timeout
        while True:
            try:
                return self.discover_instance(slot)
            except Exception:
                if self.clock() >= deadline:
                    raise SlotError(f"{slot['slot_id']}: no MCP instance after {self.start_timeout}s "
                                    f"(measured cold start is 16s); either the Editor did not come up, or a "
                                    f"stale server from a previous Editor still holds {slot['mcp_address']}",
                                    stage="editor")
                self.sleep(5.0)

    def terminate(self, slot, timeout):
        """Task 0 Step 5 settled this: `EditorApplication.Exit(0)` through execute_code returns "exiting" and
        the Editor keeps running, so it is not used at all. SIGTERM the pid that holds the folder — measured
        gone in 1 s — and confirm it. Removing Temp/UnityLockfile is the pool's job, not this method's,
        because Unity leaves it behind even here."""
        pid = editor_holds_project(slot["folder"])
        if pid is None:
            return
        os.kill(pid, signal.SIGTERM)
        deadline = self.clock() + timeout
        while editor_holds_project(slot["folder"]) is not None:
            if self.clock() >= deadline:
                raise SlotError(f"Unity pid {pid} still holds {slot['folder']} {timeout}s after SIGTERM",
                                stage="editor")
            self.sleep(1.0)

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
        self._client(slot).call_tool("refresh_unity", {})

    def wait_quiet(self, slot, timeout):
        """One sample of editor/state. The pool's wait_for_quiet owns the loop and the deadline; this raises
        TimeoutError when the Editor is still compiling, reloading, importing or running tests."""
        if not quiet(self._client(slot).read_resource("mcpforunity://editor/state")):
            raise TimeoutError("editor is not quiet")

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

        `marker` is accepted and deliberately unused. Nothing clears the Console between switches, so this
        cannot yet be "since" anything; it reads the most recent 50 errors. Making it truly incremental
        needs a `read_console` clear before the refresh, which belongs with the tool injection in Task 7.
        """
        result = self._client(slot).call_tool("read_console",
                                              {"action": "get", "types": ["error"], "count": 50})
        if isinstance(result, list):
            entries = result
        elif isinstance(result, dict):
            entries = next((result[key] for key in self.CONSOLE_KEYS if isinstance(result.get(key), list)), None)
            if entries is None:
                raise SlotError(f"{slot['slot_id']}: read_console returned no recognisable entry list "
                                f"({sorted(result)}); the Console cannot be certified clean", stage="probe")
        else:
            raise SlotError(f"{slot['slot_id']}: read_console returned {type(result).__name__}, not entries",
                            stage="probe")
        lines = [entry if isinstance(entry, str) else str(entry.get("message", entry))
                 for entry in entries if isinstance(entry, (str, dict))]
        return [line for line in lines if "error CS" in line or "Exception" in line]

    def probe(self, slot, target):
        client = self._client(slot)
        return collect(client, repository=target["repository"], instance=slot.get("instance"),
                       expected={"build_target": target.get("build_target"),
                                 "commit_sha": target["commit_sha"],
                                 "assemblies": ("HotUpdate", "AOTScripts", "Nova.Runtime", "MCPForUnity.Editor")},
                       probe_source=self.probe_path.read_text(encoding="utf-8"))

    def quiescent(self, slot, mode):
        """Spec §7's release predicate.

        In interactive mode: the Editor is back in Edit Mode, nothing is compiling, importing or running
        tests, and the state sample is fresh. In batch mode it is **not** a constant True — the batch release
        is defined as the Editor process being gone and its results file written, and returning True
        regardless would let park_idle run `git checkout --force` and `git lfs checkout` over a folder that
        still held Temp/UnityLockfile and a half-written Library/. That is precisely the corruption slots
        exist to avoid, and it would read as a project problem rather than a pool bug.
        """
        if mode == "batch":
            # A live Unity on this folder holds the slot; a dead one leaves only a stale lock, which
            # SlotPool.settle clears through clear_stale_lock before anything checks the folder out.
            return not editor_holds_project(slot["folder"])
        state = self._client(slot).read_resource("mcpforunity://editor/state")
        return ready(state, slot.get("instance"))
