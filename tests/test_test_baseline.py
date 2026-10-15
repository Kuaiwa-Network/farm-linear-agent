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
