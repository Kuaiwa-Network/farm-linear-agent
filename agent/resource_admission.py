"""Conservative shared admission for heavy resources; process containment remains separate."""
import ctypes
import os
import subprocess
import threading

MIB = 1024 * 1024
DEFAULTS = {"max_reserved_mib": 16384, "min_available_mib": 4096,
            "min_commit_mib": 4096, "unity_mib": 4096, "server_mib": 4608}


def validate_settings(raw):
    if not isinstance(raw, dict) or set(raw) - set(DEFAULTS):
        raise ValueError("resource_admission accepts memory budgets in MiB only")
    settings = {**DEFAULTS, **raw}
    if any(type(value) is not int or not 1 <= value <= 1024 * 1024 for value in settings.values()):
        raise ValueError("resource_admission budgets must be positive integer MiB")
    if max(settings["unity_mib"], settings["server_mib"]) > settings["max_reserved_mib"]:
        raise ValueError("a heavy resource exceeds the entire reservation budget")
    return settings


def host_memory():
    """Available physical and commit headroom. A failed observation never means unlimited RAM."""
    if os.name == "nt":
        class Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                        *[(name, ctypes.c_ulonglong) for name in
                          ("total", "available", "commit_total", "commit_available",
                           "virtual_total", "virtual_available", "extended")]]
        status = Status()
        status.length = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError("Windows memory observation failed")
        return {"available_mib": status.available // MIB, "commit_mib": status.commit_available // MIB}
    if os.uname().sysname == "Darwin":
        # Count free + inactive pages only: compressed and speculative memory are not admission credit.
        output = subprocess.check_output(["/usr/bin/vm_stat"], text=True, timeout=5)
        import re
        page = int(re.search(r"page size of (\d+) bytes", output).group(1))
        pages = sum(int(re.search(rf"Pages {name}:\s+(\d+)", output).group(1))
                    for name in ("free", "inactive"))
        return {"available_mib": pages * page // MIB, "commit_mib": None}
    with open("/proc/meminfo", encoding="ascii") as stream:
        values = {row.split(":")[0]: int(row.split()[1]) for row in stream if len(row.split()) >= 2}
    return {"available_mib": values["MemAvailable"] // 1024,
            "commit_mib": max(0, values["CommitLimit"] - values["Committed_AS"]) // 1024}


class ResourceAdmission:
    """One lock for both pool threads, plus the ledger's existing transactional resource fence.

    Budgets include switching and quarantined resources. Actual availability is checked too;
    configured costs are conservative admission estimates, not claimed peak measurements.
    """
    def __init__(self, settings, *, observe=host_memory):
        self.settings = validate_settings(settings)
        self.observe = observe
        self.lock = threading.RLock()
        self.last_reason = None

    def acquire(self, ledger, kind, **options):
        with self.lock:
            if not any(r["kind"] == kind for r in ledger.reservations(states=("queued",))):
                return None
            costs = {"unity_slot": self.settings["unity_mib"], "server_slot": self.settings["server_mib"]}
            cost = costs[kind]
            # Open Editors retain RAM after release. Count them until the pool closes them.
            used = sum(costs.get(slot["kind"], 0) for slot in ledger.slots(host=options["host"])
                       if slot["state"] != "idle_closed")
            # Reusing an idle-open Unity slot does not reserve another Editor's budget.
            reuse = kind == "unity_slot" and any(slot["state"] == "idle_open"
                        and slot["slot_id"] not in options.get("exclude_resources", ())
                        for slot in ledger.slots(kind=kind, host=options["host"]))
            if reuse:
                # acquire(batch) normally prefers closed projects; budget credit is valid only if
                # it actually reuses the idle-open project we counted above.
                options["exclude_resources"] = tuple(options.get("exclude_resources", ())) + tuple(
                    slot["slot_id"] for slot in ledger.slots(kind=kind, host=options["host"])
                    if slot["state"] == "idle_closed")
            extra = 0 if reuse else cost
            try:
                memory = self.observe()
                okay = (used + extra <= self.settings["max_reserved_mib"]
                        and memory["available_mib"] >= self.settings["min_available_mib"] + extra
                        and (memory["commit_mib"] is None
                             or memory["commit_mib"] >= self.settings["min_commit_mib"] + extra))
            except Exception:
                self.last_reason = "memory observation unavailable"
                return None
            if not okay:
                self.last_reason = "waiting for physical/commit headroom or reservation budget"
                return None
            self.last_reason = None
            return ledger.acquire(kind, **options)
