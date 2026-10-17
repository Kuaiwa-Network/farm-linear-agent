# Comparing existing Unity failures

Start with [candidate-first verification](candidate-verification.md). Baseline testing is on demand,
not a mandatory second full suite. Preserve required gates and complete actual selections. Request
a pair only for unresolved attribution with a concrete source/input difference it can distinguish.
Never run identical source/inputs twice solely to obtain two labels. For an actual comparison,
a draft may retain unrelated failures when complete comparable baseline and
candidate evidence proves no unresolved regressions, the issue's affected tests pass, and all other required checks pass.
This is a draft-delivery verdict, never a claim that the full suite passed or permission to merge/deploy.
Existing same-commit pairs remain reviewable with an empty diff and explicit issue scope;
they still require two real distinct jobs/reservations. Do not invent a second execution.

From the owned Client stage, save the candidate SHA and checkpoint, then request:

```text
<selected-python> -m agent --db DATABASE await-resource --item ITEM --token-file STATE_DIR/token --resource unity_slot --mode interactive --commit CANDIDATE --baseline
```

The controller validates the clean owned candidate, branch, clone, current remote-main ancestry and claim,
then reserves actual remote main. It does not reset source or the immutable target. The resource carries
`verification_role=baseline` and `candidate_commit`. Run the full identical selection on `resource.commit`,
save complete results and checkpoint. When the same candidate is ready and no test, PlayMode or compile is
active, continue on the same slot using both independent tokens:

**Candidate already tested first:** add `--reuse-candidate-reservation ID` to the baseline request.
Only this generation's exact cleanly released candidate in the same mode, with no previous comparison
round, is eligible. The controller retains its recorded admission upstream SHA (which must still belong
to actual main); a legacy run without this pin requires current-main validation. The baseline grant
includes `reuse_candidate_reservation`. In that case, save the full baseline report, release cleanly,
wait for controller settlement and compare the existing reports in the same worker. Skip the continuation
command below; the candidate does not need a later timestamp or another full test run. Complete selection,
environment, affected-test and evidence checks are unchanged. This binding itself is not a test verdict.

**Maintenance interrupted the baseline:** after its clean release and certified
worker handoff, use `--resume-baseline-reservation ORIGINAL_BASELINE_ID` instead
of `--reuse-candidate-reservation`. The original candidate and upstream remain
fixed even if main moved. Any candidate binding is copied from the controller's
original record. Completed evidence is still reused; never rerun a reviewed
comparison. Changed generations, later Unity reservations, unclean release and
unfinished handoff require diagnosis rather than a fallback to current main.

**Candidate still needs tests:**

```text
<selected-python> -m agent --db DATABASE continue-verification --item ITEM --token-file STATE_DIR/token --reservation-token-file RESOURCE_TOKEN_FILE --commit CANDIDATE
```

