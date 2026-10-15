import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO

from agent.test_baseline import ComparisonError, compare, main


def run(commit, tests):
    return {"commit": commit * 40, "reservation_id": commit, "job_id": commit, "completed": True, "mode": "PlayMode",
            "selection": {"assembly_names": ["HotUpdate.PlayTests"]},
            "environment": {"host": "windows", "slot": "unity_slot:1", "unity_version": "2022.3.62f3",
                            "platform": "Win64", "test_framework_version": "1.1.33",
                            "config_digest": "1" * 64, "package_digest": "2" * 64},
            "summary": {"total": len(tests), "passed": sum(t["state"] == "Passed" for t in tests),
                        "failed": sum(t["state"].startswith("Failed") for t in tests),
                        "skipped": sum(t["state"].startswith("Skipped") for t in tests)}, "tests": tests}


def test(name, state="Passed", message="", stack=""):
    return {"fullName": name, "state": state, "message": message, "stackTrace": stack}


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.failure = test("Card.Cleanup", "Failed:Error", "Common package absent",
                            "at Card.Cleanup() in D:/State/slot-1/Assets/Tests/Card.cs:94")
        rows = [test("Animal.Counter"), self.failure, test("E2E.Login", "Skipped:Ignored", "No E2E config")]
        self.baseline = run("a", copy.deepcopy(rows))
        self.candidate = run("b", copy.deepcopy(rows))
        self.scope = {"affected_tests": ["Animal.Counter"], "changed_paths": ["Assets/Animal/Tuning.cs"],
                      "unrelated_failures": {"Card.Cleanup": {"reason": "unchanged card fixture/dependencies",
                                                               "evidence": "exact source diff and both raw jobs"}}}

    def compare(self):
        return compare(self.baseline, self.candidate, **self.scope)

    def test_nunit_invalid_remains_a_failure_and_requires_matching_review(self):
        for value in (self.baseline, self.candidate):
            value['tests'][1]['state'] = 'Failed:Invalid'
            value['tests'][1]['message'] = 'Method has non-void return value, but no result is expected'
        report = self.compare()
        self.assertTrue(report['draft_eligible'])
        self.assertFalse(report['full_suite_passed'])
        self.assertEqual(report['candidate_counts']['failed'], 1)
        self.scope['unrelated_failures'] = {}
        self.assertFalse(self.compare()['draft_eligible'])

    def test_invalid_does_not_match_a_different_failure_state_or_affected_test(self):
        self.candidate['tests'][1]['state'] = 'Failed:Invalid'
        self.assertFalse(self.compare()['draft_eligible'])
        self.baseline['tests'][1]['state'] = 'Failed:Invalid'
        self.scope['affected_tests'].append('Card.Cleanup')
        self.assertFalse(self.compare()['draft_eligible'])

    def ui_inputs(self):
        self.ui_path = "Assets/GameRes/FairyRes/Friend/Friend_fui.bytes"
        self.card_path = "Assets/GameRes/FairyRes/Card/Card_fui.bytes"
        inventory = [{"path": path, "sha256": "3" * 64} for path in (
            "Packages/manifest.json", "Packages/packages-lock.json", self.ui_path,
            self.ui_path + ".meta", self.card_path)]
        for value in (self.baseline, self.candidate):
            value["package_inventory"] = copy.deepcopy(inventory)
        self.candidate["package_inventory"][2]["sha256"] = "4" * 64
        self.rehash()
        self.scope["changed_paths"].append(self.ui_path)
        self.scope["ui_resource_changes"] = {
            self.ui_path: {"reason": "approved Friend UI export", "evidence": "certified export diff"}}
        self.scope["unrelated_failures"]["Card.Cleanup"]["resource_review"] = {
            "dependencies": [self.card_path], "reason": "Card fixture loads Card, not Friend; shared setup unchanged",
            "evidence": "fixture, shared UI loader and package dependency review"}

    def rehash(self):
        for value in (self.baseline, self.candidate):
            entries = sorted(value["package_inventory"], key=lambda row: row["path"])
            value["environment"]["package_digest"] = hashlib.sha256(json.dumps(
                entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    def test_audited_ui_resource_change_is_subject_change_not_environment_drift(self):
        self.ui_inputs()
        report = self.compare()
        self.assertTrue(report["draft_eligible"])
        self.assertFalse(report["full_suite_passed"])
        self.assertEqual(report["ui_resource_changes"][0]["path"], self.ui_path)
        self.assertEqual(report["reviewed_unrelated_failures"], ["Card.Cleanup"])

    def test_ui_change_requires_inventory_exact_git_scope_and_dependency_review(self):
        self.ui_inputs()
        for field in ("package_inventory",):
            with self.subTest(field=field):
                candidate = copy.deepcopy(self.candidate); candidate.pop(field)
                self.assertFalse(compare(self.baseline, candidate, **self.scope)["draft_eligible"])
        for change in ("not_changed", "not_reviewed", "missing_dependencies", "changed_dependency"):
            with self.subTest(change=change):
                scope = copy.deepcopy(self.scope)
                if change == "not_changed":
                    scope["changed_paths"].remove(self.ui_path)
                elif change == "not_reviewed":
                    scope["ui_resource_changes"] = {}
                elif change == "missing_dependencies":
                    scope["unrelated_failures"]["Card.Cleanup"].pop("resource_review")
                else:
                    scope["unrelated_failures"]["Card.Cleanup"]["resource_review"]["dependencies"] = [self.ui_path]
                self.assertFalse(compare(self.baseline, self.candidate, **scope)["draft_eligible"])

    def test_ui_exception_never_allows_lock_config_settings_or_host_drift(self):
        self.ui_inputs()
        for field in ("host", "slot", "unity_version", "platform", "test_framework_version", "config_digest"):
            with self.subTest(field=field):
                candidate = copy.deepcopy(self.candidate)
                candidate["environment"][field] = "5" * 64
                self.assertFalse(compare(self.baseline, candidate, **self.scope)["draft_eligible"])
        self.candidate["environment"]["settings_digest"] = "5" * 64
        self.assertFalse(self.compare()["draft_eligible"])
        self.candidate["environment"].pop("settings_digest")
        self.candidate["package_inventory"][1]["sha256"] = "5" * 64
        self.rehash()
        self.scope["changed_paths"].append("Packages/packages-lock.json")
        self.scope["ui_resource_changes"]["Packages/packages-lock.json"] = {
            "reason": "cannot waive package drift", "evidence": "lock diff"}
        self.assertFalse(self.compare()["draft_eligible"])

    def test_unlisted_ui_or_meta_drift_stays_blocked(self):
        self.ui_inputs()
        self.candidate["package_inventory"][3]["sha256"] = "5" * 64
        self.rehash()
        self.assertFalse(self.compare()["draft_eligible"])

    def test_audited_meta_change_remains_visible_and_cannot_change_failure_dependency(self):
        self.ui_inputs()
        path = self.ui_path + ".meta"
        self.candidate["package_inventory"][3]["sha256"] = "5" * 64
        self.rehash()
        self.scope["changed_paths"].append(path)
        self.scope["ui_resource_changes"][path] = {"reason": "reviewed import setting", "evidence": "meta diff and guards"}
        self.assertTrue(self.compare()["draft_eligible"])
        self.assertEqual(len(self.compare()["ui_resource_changes"]), 2)
        self.scope["unrelated_failures"]["Card.Cleanup"]["resource_review"]["dependencies"].append(path)
        self.assertFalse(self.compare()["draft_eligible"])

    def test_inventory_tampering_duplicates_and_unsafe_paths_are_gaps(self):
        self.ui_inputs()
        for path in ("../escape", "Assets/GameRes/FairyRes/../Other", "C:/absolute", self.card_path,
                     self.card_path.upper()):
            with self.subTest(path=path), self.assertRaises(ComparisonError):
                candidate = copy.deepcopy(self.candidate)
                candidate["package_inventory"].append({"path": path, "sha256": "6" * 64})
                compare(self.baseline, candidate, **self.scope)
        self.candidate["package_inventory"][2]["sha256"] = "6" * 64
        with self.assertRaisesRegex(ComparisonError, "digest"):
            self.compare()

    def test_dependency_metadata_and_ancestor_metadata_cannot_be_waived(self):
        self.ui_inputs()
        for path in (self.card_path + ".meta", "Assets/GameRes/FairyRes/Card.meta"):
            with self.subTest(path=path):
                baseline, candidate, scope = map(copy.deepcopy, (self.baseline, self.candidate, self.scope))
                for value, digest in ((baseline, "5"), (candidate, "6")):
                    value["package_inventory"].append({"path": path, "sha256": digest * 64})
                    entries = sorted(value["package_inventory"], key=lambda row: row["path"])
                    value["environment"]["package_digest"] = hashlib.sha256(json.dumps(
                        entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
                scope["changed_paths"].append(path)
                scope["ui_resource_changes"][path] = {"reason": "import change", "evidence": "metadata diff"}
                self.assertFalse(compare(baseline, candidate, **scope)["draft_eligible"])

    def test_added_and_deleted_ui_inputs_require_exact_review_and_remain_visible(self):
        self.ui_inputs()
        added = "Assets/GameRes/FairyRes/Friend/新 图.png"
        removed = self.ui_path + ".meta"
        self.candidate["package_inventory"].append({"path": added, "sha256": "6" * 64})
        self.candidate["package_inventory"] = [r for r in self.candidate["package_inventory"] if r["path"] != removed]
        self.rehash()
        self.assertFalse(self.compare()["draft_eligible"])
        for path in (added, removed):
            self.scope["changed_paths"].append(path)
            self.scope["ui_resource_changes"][path] = {"reason": "reviewed export delta", "evidence": "export and guards"}
        report = self.compare()
        self.assertTrue(report["draft_eligible"])
        changes = {r["path"]: r for r in report["package_input_changes"]}
        self.assertIsNone(changes[added]["baseline_sha256"])
        self.assertIsNone(changes[removed]["candidate_sha256"])

    def test_true_regressions_and_new_skips_still_block_with_reviewed_ui_changes(self):
        self.ui_inputs()
        for index, field, value in ((1, "message", "new failure"), (2, "message", "new skip reason"),
                                     (0, "state", "Skipped")):
            with self.subTest(index=index, field=field):
                candidate = copy.deepcopy(self.candidate)
                candidate["tests"][index][field] = value
                if index == 0:
                    candidate["tests"][0]["message"] = "affected test skipped"
                    candidate["summary"].update(passed=0, skipped=2)
                self.assertFalse(compare(self.baseline, candidate, **self.scope)["draft_eligible"])

    def test_environment_drift_does_not_relabel_unchanged_skips_as_new(self):
        self.candidate["environment"]["slot"] = "other"
        report = self.compare()
        self.assertFalse(report["draft_eligible"])
        self.assertEqual(report["new_or_changed_skips"], [])

    def test_reviewed_existing_failure_allows_only_a_draft_verdict(self):
        report = self.compare()
        self.assertTrue(report["draft_eligible"])
        self.assertEqual(report["verdict"], "passed_with_existing_failures")
        self.assertFalse(report["full_suite_passed"])
        self.assertEqual(report["reviewed_unrelated_failures"], ["Card.Cleanup"])
        self.assertEqual(report["candidate_counts"]["failed"], 1)

    def test_passing_new_regression_is_allowed(self):
        self.candidate["tests"].append(test("Animal.NewBoundary"))
        self.candidate["summary"]["total"] += 1
        self.candidate["summary"]["passed"] += 1
        self.scope["affected_tests"].append("Animal.NewBoundary")
        self.assertTrue(self.compare()["draft_eligible"])

    def clock_failure(self):
        for value, deadline, actual in ((self.baseline, "23.638031", "23.6388607"),
                                        (self.candidate, "42.10001", "42.10084")):
            value["tests"][1].update(state="Failed", message=(
                "  timed out waiting for the entry result\r\n"
                f"  Expected: less than {deadline}f\r\n  But was:  {actual}f\r\n"))
        self.scope["unrelated_failures"]["Card.Cleanup"]["clock_diagnostic"] = {
            "kind": "unity_realtime_deadline", "clock": "UnityEngine.Time.realtimeSinceStartup",
            "source_path": "Assets/Tests/Card.cs", "baseline_source_sha256": "7" * 64,
            "candidate_source_sha256": "7" * 64, "timeout_seconds": 15,
            "reason": "same 15-second wait and assertion; printed operands are process-relative clock readings",
            "evidence": "both committed source files, unchanged wait predicate/dependencies and raw results"}

    def test_reviewed_clock_readings_allow_delivery_without_green_baseline_or_retest(self):
        self.clock_failure()
        originals = copy.deepcopy((self.baseline, self.candidate))
        report = self.compare()
        self.assertTrue(report["draft_eligible"])
        self.assertEqual(report["verdict"], "passed_with_existing_failures")
        self.assertFalse(report["full_suite_passed"])
        diagnostic, = report["reviewed_clock_diagnostics"]
        self.assertEqual(diagnostic["baseline"]["deadline"], "23.638031")
        self.assertEqual(diagnostic["candidate"]["deadline"], "42.10001")
        self.assertEqual((self.baseline, self.candidate), originals)

    def test_clock_changes_are_strict_without_complete_source_review(self):
        self.clock_failure()
        review = self.scope["unrelated_failures"]["Card.Cleanup"]
        diagnostic = copy.deepcopy(review["clock_diagnostic"])
        for change in ({}, {"kind": "ignore_numbers"}, {"clock": "configured gold"},
                       {"candidate_source_sha256": "8" * 64}, {"baseline_source_sha256": "invalid"},
                       {"timeout_seconds": 0}, {"timeout_seconds": float("nan")},
                       {"timeout_seconds": True}, {"reason": ""}, {"evidence": ""},
                       {"source_path": "Assets/Other.cs"}):
            with self.subTest(change=change):
                review["clock_diagnostic"] = {**diagnostic, **change} if change else None
                self.assertFalse(self.compare()["draft_eligible"])

    def test_late_baseline_observation_preserves_failure_without_requiring_a_retest(self):
        self.clock_failure()
        self.baseline["tests"][1]["message"] = (
            "  timed out waiting for the entry result\r\n"
            "  Expected: less than 54.5053902f\r\n  But was:  60.6000252f\r\n")
        self.candidate["tests"][1]["message"] = (
            "  timed out waiting for the entry result\r\n"
            "  Expected: less than 53.995945f\r\n  But was:  53.9964981f\r\n")
        original = copy.deepcopy((self.baseline, self.candidate, self.scope))
        report = self.compare()
        self.assertTrue(report["draft_eligible"])
        self.assertFalse(report["full_suite_passed"])
        self.assertEqual(report["verdict"], "passed_with_existing_failures")
        self.assertEqual(report["candidate_counts"]["failed"], 1)
        diagnostic, = report["reviewed_clock_diagnostics"]
        self.assertEqual(diagnostic["baseline"]["overshoot_seconds"], "6.0946350")
        self.assertEqual(diagnostic["candidate"]["overshoot_seconds"], "0.0005531")
        self.assertTrue(diagnostic["baseline_late_observation"])
        self.assertEqual((self.baseline, self.candidate, self.scope), original)

    def test_late_baseline_does_not_relax_candidate_limit_or_accept_unexpired_assertions(self):
        self.clock_failure()
        for baseline_actual, candidate_actual, eligible in (
                ("61", "101", True), ("61", "101.000001", False),
                ("61", "106", False), ("61", "111", False),
                ("51", "106", False), ("49.9", "100.1", False),
                ("61", "99.9", False)):
            with self.subTest(baseline_actual=baseline_actual, candidate_actual=candidate_actual):
                for value, deadline, actual in ((self.baseline, "50", baseline_actual),
                                                (self.candidate, "100", candidate_actual)):
                    value["tests"][1]["message"] = (
                        f"timed out waiting for the entry result\nExpected: less than {deadline}f\nBut was: {actual}f")
                self.assertEqual(self.compare()["draft_eligible"], eligible)

    def test_late_baseline_still_requires_source_scope_and_environment_review(self):
        self.clock_failure()
        self.baseline["tests"][1]["message"] = self.baseline["tests"][1]["message"].replace("23.6388607", "30.0")
        original = copy.deepcopy((self.baseline, self.candidate, self.scope))
        for kind in ("review", "source", "affected", "environment", "site", "predicate"):
            with self.subTest(kind=kind):
                self.baseline, self.candidate, self.scope = copy.deepcopy(original)
                if kind == "review":
                    self.scope["unrelated_failures"] = {}
                elif kind == "source":
                    self.scope["unrelated_failures"]["Card.Cleanup"]["clock_diagnostic"]["candidate_source_sha256"] = "8" * 64
                elif kind == "affected":
                    self.scope["affected_tests"].append("Card.Cleanup")
                elif kind == "environment":
                    self.candidate["environment"]["config_digest"] = "9" * 64
                elif kind == "site":
                    self.candidate["tests"][1]["stackTrace"] += "\nat Another.Call() in Assets/Other.cs:20"
                else:
                    self.candidate["tests"][1]["message"] = self.candidate["tests"][1]["message"].replace("entry result", "payment result")
                self.assertFalse(self.compare()["draft_eligible"])

    def test_identical_clock_operands_cannot_bypass_a_declared_clock_review(self):
        self.clock_failure()
        for actual, eligible in (("50.5", True), ("51.000001", False), ("56", False), ("49", False)):
            with self.subTest(actual=actual):
                for value in (self.baseline, self.candidate):
                    value["tests"][1]["message"] = (
                        f"timed out waiting for the entry result\nExpected: less than 50f\nBut was: {actual}f")
                self.assertEqual(self.compare()["draft_eligible"], eligible)

    def test_clock_review_never_waives_real_regressions_or_missing_evidence(self):
        self.clock_failure()
        original = copy.deepcopy((self.baseline, self.candidate, self.scope))
        for kind in ("affected", "source_changed", "new_failure", "changed_site", "error",
                     "different_message", "different_assertion", "large_overrun", "not_expired",
                     "environment", "missing_affected", "new_skip", "newly_failing_test"):
            with self.subTest(kind=kind):
                self.baseline, self.candidate, self.scope = copy.deepcopy(original)
                row = self.candidate["tests"][1]
                if kind == "affected":
                    self.scope["affected_tests"].append("Card.Cleanup")
                elif kind == "source_changed":
                    self.scope["changed_paths"].append("Assets/Tests/Card.cs")
                elif kind == "new_failure":
                    row["fullName"] = "Card.NewFailure"
                elif kind == "changed_site":
                    row["stackTrace"] = row["stackTrace"].replace("Card.Cleanup", "Card.Other")
                elif kind == "error":
                    row["state"] = "Failed:Error"
                elif kind == "different_message":
                    row["message"] = row["message"].replace("entry result", "payment result")
                elif kind == "different_assertion":
                    row["message"] = row["message"].replace("less than", "greater than")
                elif kind == "large_overrun":
                    row["message"] = row["message"].replace("42.10084f", "72.10084f")
                elif kind == "not_expired":
                    row["message"] = row["message"].replace("42.10084f", "12.10084f")
                elif kind == "environment":
                    self.candidate["environment"]["host"] = "another host"
                elif kind == "missing_affected":
                    self.scope["affected_tests"].append("Animal.Missing")
                elif kind == "new_skip":
                    self.candidate["tests"][2]["message"] = "new skip reason"
                else:
                    self.baseline["tests"][1].update(state="Passed", message="", stackTrace="")
                    self.baseline["summary"].update(passed=2, failed=0)
                self.assertFalse(self.compare()["draft_eligible"])

    def test_clock_review_does_not_erase_business_values_or_duration_errors(self):
        self.clock_failure()
        for old, new in (("Expected: 1800\nBut was: 2000", "Expected: 1800\nBut was: 3000"),
                         ("Exceed Timeout:00:00:03", "Exceed Timeout:00:00:30")):
            with self.subTest(old=old):
                self.baseline["tests"][1]["message"] = old
                self.candidate["tests"][1]["message"] = new
                self.assertFalse(self.compare()["draft_eligible"])

    def test_clock_review_still_checks_changed_resource_dependencies(self):
        self.clock_failure()
        self.ui_inputs()
        self.assertTrue(self.compare()["draft_eligible"])
        self.scope["unrelated_failures"]["Card.Cleanup"]["resource_review"]["dependencies"] = [self.ui_path]
        self.assertFalse(self.compare()["draft_eligible"])

    def test_failure_counts_do_not_match_changed_identity_message_state_or_site(self):
        for key, value in (("fullName", "Other.Failure"), ("message", "Different exception"),
                           ("state", "Failed"), ("stackTrace", "at Other.Fail() in Assets/Other.cs:94")):
            with self.subTest(key=key):
                candidate = copy.deepcopy(self.candidate)
                candidate["tests"][1][key] = value
                self.assertFalse(compare(self.baseline, candidate, **self.scope)["draft_eligible"])

    def test_environment_selection_and_mode_mismatch_block_attribution(self):
        for key, value in (("mode", "EditMode"), ("selection", {"test_names": ["Card.Cleanup"]}),
                           ("environment", {**self.candidate["environment"], "slot": "unity_slot:2"})):
            with self.subTest(key=key):
                candidate = copy.deepcopy(self.candidate); candidate[key] = value
                self.assertFalse(compare(self.baseline, candidate, **self.scope)["draft_eligible"])

    def test_review_required_and_changed_failure_code_cannot_be_waived(self):
        for scope in ({**self.scope, "unrelated_failures": {}},
                      {**self.scope, "changed_paths": ["Assets/Tests/Card.cs"]},
                      {**self.scope, "affected_tests": ["Animal.Counter", "Card.Cleanup"]}):
            with self.subTest(scope=scope):
                self.assertFalse(compare(self.baseline, self.candidate, **scope)["draft_eligible"])

    def test_missing_affected_test_and_new_or_changed_skip_block(self):
        self.scope["affected_tests"].append("Animal.Missing")
        self.assertFalse(self.compare()["draft_eligible"])
        self.scope["affected_tests"].pop()
        self.candidate["tests"][2]["message"] = "Different skip reason"
        self.assertEqual(self.compare()["new_or_changed_skips"], ["E2E.Login"])

    def test_disappeared_tests_and_same_commit_block(self):
        self.candidate["tests"].pop(); self.candidate["summary"].update(total=2, skipped=0)
        self.assertFalse(self.compare()["draft_eligible"])
        self.candidate["commit"] = self.baseline["commit"]
        self.assertIn("baseline and candidate commits must be distinct", self.compare()["blockers"])

    def test_complete_nonzero_validated_leaf_results_are_required(self):
        for change in ({"completed": False}, {"tests": []}, {"summary": {"total": 3, "passed": 3}},
                       {"environment": {}}, {"commit": "main"}):
            with self.subTest(change=change), self.assertRaises(ComparisonError):
                compare(self.baseline, {**self.candidate, **change}, **self.scope)
        self.candidate["tests"].append(copy.deepcopy(self.failure))
        with self.assertRaisesRegex(ComparisonError, "duplicate"):
            self.compare()

    def test_normalization_preserves_site_but_ignores_paths_and_line_shifts(self):
        self.candidate["tests"][1]["stackTrace"] = (
            "at Card.Cleanup() in C:/Other/slot-1/Assets/Tests/Card.cs:99")
        self.assertTrue(self.compare()["draft_eligible"])

    def test_shared_job_or_reservation_cannot_certify_two_commits(self):
        for field in ("job_id", "reservation_id"):
            with self.subTest(field=field):
                candidate = copy.deepcopy(self.candidate)
                candidate[field] = self.baseline[field]
                self.assertFalse(compare(self.baseline, candidate, **self.scope)["draft_eligible"])

    def test_failure_message_paths_and_urls_are_not_erased(self):
        for before, after in (("https://one/Assets/Config", "https://two/Assets/Config"),
                              ("D:/First/Assets/Config", "C:/Other/Assets/Config")):
            with self.subTest(before=before):
                baseline = copy.deepcopy(self.baseline); candidate = copy.deepcopy(self.candidate)
                baseline["tests"][1]["message"] = before; candidate["tests"][1]["message"] = after
                self.assertFalse(compare(baseline, candidate, **self.scope)["draft_eligible"])

    def test_non_string_failure_data_is_a_gap(self):
        for field in ("message", "stackTrace"):
            with self.subTest(field=field), self.assertRaises(ComparisonError):
                candidate = copy.deepcopy(self.candidate); candidate["tests"][1][field] = 123
                compare(self.baseline, candidate, **self.scope)

    def test_nunit_null_fields_preserve_passed_and_explained_outcomes(self):
        for run_value in (self.baseline, self.candidate):
            run_value["tests"][0].update(message=None, stackTrace=None)
            run_value["tests"][1]["stackTrace"] = None
            run_value["tests"][2]["stackTrace"] = None
        self.assertTrue(self.compare()["draft_eligible"])
        for index in (1, 2):
            with self.subTest(index=index), self.assertRaises(ComparisonError):
                candidate = copy.deepcopy(self.candidate)
                candidate["tests"][index]["message"] = None
                compare(self.baseline, candidate, **self.scope)

    def test_cli_outputs_failed_full_suite_and_rejects_gaps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, value in (("baseline", self.baseline), ("candidate", self.candidate), ("scope", self.scope)):
                (root / name).write_text(json.dumps(value), encoding="utf-8")
            args = ["--baseline", str(root / "baseline"), "--candidate", str(root / "candidate"),
                    "--scope", str(root / "scope")]
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(args), 0)
            self.assertFalse(json.loads(output.getvalue())["full_suite_passed"])
            (root / "baseline").write_text("{}", encoding="utf-8")
            with redirect_stdout(StringIO()):
                self.assertEqual(main(args), 2)
