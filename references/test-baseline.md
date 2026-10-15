# Comparing existing Unity failures

Full suites still run. A draft may retain unrelated failures only when complete comparable baseline and
candidate evidence proves no new failures, the issue's affected tests pass, and all other required checks pass.
This is a draft-delivery verdict, never a claim that the full suite passed or permission to merge/deploy.

From the owned Client stage, save the candidate SHA and checkpoint, then request:

```text
<selected-python> -m agent --db DATABASE await-resource --item ITEM --token-file STATE_DIR/token --resource unity_slot --mode interactive --commit CANDIDATE --baseline
```

The controller validates the clean owned candidate, branch, clone, current remote-main ancestry and claim,
then reserves actual remote main. It does not reset source or the immutable target. The resource carries
`verification_role=baseline` and `candidate_commit`. Run the full identical selection on `resource.commit`,
save complete results and checkpoint. When the same candidate is ready and no test, PlayMode or compile is
active, continue on the same slot using both independent tokens:

```text
<selected-python> -m agent --db DATABASE continue-verification --item ITEM --token-file STATE_DIR/token --reservation-token-file RESOURCE_TOKEN_FILE --commit CANDIDATE
```

This atomically releases the baseline and queues its exact recorded candidate for the slot's next turn.
Exit immediately: the claim is retired. The controller still verifies the old worker's exit, Unity quiescence,
memory and a worker seat before switching/preparing and resuming candidate verification. Distinct reservations,
test jobs and complete evidence remain mandatory. The priority expires if another reservation uses that slot;
it cannot be chained from the candidate. A failed settlement does not grant access to a held Editor.
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
when a transport cannot return the full verbose result. Validate counts and unique identities after assembly.
Once complete results and input identity are saved, checkpoint and release promptly; compare offline after
controller settlement, rather than holding the shared Editor while writing the diagnosis.

Scope JSON contains exact `affected_tests`, all Git `changed_paths`, and `unrelated_failures`, mapping each
candidate failure name to a nonempty `reason` and `evidence` reference. Review source dependencies and the
actual diff; absence of a failing test filename from the diff alone does not prove the failure is unrelated.
Include the issue's entire affected fixture, not one passing test. Failures whose stack points into a changed
path, affected failures, changed failure signatures, new/changed skips, removed tests and mismatched input
identity are blocked. New tests must pass. Known failures require the same state/message/failure site,
except for the narrowly reviewed absolute-clock diagnostic below. Failure messages retain path/URL
differences; by default only line endings and outer whitespace are normalized.
Stack normalization ignores checkout paths, C# line shifts, IL offsets and assembly IDs.

## Absolute Unity clock diagnostics

`Assert.Less(Time.realtimeSinceStartup, deadline, "timed out ...")` prints process-relative clock readings.
Two identical 15-second waits can fail with different `Expected: less than 23.638031f / But was: 23.6388607f`
and `Expected: less than 42.10001f / But was: 42.10084f`. Those operands alone are not a changed timeout or
gameplay value. Do not re-run Unity just to obtain identical clock readings.

First review both committed sources and the complete wait predicate/dependencies, prove the failing
assertion file and configured timeout unchanged, and prove this failure unrelated to the issue. Add
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
review retains strict matching. Affected failures, new failures/skips, incomplete results, dependency
changes and environment drift still block. This is not a general regex or numeric-ignore facility.

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

For each retained failure add `resource_review` inside its `unrelated_failures` entry:

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
pair. This command updates Monitor immediately to comparison, then eligible/blocked/evidence-gap. It prints
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
