"""The Unity slot pool: long-lived project folders Unity is pointed at, and nothing else (spec §7).

A slot is a detached worktree of FarmBot's own clone with a built Library/. Task worktrees are for code and
never open Unity. Everything host-specific lives in the slot's configuration entry or in agent/unity.py.
"""
from pathlib import Path
import time

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
}


class SlotError(RuntimeError):
    pass


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

    def __init__(self, ledger, worktrees, entries, *, host, editors_root, unity=None, mcp=None, clock=time.time):
        self.ledger = ledger
        self.worktrees = worktrees
        self.entries = {entry["id"]: entry for entry in entries}
        self.host = host
        self.editors_root = Path(editors_root)
        self.unity = unity
        self.mcp = mcp
        self.clock = clock

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
