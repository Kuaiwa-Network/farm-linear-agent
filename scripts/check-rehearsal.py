"""Check recorded Unity rehearsal evidence without modifying the ledger or fetching Git refs.

Example: python3 scripts/check-rehearsal.py --db PATH --runs PATH
         --item BATCH --item INTERACTIVE --pair BATCH INTERACTIVE
Cancellation latency and process death require external stopwatch/pgrep evidence in the report.
"""
import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.unity import UnityError, read_results


class EvidenceError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise EvidenceError(message)


class Checker:
    def __init__(self, connection, runs, cancelled):
        self.connection = connection
        self.runs = Path(runs)
        self.cancelled = set(cancelled)
        self.failed = False
        self.reservations = [dict(row) for row in connection.execute("SELECT * FROM reservations ORDER BY sequence")]
        self.audit = [dict(row) for row in connection.execute("SELECT * FROM audit ORDER BY id")]
        for row in self.audit:
            row["details"] = json.loads(row["details"])

    def check(self, label, operation):
        try:
            detail = operation()
        except (EvidenceError, OSError, ValueError, TypeError, KeyError, UnityError, subprocess.SubprocessError) as exc:
            self.failed = True
            print(f"FAIL {label}: {exc}")
        else:
            print(f"PASS {label}" + (f": {detail}" if detail else ""))

    def event(self, reservation, reason):
        matches = [row for row in self.audit if row["item_id"] == reservation["item_id"]
                   and row["kind"] == "reservation" and row["reason"] == reason
                   and row["details"].get("reservation_id") == reservation["reservation_id"]]
        require(len(matches) == 1, f"expected one reservation {reason} audit, found {len(matches)}")
        return matches[0]

    def transitions(self, reservation):
        state = "cancelled" if reservation["item_id"] in self.cancelled else "released"
        require(reservation["state"] == state, f"reservation is {reservation['state']}, expected {state}")
        queued = self.event(reservation, "queued")
        acquired = self.event(reservation, "acquired")
        released = self.event(reservation, state)
        slot = reservation["resource"]
        require(acquired["details"].get("slot") == slot, "acquired audit names a different slot")
        require(queued["id"] < acquired["id"] < released["id"], "reservation audit order is invalid")
        require(reservation["acquired_at"] is not None and reservation["released_at"] is not None,
                "missing acquired_at or released_at")
        require(reservation["created_at"] <= reservation["acquired_at"] <= reservation["released_at"],
                "reservation timestamp order is invalid")
        busy = [row for row in self.audit if row["item_id"] == slot and row["kind"] == "slot"
                and row["reason"] == f"{reservation['mode']}_busy"
                and acquired["id"] < row["id"] < released["id"]]
        require(busy, "missing busy slot transition within reservation ownership")
        next_acquired = next((row["id"] for row in self.audit if row["kind"] == "reservation"
                              and row["reason"] == "acquired" and row["details"].get("slot") == slot
                              and row["id"] > acquired["id"]), float("inf"))
        next_start = min((row["acquired_at"] for row in self.reservations
                          if row["resource"] == slot and row["acquired_at"] is not None
                          and (row["acquired_at"], row["sequence"]) >
                              (reservation["acquired_at"], reservation["sequence"])), default=float("inf"))
        idle = [row for row in self.audit if row["item_id"] == slot and row["kind"] == "slot"
                and row["reason"] in ("idle_closed", "idle_open")
                and released["id"] < row["id"] < next_acquired and row["created_at"] <= next_start]
        require(idle, "missing idle slot transition after release and before next acquisition")
        return f"queued → acquired → {reservation['mode']}_busy → {state} → {idle[0]['reason']}"

    def overlap(self, selected):
        for row in selected:
            start, end = row["acquired_at"], row["released_at"]
            require(start is not None and end is not None, "missing acquired_at/released_at for overlap check")
            for other in self.reservations:
                if other["reservation_id"] == row["reservation_id"] or other["resource"] != row["resource"]:
                    continue
                if other["acquired_at"] is None:
                    continue
                other_end = other["released_at"] if other["released_at"] is not None else float("inf")
                require(not (start < other_end and other["acquired_at"] < end),
                        f"acquisition overlap: {row['reservation_id']} and {other['reservation_id']}")

    def identity(self, reservation):
        rows = self.connection.execute(
            "SELECT * FROM identity_observations WHERE item_id=? AND reservation_id=? AND slot_id=? "
            "ORDER BY created_at", (reservation["item_id"], reservation["reservation_id"], reservation["resource"])
        ).fetchall()
        require(rows, "missing interactive identity observation")
        require(reservation["acquired_at"] is not None and reservation["released_at"] is not None,
                "missing ownership interval for identity")
        latest = rows[-1]
        require(reservation["acquired_at"] <= latest["created_at"] <= reservation["released_at"],
                "identity observation lies outside reservation ownership")
        require(json.loads(latest["result_json"]).get("aggregate") == "match", "identity aggregate is not match")
        return "aggregate=match"

    def batch(self, item):
        if item in self.cancelled:
            print(f"INTERRUPTED {item}: cancelled batch; no complete XML result is required")
            return
        folder = self.runs / item
        summary = json.loads((folder / "unity-batch.json").read_text())
        require(summary.get("state") == "ran", f"batch summary state is {summary.get('state')!r}, expected ran")
        result = read_results(folder / "unity-tests.xml")
        require(result["total"] > 0, f"batch total must be positive, got {result['total']}")
        label = "BASELINE" if result["failed"] == 26 else "CHANGED"
        print(f"{label} {item}: total={result['total']} failed={result['failed']} known baseline=26")
        return "summary=ran, XML total>0"

    def cancellation(self, item):
        row = self.connection.execute("SELECT state FROM work_items WHERE id=?", (item,)).fetchone()
        require(row is not None and row["state"] == "cancelled", "expected cancelled item")
        require(any(row["item_id"] == item and row["kind"] == "cancelled" for row in self.audit),
                "missing item cancelled audit")
        return "cancelled audit present; kill latency requires external stopwatch and process evidence"

    def parked(self, slot_id):
        slot = self.connection.execute("SELECT * FROM slots WHERE slot_id=?", (slot_id,)).fetchone()
        require(slot is not None, "missing slot")
        require(slot["state"] in ("idle_closed", "idle_open"), f"slot is {slot['state']}, not idle")
        head = subprocess.run(["git", "-C", slot["folder"], "rev-parse", "--verify", "origin/main^{commit}"],
                              capture_output=True, text=True, timeout=15, check=True).stdout.strip()
        require(slot["parked_commit"] == head, f"parked_commit={slot['parked_commit']} differs from origin/main={head}")
        actual = subprocess.run(["git", "-C", slot["folder"], "rev-parse", "--verify", "HEAD^{commit}"],
                                capture_output=True, text=True, timeout=15, check=True).stdout.strip()
        require(actual == head, f"slot HEAD={actual} differs from origin/main={head}")
        return f"{slot['state']}, parked_commit=HEAD={head} (local origin/main; no fetch)"

    def pair(self, first, second):
        before = [row for row in self.reservations if row["item_id"] == first]
        after = [row for row in self.reservations if row["item_id"] == second]
        require(before and after and first != second, "pair requires two different items with reservations")
        require({row["mode"] for row in before} != {row["mode"] for row in after}, "pair must exercise opposite modes")
        require(len({row["resource"] for row in before + after}) == 1, "pair did not use the same slot")
        require(all(row["released_at"] is not None for row in before)
                and all(row["acquired_at"] is not None for row in after), "pair lacks acquired/released timestamps")
        require(max(row["released_at"] for row in before) <= min(row["acquired_at"] for row in after),
                "pair acquisition order is reversed or overlaps")
        return f"{before[0]['mode']} → {after[0]['mode']}"

    def run(self, items, pairs):
        selected = [row for row in self.reservations if row["item_id"] in items]
        for item in items:
            rows = [row for row in selected if row["item_id"] == item]
            self.check(f"{item} reservations", lambda rows=rows: require(rows, "no reservation records"))
            if item in self.cancelled:
                self.check(f"{item} cancellation", lambda item=item: self.cancellation(item))
            for row in rows:
                label = f"{item}/{row['reservation_id']}"
                self.check(f"{label} transitions", lambda row=row: self.transitions(row))
                if row["mode"] == "interactive":
                    self.check(f"{label} identity", lambda row=row: self.identity(row))
            if any(row["mode"] == "batch" for row in rows):
                self.check(f"{item} batch", lambda item=item: self.batch(item))
        self.check("no acquisition overlap", lambda: self.overlap(selected))
        for first, second in pairs:
            self.check(f"pair {first} → {second}", lambda first=first, second=second: self.pair(first, second))
        for slot in sorted({row["resource"] for row in selected if row["resource"]}):
            self.check(f"{slot} parked", lambda slot=slot: self.parked(slot))
        return int(self.failed)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--runs", required=True, type=Path)
    parser.add_argument("--item", action="append", default=[])
    parser.add_argument("--pair", nargs=2, action="append", default=[], metavar=("FIRST", "SECOND"))
    parser.add_argument("--cancelled-item", action="append", default=[])
    args = parser.parse_args(argv)
    items = list(dict.fromkeys([*args.item, *(item for pair in args.pair for item in pair), *args.cancelled_item]))
    if not items:
        parser.error("select at least one --item, --pair, or --cancelled-item")
    connection = None
    try:
        connection = sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")  # One consistent read snapshot across the live ledger's tables.
        return Checker(connection, args.runs, args.cancelled_item).run(items, args.pair)
    except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
        print(f"FAIL evidence: {exc}")
        return 1
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
