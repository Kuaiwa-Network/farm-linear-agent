# Client verification resume

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
3. On a baseline reservation, test only its reserved SHA. If
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
   no extra handoff to the same repository. Run `compare-verification` against
   the recorded pair and inspect `draft_eligible`. New regressions, affected
   failures, skips and evidence gaps still block. Never retest to repair parsing,
   absolute-clock attribution or a new main tip alone.

If the first completed candidate needs a baseline, request it with
`await-resource --baseline --commit CANDIDATE --reuse-candidate-reservation ID`.
The controller binds only this generation's cleanly released exact candidate,
with no existing comparison round in that mode. It preserves the upstream SHA
recorded at candidate admission. Older reservations without that pin must pass
current-main validation; no historical base is guessed from worker notes.

After an authorized restart, revalidate exact candidates, tool/input identities
and evidence hashes once, then continue the missing step. A new candidate or
changed required inputs invalidate affected evidence; an unchanged resume does
not. Progress labels describe work, never certify results.
