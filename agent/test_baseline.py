"""Compare complete per-test Unity evidence; never turn an observed failure into a pass.

This read-only decision aid grants no publishing or resource authority. Workers retain
the normal claim, required-check, main-ancestry and publication checks.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys


SHA = re.compile(r"[0-9a-f]{40}")
ENVIRONMENT_KEYS = {"host", "slot", "unity_version", "platform", "test_framework_version",
                    "config_digest", "package_digest"}
UI_ROOT = "Assets/GameRes/FairyRes/"


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


def _path(value):
    value = _text(value, "input path").replace("\\", "/")
    if (":" in value or "\0" in value or value != value.strip()
            or any(part in ("", ".", "..") for part in value.split("/"))):
        raise ComparisonError("input paths must be canonical repository-relative file paths")
    return value


def _package_inventory(run):
    if "package_inventory" not in run:
        return None
    entries = run["package_inventory"]
    if not isinstance(entries, list) or not entries:
        raise ComparisonError("package_inventory must contain the complete nonempty input inventory")
    inventory, identities = {}, set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
            raise ComparisonError("each package input requires exactly path and sha256")
        path = _path(entry["path"])
        if path.casefold() in identities:
            raise ComparisonError("duplicate or case-aliased package input path")
        digest = entry["sha256"]
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ComparisonError("package input requires a SHA-256 digest")
        inventory[path] = digest
        identities.add(path.casefold())
    if not {"Packages/manifest.json", "Packages/packages-lock.json"} <= inventory.keys():
        raise ComparisonError("package inventory must retain the Unity package manifest and lock")
    canonical = [{"path": path, "sha256": digest} for path, digest in sorted(inventory.items())]
    digest = hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":")).encode("utf-8")).hexdigest()
    if digest != run["environment"]["package_digest"]:
        raise ComparisonError("package inventory digest differs from the recorded package_digest")
    return inventory


def _reviewed(value):
    return isinstance(value, dict) and all(isinstance(value.get(key), str) and value[key].strip()
                                          for key in ("reason", "evidence"))


def _package_comparison(baseline, candidate, changed_paths, reviews):
    before, after = _package_inventory(baseline), _package_inventory(candidate)
    if not isinstance(reviews, dict):
        raise ComparisonError("ui_resource_changes must map exact paths to reason and evidence")
    reviews = {_path(path): review for path, review in reviews.items()}
    if before is None or after is None:
        if reviews or baseline["environment"]["package_digest"] != candidate["environment"]["package_digest"]:
            return [], ["package identity differs without both complete inventories"], before, after
        return [], [], before, after
    changes, blockers = [], []
    for path in sorted(before.keys() | after.keys()):
        if before.get(path) == after.get(path):
            continue
        reviewed = path.startswith(UI_ROOT) and path in changed_paths and _reviewed(reviews.get(path))
        changes.append({"path": path, "baseline_sha256": before.get(path), "candidate_sha256": after.get(path),
                        "classification": "candidate_ui_resource" if reviewed else "environment_drift",
                        **({"review": reviews[path]} if reviewed else {})})
        if not reviewed:
            blockers.append("unreviewed package/environment drift: " + path)
    if set(reviews) - {row["path"] for row in changes}:
        blockers.append("UI resource review names inputs absent from the observed delta")
    return changes, blockers, before, after


def _resource_independent(review, before, after):
    """The source/dependency review must cover each retained failure, including metadata identity."""
    if not _reviewed(review) or not isinstance(review.get("dependencies"), list) or not review["dependencies"]:
        return False
    for value in review["dependencies"]:
        path = _path(value)
        related = {path, path + ".meta"}
        if path.endswith(".meta"):
            related.add(path[:-5])
        parts = path.split("/")
        related.update("/".join(parts[:index]) + ".meta" for index in range(1, len(parts)))
        if path not in before or path not in after or any(before.get(p) != after.get(p) for p in related):
            return False
    return True


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
    _package_inventory(value)
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


def compare(baseline, candidate, *, affected_tests, unrelated_failures, changed_paths, ui_resource_changes=None):
    """Allow draft consideration only for reviewed unchanged failures and zero regressions."""
    before, after = _run(baseline), _run(candidate)
    if not isinstance(affected_tests, list) or not affected_tests or not all(
            isinstance(name, str) and name for name in affected_tests):
        raise ComparisonError("an explicit nonempty affected-test selection is required")
    if not isinstance(unrelated_failures, dict):
        raise ComparisonError("unrelated failures must map exact test names to reason and evidence")
    if not isinstance(changed_paths, list) or not all(isinstance(path, str) and path for path in changed_paths):
        raise ComparisonError("changed paths must be an explicit list")
    changed_paths = [_path(path) for path in changed_paths]
    changes, blockers, packages_before, packages_after = _package_comparison(
        baseline, candidate, changed_paths, {} if ui_resource_changes is None else ui_resource_changes)
    for key in ("mode", "selection"):
        if baseline[key] != candidate[key]:
            blockers.append(f"{key} differs; failure attribution unresolved")
    for key in sorted(baseline["environment"].keys() | candidate["environment"].keys()):
        if key != "package_digest" and baseline["environment"].get(key) != candidate["environment"].get(key):
            blockers.append(f"environment.{key} differs; failure attribution unresolved")
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
        old = before.get(name)
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
        if (not comparable or not _reviewed(review)
                or (changes and not _resource_independent(review.get("resource_review"), packages_before, packages_after))):
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
            "package_input_changes": changes,
            "ui_resource_changes": [row for row in changes if row["classification"] == "candidate_ui_resource"],
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
