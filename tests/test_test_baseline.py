import copy
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
