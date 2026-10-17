# Client verification resume

First read the current resource policy. `verification_policy: "candidate-first"` overrides a saved
legacy two-run plan, including an unstarted same-tree baseline converted by the controller. Execute
the actual candidate once, preserve its original identities, and use candidate verification. Never
recreate a baseline just because the old checkpoint says to do so. Started rounds retain their pair.
For offline closeout, batch retained EditMode/PlayMode reports through `verification-closeout` as
described in [closeout reuse](closeout-reuse.md), then keep the same worker for remaining gates.

This is navigation for an existing attempt, not a test verdict or a new grant.
Read the current repository's required instructions, current human requests and
`issue-context --view resume`. Read full context if its handoff is stale or a
referenced decision is missing. For work/feature, use stage F and Closing in
`skills/feature/SKILL.md`; fix uses its verification section. Read other stages
only when a missing dependency, changed input or actual handoff requires them.

1. Read the saved HEAD/input identities, completed gates, raw report paths and
   remaining acceptance checklist. Batch independent hashes and selected report
   fields in one local command. Do not repeatedly print whole logs or re-diagnose
   already classified unchanged failures. Preserve raw evidence.
2. Before a new resource request, prepare the issue's actual acceptance harness,
   input data and commands. Report `verification-progress --phase preparation`.
   Typecheck output may use its documented private output directory. Repository
   .NET tests that discover source roots from their executable must use the
   repository-supported output layout (normally ignored in-tree bin/obj), not an
   arbitrary external BaseOutputPath. Hydrate required LFS inputs using existing
   authorized tooling before execution; never substitute pointer text for data.
   Use original TRX counters and individual outcomes, including NotExecuted,
   rather than the console's abbreviated skip count. A missing input stays a gap.
3. If maintenance released an incomplete baseline and its worker handoff has
   settled, use `await-resource --baseline --commit CANDIDATE
   --resume-baseline-reservation ORIGINAL_BASELINE_ID`. This uses the latest
   controller-recorded comparison in this generation, not a supplied base SHA or
   newer main. It retains any already bound candidate report. Read retained
   reports first; do not repeat completed checks or a reviewed comparison. A
   completed report from the original baseline keeps its original reservation/job
   IDs; the comparator accepts only the explicit clean maintenance-resume chain.
   A no-test release proves no test result. Once acquired, on a baseline reservation,
   test only its reserved SHA. If
   `resource.reuse_candidate_reservation` exists, retain all baseline leaves,
   release quiescently and compare them against that candidate's existing report
   after controller settlement. Otherwise use `continue-verification` as
   documented in `worker-cli.md`. Do not rerun a completed candidate just to put
   its timestamp after the baseline.
4. On a candidate reservation, report `--phase testing`; collect required tests
   and then `--phase presentation-qa` for the already planned issue scenarios in
   this same claim/reservation when safe. A concrete regression or unsafe runtime
   stops dependent QA; do not hold Unity for offline investigation or human waits.
   A baseline worker does not repeat candidate QA.
5. QA covers the changed behavior and concrete affected consumers. A shared
   package or component name does not require touring every scene. Reuse a saved
   harness, check camera/UI visibility before captures, and use local transient
   setup without editing committed assets. Where a permitted local fake network
   harness needs an acknowledgement before its timeout, perform action and reply
   in one execution instead of leaving a pending request across tool round trips.
   Preserve the separate timeout/refusal scenario. Attempt one bounded setup
   correction for a diagnosed environment issue; retain a concrete gap if it
   cannot be made reliable. Screenshots alone do not prove behavior. State which
   checks used local harnesses and which, if any, exercised actual service paths.
6. Save complete reports and QA before clean release. Keep the same worker claim
   for offline comparison, publication and delivery after controller settlement;
   no extra handoff to the same repository. For a candidate-only round, use
   [candidate-first verification](candidate-verification.md) and `verify-candidate`;
   do not create a baseline just because an outside-scope test failed. For an
   already recorded pair use `compare-verification` and inspect `draft_eligible`. New regressions, affected
   failures, skips and evidence gaps still block. Never retest to repair parsing,
   absolute-clock attribution or a new main tip alone.

Only if named unresolved attribution needs a meaningful source/input comparison,
save the hypothesis and required selection, then request the baseline with
`await-resource --baseline --commit CANDIDATE --reuse-candidate-reservation ID`.
The controller binds only this generation's cleanly released exact candidate,
with no existing comparison round in that mode. It preserves the upstream SHA
recorded at candidate admission. Older reservations without that pin must pass
current-main validation; no historical base is guessed from worker notes.
A candidate-only run can use its recorded admission base for remaining focused
checks and publication after clean settlement, without requesting a comparison
baseline solely to pin main. This proves ancestry only, never a passing result;
complete required test/acceptance results are still necessary. Once a comparison
round exists, its stricter pair rules take precedence.

After an authorized restart, revalidate exact candidates, tool/input identities
and evidence hashes once, then continue the missing step. A new candidate or
changed required inputs invalidate affected evidence; an unchanged resume does
not. Progress labels describe work, never certify results.
