"""Conservative shared admission for heavy resources; process containment remains separate."""
import ctypes
import os
import subprocess
import threading
import time

from .server_test_plan import profile

MIB = 1024 * 1024
DEFAULTS = {"max_reserved_mib": 16384, "min_available_mib": 4096,
            "min_commit_mib": 4096, "unity_mib": 4096, "server_mib": 4608}


def validate_settings(raw):
    if not isinstance(raw, dict) or set(raw) - (set(DEFAULTS) | {"server_focused_mib", "server_targeted_mib"}):
        raise ValueError("resource_admission accepts memory budgets in MiB only")
    settings = {**DEFAULTS, **raw}
    if any(type(value) is not int or not 1 <= value <= 1024 * 1024 for value in settings.values()):
        raise ValueError("resource_admission budgets must be positive integer MiB")
    settings.setdefault("server_focused_mib", min(4096, settings["server_mib"]))
    settings.setdefault("server_targeted_mib", min(6144, settings["server_mib"]))
    if any(type(value) is not int or not 1 <= value <= 1024 * 1024 for value in settings.values()):
        raise ValueError("resource_admission budgets must be positive integer MiB")
    if max(settings["unity_mib"], settings["server_mib"]) > settings["max_reserved_mib"]:
        raise ValueError("a heavy resource exceeds the entire reservation budget")
    if not settings["server_focused_mib"] <= settings["server_targeted_mib"] <= settings["server_mib"]:
        raise ValueError("server estimates must be ordered focused <= targeted <= full")
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
            pending = [r for r in ledger.reservations(states=("queued",)) if r["kind"] == kind]
            if not pending:
                return None
            request = pending[0]
            if options.get("expected_reservation", request["reservation_id"]) != request["reservation_id"]:
                return None
            options["expected_reservation"] = request["reservation_id"]
            cost = self.cost(request)
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
            detail = self.assess(ledger, options["host"], extra)
            if kind == "server_slot":
                detail["profile"] = profile(request["server_test_plan"]) if request["server_test_plan"] else "full"
                ledger.resource_wait_detail(request["reservation_id"], detail)
            if detail["blockers"]:
                self.last_reason = "waiting for physical/commit headroom or reservation budget" if "memory_observation" not in detail["blockers"] else "memory observation unavailable"
                return None
            self.last_reason = None
            return ledger.acquire(kind, **options)

    def cost(self, reservation):
        if reservation["kind"] == "unity_slot":
            return self.settings["unity_mib"]
        tier = profile(reservation["server_test_plan"]) if reservation.get("server_test_plan") else "full"
        return self.settings[{"focused": "server_focused_mib", "targeted": "server_targeted_mib", "full": "server_mib"}[tier]]

    def assess(self, ledger, host, extra):
        """Same numbers for admission and display; existing leases retain their actual tier cost."""
        active = {r["resource"]: r for r in ledger.reservations(states=("active", "cancel_requested")) if r["host"] == host}
        used = sum(self.cost(active.get(slot["slot_id"], {"kind": slot["kind"]}))
                   for slot in ledger.slots(host=host) if slot["state"] != "idle_closed")
        result = {"observed_at": time.time(), "requested_mib": extra, "reserved_mib": used,
                  "budget_mib": self.settings["max_reserved_mib"],
                  "required_available_mib": self.settings["min_available_mib"] + extra,
                  "required_commit_mib": self.settings["min_commit_mib"] + extra,
                  "available_mib": None, "commit_mib": None, "blockers": []}
        if used + extra > result["budget_mib"]:
            result["blockers"].append("reservation_budget")
        try:
            memory = self.observe()
            if (type(memory["available_mib"]) is not int or memory["available_mib"] < 0
                    or memory["commit_mib"] is not None and (type(memory["commit_mib"]) is not int or memory["commit_mib"] < 0)):
                raise ValueError("invalid memory observation")
            result.update(available_mib=memory["available_mib"], commit_mib=memory["commit_mib"])
            if memory["available_mib"] < result["required_available_mib"]:
                result["blockers"].append("physical_memory")
            if memory["commit_mib"] is not None and memory["commit_mib"] < result["required_commit_mib"]:
                result["blockers"].append("commit_headroom")
        except Exception:
            result["blockers"].append("memory_observation")
        return result

    def can_start_editor(self, ledger, *, host):
        """Starting a second warm Editor always costs another Editor, even with an idle one open."""
        with self.lock:
            return self._permits(ledger, host, self.settings["unity_mib"])

    def _permits(self, ledger, host, extra):
        detail = self.assess(ledger, host, extra)
        okay = not detail["blockers"]
        if "memory_observation" in detail["blockers"]:
            self.last_reason = "memory observation unavailable"
            return False
        self.last_reason = None if okay else "waiting for physical/commit headroom or reservation budget"
        return okay
