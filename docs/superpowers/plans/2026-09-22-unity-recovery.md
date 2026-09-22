# Controller-owned Unity recovery

Approved design: the 2026-09-22 conversation. Implement inline using TDD; finish with an independent review.

Goal: a stalled bot-owned Unity editor never requires a person to operate the host or answer a fake clarification question.

Use the existing `awaiting_resource` state with stage `waiting_for_recovery` rather than rebuilding SQLite's work-item state constraint. Keep recovery attempts and evidence in a separate durable journal. Human questions remain `awaiting_input`.

- [ ] Add real-SQLite regression tests for claim fencing, checkpoint preservation, cancellation, same-commit retry, bounded retries and restart persistence.
- [ ] Add a recovery journal and controller. After verifying worker teardown, detach the old reservation and queue its replacement independently of editor repair. Keep the affected slot held until verified healthy. Never resume genuine human questions or cancelled work.
- [ ] Add controller watchdog coverage for stalled active tests. Distinguish lack of progress from a mere active flag, preserve state/console/job evidence, attempt cancellation, then restart only the configured slot's editor. Reject uncertain process inspection.
- [ ] Wire an independent service recovery loop, worker guidance and diagnostic reporting. Bound both slot repair attempts and repeated job retries; finish with an explicit failure when exhausted.
- [ ] Run focused and full offline tests, review cancellation/restart races, and perform an independent code review.
- [ ] Back up the live ledger, deploy while drained, adopt FARM-1287's verified legacy infrastructure pause, and verify automatic recovery and job resumption.

Constraints: no unrelated editor termination; no checkout or lock removal while an editor still owns the folder; preserve worktrees/PRs/results; do not certify interrupted tests; fresh delegation remains required at worker launch; no network calls or process waits inside database transactions; repair must not block healthy slot scheduling.

Review focus: Stop during recovery; service restart between fencing and detachment; failed process inspection; a healthy second slot while the first repairs; repeated hangs on the same job; legacy genuine human questions; two recovery consumers; repair failure without a worker available to report it.
