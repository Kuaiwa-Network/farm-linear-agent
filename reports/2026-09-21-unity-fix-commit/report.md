# Unity verification of a worker's fix commit

## Delivered behavior

Previously, Unity reservations always selected the issue's original baseline. A fix
worker could commit a correction but could not request that revision for verification.
`await-resource --commit FULL_SHA` now selects the clean HEAD of the requesting job's
Farm-Client worktree. Omitting the option still selects the baseline. The job target
is never rewritten; the reservation records the tested commit separately.

Validation checks the live claim, configured host ledger, worktree ownership, shared
FarmBot clone, exact HEAD and clean tracked/untracked files. The ledger checks the
claim again after Git validation to fence concurrent cancellation. Read-only workers
cannot choose a revision. Batch XML, logs and summaries are retained separately per
reservation; summaries include the tested SHA and reservation ID. Fix instructions
require committing before a post-fix run and repeating verification after changes.

## Offline verification

The final gate, `python3 -B -W error -m unittest discover -s tests`, passed
456 tests in 98.549 seconds, warning-free. `git diff --check` passed.
Regressions cover explicit revision selection,
immutable baseline, dirty/foreign/stale checkout rejection, unauthorized claims,
configured-ledger mismatch, cancellation during validation, and loading an unpushed
fix commit into a slot. The independent review found no blocking defects. Additional
CLI regressions cover its suggested cancellation and ledger-mismatch cases.

## FARM-1242 live Unity verification

Target: Farm-Client PR #1311, commit
`f5e16655d3334d435203ca35719d3a0d7bc761ab`.
The original baseline remains `767a76db39ab3b75e7101c969003eae3a12dc333`.

With no active controller jobs, an isolated operator ledger used the real pool,
FarmBot clone and existing Unity slot. The production controller was temporarily
stopped to prevent concurrent slot ownership. The reservation loaded the fix SHA
and passed the Editor identity checks. This was an operator rehearsal using the
new code, not an autonomous worker restart or a deployment of the new controller.

- **PlayMode: 8/8 passed** in `MainViewTopEntryTests` and `ActivityServiceTests`.
  See [focused-result.json](focused-result.json) for names and results.
- **Compiled runtime/config probe passed**: shipped activity configuration ID 11
  matches `SystemUnlockIds.Activity`; ActivityBtn and GiftBtn both bind to 11;
  visibility is hidden before unlock and visible after unlock. This probe ran in
  Edit Mode against the compiled game code and shipped protobuf configuration.
  See [activity-unlock-probe.json](activity-unlock-probe.json), including probe code.
- A broader PlayMode run including `ActivityUiSmokeTests` **failed**:
  `GiftGardenRegisteredLocator_ParsesAndDelegatesToCrossPageCropAnchor` found zero
  Gift Garden configuration pages. The MCP response retained progress and the
  failure but no final summary; no pass count is inferred. It was not rerun against
  the baseline, so this report does not classify it as pre-existing.
  See [test-result.json](test-result.json).
- No test account is configured. Logging into the public test server and visually
  confirming the icon after completing task 200 remains unverified.

## Cleanup and deployment

The owned Editor and MCP server were closed and the project lock removed. Closing
before the interactive release probe left the isolated reservation held, because
that probe requires a live Editor. The operator confirmed process exit, recovered
the isolated reservation, and parked the slot at main (`767a76db...`), idle/closed.
The original controller was restored. Logs and the isolated ledger were retained at
`.local/verification/FARM-1242-20260921T124522/` in the installed checkout.

No issue comments or client PR changes were published. The controller changes need
to be merged and deployed before future workers receive the new command and skill.
This closes fix-revision selection; it does not implement the separate QA runner.