This atomically releases the baseline and queues its exact recorded candidate for the slot's next turn.
Exit immediately: the claim is retired. The controller still verifies the old worker's exit, Unity quiescence,
memory and a worker seat before switching/preparing and resuming candidate verification. Distinct reservations,
test jobs and complete evidence remain mandatory. The priority expires if another reservation uses that slot;
it cannot be chained from the candidate. A failed settlement does not grant access to a held Editor.
For a diagnosed descendant repair, use `--repair-candidate-reservation` as described
in [the worker CLI](worker-cli.md#continuing-a-diagnosed-client-repair). Preserve the
original baseline and failed full pair. Test-fixture-only causal repairs can use
the explicit retained-full plus fresh-focused comparison, never a relabeled full pass.

If the baseline was already released, request the unchanged candidate normally after settlement; never repeat
the baseline just to gain priority. Incomplete results, source changes or human input use normal release and
diagnosis. An environment mismatch cannot waive a failure. Do not edit a slot or operate its Editor processes.
A gap/stall is never a completed baseline.

Keep each run in a private JSON file with this shape (all identities come from observed evidence):

```json
{
  "commit": "40-character-lowercase-SHA",
  "reservation_id": "actual-reservation-id",
  "job_id": "actual-test-job-id",
  "completed": true,
  "mode": "PlayMode",
  "selection": {"assembly_names": ["HotUpdate.PlayTests"]},
  "environment": {
    "host": "observed-host", "slot": "unity_slot:1",
    "unity_version": "observed-version", "platform": "observed-build-target",
    "test_framework_version": "observed-version",
    "config_digest": "SHA256-of-all-used-config-inputs",
    "package_digest": "SHA256-of-Unity-package-lock-and-used-FairyGUI-assets"
  },
  "summary": {"total": 1, "passed": 1, "failed": 0, "skipped": 0},
  "tests": [{"fullName": "Exact.Parameterized.TestIdentity", "state": "Passed", "message": "", "stackTrace": ""}]
}
```

Hash the sorted relative-path/content-hash inventory, with identical scope for the pair. Retain the inventory,
raw result and tool/instance identities alongside each normalized run. Git/LFS content identity inventories
may be used when each reserved checkout's hydrated bytes are independently verified against those objects.
Record additional environment inputs
(account, test settings, server/stub selection, relevant environment variables) when used; the environment
objects must match exactly, except for the audited candidate UI resource delta described below. Never copy
credentials into evidence. The tests array contains every leaf outcome,
not just failures. Parse counts from actual results, not discovery progress or exit codes. Failed-job MCP polls
that hide results are gaps until the same finished job's actual complete result is retrieved read-only. Use
bounded pages or a compact projection of every leaf's fullName/state/message/stackTrace plus the actual summary
when a transport cannot return the full verbose result. Validate every leaf and the full counts after assembly.
NUnit may emit the same `fullName` for distinct array-valued parameterized cases. Retain every execution,
without deduplicating or renaming it. The comparator groups exact names and records `duplicate_executions`:
only equal execution counts with every leaf passed on both sides can be compared without separate stable
identities. A repeated group containing any failure/skip, or any count change, remains blocked; a review
cannot waive this ambiguity. All affected executions must pass. Reuse complete retained reports after a
comparator upgrade; no Unity rerun or altered result/digest is needed for passing duplicate groups.
Once complete results and input identity are saved, checkpoint and release promptly; compare offline after
controller settlement, rather than holding the shared Editor while writing the diagnosis.

Scope JSON contains exact `affected_tests`, all Git `changed_paths`, `unrelated_failures` (empty is allowed
with the issue scope below), and a reviewed `issue_scope`. Select all assertions needed for this issue's
acceptance and concretely linked dependencies from the actual diff. Include the complete relevant behavior,
not only one convenient passing case. A shared export package, directory, fixture, loader or uncertain
shared-state coupling alone does not expand the mandatory acceptance boundary. Record concrete evidence
when a changed behavior or observed regression does expand it. Failures whose stack points into a changed
path, affected failures, changed failure signatures, new/changed skips, removed tests and mismatched input
identity are blocked by default. Proven unrelated shared defects can use the explicit
[causal review and independent maintenance](shared-test-findings.md) exception, without repair or
baseline reruns as business prerequisites. New tests must pass. Known failures require the same state/message/failure site,
except for the narrowly reviewed absolute-clock diagnostic below. Failure messages retain path/URL
differences; by default only line endings and outer whitespace are normalized.
Stack normalization ignores checkout paths, C# line shifts, IL offsets and assembly IDs.
`Failed:Invalid` is a failure, not a skip or pass. It can match only the same reviewed baseline state,
message and site. An invalid affected test still blocks delivery. Preserve raw NUnit outcomes when
re-comparing retained EditMode/PlayMode results after a comparator upgrade.

## Issue acceptance boundary

Use `issue_scope` for new Unity comparisons and re-review inherited broad scopes after an upgrade:

```json
{
  "baseline_commit": "actual-40-character-baseline-SHA",
  "candidate_commit": "actual-40-character-candidate-SHA",
  "changed_paths": ["Assets/Scripts/Feature/Counter.cs"],
  "affected_tests": ["Feature.CounterTests.AcceptedBehavior"],
  "reason": "The accepted change is counter rendering; these checks cover its behavior and concrete dependencies",
  "evidence": "private issue requirements, complete source/resource diff, scope review and retained QA"
}
```

The commits and complete path/test sets must match the comparison inputs. This is a worker review of actual
requirements and the complete diff, not a declaration that merely loading a changed package makes all its
features affected. Acceptance scope must never exclude a relevant failing check to get a green verdict.
A specific changed assertion, called behavior, relevant resource property or observed regression is evidence
for inclusion; package membership alone is not. Preserve valid existing reviews/QA across handoffs instead
of investigating the same baseline debt again.

With this explicit bound scope, matching baseline failures outside it are retained by default, including
`Failed:Invalid`. No per-failure proof that every transitive resource is unchanged or root cause is known is
required. A whole-package hash change is still an audited candidate input delta, but does not itself block
an unchanged outside-scope failure. Keep clock review when only absolute operands differ. A failure stack
that names a changed source path remains a concrete association and blocks. New/changed failures, failed
or missing affected checks, new/changed skips, removed tests, ambiguous duplicate executions, unreviewed
resource changes, different settings/config/lock/host and incomplete results all retain their gates.
Only the explicitly verified [shared-finding exception](shared-test-findings.md) can retain new/changed
failure signatures; it keeps failed results and records independent maintenance without blocking the issue.

Output `scope_policy=issue-delta-v1` records `issue_scope` and exact `retained_baseline_failures`. These mean
unchanged failures outside the reviewed issue scope, not proven root causes or passing tests. Keep them in
the draft along with every skip and `full_suite_passed=false`. The normal required issue checks, QA and
publication/finish gates still apply. Unknown root cause of such retained failures does not require another
Unity diagnostic or block delivery. Do not repair unrelated failures or broaden the task to make main green.

Scopes without `issue_scope` keep the legacy per-failure review and resource-independence checks below.
This makes the new boundary explicit rather than silently changing historical verdicts. Retained evidence
can be re-compared offline with a reviewed new scope; no original report, digest, test, claim or reservation
is rewritten and no baseline/full-suite rerun is needed. Older comparators reject this optional new field;
settle work before rollback and preserve all reports and reviews. There is no database migration.

## Standalone .NET TRX

Use a separate read-only comparison for full standalone `dotnet test` results:

```text
<selected-python> -m agent.dotnet_baseline --baseline STATE_DIR/unit-baseline-manifest.json --candidate STATE_DIR/unit-candidate-manifest.json --scope STATE_DIR/unit-scope.json
```

Each manifest binds the original raw TRX by SHA-256; no Unity reservation, mode or job may be fabricated:

```json
{
  "commit": "40-character-lowercase-SHA",
  "completed": true,
  "mode": "dotnet-test",
  "selection": {"project": "tests/Farm.Tests.Unit/Farm.Tests.Unit.csproj", "filter": null},
  "environment": {
    "host": "observed-host",
    "execution_platform": "win32",
    "toolchain_digest": "SHA256-of-retained-SDK-and-test-adapter-evidence",
    "config_digest": "SHA256-of-complete-used-configuration-inventory",
    "package_digest": "SHA256-of-complete-used-dependency-and-asset-inventory"
  },
  "trx": "path-to-original-complete.trx",
  "trx_sha256": "SHA256-of-that-unmodified-TRX"
}
```

Retain the observed SDK/test adapter versions, locked dependencies, inventories, exact command, full raw
results and input/source identities. Existing contemporaneous evidence may supply these facts; do not
invent an environment or rerun unchanged suites merely to regenerate metadata. Record additional used
settings, services and environment fields; both environment objects and selections must match exactly.
The project is repository-relative; a relative TRX path resolves against its manifest's directory.

Scope contains `affected_tests` (complete exact class/display names), `changed_paths` (the full Git diff)
and `unrelated_failures` (each exact failing name mapped to reviewed `reason` and `evidence`). Review
transitive source/dependencies, shared setup and actual inputs, not just filenames. All affected executions
and new cases must pass. Changed failures, removed executions, new skips and environment drift block.
This conservative standalone comparator offers no clock or UI-package-drift exception.

The parser checks run/result finish timestamps, raw counters, test definitions and every execution entry.
It retains separate execution IDs when display names and even test IDs repeat. Repeated groups may be
compared only when every baseline and candidate execution passed; any non-passing duplicate is ambiguous
and blocks. No name-based deduplication is allowed. NUnit's adapter can report zero `notExecuted` while
retaining explicit `NotExecuted` leaves; total/executed/pass/fail counts and complete entries must still
reconcile, and each skipped leaf needs its original reason. Interrupted/unsupported outcomes are gaps.

Exit codes are 0 eligible, 1 blocked, 2 invalid/missing evidence. Save the report and manifests under the
job's state directory and in its checkpoint. `draft_eligible=true` covers this standalone check only;
required Unity comparisons, issue QA and publication gates remain independent. Keep `full_suite_passed=false`
and disclose every retained failure/skip. This adds no schema migration or worker authority to repair
unrelated code, merge or deploy. Recompare complete existing reports before requesting another test run.

## Absolute Unity clock diagnostics

`Assert.Less(Time.realtimeSinceStartup, deadline, "timed out ...")` prints process-relative clock readings.
Two identical 15-second waits can fail with different `Expected: less than 23.638031f / But was: 23.6388607f`
and `Expected: less than 42.10001f / But was: 42.10084f`. Those operands alone are not a changed timeout or
gameplay value. Do not re-run Unity just to obtain identical clock readings.

Review both committed assertion sources and the configured timeout and retain their exact identities.
Use the issue acceptance boundary above to determine impact; an outside-scope identical timeout does not
require diagnosing the entire shared package. For legacy scopes, the per-failure dependency review remains.
Add
`clock_diagnostic` to that exact test's `unrelated_failures` review:

```json
{
  "kind": "unity_realtime_deadline",
  "clock": "UnityEngine.Time.realtimeSinceStartup",
  "source_path": "Assets/Scripts/HotUpdate/Tests/PlayMode/ExampleTests.cs",
  "baseline_source_sha256": "actual-64-hex-digest-of-the-committed-assertion-file",
  "candidate_source_sha256": "same-actual-64-hex-digest",
  "timeout_seconds": 15,
  "reason": "Both sources use the same 15-second wait, predicate and dependencies; only absolute clock operands differ",
  "evidence": "private committed-source and dependency review plus both unmodified raw results"
}
```

The comparator requires equal valid source hashes, a source path absent from the change list, the same
failure state and normalized stack (whose first frame names that file), identical timeout message text,
and precisely the `Expected: less than <number> / But was: <number>` assertion form. Both observed times
must have reached the deadline. The candidate must be observed within one second of its deadline;
a later baseline observation is retained but does not make the candidate's promptly observed, otherwise
identical timeout a new failure. This is directional: a candidate overrun greater than one second still
blocks even if the baseline overran by as much or more. The comparator does not diagnose the baseline
delay as harmless frame jitter. Different assertions, business values, changed timeout durations or
exception messages are not normalized. Missing
review retains strict matching. Affected failures, new failures/skips, incomplete results, concretely linked
changed dependencies and environment drift still block. This is not a general regex or numeric-ignore facility.

Retain the original evidence unchanged. `reviewed_clock_diagnostics` in the comparison output records both
sets of operands, exact decimal `overshoot_seconds`, `baseline_late_observation`, and the source review.
Re-run the read-only comparator offline when adding/correcting this
review; retain the same completed baseline/candidate pair and do not request another Unity run solely for
these diagnostic differences.

## Candidate UI resources versus environment drift

Do not remove the UI being tested from `package_digest` or replace either run's recorded digest. Attach
`package_inventory` to each run: the complete list of `{"path": "repository/relative/file", "sha256": "64 hex"}`
entries used to compute that run's original digest. Include the package manifest/lock and all used FairyGUI
descriptors, atlases, dependency packages and `.meta` files, using identical inventory scope for the pair.
Sort entries by canonical forward-slash path, encode with
`json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")`, then SHA-256.
The comparator verifies this binding, rejects duplicate/case-aliased or unsafe paths, and compares the full
inventory. Historical completed evidence may be copied and enriched from its contemporaneous verified
inventory; retain the originals and never rewrite digests, results, reservations or job identities.

Add `ui_resource_changes` to the scope, mapping **every observed changed input** to a nonempty `reason` and
`evidence` reference. Each path must also be in the complete Git `changed_paths` and under
`Assets/GameRes/FairyRes/`. No directory wildcard, package manifest/lock, config or unrelated UI drift is
exempted. Added/deleted assets and metadata stay visible with their before/after hashes; `.meta` differences
are candidate changes only when separately audited, never silently ignored. Other environment fields
(including any recorded settings/account/server selection) still match exactly.

For legacy scopes **without `issue_scope`**, each retained failure needs `resource_review` inside its
`unrelated_failures` entry. New issue scopes use the acceptance boundary above instead of this whole-file
independence requirement:

```json
{
  "reason": "Failure signature and source/dependencies are unchanged",
  "evidence": "private per-test source diff and raw result evidence",
  "resource_review": {
    "dependencies": ["Assets/GameRes/FairyRes/Card/Card_fui.bytes"],
    "reason": "Reviewed the complete transitive resource closure, shared fixture/setup and UI loader; none depends on changed Friend resources",
    "evidence": "private fixture/loader and resource-dependency review"
  }
}
```

List the complete transitive set of package/resource files used by the failing case, not one convenient
unchanged file. Every dependency must exist identically in both inventories; its adjacent/ancestor metadata
is checked automatically when present. A changed dependency, missing review or uncertain shared-state
coupling blocks attribution. Matching names/counts or disjoint folders alone do not prove independence.
This reviewed separation permits only the existing draft verdict; all affected tests must still pass.
If inventories are absent, the original strict digest rule applies. Environment drift still blocks a draft,
but identical observed skip signatures remain existing skips in the report rather than becoming "new" skips.

Before reviewing failures, run `verification-progress --item ITEM --token-file STATE_DIR/token
--phase failure-review`. After saving the reviewed scope, run the claim-fenced comparison using the selected
Windows Python or macOS `python3`:

```text
<selected-python> -m agent --db DATABASE compare-verification --item ITEM --token-file STATE_DIR/token --baseline STATE_DIR/baseline.json --candidate STATE_DIR/candidate.json --scope STATE_DIR/comparison-scope.json
```

All three files must be under this item's `state_dir`; the evidence must name its completed controller-selected
pair. The candidate report selects its own recorded, quiescently released reservation within the current
baseline round, even after a later focused diagnostic at the same commit. Keep the original reservation IDs;
a newer baseline round cannot reuse that older candidate. This preserves evidence identity, not a passing
verdict: focused passes cannot replace failed full-suite evidence, and all comparison gates still apply.
This command updates Monitor immediately to comparison, then eligible/blocked/evidence-gap. It prints
the comparison report; inspect `draft_eligible` even when the command exits successfully. Save the full output.
Use `verification-progress --phase publication` before publication checks and `--phase delivery` before final
delivery recording, with the same item/claim arguments. Phase updates preserve the checkpoint and grant no
test, publication or completion authority.

For an independent read-only/offline comparison, the original command remains available:

```text
<selected-python> -m agent.test_baseline --baseline STATE_DIR/baseline.json --candidate STATE_DIR/candidate.json --scope STATE_DIR/comparison-scope.json
```

Exit 0 permits considering a draft; 1 blocks; 2 reports invalid/missing evidence. Save the full output in
`client.verification.comparison` with both evidence paths, SHAs, scope, stable environment identities and each audited UI resource delta. Validate
the candidate still equals the verified clean HEAD before the normal fresh delegation, foreign-work and
`verify-publication` checks. Any source/input change requires a new pair. If the verdict is
`passed_with_existing_failures`, the draft lists every retained failure and skip and explicitly states the full
suite remains failed. Do not weaken assertions, suppress logs, skip failing tests or repair other issues.

When `draft_eligible=true`, complete the issue's remaining required checks and closing work, publish/register
the draft, then `finish --outcome delivered`, retaining every limitation in the final evidence. No green
baseline, unrelated main fix or additional draft-delivery permission is required. Main can retain the same
unrelated failures indefinitely without keeping this issue open. If the comparison is ineligible, diagnose
the concrete regression/gap; fix attributable issues or report the unresolved blocker, rather than silently
repeat the unchanged pair. Restarts/checkpoints may reuse valid completed evidence at the same exact inputs.

## Bounded diagnosis before an unresolved-attribution stop

First use [shared-test-repairs](shared-test-repairs.md) to find previously verified
fixture diagnoses. Exact source/signature/dependency matches and current issue review
allow reusing that causal investigation. Fresh whole-fixture repair validation is
still required; an unrelated historical passing run never waives a current failure.

A comparison refusal is a publication gate, not proof that no further authorized work is possible. Before
finishing blocked solely because a new/changed failure has unresolved attribution, complete one bounded
diagnostic attempt when the existing tools and authority support it. This applies to new/changed or
concretely affected failures, not matching outside-scope baseline debt. Start with the retained raw reports,
exact source/input diff, failed assertions and their setup/teardown and transitive dependencies. Select
affected tests by the assertions and dependencies that can observe the change, not a shared directory or
fixture name alone. Do not remove an affected failure merely because it also failed at baseline.

Record the hypothesis, observables, proposed commands/selection, stopping condition and diagnostic budget
in the existing checkpoint/handoff before requesting resources. Preserve that consumed budget across
resumes for the same candidate and failure; a restart is not a new attempt. If runtime observations are
necessary, use the normal resource/claim/identity workflow for the original candidate and only the focused
selection needed to observe the suspected cause. Do not rerun the full baseline, chase main or repeat an
unchanged full suite to seek a pass. Capture actual timing/input/global-state observations where relevant:
for example, elapsed frames versus an animation's intermediate sampling window, or the UI/viewport gate
that prevents a synthetic camera gesture. Respect the probe and cleanup authority of the current resource;
never operate another task's Editor or erase shared state to manufacture a passing result.

Keep the original failed full-suite pair and raw evidence. A focused pass does not replace a failed
full-suite result; identical failures on different issues and unchanged source alone do not establish
causal independence. The issue-scope comparator and affected-test/QA gates remain required. Unknown causes of matching failures
outside the issue scope are retained baseline debt, not grounds for additional diagnosis or a blocked result. Fix a proven
issue-caused defect with normal candidate validation; retain a concrete shared-fixture defect for review
without weakening assertions or suppressing tests. After the one bounded attempt, record its observations,
remaining uncertainty and exact next requirement. If an attempt cannot safely be made, record the specific
missing capability or authority. Either is a concrete blocked result; an untested hypothesis alone is not a
reason to stop while the planned diagnostic remains available. Do not repeat an exhausted attempt without
new evidence that justifies a different diagnostic plan.

This optional review extends comparison output and worker instructions without a ledger migration. Older
comparators keep strict message matching; keep raw evidence and recompare offline after upgrade. It grants
no test verdict to an interrupted or unfinished candidate and requires no historical evidence rewrite.
Continuation and phase reporting also require no schema migration. Existing queued requests retain their
candidate/history and ordinary order. Older controllers ignore continuation audit metadata and use normal
FIFO on rollback; preserve evidence and deploy a complete settled release before using the new CLI commands.

No database migration is introduced. Baseline reservation identity lives in controller audit records; older
code can read the ledger but does not dispatch the comparison role or allow this draft verdict. Settle active
comparison reservations before rolling back. Running services use their installed revision until separately
deployed; editing a development checkout does not update worker authority.
