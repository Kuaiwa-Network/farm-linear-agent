"""Compare complete per-test Unity evidence; never turn an observed failure into a pass.

This read-only decision aid grants no publishing or resource authority. Workers retain
the normal claim, required-check, main-ancestry and publication checks.
"""
import argparse
import json
from pathlib import Path
import re
import sys


SHA = re.compile(r"[0-9a-f]{40}")
ENVIRONMENT_KEYS = {"host", "slot", "unity_version", "platform", "test_framework_version",
                    "config_digest", "package_digest"}


class ComparisonError(ValueError):
    pass


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ComparisonError(f"{name} must be a nonempty string")
    return value


def _outcome(state):
    if state == "Passed":
        return "passed"
    if isinstance(state, str) and state in ("Failed", "Failed:Error", "Failed:Failure"):
        return "failed"
    if isinstance(state, str) and state in ("Skipped", "Skipped:Ignored", "Skipped:Explicit", "Inconclusive"):
        return "skipped"
    raise ComparisonError(f"unknown leaf outcome: {state!r}")


def _run(value):
    if not isinstance(value, dict) or value.get("completed") is not True:
        raise ComparisonError("a completed run is required; stalls and missing results are gaps")
    if not isinstance(value.get("commit"), str) or not SHA.fullmatch(value["commit"]):
        raise ComparisonError("run commit must be a full lowercase SHA")
    _text(value.get("reservation_id"), "reservation_id")
    _text(value.get("job_id"), "job_id")
    if value.get("mode") not in ("PlayMode", "EditMode"):
        raise ComparisonError("run mode must be PlayMode or EditMode")
    selection = value.get("selection")
    if not isinstance(selection, dict) or not selection:
        raise ComparisonError("explicit test selection is required")
    environment = value.get("environment")
    if not isinstance(environment, dict) or not ENVIRONMENT_KEYS <= environment.keys():
        raise ComparisonError("complete environment identity is required")
    for key in ENVIRONMENT_KEYS:
        _text(environment[key], key)
    for key in ("config_digest", "package_digest"):
        if not re.fullmatch(r"[0-9a-f]{64}", environment[key]):
            raise ComparisonError(f"{key} must be a SHA-256 digest")
    tests = value.get("tests")
    if not isinstance(tests, list) or not tests:
        raise ComparisonError("nonzero complete leaf results are required")
    rows, counts = {}, {"passed": 0, "failed": 0, "skipped": 0}
    for row in tests:
        if not isinstance(row, dict):
            raise ComparisonError("every test must be an object")
        name = _text(row.get("fullName"), "fullName")
        if name in rows:
            raise ComparisonError(f"duplicate test identity: {name}")
        outcome = _outcome(row.get("state"))
        for field in ("message", "stackTrace"):
            if row.get(field) is not None and not isinstance(row[field], str):
                raise ComparisonError(f"{field} must be a string or null")
        if outcome != "passed":
            _text(row.get("message"), "failure/skip message")
        rows[name] = row
        counts[outcome] += 1
    summary = value.get("summary")
    expected = {"total": len(rows), **counts}
    if (not isinstance(summary, dict) or any(type(summary.get(key)) is not int
                                           or summary[key] != count for key, count in expected.items())):
        raise ComparisonError("summary counts disagree with complete leaf results")
    return rows


def _normalized(value):
    value = (value or "").replace("\\", "/").replace("\r\n", "\n").strip()
    # Different owned checkouts/line shifts/assembly IDs do not alter the failure site.
    value = re.sub(r"(?<![:/\w])(?:[A-Za-z]:)?/[^\n]*?/(?=Assets/|Packages/|Library/)", "", value)
    value = re.sub(r"\.cs:\d+", ".cs", value)
    value = re.sub(r" \[0x[0-9a-f]+\]", "", value)
    return re.sub(r"<[0-9a-f]{32}>", "<assembly>", value)


def _signature(row):
    message = (row.get("message") or "").replace("\r\n", "\n").strip()
    return row["state"], message, _normalized(row.get("stackTrace"))


