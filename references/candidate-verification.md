# Candidate-first verification

Run the candidate's required checks first. Use the issue's changed behavior and
concrete affected dependencies to select tests; retain repository-mandated gates.
Do not automatically run main, duplicate a suite, or broaden a test selection to
manufacture a baseline/candidate pair. A source base is not a tested baseline.

Reuse existing complete reports with verified source, input and environment
identity. For a candidate-only Unity round, after clean release and controller
settlement, use:

```text
<selected-python> -m agent --db DATABASE verify-candidate --item ITEM --token-file STATE_DIR/token --candidate STATE_DIR/candidate.json --scope STATE_DIR/candidate-scope.json
```

The candidate uses the complete run format in [test-baseline.md](test-baseline.md),
including every actual leaf and original counts, exact SHA/reservation/job,
selection and environment identities. A focused run stays focused evidence;
never label it as a whole-suite result. Other required suites/checks and actual
acceptance still need their own evidence before finish.

Scope shape (replace all example identities with observed evidence):

```json
{
  "source_base_commit": "controller-recorded-candidate-admission-SHA",
  "changed_paths": ["Assets/Features/Example.cs"],
  "affected_tests": ["Farm.Tests.Example.RequiredBehavior"],
  "issue_scope": {
    "source_base_commit": "controller-recorded-candidate-admission-SHA",
    "candidate_commit": "actual-tested-candidate-SHA",
    "changed_paths": ["Assets/Features/Example.cs"],
    "affected_tests": ["Farm.Tests.Example.RequiredBehavior"],
    "requirements": ["The issue's actual acceptance requirement"],
    "reason": "Why these assertions cover the change and affected consumers",
    "evidence": "Retained source review, full diff and acceptance coverage"
  },
  "unrelated_failures": {},
  "reviewed_skips": {}
}
```

The controller verifies the clean owned branch/HEAD, current generation's exact
released candidate reservation, recorded source base and complete actual Git
diff. A source, input or meaningful environment change invalidates affected
evidence; neither a new main tip nor worker restart does. Another issue's result,
an older candidate or an unclean reservation cannot grant this verdict.

All affected tests must exist and every execution must pass. Invalid tests,
ambiguous nonpassing duplicate names, unreviewed skips, unresolved failures and
missing evidence block. Start with the real report and empty review maps to get
`failure_evidence_ids`; this is an offline assessment, not a reason to rerun tests.

For failures outside acceptance:

- With **identical source commits and an empty actual diff**, each reviewed
  outside-scope failure may use `classification: "unchanged_source"`, its exact
  `evidence_id`, `reason` and `evidence`. Confirm the current environment supports
  the issue's acceptance and explain why this failure does not block it. This
  records an unchanged-source failure, not a diagnosed root cause or a fictitious
  matching baseline. The actual failure remains visible.
- With a changed candidate, use a `confirmed_unrelated` causal finding from
  [shared-test-findings.md](shared-test-findings.md), binding `evidence_id` to this
  actual candidate-only assessment. Name the real failing fixture, unchanged
  causal dependencies, concrete cause, exclusion of the complete issue diff,
  separate maintenance action, and retained evidence file/hash. The controller
  verifies those source identities and evidence bytes. A plain "unrelated" claim
  or shared directory/package membership does not establish independence. Reuse
  matching known diagnoses only after reviewing this issue's complete diff.
- Each non-applicable skip needs `classification: "not_applicable"`, exact
  `evidence_id`, `reason` and `evidence` in `reviewed_skips`. Required or affected
  checks cannot use this review to excuse missing coverage. Never suppress tests
  or edit their outcomes to obtain a verdict.

Only `draft_eligible: true` allows the scoped exception. It is not merge/deploy
authority. Complete all other checks and normal publication/finish gates. Report
all failures/skips and `baseline_executed: false`; do not describe source-only
review as empirical baseline matching. Shared causal defects are recorded as
separate maintenance findings and do not wait for unrelated repairs.

If attribution remains unresolved, decide what evidence is missing first. Fix a
proven issue regression; repair missing inputs before rerunning affected checks;
use a bounded focused observation for environment uncertainty. Request a baseline
only when it can distinguish a named source/input difference relevant to the
failure. Save that hypothesis and selection in the checkpoint, then follow
[test-baseline.md](test-baseline.md), reusing any completed candidate report.
Identical source/inputs are not a reason to execute two full suites.

An already requested comparison retains its actual pair and diagnostic budgets;
`verify-candidate` cannot bypass an unfinished/blocked comparison. After both
real runs finish, use `compare-verification`. Existing same-commit pairs are
supported with an empty diff and explicit issue scope, distinct reservations/jobs
and normal per-test/environment checks. Do not rerun them after deployment just
because they were admitted by older policy.
