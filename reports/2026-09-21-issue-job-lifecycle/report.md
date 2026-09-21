# Issue closure and cancellation cleanup

## Delivered behavior

Completed, canceled or archived Linear issues cancel queued, running, input-waiting,
resource-waiting and blocked jobs. Signed Issue notifications schedule a fresh read;
periodic polling covers missed notifications. Read errors defer launch, and source
versions prevent stale status reads from reopening closed work. Write launches still
require current delegation.

Claims are revoked before owned processes are stopped. Source is preserved in local
Git commits and recovery refs before worktree deletion. Unverified processes, living
descendants, unsettled reservations and preservation failures hold cleanup. Status
commands expose those gaps. Logs, ledger history and memory snapshots are retained.

Paused jobs keep the existing reply behavior and job ID. Cancelled jobs stay cancelled;
an authorized explicit continuation creates a fresh linked job with stale recovery
evidence. Reopening by itself starts nothing.

## Verification

- Baseline: 372 tests passed, warnings treated as errors.
- Task 1: 108 ledger/resume/CLI tests passed.
- Task 2: 147 Git, scheduler, launcher and slot tests passed.
- Task 3: 139 lifecycle, receiver, API, service, launcher and cleanup tests passed.
- Two integrated rehearsals use production service wiring, temporary Git origins,
  real owned subprocesses and stub Linear. Closing a running worker revokes its claim,
  stops it, preserves tracked/untracked changes, removes worktrees and retains logs.
  Reopening does not launch; explicit restart gets a new ID and recovery refs. An open
  paused job preserves its worktree and resumes under its original ID.
- Full suite after fixture cleanup: 413 tests passed in 93.034 s, no warnings.
- Added one further RED→GREEN regression for a paused worker whose usable PID was
  cleared before it exited: cancellation now recovers the last PID from durable audit.
- Final task completion gate: **414 tests passed in 94.396 s**, with no warnings.
  `git diff --check` passed. The independent review found three consequential issues,
  fixed in one regression-driven pass as detailed below.

The initial integration run had a fixture field-name mismatch, corrected to the
existing `push_inbox` result (`item_id`). Its production path required no change.
A separate failing CLI regression exposed missing operator cleanup diagnostics;
both status commands now include them. The first 412-test full run passed but
reported four unclosed lifecycle database connections in the older end-to-end fixture;
the fixture now closes the new connection.

## Implementation decisions

1. Replaced the withdrawn broad plan with the user's narrower scope: retain logs and
   paused reply behavior. No explicit-resume redesign, expiry or compaction ships.
2. Each worker attempt uses a unique run directory because second-resolution names
   overwrote same-second attempt logs. In-repository consumers use returned paths;
   external tooling that assumes timestamp-only names may need adjustment.
3. Stop uses a short-lived dedicated SQLite connection and does not take the preparation
   lock. Durable cancellation and atomic launcher registration protect launch races;
   this avoids cross-thread transactions and waits behind repository preparation.

## Deployment limits

This feature has not been merged or deployed and has not changed live Linear issues.
Deploy the reviewed revision, then enable Issue webhooks alongside agent-session
webhooks with the configured signing secret. `reconcile_seconds` defaults to 60;
network latency and issue count can extend the time to observe closure. Failed reads
back off up to 300 seconds. Existing API scopes and delegation authority are unchanged.
Published PRs/messages and external requests already sent are not undone by cancellation.

Reference: [Linear's webhook envelope and signature documentation](https://linear.app/developers/webhooks).

## Independent review and fix pass

The reviewer reproduced three Important findings: an exited worker could leave a
live detached child while cleanup reported success; recovery and stale terminal
snapshots could reach the old force-removal path; and retrying a partial removal
could lose manifest entries for already-removed repositories.

All three have failing-before/passing-after regressions. A real detached child now
holds cleanup after its parent exits and the launcher restarts. Process identity is
persisted per attempt; missing descendant teardown or interrupted launch evidence is
an explicit cleanup gap. Recovery retains unverifiable live PIDs. Every terminal
retirement uses the same process/resource/preservation checks, and a durable removal
fence prevents same-ID retry from racing file deletion. Repository manifests merge
across retries; a real two-repository removal-failure rehearsal retains both entries.
No second review was requested; the final full-suite result follows below.

Additional review rulings:

- All terminal retirements use the safety gates. Cost: files from normally exited or
  older attempts lacking teardown evidence remain until operator investigation;
  cancelled successors stay queued while that cleanup is unresolved.
- Live subscription/deployment testing remains deferred, as approved. Cost: deployment
  must verify the real webhook subscription and status reads.
- Unverifiable live PIDs retain files indefinitely. Cost: manual investigation and disk
  use, chosen over killing unrelated processes or deleting potentially active files.
- Logs/history have no automatic growth limit, as explicitly requested. Cost: operators
  manage storage growth.

No Minor findings were deferred. These were the review's only declined areas:
live deployment, conservative PID retention and the user-requested unbounded history.

## Final verification

After the review fix pass, **420 tests passed in 95.051 seconds**, warning-free,
using `python3 -B -W error -m unittest discover -s tests`. `git diff --check` passed.
The older blocked-worker integration assertion was updated to verify retained files
and a visible teardown gap instead of unconditional removal after parent exit.
All three consequential review findings are covered by RED→GREEN regressions;
no consequential findings remain unaddressed. No second review was run.

Raw completion output: [verification.txt](verification.txt).
