# Independent maintenance for confirmed unrelated shared test defects

Business delivery must not acquire unrelated shared-test repair work. When an
existing shared test newly fails or changes its failure, review actual issue
requirements, the complete diff and causal dependencies. If required issue checks
pass and evidence establishes an independent shared test/environment defect, use
`confirmed_unrelated` on the original reports. Do not repair the fixture, wait for
main, add a blocking child task, ask for another approval or rerun baseline merely
to get the business issue to finish.

Retain the specific mechanism and evidence excluding an interaction with the
business change, including relevant shared setup, globals and resource inputs.
Source analysis can be sufficient when it establishes causality; otherwise use a
bounded focused observation or matching prior diagnosis. A later passing retry,
another issue failing the same test, guessed flakiness or unchanged fixture alone
is insufficient. Unresolved attribution and affected failures still block.

For a candidate-only round, start with [verify-candidate](candidate-verification.md).
Its `failure_evidence_ids` binds the actual run, environment, selection and source
base without inventing a baseline execution. Use the same causal finding fields
below; source/evidence validation and separate maintenance recording still apply.
Unchanged source alone is not a root-cause diagnosis. For an existing pair, compare
the original pair. `failure_evidence_ids` binds exact leaf signatures
and both commit/reservation/job identities. Add this entry to `unrelated_failures`,
retaining the bound `issue_scope`, full Git `changed_paths` and all required
`affected_tests`:

```json
{
  "SharedFixture.ExistingTest": {
    "reason": "Specific diagnosed mechanism and its relation to this issue",
    "evidence": "Retained source/observation review, full diff and issue QA",
    "confirmed_unrelated": {
      "evidence_id": "copy this test's failure_evidence_ids value",
      "kind": "shared_test_defect",
      "fixture_path": "Assets/Tests/SharedFixture.cs",
      "dependencies": ["Assets/Scripts/RelevantSharedDependency.cs"],
      "cause": "Observed defect in shared fixture or shared environment",
      "exclusion": "Why the business delta cannot cause this failure or lose required verification",
      "maintenance_action": "Specific independent repair to follow up",
      "evidence_file": "shared-causal-review.md",
      "evidence_sha256": "SHA256 of that unchanged item-local review file"
    }
  }
}
```

`kind` is `shared_test_defect` or `shared_environment_defect`. Keep the causal
review (at most 1 MiB) under this item's state directory, citing existing raw
reports/observations. No extra experiment is required just to produce that file.
`dependencies` contains 1–128 distinct repository-relative causal input paths,
not a speculative whole-repository audit.

The claim-fenced command checks the actual complete baseline/candidate Git diff,
clean current candidate, fixture in the failing stack, unchanged Git blobs for
the fixture and declared dependencies, and evidence-file integrity. It checks
structure and identities; the worker remains responsible for truthful causal
reasoning and complete acceptance coverage. Comparable complete reports, passing
affected tests, no missing tests/new skips/ambiguous executions, and normal
QA/publication/finish gates remain mandatory. A newly added or newly invalid test,
changed fixture/dependency, or failure stack in changed source cannot use this
exception. Environment/config identity drift still blocks even for an environment
defect review.

On success the verdict is `passed_with_unrelated_findings`, `draft_eligible=true`,
`full_suite_passed=false`. Raw outcomes, failed names and counts stay unchanged.
Each `confirmed_unrelated_failures` entry gets an immutable controller
`shared_test_followup` audit record with stable `followup_id`, causal evidence,
source/dependency identities, maintenance action and `status=open`. This is an
independent maintenance backlog, never a child job or business prerequisite.
Include its ID and limitation in the draft, then finish normally. Recording does
not auto-create a Linear issue, send separate messages, schedule repair or grant
authority to change other work.
Read-only `doctor.shared_test_followups` lists up to 200 distinct recent maintenance
findings, including those whose source business job has finished. It exposes test
identity and status without private causal prose or evidence paths; it does not
make the host unhealthy or reopen the business queue.

`shared-test-repairs` also returns `maintenance_findings`. Exact signatures,
source inputs and environment yield `matching_diagnosis`; drift yields explicit
`review_required`. Reuse causal reasoning, review this issue's complete diff and
bind its own reports. Save lookup/current applicability as item-local evidence.
A lookup is not a verdict. Repeated comparison does not duplicate the same record.
Maintenance stays open until separately handled; this version does not infer
resolution from another issue finishing.

The standalone comparator checks review shape/report binding only; use
`compare-verification` for controller source/evidence checks and durable tracking.
This exception applies to the original Unity pair, not standalone .NET or a mixed
repaired-candidate comparison. No schema migration or automatic resume is involved.
Older releases ignore these audit events and conservatively block unmatched
failures. Settle workers before rollback; preserve all reports and reviews.