def compare(baseline, candidate, *, affected_tests, unrelated_failures, changed_paths):
    """Allow draft consideration only for reviewed unchanged failures and zero regressions."""
    before, after = _run(baseline), _run(candidate)
    if not isinstance(affected_tests, list) or not affected_tests or not all(
            isinstance(name, str) and name for name in affected_tests):
        raise ComparisonError("an explicit nonempty affected-test selection is required")
    if not isinstance(unrelated_failures, dict):
        raise ComparisonError("unrelated failures must map exact test names to reason and evidence")
    if not isinstance(changed_paths, list) or not all(isinstance(path, str) and path for path in changed_paths):
        raise ComparisonError("changed paths must be an explicit list")
    changed_paths = [path.replace("\\", "/") for path in changed_paths]
    blockers = []
    for key in ("mode", "selection", "environment"):
        if baseline[key] != candidate[key]:
            blockers.append(f"{key} differs; failure attribution unresolved")
    comparable = not blockers
    if baseline["commit"] == candidate["commit"]:
        blockers.append("baseline and candidate commits must be distinct")
    if (baseline["reservation_id"] == candidate["reservation_id"] or baseline["job_id"] == candidate["job_id"]):
        blockers.append("baseline and candidate must name distinct reservations and test jobs")
    missing = sorted(before.keys() - after.keys())
    if missing:
        blockers.append("baseline tests disappeared from candidate selection: " + ", ".join(missing))
    for name in affected_tests:
        if name not in after or _outcome(after[name]["state"]) != "passed":
            blockers.append(f"affected test missing or not passed: {name}")
    matched, reviewed, unresolved, new_skips = [], [], [], []
    for name, row in sorted(after.items()):
        outcome = _outcome(row["state"])
        if outcome == "passed":
            continue
        old = before.get(name) if comparable else None
        if old is None or _signature(row) != _signature(old):
            if outcome == "skipped":
                new_skips.append(name)
            else:
                unresolved.append(name)
            continue
        if outcome == "skipped":
            continue
        matched.append(name)
        review = unrelated_failures.get(name)
        if not isinstance(review, dict) or not all(isinstance(review.get(k), str) and review[k].strip()
                                                   for k in ("reason", "evidence")):
            unresolved.append(name)
            continue
        origin = _normalized(row.get("stackTrace"))
        if name in affected_tests or any(path in origin for path in changed_paths):
            unresolved.append(name)
            continue
        reviewed.append(name)
    if unresolved:
        blockers.append("new, changed, affected or unreviewed failures: " + ", ".join(unresolved))
    if new_skips:
        blockers.append("new or changed skips: " + ", ".join(new_skips))
    eligible = not blockers
    return {"baseline_commit": baseline["commit"], "candidate_commit": candidate["commit"],
            "baseline_reservation": baseline["reservation_id"], "candidate_reservation": candidate["reservation_id"],
            "baseline_job": baseline["job_id"], "candidate_job": candidate["job_id"],
            "baseline_counts": baseline["summary"], "candidate_counts": candidate["summary"],
            "draft_eligible": eligible,
            "verdict": ("passed_with_existing_failures" if reviewed else "passed") if eligible else "blocked",
            "full_suite_passed": candidate["summary"]["failed"] == 0,
            "matched_existing_failures": matched, "reviewed_unrelated_failures": reviewed,
            "unresolved_failures": unresolved, "new_or_changed_skips": new_skips, "blockers": blockers}


def _read(path):
    with Path(path).open("rb") as handle:
        raw = handle.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise ComparisonError("comparison input exceeds 16 MiB")
    return json.loads(raw.decode("utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--scope", required=True, help="affected_tests, unrelated_failures and changed_paths JSON")
    args = parser.parse_args(argv)
    try:
        report = compare(_read(args.baseline), _read(args.candidate), **_read(args.scope))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["draft_eligible"] else 1
    except (ComparisonError, OSError, ValueError, TypeError) as exc:
        print(json.dumps({"draft_eligible": False, "verdict": "gap", "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
